#ifndef SDL_WIRE_H
#define SDL_WIRE_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

uint32_t sdl_wire_read_u32(const uint8_t *buffer);
void sdl_wire_write_u32(uint8_t *buffer, uint32_t value);
void sdl_wire_encode_native(uint8_t *wire, const void *native_value, size_t size);
void sdl_wire_decode_native(void *native_value, const uint8_t *wire, size_t size);
/* Convert contiguous scalar components, including complex real/imag pairs. */
void sdl_wire_convert_array(void *dst, const void *src, size_t width, size_t count);
bool sdl_wire_valid_utf8(const uint8_t *data, size_t size);

#endif /* SDL_WIRE_H */
