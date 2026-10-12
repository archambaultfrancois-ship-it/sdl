#include "sdl_wire.h"

#include <float.h>
#include <limits.h>
#include <string.h>

#if CHAR_BIT != 8
#error "SDL C runtime requires 8-bit bytes"
#endif

#if FLT_RADIX != 2 || FLT_MANT_DIG != 24 || FLT_MIN_EXP != -125 || FLT_MAX_EXP != 128
#error "SDL C runtime requires the binary32 float model"
#endif

#if DBL_MANT_DIG != 53 || DBL_MIN_EXP != -1021 || DBL_MAX_EXP != 1024
#error "SDL C runtime requires the binary64 double model"
#endif

typedef char sdl_wire_float_must_be_4_bytes[(sizeof(float) == 4) ? 1 : -1];
typedef char sdl_wire_double_must_be_8_bytes[(sizeof(double) == 8) ? 1 : -1];

uint32_t sdl_wire_read_u32(const uint8_t *buffer) {
   return ((uint32_t)buffer[0] << 24) | ((uint32_t)buffer[1] << 16) | ((uint32_t)buffer[2] << 8) |
          (uint32_t)buffer[3];
}

void sdl_wire_write_u32(uint8_t *buffer, uint32_t value) {
   buffer[0] = (uint8_t)(value >> 24);
   buffer[1] = (uint8_t)(value >> 16);
   buffer[2] = (uint8_t)(value >> 8);
   buffer[3] = (uint8_t)value;
}

static int host_is_little_endian(void) {
   const uint16_t marker = 1;
   return *((const uint8_t *)&marker) == 1;
}

void sdl_wire_encode_native(uint8_t *wire, const void *native_value, size_t size) {
   const uint8_t *native_bytes = (const uint8_t *)native_value;
   size_t i;
   const int reverse = host_is_little_endian() != 0;

   for (i = 0; i < size; ++i)
      wire[i] = native_bytes[reverse ? size - i - 1 : i];
}

void sdl_wire_decode_native(void *native_value, const uint8_t *wire, size_t size) {
   uint8_t *native_bytes = (uint8_t *)native_value;
   size_t i;
   const int reverse = host_is_little_endian() != 0;

   for (i = 0; i < size; ++i)
      native_bytes[i] = wire[reverse ? size - i - 1 : i];
}

void sdl_wire_convert_array(void *dst, const void *src, size_t width, size_t count) {
   uint8_t *out = (uint8_t *)dst;
   const uint8_t *in = (const uint8_t *)src;
   size_t i;
   if (width == 1 || !host_is_little_endian()) {
      memcpy(out, in, width * count);
      return;
   }
   for (i = 0; i < count; ++i) {
      size_t j;
      for (j = 0; j < width; ++j)
         out[i * width + j] = in[i * width + width - j - 1];
   }
}

bool sdl_wire_valid_utf8(const uint8_t *data, size_t size) {
   size_t offset = 0;
   while (offset < size) {
      uint8_t first = data[offset++];
      uint32_t value;
      size_t continuation;
      size_t index;
      if (first < 0x80U)
         continue;
      if (first >= 0xC2U && first <= 0xDFU) {
         value = first & 0x1FU;
         continuation = 1;
      } else if (first >= 0xE0U && first <= 0xEFU) {
         value = first & 0x0FU;
         continuation = 2;
      } else if (first >= 0xF0U && first <= 0xF4U) {
         value = first & 0x07U;
         continuation = 3;
      } else {
         return false;
      }
      if (continuation > size - offset)
         return false;
      for (index = 0; index < continuation; ++index) {
         uint8_t next = data[offset++];
         if ((next & 0xC0U) != 0x80U)
            return false;
         value = (value << 6) | (next & 0x3FU);
      }
      if ((continuation == 1 && value < 0x80U) || (continuation == 2 && value < 0x800U) ||
          (continuation == 3 && value < 0x10000U) || (value >= 0xD800U && value <= 0xDFFFU) ||
          value > 0x10FFFFU)
         return false;
   }
   return true;
}
