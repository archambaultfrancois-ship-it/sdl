# Delivery and quality of service

Each subscription has an independent broker outbox and consumer inbox. Fan-out
copies publications to every matching subscription. Worker groups are absent.
Messages are identified by broker incarnation plus publication number; retries
retain that ID. Consumer FIFO order is admission order, not publication order.

| Setting | Default | Alternatives |
| --- | --- | --- |
| `buffer` | `fifo` | `latest` |
| `capacity` | 1024 | Positive integer; latest requires 1 |
| `overflow` | `reject_new` | `drop_oldest`, `wait` |
| `ack` | `none` | `received` |
| `retry` | `"none"` | Object with retry settings |
| `wait_timeout_ms` | 100 | Positive integer |
| deployment transport | `tcp` | `udp` |

A retry object defaults to `max_retries: 3`, `ack_timeout_ms: 100`, and
`backoff_multiplier: 2`. The initial send is additional to those retries.
Retry requires `ack: received`. ACK is emitted after decoding, compatibility
validation and admission to RAM. This does not confirm successful business
processing or durability across a restart. Lost ACKs cause duplicates, which are
ACKed again. Retry exhaustion drops the pending sender record, increments failure
counters and records the last 64 failures; other messages continue.

Deduplication tracks pending messages and the last 4096 admitted IDs in RAM. Very old repeats
outside that window can be admitted again. There is no exactly-once guarantee.

All queues are memory-only. `latest` has capacity one and uses `drop_oldest`. It retains the
current value in the consumer and the last publication per topic in the broker.
New subscribers receive the broker's current state. Older publication numbers
cannot overwrite newer state, and obsolete broker incarnations are rejected.
Replacing an unsent state cancels its retries. No state TTL is applied.

`reject_new` leaves existing entries intact. `drop_oldest` removes the oldest
waiting entry, preserving a component's in-flight FIFO snapshot; if every entry
is reserved it refuses the new entry. `wait` retries admission for at most the
configured delay and then refuses; it is supported only on TCP. Each subscription
has separate admission and sending tasks so a slow subscriber does not block
delivery to other subscribers. A publisher receives a per-subscription result
and the adapter reports refused deliveries rather than silently succeeding.

Queues use bounded insertion-ordered maps with direct ID lookup. Binary SDL2
envelopes remain bytes rather than Base64/JSON records, and fan-out shares their
immutable payload. Consumer payloads are views into the received envelope.
Retry deadlines use a min-heap; obsolete entries are compacted to bound memory
usage. Sender loops process at most 32 eligible records per pass and wake on
queue changes or the next retry deadline. Admission waits wake when space is
freed rather than polling every 5 ms.

There are no commits, journals or persistent queues. A restart loses pending
messages, latest states and deduplication history. Catalogues are immutable in
RAM for the lifetime of a broker. Legacy `durability: "memory"` is normalized
away; disk declarations are rejected in models and resolved plans.

## Network protocol CENV/1

Each packet is `"CE" | version 1 | u32 metadata length | JSON metadata | body`.
DATA metadata includes subscription, message ID, epoch, sequence and topic. DATA
body is an unchanged S2PS/2 envelope. ACKs use the same transport as their DATA.
Control and publisher ingress always use TCP. Catalogues are requested on cache
miss via TCP and transported as UTF-8 binary bodies. The adapter cache holds at
most 64 catalogue texts. Full catalogue text comparisons reject ID collisions.

Adapters use a separate TCP control connection and one TCP data connection per
TCP subscription. `hello` declares `data_channels: true`; each data connection
then requests `attach` with its node and subscription ID. The broker replies to
the attachment before enabling DATA on that connection. A backpressured data
reader cannot prevent catalogue, status or publication replies on the control
connection. Receipt ACKs use the control TCP connection for TCP subscriptions.
Each subscription has its own broker send loop and adapter admission worker.
Full buffers or delayed writes do not create a shared delivery round barrier.

A TCP frame is written once, then its sender waits for that write's `drain()`.
Backpressure does not cancel and reappend the frame every 100 ms. Broker shutdown
cancels the sender and bounds connection-close flushing to 0.5 seconds before
aborting the transport if necessary.

The broker checks the complete outgoing DATA packet against the TCP or UDP limit
before queue admission, including replaying retained latest state. A PUBLISH that
fits its incoming frame can therefore return `oversize` for a subscription whose
DATA metadata would exceed the configured limit.

Control replies carry JSON results in their body with `content: "json"` in the
small metadata header. `Client.request()` decodes these results into the returned
mapping; catalogue replies retain their raw UTF-8 body. Metrics are also sent as
a JSON body. Startup polls the small `state` result rather than the full status.
Status and metrics remain subject to the complete TCP packet limit.

Publisher control requests use a deadline of 15 seconds plus the largest configured
`wait_timeout_ms` among that component's outgoing wait subscriptions. Publications
waiting for admission run separately from the control reader, so ACKs, catalogue
requests and status polling continue. Each connection permits at most 32 pending
publications and explicitly refuses additional requests.

TCP frames add a big-endian u32 packet length. The packet limit defaults to
1 MiB including metadata; configurable `max_frame` is in deployment. UDP sends
one packet per datagram and defaults to 1200 total bytes, including metadata
and S2PS/2; `max_datagram` can be configured up to 65507. Oversized messages are
refused. Application fragmentation is absent. Metadata is limited to 64 KiB.
UDP dispatch and each subscription's adapter staging queue are bounded to 1024
entries. TCP applies backpressure only to that subscription's data channel.
UDP staging overflows are counted and unacknowledged, permitting retries when
configured. Catalogue text is cached separately from compatibility checks, which
are scoped to each catalogue/input-port pair.

Disabling retry means no application retransmission. TCP's own reliability still
operates. FIFO over UDP can contain gaps and preserve reordered arrivals. UDP
with retry does not promise delivery after exhausted attempts.

The v1 transport listens on loopback and does not implement remote deployment,
authentication or TLS. Retry deadlines, buffer ages and scheduling use monotonic
time. These improvements reduce overhead; the Python runtime does not provide
a hard real-time latency guarantee.
