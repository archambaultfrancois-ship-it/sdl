"""CENV/1: bounded JSON metadata plus an opaque binary S2PS/2 body."""
import json
import struct

MAGIC = b"CE\x01"


def encode(header, payload=b"", limit=1024 * 1024):
    metadata = json.dumps(header, separators=(",", ":"), allow_nan=False).encode()
    if len(metadata) > 65536:
        raise ValueError("metadata exceeds 64 KiB")
    packet = MAGIC + struct.pack(">I", len(metadata)) + metadata + payload
    if len(packet) > limit:
        raise ValueError("transport message exceeds configured size limit")
    return packet


def decode(packet, limit=1024 * 1024):
    if len(packet) > limit or len(packet) < 7 or packet[:3] != MAGIC:
        raise ValueError("invalid CENV/1 packet")
    length = struct.unpack(">I", packet[3:7])[0]
    if length > 65536 or length > len(packet) - 7:
        raise ValueError("invalid metadata length")
    header = json.loads(packet[7:7 + length])
    if not isinstance(header, dict) or not isinstance(header.get("op"), str):
        raise ValueError("invalid metadata")
    return header, packet[7 + length:]
