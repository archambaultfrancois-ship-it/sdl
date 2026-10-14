"""Select snapshots without accumulating missed periodic deadlines."""
import time


class Scheduler:
    def __init__(self, node, buffers, clock=time.monotonic):
        self.node, self.buffers, self.clock = node, buffers, clock
        self.next_tick = clock()
        self.last = {}

    def select(self):
        selected = {port: buffer.take() for port, buffer in self.buffers.items()}
        if any(desc.get("required", True) and not selected.get(port) for port, desc in self.node["inputs"].items()):
            return None
        trigger = self.node["trigger"]
        if trigger["mode"] == "periodic":
            if self.clock() < self.next_tick:
                return None
        elif not any(record and (self.buffers[port].policy.buffer == "fifo" or self.last.get(port) != record["id"])
                     for port, record in selected.items()):
            return None
        return {port: record for port, record in selected.items() if record is not None}

    def complete(self, selected):
        for port, record in selected.items():
            buffer = self.buffers[port]
            self.last[port] = record["id"]
            if buffer.policy.buffer == "fifo":
                buffer.consumed(record["id"])
        if self.node["trigger"]["mode"] == "periodic":
            self.next_tick = self.clock() + self.node["trigger"]["period_ms"] / 1000

    def wait_timeout(self):
        if self.node["trigger"]["mode"] != "periodic":
            return None
        if any(desc.get("required", True) and not self.buffers[port].take() for port, desc in self.node["inputs"].items()):
            return None
        return max(0, self.next_tick - self.clock())
