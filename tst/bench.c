#include "benchmark.h"
#include "sdl_registry.h"
#include "type_engine.h"

#include <assert.h>
#include <complex.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double elapsed_seconds(clock_t start, clock_t end) {
   return (double)(end - start) / (double)CLOCKS_PER_SEC;
}

static void report_rate(const char *operation, size_t iterations,
   size_t bytes_per_message, double seconds) {
   double messages_per_second = (double)iterations / seconds;
   double megabytes_per_second = messages_per_second * (double)bytes_per_message /
      (1024.0 * 1024.0);
   printf("%-6s %10.0f msg/s  %8.2f MiB/s  (%lu bytes/message)\n",
      operation, messages_per_second, megabytes_per_second,
      (unsigned long)bytes_per_message);
}

int main(void) {
   char header[201];
   float complex samples[5000];
   BenchPayload message;
   size_t iterations = 200;
   const char *iterations_env = getenv("SDL_BENCH_ITERATIONS");
   size_t wire_size = 0;
   uint8_t *wire;
   size_t i;
   volatile uint64_t checksum = 0;
   clock_t start;
   clock_t end;

   memset(header, 'H', sizeof(header) - 1);
   header[sizeof(header) - 1] = '\0';
   for (i = 0; i < sizeof(samples) / sizeof(samples[0]); ++i) {
      float parts[2];
      parts[0] = (float)i * 0.25f;
      parts[1] = -(float)(i % 97) * 0.5f;
      memcpy(&samples[i], parts, sizeof(parts));
   }

   if (iterations_env != NULL) {
      unsigned long long parsed = strtoull(iterations_env, NULL, 10);
      if (parsed != 0 && parsed <= SIZE_MAX)
         iterations = (size_t)parsed;
   }

   message.header = header;
   message.samples_count = (uint32_t)(sizeof(samples) / sizeof(samples[0]));
   message.samples = samples;
   register_all_types();

   wire = (uint8_t *)type_encode("BenchPayload", &message, &wire_size);
   assert(wire != NULL && wire_size != 0);

   start = clock();
   for (i = 0; i < iterations; ++i) {
      size_t encoded_size = 0;
      void *encoded = type_encode("BenchPayload", &message, &encoded_size);
      assert(encoded != NULL && encoded_size == wire_size);
      checksum += encoded_size;
      type_free(encoded);
   }
   end = clock();
   report_rate("encode", iterations, wire_size, elapsed_seconds(start, end));

   start = clock();
   for (i = 0; i < iterations; ++i) {
      size_t decoded_size = wire_size;
      BenchPayload *decoded = (BenchPayload *)type_decode(wire, &decoded_size);
      assert(decoded != NULL && decoded->samples_count == 5000);
      checksum += (uint64_t)crealf(decoded->samples[4999]);
      type_free(decoded);
   }
   end = clock();
   report_rate("decode", iterations, wire_size, elapsed_seconds(start, end));

   printf("iterations: %lu, checksum: %llu\n", (unsigned long)iterations,
      (unsigned long long)checksum);
   type_free(wire);
   return 0;
}
