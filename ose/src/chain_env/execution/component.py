"""Synchronous Python SDK matching the C and Rust binary component protocol."""
import json
import struct
import sys
from chain_env.execution.ipc import MAX_SIZE, TICK_CONTEXT, frame, port_data, unpack_port, tick_context


def read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        part = stream.read(size - len(result))
        if not part:
            raise EOFError("adapter disconnected")
        result.extend(part)
    return bytes(result)


class ComponentSession:
    def __init__(self, reader=None, writer=None, *, auto_ready=True, timed_ticks=False):
        self.reader = reader or sys.stdin.buffer
        self.writer = writer or sys.stdout.buffer
        op, payload = self.receive()
        if op != "I":
            raise ValueError("expected INIT")
        self.config = json.loads(payload)
        self.is_ready = False
        self.timed_ticks = timed_ticks
        self.time_ns, self.tick_index = 0, 0
        if auto_ready:
            self.ready()

    def ready(self):
        if not self.is_ready:
            self.send("R", TICK_CONTEXT if self.timed_ticks else b"")
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

    def ticks(self):
        inputs = {}
        while True:
            op, data = self.receive()
            if op == "D":
                port, catalogue, payload = unpack_port(data, True)
                if port in inputs:
                    raise ValueError("duplicate input in tick")
                inputs[port] = None if not catalogue and not payload else (catalogue, payload)
            elif op == "T":
                self.time_ns, self.tick_index = tick_context(data, self.tick_index)
                yield inputs
                self.send("F")
                inputs = {}
            elif op == "X":
                if data:
                    raise ValueError("STOP must be empty")
                return
            else:
                raise ValueError("unexpected adapter event")
