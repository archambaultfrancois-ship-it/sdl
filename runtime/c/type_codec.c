#include "type_private.h"
#include "sdl_wire.h"

#include <complex.h>
#include <limits.h>
#include <string.h>

/* SDL2 numeric values always use big endian. */

static size_t align_pool_offset(const uint8_t *pool, size_t value, size_t alignment) {
   size_t remainder;
   if (alignment <= 1)
      return value;
   remainder = (size_t)((uintptr_t)(pool + value) % alignment);
   if (remainder != 0 && value > SIZE_MAX - (alignment - remainder))
      return SIZE_MAX;
   return remainder == 0 ? value : value + alignment - remainder;
}

static size_t string_length(const char *value) {
   size_t length = 0;
   while (value[length] != '\0') {
      if (length == SIZE_MAX - 1)
         return SIZE_MAX;
      ++length;
   }
   return length;
}

static const void *field_value(const SdlFieldDesc *field, const void *object) {
   return (const uint8_t *)object + field->offset;
}

static bool field_present(const SdlFieldDesc *field, const void *object) {
   return field->presence_offset == SDL_NO_OFFSET ||
          *((const bool *)((const uint8_t *)object + field->presence_offset));
}

static size_t field_string_length(const SdlFieldDesc *field, const void *object, size_t index) {
   const uint8_t *base = (const uint8_t *)object;
   uint32_t length;
   if (field->string_length_offset == SDL_NO_OFFSET)
      return SIZE_MAX;
   if ((field->flags & SDL_FIELD_REPEATED) != 0) {
      const uint32_t *lengths = *(const uint32_t *const *)(base + field->string_length_offset);
      if (lengths == NULL)
         return SIZE_MAX;
      length = lengths[index];
   } else {
      length = *(const uint32_t *)(base + field->string_length_offset);
   }
   return length == 0 ? SIZE_MAX : (size_t)length;
}

static bool enum_value_valid(const SdlTypeDesc *type, int32_t value) {
   size_t index;
   for (index = 0; index < type->detail.enumeration.value_count; ++index)
      if (type->detail.enumeration.values[index].value == value)
         return true;
   return false;
}

static size_t fixed_wire_size_depth(const SdlTypeDesc *type, const SdlTypeDesc **active,
                                    size_t depth) {
   size_t size;
   size_t i;
   if (depth >= 64)
      return 0;
   if (type->kind == SDL_TYPE_STRUCT || type->kind == SDL_TYPE_ARRAY) {
      for (i = 0; i < depth; ++i)
         if (active[i] == type)
            return 0;
      active[depth] = type;
   }
   switch (type->kind) {
   case SDL_TYPE_BOOL:
   case SDL_TYPE_INT8:
      return 1;
   case SDL_TYPE_INT16:
      return 2;
   case SDL_TYPE_INT32:
   case SDL_TYPE_FLOAT32:
      return 4;
   case SDL_TYPE_INT64:
   case SDL_TYPE_FLOAT64:
      return 8;
   case SDL_TYPE_COMPLEX32:
      return 8;
   case SDL_TYPE_COMPLEX64:
      return 16;
   case SDL_TYPE_ENUM:
      return 4;
   case SDL_TYPE_STRUCT:
      break;
   case SDL_TYPE_ARRAY:
      size = fixed_wire_size_depth(type->detail.array.element, active, depth + 1);
      if (size == 0 || type->detail.array.count > SIZE_MAX / size)
         return 0;
      return size * type->detail.array.count;
   default:
      return 0;
   }
   size = 0;
   for (i = 0; i < type->detail.structure.field_count; ++i) {
      const SdlFieldDesc *field = &type->detail.structure.fields[i];
      size_t field_size;
      if ((field->flags & (SDL_FIELD_OPTIONAL | SDL_FIELD_REPEATED)) != 0)
         return 0;
      field_size = fixed_wire_size_depth(field->type, active, depth + 1);
      if (field_size == 0 || size > SIZE_MAX - field_size)
         return 0;
      size += field_size;
   }
   return size;
}

size_t sdl_fixed_wire_size(const SdlTypeDesc *type) {
   const SdlTypeDesc *active[64];
   return fixed_wire_size_depth(type, active, 0);
}

bool sdl_value_encode_fixed(const SdlTypeDesc *type, const void *value, uint8_t *buffer,
                            size_t capacity) {
   size_t size = sdl_fixed_wire_size(type);
   size_t offset = 0;
   size_t i;
   if (size == 0 || capacity < size)
      return false;
   if (type->kind == SDL_TYPE_STRUCT) {
      for (i = 0; i < type->detail.structure.field_count; ++i) {
         const SdlFieldDesc *field = &type->detail.structure.fields[i];
         size_t field_size = sdl_fixed_wire_size(field->type);
         if (!sdl_value_encode_fixed(field->type, (const uint8_t *)value + field->offset,
                                     buffer + offset, capacity - offset))
            return false;
         offset += field_size;
      }
      return true;
   }
   if (type->kind == SDL_TYPE_ARRAY) {
      const SdlTypeDesc *element = type->detail.array.element;
      size_t element_wire_size = sdl_fixed_wire_size(element);
      for (i = 0; i < type->detail.array.count; ++i)
         if (!sdl_value_encode_fixed(element, (const uint8_t *)value + i * element->size,
                                     buffer + i * element_wire_size,
                                     capacity - i * element_wire_size))
            return false;
      return true;
   }
   if (type->kind == SDL_TYPE_ENUM) {
      int32_t enum_value = 0;
      if (type->size == 1) {
         int8_t part;
         memcpy(&part, value, 1);
         enum_value = part;
      } else if (type->size == 2) {
         int16_t part;
         memcpy(&part, value, 2);
         enum_value = part;
      } else if (type->size == 4) {
         memcpy(&enum_value, value, 4);
      } else {
         return false;
      }
      if (!enum_value_valid(type, enum_value))
         return false;
      sdl_wire_write_u32(buffer, (uint32_t)enum_value);
      return true;
   }
   if (type->kind == SDL_TYPE_BOOL) {
      buffer[0] = *(const bool *)value ? 1U : 0U;
      return true;
   }
   if (type->kind == SDL_TYPE_COMPLEX32) {
      float complex complex_value;
      float parts[2];
      uint32_t bits;
      memcpy(&complex_value, value, sizeof(complex_value));
      parts[0] = crealf(complex_value);
      parts[1] = cimagf(complex_value);
      memcpy(&bits, &parts[0], 4);
      sdl_wire_write_u32(buffer, bits);
      memcpy(&bits, &parts[1], 4);
      sdl_wire_write_u32(buffer + 4, bits);
      return true;
   }
   if (type->kind == SDL_TYPE_COMPLEX64) {
      double complex complex_value;
      double parts[2];
      memcpy(&complex_value, value, sizeof(complex_value));
      parts[0] = creal(complex_value);
      parts[1] = cimag(complex_value);
      sdl_wire_encode_native(buffer, &parts[0], 8);
      sdl_wire_encode_native(buffer + 8, &parts[1], 8);
      return true;
   }
   sdl_wire_encode_native(buffer, value, size);
   return true;
}

bool sdl_value_decode_fixed(const SdlTypeDesc *type, const uint8_t *buffer, void *value) {
   size_t size = sdl_fixed_wire_size(type);
   size_t offset = 0;
   size_t i;
   if (size == 0)
      return false;
   if (type->kind == SDL_TYPE_STRUCT) {
      for (i = 0; i < type->detail.structure.field_count; ++i) {
         const SdlFieldDesc *field = &type->detail.structure.fields[i];
         size_t field_size = sdl_fixed_wire_size(field->type);
         if (!sdl_value_decode_fixed(field->type, buffer + offset,
                                     (uint8_t *)value + field->offset))
            return false;
         offset += field_size;
      }
      return true;
   }
   if (type->kind == SDL_TYPE_ARRAY) {
      const SdlTypeDesc *element = type->detail.array.element;
      size_t element_wire_size = sdl_fixed_wire_size(element);
      for (i = 0; i < type->detail.array.count; ++i) {
         if (!sdl_value_decode_fixed(element, buffer + i * element_wire_size,
                                     (uint8_t *)value + i * element->size))
            return false;
      }
      return true;
   }
   if (type->kind == SDL_TYPE_ENUM) {
      int32_t enum_value;
      sdl_wire_decode_native(&enum_value, buffer, 4);
      if (!enum_value_valid(type, enum_value))
         return false;
      if (type->size == 1) {
         int8_t part;
         if (enum_value < INT8_MIN || enum_value > INT8_MAX)
            return false;
         part = (int8_t)enum_value;
         memcpy(value, &part, 1);
      } else if (type->size == 2) {
         int16_t part;
         if (enum_value < INT16_MIN || enum_value > INT16_MAX)
            return false;
         part = (int16_t)enum_value;
         memcpy(value, &part, 2);
      } else if (type->size == 4) {
         memcpy(value, &enum_value, 4);
      } else {
         return false;
      }
      return true;
   }
   if (type->kind == SDL_TYPE_BOOL) {
      if (buffer[0] > 1)
         return false;
      *(bool *)value = buffer[0] != 0;
      return true;
   }
   if (type->kind == SDL_TYPE_COMPLEX32) {
      uint32_t bits;
      float parts[2];
      float complex complex_value;
      bits = sdl_wire_read_u32(buffer);
      memcpy(&parts[0], &bits, 4);
      bits = sdl_wire_read_u32(buffer + 4);
      memcpy(&parts[1], &bits, 4);
      memcpy(&complex_value, parts, sizeof(complex_value));
      memcpy(value, &complex_value, sizeof(complex_value));
      return true;
   }
   if (type->kind == SDL_TYPE_COMPLEX64) {
      double parts[2];
      double complex complex_value;
      sdl_wire_decode_native(&parts[0], buffer, 8);
      sdl_wire_decode_native(&parts[1], buffer + 8, 8);
      memcpy(&complex_value, parts, sizeof(complex_value));
      memcpy(value, &complex_value, sizeof(complex_value));
      return true;
   }
   sdl_wire_decode_native(value, buffer, size);
   return true;
}

static size_t value_measure(const SdlTypeDesc *type, const void *value, size_t string_length);

static size_t struct_measure(const SdlTypeDesc *type, const void *value) {
   size_t total = 0;
   size_t i;
   for (i = 0; i < type->detail.structure.field_count; ++i) {
      const SdlFieldDesc *field = &type->detail.structure.fields[i];
      if (!field_present(field, value))
         continue;
      if ((field->flags & SDL_FIELD_REPEATED) != 0) {
         uint32_t count = *(const uint32_t *)((const uint8_t *)value + field->count_offset);
         const void *items = *(const void *const *)field_value(field, value);
         size_t item;
         if (count != 0 && items == NULL)
            return SIZE_MAX;
         if (field->type->size != 0 && count > SIZE_MAX / field->type->size)
            return SIZE_MAX;
         if (field->type->alignment > 1) {
            size_t padding = field->type->alignment - 1;
            if (total > SIZE_MAX - padding)
               return SIZE_MAX;
            total += padding;
         }
         if (total > SIZE_MAX - (size_t)count * field->type->size)
            return SIZE_MAX;
         total += (size_t)count * field->type->size;
         if (field->type->kind == SDL_TYPE_STRING && count != 0) {
            size_t alignment_padding = SDL_UINT32_ALIGNMENT - 1;
            if (total > SIZE_MAX - alignment_padding ||
                count > (SIZE_MAX - total - alignment_padding) / sizeof(uint32_t))
               return SIZE_MAX;
            total += alignment_padding + (size_t)count * sizeof(uint32_t);
         }
         for (item = 0; item < count; ++item) {
            size_t extra = value_measure(
                field->type, (const uint8_t *)items + item * field->type->size,
                field->type->kind == SDL_TYPE_STRING ? field_string_length(field, value, item)
                                                     : SIZE_MAX);
            if (extra == SIZE_MAX || total > SIZE_MAX - extra)
               return SIZE_MAX;
            total += extra;
         }
      } else {
         size_t extra = value_measure(field->type, field_value(field, value),
                                      field->type->kind == SDL_TYPE_STRING
                                          ? field_string_length(field, value, 0)
                                          : SIZE_MAX);
         if (extra == SIZE_MAX || total > SIZE_MAX - extra)
            return SIZE_MAX;
         total += extra;
      }
   }
   return total;
}

static size_t value_measure(const SdlTypeDesc *type, const void *value, size_t explicit_length) {
   if (type->kind == SDL_TYPE_STRING) {
      const char *string = *(const char *const *)value;
      size_t length = explicit_length;
      if (string == NULL)
         return length == SIZE_MAX ? 0 : SIZE_MAX;
      if (length == SIZE_MAX)
         length = string_length(string);
      return length == SIZE_MAX || length == SIZE_MAX - 1 ? SIZE_MAX : length + 1;
   }
   if (type->kind == SDL_TYPE_STRUCT)
      return struct_measure(type, value);
   if (type->kind == SDL_TYPE_ARRAY)
      return 0;
   return 0;
}

size_t sdl_value_measure(const SdlTypeDesc *type, const void *value) {
   return type == NULL || value == NULL ? SIZE_MAX : value_measure(type, value, SIZE_MAX);
}

static void clone_value(const SdlTypeDesc *type, const void *src, void *dst, uint8_t *pool,
                        size_t *pool_offset, size_t explicit_length);

static void clone_struct(const SdlTypeDesc *type, const void *src, void *dst, uint8_t *pool,
                         size_t *pool_offset) {
   size_t i;
   for (i = 0; i < type->detail.structure.field_count; ++i) {
      const SdlFieldDesc *field = &type->detail.structure.fields[i];
      void *target = (uint8_t *)dst + field->offset;
      const void *source = (const uint8_t *)src + field->offset;
      if (field->presence_offset != SDL_NO_OFFSET)
         *((bool *)((uint8_t *)dst + field->presence_offset)) =
             *((const bool *)((const uint8_t *)src + field->presence_offset));
      if (!field_present(field, src))
         continue;
      if ((field->flags & SDL_FIELD_REPEATED) != 0) {
         uint32_t count = *(const uint32_t *)((const uint8_t *)src + field->count_offset);
         const uint8_t *items = *(const uint8_t *const *)source;
         uint8_t *copies;
         const uint32_t *source_lengths = NULL;
         size_t item;
         *(uint32_t *)((uint8_t *)dst + field->count_offset) = count;
         if (field->type->kind == SDL_TYPE_STRING && field->string_length_offset != SDL_NO_OFFSET)
            source_lengths =
                *(const uint32_t *const *)((const uint8_t *)src + field->string_length_offset);
         if (count == 0 || items == NULL) {
            *(void **)target = NULL;
            if (field->type->kind == SDL_TYPE_STRING &&
                field->string_length_offset != SDL_NO_OFFSET)
               *(uint32_t **)((uint8_t *)dst + field->string_length_offset) = NULL;
            continue;
         }
         *pool_offset = align_pool_offset(pool, *pool_offset, field->type->alignment);
         if (*pool_offset == SIZE_MAX)
            return;
         copies = pool + *pool_offset;
         *(void **)target = copies;
         *pool_offset += (size_t)count * field->type->size;
         memset(copies, 0, (size_t)count * field->type->size);
         if (field->type->kind == SDL_TYPE_STRING && field->string_length_offset != SDL_NO_OFFSET) {
            uint32_t *lengths = NULL;
            if (source_lengths != NULL) {
               *pool_offset = align_pool_offset(pool, *pool_offset, SDL_UINT32_ALIGNMENT);
               if (*pool_offset == SIZE_MAX)
                  return;
               lengths = (uint32_t *)(pool + *pool_offset);
               memcpy(lengths, source_lengths, (size_t)count * sizeof(*lengths));
               *pool_offset += (size_t)count * sizeof(*lengths);
            }
            *(uint32_t **)((uint8_t *)dst + field->string_length_offset) = lengths;
         }
         for (item = 0; item < count; ++item)
            clone_value(field->type, items + item * field->type->size,
                        copies + item * field->type->size, pool, pool_offset,
                        field->type->kind == SDL_TYPE_STRING ? field_string_length(field, src, item)
                                                             : SIZE_MAX);
      } else {
         if (field->type->kind == SDL_TYPE_STRING && field->string_length_offset != SDL_NO_OFFSET)
            *(uint32_t *)((uint8_t *)dst + field->string_length_offset) =
                *(const uint32_t *)((const uint8_t *)src + field->string_length_offset);
         clone_value(field->type, source, target, pool, pool_offset,
                     field->type->kind == SDL_TYPE_STRING ? field_string_length(field, src, 0)
                                                          : SIZE_MAX);
      }
   }
}

static void clone_value(const SdlTypeDesc *type, const void *src, void *dst, uint8_t *pool,
                        size_t *pool_offset, size_t explicit_length) {
   if (type->kind == SDL_TYPE_STRING) {
      const char *string = *(const char *const *)src;
      if (string == NULL) {
         *(char **)dst = NULL;
      } else {
         size_t content_length = explicit_length;
         size_t allocation_size;
         char *copy;
         if (content_length == SIZE_MAX)
            content_length = string_length(string);
         if (content_length == SIZE_MAX || content_length == SIZE_MAX - 1)
            return;
         allocation_size = content_length + 1;
         copy = (char *)(pool + *pool_offset);
         memcpy(copy, string, content_length);
         copy[content_length] = '\0';
         *(char **)dst = copy;
         *pool_offset += allocation_size;
      }
   } else if (type->kind == SDL_TYPE_STRUCT) {
      clone_struct(type, src, dst, pool, pool_offset);
   } else {
      memcpy(dst, src, type->size);
   }
}

void sdl_value_clone(const SdlTypeDesc *type, const void *src, void *dst, uint8_t *pool,
                     size_t *pool_offset) {
   clone_value(type, src, dst, pool, pool_offset, SIZE_MAX);
}
