#include "radio_capture.h"
#include "sdl_registry.h"
#include "type_engine.h"
#include "sdl_wire.h"
#include <assert.h>
#include <complex.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static RadioCapture example_capture(float complex samples[3]) {
   static const float sample_parts[3][2] = {
      { 1.0f, 0.25f }, { -0.5f, 0.75f }, { 0.125f, -1.0f }
   };
   static const int32_t trigger_offsets[] = { 128, 4096, 12000 };
   RadioCapture value;
   memcpy(samples, sample_parts, sizeof(sample_parts));
   memset(&value, 0, sizeof(value));
   value.metadata.receiver_id = "north-ridge-rx-02";
   value.metadata.sequence = 8472;
   value.metadata.captured_at_ns = INT64_C(1780000000123456789);
   value.metadata.center_frequency_hz = 915000000.0;
   value.metadata.sample_rate_hz = 2400000.0f;
   value.metadata.mode = CAPTUREMODE_LIVE;
   value.metadata.dc_offset[0] = 0.001f;
   value.metadata.dc_offset[1] = -0.002f;
   value.has_operator_note = true;
   value.operator_note = "interference burst near channel edge";
   value.iq_samples_count = 3;
   value.iq_samples = (float complex *)samples;
   value.trigger_offsets_count = (uint32_t)(sizeof(trigger_offsets) /
      sizeof(trigger_offsets[0]));
   value.trigger_offsets = (int32_t *)trigger_offsets;
   return value;
}

static uint8_t *read_file(const char *path, size_t *size) {
   FILE *file = fopen(path, "rb");
   long file_size;
   uint8_t *data;
   if (file == NULL) return NULL;
   if (fseek(file, 0, SEEK_END) != 0 || (file_size = ftell(file)) < 0 ||
       fseek(file, 0, SEEK_SET) != 0) {
      fclose(file);
      return NULL;
   }
   data = (uint8_t *)malloc((size_t)file_size);
   if (data == NULL || fread(data, 1, (size_t)file_size, file) !=
         (size_t)file_size) {
      free(data);
      fclose(file);
      return NULL;
   }
   fclose(file);
   *size = (size_t)file_size;
   return data;
}

static int write_file(const char *path, const void *data, size_t size) {
   FILE *file = fopen(path, "wb");
   int success;
   if (file == NULL) return 0;
   success = fwrite(data, 1, size, file) == size;
   if (fclose(file) != 0) success = 0;
   return success;
}

static int check_capture(const void *wire, size_t wire_size, size_t indent_width,
   int show_display) {
   size_t decoded_size = wire_size;
   RadioCapture *decoded = (RadioCapture *)type_decode(wire, &decoded_size);
   SdlDynamicMessage *generic = type_decode_dynamic(wire, wire_size);
   const SdlDynamicValue *samples;
   int valid;
   if (decoded == NULL || generic == NULL) {
      type_free(decoded);
      type_dynamic_free(generic);
      return 0;
   }
   samples = type_dynamic_get(generic, "iq_samples");
   valid = strcmp(decoded->metadata.receiver_id, "north-ridge-rx-02") == 0 &&
      decoded->metadata.sequence == 8472 &&
      decoded->metadata.mode == CAPTUREMODE_LIVE &&
      decoded->metadata.dc_offset[1] == -0.002f &&
      decoded->has_operator_note &&
      strcmp(decoded->operator_note, "interference burst near channel edge") == 0 &&
      decoded->iq_samples_count == 3 &&
      crealf(decoded->iq_samples[1]) == -0.5f &&
      cimagf(decoded->iq_samples[1]) == 0.75f &&
      decoded->trigger_offsets_count == 3 &&
      decoded->trigger_offsets[2] == 12000 &&
      strcmp(generic->type_name, "RadioCapture") == 0 &&
      samples != NULL && samples->kind == SDL_DYNAMIC_ARRAY &&
      samples->value.array.count == 3 &&
      samples->value.array.items[1].kind == SDL_DYNAMIC_COMPLEX;
   if (valid) {
      printf("C decoded %zu I/Q samples and 3 trigger offsets (%zu wire bytes)\n",
         (size_t)decoded->iq_samples_count, wire_size);
      if (show_display) {
         char *rendered = type_display("RadioCapture", decoded, indent_width);
         if (rendered == NULL) valid = 0;
         else {
            fputs(rendered, stdout);
            type_free(rendered);
         }
      }
   }
   type_free(decoded);
   type_dynamic_free(generic);
   return valid;
}

int main(int argc, char **argv) {
   float complex sample_storage[3];
   RadioCapture input = example_capture(sample_storage);
   uint8_t *wire;
   size_t wire_size = 0;
   size_t indent_width = 3;
   int valid;
   const char *mode = argc > 1 ? argv[1] : "roundtrip";
   register_all_types();
   if ((strcmp(mode, "display") == 0 || strcmp(mode, "roundtrip") == 0) &&
       argc > 2) {
      char *end;
      unsigned long parsed = strtoul(argv[2], &end, 10);
      if (*argv[2] == '\0' || *end != '\0' || parsed > 64) return 2;
      indent_width = (size_t)parsed;
   }
   if (strcmp(mode, "display") == 0) {
      char *rendered = type_display("RadioCapture", &input, indent_width);
      if (rendered == NULL) return 2;
      fputs(rendered, stdout);
      type_free(rendered);
      return 0;
   }
   if (argc == 3 && strcmp(mode, "decode") == 0) {
      wire = read_file(argv[2], &wire_size);
      if (wire == NULL) return 2;
   } else {
      wire = (uint8_t *)type_encode("RadioCapture", &input, &wire_size);
      if (wire == NULL) return 2;
      if (argc == 3 && strcmp(mode, "encode") == 0) {
         valid = write_file(argv[2], wire, wire_size);
         if (valid) printf("C wrote %zu wire bytes to %s\n", wire_size, argv[2]);
         type_free(wire);
         return valid ? 0 : 2;
      }
   }
   valid = check_capture(wire, wire_size, indent_width,
      strcmp(mode, "decode") != 0);
   type_free(wire);
   return valid ? 0 : 1;
}
