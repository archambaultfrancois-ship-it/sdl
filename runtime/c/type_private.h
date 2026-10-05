#ifndef SDL_TYPE_PRIVATE_H
#define SDL_TYPE_PRIVATE_H

#include "type_engine.h"
#include "type_descriptors.h"
#include <stdbool.h>

const SdlTypeDesc *sdl_lookup_type(const char *name);
const SdlTypeDesc *sdl_lookup_hash(uint32_t hash);
size_t sdl_value_measure(const SdlTypeDesc *type, const void *value);
void sdl_value_clone(const SdlTypeDesc *type, const void *src, void *dst,
   uint8_t *pool, size_t *pool_offset);
size_t sdl_value_encode(const SdlTypeDesc *type, const void *value,
   uint8_t *buffer, size_t capacity);
size_t sdl_value_decode_measure(const SdlTypeDesc *type,
   const uint8_t *buffer, size_t size);
bool sdl_value_decode(const SdlTypeDesc *type, const uint8_t *buffer,
   size_t size, void *value, uint8_t *pool, size_t *pool_offset);

#endif /* SDL_TYPE_PRIVATE_H */
