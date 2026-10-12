#ifndef SDL_CONTEXT_H
#define SDL_CONTEXT_H
#include "type_descriptors.h"
#include "sdl_dynamic.h"

typedef struct SdlContext SdlContext;
/* Caller owns descriptions and contexts. Description size excludes NUL. */
char *type_description(const char *const *types, size_t count, size_t *size);
SdlContext *type_prepare(const void *description, size_t size);
void type_context_free(SdlContext *context);
size_t type_encode_size(const SdlContext *context, const char *type, const void *value);
void *type_encode(const SdlContext *context, const char *type, const void *value, size_t *size);
size_t type_decode_size(const SdlContext *context, const void *data, size_t size);
void *type_decode(const SdlContext *context, const void *data, size_t size);
const char *type_message_name(const SdlContext *context, const void *data, size_t size);
SdlDynamicMessage *type_decode_dynamic(const SdlContext *context, const void *data, size_t size);
#endif
