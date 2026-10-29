# External clients

A Python, C, Rust, Java or Ada program can publish and subscribe directly to an application's
CENV/1 broker without a client-side model or a second broker. Generated message
types and the SDL runtime remain application dependencies. The existing component
SDKs that use stdin/stdout remain independent of these network SDKs.

For recording without application bindings or a client-side model, use the
[interactive recorder](recording.md). It discovers live points, accepts SDL filters
and writes local files through SDL's recording service; it also supports distributed
workers through a separate read-only protocol.

## Python

Install `ose` and make `sdl_runtime` and the generated SDL modules importable.
To try the SDK from source, generate the types with `make external-types`, then
add `src`, `$SDL_ROOT/runtime/python` and `build/generated/shared/python` to
`PYTHONPATH`.

```python
from ose.client import Client, Fifo, Latest, Retry
from measurement import Measurement

def receive(message, info):
    print(info.topic, info.publication_id, message.value)

with Client.connect(host="127.0.0.1", port=7000, client_id="tool") as client:
    print(client.list_topics())
    # Internal output topics exist as soon as the broker starts.
    subscription = client.subscribe(
        "analysis.result", Measurement, receive,
        qos=Fifo(capacity=256, overflow="drop_oldest", ack="received",
                 retry=Retry(max_retries=3, ack_timeout_ms=100)), transport="tcp")
    publisher = client.publisher("sensor.measurement", Measurement)
    result = publisher.publish(Measurement(sequence=1, value=3.5))
    print(result.publication_id, result.delivery)
    client.run()
```

`Latest()` immediately delivers the retained state, followed by updates. A FIFO
subscription starts with new publications. Callbacks start when the subscription
is activated; `run()` waits for closure or an error. Each callback receives
`(message, MessageInfo)` and runs sequentially in its subscription's thread.
Callbacks for different subscriptions can run concurrently. A callback can
publish and query the broker. It must return before its own subscription closes.

`subscription.stats()` returns broker/client counters, `subscription.close()`
stops that subscription, `publisher.close()` releases its topic and
`client.close()` closes the session. Errors have distinct types: `UnknownTopic`,
`SchemaError`, `QoSError`, `PublicationRefused`, `ConnectionLost`, `CallbackFailed`.
Callback and connection failures are reported by `run()`.

## C and Rust

The network SDK sources are in `sdk/client/`. The Rust engine exposes a C ABI;
the C wrapper adds typed encoding and decoding through the SDL C runtime. These
clients do not embed a Python process. The Rust network engine has no external
Cargo dependencies and uses IPv4.

```sh
make client-native      # build/client-rust/debug/libsdl_ose_client.a
make external-native    # generated types and two example executables
build/external-c 7000 tcp
build/client-example/debug/ose-external-example 7000 udp
```

Both examples wait for `external.input`, then publish twice the received value
on `c.result` or `rust.result`. Declare `external.input` with a Python publisher
before starting them. The Python example in `examples/external/client.py` can
drive either executable.

In C, include `ose_client.h`, call `register_all_types()` from the generated
SDL code, then use `cenv_connect`, `cenv_publisher`, `cenv_publish`,
`cenv_subscribe` and `cenv_run`. `cenv_fifo()` and `cenv_latest()` initialize
the options. The callback receives a borrowed typed pointer and returns 1 on
success. The message and the strings in `CenvMessageInfo` are valid for that call.
The caller owns returned JSON strings and frees them with `cenv_string_free`.
`cenv_last_error()` returns the network error on the calling thread.

Link `sdk/client/c/ose_client.c`, the Rust static library, the SDL C runtime and
the generated SDL sources. The `external-native` target shows the full command.
The client must remain alive until its publishers have been freed. After
`cenv_unsubscribe`, use `cenv_wait_closed` to wait for callbacks to finish before
freeing their user data. The SDK cannot interrupt a blocked callback.
`cenv_subscription_free` and `cenv_client_free` release the handles; workers
finish releasing their shared state when they exit.

In Rust, add `sdl-ose-client` (`sdk/client/rust`) and the SDL runtime to the
Cargo dependencies. After importing the generated local type:

```rust,ignore
sdl_ose_client::sdl_message!(measurement::Measurement);
let client = Client::connect("127.0.0.1", 7000, "rust-tool")?;
let publisher = client.publisher::<measurement::Measurement>("sensor.measurement")?;
let subscription = client.subscribe::<measurement::Measurement>(
    "analysis.result", Options::default(), |message, info| {
        println!("{} {}", info.topic, message.value);
        Ok(())
    })?;
client.run()?;
```

`Options::latest()` selects the latest state; `Options` and `Retry` expose the
same settings as Python. Errors carry a `code` and a `message`. The macro prepares
the SDL codecs and checks each consumer contract separately. It expands in the
application, so the SDK does not depend on an SDL checkout. The `Message` trait
also supports an explicit application codec.

## Feeding a model input

The SDKs below use the local broker. Distributed models can also receive
[recording replay](replay.md#live-replay-and-speed-control) on declared external
topics through their coordinator; this publisher-only interface does not add
distributed subscription support to the SDKs.

The application model declares the external topic's input contract:

```json
{
  "external_topics": {
    "sensor.measurement": {"root": "Measurement"}
  },
  "subscriptions": [
    {"id": "acquisition", "from_topic": "sensor.measurement", "to": "analysis.input",
     "qos": {"buffer": "fifo", "capacity": 256, "ack": "received"}}
  ]
}
```

These fields complement the usual `version`, `schemas` and `components` fields.
A specific `schema` can be added to the topic contract. The compiler checks SDL
compatibility with the input. A required input waits for data before processing,
but does not delay READY. An external session never participates in the component
startup barrier.

An external publisher can also declare a new topic without a model. It is the
only active publisher for that topic. An internal output belongs exclusively to
its component. A replacement external publisher must satisfy the initial
contract and existing subscriptions. Releasing the publisher retains the
contract and the latest state in RAM until the broker stops.

## Delivery and limits

Broker and client queues, deduplication history and caches are bounded. All state
stays in RAM. A `received` ACK follows catalogue validation, SDL decoding and
admission to the client queue; it precedes callback processing. A publication
result confirms only admission to broker RAM, with a publication ID and an
outcome per subscription. A result with no recipients contains an empty delivery
map. An admitted message can later be replaced by `drop_oldest` or `latest`.
Retries are bounded network retransmissions; they do not make messages persistent.
Deduplication uses a window of 4096 IDs.

Control requests and publications use TCP. Each TCP subscription has a separate
data connection. With UDP, saturation can drop packets; `overflow=wait` is
rejected. The client does not automatically republish after a timeout: the
outcome of an interrupted request may be unknown. Publication deadlines account
for the configured admission wait bounds.

By default, the broker listens on `127.0.0.1`. A deployment can choose
`"broker": {"host": "0.0.0.0"}` to allow connections from another machine.
Deployment components remain local. The current protocol provides neither
authentication nor TLS; the listening address determines network exposure.
Configure dynamic limits in `broker.external`:

| Setting | Default |
| --- | ---: |
| `enabled` | `true` |
| `max_clients` | 32 |
| `max_subscriptions` | 256 |
| `max_capacity` per queue | 4096 |
| `max_topics` total when declaring a new topic | 1024 |
| `max_catalogues` additional catalogues | 4096 |
| `max_wait_timeout_ms` | 1000 |

Transport limits remain `max_frame` and `max_datagram`. Exceeding a limit or
requesting an unsupported quality of service produces an explicit refusal.
Sessions close on disconnect, and their subscriptions and queues are deleted.

## Java and Ada

Java uses the shared Rust engine through JNI; Ada uses its C ABI with typed SDL
codecs. See [build commands, APIs and lifetime rules](java-ada.md#external-java-client).
Neither client requires a model or Python at runtime.
