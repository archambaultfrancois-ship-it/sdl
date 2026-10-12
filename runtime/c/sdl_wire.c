#include "sdl_wire.h"

uint32_t sdl_wire_read_u32(const uint8_t *buffer) {
#ifdef SDL_WIRE_LITTLE_ENDIAN
   return (uint32_t)buffer[0] | ((uint32_t)buffer[1] << 8) |
      ((uint32_t)buffer[2] << 16) | ((uint32_t)buffer[3] << 24);
#else
   return ((uint32_t)buffer[0] << 24) | ((uint32_t)buffer[1] << 16) |
      ((uint32_t)buffer[2] << 8) | (uint32_t)buffer[3];
#endif
}

void sdl_wire_write_u32(uint8_t *buffer, uint32_t value) {
#ifdef SDL_WIRE_LITTLE_ENDIAN
   buffer[0] = (uint8_t)value;
   buffer[1] = (uint8_t)(value >> 8);
   buffer[2] = (uint8_t)(value >> 16);
   buffer[3] = (uint8_t)(value >> 24);
#else
   buffer[0] = (uint8_t)(value >> 24);
   buffer[1] = (uint8_t)(value >> 16);
   buffer[2] = (uint8_t)(value >> 8);
   buffer[3] = (uint8_t)value;
#endif
}

static int host_is_little_endian(void) {
   const uint16_t marker = 1;
   return *((const uint8_t *)&marker) == 1;
}

void sdl_wire_encode_native(uint8_t *wire, const void *native_value,
   size_t size) {
   const uint8_t *native_bytes = (const uint8_t *)native_value;
   size_t i;
   const int reverse = host_is_little_endian() !=
#ifdef SDL_WIRE_LITTLE_ENDIAN
      1;
#else
      0;
#endif
   for (i = 0; i < size; ++i)
      wire[i] = native_bytes[reverse ? size - i - 1 : i];
}

void sdl_wire_decode_native(void *native_value, const uint8_t *wire,
   size_t size) {
   uint8_t *native_bytes = (uint8_t *)native_value;
   size_t i;
   const int reverse = host_is_little_endian() !=
#ifdef SDL_WIRE_LITTLE_ENDIAN
      1;
#else
      0;
#endif
   for (i = 0; i < size; ++i)
      native_bytes[i] = wire[reverse ? size - i - 1 : i];
}
