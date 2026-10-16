# Generated component containers

A deployment component can select a generated container instead of supplying
an executable command:

```json
{
  "container": {
    "language": "python",
    "handler": "components/analyse/logic.py"
  }
}
```

The handler path is relative to the deployment document; existing substitutions
such as `{project}` also work. The component must specify exactly one of
`command` or `container`. `language` accepts `python`, `c`, `rust`, `java` and
`ada`, with a `.py`, `.c`, `.rs`, `.java` or `.adb` handler respectively. Java
requires the fully qualified `class`; Ada requires the package `unit` and its
`.ads` specification alongside the body. Existing command-based components
continue to use the same binary SDK protocol.

`build` generates the wrapper, component-specific SDL bindings, compilation
commands and executable command in the resolved plan:

| Language | Generated API and wrapper | Executable |
| --- | --- | --- |
| Python | `build/components/<name>/container.py`, `types/` | Python wrapper |
| C | `build/components/<name>/component.h`, `container.c`, `types/` | `build/components/<name>/component` |
| Rust | `build/components/<name>/src/container.rs`, `src/types/`, `Cargo.toml` | `build/rust-target/release/chain-component-<name>` |
| Java | `org.sdlchain.generated.<name>.API`, `Main`, typed bindings | JVM with generated classes |
| Ada | `Chain_<name>`, typed bindings, generated main | `build/components/<name>/component` |

C uses the compiler from `CC` (default `cc`), C99 and the SDL C runtime.
Rust builds offline with local paths to the SDK and SDL runtime; components
share a build target directory while retaining distinct executable names.
Optional deployment `build` commands run before generated compilation commands.
Native SDK sources are included in installed packages as well as the checkout.

Only generated files are replaced on rebuild. A missing handler receives
an initial callback skeleton; an existing handler is never overwritten. Handlers
must be outside the build directory. `validate` checks and generates temporary
artefacts without creating or changing handler files. Implement the generated
callback placeholders before running the chain.

## Python callbacks

For an `input` port carrying `Measurement` and a `result` output:

```python
from measurement import Measurement


def on_input(ctx, message: Measurement):
    ctx.publish_result(
        Measurement(sequence=message.sequence, value=message.value * 2)
    )
```

The container imports the generated bindings before loading the handler. Local
helper modules next to the handler can be imported normally. Each output gets
`ctx.publish_<port>(message)` with a generated type annotation; the generic
`ctx.publish(port, message)` is also available. Publication checks the declared
message class and serializes with that output's catalogue. `ctx.name` contains
the component name and `ctx.parameters` contains model parameters.

The container initializes codecs, loads the
handler, checks callback signatures, and calls optional `on_start(ctx)` before
sending READY. Optional `on_stop(ctx)` runs after STOP or an execution error once
initialization has reached `on_start`. Publication is allowed only from tick or
input callbacks, when the adapter is prepared to receive outputs. Exceptions
escape with a nonzero process status and stop the chain; failed ticks do not
send FINISHED. Publications already emitted before an exception are not rolled
back.

## Scheduling

Callback dispatch occurs at TICK, after all selected INPUT frames have arrived
and all payloads have been decoded. The adapter still determines readiness,
snapshot selection, FIFO consumption and periodic deadlines. Receiving a
network packet alone does not run user code.

- With named callbacks, each available input invokes `on_<port>(ctx, message)`
  once per tick, in model port order. Every declared input needs a callback;
  absent optional inputs are skipped. A timed-out required input invokes its
  callback with a null value.
- If `on_tick(ctx, inputs)` exists, it replaces named input dispatch. `inputs`
  is a complete dictionary of declared ports, with `None` for absent optional
  inputs and timed-out required inputs. This is useful for processing multiple required
  inputs. A multi-input handler skeleton uses this form.
- A source with no inputs supplies `on_tick(ctx)`. Its periodic trigger comes
  from the model.

A FIFO contributes one queued message per tick. A `latest` input supplies its
current state and can therefore present the same value across several periodic
ticks, or when another FIFO input triggers execution. No network ACK or retry
policy is changed: ACK still means admission into the adapter's RAM queue.

Set `"timeout": 0.5` on a required input to wait up to half a second for a value.
Timeouts start after the READY barrier and rearm after each completed tick.
The existing callback receives `None` in Python or `NULL` in C; Rust named
callbacks for timed inputs take `Option<&MessageType>`. No extra timeout handler
or event metadata is required. Inputs without a timeout preserve their current
callback signatures and waiting behavior. Existing handlers are never rewritten;
when adding a timeout, update the handler to accept and process a null value.
See [required-input timeouts](runtime.md#required-input-timeouts) for multi-input
readiness, periodic scheduling and latest-state behavior.

All callbacks are synchronous and execute in one component process. Generated
component and port names must be identifiers; C/Rust names must additionally be
portable ASCII identifiers and ports cannot be language keywords or C macros.
Input names `start`, `stop` and `tick` are reserved for lifecycle and snapshot
callbacks.

## Timed input callbacks

Both execution engines expose a time value frozen during the callback:
Python `ctx.now()`/`ctx.time_ns`/`ctx.tick_index`, C
`chain_now(ctx)`/`chain_time_ns(ctx)`/`chain_tick_index(ctx)`, and Rust
`ctx.now()`/`ctx.time_ns()`/`ctx.tick_index()`. Seconds are floating point;
nanoseconds and tick indices are integers. Simulation uses virtual time starting
at zero; real-time adapters provide elapsed monotonic time since activation.
Use this clock rather than the system clock for time-dependent deterministic
callbacks. See [simulation](simulation.md) for protocol compatibility and guarantees.

For a required `input` carrying `Measurement` with `"timeout": 0.5`, the same
callback receives both messages and timeout nulls. A handler can choose to emit
no output on timeout, for example:

```python
from measurement import Measurement

def on_input(ctx, message: Measurement | None):
    if message is None:
        return
    ctx.publish_result(message)
```

```c
static int on_input(ChainContext *ctx, const Measurement *message) {
    if (!message) return 1;
    return publish_result(ctx, message);
}
```

```rust
fn on_input(&mut self, ctx: &mut Context, message: Option<&Measurement>) -> Result {
    if let Some(message) = message {
        ctx.publish_result(message)?;
    }
    Ok(())
```

The C function belongs in the generated callback table; the Rust method belongs
in the handler's `Callbacks` implementation, as shown below. A snapshot callback
receives null in the corresponding input field instead. Returning successfully
still completes the tick even if no output was published.

## C callbacks

Select `"container": {"language": "c", "handler": "components/logic.c"}`.
The generated header exposes the SDL message types, typed `publish_<port>`
functions and a `ComponentCallbacks` table. Port aliases `Input_<port>` and
`Output_<port>` also name the declared types, including qualified SDL types:

```c
#include "component.h"

static int on_input(ChainContext *ctx, const Measurement *message) {
    Measurement result = *message;
    result.value *= 2.0;
    return publish_result(ctx, &result);
}

const ComponentCallbacks component_callbacks = { .on_input = on_input };
```

Callbacks return **1 for success, 0 for failure**. Publication returns the same
status; propagate it, or use `chain_fail(ctx, "reason")` to report a failure.
Optional `.on_start` and `.on_stop` hooks accept `ChainContext *`. A source sets
`.on_tick` with signature `int callback(ChainContext *)`.

For a component with inputs, `.on_tick` has signature
`int callback(ChainContext *, const ComponentInputs *)` and replaces named input
dispatch when supplied. Each snapshot field is a `const MessageType *`; absent
optional inputs and timed-out required inputs are `NULL`. Without `.on_tick`, every input requires a named
callback; missing callback pointers are refused before READY.

`ctx->name` identifies the component and `ctx->config_json` contains its actual
INIT JSON, including parameters. `ctx->user_data` is available for user state:
initialize it in `.on_start` and release it in `.on_stop`. No JSON library is
required or imposed; native handlers can parse configuration with their chosen
library. Publication is only allowed during a tick.

Decoded messages and their nested data are borrowed for the duration of the
callback. Do not retain these pointers; use SDL `type_clone` when an owned copy
is needed. The container releases selected values after each successful tick,
and releases values and decoder caches on STOP or failure. A shallow structure
copy in the example is used only for immediate publication within the callback.

## Rust callbacks

Select `"container": {"language": "rust", "handler": "components/logic.rs"}`.
Implement the generated `Callbacks` trait on a public `Handler` that implements
`Default`:

```rust
use crate::container::{Callbacks, Context, Result};
use crate::types::measurement::Measurement;

#[derive(Default)]
pub struct Handler;

impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, message: &Measurement) -> Result {
        let mut result = message.clone();
        result.value *= 2.0;
        ctx.publish_result(&result)
    }
}
```

`Result` aliases `Result<T = (), Box<dyn std::error::Error>>`. Optional
`on_start`/`on_stop` methods default to success. A source implements
`on_tick(&mut self, ctx: &mut Context) -> Result`. For components with inputs,
the snapshot method is
`on_tick(&mut self, ctx: &mut Context, inputs: &Inputs<'_>) -> Result`; its default
implementation dispatches available values to named input methods. Snapshot
fields are `Option<&MessageType>`, so absent optional inputs and timed-out required
inputs are `None`. A timed named input method has signature
`fn on_input(&mut self, ctx: &mut Context, message: Option<&Measurement>) -> Result`;
the default snapshot dispatcher calls it even when the value is `None`.
Unimplemented input/tick methods return an error when first dispatched.

State belongs in `Handler` fields. `ctx.name` identifies the component and
`ctx.config_json` holds the actual INIT document, including parameters.
References passed to callbacks cannot outlive their snapshot. Use `clone()` to
retain an owned value. Returned errors stop the component without FINISHED;
unwinding panics also run `on_stop` before propagating. Forced process termination
cannot guarantee user cleanup hooks in any language.

## Catalogue compatibility

Typed input decoding uses the catalogue attached to the received message, with
the consumer's generated type. Compatible publisher schema evolution, including
different wire IDs, therefore preserves typed callbacks. Decoder caches are
bounded; no delivery state is persisted.

The build combines the schema modules needed by a component into its own binding
directory. Distinct schema overrides and SDL packages are supported, provided
they do not define conflicting module files or logical type bindings
within one component. Such conflicts are refused during compilation; give the
schemas distinct module/type names when a component needs both. Different
components have separate binding directories and processes, so they can use
different revisions of the same module.
Python schema modules must not shadow standard library or SDK modules; C and
Rust do not have this Python import restriction (for example, `types.sdl` works).

## Java and Ada callbacks

See [Java and Ada SDKs](java-ada.md) for typed handlers, complete snapshots,
nullable timed inputs, toolchain configuration and external clients. Both use
the same scheduler and CH/1 protocol in real-time and simulated execution.
