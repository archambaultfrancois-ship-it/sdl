#include "type_private.h"
#include "sdl_wire.h"

#include <complex.h>
#include <stdlib.h>
#include <string.h>

const SdlTypeDesc SDL_BOOL_DESC = { SDL_TYPE_BOOL, sizeof(bool), sizeof(bool), "bool", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_INT8_DESC = { SDL_TYPE_INT8, sizeof(int8_t), sizeof(int8_t), "int8", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_INT16_DESC = { SDL_TYPE_INT16, sizeof(int16_t), sizeof(int16_t), "int16", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_INT32_DESC = { SDL_TYPE_INT32, sizeof(int32_t), sizeof(int32_t), "int32", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_INT64_DESC = { SDL_TYPE_INT64, sizeof(int64_t), sizeof(int64_t), "int64", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_FLOAT32_DESC = { SDL_TYPE_FLOAT32, sizeof(float), sizeof(float), "float32", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_FLOAT64_DESC = { SDL_TYPE_FLOAT64, sizeof(double), sizeof(double), "float64", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_COMPLEX32_DESC = { SDL_TYPE_COMPLEX32, sizeof(float complex), sizeof(float complex), "complex32", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_COMPLEX64_DESC = { SDL_TYPE_COMPLEX64, sizeof(double complex), sizeof(double complex), "complex64", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_ENUM_DESC = { SDL_TYPE_ENUM, sizeof(int32_t), sizeof(int32_t), "enum", 0, NULL, 0, { { 0, NULL } } };
const SdlTypeDesc SDL_STRING_DESC = { SDL_TYPE_STRING, sizeof(char *), sizeof(char *), "string", 0, NULL, 0, { { 0, NULL } } };

static size_t encoded_value_size(const SdlTypeDesc *type, const void *value);

static size_t encoded_struct_size(const SdlTypeDesc *type, const void *value) {
   size_t total = 0, i;
   for (i = 0; i < type->detail.structure.field_count; ++i) {
      const SdlFieldDesc *field = &type->detail.structure.fields[i];
      size_t count = 1, item;
      const void *data = (const uint8_t *)value + field->offset;
      if (field->presence_offset != SDL_NO_OFFSET &&
          !*((const bool *)((const uint8_t *)value + field->presence_offset)))
         continue;
      if ((field->flags & SDL_FIELD_REPEATED) != 0) {
         count = *(const uint32_t *)((const uint8_t *)value + field->count_offset);
         data = *(const void * const *)data;
         if (count != 0 && data == NULL) return 0;
         if ((field->flags & SDL_FIELD_PACKED) != 0) {
            size_t fixed_size = sdl_fixed_wire_size(field->type);
            size_t payload_size;
            if (count == 0) continue;
            if (fixed_size == 0 || count > SIZE_MAX / fixed_size)
               return 0;
            payload_size = (size_t)count * fixed_size;
            if (payload_size > UINT32_MAX || total > SIZE_MAX - 8 ||
                payload_size > SIZE_MAX - total - 8)
               return 0;
            total += 8 + payload_size;
            continue;
         }
      }
      for (item = 0; item < count; ++item) {
         const void *element = (field->flags & SDL_FIELD_REPEATED) != 0 ?
            (const uint8_t *)data + item * field->type->size : data;
         size_t payload = encoded_value_size(field->type, element);
         if (payload == SIZE_MAX || payload > SIZE_MAX - total - 8)
            return 0;
         total += 8 + payload;
      }
   }
   return total;
}

static size_t encoded_value_size(const SdlTypeDesc *type, const void *value) {
   if (type->kind == SDL_TYPE_STRING) {
      const char *string = *(const char * const *)value;
      size_t n = 0;
      if (string == NULL) return 0;
      while (string[n] != '\0') { if (n == SIZE_MAX - 1) return SIZE_MAX; ++n; }
      return n;
   }
   if (type->kind == SDL_TYPE_STRUCT) return encoded_struct_size(type, value);
   if (type->kind == SDL_TYPE_ARRAY) return sdl_fixed_wire_size(type);
   if (type->kind == SDL_TYPE_ENUM) return 4;
   if (type->kind == SDL_TYPE_COMPLEX32) return 8;
   if (type->kind == SDL_TYPE_COMPLEX64) return 16;
   return type->size;
}

size_t type_encode_size(const char *name, const void *decoded) {
   const SdlTypeDesc *type = sdl_lookup_type(name);
   size_t payload, descriptor_size;
   if (type == NULL || decoded == NULL || type->schema_descriptor == NULL) return 0;
   descriptor_size = type->schema_descriptor_size;
   if (descriptor_size > UINT32_MAX || descriptor_size > SIZE_MAX - 8)
      return 0;
   payload = encoded_struct_size(type, decoded);
   return payload > SIZE_MAX - descriptor_size - 8 ? 0 : payload + descriptor_size + 8;
}

void *type_encode(const char *name, const void *decoded, size_t *size) {
   const SdlTypeDesc *type;
   size_t capacity, written, descriptor_size;
   uint8_t *buffer;
   if (size == NULL) return NULL;
   *size = 0;
   type = sdl_lookup_type(name);
   if (type == NULL || decoded == NULL) return NULL;
   capacity = type_encode_size(name, decoded);
   if (capacity == 0) return NULL;
   buffer = (uint8_t *)malloc(capacity);
   if (buffer == NULL) return NULL;
   descriptor_size = type->schema_descriptor_size;
   sdl_wire_write_u32(buffer, (uint32_t)descriptor_size);
   memcpy(buffer + 4, type->schema_descriptor, descriptor_size);
   sdl_wire_write_u32(buffer + 4 + descriptor_size, type->hash);
   written = sdl_value_encode(type, decoded, buffer + 8 + descriptor_size,
      capacity - 8 - descriptor_size);
   if (written == SIZE_MAX || written + 8 + descriptor_size != capacity) {
      free(buffer);
      return NULL;
   }
   *size = capacity;
   return buffer;
}

size_t type_decode_size(const void *encoded, size_t size) {
   const uint8_t *buffer = (const uint8_t *)encoded;
   const SdlTypeDesc *type;
   size_t extra, descriptor_size, header_size;
   uint32_t hash;
   if (buffer == NULL || size < 8) return 0;
   descriptor_size = sdl_wire_read_u32(buffer);
   if (descriptor_size > size - 8) return 0;
   header_size = descriptor_size + 8;
   hash = sdl_wire_read_u32(buffer + 4 + descriptor_size);
   type = sdl_lookup_hash(hash);
   if (type == NULL || type->schema_descriptor == NULL ||
       type->schema_descriptor_size != descriptor_size ||
       memcmp(buffer + 4, type->schema_descriptor, descriptor_size) != 0)
      return 0;
   extra = sdl_value_decode_measure(type, buffer + header_size, size - header_size);
   if (extra == SIZE_MAX || type->size > SIZE_MAX - extra) return 0;
   return type->size + extra;
}

void *type_decode(const void *encoded, size_t *size) {
   const uint8_t *buffer = (const uint8_t *)encoded;
   const SdlTypeDesc *type;
   void *block;
   size_t total, offset = 0;
   uint32_t hash, descriptor_size;
   size_t header_size;
   if (buffer == NULL || size == NULL || *size < 8) return NULL;
   descriptor_size = sdl_wire_read_u32(buffer);
   if (descriptor_size > *size - 8) return NULL;
   header_size = (size_t)descriptor_size + 8;
   hash = sdl_wire_read_u32(buffer + 4 + descriptor_size);
   type = sdl_lookup_hash(hash);
   total = type_decode_size(encoded, *size);
   if (type == NULL || total == 0) return NULL;
   block = calloc(1, total);
   if (block == NULL) return NULL;
   if (!sdl_value_decode(type, buffer + header_size, *size - header_size, block,
         (uint8_t *)block + type->size, &offset)) {
      free(block);
      return NULL;
   }
   return block;
}

void *type_clone(const char *name, const void *decoded) {
   const SdlTypeDesc *type = sdl_lookup_type(name);
   size_t extra;
   void *block;
   size_t offset = 0;
   if (type == NULL || decoded == NULL) return NULL;
   extra = sdl_value_measure(type, decoded);
   if (extra == SIZE_MAX || type->size > SIZE_MAX - extra) return NULL;
   block = calloc(1, type->size + extra);
   if (block == NULL) return NULL;
   sdl_value_clone(type, decoded, block, (uint8_t *)block + type->size, &offset);
   return block;
}

void type_free(void *ptr) { free(ptr); }
