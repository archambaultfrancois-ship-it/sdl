/* Public SDL runtime API. */
#ifndef SDL_TYPE_ENGINE_H
#define SDL_TYPE_ENGINE_H

#include <stddef.h>
#include "sdl_dynamic.h"
/* These functions are the public message and buffer API. */
size_t type_encode_size(const char *type, const void *decoded);
void *type_encode(const char *type, const void *decoded, size_t *size);
size_t type_decode_size(const void *encoded, size_t size);
void *type_decode(const void *encoded, size_t *size);
void *type_clone(const char *type, const void *decoded);
/* Display a registered in-memory message. Indentation is spaces per level.
 * Returns NULL for an unknown type, invalid value, or allocation failure.
 * The caller owns the result and may release it with type_free(). */
char *type_display(const char *type, const void *decoded, size_t indent_width);
void type_free(void *ptr);

#endif /* SDL_TYPE_ENGINE_H */
