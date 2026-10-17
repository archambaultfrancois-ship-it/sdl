"""CH/1 component protocol: length-prefixed binary events on stdio."""
import struct

MAX_SIZE = 1024 * 1024
TICK_CONTEXT = b"\x01"


def tick_context(data, previous_index=0):
    """Decode negotiated tick metadata; retain support for historical empty ticks."""
    if not data:
        return 0, previous_index + 1
    if len(data) != 16:
        raise ValueError("invalid tick context")
    timestamp, index = struct.unpack(">QQ", data)
    if index == 0:
        raise ValueError("tick index must be positive")
    return timestamp, index


def frame(op, data=b""):
    body = op.encode("ascii") + data
    if len(body) > MAX_SIZE:
        raise ValueError("IPC frame exceeds 1 MiB")
    return struct.pack(">I", len(body)) + body


def port_data(port, payload, catalogue=None):
    name = port.encode()
    if len(name) > 65535:
        raise ValueError("port name too long")
    result = struct.pack(">H", len(name)) + name
    if catalogue is not None:
        text = catalogue.encode()
        result += struct.pack(">I", len(text)) + text
    return result + payload


def unpack_port(data, with_catalogue=False):
    if len(data) < 2:
        raise ValueError("truncated IPC port")
    length = struct.unpack(">H", data[:2])[0]
    if len(data) < 2 + length:
        raise ValueError("truncated IPC port name")
    port, rest = data[2:2 + length].decode(), data[2 + length:]
    if not with_catalogue:
        return port, rest
    if len(rest) < 4:
        raise ValueError("truncated IPC catalogue")
    size = struct.unpack(">I", rest[:4])[0]
    if size > len(rest) - 4:
        raise ValueError("truncated IPC catalogue")
    return port, rest[4:4 + size].decode(), rest[4 + size:]


async def receive(reader):
    size = struct.unpack(">I", await reader.readexactly(4))[0]
    if not 1 <= size <= MAX_SIZE:
        raise ValueError("invalid IPC size")
    packet = await reader.readexactly(size)
    return chr(packet[0]), packet[1:]


async def send(writer, op, data=b""):
    writer.write(frame(op, data))
    await writer.drain()
