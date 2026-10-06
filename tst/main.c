/* ============================================================================
   ADVANCED HYBRID ARRAYS BUILT-IN TEST SUITE
   ============================================================================ */

#include "type_engine.h"
#include "schema.h"
#include "codec_cases.h"
#include "empty_message.h"
#include "sdl_registry.h"
#include "sdl_wire.h"
#include <assert.h>
#include <complex.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t *reverse_wire_fields(const uint8_t *wire, size_t wire_size) {
   size_t descriptor_size;
   size_t body_offset;
   size_t offset;
   size_t count = 0;
   size_t index;
   size_t *starts;
   size_t *ends;
   uint8_t *result;
   if (wire_size < 8) return NULL;
   descriptor_size = sdl_wire_read_u32(wire);
   if (descriptor_size > wire_size - 8) return NULL;
   body_offset = 8 + descriptor_size;
   starts = (size_t *)malloc((wire_size / 8 + 1) * sizeof(size_t));
   ends = (size_t *)malloc((wire_size / 8 + 1) * sizeof(size_t));
   result = (uint8_t *)malloc(wire_size);
   if (starts == NULL || ends == NULL || result == NULL) {
      free(starts);
      free(ends);
      free(result);
      return NULL;
   }
   offset = body_offset;
   while (offset < wire_size) {
      uint32_t field_id;
      size_t length;
      if (wire_size - offset < 8) goto error;
      field_id = sdl_wire_read_u32(wire + offset);
      length = sdl_wire_read_u32(wire + offset + 4);
      if (length > wire_size - offset - 8) goto error;
      if (count > 0 &&
          sdl_wire_read_u32(wire + starts[count - 1]) == field_id) {
         ends[count - 1] = offset + 8 + length;
      } else {
         starts[count] = offset;
         ends[count] = offset + 8 + length;
         ++count;
      }
      offset = offset + 8 + length;
   }
   memcpy(result, wire, body_offset);
   offset = body_offset;
   for (index = count; index > 0; --index) {
      size_t length = ends[index - 1] - starts[index - 1];
      memcpy(result + offset, wire + starts[index - 1], length);
      offset += length;
   }
   free(starts);
   free(ends);
   return result;
error:
   free(starts);
   free(ends);
   free(result);
   return NULL;
}

static void test_float_subnormal_round_trip(void) {
   CodecCases input;
   CodecCases *decoded;
   uint32_t f32_bits = 1;
   uint64_t f64_bits = 1;
   float f32_subnormal;
   double f64_subnormal;
   float point_parts[2];
   double position_parts[2];
   float decoded_point_parts[2];
   double decoded_position_parts[2];
   uint8_t *wire;
   size_t wire_size = 0;
   size_t decoded_size;
   memcpy(&f32_subnormal, &f32_bits, sizeof(f32_subnormal));
   memcpy(&f64_subnormal, &f64_bits, sizeof(f64_subnormal));
   point_parts[0] = -f32_subnormal;
   point_parts[1] = -0.0f;
   position_parts[0] = f64_subnormal;
   position_parts[1] = -0.0;
   memset(&input, 0, sizeof(input));
   input.has_ratio = true;
   input.ratio = f32_subnormal;
   input.has_precise = true;
   input.precise = f64_subnormal;
   input.has_point = true;
   memcpy(&input.point, point_parts, sizeof(input.point));
   input.has_position = true;
   memcpy(&input.position, position_parts, sizeof(input.position));
   wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
   assert(wire != NULL);
   decoded_size = wire_size;
   decoded = (CodecCases *)type_decode(wire, &decoded_size);
   assert(decoded != NULL);
   assert(memcmp(&decoded->ratio, &f32_subnormal, sizeof(f32_subnormal)) == 0);
   assert(memcmp(&decoded->precise, &f64_subnormal, sizeof(f64_subnormal)) == 0);
   memcpy(decoded_point_parts, &decoded->point, sizeof(decoded_point_parts));
   memcpy(decoded_position_parts, &decoded->position,
      sizeof(decoded_position_parts));
   assert(memcmp(decoded_point_parts, point_parts, sizeof(point_parts)) == 0);
   assert(memcmp(decoded_position_parts, position_parts,
      sizeof(position_parts)) == 0);
   type_free(decoded);
   type_free(wire);
   printf(" [S22] Float subnormals and signed zero round-trip... OK\n");
}

static void test_extended_usage_cases(void) {
   CodecCases input;
   CodecCases scalar_input;
   CodecCases *decoded;
   RootPayload root;
   RootPayload *decoded_root;
   uint8_t *wire;
   uint8_t *reordered;
   uint8_t *malformed;
   uint8_t *scalar_wire;
   size_t wire_size = 0;
   size_t scalar_wire_size = 0;
   size_t malformed_size;
   size_t decoded_size;
   size_t descriptor_size;
   size_t body_offset;
   size_t first_length;
   size_t cut;
   char text[2048];
   float complex_parts[2] = { -INFINITY, NAN };
   double complex64_parts[2] = { INFINITY, -INFINITY };
   const char *repeat = "SDL-é-📦-";
   size_t repeat_size = strlen(repeat);
   size_t repeat_index;

   memset(&input, 0, sizeof(input));
   input.has_ratio = true;
   input.ratio = INFINITY;
   input.has_precise = true;
   input.precise = NAN;
   input.has_point = true;
   memcpy(&input.point, complex_parts, sizeof(input.point));
   input.has_position = true;
   memcpy(&input.position, complex64_parts, sizeof(input.position));
   wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
   assert(wire != NULL);
   reordered = reverse_wire_fields(wire, wire_size);
   assert(reordered != NULL);
   decoded_size = wire_size;
   decoded = (CodecCases *)type_decode(reordered, &decoded_size);
   assert(decoded != NULL);
   assert(decoded_size == wire_size);
   assert(isinf(decoded->ratio) && decoded->ratio > 0.0f);
   assert(isnan(decoded->precise));
   assert(isinf(crealf(decoded->point)) && crealf(decoded->point) < 0.0f);
   assert(isnan(cimagf(decoded->point)));
   assert(isinf(creal(decoded->position)) && creal(decoded->position) > 0.0);
   assert(isinf(cimag(decoded->position)) && cimag(decoded->position) < 0.0);
   type_free(decoded);
   type_free(reordered);
   type_free(wire);
   printf(" [S01] Reordered wire fields decode correctly... OK\n");

   for (repeat_index = 0; repeat_index < 128; ++repeat_index)
      memcpy(text + repeat_index * repeat_size, repeat, repeat_size);
   text[128 * repeat_size] = '\0';
   memset(&root, 0, sizeof(root));
   root.has_header = true;
   root.header = text;
   wire_size = 0;
   wire = (uint8_t *)type_encode("RootPayload", &root, &wire_size);
   assert(wire != NULL);
   decoded_size = wire_size;
   decoded_root = (RootPayload *)type_decode(wire, &decoded_size);
   assert(decoded_root != NULL);
   assert(strcmp(decoded_root->header, text) == 0);
   type_free(decoded_root);
   printf(" [S02] UTF-8 strings and float edge values round-trip... OK\n");

   descriptor_size = sdl_wire_read_u32(wire);
   body_offset = 8 + descriptor_size;
   first_length = sdl_wire_read_u32(wire + body_offset + 4);
   for (cut = body_offset + 1; cut < body_offset + 8 + first_length; ++cut)
      assert(type_decode_size(wire, cut) == 0);

   malformed = (uint8_t *)malloc(wire_size);
   assert(malformed != NULL);
   memcpy(malformed, wire, wire_size);
   sdl_wire_write_u32(malformed, UINT32_MAX);
   assert(type_decode_size(malformed, wire_size) == 0);
   assert(type_decode(malformed, &wire_size) == NULL);
   assert(type_decode_dynamic(malformed, wire_size) == NULL);
   sdl_wire_write_u32(malformed, 1024U * 1024U + 1U);
   assert(type_decode_dynamic(malformed, wire_size) == NULL);
   memcpy(malformed, wire, wire_size);
   sdl_wire_write_u32(malformed + body_offset + 4, UINT32_MAX);
   assert(type_decode_size(malformed, wire_size) == 0);
   decoded_size = wire_size;
   assert(type_decode(malformed, &decoded_size) == NULL);
   assert(type_decode_dynamic(malformed, wire_size) == NULL);
   assert(type_decode_size(wire, 7) == 0);
   assert(type_decode_dynamic(wire, 7) == NULL);

   memset(&scalar_input, 0, sizeof(scalar_input));
   scalar_wire = (uint8_t *)type_encode("CodecCases", &scalar_input,
      &scalar_wire_size);
   assert(scalar_wire != NULL);
   malformed_size = scalar_wire_size + 8 + 3;
   free(malformed);
   malformed = (uint8_t *)malloc(malformed_size);
   assert(malformed != NULL);
   memcpy(malformed, scalar_wire, scalar_wire_size);
   sdl_wire_write_u32(malformed + scalar_wire_size, 11);
   sdl_wire_write_u32(malformed + scalar_wire_size + 4, 3);
   malformed[scalar_wire_size + 8] = 1;
   malformed[scalar_wire_size + 9] = 2;
   malformed[scalar_wire_size + 10] = 3;
   assert(type_decode_size(malformed, malformed_size) == 0);
   decoded_size = malformed_size;
   assert(type_decode(malformed, &decoded_size) == NULL);
   assert(type_decode_dynamic(malformed, malformed_size) == NULL);
   free(malformed);
   type_free(scalar_wire);
   type_free(wire);
   printf(" [S03] Truncation and invalid lengths are rejected... OK\n");
}

static void test_primitive_payload_widths(void) {
   static const uint32_t cases[][2] = {
      { 1, 0 }, { 1, 2 }, { 2, 1 }, { 2, 3 },
      { 3, 3 }, { 3, 5 }, { 4, 7 }, { 4, 9 },
      { 5, 3 }, { 5, 5 }, { 6, 7 }, { 6, 9 },
      { 7, 7 }, { 7, 9 }, { 8, 15 }, { 8, 17 },
      { 18, 0 }, { 18, 2 }
   };
   CodecCases input;
   uint8_t *base;
   size_t base_size = 0;
   size_t case_index;

   memset(&input, 0, sizeof(input));
   base = (uint8_t *)type_encode("CodecCases", &input, &base_size);
   assert(base != NULL);
   for (case_index = 0; case_index < sizeof(cases) / sizeof(cases[0]);
        ++case_index) {
      uint32_t field_id = cases[case_index][0];
      uint32_t invalid_length = cases[case_index][1];
      size_t malformed_size = base_size + 8 + invalid_length;
      uint8_t *malformed = (uint8_t *)malloc(malformed_size);
      size_t decoded_size = malformed_size;
      assert(malformed != NULL);
      memcpy(malformed, base, base_size);
      sdl_wire_write_u32(malformed + base_size, field_id);
      sdl_wire_write_u32(malformed + base_size + 4, invalid_length);
      memset(malformed + base_size + 8, 0, invalid_length);
      assert(type_decode_size(malformed, malformed_size) == 0);
      assert(type_decode(malformed, &decoded_size) == NULL);
      assert(type_decode_dynamic(malformed, malformed_size) == NULL);
      free(malformed);
   }
   type_free(base);
   printf(" [S25] Primitive payload widths are checked... OK\n");
}

static void test_missing_required_fields_default(void) {
   CodecCases input;
   CodecCases *decoded;
   SdlDynamicMessage *dynamic;
   uint8_t *wire;
   uint8_t *empty_body;
   size_t wire_size = 0;
   size_t empty_size;
   size_t decoded_size;
   memset(&input, 0, sizeof(input));
   wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
   assert(wire != NULL);
   empty_size = (size_t)sdl_wire_read_u32(wire) + 8;
   empty_body = (uint8_t *)malloc(empty_size);
   assert(empty_body != NULL);
   memcpy(empty_body, wire, empty_size);
   decoded_size = empty_size;
   decoded = (CodecCases *)type_decode(empty_body, &decoded_size);
   assert(decoded != NULL && decoded->required_zero == 0 &&
      !decoded->required_enabled);
   dynamic = type_decode_dynamic(empty_body, empty_size);
   assert(dynamic != NULL);
   type_dynamic_free(dynamic);
   type_free(decoded);
   free(empty_body);
   type_free(wire);
}

static void test_duplicate_field_semantics(void) {
   CodecCases input;
   CodecCases *decoded;
   SdlDynamicMessage *dynamic;
   const SdlDynamicValue *value;
   uint8_t *wire;
   uint8_t *extended;
   uint8_t *malformed;
   size_t wire_size = 0;
   size_t extended_size;
   size_t malformed_size;
   size_t decoded_size;
   int32_t replacement = 42;
   int16_t extra_sample = 1234;
   uint8_t false_value = 0;
   memset(&input, 0, sizeof(input));
   input.has_optional_enabled = true;
   input.optional_enabled = true;
   input.required_zero = 7;
   input.samples_count = 1;
   {
      int16_t first_sample = -9;
      input.samples = &first_sample;
      wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
      assert(wire != NULL);
   }
   extended_size = wire_size + 8 + sizeof(replacement) + 8 +
      sizeof(extra_sample) + 8 + sizeof(false_value);
   extended = (uint8_t *)malloc(extended_size);
   assert(extended != NULL);
   memcpy(extended, wire, wire_size);
   sdl_wire_write_u32(extended + wire_size, 11);
   sdl_wire_write_u32(extended + wire_size + 4, sizeof(replacement));
   sdl_wire_encode_native(extended + wire_size + 8, &replacement,
      sizeof(replacement));
   sdl_wire_write_u32(extended + wire_size + 12, 12);
   sdl_wire_write_u32(extended + wire_size + 16, sizeof(extra_sample));
   sdl_wire_encode_native(extended + wire_size + 20, &extra_sample,
      sizeof(extra_sample));
   sdl_wire_write_u32(extended + wire_size + 22, 18);
   sdl_wire_write_u32(extended + wire_size + 26, sizeof(false_value));
   extended[wire_size + 30] = false_value;
   decoded_size = extended_size;
   decoded = (CodecCases *)type_decode(extended, &decoded_size);
   assert(decoded != NULL);
   assert(decoded->required_zero == replacement);
   assert(decoded->has_optional_enabled && !decoded->optional_enabled);
   assert(decoded->samples_count == 2);
   assert(decoded->samples[0] == -9 && decoded->samples[1] == extra_sample);
   type_free(decoded);
   dynamic = type_decode_dynamic(extended, extended_size);
   assert(dynamic != NULL);
   value = type_dynamic_get(dynamic, "required_zero");
   assert(value != NULL && value->kind == SDL_DYNAMIC_INTEGER &&
      value->value.integer == replacement);
   value = type_dynamic_get(dynamic, "optional_enabled");
   assert(value != NULL && value->kind == SDL_DYNAMIC_BOOL &&
      !value->value.boolean);
   value = type_dynamic_get(dynamic, "samples");
   assert(value != NULL && value->kind == SDL_DYNAMIC_ARRAY &&
      value->value.array.count == 2 &&
      value->value.array.items[1].value.integer == extra_sample);
   type_dynamic_free(dynamic);

   malformed_size = wire_size + 8 + 8 + 3 + 8 + sizeof(replacement);
   malformed = (uint8_t *)malloc(malformed_size);
   assert(malformed != NULL);
   memcpy(malformed, wire, wire_size);
   sdl_wire_write_u32(malformed + wire_size, 998);
   sdl_wire_write_u32(malformed + wire_size + 4, 0);
   sdl_wire_write_u32(malformed + wire_size + 8, 11);
   sdl_wire_write_u32(malformed + wire_size + 12, 3);
   memset(malformed + wire_size + 16, 0, 3);
   sdl_wire_write_u32(malformed + wire_size + 19, 11);
   sdl_wire_write_u32(malformed + wire_size + 23, sizeof(replacement));
   sdl_wire_encode_native(malformed + wire_size + 27, &replacement,
      sizeof(replacement));
   decoded_size = malformed_size;
   assert(type_decode(malformed, &decoded_size) == NULL);
   assert(type_decode_dynamic(malformed, malformed_size) == NULL);
   free(malformed);
   free(extended);
   type_free(wire);
   printf(" [S07] Duplicate singular and repeated field semantics... OK\n");
}

static void assert_invalid_codec_cases(const CodecCases *input) {
   size_t encoded_size = 123;
   assert(type_encode_size("CodecCases", input) == 0);
   assert(type_encode("CodecCases", input, &encoded_size) == NULL);
   assert(encoded_size == 0);
   assert(type_clone("CodecCases", input) == NULL);
   assert(type_display("CodecCases", input, 3) == NULL);
}

static void test_repeated_fields_reject_null_storage(void) {
   CodecCases input;
   memset(&input, 0, sizeof(input));
   input.samples_count = 1;
   assert_invalid_codec_cases(&input);

   memset(&input, 0, sizeof(input));
   input.points_count = 1;
   assert_invalid_codec_cases(&input);

   memset(&input, 0, sizeof(input));
   input.labels_count = 1;
   assert_invalid_codec_cases(&input);
   printf(" [S16] Repeated fields reject null storage... OK\n");
}

static void test_invalid_enum_values(void) {
   CodecCases input;
   CodecCases *decoded;
   uint8_t *wire;
   uint8_t *extended;
   size_t wire_size = 0;
   size_t extended_size;
   size_t decoded_size;
   int32_t invalid_value = 99;
   memset(&input, 0, sizeof(input));
   input.has_state = true;
   input.state = (State)invalid_value;
   assert(type_encode("CodecCases", &input, &wire_size) == NULL);

   memset(&input, 0, sizeof(input));
   {
      State invalid_packed_state = (State)invalid_value;
      input.packed_states_count = 1;
      input.packed_states = &invalid_packed_state;
      assert(type_encode("CodecCases", &input, &wire_size) == NULL);
   }

   memset(&input, 0, sizeof(input));
   wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
   assert(wire != NULL);
   extended_size = wire_size + 8 + sizeof(invalid_value);
   extended = (uint8_t *)malloc(extended_size);
   assert(extended != NULL);
   memcpy(extended, wire, wire_size);
   sdl_wire_write_u32(extended + wire_size, 9);
   sdl_wire_write_u32(extended + wire_size + 4, sizeof(invalid_value));
   sdl_wire_encode_native(extended + wire_size + 8, &invalid_value,
      sizeof(invalid_value));
   decoded_size = extended_size;
   decoded = (CodecCases *)type_decode(extended, &decoded_size);
   assert(decoded == NULL);
   assert(type_decode_dynamic(extended, extended_size) == NULL);
   free(extended);

   extended_size = wire_size + 8 + sizeof(invalid_value);
   extended = (uint8_t *)malloc(extended_size);
   assert(extended != NULL);
   memcpy(extended, wire, wire_size);
   sdl_wire_write_u32(extended + wire_size, 21);
   sdl_wire_write_u32(extended + wire_size + 4, sizeof(invalid_value));
   sdl_wire_encode_native(extended + wire_size + 8, &invalid_value,
      sizeof(invalid_value));
   decoded_size = extended_size;
   assert(type_decode(extended, &decoded_size) == NULL);
   assert(type_decode_dynamic(extended, extended_size) == NULL);
   free(extended);

   extended_size = wire_size + 8 + 3;
   extended = (uint8_t *)malloc(extended_size);
   assert(extended != NULL);
   memcpy(extended, wire, wire_size);
   sdl_wire_write_u32(extended + wire_size, 21);
   sdl_wire_write_u32(extended + wire_size + 4, 3);
   memset(extended + wire_size + 8, 0, 3);
   decoded_size = extended_size;
   assert(type_decode(extended, &decoded_size) == NULL);
   assert(type_decode_dynamic(extended, extended_size) == NULL);
   free(extended);
   type_free(wire);
   printf(" [S08] Undeclared enum values are rejected... OK\n");
}

static void test_packed_fixed_message_rejects_nested_invalid_enum(void) {
   EnumRecordBatch input;
   EnumRecord invalid_record;
   EnumRecordBatch *decoded;
   SdlDynamicMessage *dynamic;
   uint8_t *wire;
   uint8_t *malformed;
   size_t wire_size = 0;
   size_t malformed_size;
   size_t decoded_size;
   memset(&input, 0, sizeof(input));
   memset(&invalid_record, 0, sizeof(invalid_record));
   invalid_record.state = (State)99;
   invalid_record.code = 42;
   input.records_count = 1;
   input.records = &invalid_record;
   assert(type_encode("EnumRecordBatch", &input, &wire_size) == NULL);

   memset(&input, 0, sizeof(input));
   wire = (uint8_t *)type_encode("EnumRecordBatch", &input, &wire_size);
   assert(wire != NULL);
   malformed_size = wire_size + 8 + 8;
   malformed = (uint8_t *)malloc(malformed_size);
   assert(malformed != NULL);
   memcpy(malformed, wire, wire_size);
   sdl_wire_write_u32(malformed + wire_size, 1);
   sdl_wire_write_u32(malformed + wire_size + 4, 8);
   sdl_wire_encode_native(malformed + wire_size + 8, &(int32_t){ 99 }, 4);
   sdl_wire_encode_native(malformed + wire_size + 12, &(int32_t){ 42 }, 4);
   decoded_size = malformed_size;
   decoded = (EnumRecordBatch *)type_decode(malformed, &decoded_size);
   assert(decoded == NULL);
   dynamic = type_decode_dynamic(malformed, malformed_size);
   assert(dynamic == NULL);
   free(malformed);
   type_free(wire);
}

static void test_fixed_enum_array_rejects_unknown_value(void) {
   CodecCases input;
   CodecCases *decoded;
   SdlDynamicMessage *dynamic;
   uint8_t *wire;
   uint8_t *malformed;
   size_t wire_size = 0;
   size_t malformed_size;
   size_t decoded_size;
   int32_t states[3] = { STATE_READY, 99, STATE_NEGATIVE };
   memset(&input, 0, sizeof(input));
   input.fixed_states[0] = STATE_READY;
   input.fixed_states[1] = (State)99;
   input.fixed_states[2] = STATE_NEGATIVE;
   assert(type_encode("CodecCases", &input, &wire_size) == NULL);
   assert(wire_size == 0);

   memset(&input, 0, sizeof(input));
   wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
   assert(wire != NULL);
   malformed_size = wire_size + 8 + sizeof(states);
   malformed = (uint8_t *)malloc(malformed_size);
   assert(malformed != NULL);
   memcpy(malformed, wire, wire_size);
   sdl_wire_write_u32(malformed + wire_size, 20);
   sdl_wire_write_u32(malformed + wire_size + 4, sizeof(states));
   sdl_wire_encode_native(malformed + wire_size + 8, states, sizeof(states));
   decoded_size = malformed_size;
   decoded = (CodecCases *)type_decode(malformed, &decoded_size);
   assert(decoded == NULL);
   dynamic = type_decode_dynamic(malformed, malformed_size);
   assert(dynamic == NULL);
   free(malformed);

   malformed_size = wire_size + 8 + 2 * sizeof(int32_t);
   malformed = (uint8_t *)malloc(malformed_size);
   assert(malformed != NULL);
   memcpy(malformed, wire, wire_size);
   sdl_wire_write_u32(malformed + wire_size, 20);
   sdl_wire_write_u32(malformed + wire_size + 4, 2 * sizeof(int32_t));
   sdl_wire_encode_native(malformed + wire_size + 8, states,
      2 * sizeof(int32_t));
   decoded_size = malformed_size;
   decoded = (CodecCases *)type_decode(malformed, &decoded_size);
   assert(decoded == NULL);
   assert(type_decode_dynamic(malformed, malformed_size) == NULL);
   free(malformed);
   type_free(wire);
}

static void test_negative_enum_values(void) {
   CodecCases input;
   CodecCases *decoded;
   SdlDynamicMessage *dynamic;
   const SdlDynamicValue *state;
   uint8_t *wire;
   size_t wire_size = 0;
   size_t decoded_size;
   memset(&input, 0, sizeof(input));
   input.has_state = true;
   input.state = STATE_NEGATIVE;
   wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
   assert(wire != NULL);
   decoded_size = wire_size;
   decoded = (CodecCases *)type_decode(wire, &decoded_size);
   assert(decoded != NULL && decoded->has_state && decoded->state == STATE_NEGATIVE);
   dynamic = type_decode_dynamic(wire, wire_size);
   assert(dynamic != NULL);
   state = type_dynamic_get(dynamic, "state");
   assert(state != NULL && state->kind == SDL_DYNAMIC_ENUM);
   assert(state->value.enumeration.value == -7);
   assert(strcmp(state->value.enumeration.name, "NEGATIVE") == 0);
   type_dynamic_free(dynamic);
   type_free(decoded);
   type_free(wire);
   printf(" [S12] Signed enum values round-trip... OK\n");
}

static void test_enum_int32_boundaries(void) {
   const State states[2] = { STATE_MINIMUM, STATE_MAXIMUM };
   const int32_t expected[2] = { INT32_MIN, INT32_MAX };
   const char *names[2] = { "MINIMUM", "MAXIMUM" };
   size_t boundary;

   for (boundary = 0; boundary < 2; ++boundary) {
      CodecCases input;
      CodecCases *decoded;
      SdlDynamicMessage *dynamic;
      const SdlDynamicValue *value;
      uint8_t *wire;
      size_t wire_size = 0;
      size_t decoded_size;

      memset(&input, 0, sizeof(input));
      input.has_state = true;
      input.state = states[boundary];
      wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
      assert(wire != NULL);
      decoded_size = wire_size;
      decoded = (CodecCases *)type_decode(wire, &decoded_size);
      assert(decoded != NULL && decoded->has_state &&
         decoded->state == states[boundary]);
      dynamic = type_decode_dynamic(wire, wire_size);
      assert(dynamic != NULL);
      value = type_dynamic_get(dynamic, "state");
      assert(value != NULL && value->kind == SDL_DYNAMIC_ENUM);
      assert(value->value.enumeration.value == expected[boundary]);
      assert(strcmp(value->value.enumeration.name, names[boundary]) == 0);
      type_dynamic_free(dynamic);
      type_free(decoded);
      type_free(wire);
   }
   printf(" [S24] Enum int32 boundary values round-trip... OK\n");
}

static uint32_t test_fnv1a_32(const uint8_t *data, size_t size) {
   uint32_t hash = 2166136261U;
   size_t i;
   for (i = 0; i < size; ++i)
      hash = (hash ^ data[i]) * 16777619U;
   return hash;
}


static void test_descriptor_append_u16(uint8_t *data, size_t *offset,
   uint16_t value) {
   data[(*offset)++] = (uint8_t)(value >> 8);
   data[(*offset)++] = (uint8_t)value;
}

static void test_descriptor_append_u32(uint8_t *data, size_t *offset,
   uint32_t value) {
   data[(*offset)++] = (uint8_t)(value >> 24);
   data[(*offset)++] = (uint8_t)(value >> 16);
   data[(*offset)++] = (uint8_t)(value >> 8);
   data[(*offset)++] = (uint8_t)value;
}

static void test_descriptor_append_text(uint8_t *data, size_t *offset,
   const char *value) {
   size_t length = strlen(value);
   test_descriptor_append_u16(data, offset, (uint16_t)length);
   memcpy(data + *offset, value, length);
   *offset += length;
}

static size_t test_single_field_descriptor(uint8_t *descriptor,
   const char *type_name, uint8_t modifier, const uint32_t *dimensions,
   uint8_t dimension_count) {
   size_t offset = 0;
   memcpy(descriptor, "SDD1", 4);
   offset = 4;
   test_descriptor_append_text(descriptor, &offset, "Packet");
   test_descriptor_append_u16(descriptor, &offset, 1);
   test_descriptor_append_text(descriptor, &offset, "Packet");
   test_descriptor_append_u16(descriptor, &offset, 1);
   test_descriptor_append_u32(descriptor, &offset, 1);
   test_descriptor_append_text(descriptor, &offset, "value");
   descriptor[offset++] = modifier;
   test_descriptor_append_text(descriptor, &offset, type_name);
   descriptor[offset++] = dimension_count;
   while (dimension_count-- != 0)
      test_descriptor_append_u32(descriptor, &offset, *dimensions++);
   test_descriptor_append_u16(descriptor, &offset, 0);
   return offset;
}

static void test_builtin_type_name_collision(const char *type_name,
   bool as_enum) {
   uint8_t descriptor[128];
   uint8_t frame[sizeof(descriptor) + 8];
   const char *message_names[2] = { "Packet", "Packet" };
   size_t message_count = as_enum ? 1 : 2;
   size_t offset = 0;
   size_t i;
   size_t descriptor_size;
   memcpy(descriptor, "SDD1", 4);
   offset = 4;
   test_descriptor_append_text(descriptor, &offset, "Packet");
   if (!as_enum) {
      message_names[0] = type_name;
      message_names[1] = "Packet";
      if (strcmp(message_names[0], message_names[1]) > 0) {
         const char *temporary = message_names[0];
         message_names[0] = message_names[1];
         message_names[1] = temporary;
      }
   }
   test_descriptor_append_u16(descriptor, &offset, (uint16_t)message_count);
   for (i = 0; i < message_count; ++i) {
      test_descriptor_append_text(descriptor, &offset, message_names[i]);
      test_descriptor_append_u16(descriptor, &offset, 0);
   }
   test_descriptor_append_u16(descriptor, &offset, as_enum ? 1 : 0);
   if (as_enum) {
      test_descriptor_append_text(descriptor, &offset, type_name);
      test_descriptor_append_u16(descriptor, &offset, 1);
      test_descriptor_append_text(descriptor, &offset, "VALUE");
      test_descriptor_append_u32(descriptor, &offset, 1);
   }
   descriptor_size = offset;
   sdl_wire_write_u32(frame, (uint32_t)descriptor_size);
   memcpy(frame + 4, descriptor, descriptor_size);
   sdl_wire_write_u32(frame + 4 + descriptor_size,
      test_fnv1a_32(descriptor, descriptor_size));
   assert(type_decode_dynamic(frame, descriptor_size + 8) == NULL);
}

static void test_dynamic_descriptors_reject_builtin_type_name_collisions(void) {
   test_builtin_type_name_collision("int32", false);
   test_builtin_type_name_collision("string", true);
   printf(" [S19] Built-in type name collisions are rejected... OK\n");
}

static void test_empty_message_round_trip(void) {
   EmptyMessage input = { 0 };
   EmptyMessage *decoded;
   EmptyMessage *cloned;
   SdlDynamicMessage *dynamic;
   size_t wire_size = 0;
   size_t decoded_size;
   uint8_t *wire = (uint8_t *)type_encode("EmptyMessage", &input,
      &wire_size);
   assert(wire != NULL);
   assert(wire_size == 8 + EMPTYMESSAGE_SCHEMA_DESCRIPTOR_SIZE);
   assert(type_encode_size("EmptyMessage", &input) == wire_size);
   decoded_size = wire_size;
   decoded = (EmptyMessage *)type_decode(wire, &decoded_size);
   assert(decoded != NULL);
   cloned = (EmptyMessage *)type_clone("EmptyMessage", &input);
   assert(cloned != NULL);
   dynamic = type_decode_dynamic(wire, wire_size);
   assert(dynamic != NULL && dynamic->field_count == 0);
   assert(strcmp(dynamic->type_name, "EmptyMessage") == 0);
   type_dynamic_free(dynamic);
   type_free(cloned);
   type_free(decoded);
   type_free(wire);
   printf(" [S21] Empty messages encode and decode... OK\n");
}

static void test_dynamic_descriptors_reject_empty_enums(void) {
   uint8_t descriptor[64];
   uint8_t frame[sizeof(descriptor) + 8];
   size_t offset = 0;
   size_t descriptor_size;
   memcpy(descriptor, "SDD1", 4);
   offset = 4;
   test_descriptor_append_text(descriptor, &offset, "Packet");
   test_descriptor_append_u16(descriptor, &offset, 1);
   test_descriptor_append_text(descriptor, &offset, "Packet");
   test_descriptor_append_u16(descriptor, &offset, 0);
   test_descriptor_append_u16(descriptor, &offset, 1);
   test_descriptor_append_text(descriptor, &offset, "State");
   test_descriptor_append_u16(descriptor, &offset, 0);
   descriptor_size = offset;
   sdl_wire_write_u32(frame, (uint32_t)descriptor_size);
   memcpy(frame + 4, descriptor, descriptor_size);
   sdl_wire_write_u32(frame + 4 + descriptor_size,
      test_fnv1a_32(descriptor, descriptor_size));
   assert(type_decode_dynamic(frame, descriptor_size + 8) == NULL);
   printf(" [S20] Empty enum descriptors are rejected... OK\n");
}

static void test_dynamic_descriptors_reject_invalid_fixed_layouts(void) {
   static const uint32_t zero_dimension[] = { 0 };
   static const uint32_t oversized_dimension[] = { UINT32_MAX };
   static const char *type_names[] = { "string", "int8", "int16" };
   static const uint8_t modifiers[] = { 3, 0, 0 };
   static const uint8_t dimension_counts[] = { 0, 1, 1 };
   const uint32_t *dimensions[] = { NULL, zero_dimension,
      oversized_dimension };
   size_t i;
   for (i = 0; i < sizeof(type_names) / sizeof(type_names[0]); ++i) {
      uint8_t descriptor[128];
      uint8_t frame[sizeof(descriptor) + 8];
      size_t descriptor_size = test_single_field_descriptor(descriptor,
         type_names[i], modifiers[i], dimensions[i], dimension_counts[i]);
      sdl_wire_write_u32(frame, (uint32_t)descriptor_size);
      memcpy(frame + 4, descriptor, descriptor_size);
      sdl_wire_write_u32(frame + 4 + descriptor_size,
         test_fnv1a_32(descriptor, descriptor_size));
      assert(type_decode_dynamic(frame, descriptor_size + 8) == NULL);
   }
   printf(" [S18] Invalid fixed-layout descriptors are rejected... OK\n");
}

static void test_recursive_wire_descriptor_is_rejected(void) {
   static const uint8_t descriptor[] = {
      0x53, 0x44, 0x44, 0x31, 0x00, 0x01, 0x41, 0x00,
      0x01, 0x00, 0x01, 0x41, 0x00, 0x01, 0x00, 0x00,
      0x00, 0x01, 0x00, 0x04, 0x6E, 0x65, 0x78, 0x74,
      0x01, 0x00, 0x01, 0x41, 0x00, 0x00, 0x00
   };
   uint8_t frame[sizeof(descriptor) + 8];
   sdl_wire_write_u32(frame, (uint32_t)sizeof(descriptor));
   memcpy(frame + 4, descriptor, sizeof(descriptor));
   sdl_wire_write_u32(frame + 4 + sizeof(descriptor),
      test_fnv1a_32(descriptor, sizeof(descriptor)));
   assert(type_decode_dynamic(frame, sizeof(frame)) == NULL);
   printf(" [S13] Recursive dynamic descriptors are rejected... OK\n");
}

static void test_nul_in_wire_descriptor_is_rejected(void) {
   static const uint8_t descriptor[] = {
      0x53, 0x44, 0x44, 0x31, 0x00, 0x02, 0x41, 0x00,
      0x00, 0x01, 0x00, 0x02, 0x41, 0x00, 0x00, 0x00,
      0x00, 0x00
   };
   uint8_t frame[sizeof(descriptor) + 8];
   sdl_wire_write_u32(frame, (uint32_t)sizeof(descriptor));
   memcpy(frame + 4, descriptor, sizeof(descriptor));
   sdl_wire_write_u32(frame + 4 + sizeof(descriptor),
      test_fnv1a_32(descriptor, sizeof(descriptor)));
   assert(type_decode_dynamic(frame, sizeof(frame)) == NULL);
   printf(" [S14] NUL in wire descriptor strings is rejected... OK\n");
}

static void test_invalid_utf8_wire_descriptor_is_rejected(void) {
   static const uint8_t descriptor[] = {
      0x53, 0x44, 0x44, 0x31, 0x00, 0x01, 0xFF, 0x00,
      0x01, 0x00, 0x01, 0xFF, 0x00, 0x00, 0x00, 0x00
   };
   uint8_t frame[sizeof(descriptor) + 8];
   sdl_wire_write_u32(frame, (uint32_t)sizeof(descriptor));
   memcpy(frame + 4, descriptor, sizeof(descriptor));
   sdl_wire_write_u32(frame + 4 + sizeof(descriptor),
      test_fnv1a_32(descriptor, sizeof(descriptor)));
   assert(type_decode_dynamic(frame, sizeof(frame)) == NULL);
   printf(" [S15] Invalid UTF-8 in wire descriptors is rejected... OK\n");
}


static void test_fixed_arrays_reject_variable_wire_elements(void) {
   static const uint8_t descriptor[] = {

      0x53, 0x44, 0x44, 0x31, 0x00, 0x06, 0x50, 0x61,
      0x63, 0x6B, 0x65, 0x74, 0x00, 0x02, 0x00, 0x05,
      0x43, 0x68, 0x69, 0x6C, 0x64, 0x00, 0x01, 0x00,
      0x00, 0x00, 0x01, 0x00, 0x04, 0x74, 0x65, 0x78,
      0x74, 0x00, 0x00, 0x06, 0x73, 0x74, 0x72, 0x69,
      0x6E, 0x67, 0x00, 0x00, 0x06, 0x50, 0x61, 0x63,
      0x6B, 0x65, 0x74, 0x00, 0x01, 0x00, 0x00, 0x00,
      0x01, 0x00, 0x05, 0x69, 0x74, 0x65, 0x6D, 0x73,
      0x00, 0x00, 0x05, 0x43, 0x68, 0x69, 0x6C, 0x64,
      0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00
   };
   uint8_t frame[sizeof(descriptor) + 8];
   sdl_wire_write_u32(frame, (uint32_t)sizeof(descriptor));
   memcpy(frame + 4, descriptor, sizeof(descriptor));
   sdl_wire_write_u32(frame + 4 + sizeof(descriptor),
      test_fnv1a_32(descriptor, sizeof(descriptor)));
   assert(type_decode_dynamic(frame, sizeof(frame)) == NULL);
   printf(" [S17] Fixed arrays reject variable-size elements... OK\n");
}

static void test_invalid_utf8_strings(void) {
   RootPayload input;
   static const uint8_t invalid_sequences[][5] = {
      { 0xC0U, 0xAFU, 'A', 'B', 0 },       /* Overlong encoding. */
      { 0xE2U, 0x82U, 'A', 'B', 0 },        /* Invalid continuation after a prefix. */
      { 0xEDU, 0xA0U, 0x80U, 'A', 0 },      /* Encoded surrogate. */
      { 0xF4U, 0x90U, 0x80U, 0x80U, 0 },   /* Code point above U+10FFFF. */
      { 0x80U, 'A', 'B', 'C', 0 }           /* Isolated continuation byte. */
   };
   static const size_t invalid_lengths[] = { 4, 4, 4, 4, 4 };
   uint8_t *wire;
   size_t wire_size = 0;
   size_t decoded_size;
   size_t body_offset;
   size_t i;
   memset(&input, 0, sizeof(input));
   input.has_header = true;
   for (i = 0; i < sizeof(invalid_lengths) / sizeof(invalid_lengths[0]); ++i) {
      input.header = (const char *)invalid_sequences[i];
      assert(type_encode("RootPayload", &input, &wire_size) == NULL);
   }

   input.header = "four";
   wire = (uint8_t *)type_encode("RootPayload", &input, &wire_size);
   assert(wire != NULL);
   body_offset = 8 + sdl_wire_read_u32(wire);
   for (i = 0; i < sizeof(invalid_lengths) / sizeof(invalid_lengths[0]); ++i) {
      memcpy(wire + body_offset + 8, invalid_sequences[i], invalid_lengths[i]);
      sdl_wire_write_u32(wire + body_offset + 4, (uint32_t)invalid_lengths[i]);
      assert(type_decode_size(wire, wire_size) == 0);
      decoded_size = wire_size;
      assert(type_decode(wire, &decoded_size) == NULL);
      assert(type_decode_dynamic(wire, wire_size) == NULL);
   }
   wire[body_offset + 8] = 0xE2U;
   wire[body_offset + 9] = 0x82U;
   sdl_wire_write_u32(wire + body_offset + 4, 2);
   assert(type_decode_size(wire, body_offset + 10) == 0);
   decoded_size = body_offset + 10;
   assert(type_decode(wire, &decoded_size) == NULL);
   assert(type_decode_dynamic(wire, body_offset + 10) == NULL);
   type_free(wire);
   printf(" [S09] Invalid and truncated UTF-8 strings are rejected... OK\n");
}

static void test_embedded_nul_string(void) {
   RootPayload input;
   RootPayload *decoded;
   RootPayload *cloned;
   SdlDynamicMessage *dynamic;
   const SdlDynamicValue *header;
   const char text[] = { 'A', '\0', 'B', (char)0xE2, (char)0x98,
      (char)0x83 };
   const size_t text_length = sizeof(text);
   uint8_t *wire;
   size_t wire_size = 0;
   size_t decoded_size;
   char *displayed;
   memset(&input, 0, sizeof(input));
   input.has_header = true;
   input.header = text;
   input.sdl_string_length_1 = (uint32_t)text_length;
   wire = (uint8_t *)type_encode("RootPayload", &input, &wire_size);
   assert(wire != NULL);
   assert(type_encode_size("RootPayload", &input) == wire_size);
   decoded_size = wire_size;
   decoded = (RootPayload *)type_decode(wire, &decoded_size);
   assert(decoded != NULL && decoded->sdl_string_length_1 == text_length);
   assert(memcmp(decoded->header, text, text_length) == 0);
   dynamic = type_decode_dynamic(wire, wire_size);
   assert(dynamic != NULL);
   header = type_dynamic_get(dynamic, "header");
   assert(header != NULL && header->kind == SDL_DYNAMIC_STRING);
   assert(header->value.string.size == text_length);
   assert(memcmp(header->value.string.data, text, text_length) == 0);
   cloned = (RootPayload *)type_clone("RootPayload", decoded);
   assert(cloned != NULL && cloned->sdl_string_length_1 == text_length);
   assert(cloned->header != decoded->header);
   assert(memcmp(cloned->header, text, text_length) == 0);
   displayed = type_display("RootPayload", decoded, 3);
   assert(displayed != NULL);
   assert(strstr(displayed, "\"A\\u0000B\342\230\203\"") != NULL);
   type_free(displayed);
   {
      uint8_t *reencoded;
      size_t reencoded_size = 0;
      reencoded = (uint8_t *)type_encode("RootPayload", decoded, &reencoded_size);
      assert(reencoded != NULL && reencoded_size == wire_size);
      assert(memcmp(reencoded, wire, wire_size) == 0);
      type_free(reencoded);
   }
   type_free(cloned);
   type_dynamic_free(dynamic);
   type_free(decoded);
   type_free(wire);
   printf(" [S10] Embedded NUL strings preserve byte lengths... OK\n");
}

static void test_c_string_length_fallback(void) {
   RootPayload scalar_input;
   RootPayload *scalar_decoded;
   CodecCases repeated_input;
   CodecCases *repeated_decoded;
   const char *labels[] = { "alpha", "beta" };
   uint8_t *wire;
   size_t wire_size = 0;
   size_t decode_size;

   memset(&scalar_input, 0, sizeof(scalar_input));
   scalar_input.has_header = true;
   scalar_input.header = "ordinary C string";
   wire = (uint8_t *)type_encode("RootPayload", &scalar_input, &wire_size);
   assert(wire != NULL);
   decode_size = wire_size;
   scalar_decoded = (RootPayload *)type_decode(wire, &decode_size);
   assert(scalar_decoded != NULL);
   assert(strcmp(scalar_decoded->header, "ordinary C string") == 0);
   assert(scalar_decoded->sdl_string_length_1 == strlen("ordinary C string"));
   type_free(scalar_decoded);
   type_free(wire);

   memset(&repeated_input, 0, sizeof(repeated_input));
   repeated_input.labels_count = 2;
   repeated_input.labels = labels;
   wire_size = 0;
   wire = (uint8_t *)type_encode("CodecCases", &repeated_input, &wire_size);
   assert(wire != NULL);
   decode_size = wire_size;
   repeated_decoded = (CodecCases *)type_decode(wire, &decode_size);
   assert(repeated_decoded != NULL && repeated_decoded->labels_count == 2);
   assert(strcmp(repeated_decoded->labels[0], labels[0]) == 0);
   assert(strcmp(repeated_decoded->labels[1], labels[1]) == 0);
   assert(repeated_decoded->sdl_string_length_14[0] == strlen(labels[0]));
   assert(repeated_decoded->sdl_string_length_14[1] == strlen(labels[1]));
   type_free(repeated_decoded);
   type_free(wire);
   printf(" [S11] Ordinary C strings infer UTF-8 byte lengths... OK\n");
}

static void test_codec_cases(void) {
   CodecCases input;
   CodecCases absent;
   CodecCases *decoded;
   CodecCases *cloned;
   uint8_t *encoded;
   uint8_t *extended;
   uint8_t *invalid;
   CodecCases *invalid_decoded;
   size_t encoded_size = 0;
   size_t extended_size;
   size_t decode_size;
   float point_parts[2] = { 1.25f, -2.5f };
   double position_parts[2] = { -3.125, 4.75 };
   float points[2][2] = { { 5.5f, -6.25f }, { 0.0f, 9.0f } };
   int16_t samples[3] = { -32768, -1, 32767 };
   State packed_states[2] = { STATE_READY, STATE_NEGATIVE };
   double measurements[2] = { 0.125, -1024.5 };
   const char *labels[3] = { "", "alpha", "ome\0ga\0" };
   uint32_t label_lengths[3] = { 0, 5, 7 };
   float complex point_values[2];
   uint8_t zero_length_unknown_field[8];
   uint8_t known_zero_field[12];
   uint8_t unknown_field[8];
   uint8_t repeated_unknown_field[9];
   const uint8_t unknown_payload[3] = { 0xA1, 0xB2, 0xC3 };
   uint8_t invalid_bool_field[9];
   uint8_t invalid_packed_field[9];
   uint8_t invalid_packed_bool_field[9];
   bool bool_flags[3] = { true, false, true };
   bool packed_flags[3] = { false, true, false };
   char *displayed;

   sdl_wire_write_u32(zero_length_unknown_field, 998);
   sdl_wire_write_u32(zero_length_unknown_field + 4, 0);
   sdl_wire_write_u32(known_zero_field, 11);
   sdl_wire_write_u32(known_zero_field + 4, 4);
   memset(known_zero_field + 8, 0, 4);
   sdl_wire_write_u32(unknown_field, 999);
   sdl_wire_write_u32(unknown_field + 4, 3);
   sdl_wire_write_u32(repeated_unknown_field, 999);
   sdl_wire_write_u32(repeated_unknown_field + 4, 1);
   repeated_unknown_field[8] = 0xD4;
   sdl_wire_write_u32(invalid_bool_field, 17);
   sdl_wire_write_u32(invalid_bool_field + 4, 1);
   invalid_bool_field[8] = 2;
   sdl_wire_write_u32(invalid_packed_field, 15);
   sdl_wire_write_u32(invalid_packed_field + 4, 1);
   invalid_packed_field[8] = 0;
   sdl_wire_write_u32(invalid_packed_bool_field, 22);
   sdl_wire_write_u32(invalid_packed_bool_field + 4, 1);
   invalid_packed_bool_field[8] = 2;

   memset(&input, 0, sizeof(input));
   input.has_tiny = true;
   input.tiny = INT8_MIN;
   input.has_small = true;
   input.small = INT16_MIN;
   input.has_signed_value = true;
   input.signed_value = INT32_MIN;
   input.has_wide = true;
   input.wide = INT64_MAX;
   input.has_ratio = true;
   input.ratio = -0.0f;
   input.has_precise = true;
   input.precise = 1.0 / 3.0;
   input.has_point = true;
   memcpy(&input.point, point_parts, sizeof(input.point));
   input.has_position = true;
   memcpy(&input.position, position_parts, sizeof(input.position));
   input.has_state = true;
   input.state = STATE_READY;
   input.has_empty_text = true;
   input.empty_text = "";
   input.required_zero = 0;
   input.samples_count = 3;
   input.samples = samples;
   input.measurements_count = 2;
   input.measurements = measurements;
   input.labels_count = 3;
   input.labels = labels;
   input.sdl_string_length_14 = label_lengths;
   input.points_count = 2;
   memcpy(&point_values[0], points[0], sizeof(points[0]));
   memcpy(&point_values[1], points[1], sizeof(points[1]));
   input.points = point_values;
   input.required_enabled = true;
   input.has_high_id_value = true;
   input.high_id_value = 2147483647;
   input.has_optional_enabled = true;
   input.optional_enabled = false;
   input.bool_flags_count = 3;
   input.bool_flags = bool_flags;
   input.fixed_states[0] = STATE_READY;
   input.fixed_states[1] = STATE_NEGATIVE;
   input.fixed_states[2] = STATE_UNKNOWN;
   input.packed_states_count = 2;
   input.packed_states = packed_states;
   input.packed_flags_count = 3;
   input.packed_flags = packed_flags;

   encoded = (uint8_t *)type_encode("CodecCases", &input, &encoded_size);
   assert(encoded != NULL);
   assert(type_encode_size("CodecCases", &input) == encoded_size);
   displayed = type_display("CodecCases", &input, 2);
   assert(displayed != NULL);
   assert(strstr(displayed, "  tiny: -128\n") != NULL);
   assert(strstr(displayed, "  state: READY\n") != NULL);
   assert(strstr(displayed, "  samples: [\n    -32768,\n") != NULL);
   assert(strstr(displayed, "\"ome\\u0000ga\\u0000\"") != NULL);
   type_free(displayed);
   assert(encoded_size > 4);
   assert(type_decode_size(encoded, encoded_size - 1) == 0);
   {
      SdlDynamicMessage *dynamic = type_decode_dynamic(encoded, encoded_size);
      uint8_t *modified = (uint8_t *)malloc(encoded_size);
      uint32_t descriptor_size = sdl_wire_read_u32(encoded);
      const SdlDynamicValue *state;
      assert(modified != NULL);
      memcpy(modified, encoded, encoded_size);
      modified[4 + descriptor_size] ^= 1U;
      assert(type_decode_dynamic(modified, encoded_size) == NULL);
      assert(type_decode_size(modified, encoded_size) == 0);
      {
         size_t modified_size = encoded_size;
         assert(type_decode(modified, &modified_size) == NULL);
      }
      free(modified);
      assert(dynamic != NULL);
      state = type_dynamic_get(dynamic, "state");
      assert(type_dynamic_get(dynamic, "high_id_value") != NULL);
      assert(type_dynamic_get(dynamic, "high_id_value")->kind == SDL_DYNAMIC_INTEGER);
      assert(type_dynamic_get(dynamic, "high_id_value")->value.integer == 2147483647);
      assert(state != NULL && state->kind == SDL_DYNAMIC_ENUM);
      assert(strcmp(state->value.enumeration.type_name, "State") == 0);
      assert(strcmp(state->value.enumeration.name, "READY") == 0);
      assert(state->value.enumeration.value == 1);
      type_dynamic_free(dynamic);
   }

   extended_size = encoded_size + sizeof(zero_length_unknown_field) +
      sizeof(known_zero_field) + sizeof(unknown_field) + sizeof(unknown_payload) +
      sizeof(repeated_unknown_field);
   extended = (uint8_t *)malloc(extended_size);
   assert(extended != NULL);
   memcpy(extended, encoded, encoded_size);
   memcpy(extended + encoded_size, zero_length_unknown_field,
      sizeof(zero_length_unknown_field));
   memcpy(extended + encoded_size + sizeof(zero_length_unknown_field),
      known_zero_field, sizeof(known_zero_field));
   memcpy(extended + encoded_size + sizeof(zero_length_unknown_field) +
      sizeof(known_zero_field), unknown_field, sizeof(unknown_field));
   memcpy(extended + encoded_size + sizeof(zero_length_unknown_field) +
      sizeof(known_zero_field) + sizeof(unknown_field), unknown_payload,
      sizeof(unknown_payload));
   memcpy(extended + encoded_size + sizeof(zero_length_unknown_field) +
      sizeof(known_zero_field) + sizeof(unknown_field) + sizeof(unknown_payload),
      repeated_unknown_field, sizeof(repeated_unknown_field));
   decode_size = extended_size;
   decoded = (CodecCases *)type_decode(extended, &decode_size);
   assert(decoded != NULL);
   {
      SdlDynamicMessage *dynamic = type_decode_dynamic(extended, extended_size);
      const SdlDynamicValue *dynamic_zero;
      const SdlDynamicValue *dynamic_samples;
      const SdlDynamicValue *dynamic_packed_flags;
      const SdlDynamicValue *dynamic_labels;
      uint8_t *truncated_unknown = (uint8_t *)malloc(extended_size - 1);
      assert(dynamic != NULL);
      dynamic_zero = type_dynamic_get(dynamic, "required_zero");
      dynamic_samples = type_dynamic_get(dynamic, "samples");
      dynamic_packed_flags = type_dynamic_get(dynamic, "packed_flags");
      dynamic_labels = type_dynamic_get(dynamic, "labels");
      assert(dynamic_zero != NULL && dynamic_zero->kind == SDL_DYNAMIC_INTEGER &&
         dynamic_zero->value.integer == 0);
      assert(dynamic_samples != NULL && dynamic_samples->kind == SDL_DYNAMIC_ARRAY &&
         dynamic_samples->value.array.count == 3);
      assert(dynamic_samples->value.array.items[0].value.integer == -32768 &&
         dynamic_samples->value.array.items[1].value.integer == -1 &&
         dynamic_samples->value.array.items[2].value.integer == 32767);
      assert(dynamic_packed_flags != NULL &&
         dynamic_packed_flags->kind == SDL_DYNAMIC_ARRAY &&
         dynamic_packed_flags->value.array.count == 3);
      assert(dynamic_packed_flags->value.array.items[0].kind == SDL_DYNAMIC_BOOL &&
         !dynamic_packed_flags->value.array.items[0].value.boolean);
      assert(dynamic_packed_flags->value.array.items[1].kind == SDL_DYNAMIC_BOOL &&
         dynamic_packed_flags->value.array.items[1].value.boolean);
      assert(dynamic_packed_flags->value.array.items[2].kind == SDL_DYNAMIC_BOOL &&
         !dynamic_packed_flags->value.array.items[2].value.boolean);
      assert(dynamic_labels != NULL && dynamic_labels->kind == SDL_DYNAMIC_ARRAY &&
         dynamic_labels->value.array.count == 3);
      assert(dynamic_labels->value.array.items[2].kind == SDL_DYNAMIC_STRING &&
         dynamic_labels->value.array.items[2].value.string.size == 7 &&
         memcmp(dynamic_labels->value.array.items[2].value.string.data,
            "ome\0ga\0", 7) == 0);
      assert(truncated_unknown != NULL);
      memcpy(truncated_unknown, extended, extended_size - 1);
      assert(type_decode_dynamic(truncated_unknown, extended_size - 1) == NULL);
      type_dynamic_free(dynamic);
      free(truncated_unknown);
   }

   assert(decoded->has_tiny && decoded->tiny == input.tiny);
   assert(decoded->has_small && decoded->small == input.small);
   assert(decoded->has_signed_value && decoded->signed_value == input.signed_value);
   assert(decoded->has_wide && decoded->wide == input.wide);
   assert(decoded->has_ratio && signbit(decoded->ratio));
   assert(decoded->has_precise && decoded->precise == input.precise);
   assert(decoded->has_point && memcmp(&decoded->point, &input.point, sizeof(input.point)) == 0);
   assert(decoded->has_position && memcmp(&decoded->position, &input.position, sizeof(input.position)) == 0);
   assert(decoded->has_state && decoded->state == input.state);
   assert(decoded->has_empty_text && strcmp(decoded->empty_text, "") == 0);
   assert(decoded->required_zero == 0);
   assert(decoded->has_high_id_value && decoded->high_id_value == 2147483647);
   assert(decoded->samples_count == 3);
   assert(memcmp(decoded->samples, samples, sizeof(samples)) == 0);
   assert(decoded->measurements_count == 2);
   assert(memcmp(decoded->measurements, measurements, sizeof(measurements)) == 0);
   assert(decoded->labels_count == 3);
   assert(strcmp(decoded->labels[0], "") == 0);
   assert(strcmp(decoded->labels[1], "alpha") == 0);
   assert(decoded->sdl_string_length_14[2] == 7);
   assert(memcmp(decoded->labels[2], "ome\0ga\0", 7) == 0);
   cloned = (CodecCases *)type_clone("CodecCases", &input);
   assert(cloned != NULL && cloned->labels_count == 3);
   assert(cloned->sdl_string_length_14 != input.sdl_string_length_14);
   assert(cloned->sdl_string_length_14[2] == 7);
   assert(memcmp(cloned->labels[2], "ome\0ga\0", 7) == 0);
   type_free(cloned);
   assert(decoded->points_count == 2);
   assert(memcmp(decoded->points, point_values, sizeof(point_values)) == 0);
   assert(decoded->empty_values_count == 0);
   assert(decoded->empty_values == NULL);
   assert(decoded->required_enabled);
   assert(decoded->has_optional_enabled && !decoded->optional_enabled);
   assert(decoded->bool_flags_count == 3);
   assert(decoded->bool_flags[0] && !decoded->bool_flags[1] && decoded->bool_flags[2]);
   assert(decoded->fixed_states[0] == STATE_READY &&
      decoded->fixed_states[1] == STATE_NEGATIVE &&
      decoded->fixed_states[2] == STATE_UNKNOWN);
   assert(decoded->packed_states_count == 2 &&
      decoded->packed_states[0] == STATE_READY &&
      decoded->packed_states[1] == STATE_NEGATIVE);
   assert(decoded->packed_flags_count == 3 &&
      !decoded->packed_flags[0] && decoded->packed_flags[1] &&
      !decoded->packed_flags[2]);
   type_free(decoded);
   type_free(extended);

   extended_size = encoded_size + sizeof(invalid_bool_field);
   invalid = (uint8_t *)malloc(extended_size);
   assert(invalid != NULL);
   memcpy(invalid, encoded, encoded_size);
   memcpy(invalid + encoded_size, invalid_bool_field, sizeof(invalid_bool_field));
   decode_size = extended_size;
   invalid_decoded = (CodecCases *)type_decode(invalid, &decode_size);
   assert(invalid_decoded == NULL);
   assert(type_decode_dynamic(invalid, extended_size) == NULL);
   type_free(invalid);

   extended_size = encoded_size + sizeof(invalid_packed_field);
   invalid = (uint8_t *)malloc(extended_size);
   assert(invalid != NULL);
   memcpy(invalid, encoded, encoded_size);
   memcpy(invalid + encoded_size, invalid_packed_field,
      sizeof(invalid_packed_field));
   decode_size = extended_size;
   invalid_decoded = (CodecCases *)type_decode(invalid, &decode_size);
   assert(invalid_decoded == NULL);
   assert(type_decode_dynamic(invalid, extended_size) == NULL);
   type_free(invalid);

   extended_size = encoded_size + sizeof(invalid_packed_bool_field);
   invalid = (uint8_t *)malloc(extended_size);
   assert(invalid != NULL);
   memcpy(invalid, encoded, encoded_size);
   memcpy(invalid + encoded_size, invalid_packed_bool_field,
      sizeof(invalid_packed_bool_field));
   decode_size = extended_size;
   invalid_decoded = (CodecCases *)type_decode(invalid, &decode_size);
   assert(invalid_decoded == NULL);
   assert(type_decode_dynamic(invalid, extended_size) == NULL);
   type_free(invalid);
   type_free(encoded);

   memset(&absent, 0, sizeof(absent));
   encoded_size = 0;
   encoded = (uint8_t *)type_encode("CodecCases", &absent, &encoded_size);
   assert(encoded != NULL);
   decode_size = encoded_size;
   decoded = (CodecCases *)type_decode(encoded, &decode_size);
   assert(decoded != NULL);
   assert(!decoded->has_tiny && !decoded->has_small);
   assert(!decoded->has_signed_value && !decoded->has_wide);
   assert(!decoded->has_ratio && !decoded->has_precise);
   assert(!decoded->has_point && !decoded->has_position);
   assert(!decoded->has_state && !decoded->has_empty_text);
   assert(decoded->required_zero == 0);
   assert(!decoded->required_enabled);
   assert(!decoded->has_optional_enabled && !decoded->optional_enabled);
   assert(decoded->bool_flags_count == 0 && decoded->bool_flags == NULL);
   assert(decoded->packed_flags_count == 0 && decoded->packed_flags == NULL);
   assert(decoded->samples_count == 0 && decoded->samples == NULL);
   assert(decoded->measurements_count == 0 && decoded->measurements == NULL);
   assert(decoded->labels_count == 0 && decoded->labels == NULL);
   assert(decoded->points_count == 0 && decoded->points == NULL);
   assert(decoded->empty_values_count == 0 && decoded->empty_values == NULL);
   type_free(decoded);
   type_free(encoded);
}

static void test_signed_integer_boundaries(void) {
   const int64_t expected[2][4] = {
      { INT8_MIN, INT16_MIN, INT32_MIN, INT64_MIN },
      { INT8_MAX, INT16_MAX, INT32_MAX, INT64_MAX }
   };
   size_t boundary;

   for (boundary = 0; boundary < 2; ++boundary) {
      CodecCases input;
      CodecCases *decoded;
      SdlDynamicMessage *dynamic;
      const char *names[4] = { "tiny", "small", "signed_value", "wide" };
      size_t wire_size = 0;
      size_t decoded_size;
      uint8_t *wire;
      size_t index;

      memset(&input, 0, sizeof(input));
      input.has_tiny = true;
      input.tiny = (int8_t)expected[boundary][0];
      input.has_small = true;
      input.small = (int16_t)expected[boundary][1];
      input.has_signed_value = true;
      input.signed_value = (int32_t)expected[boundary][2];
      input.has_wide = true;
      input.wide = expected[boundary][3];
      input.has_high_id_value = true;
      input.high_id_value = (int32_t)expected[boundary][2];

      wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
      assert(wire != NULL);
      decoded_size = wire_size;
      decoded = (CodecCases *)type_decode(wire, &decoded_size);
      assert(decoded != NULL);
      assert(decoded->tiny == input.tiny && decoded->small == input.small &&
         decoded->signed_value == input.signed_value && decoded->wide == input.wide &&
         decoded->high_id_value == input.high_id_value);

      dynamic = type_decode_dynamic(wire, wire_size);
      assert(dynamic != NULL);
      for (index = 0; index < 4; ++index) {
         const SdlDynamicValue *value = type_dynamic_get(dynamic, names[index]);
         assert(value != NULL && value->kind == SDL_DYNAMIC_INTEGER);
         assert(value->value.integer == expected[boundary][index]);
      }
      type_dynamic_free(dynamic);
      type_free(decoded);
      type_free(wire);
   }
   printf(" [S23] Signed integer minimum and maximum values round-trip... OK\n");
}

static void test_packed_field_occurrences_concatenate(void) {
   CodecCases input;
   CodecCases *decoded;
   SdlDynamicMessage *dynamic;
   const SdlDynamicValue *points;
   uint8_t *wire;
   uint8_t *extended;
   float complex first_point;
   float first_components[2] = { 1.25f, -2.5f };
   float second_components[2] = { 3.5f, 4.75f };
   size_t wire_size = 0;
   size_t extended_size;
   size_t malformed_size;
   size_t decoded_size;
   uint8_t *malformed;
   int16_t repeated_value = 123;
   memcpy(&first_point, first_components, sizeof(first_point));
   memset(&input, 0, sizeof(input));
   wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
   assert(wire != NULL);
   extended_size = wire_size + 8;
   extended = (uint8_t *)malloc(extended_size);
   assert(extended != NULL);
   memcpy(extended, wire, wire_size);
   sdl_wire_write_u32(extended + wire_size, 15);
   sdl_wire_write_u32(extended + wire_size + 4, 0);
   decoded_size = extended_size;
   decoded = (CodecCases *)type_decode(extended, &decoded_size);
   assert(decoded != NULL && decoded->points_count == 0 && decoded->points == NULL);
   dynamic = type_decode_dynamic(extended, extended_size);
   assert(dynamic != NULL);
   points = type_dynamic_get(dynamic, "points");
   assert(points != NULL && points->kind == SDL_DYNAMIC_ARRAY &&
      points->value.array.count == 0);
   type_dynamic_free(dynamic);
   type_free(decoded);
   free(extended);
   type_free(wire);

   memset(&input, 0, sizeof(input));
   input.points_count = 1;
   input.points = &first_point;
   wire = (uint8_t *)type_encode("CodecCases", &input, &wire_size);
   assert(wire != NULL);
   extended_size = wire_size + 8 + sizeof(second_components) + 8;
   extended = (uint8_t *)malloc(extended_size);
   assert(extended != NULL);
   memcpy(extended, wire, wire_size);
   sdl_wire_write_u32(extended + wire_size, 15);
   sdl_wire_write_u32(extended + wire_size + 4, sizeof(second_components));
   sdl_wire_encode_native(extended + wire_size + 8, &second_components[0],
      sizeof(second_components[0]));
   sdl_wire_encode_native(extended + wire_size + 8 + sizeof(float),
      &second_components[1], sizeof(second_components[1]));
   sdl_wire_write_u32(extended + wire_size + 8 + sizeof(second_components), 15);
   sdl_wire_write_u32(extended + wire_size + 12 + sizeof(second_components), 0);
   decoded_size = extended_size;
   decoded = (CodecCases *)type_decode(extended, &decoded_size);
   assert(decoded != NULL && decoded->points_count == 2);
   assert(crealf(decoded->points[0]) == 1.25f && cimagf(decoded->points[0]) == -2.5f);
   assert(crealf(decoded->points[1]) == 3.5f && cimagf(decoded->points[1]) == 4.75f);
   dynamic = type_decode_dynamic(extended, extended_size);
   assert(dynamic != NULL);
   points = type_dynamic_get(dynamic, "points");
   assert(points != NULL && points->kind == SDL_DYNAMIC_ARRAY &&
      points->value.array.count == 2);
   assert(points->value.array.items[0].kind == SDL_DYNAMIC_COMPLEX &&
      points->value.array.items[0].value.complex_value.real == 1.25 &&
      points->value.array.items[0].value.complex_value.imag == -2.5);
   assert(points->value.array.items[1].kind == SDL_DYNAMIC_COMPLEX &&
      points->value.array.items[1].value.complex_value.real == 3.5 &&
      points->value.array.items[1].value.complex_value.imag == 4.75);
   type_dynamic_free(dynamic);
   type_free(decoded);
   free(extended);

   malformed_size = wire_size + 8 + 1 + 8 + sizeof(repeated_value);
   malformed = (uint8_t *)malloc(malformed_size);
   assert(malformed != NULL);
   memcpy(malformed, wire, wire_size);
   sdl_wire_write_u32(malformed + wire_size, 12);
   sdl_wire_write_u32(malformed + wire_size + 4, 1);
   malformed[wire_size + 8] = 0;
   sdl_wire_write_u32(malformed + wire_size + 9, 12);
   sdl_wire_write_u32(malformed + wire_size + 13, sizeof(repeated_value));
   sdl_wire_encode_native(malformed + wire_size + 17, &repeated_value,
      sizeof(repeated_value));
   decoded_size = malformed_size;
   assert(type_decode(malformed, &decoded_size) == NULL);
   assert(type_decode_dynamic(malformed, malformed_size) == NULL);
   free(malformed);

   malformed_size = wire_size + 8 + 1 + 8 + sizeof(second_components);
   malformed = (uint8_t *)malloc(malformed_size);
   assert(malformed != NULL);
   memcpy(malformed, wire, wire_size);
   sdl_wire_write_u32(malformed + wire_size, 15);
   sdl_wire_write_u32(malformed + wire_size + 4, 1);
   malformed[wire_size + 8] = 0;
   sdl_wire_write_u32(malformed + wire_size + 9, 15);
   sdl_wire_write_u32(malformed + wire_size + 13, sizeof(second_components));
   sdl_wire_encode_native(malformed + wire_size + 17, &second_components[0],
      sizeof(second_components[0]));
   sdl_wire_encode_native(malformed + wire_size + 17 + sizeof(float),
      &second_components[1], sizeof(second_components[1]));
   decoded_size = malformed_size;
   assert(type_decode(malformed, &decoded_size) == NULL);
   assert(type_decode_dynamic(malformed, malformed_size) == NULL);
   free(malformed);
   type_free(wire);
}

static void test_fixed_nested_arrays(void) {
   FixedBoard input;
   FixedRow packed_rows[2];
   FixedBoard *decoded;
   SdlDynamicMessage *dynamic;
   uint8_t *wire;
   size_t wire_size = 0;
   size_t decode_size;
   size_t row, vector, coordinate, grid_row, grid_column;
   memset(&input, 0, sizeof(input));
   memset(packed_rows, 0, sizeof(packed_rows));
   for (row = 0; row < 2; ++row) {
      for (vector = 0; vector < 2; ++vector) {
         for (coordinate = 0; coordinate < 2; ++coordinate) {
            input.rows[row].vectors[vector].coords[coordinate] =
               (float)(row * 100 + vector * 10 + coordinate);
            packed_rows[row].vectors[vector].coords[coordinate] =
               (float)(500 + row * 100 + vector * 10 + coordinate);
         }
         for (grid_row = 0; grid_row < 2; ++grid_row)
            for (grid_column = 0; grid_column < 3; ++grid_column) {
               input.rows[row].vectors[vector].grid[grid_row][grid_column] =
                  (int16_t)(row * 100 + vector * 10 + grid_row * 3 + grid_column);
               packed_rows[row].vectors[vector].grid[grid_row][grid_column] =
                  (int16_t)(500 + row * 100 + vector * 10 + grid_row * 3 + grid_column);
            }
      }
   }
   input.packed_rows_count = 2;
   input.packed_rows = packed_rows;
   wire = (uint8_t *)type_encode("FixedBoard", &input, &wire_size);
   assert(wire != NULL);
   decode_size = wire_size;
   decoded = (FixedBoard *)type_decode(wire, &decode_size);
   assert(decoded != NULL);
   assert(memcmp(decoded->rows, input.rows, sizeof(input.rows)) == 0);
   assert(decoded->packed_rows_count == 2);
   assert(memcmp(decoded->packed_rows, packed_rows, sizeof(packed_rows)) == 0);
   dynamic = type_decode_dynamic(wire, wire_size);
   assert(dynamic != NULL);
   {
      const SdlDynamicValue *rows = type_dynamic_get(dynamic, "rows");
      const SdlDynamicMessage *row_value;
      const SdlDynamicValue *vectors;
      const SdlDynamicMessage *vector_value;
      const SdlDynamicValue *coords;
      assert(rows != NULL && rows->kind == SDL_DYNAMIC_ARRAY);
      assert(rows->value.array.count == 2);
      assert(rows->value.array.items[0].kind == SDL_DYNAMIC_MESSAGE);
      row_value = rows->value.array.items[0].value.message;
      vectors = type_dynamic_get(row_value, "vectors");
      assert(vectors != NULL && vectors->kind == SDL_DYNAMIC_ARRAY);
      vector_value = vectors->value.array.items[0].value.message;
      coords = type_dynamic_get(vector_value, "coords");
      assert(coords != NULL && coords->kind == SDL_DYNAMIC_ARRAY);
      assert(coords->value.array.items[1].value.floating == 1.0);
   }
   {
      const SdlDynamicValue *packed_rows =
         type_dynamic_get(dynamic, "packed_rows");
      const SdlDynamicMessage *packed_row;
      const SdlDynamicValue *vectors;
      const SdlDynamicMessage *vector_value;
      const SdlDynamicValue *grid;
      assert(packed_rows != NULL && packed_rows->kind == SDL_DYNAMIC_ARRAY);
      assert(packed_rows->value.array.count == 2);
      packed_row = packed_rows->value.array.items[1].value.message;
      vectors = type_dynamic_get(packed_row, "vectors");
      assert(vectors != NULL && vectors->kind == SDL_DYNAMIC_ARRAY);
      vector_value = vectors->value.array.items[0].value.message;
      grid = type_dynamic_get(vector_value, "grid");
      assert(grid != NULL && grid->kind == SDL_DYNAMIC_ARRAY);
      assert(grid->value.array.items[1].value.array.items[2].value.integer == 605);
   }
   type_dynamic_free(dynamic);
   type_free(decoded);
   type_free(wire);
}

static void test_anonymous_nested_structs(void) {
   AnonymousEnvelope input;
   AnonymousEnvelope_3 points[2] = { { .x = 1.25f, .y = -2.5f },
      { .x = 3.0f, .y = 4.5f } };
   AnonymousEnvelope *decoded;
   SdlDynamicMessage *dynamic;
   uint8_t *wire;
   size_t wire_size = 0;
   size_t decode_size;
   memset(&input, 0, sizeof(input));
   input.metadata.code = 42;
   input.metadata.has_detail = true;
   input.metadata.detail.text = "anonymous detail";
   input.points_count = 2;
   input.points = points;
   wire = (uint8_t *)type_encode("AnonymousEnvelope", &input, &wire_size);
   assert(wire != NULL);
   decode_size = wire_size;
   decoded = (AnonymousEnvelope *)type_decode(wire, &decode_size);
   assert(decoded != NULL);
   assert(decoded->metadata.code == 42);
   assert(decoded->metadata.has_detail);
   assert(strcmp(decoded->metadata.detail.text, "anonymous detail") == 0);
   assert(decoded->points_count == 2);
   assert(decoded->points[0].x == points[0].x && decoded->points[0].y == points[0].y);
   assert(decoded->points[1].x == points[1].x && decoded->points[1].y == points[1].y);
   dynamic = type_decode_dynamic(wire, wire_size);
   assert(dynamic != NULL);
   {
      const SdlDynamicValue *metadata = type_dynamic_get(dynamic, "metadata");
      const SdlDynamicValue *detail;
      const SdlDynamicValue *text;
      assert(metadata != NULL && metadata->kind == SDL_DYNAMIC_MESSAGE);
      detail = type_dynamic_get(metadata->value.message, "detail");
      assert(detail != NULL && detail->kind == SDL_DYNAMIC_MESSAGE);
      text = type_dynamic_get(detail->value.message, "text");
      assert(text != NULL && text->kind == SDL_DYNAMIC_STRING);
      assert(text->value.string.size == strlen("anonymous detail"));
      assert(memcmp(text->value.string.data, "anonymous detail",
         text->value.string.size) == 0);
   }
   type_dynamic_free(dynamic);
   type_free(decoded);
   type_free(wire);
}

static void assert_root_wire_fixture(const void *wire, size_t wire_size) {
   uint8_t expected[512];
   size_t expected_size;
#ifdef SDL_WIRE_LITTLE_ENDIAN
   FILE *fixture = fopen("tst/fixtures/root_payload.bin", "rb");
#else
   FILE *fixture = fopen("tst/fixtures/root_payload_be.bin", "rb");
#endif
   assert(fixture != NULL);
   expected_size = fread(expected, 1, sizeof(expected), fixture);
   assert(!ferror(fixture));
   assert(feof(fixture));
   fclose(fixture);
   {
      const uint8_t *actual = (const uint8_t *)wire;
      size_t descriptor_size = sdl_wire_read_u32(actual);
      assert(descriptor_size == ROOTPAYLOAD_SCHEMA_DESCRIPTOR_SIZE);
      assert(memcmp(actual + 4, ROOTPAYLOAD_SCHEMA_DESCRIPTOR,
         descriptor_size) == 0);
      assert(sdl_wire_read_u32(actual + 4 + descriptor_size) == ROOTPAYLOAD_HASH);
      assert(expected_size == wire_size);
      assert(memcmp(actual, expected, wire_size) == 0);
   }
}

int main(void) {
   /* Component C1: generated typed codec and ordinary message behavior. */
   register_all_types();
   printf("\n[C1] Typed codec: round trips and composite layouts\n");
   test_codec_cases();
   test_missing_required_fields_default();
   test_empty_message_round_trip();
   test_fixed_nested_arrays();
   test_anonymous_nested_structs();
   test_embedded_nul_string();
   test_c_string_length_fallback();
   test_negative_enum_values();
   test_packed_field_occurrences_concatenate();
   test_duplicate_field_semantics();

   /* Component C2: C storage and descriptor-driven runtime behavior. */
   printf("\n[C2] Runtime storage and dynamic descriptors\n");
   test_repeated_fields_reject_null_storage();
   test_dynamic_descriptors_reject_invalid_fixed_layouts();
   test_dynamic_descriptors_reject_builtin_type_name_collisions();
   test_dynamic_descriptors_reject_empty_enums();
   test_recursive_wire_descriptor_is_rejected();
   test_nul_in_wire_descriptor_is_rejected();
   test_invalid_utf8_wire_descriptor_is_rejected();
   test_fixed_arrays_reject_variable_wire_elements();
   printf(" [Boot] Schema dynamic registration completed\n");
   printf(" [Info] ROOTPAYLOAD_HASH is: 0x%08X\n", ROOTPAYLOAD_HASH);

   /* Component C3: malformed input and boundary conditions run last. */
   printf("\n[C3] Wire rejection and numeric boundaries\n");
   test_invalid_enum_values();
   test_fixed_enum_array_rejects_unknown_value();
   test_packed_fixed_message_rejects_nested_invalid_enum();
   test_invalid_utf8_strings();
   test_primitive_payload_widths();
   test_signed_integer_boundaries();
   test_enum_int32_boundaries();
   test_float_subnormal_round_trip();
   test_extended_usage_cases();

   /* 2. Instantiate arrays data source */
   FixedItem mock_fixed[2] = {
      { .x = 1.1f, .y = 2.2f },
      { .x = 3.3f, .y = 4.4f }
   };

   VarItem mock_var[2] = {
      { .has_name = true, .name = "Variable_Node_A", .id = 99999LL },
      { .has_name = true, .name = "Variable_Node_B", .id = 77777LL }
   };

   RootPayload original = {
      .has_header = true,
      .header = "Mission_Data_Packet",
      .fixed_array_count = 2,
      .fixed_array = mock_fixed,
      .var_array_count = 2,
      .var_array = mock_var
   };

   /* 3. Execute Deep Clone */
   RootPayload* cloned = (RootPayload*)type_clone("RootPayload", &original);
   if (!cloned) { printf("Error: Cloning step failed\n"); return 1; }
   assert(cloned->has_header);
   assert(strcmp(cloned->header, original.header) == 0);
   assert(cloned->header != original.header);
   assert(cloned->fixed_array_count == original.fixed_array_count);
   assert(cloned->fixed_array != original.fixed_array);
   assert(cloned->var_array_count == original.var_array_count);
   assert(cloned->var_array != original.var_array);
   assert(cloned->var_array[0].name != original.var_array[0].name);
   assert(strcmp(cloned->var_array[0].name, original.var_array[0].name) == 0);
   assert(cloned->var_array[1].name != original.var_array[1].name);
   assert(strcmp(cloned->var_array[1].name, original.var_array[1].name) == 0);
   printf(" 1. Deep Copy Cloning system........ OK (Header: %s)\n", cloned->header);

   /* 4. Encode to Binary Stream Buffer */
   size_t bin_size = 0;
   void* bin_stream = type_encode("RootPayload", cloned, &bin_size);
   if (!bin_stream) { printf("Error: Encoding step failed\n"); return 1; }
   assert_root_wire_fixture(bin_stream, bin_size);
   printf(" 2. Binary Auto-Descriptive Stream... OK (%zu bytes written)\n", bin_size);

   /* 5. Blind dynamic decoder step (Zero type info passed) */
   size_t rx_size = bin_size;
   void* generic_output = type_decode(bin_stream, &rx_size);
   if (!generic_output) { printf("Error: Decoding step failed\n"); return 1; }
   SdlDynamicMessage *descriptor_output = type_decode_dynamic(bin_stream, bin_size);
   assert(descriptor_output != NULL);
   {
      const SdlDynamicValue *dynamic_header = type_dynamic_get(descriptor_output, "header");
      const SdlDynamicValue *dynamic_items = type_dynamic_get(descriptor_output, "var_array");
      assert(dynamic_header != NULL && dynamic_header->kind == SDL_DYNAMIC_STRING);
      assert(dynamic_header->value.string.size == strlen(original.header));
      assert(memcmp(dynamic_header->value.string.data, original.header,
         dynamic_header->value.string.size) == 0);
      assert(dynamic_items != NULL && dynamic_items->kind == SDL_DYNAMIC_ARRAY);
      assert(dynamic_items->value.array.count == original.var_array_count);
      assert(dynamic_items->value.array.items[0].kind == SDL_DYNAMIC_MESSAGE);
      assert(type_dynamic_get(dynamic_items->value.array.items[0].value.message,
         "id")->value.integer == original.var_array[0].id);
   }
   printf(" 3. Blind Type-Agnostic Decoding..... OK\n");

   /* 6. Verify data integrity */
   const uint8_t* wire_bytes = (const uint8_t*)bin_stream;
   size_t descriptor_size = sdl_wire_read_u32(wire_bytes);
   uint32_t stream_hash = sdl_wire_read_u32(wire_bytes + 4 + descriptor_size);
   assert(stream_hash == ROOTPAYLOAD_HASH);
   {
      RootPayload* res = (RootPayload*)generic_output;
      assert(res->has_header);
      assert(strcmp(res->header, original.header) == 0);
      assert(res->fixed_array_count == 2);
      assert(res->fixed_array[0].x == mock_fixed[0].x);
      assert(res->fixed_array[0].y == mock_fixed[0].y);
      assert(res->fixed_array[1].x == mock_fixed[1].x);
      assert(res->fixed_array[1].y == mock_fixed[1].y);
      assert(res->var_array_count == 2);
      assert(res->var_array[0].has_name);
      assert(strcmp(res->var_array[0].name, mock_var[0].name) == 0);
      assert(res->var_array[0].id == mock_var[0].id);
      assert(res->var_array[1].has_name);
      assert(strcmp(res->var_array[1].name, mock_var[1].name) == 0);
      assert(res->var_array[1].id == mock_var[1].id);
      printf("\n============================================\n");
      printf(" INTEGRITY VERIFICATION REPORT\n");
      printf("============================================\n");
      printf(" Header Value        : %s\n", res->header);
      printf(" Fixed Array Items   : %u structural elements\n", res->fixed_array_count);
      printf("   -> Item[0]        : X=%.1f, Y=%.1f\n", res->fixed_array[0].x, res->fixed_array[0].y);
      printf("   -> Item[1]        : X=%.1f, Y=%.1f\n", res->fixed_array[1].x, res->fixed_array[1].y);
      printf(" Variable Array Items: %u structural elements\n", res->var_array_count);
      printf("   -> Item[0]        : Name='%s', ID=%" PRId64 "\n", res->var_array[0].name, res->var_array[0].id);
      printf("   -> Item[1]        : Name='%s', ID=%" PRId64 "\n", res->var_array[1].name, res->var_array[1].id);
      printf("============================================\n");
   }

   /* 7. Graceful memory block cleanups */
   type_free(generic_output);
   type_dynamic_free(descriptor_output);
   type_free(bin_stream);
   type_free(cloned);
   printf("\n [Clean] Memory resources wiped out. Zero fragmentation.\n");

   return 0;
}
