# SDL2 PoC: topic-centric pub/sub

An application with publishers and subscribers connected through a small
**TCP/IP** broker. The application is isolated in `examples/PoC_PubSub` and uses the public
SDL2 Python catalogue identity and message inspection APIs. Only Python 3 and
its standard library are required.

## Automated demonstration

From the repository root:

```sh
make -C examples/PoC_PubSub demo
make -C examples/PoC_PubSub test
```

The scenario starts a broker on an available local port and uses real TCP
connections for every operation:

1. Two publishers register immutable catalogues and publish on
   `usine/temperature/v1`, using two revisions of `Temperature`.
2. A typed v1 subscriber joins after publication and replays the history.
3. It retrieves the catalogues and caches their contexts. The next message
   reuses the cache without fetching the catalogue again.
4. A dynamic subscriber decodes messages without local message classes.
5. An incompatible schema is rejected during registration.

Revision v2 renames `celsius` to `value` while preserving field ID 2, then adds
an optional `unit` field. The v1 consumer reads `celsius` and ignores `unit`.
The incompatible revision replaces the number with a string.

The topic's `/v1` suffix identifies the **baseline consumer contract**, rather
than an exact catalogue fingerprint. Multiple compatible catalogues coexist.

## Running components in separate processes

Generate the classes and configure each terminal as follows:

```sh
make -C examples/PoC_PubSub generate
cd examples/PoC_PubSub
export PYTHONPATH="../../runtime/python:build/v1/python:build/v2/python:build/bad/python"
```

In the first terminal:

```sh
python3 app.py broker --port 8765
```

In other terminals, using the same directory and `PYTHONPATH`:

```sh
# Two publishers
python3 app.py publish --revision v1 --producer sensor-A --sensor oven-A --value 21.5
python3 app.py publish --revision v2 --producer sensor-B --sensor oven-B --value 23

# Late subscriber: replay history, then follow new publications
python3 app.py subscribe

# Read once without local classes for decoding
python3 app.py subscribe --dynamic --once

# Expected rejection: INCOMPATIBLE_SCHEMA and a nonzero exit status
python3 app.py publish --revision bad
```

`--host` and `--port` select the broker. By default, it listens on
`127.0.0.1:8765`. Stop components with Ctrl+C.

## Topic contract manifest and offline validation

The CLI loads `contracts/temperature.json` by default. JSON keeps this PoC
standard-library-only. `app.py --contract PATH` selects another manifest for
the broker, publisher, subscriber, or demonstration. The application still
handles one topic per broker; the generated message examples remain Temperature.

```json
{
  "version": 1,
  "topic": "usine/temperature/v1",
  "root_type": "Temperature",
  "baseline_catalogue": "temperature_v1.sdl2",
  "compatibility": "readable-by-baseline-consumer",
  "require_required": false
}
```

`version` selects the manifest format, independently of the S2PS envelope
version. The baseline path is resolved relative to the manifest. It points to
an emitted SDL2 catalogue, including its `SDL2` header, not a `.sdl` source
schema. The example catalogue matches canonical emission of the v1 schema;
a test detects divergence when the schema changes.

The loader rejects unsupported versions or policies, unknown or missing keys,
duplicate JSON keys, invalid field types, missing files, and invalid baselines.
The baseline must declare only the configured root message. All six manifest
fields are mandatory to keep deployment policy explicit. Manifest files are
limited to 64 KiB and catalogue files to 1 MiB.

`TopicContract.prepare_candidate(text)` performs the shared offline and broker
registration checks: the reference consumer must be able to read the candidate
publisher catalogue, the optional required-field policy must pass, and the
catalogue must contain only the configured root message. The broker retains
separate registry checks for ID verification, collisions, and capacity.

Validate a catalogue without starting a broker or generating Python bindings:

```sh
# From the repository root: validate the supplied baseline example
make -C examples/PoC_PubSub validate

# Validate a candidate catalogue against a chosen manifest
make -C examples/PoC_PubSub validate CONTRACT=contracts/temperature.json CATALOGUE=/tmp/candidate.sdl2

# Equivalent command from examples/PoC_PubSub, with runtime/python on PYTHONPATH
python3 contracts.py validate --contract contracts/temperature.json --catalogue /tmp/candidate.sdl2
```

The command writes a single JSON result suitable for CI. Exit status is `0`
for acceptance, `1` for a rejected candidate, and `2` for invalid configuration,
unreadable files, size limits, or invalid CLI arguments. Schema rejection results
include the same structured compatibility diagnostic as the broker. A different
or additional root returns `TOPIC_CONTRACT`; an additional root may have no
field-level diagnostic because it violates the PoC topic rule rather than
SDL2 directional compatibility.

```sh
python3 app.py broker --contract contracts/temperature.json --port 8765
```

The manifest is loaded once at startup, not hot-reloaded. Changes to the
reference catalogue or policy should be reviewed together with validation of
existing publisher catalogues. The manifest can be versioned in the application
repository independently of schema source and broker code. It does not provide
ACLs, signatures, authorization, or automatic approval of schema changes.

Programmatic `BrokerState(TopicContract(...))` uses the same mechanism. Existing
`BrokerState(catalogue_text_or_class, ...)` calls remain available. Metadata
advertised to clients contains the topic, root, reference catalogue ID, and
policy; it does not expose the manifest's local file path. The broker enforces
the contract, while the subscriber stores the advertised metadata and performs
its existing typed or dynamic decoding.

## Architecture and experimental protocol

```text
Publishers v1/v2 ──TCP──► Broker ──TCP (poll/replay)──► Subscribers
                           │                           │
                           ├─ immutable catalogues     ├─ LRU context cache
                           ├─ topic contract           └─ SDL2 decoder
                           └─ bounded history
```

Operations are encoded as JSON with a big-endian uint32 length prefix.
Each connection carries one request and one response:

| Operation | Purpose |
| --- | --- |
| `contract` | Read the topic contract |
| `put_catalogue` | Register a catalogue after fingerprint and contract validation |
| `get_catalogue` | Resolve a catalogue by its identity |
| `publish` | Validate and append a message to the history |
| `poll` | Read messages after a sequence number |

Logical metadata namespaces:

```text
_sdl2/catalogues/<id32>                  → immutable catalogue
_sdl2/topics/usine/temperature/v1/contract → contract
usine/temperature/v1                     → messages
```

Metadata is accessed through dedicated operations, with its topic name in the
response; it is not delivered through generic subscriptions. An MQTT adapter
could map it to retained topics. Data subscriptions poll every 250 ms.

Binary publication envelope:

```text
"S2PS" (4 bytes) | version 2 (1 byte) | catalogue ID (4 bytes) | SDL2 message
```

It is transported as Base64 in the JSON request. **S2PS/2 is a PoC convention,
not a standard SDL2 format.** The SDL2 payload is unchanged, including its
message ID relative to the specified catalogue.

The catalogue ID is the first 32 bits of SHA-256, carried as four bytes in
digest order (big endian) and eight lowercase hexadecimal digits in JSON and
metadata topic names. The envelope header is now 9 bytes instead of 37.
Version 2 distinguishes this layout from the former 32-byte fingerprint layout;
version 1 envelopes are rejected.

The public SDL2 `catalogue_id()` API computes the fingerprint from the exact
UTF-8 bytes returned by `description(...)`,
which produces the canonical catalogue. The server verifies the fingerprint
but does not canonicalize text supplied by another tool: different whitespace
accepted by SDL2 would produce a different identity in this PoC.

Because 32-bit fingerprints can collide, registration compares the full
catalogue text whenever an ID is already present. Identical catalogues can be
registered again; different catalogues with the same ID are rejected with
`CATALOGUE_COLLISION`. The check and registration are atomic under the broker
lock. A rejected publisher must not send data using that ID. Collision handling
is scoped to this broker; independent registries can assign the same ID to
different catalogues. The short fingerprint is not a security integrity check.

The contract requires `Temperature` to be the only message declaration. The
broker compares the publisher catalogue against the baseline catalogue using
public `compare_catalogues()` with this root, without any generated bindings.
It then prepares a dynamic context and validates every payload before
publication. `BrokerState` accepts baseline catalogue text directly; passing a
generated class is just a convenience for obtaining that text.

Default compatibility permits missing consumer fields and uses SDL2 defaults.
An optional `BrokerState(..., require_required=True)` policy requires consumer
`required` fields in publisher metadata. This policy is advertised in the topic
contract and is separate from wire compatibility.

Schema rejections carry a `diagnostic` object in the TCP error response, and
`PocError.diagnostic` exposes it to the caller. It includes a stable code, root
message, nested field path, field ID, logical types, and offending enum value.
For example, the incompatible revision reports `FIELD_TYPE_MISMATCH` at
`Temperature.celsius`, field 2, publisher `string`, consumer `fl64`.

The subscriber retrieves each message's catalogue on demand, verifies its
fingerprint, and prepares its context. It does not need to have received a
prior announcement. The broker uses public `message_names()` to inspect the
topic root. Each subscriber owns its local types and cache. Older
catalogues remain available after a new revision is registered.

## Optional resolution helper

`resolution.py` provides `CatalogueDecoder`, a synchronous helper independent
of TCP. It imports only the standard library and the existing SDL2 runtime.
No code or dependencies were added to the runtime for this integration step.

A resolver is any callable `resolver(unsigned_catalogue_id)` returning exact
catalogue text (`str` or UTF-8 `bytes`), or `None` when unavailable. For example,
an application can use a dictionary populated by its metadata subscriber:

```python
from resolution import CatalogueDecoder, IntegrationError
from sdl_runtime import catalogue_id, description
from temperature_v1 import Temperature

text = description([Temperature])
store = {catalogue_id(text): text}
reader = CatalogueDecoder(store.get, [Temperature], cache_limit=16)

try:
    value = reader.decode(received_envelope)
except IntegrationError as error:
    if error.code == 'CATALOGUE_UNKNOWN':
        reject_message(error)
    else:
        raise
```

`received_envelope` and `reject_message` are supplied by the application. Use an
empty `local_types` collection for dynamic decoding. Each decoder fixes its
local bindings at construction and owns its own cache; do not share one decoder
between consumers using different bindings or independent catalogue registries.

On a cache miss, the helper calls the resolver once, verifies the catalogue ID,
prepares a context, and inserts it into a bounded LRU cache. Default limits are
16 contexts, 1 MiB per resolved catalogue, and 4096 bytes per envelope. Override
these with `cache_limit`, `max_catalogue_size`, and `max_envelope_size`.
The application must also bound its catalogue store and incoming transport
allocations. Registry collision checks remain the registry's responsibility.

An absent catalogue immediately raises `IntegrationError` with code
`CATALOGUE_UNKNOWN`. There is no queue, waiting, polling, automatic retry, or
background thread. Resolver infrastructure exceptions propagate unchanged;
absence and an unavailable backend are different conditions. A later explicit
call can succeed if the catalogue has since become available. Invalid catalogues
never enter the cache. `cache` exposes a read-only view; `catalogue_fetches`
counts successfully resolved and prepared contexts.

`TcpCatalogueResolver` in `pubsub.py` adapts the existing RPC client to this
callback contract. `Subscriber` uses it by default; an application can inject
another resolver with `Subscriber(client, [Temperature], resolver=store.get)`.
The subscriber still uses TCP to read the topic contract and events.

A rejected event raises to the caller and does not advance the subscriber's
cursor past that event. It is not silently dropped or acknowledged; a subsequent
explicit `poll()` attempts it again. Earlier successfully decoded events in the
same poll have already advanced the cursor. Delivery and skip/acknowledgement
policies belong to the transport adapter or application.

The helper is a Python PoC, not a new cross-language runtime API. Kafka and MQTT
adapters remain future integration work; their catalogue subscriptions can feed
a store compatible with the same callback.

## Limits and observable errors

- A single topic per broker, configured by the contract manifest.
- Catalogues must be registered before publication; a missing catalogue returns
  `CATALOGUE_UNKNOWN`, without queueing the message.
- An in-memory history of 256 messages. A cursor older than the available
  history returns `HISTORY_GAP`. Subscribers start at cursor 0.
- At most 64 catalogues of 1 MiB each, 4 KiB envelopes, and 128-byte publisher
  names. Each subscriber caches up to 16 contexts by default. These limits
  keep a replay response below the 2 MiB TCP frame limit.
- No persistence across broker restarts, delivery guarantees, deduplication,
  or application acknowledgements. Subscriber cursors are held in memory.
- The `producer` field is self-reported; this local PoC does not implement TLS,
  authentication, or access control.

TCP tests cover late subscribers, compatibility, catalogue corruption,
truncated data, incorrect topics, missing catalogues, cache eviction, size
limits, collision rejection, envelope version checks, explicit history gaps,
structured compatibility diagnostics, and the optional required-field policy.

See [SDL2_EVOLUTION.md](SDL2_EVOLUTION.md) for the identified SDL2 enhancements.

The fixed envelope now uses the public SDL2 runtime API. See the
[S2PS/2 profile](../../docs/pubsub_envelope.md) for its specification and optional
use alongside direct SDL2 connections.
