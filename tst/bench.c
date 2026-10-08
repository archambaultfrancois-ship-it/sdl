#define _POSIX_C_SOURCE 200809L
#include "type_engine.h"
#include "sdl_registry.h"
#include "benchmark.h"
#include "bench_cases.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static double now(void) {
   struct timespec t;
   clock_gettime(CLOCK_MONOTONIC, &t);
   return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}
static size_t iterations(void) {
   const char *s = getenv("SDL_BENCH_ITERATIONS");
   char *end;
   unsigned long n;
   size_t i;
   if (!s || !*s)
      return 200;
   for (i = 0; s[i]; ++i)
      if (s[i] < '0' || s[i] > '9')
         return 200;
   n = strtoul(s, &end, 10);
   return *end || !n ? 200 : (size_t)n;
}
static void bench(const char *label, const char *name, const void *value, size_t useful) {
   const char *types[] = {name};
   size_t ds, n, i, min_iterations = iterations();
   char *text = type_description(types, 1, &ds);
   double start = now(), preparation;
   SdlContext *ctx;
   void *wire, *copy;
   assert(text);
   ctx = type_prepare(text, ds);
   preparation = (now() - start) * 1e6;
   assert(ctx);
   wire = type_encode(ctx, name, value, &n);
   assert(wire);
   copy = type_decode(ctx, wire, n);
   assert(copy);
   type_free(copy);
   for (i = 0; i < 100; ++i) {
      size_t sz;
      copy = type_encode(ctx, name, value, &sz);
      assert(copy && sz == n);
      type_free(copy);
      copy = type_decode(ctx, wire, n);
      assert(copy);
      type_free(copy);
   }
   printf("%s metadata: %lu description bytes, %.1f us prepare\n", label, (unsigned long)ds,
          preparation);
   for (i = 0; i < 2; ++i) {
      double rates[3];
      size_t trial;
      for (trial = 0; trial < 3; ++trial) {
         size_t count = 0;
         start = now();
         do {
            size_t sz;
            if (i == 0)
               copy = type_encode(ctx, name, value, &sz);
            else
               copy = type_decode(ctx, wire, n);
            assert(copy);
            type_free(copy);
            ++count;
         } while (count < min_iterations || now() - start < 0.1);
         rates[trial] = (double)count / (now() - start);
      }
      {
         double temp;
         if (rates[0] > rates[1]) {
            temp = rates[0];
            rates[0] = rates[1];
            rates[1] = temp;
         }
         if (rates[1] > rates[2]) {
            temp = rates[1];
            rates[1] = rates[2];
            rates[2] = temp;
         }
         if (rates[0] > rates[1]) {
            temp = rates[0];
            rates[0] = rates[1];
            rates[1] = temp;
         }
      }
      printf("%s %s %.1f msg/s %.4f MiB/s (%lu bytes/message)\n", label,
             i == 0 ? "encode" : "decode", rates[1], rates[1] * (double)useful / 1048576.,
             (unsigned long)n);
   }
   type_free(wire);
   type_free(text);
   type_context_free(ctx);
}
int main(void) {
   BenchSmall small = {0};
   BenchOptionals optional = {0};
   BenchVariable variable = {0};
   BenchEntry entries[8];
   char labels[8][16], vh[41], header[201];
   BenchPayload packed = {0};
   float complex samples[5000];
   size_t i;
   register_all_types();
   small.active = true;
   small.sequence = 123;
   small.code = -2;
   bench("small", "BenchSmall", &small, 7);
   optional.has_a = true;
   optional.a = 1;
   bench("optional_sparse", "BenchOptionals", &optional, 4);
   optional.has_b = optional.has_c = optional.has_d = optional.has_e = optional.has_f =
       optional.has_g = optional.has_h = true;
   optional.b = 2;
   optional.c = 3;
   optional.d = 4;
   optional.e = 5;
   optional.f = 6;
   optional.g = 7;
   optional.h = 8;
   bench("optional_dense", "BenchOptionals", &optional, 32);
   memset(entries, 0, sizeof(entries));
   memset(vh, 'V', 40);
   vh[40] = 0;
   variable.header = vh;
   variable.entries_count = 8;
   variable.entries = entries;
   for (i = 0; i < 8; ++i) {
      snprintf(labels[i], sizeof(labels[i]), "entry%lu", (unsigned long)i);
      entries[i].has_label = true;
      entries[i].label = labels[i];
      entries[i].number = (int64_t)i;
   }
   bench("variable", "BenchVariable", &variable, 152);
   memset(header, 'H', 200);
   header[200] = 0;
   packed.header = header;
   packed.samples_count = 5000;
   packed.samples = samples;
   for (i = 0; i < 5000; ++i)
      samples[i] = (float)i * .25f + I * (-(float)(i % 97) * .5f);
   bench("packed", "BenchPayload", &packed, 40200);
   return 0;
}
