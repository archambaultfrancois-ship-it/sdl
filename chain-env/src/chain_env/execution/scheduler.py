"""Select input snapshots, including null values after required-input deadlines."""
import time


class Scheduler:
    def __init__(self, node, buffers, clock=time.monotonic):
        self.node, self.buffers, self.clock = node, buffers, clock
        self.last = {}
        self.start()

    def start(self):
        """Arm input waits when execution is enabled, after the READY barrier."""
        now = self.clock()
        self.next_tick = now
        self._arm_inputs(now)

    def _arm_inputs(self, now):
        self.deadlines = {port: now + desc["timeout"] for port, desc in self.node["inputs"].items()
                          if desc.get("required", True) and "timeout" in desc}

    def select(self):
        now = self.clock()
        selected = {port: record for port, buffer in self.buffers.items()
                    if (record := buffer.take()) is not None}
        for port, desc in self.node["inputs"].items():
            if desc.get("required", True) and port not in selected:
                if port not in self.deadlines or now < self.deadlines[port]:
                    return None
                selected[port] = None
        trigger = self.node["trigger"]
        if trigger["mode"] == "periodic":
            if now < self.next_tick:
                return None
        elif not any(record is None or self.buffers[port].policy.buffer == "fifo" or
                     self.last.get(port) != record["id"] for port, record in selected.items()):
            return None
        return selected

    def complete(self, selected):
        for port, record in selected.items():
            if record is None:
                continue
            buffer = self.buffers[port]
            self.last[port] = record["id"]
            if buffer.policy.buffer == "fifo":
                buffer.consumed(record["id"])
        now = self.clock()
        self._arm_inputs(now)
        if self.node["trigger"]["mode"] == "periodic":
            self.next_tick = now + self.node["trigger"]["period_ms"] / 1000

    def wait_timeout(self):
        now, ready_at, waiting = self.clock(), None, False
        for port, desc in self.node["inputs"].items():
            if desc.get("required", True) and self.buffers[port].take() is None:
                if port not in self.deadlines:
                    return None
                waiting = True
                ready_at = max(ready_at if ready_at is not None else now, self.deadlines[port])
        if self.node["trigger"]["mode"] == "periodic":
            ready_at = max(ready_at if ready_at is not None else now, self.next_tick)
        elif not waiting:
            return None
        return max(0, ready_at - now)
