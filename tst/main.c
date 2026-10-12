#include "type_engine.h"
#include "sdl_registry.h"
#include "wire_example.h"
#include "codec_cases.h"
#include "schema.h"
#include "empty_message.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static const unsigned char fixture[] = {1,   1,   1,   255, 254, 5,  195, 169,
                                        116, 195, 169, 2,   1,   44, 255, 255};
static SdlContext *context(const char *name) {
   const char *names[] = {name};
   size_t size;
   char *d = type_description(names, 1, &size);
   SdlContext *ctx;
   assert(d);
   if (!strcmp(name, "Packet")) {
      char expected[132];
      FILE *file = fopen("tst/fixtures/packet.sdl2", "rb");
      assert(file && fread(expected, 1, sizeof(expected), file) == sizeof(expected));
      assert(fgetc(file) == EOF);
      fclose(file);
      assert(size == sizeof(expected) && !memcmp(d, expected, size));
   }
   ctx = type_prepare(d, size);
   type_free(d);
   assert(ctx);
   return ctx;
}
static void packet_cases(void) {
   SdlContext *ctx = context("Packet");
   int16_t samples[] = {300, -1};
   Packet m = {0}, *copy;
   size_t n;
   void *data;
   SdlDynamicMessage *dynamic;
   m.active = true;
   m.has_code = true;
   m.code = -2;
   m.label = "\xc3\xa9t\xc3\xa9";
   m.samples = samples;
   m.samples_count = 2;
   data = type_encode(ctx, "Packet", &m, &n);
   assert(data && n == sizeof(fixture) && !memcmp(data, fixture, n));
   assert(!strcmp(type_message_name(ctx, data, n), "Packet"));
   copy = type_decode(ctx, data, n);
   assert(copy && copy->code == -2 && copy->samples_count == 2 && copy->samples[0] == 300);
   assert(copy->sdl_string_length_3 == 5 && !memcmp(copy->label, m.label, 5));
   type_free(copy);
   dynamic = type_decode_dynamic(ctx, data, n);
   assert(dynamic);
   assert(type_dynamic_get(dynamic, "samples")->value.array.count == 2);
   type_dynamic_free(dynamic);
   {
      size_t i;
      for (i = 0; i < n; ++i)
         assert(!type_decode(ctx, data, i));
   }
   {
      unsigned char bad[32];
      memcpy(bad, fixture, n);
      bad[n] = 0;
      assert(!type_decode(ctx, bad, n + 1));
      bad[1] = 2;
      assert(!type_decode(ctx, bad, n));
      memcpy(bad, fixture, n);
      bad[2] = 2;
      assert(!type_decode(ctx, bad, n));
   }
   type_free(data);
   m.has_code = false;
   m.label = "a\0b";
   m.sdl_string_length_3 = 3;
   m.samples_count = 0;
   m.samples = NULL;
   data = type_encode(ctx, "Packet", &m, &n);
   copy = type_decode(ctx, data, n);
   assert(copy && !copy->has_code && copy->sdl_string_length_3 == 3 &&
          !memcmp(copy->label, "a\0b", 3));
   type_free(copy);
   type_free(data);
   m.samples_count = 1;
   assert(!type_encode(ctx, "Packet", &m, &n));
   type_context_free(ctx);
}
static void evolution(void) {
   const char text[] =
       "SDL2\nmessage Packet {\n  1: required bool enabled;\n  2: optional int16 code;\n  3: "
       "required string label;\n  4: repeated int16 samples;\n  5: required string extra;\n}\n";
   SdlContext *ctx = type_prepare(text, strlen(text));
   unsigned char data[20];
   Packet *p;
   assert(ctx);
   memcpy(data, fixture, 16);
   data[16] = 2;
   data[17] = 'o';
   data[18] = 'k';
   p = type_decode(ctx, data, 19);
   assert(p && p->active && p->samples_count == 2);
   type_free(p);
   data[18] = 255;
   assert(!type_decode(ctx, data, 19));
   type_context_free(ctx);
   {
      const char removed[] = "SDL2\nmessage Packet {\n  1: required bool active;\n}\n";
      unsigned char small[] = {1, 1};
      ctx = type_prepare(removed, strlen(removed));
      assert(ctx);
      p = type_decode(ctx, small, 2);
      assert(p && p->active && !p->has_code && !p->samples_count);
      type_free(p);
      type_context_free(ctx);
   }
   {
      const char incompatible[] = "SDL2\nmessage Packet {\n  1: required int32 active;\n}\n";
      assert(!type_prepare(incompatible, strlen(incompatible)));
   }
   {
      const char nested[] =
          "SDL2\nmessage FixedItem {\n  1: required fl32 renamed_x;\n  2: required fl32 y;\n  3: "
          "required int32 extra;\n}\nmessage RootPayload {\n  1: optional string header;\n  2: "
          "packed FixedItem fixed_array;\n  3: repeated VarItem var_array;\n}\nmessage VarItem {\n "
          " 1: optional string name;\n  2: required int64 id;\n}\n";
      unsigned char body[] = {2, 0, 1, 0x3f, 0x80, 0, 0, 0x40, 0, 0, 0, 0, 0, 0, 7, 0};
      RootPayload *root;
      ctx = type_prepare(nested, strlen(nested));
      assert(ctx);
      root = type_decode(ctx, body, sizeof(body));
      assert(root && root->fixed_array_count == 1 && root->fixed_array[0].x == 1.0f &&
             root->fixed_array[0].y == 2.0f);
      type_free(root);
      type_context_free(ctx);
   }
}
static void composites(void) {
   SdlContext *ctx = context("CodecCases");
   CodecCases m = {0}, *p;
   size_t n;
   void *data;
   int16_t xs[] = {-32768, 32767};
   bool flags[] = {true, false};
   char *labels[] = {"", "x"};
   int32_t enums[] = {STATE_READY, STATE_NEGATIVE};
   m.has_tiny = true;
   m.tiny = -128;
   m.has_small = true;
   m.small = 32767;
   m.has_signed_value = true;
   m.signed_value = INT32_MIN;
   m.has_wide = true;
   m.wide = INT64_MIN;
   m.has_precise = true;
   m.precise = -0.0;
   m.has_ratio = true;
   m.ratio = INFINITY;
   m.has_state = true;
   m.state = STATE_MINIMUM;
   m.samples = xs;
   m.samples_count = 2;
   m.labels = (const char **)labels;
   m.labels_count = 2;
   m.required_enabled = true;
   m.bool_flags = flags;
   m.bool_flags_count = 2;
   m.fixed_states[0] = STATE_UNKNOWN;
   m.fixed_states[1] = STATE_READY;
   m.fixed_states[2] = STATE_NEGATIVE;
   m.packed_states = enums;
   m.packed_states_count = 2;
   data = type_encode(ctx, "CodecCases", &m, &n);
   assert(data);
   p = type_decode(ctx, data, n);
   assert(p && p->wide == INT64_MIN && p->tiny == -128 && signbit(p->precise) && isinf(p->ratio));
   assert(p->labels_count == 2 && p->sdl_string_length_14[0] == 0 && !strcmp(p->labels[1], "x"));
   assert(p->packed_states[1] == STATE_NEGATIVE);
   type_free(p);
   p = type_clone("CodecCases", &m);
   assert(p && p->samples != m.samples && p->samples[0] == -32768);
   type_free(p);
   {
      char *text = type_display("CodecCases", &m, 2);
      assert(text);
      type_free(text);
   }
   type_free(data);
   m.state = 99;
   assert(!type_encode(ctx, "CodecCases", &m, &n));
   type_context_free(ctx);
   {
      SdlContext *empty = context("EmptyMessage");
      EmptyMessage e = {0};
      data = type_encode(empty, "EmptyMessage", &e, &n);
      assert(data && n == 1);
      p = type_decode(empty, data, n);
      assert(p);
      type_free(p);
      type_free(data);
      type_context_free(empty);
   }
}
static void graph_limits(void) {
   char text[16384];
   unsigned count, shared;
   for (shared = 0; shared < 2; ++shared) {
      size_t used = 5;
      SdlContext *ctx;
      memcpy(text, "SDL2\n", 5);
      for (count = 0; count < (shared ? 30u : 66u); ++count) {
         used += (size_t)sprintf(text + used, "message N%03u {\n", count);
         if (!count)
            used += (size_t)sprintf(text + used, "  1: required int8 value;\n");
         else {
            used += (size_t)sprintf(text + used, "  1: optional N%03u left;\n", count - 1);
            if (shared)
               used += (size_t)sprintf(text + used, "  2: optional N%03u right;\n", count - 1);
         }
         used += (size_t)sprintf(text + used, "}\n");
      }
      ctx = type_prepare(text, used);
      assert(shared ? ctx != NULL : ctx == NULL);
      type_context_free(ctx);
   }
}
static void invalid(void) {
   const char *bad[] = {"SDL1\n",
                        "SDL2\nmessage A {\n  1: required A a;\n}\n",
                        "SDL2\nmessage A {\n  1: required Missing a;\n}\n",
                        "SDL2\nmessage A {\n  1: required bool a;\n  1: required bool b;\n}\n",
                        "SDL2\nmessage A {\n  1: packed string a;\n}\n",
                        "SDL2\nenum E {\n}\nmessage A {\n}\n"};
   size_t i;
   for (i = 0; i < sizeof(bad) / sizeof(bad[0]); ++i)
      assert(!type_prepare(bad[i], strlen(bad[i])));
   {
      SdlContext *ctx = context("Packet");
      unsigned char badid[] = {0x81, 0};
      unsigned char over[] = {255, 255, 255, 255, 16};
      assert(!type_decode(ctx, badid, sizeof(badid)));
      assert(!type_decode(ctx, over, sizeof(over)));
      type_context_free(ctx);
   }
}
int main(void) {
   register_all_types();
   packet_cases();
   evolution();
   composites();
   invalid();
   graph_limits();
   puts("C SDL2 codec, evolution, malformed input and storage checks passed.");
   return 0;
}
