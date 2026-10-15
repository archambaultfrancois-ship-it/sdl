"""Deterministic direct admission, with independent bounded virtual waits."""
from dataclasses import dataclass
import hashlib
from chain_env.delivery.buffers import Buffer
from chain_env.delivery.policies import SubscriptionPolicy
from chain_env.delivery.stores import MemoryStore
from chain_env.execution.ipc import MAX_SIZE


@dataclass
class Publication:
    record: dict
    pending: dict


class Router:
    def __init__(self, plan, codec, clock, trace):
        self.codec, self.clock, self.trace = codec, clock, trace
        self.subscriptions = {s["id"]: s for s in sorted(plan["subscriptions"], key=lambda s: s["id"])}
        self.buffers = {name: {} for name in plan["components"]}
        self.by_topic, self.by_id = {}, {}
        self.topics = dict(plan.get("external_topics", {}))
        self.sequence = 0
        for name, node in plan["components"].items():
            self.topics.update({f"{name}.{port}": desc for port, desc in node["outputs"].items()})
        for identity, sub in self.subscriptions.items():
            name, port = sub["to"].split(".")
            policy = SubscriptionPolicy.from_dict(sub["policy"])
            buffer = Buffer(policy, MemoryStore(policy.capacity), clock.seconds)
            self.buffers[name][port] = self.by_id[identity] = buffer
            self.by_topic.setdefault(sub["from"], []).append(identity)
            publisher, consumer = self.topics[sub["from"]], plan["components"][name]["inputs"][port]
            codec.compare(publisher["catalogue"], consumer["catalogue"], consumer["root"])

    def publish(self, topic, body, identity):
        desc = self.topics[topic]
        self.codec.validate(desc["catalogue"], desc["root"], body)
        self.sequence += 1
        record = dict(id=identity, epoch="simulated", sequence=self.sequence, topic=topic,
                      body=body, payload=memoryview(body)[self.codec.api.ENVELOPE_HEADER_SIZE:], catalogue=desc["catalogue"])
        self.trace.emit("publication", id=identity, topic=topic, sha256=hashlib.sha256(body).hexdigest())
        pending = {sub: self.clock.time_ns + self.by_id[sub].policy.wait_timeout_ms * 1_000_000
                   for sub in self.by_topic.get(topic, ())}
        return Publication(record, pending)

    def advance(self, publication):
        changed = False
        for identity, deadline in list(publication.pending.items()):
            buffer, record = self.by_id[identity], publication.record
            port = self.subscriptions[identity]["to"].split(".")[1]
            size = 7 + len(port.encode()) + len(record["catalogue"].encode()) + len(record["payload"])
            if size > MAX_SIZE:
                result = "oversize"
            elif (buffer.policy.overflow == "wait" and buffer.store.size() >= buffer.policy.capacity
                  and self.clock.time_ns < deadline):
                continue
            else:
                result = buffer.admit(record)
            self.trace.emit("admission", id=record["id"], subscription=identity, result=result)
            del publication.pending[identity]
            changed = True
        return changed

    def close(self):
        for buffer in self.by_id.values():
            buffer.store.close()
