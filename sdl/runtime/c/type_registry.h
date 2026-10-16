#ifndef SDL_TYPE_REGISTRY_H
#define SDL_TYPE_REGISTRY_H

#include "type_descriptors.h"
#include <stdbool.h>

/* Registers a static descriptor for the lifetime of the process. Re-registering
 * the same descriptor succeeds; conflicting names or allocation failure fail. */
bool sdl_register_type(const SdlTypeDesc *type);

#endif /* SDL_TYPE_REGISTRY_H */
