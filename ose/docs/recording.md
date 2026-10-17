# Interactive live recording

`ose-ctl record` is a generic external console included in OSE. It discovers
the outputs of a running application and records selected publications through
SDL's recording service. It needs no model, generated application classes or
recorder component in the graph. Start and stop files repeatedly in one session.
It supports the local broker and distributed workers, including direct routes.
Simulation has no live network endpoint; use its scenario and trace interfaces.
The running broker/workers must also use an OSE version providing recording taps;
an older application runtime cannot gain them by upgrading only the console.
Rebuild/restart the application with the updated framework. For Vibration Watch
development, its existing `FRAMEWORK_ROOT=/path/to/sdl-ose make run DURATION=60`
override selects this framework checkout without changing the model. Cluster
deployments need updated worker images.

## Optional services and installation

Recording uses SDL's `sdl_recording.RecordingWriter`, defaulting to PCAPNG.
MCAP is an explicit alternative; both formats include the original catalogue
and encoded payloads. Both require the optional MCAP Python package. The OSE
broker and workers themselves need neither that dependency nor filter libraries.

For an installed OSE package, install `ose[recording]` and make the SDL
Python modules importable. For a source checkout, from the OSE root:

```sh
export SDL_ROOT=/absolute/path/to/sdl
make -C "$SDL_ROOT" recording-deps
export PYTHONPATH="$PWD/src:$SDL_ROOT/runtime/python:$SDL_ROOT/build/recording-deps"
```

Content filters additionally use SDL's optional native filter service:

```sh
make -C "$SDL_ROOT" filter-python
export PYTHONPATH="$SDL_ROOT/runtime/filter/python:$SDL_ROOT/build/filter/release:$PYTHONPATH"
```

No filter dependency is loaded when recording without filters. Missing optional
services produce installation guidance. `--sdl-root` selects a source runtime;
an installed/importable SDL runtime also works without a checkout.

## Connect and discover

Start the application first. In another terminal, connect to its endpoint:

```sh
python3 -m ose.cli record --endpoint /path/to/application/build/endpoint.json
# Or specify the broker directly:
python3 -m ose.cli record --host 127.0.0.1 --port 7000
```

With no connection options, the console uses `build/endpoint.json` when present,
otherwise `127.0.0.1:7000`. All points are listed on connection. `list` refreshes
the directory; `list sensor_*` narrows the display. Points are output topics
such as `sensor_1.signal`, including outputs with no model subscriber. The local
broker also exposes externally declared topics. An unavailable point can be
selected for later use, but cannot be recorded until its producer is available.

```text
record> list
record> show alarm.event
record> select sensor_*.signal analyzer_*.result alarm.event
record> selected
record> filter alarm.event state == 1 && rms > 1.0
record> start capture.pcapng
record> status
record> stop
record> filter alarm.event off
record> unselect sensor_*.signal
record> start summaries.mcap mcap
record> stop
record> quit
```

These point names are from Vibration Watch; no application names are built into
the console. `show` displays the root and complete SDL catalogue, including field
names, types, enums and nested declarations usable by filters. List numbers may
replace point names. Numbers follow the most recent directory ordering; names
are preferable in scripts. Selection and filters are frozen while recording.

`start` without a path generates a UTC timestamped `.pcapng` filename in the
current directory. Paths are local to the recorder process, even for a remote
application. Quote paths containing spaces. Files are created exclusively;
existing files are never overwritten. The parent directory must already exist.
The filename extension does not select the format: pass `mcap` explicitly.

## Content filters

`filter POINT EXPRESSION` prepares an SDL predicate against that point's current
catalogue and root. Invalid field names and expressions are refused immediately.
The console preserves quotes inside expressions, for example `label == 'motor 1'`.
Use SDL's operators, field paths, array indices, `any`/`all`, enum constants and
functions such as `len`, `abs` and `isfinite`; see the selected SDL checkout's
`docs/filtering.md` for the expression language. No Python `eval` is used.

Filters run at the recorder on the original wire payload before writing. Match
keeps a message; NoMatch and Indeterminate skip it and increment separate counters.
An evaluation Error ends the recording with a diagnostic. The unfiltered stream
still crosses the network: filters do not reduce application-to-recorder traffic.
Wire quantization therefore affects predicate results exactly as in SDL.

## Distributed applications

Connect to the coordinator using a client certificate signed by the application
CA. A separate certificate identity such as `recorder` is suitable:

```sh
ose-ctl record --distributed --host coordinator.example --port 7000 \
  --tls-ca ca.crt --tls-cert recorder.crt --tls-key recorder.key
```

The coordinator advertises live worker endpoints. The recorder opens a separate
connection to each selected producer and verifies its exact certificate identity.
Those endpoints must be reachable and resolvable from the recorder, for example
inside the cluster network or through a VPN. It needs no worker installation and
does not join the READY barrier. OSE does not expose cluster Services publicly.
For loopback-only distributed demonstrations use `--insecure` instead of TLS.

Worker capture is read-only and authorized by the current coordinator generation.
Any client certificate trusted by the application CA can read recording points;
v1 has no separate recording role or per-topic access rules. The local broker
uses its existing plaintext endpoint and respects `broker.external.enabled`.

Direct payloads are captured at their producer, without routing them through the
coordinator. A broker route is captured at the same worker publication boundary.
An observation is a publication, not evidence that downstream processing completed.
There is no historical replay or network ACK-to-disk guarantee.

## Bounds, stopping and errors

Application-side capture queues drop their oldest waiting record on overflow.
They never await the recorder or reserve a business input. `--capacity` defaults
to 256 messages and `--byte-budget` to 8 MiB **per selected producer endpoint**.
Each service allows at most 16 capture sessions, with aggregate reserved byte
budgets of at most 64 MiB, at most 4096 records and 128 points per session.
The console accepts up to 32 producer endpoints per recording. Frame limits still
apply; oversized capture replies are counted separately rather than interrupting
business delivery. In-flight network frames and one writer operation per feed
are additional to these queue budgets. Capture adds CPU and network work.

The recorder serializes disk IO and filter evaluation on a dedicated thread.
`status` and the final summary report written, filtered, indeterminate, dropped
and oversized counts. The final `ose.session` recording metadata retains these
counts and any failure. Inspect them before treating a recording as complete.

`stop`, `quit`, stdin EOF, Ctrl+C and SIGTERM stop new capture, drain admitted
queues and finalize the file. Stop waits for pending disk writes. SIGKILL, process
crashes and power loss can leave an incomplete file; SDL validation detects that.
Ordinary close is not a power-loss persistence guarantee.

A source disconnect, worker replacement, catalogue/generation change or disk
error ends the recording explicitly. Other application components keep running.
Already written messages are finalized when possible; unwritten data cannot be
recovered. Refresh discovery and start a new file after recovery. If the broker
or coordinator restarted, use `reconnect` to reopen its control connection first
(at the same host/port). The console
does not silently reconnect across an unknown gap.

Log and publish timestamps both use the recorder's Unix arrival time, since OSE's
publication protocol does not provide a Unix production timestamp. Model clock
fields such as `time_ns` stay untouched inside payloads. A channel's metadata
records its source generation; the container sequence is the OSE publication
counter modulo 2^32, independently of any application counter in the payload.
Cross-producer file order is recorder write order, not a global causal ordering.

## Scripted sessions and reading files

Repeat `--command` to execute the same console language without prompting:

```sh
ose-ctl record --endpoint /path/to/build/endpoint.json \
  --command 'select *.result alarm.event' \
  --command 'filter alarm.event state == 1' \
  --command 'start capture.pcapng' --command 'wait 10' --command stop
```

Commands can also come from stdin. Script errors return a nonzero exit status;
the file is closed on exit. `help` lists all commands. Use SDL's existing tools
to inspect, validate or replay the resulting file, without a running application:

```sh
python3 "$SDL_ROOT/tools/recording/sdl-recording" info capture.pcapng
python3 "$SDL_ROOT/tools/recording/sdl-recording" validate capture.pcapng
python3 "$SDL_ROOT/tools/recording/sdl-recording" inspect capture.pcapng --topic alarm.event --limit 3
```

From the OSE root, `make test-recording` checks both formats, unchanged payload
bytes, filters, bounded overflow, failure cleanup, scripted CLI shutdown and
authenticated distributed capture. Install/build the optional SDL services first.
