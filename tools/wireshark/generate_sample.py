#!/usr/bin/env python3
"""Generate Ethernet/IPv4/UDP sample captures for the SDL Wireshark dissectors."""

from pathlib import Path
import struct
import subprocess
import sys


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


def main():
   subprocess.run([sys.executable, str(ROOT/'gen/generator.py'), '-python',
      str(ROOT/'sdl'), str(ROOT/'build/generated')], check=True)
   sys.path[:0]=[str(ROOT/'runtime/python'),str(ROOT/'build/generated/python')]
   from sdl_runtime import description, prepare, encode
   from wire_example import Packet
   from empty_message import EmptyMessage
   from codec_cases import CodecCases, State
   from schema import RootPayload, FixedItem, VarItem
   types=[Packet,EmptyMessage,CodecCases,RootPayload]
   text=description(types);ctx=prepare(text,types)
   messages=[Packet(active=True,code=-2,label='été',samples=[300,-1]),
      Packet(label='a\0b',samples=[]),EmptyMessage(),
      CodecCases(tiny=-128,wide=-9223372036854775808,state=State.MINIMUM),
      RootPayload(header='nested',fixed_array=[FixedItem(x=1,y=2)],var_array=[VarItem(name='',id=7)])]
   payloads=[text.encode('utf-8')]+[encode(ctx,m) for m in messages]
   records=[(1780000000+i,udp_frame(payload,46000,i+1)) for i,payload in enumerate(payloads)]
   write_pcap(records)
   robustness=[(seconds,packet,b'SDL2 catalogue' if i==0 else b'valid SDL2 data')
      for i,(seconds,packet) in enumerate(records)]
   bad_bool=bytearray(payloads[1]);bad_bool[1]=2
   bad_optional=bytearray(payloads[1]);bad_optional[2]=2
   invalid=[(payloads[1][:-1],b'truncated array'),(bytes(bad_bool),b'invalid bool'),
      (bytes(bad_optional),b'optional count exceeds one')]
   for i,(payload,comment) in enumerate(invalid):
      robustness.append((1780000100+i,udp_frame(payload,46000,100+i),comment))
   write_pcapng(robustness)
   print('Wrote SDL2 captures: one catalogue announcement, five valid data messages, three malformed cases.')


if __name__=='__main__':main()
