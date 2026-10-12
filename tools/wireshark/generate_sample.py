#!/usr/bin/env python3
"""Generate Ethernet/IPv4/UDP sample captures for the SDL Wireshark dissectors."""

import importlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT = OUTPUT_DIR / "sdl-demo.pcap"
ROBUSTNESS_OUTPUT = OUTPUT_DIR / "sdl-robustness.pcapng"


def checksum(data):
   if len(data) & 1:
      data += b"\0"
   total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
   while total >> 16:
      total = (total & 0xffff) + (total >> 16)
   return (~total) & 0xffff


def udp_frame(payload, source_port, packet_id):
   source_ip = bytes((192, 0, 2, 10))
   target_ip = bytes((192, 0, 2, 20))
   source_mac = bytes.fromhex("02000000000a")
   target_mac = bytes.fromhex("020000000014")
   udp_length = 8 + len(payload)
   udp = struct.pack("!HHHH", source_port, 47000, udp_length, 0) + payload
   ip_header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + udp_length,
      packet_id, 0x4000, 64, 17, 0, source_ip, target_ip)
   ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
   ethernet = target_mac + source_mac + struct.pack("!H", 0x0800)
   return ethernet + ip_header + udp


def write_pcap(records):
   OUTPUT.parent.mkdir(parents=True, exist_ok=True)
   with OUTPUT.open("wb") as capture:
      capture.write(struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))
      for seconds, packet in records:
         capture.write(struct.pack("<IIII", seconds, 0, len(packet), len(packet)))
         capture.write(packet)


def pcapng_block(block_type, body):
   length = 12 + len(body)
   return struct.pack("<II", block_type, length) + body + struct.pack("<I", length)


def pad4(data):
   return data + bytes((-len(data)) % 4)


def write_pcapng(records):
   OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
   with ROBUSTNESS_OUTPUT.open("wb") as capture:
      # Section Header Block: little-endian section, version 1.0.
      capture.write(pcapng_block(0x0A0D0D0A,
         struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)))
      # Interface Description Block: Ethernet, 65535-byte snapshot length.
      capture.write(pcapng_block(1, struct.pack("<HHI", 1, 0, 65535)))
      for seconds, packet, comment in records:
         timestamp = seconds * 1_000_000
         options = (struct.pack("<HH", 1, len(comment)) + pad4(comment) +
            struct.pack("<HH", 0, 0))
         body = struct.pack("<IIIII", 0, timestamp >> 32,
            timestamp & 0xFFFFFFFF, len(packet), len(packet))
         body += pad4(packet) + options
         capture.write(pcapng_block(6, body))


def corrupt_field_payload(frame, field_id, replacement):
   descriptor_length = int.from_bytes(frame[:4], "big")
   body_offset = 8 + descriptor_length
   body = bytearray(frame[body_offset:])
   offset = 0
   while offset < len(body):
      current_id = int.from_bytes(body[offset:offset + 4], "big")
      payload_length = int.from_bytes(body[offset + 4:offset + 8], "big")
      payload_offset = offset + 8
      if current_id == field_id:
         if payload_length != len(replacement):
            raise ValueError("replacement must preserve the field payload length")
         body[payload_offset:payload_offset + payload_length] = replacement
         return frame[:body_offset] + bytes(body)
      offset = payload_offset + payload_length
   raise ValueError("field ID not found in encoded frame")


def main():
   with tempfile.TemporaryDirectory(prefix="sdl-wireshark-") as temp:
      generated = Path(temp) / "generated"
      subprocess.run([sys.executable, str(ROOT / "gen/generator.py"),
         "-python", str(ROOT / "sdl"), str(generated)], check=True)
      sys.path.insert(0, str(ROOT / "runtime/python"))
      sys.path.insert(0, str(generated / "python"))
      os.environ["SDL_WIRE_ENDIAN"] = "big"
      runtime = importlib.import_module("sdl_runtime")
      schema = importlib.import_module("schema")
      cases = importlib.import_module("codec_cases")
      benchmark = importlib.import_module("benchmark")
      empty_message = importlib.import_module("empty_message")

      basic = schema.RootPayload(
         header="SDL monitor report",
         fixed_array=[schema.FixedItem(x=1.25, y=-2.5),
            schema.FixedItem(x=3.5, y=4.75)],
         var_array=[schema.VarItem(name="sensor-A", id=101),
            schema.VarItem(name=None, id=-202)])
      rich_demo = cases.CodecCases(
         tiny=-7, small=-300, signed_value=-12345, wide=1234567890123,
         ratio=0.625, precise=-123.0000001,
         point=runtime.Complex32(1.5, -2.25),
         position=runtime.Complex64(-3.125, 4.5),
         state=cases.State.NEGATIVE, empty_text="", required_zero=0,
         samples=[-2, 0, 300], measurements=[0.125, -9.5],
         labels=["north", "east"],
         points=[runtime.Complex32(0.5, 1.0),
            runtime.Complex32(-0.5, -1.0)],
         required_enabled=True, optional_enabled=False,
         bool_flags=[True, False, True],
         fixed_states=[cases.State.UNKNOWN, cases.State.READY,
            cases.State.NEGATIVE],
         packed_states=[cases.State.READY, cases.State.NEGATIVE],
         packed_flags=[False, True, True], high_id_value=-77)
      iq = benchmark.BenchPayload(
         header="Radio capture metadata: " + "receiver=demo; channel=42; " * 7,
         samples=[runtime.Complex32(index / 4.0, -index / 8.0)
            for index in range(12)])

      vector = lambda base: schema.FixedVector(
         coords=[base, base + 1.0],
         grid=[[int(base) + value for value in range(3)],
            [int(base) + value for value in range(3, 6)]])
      row = lambda base: schema.FixedRow(
         vectors=[vector(base), vector(base + 10.0)])
      board = schema.FixedBoard(rows=[row(0.0), row(100.0)],
         packed_rows=[row(200.0), row(300.0)])
      row_batch = schema.FixedRowBatch(rows=[row(-20.0), row(40.0)])
      anonymous = schema.AnonymousEnvelope(
         metadata=schema.AnonymousEnvelope_1(code=42,
            detail=schema.AnonymousEnvelope_2(text="nested metadata")),
         points=[schema.AnonymousEnvelope_3(x=1.25, y=-2.5),
            schema.AnonymousEnvelope_3(x=3.0, y=4.5)])
      enum_batch = cases.EnumRecordBatch(records=[
         cases.EnumRecord(state=cases.State.MINIMUM, code=-2147483648),
         cases.EnumRecord(state=cases.State.MAXIMUM, code=2147483647)])
      sparse = cases.CodecCases(required_zero=0, required_enabled=False)
      rich = cases.CodecCases(
         tiny=-128, small=32767, signed_value=-2147483648, wide=-(1 << 63),
         ratio=float("-inf"), precise=float("nan"),
         point=runtime.Complex32(-0.0, float("inf")),
         position=runtime.Complex64(float("inf"), -0.0),
         state=cases.State.MINIMUM, empty_text="", required_zero=0,
         samples=[-32768, 0, 32767], measurements=[0.125, -1024.5],
         labels=["", "été 🌍", "embedded\0NUL"],
         points=[runtime.Complex32(0.5, 1.0),
            runtime.Complex32(-0.5, -1.0)],
         required_enabled=True, optional_enabled=False,
         bool_flags=[True, False, True],
         fixed_states=[cases.State.MINIMUM, cases.State.READY,
            cases.State.MAXIMUM],
         packed_states=[cases.State.MINIMUM, cases.State.MAXIMUM],
         packed_flags=[False, True, True], high_id_value=-2147483648)

      messages = [basic, rich_demo, iq]
      records = []
      for index, message in enumerate(messages):
         encoded = runtime.encode(message)
         records.append((1_700_000_000 + index, udp_frame(encoded, 46000 + index, index + 1)))
      write_pcap(records)

      robustness_messages = [
         ("RootPayload: packed fixed structs and repeated nested messages", basic),
         ("CodecCases: integer limits, floats, complex, UTF-8, NUL, enums, arrays, field ID max", rich),
         ("BenchPayload: long string and packed complex samples", iq),
         ("FixedBoard: nested fixed and packed multidimensional arrays", board),
         ("FixedRowBatch: packed nested fixed-layout messages", row_batch),
         ("AnonymousEnvelope: anonymous nested structs and repeated structs", anonymous),
         ("EmptyMessage: descriptor-only frame", empty_message.EmptyMessage()),
         ("EnumRecordBatch: packed structs with enum and integer boundaries", enum_batch),
         ("CodecCases: absent optional and empty repeated fields", sparse),
      ]
      robustness_records = []
      encoded_frames = []
      for index, (comment, message) in enumerate(robustness_messages):
         encoded = runtime.encode(message)
         encoded_frames.append(encoded)
         packet = udp_frame(encoded, 46100 + index, index + 1)
         robustness_records.append((1_700_001_000 + index, packet,
            comment.encode("utf-8")))

      invalid_hash = bytearray(encoded_frames[0])
      descriptor_length = int.from_bytes(invalid_hash[:4], "big")
      invalid_hash[4 + descriptor_length] ^= 0x01
      robustness_records.append((1_700_001_009,
         udp_frame(bytes(invalid_hash), 46109, 10),
         b"EXPECTED MALFORMED: descriptor hash mismatch"))
      robustness_records.append((1_700_001_010,
         udp_frame(encoded_frames[0][:-1], 46110, 11),
         b"EXPECTED MALFORMED: truncated frame body"))
      invalid_bool = corrupt_field_payload(encoded_frames[1], 17, b"\x02")
      robustness_records.append((1_700_001_011,
         udp_frame(invalid_bool, 46111, 12),
         b"EXPECTED MALFORMED: bool payload is neither 0 nor 1"))
      write_pcapng(robustness_records)
   print("Wrote {} ({} UDP/SDL frames)".format(OUTPUT, len(records)))
   print("SDL wire order: big endian; UDP destination port: 47000")
   print("Wrote {} ({} valid and 3 intentionally malformed UDP/SDL frames)".format(
      ROBUSTNESS_OUTPUT, len(robustness_records) - 3))


if __name__ == "__main__":
   main()
