#include "benchmark.h"
#include "sdl_registry.h"
#include "type_engine.h"

#include <complex.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double elapsed_seconds(clock_t start, clock_t end) {
   return (double)(end - start) / (double)CLOCKS_PER_SEC;
}

static size_t read_iterations(const char *value, size_t fallback) {
   size_t parsed = 0;
   if (value == NULL || *value == '\0') return fallback;
   while (*value != '\0') {
      size_t digit;
      if (*value < '0' || *value > '9') return fallback;
      digit = (size_t)(*value - '0');
      if (parsed > (SIZE_MAX - digit) / 10) return fallback;
      parsed = parsed * 10 + digit;
      ++value;
   }
   return parsed == 0 ? fallback : parsed;
}

static void report_rate(const char *operation, size_t iterations,
   size_t bytes_per_message, double seconds) {
   double messages_per_second;
   double megabytes_per_second;
   if (seconds <= 0.0) {
      printf("%-6s below timer resolution\n", operation);
      return;
   }
   messages_per_second = (double)iterations / seconds;
   megabytes_per_second = messages_per_second * (double)bytes_per_message /
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

   iterations = read_iterations(iterations_env, iterations);

   message.header = header;
   message.samples_count = (uint32_t)(sizeof(samples) / sizeof(samples[0]));
   message.samples = samples;
   register_all_types();

   wire = (uint8_t *)type_encode("BenchPayload", &message, &wire_size);
   if (wire == NULL || wire_size == 0) {
      fprintf(stderr, "Could not encode benchmark message.\n");
      type_free(wire);
      return 1;
   }
   if (wire_size != 40224 + BENCHPAYLOAD_SCHEMA_DESCRIPTOR_SIZE) {
      fprintf(stderr, "Unexpected benchmark frame size: %lu bytes.\n",
         (unsigned long)wire_size);
      type_free(wire);
      return 1;
   }

   start = clock();
   for (i = 0; i < iterations; ++i) {
      size_t encoded_size = 0;
      void *encoded = type_encode("BenchPayload", &message, &encoded_size);
      if (encoded == NULL || encoded_size != wire_size) {
         fprintf(stderr, "Benchmark encoding failed or changed frame size.\n");
         type_free(encoded);
         type_free(wire);
         return 1;
      }
      type_free(encoded);
   }
   end = clock();
   report_rate("encode", iterations, wire_size, elapsed_seconds(start, end));

   start = clock();
   for (i = 0; i < iterations; ++i) {
      size_t decoded_size = wire_size;
      BenchPayload *decoded = (BenchPayload *)type_decode(wire, &decoded_size);
      if (decoded == NULL) {
         fprintf(stderr, "Benchmark decoding failed.\n");
         type_free(wire);
         return 1;
      }
      type_free(decoded);
   }
   end = clock();
   report_rate("decode", iterations, wire_size, elapsed_seconds(start, end));

   printf("iterations: %lu\n", (unsigned long)iterations);
   type_free(wire);
   return 0;
}
