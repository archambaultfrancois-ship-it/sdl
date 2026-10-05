/* Public SDL runtime API. */
#ifndef SDL_TYPE_ENGINE_H
#define SDL_TYPE_ENGINE_H

#include <stddef.h>
/* These functions are the public message and buffer API. */
size_t type_encode_size(const char *type, const void *decoded);
void *type_encode(const char *type, const void *decoded, size_t *size);
size_t type_decode_size(const void *encoded, size_t size);
void *type_decode(const void *encoded, size_t *size);
void *type_clone(const char *type, const void *decoded);
void type_free(void *ptr);

#endif /* SDL_TYPE_ENGINE_H */
