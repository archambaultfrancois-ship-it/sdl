#ifndef SDL_TYPE_DESCRIPTORS_H
#define SDL_TYPE_DESCRIPTORS_H

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

struct SdlTypeDesc {
   SdlTypeKind kind;
   size_t size;
   size_t alignment;
   const char *name;
   uint32_t hash;
   const uint8_t *schema_descriptor;
   size_t schema_descriptor_size;
   union {
      struct {
         size_t field_count;
         const SdlFieldDesc *fields;
      } structure;
      struct {
         const SdlTypeDesc *element;
         size_t count;
      } array;
   } detail;
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

#define SDL_NO_OFFSET ((size_t)-1)
#define SDL_FIELD_OPTIONAL 0x01u
#define SDL_FIELD_REPEATED 0x02u
#define SDL_FIELD_PACKED 0x04u

#endif /* SDL_TYPE_DESCRIPTORS_H */
