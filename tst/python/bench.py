import os
import sys
import time

from sdl_runtime import Complex32, decode, encode
from benchmark import BenchPayload


def report_rate(operation, iterations, bytes_per_message, seconds):
   if seconds <= 0.0:
      print('{:<6} below timer resolution'.format(operation))
      return
   messages_per_second = iterations / seconds
   mebibytes_per_second = messages_per_second * bytes_per_message / (1024.0 * 1024.0)
   print('{:<6} {:>10.0f} msg/s  {:>8.2f} MiB/s  ({} bytes/message)'.format(
      operation, messages_per_second, mebibytes_per_second, bytes_per_message))


def read_iterations():
   value = os.environ.get('SDL_BENCH_ITERATIONS', '200')
   if not value or any(character < '0' or character > '9' for character in value):
      return 200
   try:
      iterations = int(value)
   except ValueError:
      return 200
   return iterations if 0 < iterations <= sys.maxsize else 200


def main():
   iterations = read_iterations()
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
      decode(wire, BenchPayload)
   report_rate('decode', iterations, len(wire), time.perf_counter() - start)
   print('iterations: {}, wire endian: big'.format(iterations))


if __name__ == '__main__':
   main()
