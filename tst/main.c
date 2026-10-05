/* ============================================================================
   ADVANCED HYBRID ARRAYS BUILT-IN TEST SUITE
   ============================================================================ */

#include "type_engine.h"
#include "schema.h"
#include "codec_cases.h"
#include "sdl_registry.h"
#include "sdl_wire.h"
#include <assert.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void test_codec_cases(void) {
   CodecCases input;
   CodecCases absent;
   CodecCases *decoded;
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
   double measurements[2] = { 0.125, -1024.5 };
   const char *labels[3] = { "", "alpha", "omega" };
   float complex point_values[2];
   uint8_t unknown_field[8];
   const uint8_t unknown_payload[3] = { 0xA1, 0xB2, 0xC3 };
   uint8_t invalid_bool_field[9];
   uint8_t invalid_packed_field[9];
   bool bool_flags[3] = { true, false, true };

   sdl_wire_write_u32(unknown_field, 999);
   sdl_wire_write_u32(unknown_field + 4, 3);
   sdl_wire_write_u32(invalid_bool_field, 17);
   sdl_wire_write_u32(invalid_bool_field + 4, 1);
   invalid_bool_field[8] = 2;
   sdl_wire_write_u32(invalid_packed_field, 15);
   sdl_wire_write_u32(invalid_packed_field + 4, 1);
   invalid_packed_field[8] = 0;

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
   input.points_count = 2;
   memcpy(&point_values[0], points[0], sizeof(points[0]));
   memcpy(&point_values[1], points[1], sizeof(points[1]));
   input.points = point_values;
   input.required_enabled = true;
   input.has_optional_enabled = true;
   input.optional_enabled = false;
   input.bool_flags_count = 3;
   input.bool_flags = bool_flags;

   encoded = (uint8_t *)type_encode("CodecCases", &input, &encoded_size);
   assert(encoded != NULL);
   assert(encoded_size > 4);
   assert(type_decode_size(encoded, encoded_size - 1) == 0);

   extended_size = encoded_size + sizeof(unknown_field) + sizeof(unknown_payload);
   extended = (uint8_t *)malloc(extended_size);
   assert(extended != NULL);
   memcpy(extended, encoded, encoded_size);
   memcpy(extended + encoded_size, unknown_field, sizeof(unknown_field));
   memcpy(extended + encoded_size + sizeof(unknown_field), unknown_payload,
      sizeof(unknown_payload));
   decode_size = extended_size;
   decoded = (CodecCases *)type_decode(extended, &decode_size);
   assert(decoded != NULL);

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
   assert(decoded->samples_count == 3);
   assert(memcmp(decoded->samples, samples, sizeof(samples)) == 0);
   assert(decoded->measurements_count == 2);
   assert(memcmp(decoded->measurements, measurements, sizeof(measurements)) == 0);
   assert(decoded->labels_count == 3);
   assert(strcmp(decoded->labels[0], "") == 0);
   assert(strcmp(decoded->labels[1], "alpha") == 0);
   assert(strcmp(decoded->labels[2], "omega") == 0);
   assert(decoded->points_count == 2);
   assert(memcmp(decoded->points, point_values, sizeof(point_values)) == 0);
   assert(decoded->empty_values_count == 0);
   assert(decoded->empty_values == NULL);
   assert(decoded->required_enabled);
   assert(decoded->has_optional_enabled && !decoded->optional_enabled);
   assert(decoded->bool_flags_count == 3);
   assert(decoded->bool_flags[0] && !decoded->bool_flags[1] && decoded->bool_flags[2]);
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
   assert(decoded->samples_count == 0 && decoded->samples == NULL);
   assert(decoded->measurements_count == 0 && decoded->measurements == NULL);
   assert(decoded->labels_count == 0 && decoded->labels == NULL);
   assert(decoded->points_count == 0 && decoded->points == NULL);
   assert(decoded->empty_values_count == 0 && decoded->empty_values == NULL);
   type_free(decoded);
   type_free(encoded);
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
   assert(expected_size == wire_size);
   if (memcmp(wire, expected, wire_size) != 0) {
      const uint8_t *actual = (const uint8_t *)wire;
      size_t i;
      for (i = 0; i < wire_size; ++i) {
         if (actual[i] != expected[i]) {
            fprintf(stderr, "wire mismatch at %lu: %02X != %02X\n",
               (unsigned long)i, actual[i], expected[i]);
            break;
         }
      }
   }
   assert(memcmp(wire, expected, wire_size) == 0);
}

int main(void) {
   /* 1. Startup registry initialization */
   register_all_types();
   test_codec_cases();
   printf(" [Tests] Scalar, optional, array and malformed-wire cases... OK\n");
   printf(" [Boot] Schema dynamic registration completed\n");
   printf(" [Info] ROOTPAYLOAD_HASH is: 0x%08X\n\n", ROOTPAYLOAD_HASH);

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
   printf(" 3. Blind Type-Agnostic Decoding..... OK\n");

   /* 6. Verify data integrity */
   const uint8_t* wire_bytes = (const uint8_t*)bin_stream;
   uint32_t stream_hash = sdl_wire_read_u32(wire_bytes);
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
   type_free(bin_stream);
   type_free(cloned);
   printf("\n [Clean] Memory resources wiped out. Zero fragmentation.\n");

   return 0;
}
