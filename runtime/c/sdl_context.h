#ifndef SDL_CONTEXT_H
#define SDL_CONTEXT_H
#include "type_descriptors.h"
#include "sdl_dynamic.h"
#include <stdbool.h>

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
/* Foreign bindings: field IDs, rather than names, determine compatibility. */
bool type_context_validate(const SdlContext *context, const void *data, size_t size);
uint32_t type_context_message_id(const SdlContext *context, const char *name);
bool type_context_compatible(const SdlContext *remote, const SdlContext *local);
/* Caller owns the returned byte flags indexed by remote message ID minus one. */
uint8_t *type_context_direct_layouts(const SdlContext *remote, const SdlContext *local, size_t *count);
bool type_context_exact_message(const SdlContext *remote, const SdlContext *local, const char *name);
const SdlDynamicValue *type_dynamic_get_id(const SdlContext *context, const SdlDynamicMessage *message, uint32_t id);
#endif
