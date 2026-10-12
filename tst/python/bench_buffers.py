"""Explicit Packed-buffer API benchmark; separate from the object API matrix."""
import os
import json
from pathlib import Path
import statistics
import struct
import time
from sdl_runtime import PackedArray, Complex32, description, prepare, encode, encode_into, decode
from benchmark import BenchPayload
from bench_cases import BenchRecordBatch, BenchRecord, BenchPose, BenchVector


def measure(label, message, useful):
   ctx = prepare(description(type(message)), type(message))
   wire = encode(ctx, message)
   field = 'samples' if isinstance(message, BenchPayload) else 'records'
   value_type = 'c32' if field == 'samples' else 'BenchRecord'
   values = getattr(message, field)
   if field == 'samples':
      native_buffer = b''.join(struct.pack('=ff', z.real, z.imag) for z in values)
   else:
      native_buffer = b''.join(struct.pack('=i9f', r.id, *r.pose.position.values, *r.pose.rotation, *r.measures) for r in values)
   wire_packed = PackedArray.from_values(ctx, value_type, values)
   native_packed = PackedArray.from_buffer(ctx, value_type, native_buffer, defer=True)
   setattr(message, field, wire_packed)
   assert encode(ctx, message) == wire
   setattr(message, field, native_packed)
   assert encode(ctx, message) == wire
   assert encode(ctx, message) == wire
   decoded = decode(ctx, wire, packed='view')
   assert decoded == message
   output = bytearray(len(wire))
   assert encode_into(ctx, message, output) == len(wire) and output == wire
   setting = os.environ.get('SDL_BENCH_ITERATIONS', '200')
   iterations = int(setting) if setting.isascii() and setting.isdecimal() and int(setting)>0 else 200
   rates = []
   for source, run in ((wire_packed, lambda: encode(ctx, message)),
                       (native_packed, lambda: encode(ctx, message)),
                       (native_packed, lambda: encode_into(ctx, message, output)),
                       (wire_packed, lambda: decode(ctx, wire, packed='view'))):
      setattr(message, field, source)
      for _ in range(100):
         run()
      trials = []
      for _ in range(3):
         n = 0
         start = time.perf_counter()
         while n < iterations or time.perf_counter()-start < .1:
            run()
            n += 1
         trials.append(n/(time.perf_counter()-start)*useful/1e6)
      rates.append(round(statistics.median(trials)))
   return label, rates


def main():
   rows = [measure('packed', BenchPayload(header='H'*200, samples=[
      Complex32(i*.25, -(i%97)*.5) for i in range(5000)]), 40200),
      measure('packed_struct', BenchRecordBatch(records=[
         BenchRecord(id=i, pose=BenchPose(position=BenchVector(values=[i*.25, -i*.5, i%97]),
            rotation=[0, 0, 0, 1]), measures=[i*.125, -(i%31)*.5])
         for i in range(1000)]), 40000)]
   names = ('encode_wire_mb_s', 'encode_native_mb_s', 'encode_native_into_mb_s', 'decode_view_mb_s')
   artifact = Path(__file__).resolve().parents[2] / 'build' / 'python-buffer-benchmarks.json'
   artifact.parent.mkdir(exist_ok=True)
   artifact.write_text(json.dumps([dict(case=label, **dict(zip(names, rates))) for label, rates in rows], indent=2)+'\n')
   print('Python Packed buffer API — MB/s, rounded to integers')
   print('| Case          | Encode wire buffer | Encode native buffer | Encode native into | Decode view |')
   print('| ------------- | -----------------: | -------------------: | -----------------: | ----------: |')
   for label, rates in rows:
      print('| {:13} | {:18} | {:20} | {:18} | {:11} |'.format(label, *rates))
   print('Same messages and wire bytes; buffer representation, no per-record object creation.')
   print('Native-buffer encoders swap directly into final output on every call; wire-buffer encode reuses big-endian input.')
   print('Initial input creation and optional tolist() materialization are outside the measurements.')


if __name__ == '__main__':
   main()
