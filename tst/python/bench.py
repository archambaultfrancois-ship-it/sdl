import os
import time

from sdl_runtime import Complex32, decode, encode
from benchmark import BenchPayload


def report_rate(operation, iterations, bytes_per_message, seconds):
   messages_per_second = iterations / seconds
   mebibytes_per_second = messages_per_second * bytes_per_message / (1024.0 * 1024.0)
   print('{:<6} {:>10.0f} msg/s  {:>8.2f} MiB/s  ({} bytes/message)'.format(
      operation, messages_per_second, mebibytes_per_second, bytes_per_message))


def main():
   iterations = int(os.environ.get('SDL_BENCH_ITERATIONS', '200'))
   message = BenchPayload(
      header='H' * 200,
      samples=[
         Complex32(index * 0.25, -(index % 97) * 0.5)
         for index in range(5000)
      ],
   )
   wire = encode(message)
   assert len(wire) == 40224 + len(BenchPayload._SDL_DESCRIPTOR)

   start = time.perf_counter()
   for unused_iteration in range(iterations):
      encoded = encode(message)
      if len(encoded) != len(wire):
         raise RuntimeError('encoded message size changed')
   report_rate('encode', iterations, len(wire), time.perf_counter() - start)

   start = time.perf_counter()
   for unused_iteration in range(iterations):
      decoded = decode(wire, BenchPayload)
      if len(decoded.samples) != 5000:
         raise RuntimeError('decoded array size changed')
   report_rate('decode', iterations, len(wire), time.perf_counter() - start)
   print('iterations: {}, wire endian: big'.format(iterations))


if __name__ == '__main__':
   main()
