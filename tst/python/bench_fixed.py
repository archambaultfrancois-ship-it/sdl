"""Compare largest fixed-message plans with the existing field codec.

Run with PYTHONPATH=runtime/python:build/generated/python.
SDL_PYTHON_NO_NATIVE=1 measures the pure Python implementation.
"""
import statistics
import time

from sdl_runtime import description, prepare, encode, decode
from bench_cases import BenchSmall, BenchRecord, BenchPose, BenchVector, BenchRecordBatch


def rate(run):
   samples = []
   for unused in range(5):
      start = time.perf_counter()
      count = 0
      while time.perf_counter() - start < .15:
         run()
         count += 1
      samples.append(count / (time.perf_counter() - start))
   return statistics.median(samples)


def main():
   record = BenchRecord(id=7, pose=BenchPose(
      position=BenchVector(values=[1., 2., 3.]), rotation=[0., 0., 0., 1.]),
      measures=[4., 5.])
   for message in (BenchSmall(active=True, sequence=123, code=-2), record,
                   BenchRecordBatch(records=[record] * 1000)):
      cls = type(message)
      text = description(cls)
      baseline = prepare(text, cls)
      baseline.fixed_messages.clear()
      accelerated = prepare(text, cls)
      wire = encode(baseline, message)
      assert encode(accelerated, message) == wire
      assert decode(accelerated, wire) == message
      print(cls.__name__)
      for label, operation in (('encode', lambda ctx: encode(ctx, message)),
                               ('decode', lambda ctx: decode(ctx, wire))):
         before = rate(lambda: operation(baseline))
         after = rate(lambda: operation(accelerated))
         print('  {}: {:.0f} -> {:.0f} msg/s ({:.2f}x)'.format(label, before, after, after / before))


if __name__ == '__main__':
   main()
