#ifndef SDL_FILTER_NATIVE_H
#define SDL_FILTER_NATIVE_H
#include "sdl_filter.h"
#include "type_descriptors.h"
int sdl_filter_evaluate_native(const SdlFilter *, const SdlTypeDesc *, const void *);
const SdlTypeDesc *sdl_filter_native_descriptor(const char *);
#endif
