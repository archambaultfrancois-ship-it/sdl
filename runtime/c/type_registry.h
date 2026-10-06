#ifndef SDL_TYPE_REGISTRY_H
#define SDL_TYPE_REGISTRY_H

#include "type_descriptors.h"
#include <stdbool.h>

/* Registers a static descriptor emitted by the schema generator. */
bool sdl_register_type(const SdlTypeDesc *type);

#endif /* SDL_TYPE_REGISTRY_H */
