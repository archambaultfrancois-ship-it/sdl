# Distributed real-time execution

OSE runs one adapter/component pair per worker, a central coordinator and optional
direct TCP subscriptions. Business callbacks, SDL contracts and input timeouts
use the existing local APIs. Simulation remains local.

## Deployment and build

Add `"execution": "distributed"` to a deployment. Subscription bindings accept
`"routing": "broker"` (default) or `"routing": "direct"`, with `"transport": "tcp"`.
UDP and the general external client SDKs remain local-only. Declared external
topics support [live recording replay](replay.md#live-replay-and-speed-control)
through broker routes, with a dedicated `replay` certificate identity.
The read-only [interactive recorder](recording.md) can discover outputs through
the coordinator and capture publications directly from workers, without external
publisher topics or changes to the application model.

Components may declare Kubernetes constraints in `placement`:

```json
{"node_selector": {"kubernetes.io/os": "linux"},
 "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}}}
```

`build --component NAME` compiles only that executable while emitting the complete
plan, supporting separate component images. Registration checks a fingerprint of
contracts, parameters, environment, triggers and routes. Build images with fixed
internal paths such as `/app`, since commands in resolved plans are absolute.

## External recording publishers

The coordinator accepts publisher-only RPCs used by `ose-ctl replay`. They use
the same authenticated CENV/1 control connection as worker RPCs, with a separate
certificate common name `replay`. Loopback development can use explicit
`--insecure`. The application must already be activated.

| Operation | Request | Result |
| --- | --- | --- |
| `external_open` | `topics`: declared external topic names; `plan_id`: application fingerprint | Session `token` and coordinator `epoch` |
| `external_publish` | Session `token`, owned `topic`, S2PS/2 binary body | Per-subscription `delivery` admission results |
| `external_close` | Session `token` | Releases topic ownership |

One connection owns one session and reserves its selected topics exclusively.
The coordinator validates envelopes against the declared contracts and assigns
publication sequence numbers. Broker records identify an external source by its
full topic name; the session token is the source generation. Workers authorize
admissions through the coordinator, including after publisher disconnect while
accepted records drain. Reopening a topic creates a new generation and clears
its remaining broker records. Disconnect or close releases ownership. No external
publisher can impersonate a worker or publish to internal component outputs.

These RPCs do not provide external subscriptions or dynamic topic declarations.
`broker.external.enabled` controls access; `max_clients` bounds simultaneous
publisher sessions. Recordings and live replay do not join the READY barrier.
See [replay](replay.md) for timing, speed and interactive controls.

## Processes and authentication

Build normally, then start a coordinator and one worker per logical component:

```sh
ose-ctl serve --plan /app/build/plan.json --host 0.0.0.0 --port 7000 \
  --tls-ca ca.crt --tls-cert coordinator.crt --tls-key coordinator.key
ose-ctl worker --plan /app/build/plan.json --node source \
  --host coordinator.example --port 7000 --listen-host 0.0.0.0 \
  --listen-port 7001 --advertise-host source.example \
  --tls-ca ca.crt --tls-cert source.crt --tls-key source.key
ose-ctl cluster-status --host coordinator.example --port 7000 \
  --tls-ca ca.crt --tls-cert observer.crt --tls-key observer.key
```

All connections require mutual TLS. Each certificate has one common name:
`coordinator` for the coordinator, the exact model name for a worker. A certificate
from the same CA may read status. OSE checks certificate identities independently
of DNS, allowing pod addresses to change. Issuance, private-key distribution and
rotation are operator tasks. Certificates need normal CA/key-usage and client/server
authentication extensions required by the host's TLS implementation.

`--insecure` is explicitly limited to loopback listen, advertised and coordinator
addresses. It is for local demonstrations and tests, not cluster manifests.
All advertised worker endpoints must be reachable by their upstream workers.

## Lifecycle and replacement

The coordinator waits for every READY before initial activation. After activation,
surviving components continue according to their optional/required inputs and
timeouts. A replacement registers a new generation and starts with empty state.

The default lease is six seconds, renewed every quarter lease. Connection loss or
lease expiry retires that generation and clears affected broker queues. Concurrent
live registrations for one component are refused. Direct senders discard messages
addressed to obsolete target generations. Each recipient checks admission with
the coordinator, including current source and target generations. A disconnected
old sender cannot keep publishing just because its process cannot be terminated.

The worker CLI cold-reconnects after a transport or process failure. Application
effects outside OSE (devices, files or external services) require their own fencing
and idempotency. There is no exactly-once execution, checkpoint, message journal,
historical replay or bounded failover-time guarantee.

The coordinator is a singleton with volatile state. Its replacement starts a new
epoch; workers cold-reconnect and rebuild the initial readiness barrier. Kubernetes
control-plane HA does not replicate OSE queues or remove that interruption.
Kubernetes exports also fence the coordinator with a dedicated Kubernetes Lease.
Only its service account can read/update that lease. Authorization expires before
the full lease duration; a replacement observes an unchanged API resource version
for the complete duration before taking ownership using a compare-and-swap update.
This avoids comparing different hosts' wall clocks. If the old coordinator loses
API access, it stops authorizing admissions even if some workers can still reach it.
The lease is ten seconds, with an eight-second local authorization budget and
renewals every 2.5 seconds. This assumes normally progressing monotonic clocks.
For manual serve/worker deployments, maintain a single coordinator; enable
`serve --kubernetes-lease NAME --namespace NS` when using Kubernetes failover.

## Delivery and bounds

Broker routes enqueue at the coordinator and are polled on the persistent worker
control connection. Direct routes use bounded sender engines at the source and
send payloads to the destination adapter. Only authorization RPCs, not those payload
bytes, pass through the coordinator. Both paths retain receiver FIFO/latest buffers,
overflow rules, admission ACKs and retry budgets. ACK means admission in RAM, not
business callback completion. A cold replacement does not replay history.

Direct admission requires coordinator connectivity. This favors fencing over
continued admission during partitions. Control load still grows with admission
rate; benchmark before targeting very high message rates. Each subscription has
its own network loop. Application publication admission obeys its bounded wait
policy. TCP frames obey the plan limit and CH/1 IPC remains limited to 1 MiB;
chunk larger application objects into suitably sized SDL messages.

## Kubernetes export

Supply a JSON image map covering `coordinator` and every component. Values must
be pinned registry references: `registry/image@sha256:<64 hex digits>`.

```sh
ose-ctl export-kubernetes --plan build/plan.json --images build/images.json \
  --namespace my-app --output build/kubernetes.json
kubectl apply -f build/kubernetes.json
```

The export creates a namespace and one singleton Deployment/Service per component,
plus the coordinator, its Lease and narrowly scoped RBAC. It uses Recreate updates, Linux placement, declared resources
and preferred host spreading. Images must contain `/app/build/plan.json` at runtime.
The coordinator needs OSE and the SDL Python runtime; workers also need their binary.

Create Secrets `ose-coordinator-tls` and `ose-<component DNS name>-tls`, each with
`ca.crt`, `tls.crt`, `tls.key`. Underscores become hyphens in Kubernetes names;
certificate identities retain model names. Collisions and the reserved
`coordinator` and `replay` names are refused. Pass `--image-pull-secret NAME` to the export when using a private
registry; the Secret must exist in the namespace. TCP readiness
probes indicate a listening process; OSE READY/status separately indicate application
readiness. The export neither installs K3s nor publishes images.

Kubernetes controls node failure detection and replacement timing. Provision
compatible spare capacity and registry access or preloaded images. Use simulation
for deterministic business behavior, network tests for transport/fencing, and
cluster tests for image deployment and placement.
The export sets ten-second tolerations after a node becomes NotReady or unreachable;
this is additional to Kubernetes failure-detection and image/startup delays.
