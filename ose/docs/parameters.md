# Shared parameter blocks

OSE retains complete typed SDL blocks at named paths and distributes their latest
values to explicitly bound clients. Several clients may read or write a block.
The last write processed by the service replaces the entire previous value.
There are no revisions, locks, merges or business acceptance callbacks.

## Model and deployment

Declare each block in `shared_parameters` with a message `root`, an optional
`schema` path (default: the model's `schemas`) and a complete JSON `initial` value:

```json
"shared_parameters": {
  "machines/press_1/detection": {
    "root": "DetectionParams",
    "initial": {"enabled": true, "threshold": 80.0, "hysteresis": 5.0, "window_ms": 200}
  }
}
```

Messages, nested records, fixed arrays, repeated/Packed sequences, enums and
optional fields use their SDL types. Enums accept labels or integer values;
complex values use `{"real": ..., "imag": ...}`. Required fields must be supplied,
including repeated fields; optional fields may be omitted or null. Unknown JSON
fields and invalid scalar/array values are errors. OSE encodes initial values
during build/validation. Contracts stay fixed during a run.

Each component declares local bindings:

```json
"parameter_bindings": {
  "detection": {"path": "machines/press_1/detection", "access": "read"},
  "security": {"path": "atelier/security", "access": "read"}
}
```

Access is `read`, `write` or `read_write`. Readers receive current values before
`on_start`, then updates between callbacks. A writer-only binding needs no initial
value in the component. Shared parameter paths form a namespace, without implicit
inheritance or subtree subscriptions. A component may bind several blocks and
several components may bind the same block. Existing static `parameters` remain
available through the existing APIs.

External clients receive rights in the deployment:

```json
"parameter_clients": {
  "operator": {
    "read": ["machines/press_1/detection", "atelier/security"],
    "write": ["machines/press_1/detection"],
    "token": "example-local-token-change-me"
  }
}
```

Local external sessions require the configured token (at least 16 characters).
Distributed sessions authenticate their exact client identity with mutual TLS;
their token can be omitted. Component rights come from their bound worker session
and compiled plan. Local component sessions retain the existing trusted-host model.
An external parameter session cannot impersonate a worker or publish message topics.

## Generated component APIs

| Language | Read binding `detection` | Write binding `detection` |
| --- | --- | --- |
| Python | `ctx.parameters_detection()` | `ctx.set_parameters_detection(value)` |
| C | `parameters_detection(ctx)` | `set_parameters_detection(ctx, &value)` |
| Rust | `ctx.parameters_detection()` | `ctx.set_parameters_detection(&value)?` |
| Java | `ctx.parameters_detection()` | `ctx.set_parameters_detection(value)` |
| Ada | `Parameters_Detection (Ctx)` | `Set_Parameters_Detection (Ctx, Value)` |

Methods are generated only for the declared access. Values are generated SDL
messages/records. Python and Java reads return independent decoded objects; C and
Rust return borrowed constant references valid during the callback; Ada returns a
record value. Reads do not use the network. Mutating a local copy has no service
effect; use the setter to send a complete block.

Writes are allowed from `on_start` and tick/input callbacks, not `on_stop` or
background tasks. They synchronously return the service's technical result: the
contract, size and write right have been checked and the value stored. They do
not wait for readers to apply it. A read/write component retains its current
callback snapshot even after its own write; it receives that value through its
subscription before a later callback. There is no atomic switch across blocks
or across components. A parameter change alone does not trigger a business tick.

The adapter manages network subscriptions. Generated runtimes decode typed blocks
and keep their values stable for each callback. Invalid wire data does not replace
a valid cached value or enter business code. Business constraints belong at the
writing application, or in an explicit application component.

## Retention, reconnects and limits

The local broker or distributed coordinator owns the same parameter service.
A slow reader retains only a pending indication per block and receives the latest
value when it next polls. Intermediate values may disappear. A disconnected
reader releases its subscription and fetches current values on reconnect. A
replacement worker receives current values before starting its component.

Values live in RAM. A broker/coordinator restart restores the plan's initial
values. The service has no journal and does not restore runtime writes.
An interrupted write may have reached the service even if its reply was lost;
read the current value after reconnecting rather than blindly retrying an old write.

Limits: 4096 declared blocks, 256 bindings per component or paths per subscription,
1024 subscriptions/external sessions and 64 MiB of current encoded values in total.
Individual blocks and catalogues must fit the existing transport and 1 MiB CH/1
frame limits. Status reports expose block roots, reader counts and storage bytes;
reading values requires the block's read right.

## CLI

From an application's root with a compiled plan and running broker:

```sh
export OSE_PARAMETER_TOKEN='example-local-token-change-me'
ose-ctl parameters list --client operator
ose-ctl parameters get machines/press_1/detection --client operator
ose-ctl parameters set machines/press_1/detection --client operator --file detection.json
ose-ctl parameters watch machines/press_1/detection --client operator --count 3
```

After sourcing the framework's `setenv.sh`, the shell shortcuts are:

```sh
params-list
params-get machines/press_1/detection
params-get '/detection$'
params-get
params-set --file parameters.json
params-set machines/press_1/detection --file detection.json
params-watch machines/press_1/detection --count 3
```

They run from `OSE_APP_ROOT` and default to client `operator`. Set
`OSE_PARAMETER_CLIENT` or pass `--client NAME` to use another identity.
When sourcing, `setenv.sh` loads `OSE_PARAMETER_TOKEN` for the selected client
from `build/plan.json`, or the first existing `deployment.local.json` /
`deployment.json` before a build. An existing plan takes precedence even when
the client has no token. Explicit environment values, including an empty token,
are preserved; the script never prints the token. Sourcing again refreshes an
automatically loaded value, including when switching applications or clients.
Set `OSE_PARAMETER_CLIENT` before sourcing when selecting another client's token;
an explicit `--client` alone does not select a different token automatically.
`--token-env` still selects a manually managed environment variable.
Relative filenames resolve from the application directory.
All CLI options pass through; `params` shows help and `params ACTION ...` is
the general shortcut.

### Export and restore blocks by path

The `params-get` shortcut always includes paths in its output. Single-block and
multiple-block exports use the same JSON shape:

```json
{
  "machines/press_1/detection": {
    "enabled": true, "threshold": 85.0, "hysteresis": 5.0, "window_ms": 200
  }
}
```

Use `params-get PATTERN` to search paths with a Python regular expression.
The pattern is optional: `params-get` alone exports every listed block.
`params-get-all` remains a synonym. Use `params-get --exact PATH` to select
one literal path. Quote patterns to keep the shell from expanding them.
Anchor with `^` / `$` when needed; for example `^machines/press_1/` selects one
machine and `/detection$` selects detection blocks. Every selected block must be
readable. A pattern matching nothing produces `{}`; invalid patterns fail.

`params-set --file FILE` reads the embedded paths, without an `--all` option:

```sh
params-get > parameters.json
params-set --file parameters.json
params-get '/detection$' | params-set --file -
```

The ordinary CLI supports `ose-ctl parameters get --export [PATTERN] --client operator`
and `ose-ctl parameters get --export --exact PATH --client operator`. Export mode
interprets the positional argument as a pattern, or selects all paths when omitted.
`ose-ctl parameters get --client operator` with no path also exports all blocks.
For compatibility,
`ose-ctl parameters get PATH` without `--export` still returns only the value.
An explicit path on `set` likewise continues to accept a bare block, rather than
a path-keyed export. `watch` keeps its existing `{path, value}` event format.

Imports describe and validate every block before the first write; empty imports
are valid no-ops. They replace complete blocks, and each path needs write access.
Reads do not form an atomic snapshot; writes are sequential, not transactional.
A service or network failure can leave a partially applied import. The error
names the failed block and lists acknowledged writes; a lost reply can leave the
failed block's state uncertain, so read current values before retrying. Grouped
imports have the same total 1 MiB JSON input limit as single-block writes.

For `set`, `--file -` reads a complete JSON block from stdin instead of a file:

```sh
params-set machines/press_1/detection --file - <<'JSON'
{"enabled": true, "threshold": 85.0, "hysteresis": 5.0, "window_ms": 200}
JSON
# Pipes and redirected files work as well:
cat detection.json | params-set machines/press_1/detection --file -
```

The ordinary `ose-ctl parameters set ... --client operator --file -` command
supports the same input. File and stdin reads are bounded to 1 MiB and reject
duplicate JSON keys; the service applies the same SDL validation to both.

The CLI reads `build/endpoint.json` and finds SDL through `build/plan.json`,
`SDL_ROOT`/`--sdl-root`, or an installed Python runtime. Use `--host` and `--port`
for explicit endpoints. `--token-env NAME` selects another token environment
variable. Distributed access uses `--distributed --tls-ca ... --tls-cert ...
--tls-key ...`; the certificate CN must match `--client`. Explicit loopback
development may use `--distributed --insecure` with a configured token.

## External SDKs

Python exposes an async client independently of a model or checkout (put the SDL
Python runtime on `PYTHONPATH`):

```python
from ose.parameters.client import ParameterClient

async with await ParameterClient.connect("127.0.0.1", 7000, "operator", token=token) as client:
    block = await client.block("machines/press_1/detection", DetectionParams)
    value = await block.get()
    value.threshold = 85.0
    await block.set(value)
    await client.subscribe([block.path])
    update = await client.next()  # None after an empty bounded long-poll
```

Without `DetectionParams`, a Python block reads/writes JSON-shaped dictionaries.
Distributed Python clients pass a client SSL context from `ose.transport.secure.contexts`
and `distributed=True` to `connect`.

Rust: `sdl_chain_client::parameters::ParameterClient` supplies `connect`,
`connect_tls`, `list`, `describe`, `get`, `set`, `subscribe`, `next` and `unsubscribe`.
`describe` returns the root/catalogue; encode/decode raw SDL bytes with the
application's generated SDL runtime. The crate keeps its dependency-free Cargo
manifest. Native TLS loads OpenSSL 1.1/3 at runtime and verifies the CA and the
single exact `coordinator` certificate CN.

C: include `chain_parameters.h` and `chain_client.h`. Use
`cenv_parameters_connect` (NULL TLS paths for token-authenticated local TCP),
`cenv_parameter_block`, `cenv_parameter_get` and `cenv_parameter_set` with registered
SDL types. Release returned messages with `type_free`; free blocks before their
borrowed client. `cenv_parameters_request` exposes subscriptions and other RPCs;
`cenv_parameter_decode` decodes a returned update for its block. Packet strings and
bytes remain borrowed until `cenv_parameter_packet_free`; extracted text fields
are owned and use `cenv_string_free`. Link `chain_client.c` and the native Rust SDK.

Java: `org.sdlchain.client.ParameterClient` uses the existing `client-java` JNI
library. `client.block(path, DetectionParams.class)` provides typed `get`/`set`;
`subscribe`/`next` deliver packets decoded with `block.decode(packet)`. The
seven-argument `connect` accepts token and CA/certificate/key paths for native TLS.

Ada: `Chain_Parameters` provides a controlled client, `Request`, `List`,
`Subscribe`, `Next` and `Unsubscribe`. Instantiate `Chain_Parameters.Typed` with
the SDL message type, root, catalogue and encode/decode functions to obtain typed
`Open`, `Get`, `Set` and `Decode_Update`. An empty `Next` packet has an empty path.
`client-ada` includes these units and the same native TCP/TLS engine.

The synchronous native/Java/Ada channels serialize requests. A long-poll is bounded
to 250 ms; clients drive their own polling loop. Finish calls before closing their
client. Reconnect explicitly and resubscribe to receive current state.

## Wire operations and CH/1

Parameter RPCs use CENV/1 with ordinary SDL payloads, without S2PS envelopes.
`parameters_open` selects an authenticated client session. List/describe/get/set,
subscribe/next/unsubscribe operations use exact paths; describe returns the
catalogue as binary UTF-8. Subscribe supplies a `paths` array; next returns one
`path` and SDL payload, or `empty: true`. Component sessions use existing identity
and worker-generation checks instead of `parameters_open`.

CH/1 adds `U` (adapter → component: alias/catalogue/payload, INPUT layout), `S`
(component → adapter: alias/payload, PUBLISH layout) and `A` (adapter → component:
byte 1 for success, or byte 0 plus UTF-8 error). Initial readable `U` frames follow
INIT before `on_start`; their count comes from the bindings. READY byte `0x03`
advertises timestamped ticks and shared parameters. Legacy empty/`0x01` READYs
remain valid for components without shared bindings. Regenerate containers after
adding bindings. A setter waits for its `A` while the engine handles `S` during
initialization or the active tick. Updates never interrupt a callback.

## Simulation

The local simulation engine uses the same blocks and access rules. Initializers
run in stable graph order when shared parameters exist. All calculations in a wave
read the pre-wave state; component writes are collected and become visible after
the whole wave succeeds, in component order. Repeated writes to a block by one
component coalesce to its last value for that wave.

Scenario events may update complete blocks:

```json
{"version": 1, "events": [
  {"at": 1.0, "parameter": "machines/press_1/detection",
   "value": {"enabled": true, "threshold": 85.0, "hysteresis": 5.0, "window_ms": 200}}
]}
```

Events are validated before starting components and applied before calculations
at their timestamp. Traces include parameter paths and payload hashes, and status
counts parameter changes. Outputs remain reproducible across worker counts for
deterministic callbacks. Live recording/replay of parameter changes is a separate
extension; ordinary output recording retains its existing behavior.

See the runnable [shared parameter example](../examples/shared_parameters/README.md).
