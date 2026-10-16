"""Socket-free sender retry state machine with injectable time."""
from collections import Counter
import time
from chain_env.delivery.buffers import Buffer


class DeliveryEngine:
    def __init__(self, policy, store, clock=time.monotonic):
        self.policy, self.store, self.clock = policy, store, clock
        self.buffer = Buffer(policy, store, clock)
        self.metrics = Counter()
        self.failures = []

    def enqueue(self, record):
        return self.buffer.admit(dict(record, attempts=0, deadline=self.clock()), deduplicate=False)

    def due(self, limit=None):
        now = self.clock()
        for record in self.store.due(now, limit):
            if record["attempts"] >= 1 + self.policy.max_retries:
                self.fail(record["id"], "retry_exhausted")
            else:
                yield record

    def sent(self, identity):
        record = self.store.get(identity)
        if record is None:
            return
        if record["attempts"]:
            self.metrics["retries"] += 1
        record["attempts"] += 1
        self.metrics["sent"] += 1
        if self.policy.ack == "none":
            self.store.remove(identity)
        else:
            record["deadline"] = self.clock() + self.policy.ack_timeout_ms / 1000 * self.policy.backoff_multiplier ** (record["attempts"] - 1)
            self.store.update(record)

    def acknowledge(self, identity):
        if self.store.has_pending(identity):
            self.store.remove(identity)
            self.metrics["acknowledged"] += 1

    def fail(self, identity, reason):
        self.store.remove(identity)
        self.metrics["failed"] += 1
        self.failures.append({"id": identity, "reason": reason})
        self.failures = self.failures[-64:]

    def snapshot(self):
        return {**self.buffer.snapshot(), **self.metrics, "failures": self.failures}
