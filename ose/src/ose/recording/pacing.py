"""A reanchorable monotonic clock for paced playback and live controls."""
import asyncio
import math
import time


def playback_speed(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("speed must be finite and nonnegative (0 means maximum speed)")
    return value


class PlaybackClock:
    def __init__(self, speed=1, *, now=time.monotonic):
        self.speed, self.now = playback_speed(speed), now
        self.anchor_wall, self.anchor_ns = now(), 0
        self.last_ns, self.paused, self.stopped = 0, False, False
        self.changed = asyncio.Event()

    def position(self):
        if self.paused:
            return self.anchor_ns
        if self.speed == 0:
            return self.last_ns
        return self.anchor_ns + (self.now() - self.anchor_wall) * self.speed * 1e9

    def set_speed(self, value):
        value = playback_speed(value)
        self.anchor_ns, self.anchor_wall = self.position(), self.now()
        self.speed = value
        self.changed.set()

    def pause(self):
        self.anchor_ns, self.anchor_wall = self.position(), self.now()
        self.paused = True
        self.changed.set()

    def resume(self):
        if self.paused:
            self.anchor_wall = self.now()
            self.paused = False
            self.changed.set()

    def stop(self):
        self.stopped = True
        self.changed.set()

    async def wait(self, at_ns):
        while not self.stopped:
            self.changed.clear()
            if self.paused:
                await self.changed.wait()
                continue
            if self.speed == 0:
                self.last_ns = at_ns
                return 0.0
            delay = (at_ns - self.position()) / (self.speed * 1e9)
            if delay <= 0:
                self.last_ns = at_ns
                return -delay
            try:
                await asyncio.wait_for(self.changed.wait(), delay)
            except asyncio.TimeoutError:
                pass
        return None
