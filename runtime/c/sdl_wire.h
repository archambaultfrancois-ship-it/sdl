#ifndef SDL_WIRE_H
#define SDL_WIRE_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#if defined(SDL_WIRE_LITTLE_ENDIAN) && defined(SDL_WIRE_BIG_ENDIAN)
#error "Select only one SDL wire byte order"
#endif

#if !defined(SDL_WIRE_LITTLE_ENDIAN) && !defined(SDL_WIRE_BIG_ENDIAN)
#define SDL_WIRE_BIG_ENDIAN 1
#endif

uint32_t sdl_wire_read_u32(const uint8_t *buffer);
void sdl_wire_write_u32(uint8_t *buffer, uint32_t value);
void sdl_wire_encode_native(uint8_t *wire, const void *native_value,
   size_t size);
void sdl_wire_decode_native(void *native_value, const uint8_t *wire,
   size_t size);
bool sdl_wire_valid_utf8(const uint8_t *data, size_t size);

#endif /* SDL_WIRE_H */
