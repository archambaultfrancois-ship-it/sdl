/* Public SDL runtime API. */
#ifndef SDL_TYPE_ENGINE_H
#define SDL_TYPE_ENGINE_H

#include <stddef.h>
#include "sdl_dynamic.h"
/* Return the encoded frame size, or zero if the type is unknown or sizing fails. */
size_t type_encode_size(const char *type, const void *decoded);
/* Encode a registered message. On success, *size receives the frame size.
 * The returned buffer is owned by the caller and released with type_free(). */
void *type_encode(const char *type, const void *decoded, size_t *size);
/* Return the memory size required for a decoded message, or zero if invalid. */
size_t type_decode_size(const void *encoded, size_t size);
/* Decode one complete frame from encoded; *size is the available input length
 * and is not modified. The returned message and dynamic fields share one
 * allocation released by type_free(). */
void *type_decode(const void *encoded, size_t *size);
/* Deep-clone a registered in-memory message into one allocation. */
void *type_clone(const char *type, const void *decoded);
/* Display a registered in-memory message. Indentation is spaces per level.
 * Returns NULL for an unknown type, invalid value, or allocation failure.
 * The caller owns the result and may release it with type_free(). */
char *type_display(const char *type, const void *decoded, size_t indent_width);
void type_free(void *ptr);

#endif /* SDL_TYPE_ENGINE_H */
