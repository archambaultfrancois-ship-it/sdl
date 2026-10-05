#include "sdl_wire.h"

uint32_t sdl_wire_read_u32(const uint8_t *buffer) {
   return (uint32_t)buffer[0] | ((uint32_t)buffer[1] << 8) |
      ((uint32_t)buffer[2] << 16) | ((uint32_t)buffer[3] << 24);
}

void sdl_wire_write_u32(uint8_t *buffer, uint32_t value) {
   buffer[0] = (uint8_t)value;
   buffer[1] = (uint8_t)(value >> 8);
   buffer[2] = (uint8_t)(value >> 16);
   buffer[3] = (uint8_t)(value >> 24);
}

static int host_is_little_endian(void) {
   const uint16_t marker = 1;
   return *((const uint8_t *)&marker) == 1;
}

void sdl_wire_encode_native(uint8_t *wire, const void *native_value,
   size_t size) {
   const uint8_t *native_bytes = (const uint8_t *)native_value;
   size_t i;
   for (i = 0; i < size; ++i)
      wire[i] = native_bytes[host_is_little_endian() ? i : size - i - 1];
}

void sdl_wire_decode_native(void *native_value, const uint8_t *wire,
   size_t size) {
   uint8_t *native_bytes = (uint8_t *)native_value;
   size_t i;
   for (i = 0; i < size; ++i)
      native_bytes[i] = wire[host_is_little_endian() ? i : size - i - 1];
}
