#include "type_engine.h"
#include "type_private.h"
#include "type_registry.h"
#include "sdl_wire.h"
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
/* Mixed widths and native padding exercise the non-contiguous span path. */
typedef struct {
   bool enabled;
   int16_t code;
   double precise;
   State state;
   float complex point;
} PackedTestRecord;
typedef struct { char prefix; PackedTestRecord value; } PackedTestRecordAlign;
typedef struct { uint32_t count; PackedTestRecord *records; } PackedTestBatch;
typedef struct { char prefix; PackedTestBatch value; } PackedTestBatchAlign;
static const SdlFieldDesc packed_test_record_fields[] = {
   {1, "enabled", &SDL_BOOL_DESC, offsetof(PackedTestRecord, enabled), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
   {2, "code", &SDL_INT16_DESC, offsetof(PackedTestRecord, code), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
   {3, "precise", &SDL_FLOAT64_DESC, offsetof(PackedTestRecord, precise), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
   {4, "state", &SDL_ENUM_STATE_DESC, offsetof(PackedTestRecord, state), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
   {5, "point", &SDL_COMPLEX32_DESC, offsetof(PackedTestRecord, point), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
};
static const SdlTypeDesc packed_test_record_desc = {
   .kind = SDL_TYPE_STRUCT, .size = sizeof(PackedTestRecord),
   .alignment = offsetof(PackedTestRecordAlign, value), .name = "PackedTestRecord",
   .detail.structure = {5, packed_test_record_fields}
};
static const SdlFieldDesc packed_test_batch_fields[] = {
   {1, "records", &packed_test_record_desc, offsetof(PackedTestBatch, records), SDL_NO_OFFSET,
    offsetof(PackedTestBatch, count), SDL_NO_OFFSET, SDL_FIELD_REPEATED | SDL_FIELD_PACKED}
};
static const SdlTypeDesc packed_test_batch_desc = {
   .kind = SDL_TYPE_STRUCT, .size = sizeof(PackedTestBatch),
   .alignment = offsetof(PackedTestBatchAlign, value), .name = "PackedTestBatch",
   .detail.structure = {1, packed_test_batch_fields}
};
static void packed_records(void) {
   static const char text[] =
      "SDL2\nenum State {\n  READY = 1;\n  NEGATIVE = -7;\n}\n"
      "message PackedTestBatch {\n  1: packed PackedTestRecord records;\n}\n"
      "message PackedTestRecord {\n  1: required bool enabled;\n  2: required int16 code;\n"
      "  3: required fl64 precise;\n  4: required State state;\n  5: required c32 point;\n}\n";
   PackedTestRecord records[2] = {{0}};
   PackedTestBatch batch = {2, records}, *copy;
   unsigned char expected[48] = {1, 2};
   void *wire;
   size_t size, i;
   SdlContext *ctx;
   assert(sdl_register_type(&packed_test_record_desc));
   assert(sdl_register_type(&packed_test_batch_desc));
   ctx = type_prepare(text, strlen(text));
   assert(ctx);
   records[0].enabled = true;
   records[0].code = INT16_MIN;
   records[0].precise = -0.0;
   records[0].state = STATE_NEGATIVE;
   records[0].point = 1.f - 2.f * I;
   records[1].code = INT16_MAX;
   records[1].precise = INFINITY;
   records[1].state = STATE_READY;
   records[1].point = -3.f + 4.f * I;
   for (i = 0; i < 2; ++i)
      assert(sdl_value_encode_fixed(&packed_test_record_desc, &records[i], expected + 2 + i * 23, 23));
   wire = type_encode(ctx, "PackedTestBatch", &batch, &size);
   assert(wire && size == sizeof(expected) && !memcmp(wire, expected, size));
   copy = type_decode(ctx, wire, size);
   assert(copy && copy->count == 2 && copy->records[0].enabled && !copy->records[1].enabled);
   assert(copy->records[0].code == INT16_MIN && copy->records[1].code == INT16_MAX);
   assert(signbit(copy->records[0].precise) && isinf(copy->records[1].precise));
   assert(copy->records[0].state == STATE_NEGATIVE && copy->records[1].state == STATE_READY);
   assert(copy->records[0].point == records[0].point && copy->records[1].point == records[1].point);
   type_free(copy);
   for (i = 0; i < size; ++i) {
      assert(!type_decode_size(ctx, wire, i));
      assert(!type_decode(ctx, wire, i));
   }
   expected[2] = 2;
   assert(!type_decode(ctx, expected, sizeof(expected)));
   expected[2] = 1;
   sdl_wire_write_u32(expected + 13, 123);
   assert(!type_decode(ctx, expected, sizeof(expected)));
   /* Unknown fields validate against the remote enum, not the local subset. */
   {
      char remote[1024];
      const char *closing = strstr(text, "}\nmessage");
      SdlContext *changed;
      assert(closing);
      snprintf(remote, sizeof(remote), "%.*s  OTHER = 123;\n%s",
               (int)(closing - text), text, closing);
      changed = type_prepare(remote, strlen(remote));
      assert(changed && !type_decode(changed, expected, sizeof(expected)));
      type_context_free(changed);
      strstr(remote, "  1: packed")[2] = '2';
      changed = type_prepare(remote, strlen(remote));
      assert(changed);
      copy = type_decode(changed, expected, sizeof(expected));
      assert(copy && copy->count == 0 && !copy->records);
      type_free(copy);
      type_context_free(changed);
   }
   records[0].state = 123;
   assert(!type_encode_size(ctx, "PackedTestBatch", &batch));
   assert(!type_encode(ctx, "PackedTestBatch", &batch, &size));
   records[0].state = STATE_NEGATIVE;
   batch.count = 0;
   batch.records = NULL;
   {
      void *empty = type_encode(ctx, "PackedTestBatch", &batch, &size);
      assert(empty && size == 2 && !memcmp(empty, "\1\0", 2));
      copy = type_decode(ctx, empty, size);
      assert(copy && copy->count == 0);
      type_free(copy);
      type_free(empty);
   }
   /* Added remote fields disable the plan and retain schema evolution. */
   {
      char remote[1024];
      unsigned char evolved[50] = {1, 2};
      SdlContext *changed;
      memcpy(remote, text, strlen(text) - 2);
      strcpy(remote + strlen(text) - 2, "  6: required int8 extra;\n}\n");
      changed = type_prepare(remote, strlen(remote));
      assert(changed);
      for (i = 0; i < 2; ++i) {
         memcpy(evolved + 2 + i * 24, (const unsigned char *)wire + 2 + i * 23, 23);
         evolved[2 + i * 24 + 23] = 7;
      }
      copy = type_decode(changed, evolved, sizeof(evolved));
      assert(copy && copy->count == 2 && copy->records[0].state == STATE_NEGATIVE &&
             copy->records[1].point == records[1].point);
      type_free(copy);
      assert(!type_encode_size(changed, "PackedTestBatch", &batch));
      type_context_free(changed);
   }
   type_free(wire);
   type_context_free(ctx);
   /* Nested fixed arrays use several spans with different component widths. */
   {
      FixedRow rows[2] = {0};
      FixedRowBatch nested = {2, rows}, *result;
      unsigned char reference[82];
      ctx = context("FixedRowBatch");
      rows[0].vectors[0].coords[0] = -0.0f;
      rows[0].vectors[1].coords[1] = INFINITY;
      rows[1].vectors[1].grid[1][2] = INT16_MIN;
      wire = type_encode(ctx, "FixedRowBatch", &nested, &size);
      assert(wire && size == sizeof(reference));
      memcpy(reference, wire, 2);
      for (i = 0; i < 2; ++i)
         assert(sdl_value_encode_fixed(&FIXEDROW_DESC, &rows[i], reference + 2 + 40 * i, 40));
      assert(!memcmp(reference, wire, size));
      result = type_decode(ctx, wire, size);
      assert(result && result->rows_count == 2 && !memcmp(result->rows, rows, sizeof(rows)));
      type_free(result);
      type_free(wire);
      type_context_free(ctx);
   }
}

static void wire_word_arrays(void) {
   const size_t widths[] = {1, 2, 3, 4, 8, 16};
   const size_t counts[] = {0, 1, 2, 3, 7, 15, 16, 17, 64, 1000};
   unsigned char source[16005], actual[16005], expected[16005];
   uint32_t state = 123456789;
   size_t w, c, i;
   for (i = 0; i < sizeof(source); ++i) {
      state ^= state << 13; state ^= state >> 17; state ^= state << 5;
      source[i] = (unsigned char)state;
   }
   for (w = 0; w < sizeof(widths) / sizeof(widths[0]); ++w)
      for (c = 0; c < sizeof(counts) / sizeof(counts[0]); ++c) {
         size_t width = widths[w], count = counts[c];
         memset(actual, 0xa5, sizeof(actual));
         memset(expected, 0xa5, sizeof(expected));
         /* Both buffers are deliberately unaligned; canaries detect overruns. */
         for (i = 0; i < count; ++i)
            sdl_wire_encode_native(expected + 3 + i * width, source + 1 + i * width, width);
         sdl_wire_convert_array(actual + 3, source + 1, width, count);
         assert(!memcmp(actual, expected, sizeof(actual)));
         sdl_wire_convert_array(NULL, NULL, width, 0);
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
   packed_records();
   wire_word_arrays();
   invalid();
   graph_limits();
   puts("C SDL2 codec, evolution, malformed input and storage checks passed.");
   return 0;
}
