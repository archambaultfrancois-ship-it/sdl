# External clients: CENV/1 control API

TCP uses a u32 big-endian packet length, then `CE 01`, u32 metadata length,
UTF-8 JSON metadata (at most 64 KiB), and a binary body. `max_frame` bounds the
whole packet. UDP omits the outer length and uses `max_datagram`.
Every RPC carries `op` and an integer `request`. Replies have `op=result` and
the same request number. Successful structured replies put JSON in the **body**
and set `content=json`; catalogues use a raw UTF-8 body. Errors use `error` and
`error_code` in metadata. Publications and control use one TCP connection.
The broker accepts at most 32 concurrent publications per connection.

| Request | Metadata in addition to op/request | Body | Result |
| --- | --- | --- | --- |
| `client_hello` | `client_id`, `udp_port` | empty | `session`, `udp_port`, `max_frame`, `max_datagram`, `max_wait_timeout_ms` |
| `list_topics` | none | empty | `topics`: topic/root/catalogue_id/publisher_connected |
| `catalogue` | `catalogue_id` | empty | raw SDL2 catalogue |
| `declare_publisher` | `topic`, `root` | SDL2 catalogue | topic/catalogue_id |
| `release_publisher` | `topic` | empty | empty object |
| `subscribe` | `topic`, `root`, `policy` | consumer SDL2 catalogue | subscription/policy |
| `attach_client` | `session`, `subscription` | empty | empty object |
| `activate_subscription` | `subscription` | empty | empty object |
| `unsubscribe` | `subscription` | empty | empty object |
| `subscription_stats` | `subscription` | empty | delivery counters/depth/age |
| `publish` | `topic` | S2PS/2 envelope | publication_id/epoch/sequence/delivery |

`client_hello` identifies the control connection. Its `max_wait_timeout_ms` includes
existing internal admission waits and the limit for future external subscriptions;
publication RPC deadlines must exceed that bound. A TCP subscription attaches a
**separate** data connection with `attach_client`. Its reply precedes any DATA.
UDP uses the control connection's observed IP and declared UDP port. Register
local queues/workers before `activate_subscription`; the broker may already
queue a retained state or new publications while the subscription is inactive.
FIFO starts at subscription creation. Latest enqueues the retained state.

Policy is a resolved object with buffer (`fifo`/`latest`), capacity, overflow
(`reject_new`/`drop_oldest`/`wait`), ack (`none`/`received`), max_retries,
ack_timeout_ms, backoff_multiplier, wait_timeout_ms, transport (`tcp`/`udp`).
Latest requires capacity=1/drop_oldest. Retry requires ack=received. UDP cannot
wait. The server validates policies against its external session limits.

DATA metadata is `op=data`, subscription, id, epoch, sequence, topic; its body
is the S2PS/2 envelope. After SDL compatibility validation, typed decode, and
RAM admission, send `op=ack`, subscription, id with an empty body on control TCP
or the same UDP socket. ACK has no request/reply. Duplicates/stale latest states
are ACKed without invoking the callback again within the deduplication window.

Subscriptions belong to sessions, and their policies are independent. A client
session is never an internal component and cannot send READY on its behalf.
Disconnect deletes session subscriptions/queues and releases publisher leases;
topic contracts and retained states remain in bounded RAM until broker exit.

Known error codes include unknown_topic, schema, qos, ownership, capacity,
disabled, duplicate_client, invalid_client, invalid_endpoint, session, not_ready,
oversize, invalid_request. Publication `delivery` maps subscription IDs to
accepted/full/oversize/duplicate/stale/closed. This is broker RAM admission,
not a callback completion receipt. See [client semantics](../../docs/clients.md).
