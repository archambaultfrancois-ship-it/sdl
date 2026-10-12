#ifndef SDL_CONTEXT_H
#define SDL_CONTEXT_H
#include "type_descriptors.h"
#include "sdl_dynamic.h"
#include <stdbool.h>

/* Optional fixed S2PS/2 envelope. Payload is opaque, nonempty SDL2 bytes.
 * Pack returns owned memory (type_free); unpack borrows from input.
 * Failure leaves output parameters unchanged. No catalogue resolution occurs. */
#define SDL_ENVELOPE_HEADER_SIZE 9
void *type_envelope_pack(uint32_t catalogue_id, const void *payload, size_t size, size_t *envelope_size);
bool type_envelope_unpack(const void *data, size_t size, uint32_t *catalogue_id,
                          const void **payload, size_t *payload_size);
typedef struct SdlContext SdlContext;
/* Caller owns descriptions and contexts. Description size excludes NUL. */
char *type_description(const char *const *types, size_t count, size_t *size);
/* First 32 SHA-256 bits of exact UTF-8 bytes (including embedded NUL).
 * No parsing/normalization; use type_description() output. Zero is a valid ID.
 * Returns false for invalid UTF-8, oversized input, or invalid pointers. */
bool type_catalogue_id(const void *description, size_t size, uint32_t *id);
/* Names follow 1-based wire IDs; enums are excluded. Borrowed until free. */
size_t type_context_message_count(const SdlContext *context);
const char *type_context_message_name(const SdlContext *context, uint32_t id);
/* Direction: consumer reads publisher. Diagnostics own their strings. */
typedef enum {
   SDL_COMPAT_COMPATIBLE = 0,
   SDL_COMPAT_INVALID_PUBLISHER_CATALOGUE = 1,
   SDL_COMPAT_INVALID_CONSUMER_CATALOGUE = 2,
   SDL_COMPAT_ROOT_UNKNOWN = 3,
   SDL_COMPAT_TYPE_KIND_MISMATCH = 4,
   SDL_COMPAT_FIELD_TYPE_MISMATCH = 5,
   SDL_COMPAT_FIELD_DIMENSIONS_MISMATCH = 6,
   SDL_COMPAT_FIELD_CARDINALITY_MISMATCH = 7,
   SDL_COMPAT_ENUM_VALUE_UNKNOWN = 8,
   SDL_COMPAT_REQUIRED_FIELD_MISSING = 9
} SdlCompatibilityCode;
typedef struct {
   SdlCompatibilityCode code;
   const char *message, *path, *publisher_type, *consumer_type;
   uint32_t field_id;
   int32_t enum_value;
} SdlCompatibilityDiagnostic;
/* root=NULL checks all publisher message declarations in wire ID order.
 * Invalid catalogues return structured diagnostics; allocation failure is NULL.
 * require_required is an optional business policy, not the SDL2 default. */
SdlCompatibilityDiagnostic *type_compare_catalogues(
   const void *publisher, size_t publisher_size, const void *consumer, size_t consumer_size,
   const char *root, bool require_required);
const char *type_compatibility_code_name(SdlCompatibilityCode code);
void type_compatibility_free(SdlCompatibilityDiagnostic *diagnostic);
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
