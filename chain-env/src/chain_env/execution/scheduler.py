"""Select input snapshots, including null values after required-input deadlines."""
import time


def select_snapshot(node, buffers, last, expired, periodic_due):
    """Pure readiness rules shared by real-time and integer-time simulation."""
    selected = {port: record for port, buffer in buffers.items()
                if (record := buffer.take()) is not None}
    for port, desc in node["inputs"].items():
        if desc.get("required", True) and port not in selected:
            if port not in expired:
                return None
            selected[port] = None
    if node["trigger"]["mode"] == "periodic":
        if not periodic_due:
            return None
    elif not any(record is None or buffers[port].policy.buffer == "fifo" or
                 last.get(port) != record["id"] for port, record in selected.items()):
        return None
    return selected


def reserve_snapshot(buffers, selected):
    for port, record in selected.items():
        if record is not None and buffers[port].policy.buffer == "fifo":
            buffers[port].reserved.add(record["id"])


def consume_snapshot(buffers, last, selected):
    for port, record in selected.items():
        if record is not None:
            last[port] = record["id"]
            if buffers[port].policy.buffer == "fifo":
                buffers[port].consumed(record["id"])


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
        expired = {port for port, deadline in self.deadlines.items() if now >= deadline}
        return select_snapshot(self.node, self.buffers, self.last, expired, now >= self.next_tick)

    def complete(self, selected):
        consume_snapshot(self.buffers, self.last, selected)
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
