"""Component-owned diagnostic points, separate from application publications."""
import asyncio
import re
from collections import Counter

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
MAX_POINTS = 128
MAX_TICK_BYTES = 256 * 1024


def validate_name(name):
    if not isinstance(name, str) or len(name.encode()) > 200 or not NAME.fullmatch(name):
        raise ValueError("recording name must contain dot-separated ASCII identifiers (max 200 bytes)")
    return name


def declaration(body):
    from ose.execution.ipc import unpack_port
    name, rest = unpack_port(body)
    validate_name(name)
    root, description, catalogue = rest.decode().split("\0", 2)
    if not root or "\0" in catalogue or len(description.encode()) > 1024:
        raise ValueError("invalid recording declaration")
    return name, dict(root=root, description=description, catalogue=catalogue)


class ComponentRecording:
    """CH/1 endpoint; callbacks never wait for network/disk when emitting."""
    def __init__(self, codec, *, declare=None, active=None, emit=None):
        self.codec, self.on_declare, self.on_active, self.on_emit = codec, declare, active, emit
        self.points = {}

    async def declare(self, body):
        name, desc = declaration(body)
        if name in self.points or len(self.points) >= MAX_POINTS:
            raise ValueError("duplicate recording point or too many points")
        if self.codec is not None:
            prepared = self.codec.api.prepare(desc["catalogue"])
            if desc["root"] not in self.codec.api.message_names(prepared):
                raise ValueError("unknown recording message root")
        if self.on_declare:
            await self.on_declare(name, desc)
        self.points[name] = desc

    def activation(self):
        active = set(self.on_active() if self.on_active else ())
        return "\0".join(sorted(active & self.points.keys())).encode()

    def emit(self, name, payload):
        desc = self.points[name]
        if not payload:
            raise ValueError("empty recording payload")
        if self.on_emit:
            self.on_emit(name, desc, payload)


class RemoteRecording:
    """Bounded adapter queue and independent control polling for the local broker."""
    def __init__(self, name, codec, request):
        self.name, self.codec, self.request = name, codec, request
        self.active = set()
        self.queue = asyncio.Queue(maxsize=64)
        self.bytes = 0
        self.counts = Counter()
        self.endpoint = ComponentRecording(codec, declare=self.declare,
            active=lambda: self.active, emit=self.emit)

    async def declare(self, name, desc):
        await self.request("recording_declare", desc["catalogue"].encode(),
            name=name, root=desc["root"], description=desc["description"])

    def emit(self, name, desc, payload):
        if self.queue.full() or self.bytes + len(payload) > 1024 * 1024:
            self.counts["dropped"] += 1
            return
        self.queue.put_nowait((name, payload))
        self.bytes += len(payload)

    async def poll(self):
        while True:
            result, _ = await self.request("recording_component_state")
            self.active = set(result["active"])
            await asyncio.sleep(.05)

    async def send(self):
        while True:
            name, payload = await self.queue.get()
            try:
                await self.request("recording_emit", payload, name=name)
            finally:
                self.bytes -= len(payload)
