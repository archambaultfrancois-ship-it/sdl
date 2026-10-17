# Java and Ada components and external clients

Java 7 and Ada 2012 have the same execution boundary as C and Rust: generated
SDL message types, CH/1 component IPC, typed callbacks, and CENV external clients.
Delivery uses bounded RAM queues. No persistence is introduced.

## Toolchains and deployment

Java containers require a JDK for building and a JVM for execution. Use JDK 7
itself or a JDK supporting `javac --release 7` (for example JDK 17). Compilation
checks the Java 7 API and emits Java 7 bytecode. JDK 21 removed release 7 support.
`CHAIN_JAVA_LOCAL_RELEASE=8` permits explicitly marked local smoke builds on
such a JDK; these builds do **not** establish Java 7 compatibility.

Ada containers require GNAT with the full runtime, a C compiler, and the public
SDL Ada/C runtime. Restricted embedded runtimes are not supported. External
clients additionally require Cargo to build the shared network engine.

```json
{"container": {
  "language": "java",
  "handler": "components/AnalyseHandler.java",
  "class": "app.AnalyseHandler"
}}
```

```json
{"container": {
  "language": "ada",
  "handler": "components/analyse_handler.adb",
  "unit": "Analyse_Handler"
}}
```

Handler paths are relative to the deployment. Java filenames match their class
name; Ada filenames follow GNAT conventions. Ada child units use `parent-child`
filenames. `validate` checks the model and generates sources without invoking a
Java or Ada compiler. `build` creates missing handlers and compiles executable
containers. Existing handlers are never overwritten. An incomplete Ada
specification/body pair is rejected.

Use `JAVA`, `JAVAC`, `JAR`, `JAVA_HOME`, `GNATMAKE`, `ADA_FLAGS`, `ADA_RTS`, `CC`,
and `CARGO` to select tools. No component build downloads a toolchain or contacts
a dependency registry. `SDL_ROOT` selects the separate SDL checkout.
`JAVA_HOME` supplies the default `java`, `javac`, and `jar` executables; explicit
`JAVA`, `JAVAC`, and `JAR` commands take precedence.

## Java callbacks

For a component named `analyse`, the generated API is
`org.sdlchain.generated.analyse.API`. Extend `API.Handler` with a public no-argument
constructor. Its fields hold application state.

```java
package app;
import org.sdlchain.generated.analyse.API;
import sdl.globaltypes.measurement.Measurement;

public final class AnalyseHandler extends API.Handler {
    private long received;

    @Override public void on_input(API.Context ctx, Measurement message)
            throws Exception {
        ++received;
        if (message == null) return; // Required input timeout.
        ctx.publish_result(message);
    }
}
```

`onStart` and `onStop` are optional. `onTick(Context, Inputs)` defaults to calling
`on_<port>` in model order. Override it to process the complete snapshot together.
A source overrides `onTick` as well. Optional absent inputs are null in the
snapshot and their individual callbacks are skipped. A required timed input
receives null when its timeout expires. Missing callbacks fail explicitly when
invoked.

`ctx.name` and `ctx.configJson` expose identity and raw INIT configuration.
`Json.parse(ctx.configJson)` provides a dependency-free reader for parameters.
`timeNs()` and `tickIndex()` return `BigInteger`, preserving unsigned 64-bit
values; `now()` returns seconds as a double. Generated SDL `uint64` message
fields keep the SDL codec's `long` bit representation.

Typed `publish_<port>` methods are valid only on the active callback thread.
Use stderr for diagnostics; stdout carries binary IPC. Messages can be retained
as normal Java objects. User-created background threads are the handler's
responsibility; the generated launcher exits when component execution ends.

## Ada callbacks

For a component named `analyse`, the generated package is `Chain_analyse`.
The user unit defines `Object` derived from `Chain_analyse.Handler` and overrides
`On_Start`, `On_Stop`, `On_Tick`, or `On_<port>` as needed. Add fields to `Object`
for application state. The default `On_Tick` dispatches the selected ports.

```ada
with Chain_analyse;
package Analyse_Handler is
   type Object is new Chain_analyse.Handler with null record;
   overriding procedure On_input
     (Self : in out Object; Ctx : in out Chain_analyse.Context;
      Message : Chain_analyse.Input_input);
end Analyse_Handler;
```

A timed required input uses a nullable access-to-constant message. Untimed
individual callbacks use `not null` access. Snapshot fields may be null for
absent optional inputs. Message references are borrowed for the callback;
copy the record to retain it. Generated SDL controlled fields/vectors preserve
ownership during copies. The container frees selected messages on successful
completion, shutdown, and exceptions.

`Name`, `Config_JSON`, `Time_Ns`, `Tick_Index`, and `Now` provide the same context
as other languages. Counters use `Interfaces.Unsigned_64`. Configuration remains
raw UTF-8 JSON, as in the C/Rust component APIs. Publication is restricted to the
active callback task. Use `Ada.Text_IO.Standard_Error` for diagnostics.

## Execution modes

Both languages use the existing process driver and CH/1 protocol. They advertise
timestamped ticks and accept historical empty ticks. Malformed frames, duplicate
inputs, missing required inputs, incompatible catalogues, and incorrect message
roots fail the tick. No completion frame is sent after a failed callback.

In `realtime`, the adapter applies configured subscriptions and timeouts. In
`simulated`, virtual time and the coordinator determine inputs, wave membership,
and output commit order. Component wall-clock delays do not affect the logical
trace. Independent Java/Ada/C/Rust/Python processes can execute on different
cores. User code must avoid external clocks, uncontrolled random state, and
other nondeterministic inputs when reproducibility is required.

The `examples/multilingual` graph contains all five languages and a four-input
join. Build and compare traces with:

```sh
make build MODEL=examples/multilingual/model.json DEPLOYMENT=examples/multilingual/deployment.json
python3 -m ose.cli run --mode simulated --duration .31 --workers 1 --trace build/w1.jsonl
python3 -m ose.cli run --mode simulated --duration .31 --workers 4 --trace build/w4.jsonl
cmp build/w1.jsonl build/w4.jsonl
```

## External Java client

`make client-java` builds `build/client-java/sdl-chain-client.jar` and the JNI
library. Add the JAR to the classpath and its directory to `java.library.path`.
Generated SDL classes must also be on the classpath. The component SDK itself
never loads JNI.

```java
Client client = Client.connect("127.0.0.1", port, "instrument");
Client.Publisher<Measurement> publisher = client.publisher("sensor.measurement", Measurement.class);
Client.Options options = Client.Options.fifo();
options.ackReceived = true;
options.maxRetries = 3;
Client.Subscription sub = client.subscribe("sensor.measurement", Measurement.class,
    options, new Client.Callback<Measurement>() {
        public void onMessage(Measurement value, Client.MessageInfo info) {
            System.err.println(info.sequence + ": " + value.value);
        }
    });
```

`publish` returns the broker receipt as JSON, including admission results;
inspect `delivery` to distinguish accepted and refused subscriptions.
`listTopics` and `stats` return JSON. `Client.run()` waits for closure/failure;
callback failures are attached as the cause of its `IOException`.

`Options` covers FIFO/latest, capacity, overflow, admission wait, ACK, retry,
backoff and TCP/UDP. Use `Options.latest()` for the latest-state defaults.
Messages are decoded and checked before admission/ACK. Callbacks are serial
within a subscription and may run concurrently across subscriptions.

`unsubscribe()` initiates shutdown; `waitClosed(timeoutMs)` joins its workers.
Call `close()` to release the subscription handle. Calling `close` directly is
nonblocking with respect to callbacks. Never wait from your own callback; the
SDK rejects that operation. Client close wakes blocking operations, and leases
protect native handles during concurrent calls. Explicitly close publishers,
subscriptions and clients; garbage collection is not a shutdown mechanism.

## External Ada client

`make client-ada` compiles the SDK and a link probe. Applications use
`Chain_Client` and instantiate `Chain_Client.Typed` with their generated message
package:

```ada
package Messages is new Chain_Client.Typed
  (SDL_measurement.T_Measurement, "Measurement", SDL_measurement.Description,
   SDL_measurement.Encode, SDL_measurement.Decode);
```

`Connect`, `List_Topics`, `Run` and `Close` operate on a controlled client.
The generic package provides `Publisher`, `Open`, `Publish`, `Subscription`,
`Subscribe`, `Unsubscribe`, `Wait_Closed`, and `Stats`. Publications and
administrative operations return JSON like the C API. `Options` expresses the
same policies as Java/C/Rust. Callback parameters include typed message data,
metadata with an unsigned 64-bit sequence, and an optional user address.

Messages are borrowed during callbacks; copy records to retain them. The bridge
registers foreign threads with GNAT and catches exceptions at every ABI callback
boundary. `Run` reports callback exception details. `Close`/`Unsubscribe` initiate
shutdown; subscription finalization joins workers before releasing Ada state.
Callbacks must eventually return. Never finalize shared objects while another
task uses them, and never wait for a subscription from its own callback.

These clients work without a model or Python at runtime. They connect to the
existing real-time broker. Simulated external input continues to use scenarios.

## Validation and installation

`make check` runs the complete five-language suite. `make test-java`,
`make test-ada`, and `make test-mixed` isolate the new checks. Missing required
toolchains fail these targets. `make check TEST_LANGUAGES=python,c,rust` runs an
explicit local subset and reports Java/Ada as not verified.

The SDK sources are installed with the Python package under `share/ose/sdk`.
Both checkout and installed-package builds use that distribution layout. The
root CI validates the SDL monorepo, and the nested workflow supports extraction
of `ose` with its pinned public SDL dependency. A separate CI job executes
Java tests on a real Java 7 JVM.

The external examples in `examples/external/java` and `examples/external/ada`
subscribe to `external.input` and publish doubled measurements on `java.result`
or `ada.result`. Declare the input topic before starting them, as for the C/Rust
examples. Generate `-java` or `-ada` bindings for the acquisition schema first.
Compile Java application sources with `javac --release 7 -cp
build/client-java/sdl-chain-client.jar`, including those generated bindings, then
run `ClientExample <port> tcp` with the JNI library directory configured.

For an Ada application, the build helper accepts the main and generated units:

```python
from ose.modeling.ada_client_build import build_ada
binary = build_ada(SDL_ROOT, "build/ada-example",
                   "examples/external/ada/client_example.adb",
                   ["build/generated/shared/ada"])
```

The helper returns the executable path. Run it with `<port> tcp` or `<port> udp`.
