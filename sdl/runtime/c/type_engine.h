/* Public SDL runtime API. */
#ifndef SDL_TYPE_ENGINE_H
#define SDL_TYPE_ENGINE_H

#include <stddef.h>
#include "sdl_dynamic.h"
#include "sdl_context.h"
/* Deep-clone a registered in-memory message into one allocation. */
void *type_clone(const char *type, const void *decoded);
/* Display a registered in-memory message. Indentation is spaces per level.
 * Returns NULL for an unknown type, invalid value, or allocation failure.
 * The caller owns the result and may release it with type_free(). */
char *type_display(const char *type, const void *decoded, size_t indent_width);
void type_free(void *ptr);

#endif /* SDL_TYPE_ENGINE_H */
