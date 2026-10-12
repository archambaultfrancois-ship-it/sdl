#!/usr/bin/env python3
"""Generate a small Ethernet/IPv4/UDP capture containing SDL frames."""

import importlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent / "samples" / "sdl-demo.pcap"


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

      basic = schema.RootPayload(
         header="SDL monitor report",
         fixed_array=[schema.FixedItem(x=1.25, y=-2.5),
            schema.FixedItem(x=3.5, y=4.75)],
         var_array=[schema.VarItem(name="sensor-A", id=101),
            schema.VarItem(name=None, id=-202)])
      rich = cases.CodecCases(
         tiny=-7, small=-300, signed_value=-12345, wide=1234567890123,
         ratio=0.625, precise=-123.0000001,
         point=runtime.Complex32(1.5, -2.25),
         position=runtime.Complex64(-3.125, 4.5),
         state=cases.State.NEGATIVE, empty_text="", required_zero=0,
         samples=[-2, 0, 300], measurements=[0.125, -9.5],
         labels=["north", "east"],
         points=[runtime.Complex32(0.5, 1.0), runtime.Complex32(-0.5, -1.0)],
         required_enabled=True, optional_enabled=False,
         bool_flags=[True, False, True],
         fixed_states=[cases.State.UNKNOWN, cases.State.READY, cases.State.NEGATIVE],
         packed_states=[cases.State.READY, cases.State.NEGATIVE],
         packed_flags=[False, True, True], high_id_value=-77)
      iq = benchmark.BenchPayload(
         header="Radio capture metadata: " + "receiver=demo; channel=42; " * 7,
         samples=[runtime.Complex32(index / 4.0, -index / 8.0)
            for index in range(12)])

      messages = [basic, rich, iq]
      records = []
      for index, message in enumerate(messages):
         encoded = runtime.encode(message)
         records.append((1_700_000_000 + index, udp_frame(encoded, 46000 + index, index + 1)))
      write_pcap(records)
   print("Wrote {} ({} UDP/SDL frames)".format(OUTPUT, len(records)))
   print("SDL wire order: big endian; UDP destination port: 47000")


if __name__ == "__main__":
   main()
