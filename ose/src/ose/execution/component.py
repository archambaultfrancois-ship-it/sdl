"""Synchronous Python SDK matching the C and Rust binary component protocol."""
import json
import struct
import sys
from enum import Enum
from ose.recording.component import validate_name, MAX_POINTS, MAX_TICK_BYTES
from ose.execution.ipc import MAX_SIZE, TICK_CONTEXT, frame, port_data, unpack_port, tick_context


def read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        part = stream.read(size - len(result))
        if not part:
            raise EOFError("adapter disconnected")
        result.extend(part)
    return bytes(result)


class RecordResult(str, Enum):
    QUEUED = "queued"
    DISABLED = "disabled"
    DROPPED = "dropped"


class ComponentSession:
    def __init__(self, reader=None, writer=None, *, auto_ready=True, timed_ticks=False):
        self.reader = reader or sys.stdin.buffer
        self.writer = writer or sys.stdout.buffer
        op, payload = self.receive()
        if op != "I":
            raise ValueError("expected INIT")
        self.config = json.loads(payload)
        self.parameter_values = {}
        bindings = self.config.get("parameter_bindings", {})
        expected = {name for name, binding in bindings.items() if 'read' in binding['access']}
        for _ in expected:
            op, data = self.receive()
            if op != 'U':
                raise ValueError('expected initial shared parameter block')
            alias, catalogue, payload = unpack_port(data, True)
            if alias not in expected or alias in self.parameter_values or catalogue != bindings[alias]['catalogue'] or not payload:
                raise ValueError('invalid initial shared parameter block')
            self.parameter_values[alias] = (catalogue, payload)
        self.is_ready = False
        self.recording_points = {}
        self._recording_bytes = 0
        self._in_tick = False
        self.timed_ticks = timed_ticks
        self.time_ns, self.tick_index = 0, 0
        if auto_ready:
            self.ready()

    def ready(self):
        if not self.is_ready:
            self.send("R", b"\x03" if self.config.get("parameter_bindings") else (TICK_CONTEXT if self.timed_ticks else b""))
            self.is_ready = True

    def receive(self):
        size = struct.unpack(">I", read_exact(self.reader, 4))[0]
        if not 1 <= size <= MAX_SIZE:
            raise ValueError("invalid IPC size")
        body = read_exact(self.reader, size)
        return chr(body[0]), body[1:]

    def send(self, op, data=b""):
        self.writer.write(frame(op, data))
        self.writer.flush()

    def publish(self, port, payload):
        self.send("P", port_data(port, payload))

    def declare_recording_point(self, name, root, catalogue, description=""):
        validate_name(name)
        if self.is_ready or name in self.recording_points or len(self.recording_points) >= MAX_POINTS:
            raise ValueError("recording declarations require initialization and unique names")
        if not root or any("\0" in value for value in (root, catalogue, description)) or len(description.encode()) > 1024:
            raise ValueError("invalid recording descriptor")
        self.send("N", port_data(name, (root + "\0" + description + "\0" + catalogue).encode()))
        self.recording_points[name] = False

    def recording_enabled(self, name):
        return self.recording_points[name]

    def record(self, name, payload):
        if not self._in_tick:
            raise RuntimeError("recording requires an active tick")
        if not self.recording_enabled(name):
            return RecordResult.DISABLED
        body_size = 2 + len(name.encode()) + len(payload)
        if body_size > MAX_TICK_BYTES - self._recording_bytes:
            return RecordResult.DROPPED
        if not payload:
            raise ValueError("empty recording payload")
        self.send("C", port_data(name, payload))
        self._recording_bytes += body_size
        return RecordResult.QUEUED

    def set_parameters(self, alias, payload):
        binding = self.config.get('parameter_bindings', {}).get(alias)
        if binding is None or 'write' not in binding['access']:
            raise ValueError('parameter write access denied')
        self.send('S', port_data(alias, payload))
        op, result = self.receive()
        if op != 'A' or not result or result[:1] not in (b'\x00', b'\x01'):
            raise ValueError('invalid parameter write result')
        if result != b'\x01':
            raise ValueError(result[1:].decode() or 'parameter write failed')

    def ticks(self):
        inputs = {}
        while True:
            op, data = self.receive()
            if op == 'E':
                active = data.decode().split("\0") if data else []
                if len(active) != len(set(active)) or set(active) - self.recording_points.keys():
                    raise ValueError("unknown or duplicate active recording point")
                self.recording_points.update((name, name in active) for name in self.recording_points)
            elif op == 'U':
                alias, catalogue, payload = unpack_port(data, True)
                binding = self.config.get('parameter_bindings', {}).get(alias)
                if binding is None or 'read' not in binding['access'] or catalogue != binding['catalogue'] or not payload:
                    raise ValueError('invalid shared parameter update')
                self.parameter_values[alias] = (catalogue, payload)
            elif op == "D":
                port, catalogue, payload = unpack_port(data, True)
                if port in inputs:
                    raise ValueError("duplicate input in tick")
                inputs[port] = None if not catalogue and not payload else (catalogue, payload)
            elif op == "T":
                self.time_ns, self.tick_index = tick_context(data, self.tick_index)
                self._in_tick = True
                self._recording_bytes = 0
                yield inputs
                self._in_tick = False
                self.send("F")
                inputs = {}
            elif op == "X":
                if data:
                    raise ValueError("STOP must be empty")
                return
            else:
                raise ValueError("unexpected adapter event")
