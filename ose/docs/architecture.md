# Architecture and model

The dependency direction is:

```
CLI -> modeling/compiler -> modeling/model + codec/SDL2
supervisor -> broker + adapter processes -> delivery + transport
adapter -> scheduler + component IPC
component -> language SDK + generated SDL codec
```

SDL has no dependency on this project. The codec facade uses the public SDL
generator CLI and runtime operations: catalogue description and identity,
prepare, compare_catalogues, dynamic decode and envelope pack/unpack. Generated
classes are enumerated using the public SdlMessage base class. No PoC internals
are imported. The dependency revision is recorded in `sdl-dependency.json`.

## Documents

Documents require integer `version: 1`; duplicate and unsupported keys are
rejected. Model `schemas` names a source file or directory relative to the model.
`components` is a map of component names to `inputs`, `outputs`, `trigger` and
optional `parameters`. A port has a logical `root`, optional overriding `schema`
and, for inputs, optional `required` (default true) and `timeout`. A timeout is a
finite positive number of seconds, accepts fractional values and is supported
only on required inputs. Omitting it preserves an unbounded wait. Names cannot
contain dots; an endpoint is `component.port`. Each output endpoint is a separate topic.

`subscriptions` is a list of `{id, from, to, qos?}`. IDs are unique. Each input
has at most one producer; required inputs must be connected. Connected roots
have the same logical name. All connections undergo publisher-to-consumer
compatibility checking with `require_required=True`; nested declarations are
allowed. Cycles are rejected.

Triggers are `{mode: "on_change"}` or `{mode: "periodic", period_ms: positive}`.
Sources require periodic triggers. Periodic deadlines start after completion,
so missed periods do not accumulate. Optional inputs do not gate readiness.
Required inputs must each have a usable value or an expired configured timeout.
An expired empty input contributes `None`/`NULL` to the tick; a required input
without a timeout continues to wait. One message per available FIFO input and
each latest state form a tick. All selected FIFO inputs are removed only on FINISHED.
An on-change component combines current latest values with queued FIFO events;
updates received during processing are handled in the next eligible tick.
Processing wakes on input admission, an input timeout or its periodic deadline;
there is no fixed 5 ms polling delay in the message-processing path.

Input timeout deadlines start after the READY barrier and are rearmed when the
adapter completes each successful tick. An expired empty input can trigger an
`on_change` tick, subject to readiness of the other required inputs. Periodic
components also obey their periodic deadline. Available data wins over timeout;
a retained latest state does not expire. No timeout callbacks or extra callback
event metadata are introduced. See [input timeouts](runtime.md#required-input-timeouts)
for examples and exact timing rules.

Deployment `components` covers exactly the model components. Each entry has an
argv `command` or a `container: {language: "python" | "c" | "rust", handler: "path/to/logic"}`
definition, optional `build` list of argv commands, `cwd`, `env`, and
`placement` (`local`, or Kubernetes constraints in distributed deployments). No shell interprets commands. `cwd` is
relative to the deployment file. Supported substitutions in commands and env:
`{python}`, `{project}` (model directory), `{sdl_root}`, `{build}`, `{generated}`.
The latter names the shared generated directory; schema overrides have separate
generated directories with hashes of their source paths.

Generated containers bridge the same component protocol to typed user
callbacks. The generated code is under `build/components/<name>`; user handlers
remain outside build artefacts and are never overwritten. See
[callback containers](callbacks.md) for callback selection and lifecycle rules.
Native containers compile with the chosen SDL checkout and bundled stdio SDKs.

Deployment `subscriptions` maps IDs to `{transport: "tcp" | "udp"}`. Model QoS
specifies the delivery requirements. Compilation resolves both into the plan,
never silently weakening an unsupported profile.

## Process lifecycle

The runtime also provides a [functional simulated engine](simulation.md), selected
by `run --mode simulated`. It uses the same compiled plan and component protocol,
with central virtual-time coordination and direct bounded RAM admission instead
of network adapters and broker processes. Parallel calculations cross deterministic
publication barriers, so worker count does not change the logical execution.

See [runtime execution, ticks and message flow](runtime.md) for a detailed
walkthrough of startup, input selection, callbacks and delivery confirmations.

The supervisor hosts the broker and launches a Python adapter process per
component. Each adapter starts its component process. All components initialize
and send READY before the supervisor enables publication and delivery. Startup
has a 20-second readiness limit. A process failure stops OSE execution.
SIGINT/SIGTERM stop adapters, their children, then the broker. Startup and run
failures also clean up children. Infinite execution is the default; `--duration`
supports demonstrations and integration tests.

The generated plan contains absolute paths and is rebuilt for another machine.
It is not intended as a portable deployment bundle. `endpoint.json` enables
live inspection and `last-status.json` stores the final available counters.
Messages, catalogues, retry state and deduplication history exist only in RAM.
No runtime state directory or database is created; restarting begins with empty
queues and the model's catalogues. Diagnostic files do not restore delivery state.
There is no automatic restart.

## Public boundary

The CLI and the SDK protocol are the supported v1 interfaces. Python internal
classes are implementation details. The resolved plan is versioned but intended
for the matching CLI version. Future transports implement byte movement;
delivery policy stays in the common delivery engine. The
[distributed real-time engine](distributed.md) provides remote workers, direct
TCP routes and Kubernetes export; the ordinary supervisor remains local.

The optional [interactive recorder](recording.md) discovers live publication
points and component-owned internal points through read-only RPCs. Components
declare typed, namespaced diagnostic points before READY; their contexts own
hash registries and receive activation updates between ticks. Internal samples
use a dedicated CH/1 path and never enter application subscriptions. Bounded
taps observe broker/worker publications and diagnostics;
the external console selects filters and writes SDL recordings on its own disk.
It has no model component, input reservation or role in the startup barrier.
