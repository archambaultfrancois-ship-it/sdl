"""Bounded real-time observations, independent of delivery and logical time."""
from collections import deque
import math
import time


def describe(plan):
    """Expose scheduling and queue policies, without commands, secrets or schemas."""
    return {
        "version": 1,
        "components": {name: {"trigger": node.get("trigger", {}), "inputs": {
            port: {key: desc[key] for key in ("required", "timeout") if key in desc}
            for port, desc in node["inputs"].items()}}
            for name, node in plan["components"].items()},
        "subscriptions": {sub["id"]: {key: sub[key] for key in ("from", "to", "policy", "routing") if key in sub}
                          for sub in plan["subscriptions"]},
    }


class TickTimings:
    """Wall time driving CH/1, excluding caller-side publication/delivery waits.

    Includes IPC and waiting for the component process; this is not CPU time.
    Percentiles cover at most 256 completed ticks from the last five seconds.
    """
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.samples = deque(maxlen=256)
        self.started = None

    async def events(self, events):
        self.started = self.clock()
        elapsed = 0
        try:
            while True:
                start = self.clock()
                try:
                    event = await anext(events)
                except StopAsyncIteration:
                    elapsed += self.clock() - start
                    self.samples.append((self.clock(), elapsed * 1000))
                    return
                elapsed += self.clock() - start
                yield event
        finally:
            self.started = None
            await events.aclose()

    def snapshot(self, scheduler):
        now = self.clock()
        while self.samples and now - self.samples[0][0] > 5:
            self.samples.popleft()
        values = sorted(value for _, value in self.samples)
        return {"tick_p95_ms": values[math.ceil(.95 * len(values)) - 1] if values else None,
                "tick_samples": len(values), "tick_window_seconds": 5,
                "busy_ms": (now - self.started) * 1000 if self.started is not None else None,
                "waiting_inputs": [port for port, desc in scheduler.node["inputs"].items()
                    if desc.get("required", True) and scheduler.buffers.get(port) is not None
                    and scheduler.buffers[port].take() is None
                    and now < scheduler.deadlines.get(port, float("inf"))]
                    if self.started is None else []}
