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
broker and workers do not need MCAP. Sources serving filtered captures need the
SDL native filter service; unfiltered capture does not load it.

For an installed OSE package, install `ose[recording]` and make the SDL
Python modules importable. For a source checkout, from the OSE root:

```sh
export SDL_ROOT=/absolute/path/to/sdl
make -C "$SDL_ROOT" recording-deps
export PYTHONPATH="$PWD/src:$SDL_ROOT/runtime/python:$SDL_ROOT/build/recording-deps"
```

Content filters additionally use SDL's optional native filter service. Build it
with the Python interpreter used by each recording source (the local broker or
distributed producer workers), and by the console for expression validation:

```sh
make -C "$SDL_ROOT" filter-python
python3 -m ose.cli record --sdl-root "$SDL_ROOT" --endpoint /path/to/application/build/endpoint.json
```

No filter dependency is loaded when recording without filters. Missing optional
services produce installation guidance. `--sdl-root` selects a source runtime;
it also discovers `build/recording-deps`, `runtime/filter/python` and
`build/filter/release` in that checkout when present. Restart the console after
building optional services. An installed/importable SDL runtime also works
without a checkout; in that case, make the optional modules importable through
installation or `PYTHONPATH`.
Broker/workers automatically discover `runtime/filter/python` and
`build/filter/release` under their plan's SDL root when needed. Container images
must build/copy these services for the container's Python version and platform.
The Vibration Watch Dockerfile builds them in its image builder. An upgraded
console refuses filtered capture against a source that does not advertise source
filtering support; it never falls back to transferring an unfiltered stream.

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

At the interactive `record>` prompt, use Up/Down to recall commands from the
current console session (up to 1,000 entries). Recalled commands can be edited
before pressing Enter. Down past the newest entry restores the unfinished line.

Each recording session carries an informational `label`, defaulting to
`recorder`, the recorder client's component name. Set it at launch with
`record --label moteur-1` (or `ose-ctl record -l moteur-1`). The console's
`label` command shows the current value; `label "essai moteur 1"` changes it
before the next recording. The label is frozen while a capture is running.
Labels accept nonempty text up to 256 UTF-8 bytes and need not be unique.

The label is sent to each source with `recording_open`, retained in source
session statistics, shown by recorder `status` and final summaries, and stored
in the file's `sdl.recording` and `ose.session` metadata (visible through
`record-info FILE`). Each source still assigns an independent UUID, and session
operations remain bound to the connection that created it. Labels do not
identify application graph nodes or grant access to another session.

```text
record> list
record> label moteur-1
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

`select` and `unselect` match each argument against both the point name and its
root message type (the type displayed by `list`). Matching is case-sensitive and
supports glob patterns: `*`, `?`, and `[abc]`. For example, `select SignalBlock`
selects every point carrying that message type; `select Signal* Analys?s` selects
matching signal and analysis types. Point names, types, patterns and list numbers
can be mixed in the same command. A pattern matching both names and types selects
the union of those points, preserving any existing filters.

`select all` selects all discovered recording points, like `select *`.
`unselect all` clears the selection. Unavailable points are included by selection
patterns and `all`; they must become available before recording can start.

`start` without a path generates a UTC timestamped `.pcapng` filename in the
current directory. Paths are local to the recorder process, even for a remote
application. Quote paths containing spaces. Files are created exclusively;
existing files are never overwritten at startup. The parent directory must already exist.
The filename extension does not select the format: pass `mcap` explicitly.

## File rotation and cyclic retention

Configure rotation before `start`; both settings default to `off`:

```text
record> max-size 100MB
record> cycle 3
record> start moteur.pcapng
record> status
record> stop
record> cycle off
record> max-size off
```

`max-size SIZE` accepts bytes (`1000000` or `1000000B`) and decimal suffixes
`KB`, `MB`, `GB` (1,000, 1,000,000 and 1,000,000,000 bytes). These are the only
supported size suffixes, along with `B`. Suffixes are case-insensitive;
fractions must resolve to whole bytes (`1.5KB` is 1,500 bytes). A bare
`max-size` shows the current setting; `max-size off` disables rotation.
`cycle N` retains at most N files, including the current file. A bare `cycle`
shows the setting; `cycle off` keeps every numbered file. N must be positive.
Cyclic retention requires an active size limit when starting a capture.
Queries remain available during recording, but settings can only be changed
while stopped. The console's scripted `--command` mode accepts these commands.

With rotation enabled, `start moteur.pcapng` opens `moteur_001.pcapng`. Subsequent
files use continuously increasing indices, padded to at least three digits:
`002`, `003`, ..., `999`, `1000`, etc. With `cycle 3`, the fourth file reuses the
oldest output under the new name `moteur_004.pcapng`, leaving `002`, `003`, `004`;
the next rotation leaves `003`, `004`, `005`. Each new capture starts at `001`.
With rotation disabled the requested filename is used unchanged.

Every segment contains its own catalogues, channels, label and final container
records and can be inspected or validated independently. Messages are never
split. The size is a rotation threshold: rotation occurs before the next message
after reaching it, and indivisible messages, indexes and closing metadata can
make the final file larger. MCAP buffers are flushed near the threshold to
account for messages still in chunks. An oversized first message stays in the
current file; an empty capture produces one valid file without rotating.

The parent directory must exist. Startup refuses an existing base filename or
any existing numbered file in the requested series. Subsequent name collisions
or externally replaced cyclic files end the capture explicitly, preserving
unrelated files. Only files created by this capture are eligible for reuse;
cyclic retention deliberately replaces their old contents. On filesystems
without hard links, reuse creates the next file exclusively and removes the
owned oldest file before writing the replacement.

`status` reports `max_size` in bytes (`null` when off), `cycle`, `base_file`,
current `file`, `file_index`, `file_count` and `rotations`. `file_message_count`
and `file_topics` describe the current file; session `counts` and `topics` are
cumulative, including messages in cyclic files already replaced. Each segment's
`ose.session` metadata includes this snapshot and a `final` flag indicating
whether it was closed at capture termination rather than for rotation. Rotation
does not reopen source sessions or change their filters and counters.

## Content filters

`filter POINT EXPRESSION` prepares an SDL predicate against that point's current
catalogue and root. Invalid field names and expressions are refused immediately.
The console preserves quotes inside expressions, for example `label == 'motor 1'`.
In the interactive console, do not quote the entire expression. Field paths are
relative to the root message: if `show 3` reports root `Alarm` with field
`machine_id`, use `filter 3 machine_id == 2`. The point must be selected first,
and recording must be stopped before changing its filter.
Use SDL's operators, field paths, array indices, `any`/`all`, enum constants and
functions such as `len`, `abs` and `isfinite`; see the selected SDL checkout's
`docs/filtering.md` for the expression language. No Python `eval` is used.

Filters are prepared per source session at `recording_open` and evaluated on
the original wire payload at the local broker or distributed producer worker,
before capture queue admission, reply serialization and network transfer.
Match keeps a message; NoMatch and Indeterminate skip it and increment separate
source counters. Only matching messages consume capture queue capacity or cross
the network to the recorder. Two recorders have independent predicates, counters
and queues, including when they select the same topic. Closing, stopping or
disconnecting one session releases its predicates without affecting the other.
An evaluation Error stops only the affected capture session with a diagnostic;
other capture sessions and application delivery continue. Filters do not modify
application subscriptions or their payloads. In local execution the broker still
receives application publications; in distributed execution the recording data
already flows directly from each worker to the recorder.
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
are additional to these queue budgets. Source predicate evaluation runs
synchronously on the publication loop and adds CPU work. It performs no disk IO
and never waits for the recorder. Filtering reduces capture serialization,
queueing and network work, but does not guarantee a reduction in source CPU for
every predicate or workload.

The recorder serializes disk IO on a dedicated thread. It does not re-evaluate
source filters. Source filter plans are released on stop, close, disconnect,
generation changes, evaluation errors and source shutdown.
`status` and the final summary report written, filtered, indeterminate, dropped
and oversized counts as readable text. The summary includes the state, label,
size and retention settings, current file and rotation details, selected points
with their filters, per-point written counts and any error. Missing counters
are displayed as zero. Before the first capture, `status` shows the settings
and selection with state `idle`. The final `ose.session` recording metadata retains these
counts and any failure. Inspect them before treating a recording as complete.
`counts.received` counts matching messages received over the network;
`counts.filtered` and `counts.indeterminate` aggregate the source counters,
also present in `capture_counts`. `capture_counts.delivered` counts records
sent by the sources, excluding those rejected by a content filter.

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
python3 "$SDL_ROOT/tools/recording/sdl-recording" inspect capture.pcapng --check
python3 "$SDL_ROOT/tools/recording/sdl-recording" inspect capture.pcapng --topic alarm.event --limit 3
record-inspect capture.pcapng --summary --from +1s --to +5s --format table
record-inspect capture.pcapng --check --from +1s --duration 4s --format table
record-inspect capture.pcapng --check --summary --topic alarm.event --format json
```

After sourcing the framework's `setenv.sh`, `record-inspect --summary` reports
`message_count`, `message_count_by_topic`, `start_time_ns`, `end_time_ns` and
`duration_seconds` for the selection. Duration is the difference between the
last and first selected log times, in seconds, and is zero for fewer than two
messages. Declared selected points with no messages in the range have a
zero count; an empty selection has null time bounds. `--check` also validates
selected message bodies and adds `valid: true` on success. The flags can be
combined. Neither mode prints individual messages.

Both modes accept repeated `--topic` selections, `--from` (inclusive), `--to`
(exclusive), and `--format text|json|table`. Time bounds use `ns`, `us`, `ms` or
`s` units. Each bound is independently interpreted as an absolute timestamp
(`1700000000s`) or, with a `+` prefix, an offset from the earliest log time in
the entire file before topic filtering (`+1.5s`). Absolute and relative bounds
can be mixed. `--duration 4s` replaces `--to` and sets the exclusive end to
`--from + 4s`; with no `--from`, the interval starts at the earliest file log
time. Duration may be zero. `--to` and `--duration` cannot be combined. The
reported duration spans the selected messages, which can be shorter than the
requested interval. Reported time bounds remain absolute. Text is the shortcut's
default format. `--limit` does not restrict checking or summary counts. Container
integrity is always checked through the end of the file, including outside the
selection; only selected message bodies are decoded by `--check`. Errors return
a nonzero status without emitting a success report.

`record-check` remains a compatibility shortcut for `inspect --check` with JSON
output by default, and accepts the same options. The underlying `validate`
command remains available for complete-file validation and its original JSON
count report.

`inspect` and the `record-inspect` shortcut display all selected messages by
default. Add `--limit N` for a bounded preview. `--full` controls the detail of
arrays and strings within each message, independently of the message count.

Options taking values also accept `=`, for example
`record-inspect capture.pcapng --summary --from=+1s --duration=4s --format=table`.
Short options are `-b`/`--from`, `-e`/`--to`, `-d`/`--duration`, `-t`/`--topic`,
`-c`/`--check`, `-s`/`--summary`, `-f`/`--format`, `-n`/`--limit`,
`-a`/`--full`, `-i`/`--indent-width`, and `-h`/`--help`. For example:

```sh
record-inspect capture.pcapng -cs -b +1s -d 4s -f table
```

From the OSE root, `make test-recording` checks both formats, unchanged payload
bytes, filters, bounded overflow, failure cleanup, scripted CLI shutdown and
authenticated distributed capture. Install/build the optional SDL services first.
