# Deterministic simulated execution

`simulated` is a functional execution engine for the same compiled graph and
Python, C, Rust, Java and Ada callback containers used by `realtime`. It runs real component
processes, controls their ticks centrally and advances a virtual clock without
waiting for wall time. The default engine remains `realtime`.

Given identical models, scenarios, executables, parameters and initial state,
successful simulations produce identical messages and byte-identical logical
traces for `--workers 1`, `--workers 2` and larger worker counts. This assumes
deterministic application code and a compatible execution environment. It does
not promise identical floating-point results across different platforms or builds.

## Run a simulation

Build normally, then select the execution engine at runtime. No model or
deployment field changes are required:

```sh
make build
PYTHONPATH=src python3 -m ose.cli run --plan build/plan.json \
  --mode simulated --duration 60 --workers 4 --trace build/simulation.jsonl
PYTHONPATH=src python3 -m ose.cli inspect --last
```

`--duration` is required and specifies a finite positive virtual horizon in
seconds. Events at the horizon are excluded: a duration of `1` executes events
in `[0, 1)`. When no immediate work or future event remains, execution ends
early with `stop_reason: "quiescent"`, listing any required inputs still waiting.
An empty input without a timeout does not make the engine wait in real time.

`--workers` defaults to `1` and must be a positive integer. It limits concurrent
component calculations, using the separate Python, C, Rust, Java and Ada processes already
provided by the framework. It does not allocate CPU affinity or parallelize a
single callback. A sequential graph offers little parallel speedup.

`--scenario`, `--trace`, `--workers` and `--max-tick-output-bytes` are specific to
simulated execution. `--port` is rejected in simulated mode. No network broker,
adapter subprocesses or live external clients participate in this engine.
Only `inspect --last` is available after a simulation; live network inspection
continues to belong to the real-time engine.

## Virtual time and waves

The clock starts at zero after every component reports READY. Internal timestamps
are unsigned 64-bit nanoseconds. Decimal durations round to the nearest
nanosecond, with halves rounded upward and positive sub-nanosecond durations
clamped to one nanosecond. Nonfinite, negative and out-of-range times are rejected
before launching components. Initial periodic source ticks occur at time zero.

At each virtual timestamp the coordinator:

1. Injects scheduled external messages and advances pending admissions.
2. Selects ready components using the normal FIFO/latest/required-input rules.
3. Forms a wave from ready components with no ready ancestor. The graph's stable
   topological order, breaking ties by component name, determines their order.
4. Freezes and reserves every snapshot before starting any callback in the wave.
5. Runs up to `workers` component calculations simultaneously and collects their
   publications until every component reports FINISHED.
6. Makes those publications visible in component order, then emission order.
   Subscription admission uses subscription-ID order.
7. Repeats at the same timestamp while immediate work remains, or jumps directly
   to the next useful deadline or scenario event.

The wave is identical for every worker count. With one worker, its calculations
run successively but still cross the same publication barrier. A component that
finishes physically first cannot make its data visible early. Ancestor and
descendant components do not run in the same wave, even if both already have data.

A callback has zero simulated execution cost. A slow computation or `sleep` can
increase the physical runtime without advancing virtual time. Periods and input
timeouts rearm after logical tick completion. An available value wins over an
expired input timeout, including a same-time publication from a ready ancestor.
Required empty inputs with expired timeouts still pass `None`/`NULL` to the normal
callback. Retained latest values do not expire.

## Time in callbacks and protocol compatibility

Use the framework clock for time-dependent application logic:

| Language | Seconds | Exact nanoseconds | Component-local tick index |
| --- | --- | --- | --- |
| Python | `ctx.now()` | `ctx.time_ns` | `ctx.tick_index` |
| C | `chain_now(ctx)` | `chain_time_ns(ctx)` | `chain_tick_index(ctx)` |
| Rust | `ctx.now()` | `ctx.time_ns()` | `ctx.tick_index()` |

The timestamp is frozen throughout a callback and its publication calls. Tick
indices start at one. Context values are zero before the first tick and retain
the last tick's values during shutdown. In real-time execution, updated containers
receive elapsed monotonic time since their adapter was enabled. Activation origins
are local to each adapter; they are not a synchronized clock across components.

Generated containers opt into the timestamp extension of [CH/1](../sdk/protocol.md)
in READY. Regenerate existing containers to use simulation; handlers keep their
existing signatures and are never overwritten. The real-time engine still accepts
legacy empty READY/TICK frames. Simulation refuses legacy components explicitly.
The updated containers also accept historical empty TICKs in protocol-level tests;
these carry time zero and increment the local tick index.

Raw Python components opt in with `ComponentSession(timed_ticks=True)`. Raw C
components call `chain_ready()` and decode ticks with `chain_tick()`. Raw Rust
components send `R` with `sdl_chain_component::TICK_CONTEXT` after initialization
and decode ticks with `sdl_chain_component::tick()`. The historical Rust
`initialize()` convenience function retains its empty READY behavior.

## External scenarios

To use a PCAPNG or MCAP capture instead of separate envelope files, see
[recording replay](replay.md) through `ose-ctl replay`.

A scenario is a version-1 JSON document:

```json
{
  "version": 1,
  "events": [
    {"at": 0.15, "topic": "external.offset", "envelope": "offset-1.s2ps"},
    {"at": 0.35, "topic": "external.offset", "envelope": "offset-2.s2ps"}
  ]
}
```

`at` is nonnegative virtual time in seconds. Topics must be declared in the model's
`external_topics`; scenarios cannot impersonate internal component outputs.
Envelope paths resolve relative to the scenario file. Each file contains a binary
S2PS/2 envelope with the declared topic's catalogue identity and root message.
This first version uses that exact declared catalogue, rather than loading
alternative publisher catalogues from scenario files.

The engine validates all inputs before starting components and processes events
by timestamp, then original file order. It retains file paths and SHA-256
fingerprints instead of keeping every future payload in RAM. Each envelope is
checked again when injected; changing an input file during a run fails explicitly.
Live network injection is unavailable because its arrival order would depend on
the physical environment.

## Queues, waits and transport semantics

Each subscription owns one bounded input queue in RAM. The same FIFO/latest
selection, capacity, reservation and `reject_new`/`drop_oldest` rules apply.
There is no additional sender transport queue in functional simulation, so it
does not reproduce the real engine's physical backpressure behavior.

For `overflow: "wait"`, a full destination creates a pending admission with a
virtual `wait_timeout_ms` deadline. Other destinations of that publication and
other components continue to progress. A consumer can free capacity before the
deadline, or the pending admission returns `full` at expiry. Capacity available
at the deadline is tried before refusing admission. Subsequent publications from
the same component wait behind the unfinished publication. Selected FIFO inputs
remain reserved until all publications of their tick have resolved, then the
component's periods and input deadlines rearm.

TCP/UDP, ACK and retry settings remain in the common compiled plan but do not
create socket traffic, loss, latency or retransmissions in this engine. Network
frame/datagram limits do not model delivery here; CH/1's 1 MiB frame limit still
applies at the component boundary. Oversized outgoing INPUT frames are refused
explicitly. Transport fault simulation can be added as another layer later.

## Bounds, failures and observations

`--max-tick-output-bytes` defaults to 16 MiB per component per tick, counting the
encoded CH/1 publication bodies including opcode and port. Crossing this limit
fails the run instead of spilling to disk or waiting forever for FINISHED.
The same byte limit bounds aggregate pending external envelope bodies. Payloads
and queue records are volatile; there is no message persistence or recovery.

If a callback fails, the entire current wave's outputs are discarded and all
component processes are stopped. Earlier completed waves remain visible. This
does not roll back user code's filesystem or other external side effects.
Ctrl+C/SIGTERM cancels the run and uses the bounded STOP/terminate/kill cleanup.
Startup keeps infrastructure deadlines; callbacks have no wall-time deadline by
default. Physical infrastructure failures abort a run rather than producing a
different successful logical execution.

The optional JSONL trace records virtual timestamps, waves, selected input IDs,
publications with SHA-256 envelope fingerprints, terminal admission results,
logical completions and the stop reason. It contains no payload journal, random
epoch, PID, physical timings or stderr ordering. A streaming trace hash is also
computed when no trace file is requested. The final JSON summary and
`last-status.json` report this hash, virtual time, tick/publication counts, queue
metrics, waiting inputs and any pending ticks at the horizon.

For reproducibility, callbacks must not let wall clocks, unseeded randomness,
live I/O, shared mutable files or internal thread races affect their outputs.
Standard output remains reserved for CH/1. Application stderr logs and external
side effects are outside the deterministic trace contract.

## Runnable parallel example

The example has a C source, independent Python and Rust branches, and a Python
join with an optional externally scripted offset. Run from `ose`:

```sh
make build MODEL=examples/simulated/model.json DEPLOYMENT=examples/simulated/deployment.json
export PYTHONPATH="$PWD/src:$PWD/build/generated/shared/python:$PWD/../sdl/runtime/python"
python3 examples/simulated/make_scenario.py
python3 -m ose.cli run --mode simulated --duration 0.5 --workers 1 \
  --scenario build/simulated-scenario/scenario.json --trace build/sim-1.jsonl
python3 -m ose.cli run --mode simulated --duration 0.5 --workers 4 \
  --scenario build/simulated-scenario/scenario.json --trace build/sim-4.jsonl
cmp build/sim-1.jsonl build/sim-4.jsonl
```

For a separate SDL dependency checkout, replace the last PYTHONPATH entry with
`$SDL_ROOT/runtime/python`. Both branches can calculate concurrently, while the
join sees the same input pairs and offsets for every worker count.

`make test-simulated` runs the simulation regressions. `make check` includes this
suite after unit, Rust SDK and real-time integration checks.
