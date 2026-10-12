# SDL2 enhancements identified by the PoC

The PoC uses the public SDL2 catalogue identity and message inspection APIs.
The remaining proposals below concern reusable pub/sub integration.

## Priority 1: public catalogue identity and inspection APIs — implemented

All six runtimes expose `sha256-prefix32-v1`: the first 32 bits of SHA-256 over
exact UTF-8 text, represented as an unsigned integer. Emission already produces
the canonical catalogue. As agreed, there is no new receive-side normalization
API and no catalogue compaction.

Public APIs enumerate logical message names in wire ID order. The PoC now uses
Python `catalogue_id()` and `message_names()` rather than
private generated or context attributes.

Shared fixtures cover UTF-8 and SHA-256 padding boundaries across all runtimes.
See [runtime APIs](../../docs/runtime.md#catalogue-identity-and-message-inspection)
for signatures, input limits, representation, and collision policy.

The broker compares full catalogue texts under its lock and rejects a different
catalogue with the same short ID. Registry scope and collision resolution remain
application responsibilities. A short ID is not a security integrity check.

## Priority 1: directional compatibility and diagnostics — implemented

All six runtimes provide catalogue-only preflight comparison in the explicit
direction "consumer reads publisher", without generated bindings. Callers can
select a message root or check every publisher message declaration.

The result carries the first incompatibility with a stable code, root message,
nested path, field ID, logical types, and offending numeric enum value. Shared
field IDs preserve logical type, dimensions, and cardinality. Matched nested
messages are checked recursively and publisher enum values must be a subset of
consumer values. Unknown publisher fields are skipped.

Missing consumer fields retain SDL2 defaults. The separate, disabled-by-default
`require_required` policy requires consumer required fields in publisher metadata.
The comparison is opt-in and does not alter existing `prepare` exceptions.

The PoC broker accepts baseline catalogue text directly, enforces its contract
with this API, validates data with dynamic decoding, and transmits diagnostics
in TCP error responses. The broker does not need generated consumer classes.

Shared fixtures verify the same codes and details in all six runtimes. See
[runtime APIs](../../docs/runtime.md#directional-compatibility-and-diagnostics)
for signatures, ordering, ownership, policy, and code definitions.

## Priority 1: pub/sub envelope profile — implemented

All six runtimes provide optional packing and unpacking of the fixed S2PS/2
profile: `S2PS`, version 2, unsigned 32-bit catalogue ID in big endian, then
nonempty unchanged SDL2 bytes. The header is always 9 bytes, without extensions.
The PoC uses the public Python API and preserves its existing wire bytes.

Raw SDL2 connections and existing codecs remain available. Catalogue resolution,
transport framing, and application size limits are separate responsibilities.
See [the envelope profile](../../docs/pubsub_envelope.md) for layout, ownership,
error behavior, and Kafka mapping. No Kafka adapter is implemented here.

## Priority 2: resolution and adapters — PoC helper implemented

`resolution.py` now exposes a transport-independent Python `CatalogueDecoder`
with an injectable synchronous resolver and a bounded LRU cache. It verifies
catalogue identities and prepares typed or dynamic contexts. Each instance fixes
its local bindings and registry scope, so contexts are not shared across
consumers with different bindings. The existing TCP subscriber delegates to this
helper through `TcpCatalogueResolver`.

An absent catalogue is rejected immediately with `CATALOGUE_UNKNOWN`. There is
no queue, automatic retry, background thread, or new runtime dependency.
Invalid catalogues are not cached; backend failures propagate to the application.
The helper and adapter remain isolated in `samples/PoC_PubSub`; no runtime changes are
needed for this point. See [usage and policies](README.md#optional-resolution-helper).

Kafka and MQTT adapters remain future optional integration modules. Metadata
subscriptions could populate an application-owned store used by the resolver.
Durable catalogue availability, collision rejection, replay retention, and
transport acknowledgement policies belong to the application and infrastructure.

## Priority 2: topic contracts and tooling — PoC implemented

A separate versioned JSON manifest declares the topic, root message, baseline
catalogue path, directional compatibility policy, and required-field policy.
The CLI loads the manifest at startup; existing programmatic BrokerState calls
remain available. The loader validates the manifest and baseline before use.

`contracts.py validate` compares a candidate catalogue against the manifest,
without a broker or generated bindings, and returns machine-readable JSON with
exit statuses for acceptance, rejection, and configuration errors. Broker
registration and offline validation share `TopicContract.prepare_candidate`,
including the single-root rule. See [usage](README.md#topic-contract-manifest-and-offline-validation).

All additions remain in `samples/PoC_PubSub`; no runtime changes are required. The
manifest is deployment configuration, not an SDL2 wire extension. It is loaded
once and supports one topic per broker in this PoC. General code generation,
multi-topic routing, and registry administration are outside this first step.

Routing, ACLs, persistence, approval authority, and delivery guarantees remain
responsibilities of the application and pub/sub system.
