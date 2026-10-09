import os
import statistics
import time
from sdl_runtime import Complex32, description, prepare, encode, decode
from benchmark import BenchPayload
from bench_cases import BenchSmall, BenchOptionals, BenchEntry, BenchVariable, BenchVector, BenchPose, BenchRecord, BenchRecordBatch


def benchmark(label, message, useful):
   cls = type(message)
   text = description([cls]); start = time.perf_counter()
   ctx = prepare(text, [cls]); preparation = (time.perf_counter()-start)*1e6
   wire = encode(ctx, message)
   assert decode(ctx, wire) == message
   for unused in range(100): encode(ctx, message); decode(ctx, wire)
   setting = os.environ.get('SDL_BENCH_ITERATIONS', '200')
   iterations = int(setting) if setting.isascii() and setting.isdecimal() and int(setting)>0 else 200
   print('{} metadata: {} description bytes, {:.1f} us prepare'.format(label, len(text.encode()), preparation))
   for operation, run in (('encode', lambda: encode(ctx, message)), ('decode', lambda: decode(ctx, wire))):
      rates = []
      for unused in range(3):
         start = time.perf_counter(); n = 0
         while n < iterations or time.perf_counter()-start < .1:
            run(); n += 1
         rates.append(n/(time.perf_counter()-start))
      rate = statistics.median(rates)
      print('{} {} {:.1f} msg/s {:.4f} MiB/s ({} bytes/message)'.format(
         label, operation, rate, rate*useful/1048576, len(wire)))


if __name__ == '__main__':
   benchmark('small', BenchSmall(active=True, sequence=123, code=-2), 7)
   benchmark('optional_sparse', BenchOptionals(a=1), 4)
   benchmark('optional_dense', BenchOptionals(**dict(zip('abcdefgh', range(1, 9)))), 32)
   benchmark('variable', BenchVariable(header='V'*40, entries=[
      BenchEntry(label='entry'+str(i), number=i) for i in range(8)]), 152)
   benchmark('packed', BenchPayload(header='H'*200, samples=[
      Complex32(i*.25, -(i%97)*.5) for i in range(5000)]), 40200)
   benchmark('packed_struct', BenchRecordBatch(records=[
      BenchRecord(id=i, pose=BenchPose(position=BenchVector(values=[i*.25, -i*.5, i%97]),
         rotation=[0, 0, 0, 1]), measures=[i*.125, -(i%31)*.5])
      for i in range(1000)]), 40000)

   # Keep the object API matrix and expose the alternate representation separately.
   from sdl_runtime import _sdl_native
   if _sdl_native is not None and not os.environ.get('SDL_PYTHON_NO_NATIVE'):
      from bench_buffers import main as buffer_bench
      buffer_bench()
