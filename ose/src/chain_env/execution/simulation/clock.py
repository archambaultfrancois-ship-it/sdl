"""Exact virtual deadlines and the shared component readiness rules."""
from decimal import Decimal, ROUND_HALF_UP
import math
from chain_env.execution.scheduler import select_snapshot, consume_snapshot

MAX_NS = (1 << 64) - 1


def seconds_ns(value, *, positive=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0 and (not positive or value > 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("simulation time must be a finite nonnegative number (positive for durations)")
    scaled = Decimal(str(value)) * 1_000_000_000
    if scaled > MAX_NS:
        raise ValueError("simulation time exceeds uint64 nanoseconds")
    result = int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
    return max(1, result) if value > 0 else 0


class VirtualClock:
    def __init__(self):
        self.time_ns = 0

    def seconds(self):
        return self.time_ns / 1_000_000_000


class Schedule:
    def __init__(self, node, buffers, clock):
        self.node, self.buffers, self.clock = node, buffers, clock
        self.last, self.tick_index = {}, 0
        self.timeouts = {port: seconds_ns(desc["timeout"], positive=True)
                         for port, desc in node["inputs"].items() if "timeout" in desc}
        self.period = node["trigger"]["period_ms"] * 1_000_000 if node["trigger"]["mode"] == "periodic" else 0
        if self.period > MAX_NS:
            raise ValueError("simulation period exceeds uint64 nanoseconds")
        self.arm()

    def arm(self):
        self.deadlines = {port: self.clock.time_ns + duration for port, duration in self.timeouts.items()}
        self.next_tick = self.clock.time_ns + (self.period if self.tick_index else 0)

    def select(self):
        expired = {port for port, deadline in self.deadlines.items() if deadline <= self.clock.time_ns}
        return select_snapshot(self.node, self.buffers, self.last, expired, self.clock.time_ns >= self.next_tick)

    def complete(self, selected):
        consume_snapshot(self.buffers, self.last, selected)
        self.arm()

    def next_event(self):
        """Earliest time readiness can change without a new input; avoid expired-timer spin."""
        ready = self.clock.time_ns
        waiting = False
        for port, desc in self.node["inputs"].items():
            if desc.get("required", True) and self.buffers.get(port) is not None and self.buffers[port].take() is None:
                if port not in self.deadlines:
                    return None
                ready = max(ready, self.deadlines[port])
                waiting = True
        if self.period:
            ready = max(ready, self.next_tick)
        elif not waiting:
            return None
        return ready if ready > self.clock.time_ns else None
