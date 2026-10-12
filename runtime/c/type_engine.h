/* Public SDL runtime API. */
#ifndef SDL_TYPE_ENGINE_H
#define SDL_TYPE_ENGINE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
   SDL_TYPE_BOOL,
   SDL_TYPE_INT8,
   SDL_TYPE_INT16,
   SDL_TYPE_INT32,
   SDL_TYPE_INT64,
   SDL_TYPE_FLOAT32,
   SDL_TYPE_FLOAT64,
   SDL_TYPE_COMPLEX32,
   SDL_TYPE_COMPLEX64,
   SDL_TYPE_ENUM,
   SDL_TYPE_STRING,
   SDL_TYPE_STRUCT,
   SDL_TYPE_ARRAY
} SdlTypeKind;

typedef struct SdlTypeDesc SdlTypeDesc;
typedef struct SdlFieldDesc SdlFieldDesc;

struct SdlFieldDesc {
   uint32_t id;
   const char *name;
   const SdlTypeDesc *type;
   size_t offset;
   size_t presence_offset;
   size_t count_offset;
   uint32_t flags;
};

extern const SdlTypeDesc SDL_BOOL_DESC;
extern const SdlTypeDesc SDL_INT8_DESC;
extern const SdlTypeDesc SDL_INT16_DESC;
extern const SdlTypeDesc SDL_INT32_DESC;
extern const SdlTypeDesc SDL_INT64_DESC;
extern const SdlTypeDesc SDL_FLOAT32_DESC;
extern const SdlTypeDesc SDL_FLOAT64_DESC;
extern const SdlTypeDesc SDL_COMPLEX32_DESC;
extern const SdlTypeDesc SDL_COMPLEX64_DESC;
extern const SdlTypeDesc SDL_ENUM_DESC;
extern const SdlTypeDesc SDL_STRING_DESC;

struct SdlTypeDesc {
   SdlTypeKind kind;
   size_t size;
   size_t alignment;
   const char *name;
   uint32_t hash;
   union {
      struct {
         size_t field_count;
         const SdlFieldDesc *fields;
      } structure;
      struct {
         const SdlTypeDesc *element;
      } array;
   } detail;
};

#define SDL_NO_OFFSET ((size_t)-1)
#define SDL_FIELD_OPTIONAL 0x01u
#define SDL_FIELD_REPEATED 0x02u

/* Registers a static, generated type descriptor. */
bool sdl_register_type(const SdlTypeDesc *type);

size_t type_encode_size(const char *type, const void *decoded);
void *type_encode(const char *type, const void *decoded, size_t *size);
size_t type_decode_size(const void *encoded, size_t size);
void *type_decode(const void *encoded, size_t *size);
void *type_clone(const char *type, const void *decoded);
void type_free(void *ptr);

#endif /* SDL_TYPE_ENGINE_H */
