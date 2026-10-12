#include "type_private.h"
#include "sdl_wire.h"

#include <complex.h>
#include <limits.h>
#include <string.h>

/* SDL wire values, field IDs, and lengths use the configured byte order. */

static size_t align_pool_offset(const uint8_t *pool, size_t value,
   size_t alignment) {
   size_t remainder;
   if (alignment <= 1) return value;
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

static size_t fixed_wire_size_depth(const SdlTypeDesc *type,
   const SdlTypeDesc **active, size_t depth) {
   size_t size;
   size_t i;
   if (depth >= 64) return 0;
   if (type->kind == SDL_TYPE_STRUCT || type->kind == SDL_TYPE_ARRAY) {
      for (i = 0; i < depth; ++i)
         if (active[i] == type) return 0;
      active[depth] = type;
   }
   switch (type->kind) {
      case SDL_TYPE_BOOL:
      case SDL_TYPE_INT8: return 1;
      case SDL_TYPE_INT16: return 2;
      case SDL_TYPE_INT32:
      case SDL_TYPE_FLOAT32: return 4;
      case SDL_TYPE_INT64:
      case SDL_TYPE_FLOAT64: return 8;
      case SDL_TYPE_COMPLEX32: return 8;
      case SDL_TYPE_COMPLEX64: return 16;
      case SDL_TYPE_ENUM: return 4;
      case SDL_TYPE_STRUCT: break;
      case SDL_TYPE_ARRAY:
         size = fixed_wire_size_depth(type->detail.array.element, active, depth + 1);
         if (size == 0 || type->detail.array.count > SIZE_MAX / size)
            return 0;
         return size * type->detail.array.count;
      default: return 0;
   }
   size = 0;
   for (i = 0; i < type->detail.structure.field_count; ++i) {
      const SdlFieldDesc *field = &type->detail.structure.fields[i];
      size_t field_size;
      if ((field->flags & (SDL_FIELD_OPTIONAL | SDL_FIELD_REPEATED)) != 0)
         return 0;
      field_size = fixed_wire_size_depth(field->type, active, depth + 1);
      if (field_size == 0 || size > SIZE_MAX - field_size) return 0;
      size += field_size;
   }
   return size;
}

size_t sdl_fixed_wire_size(const SdlTypeDesc *type) {
   const SdlTypeDesc *active[64];
   return fixed_wire_size_depth(type, active, 0);
}

bool sdl_value_encode_fixed(const SdlTypeDesc *type, const void *value,
   uint8_t *buffer, size_t capacity) {
   size_t size = sdl_fixed_wire_size(type);
   size_t offset = 0;
   size_t i;
   if (size == 0 || capacity < size) return false;
   if (type->kind == SDL_TYPE_STRUCT) {
      for (i = 0; i < type->detail.structure.field_count; ++i) {
         const SdlFieldDesc *field = &type->detail.structure.fields[i];
         size_t field_size = sdl_fixed_wire_size(field->type);
         if (!sdl_value_encode_fixed(field->type,
               (const uint8_t *)value + field->offset, buffer + offset,
               capacity - offset)) return false;
         offset += field_size;
      }
      return true;
   }
   if (type->kind == SDL_TYPE_ARRAY) {
      const SdlTypeDesc *element = type->detail.array.element;
      size_t element_wire_size = sdl_fixed_wire_size(element);
      for (i = 0; i < type->detail.array.count; ++i)
         if (!sdl_value_encode_fixed(element,
               (const uint8_t *)value + i * element->size,
               buffer + i * element_wire_size, capacity - i * element_wire_size))
            return false;
      return true;
   }
   if (type->kind == SDL_TYPE_ENUM) {
      int32_t enum_value = 0;
      if (type->size == 1) {
         int8_t part; memcpy(&part, value, 1); enum_value = part;
      } else if (type->size == 2) {
         int16_t part; memcpy(&part, value, 2); enum_value = part;
      } else if (type->size == 4) {
         memcpy(&enum_value, value, 4);
      } else {
         return false;
      }
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
      memcpy(&bits, &parts[0], 4); sdl_wire_write_u32(buffer, bits);
      memcpy(&bits, &parts[1], 4); sdl_wire_write_u32(buffer + 4, bits);
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

bool sdl_value_decode_fixed(const SdlTypeDesc *type, const uint8_t *buffer,
   void *value) {
   size_t size = sdl_fixed_wire_size(type);
   size_t offset = 0;
   size_t i;
   if (size == 0) return false;
   if (type->kind == SDL_TYPE_STRUCT) {
      for (i = 0; i < type->detail.structure.field_count; ++i) {
         const SdlFieldDesc *field = &type->detail.structure.fields[i];
         size_t field_size = sdl_fixed_wire_size(field->type);
         if (!sdl_value_decode_fixed(field->type, buffer + offset,
               (uint8_t *)value + field->offset)) return false;
         offset += field_size;
      }
      return true;
   }
   if (type->kind == SDL_TYPE_ARRAY) {
      const SdlTypeDesc *element = type->detail.array.element;
      size_t element_wire_size = sdl_fixed_wire_size(element);
      for (i = 0; i < type->detail.array.count; ++i) {
         if (!sdl_value_decode_fixed(element,
               buffer + i * element_wire_size,
               (uint8_t *)value + i * element->size))
            return false;
      }
      return true;
   }
   if (type->kind == SDL_TYPE_ENUM) {
      int32_t enum_value = (int32_t)sdl_wire_read_u32(buffer);
      if (type->size == 1) {
         int8_t part = (int8_t)enum_value;
         if (part != enum_value) return false;
         memcpy(value, &part, 1);
      } else if (type->size == 2) {
         int16_t part = (int16_t)enum_value;
         if (part != enum_value) return false;
         memcpy(value, &part, 2);
      } else if (type->size == 4) {
         memcpy(value, &enum_value, 4);
      } else {
         return false;
      }
      return true;
   }
   if (type->kind == SDL_TYPE_BOOL) {
      if (buffer[0] > 1) return false;
      *(bool *)value = buffer[0] != 0;
      return true;
   }
   if (type->kind == SDL_TYPE_COMPLEX32) {
      uint32_t bits;
      float parts[2];
      float complex complex_value;
      bits = sdl_wire_read_u32(buffer); memcpy(&parts[0], &bits, 4);
      bits = sdl_wire_read_u32(buffer + 4); memcpy(&parts[1], &bits, 4);
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

static size_t value_measure(const SdlTypeDesc *type, const void *value);

static size_t struct_measure(const SdlTypeDesc *type, const void *value) {
   size_t total = 0;
   size_t i;
   for (i = 0; i < type->detail.structure.field_count; ++i) {
      const SdlFieldDesc *field = &type->detail.structure.fields[i];
      if (!field_present(field, value))
         continue;
      if ((field->flags & SDL_FIELD_REPEATED) != 0) {
         uint32_t count = *(const uint32_t *)((const uint8_t *)value + field->count_offset);
         const void *items = *(const void * const *)field_value(field, value);
         size_t item;
         if (count != 0 && items == NULL)
            return SIZE_MAX;
         if (field->type->size != 0 && count > SIZE_MAX / field->type->size)
            return SIZE_MAX;
         if (field->type->alignment > 1) {
            size_t padding = field->type->alignment - 1;
            if (total > SIZE_MAX - padding) return SIZE_MAX;
            total += padding;
         }
         if (total > SIZE_MAX - (size_t)count * field->type->size)
            return SIZE_MAX;
         total += (size_t)count * field->type->size;
         for (item = 0; item < count; ++item) {
            size_t extra = value_measure(field->type,
               (const uint8_t *)items + item * field->type->size);
            if (extra == SIZE_MAX || total > SIZE_MAX - extra)
               return SIZE_MAX;
            total += extra;
         }
      } else {
         size_t extra = value_measure(field->type, field_value(field, value));
         if (extra == SIZE_MAX || total > SIZE_MAX - extra)
            return SIZE_MAX;
         total += extra;
      }
   }
   return total;
}

static size_t value_measure(const SdlTypeDesc *type, const void *value) {
   if (type->kind == SDL_TYPE_STRING) {
      const char *string = *(const char * const *)value;
      size_t length;
      if (string == NULL)
         return 0;
      length = string_length(string);
      return length == SIZE_MAX ? SIZE_MAX : length + 1;
   }
   if (type->kind == SDL_TYPE_STRUCT)
      return struct_measure(type, value);
   if (type->kind == SDL_TYPE_ARRAY)
      return 0;
   return 0;
}

size_t sdl_value_measure(const SdlTypeDesc *type, const void *value) {
   return type == NULL || value == NULL ? SIZE_MAX : value_measure(type, value);
}

static void clone_value(const SdlTypeDesc *type, const void *src, void *dst,
   uint8_t *pool, size_t *pool_offset);

static void clone_struct(const SdlTypeDesc *type, const void *src, void *dst,
   uint8_t *pool, size_t *pool_offset) {
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
         const uint8_t *items = *(const uint8_t * const *)source;
         uint8_t *copies;
         size_t item;
         *(uint32_t *)((uint8_t *)dst + field->count_offset) = count;
         if (count == 0 || items == NULL) {
            *(void **)target = NULL;
            continue;
         }
         *pool_offset = align_pool_offset(pool, *pool_offset, field->type->alignment);
         if (*pool_offset == SIZE_MAX) return;
         copies = pool + *pool_offset;
         *(void **)target = copies;
         *pool_offset += (size_t)count * field->type->size;
         memset(copies, 0, (size_t)count * field->type->size);
         for (item = 0; item < count; ++item)
            clone_value(field->type, items + item * field->type->size,
               copies + item * field->type->size, pool, pool_offset);
      } else {
         clone_value(field->type, source, target, pool, pool_offset);
      }
   }
}

static void clone_value(const SdlTypeDesc *type, const void *src, void *dst,
   uint8_t *pool, size_t *pool_offset) {
   if (type->kind == SDL_TYPE_STRING) {
      const char *string = *(const char * const *)src;
      if (string == NULL) {
         *(char **)dst = NULL;
      } else {
         size_t length = string_length(string) + 1;
         char *copy = (char *)(pool + *pool_offset);
         memcpy(copy, string, length);
         *(char **)dst = copy;
         *pool_offset += length;
      }
   } else if (type->kind == SDL_TYPE_STRUCT) {
      clone_struct(type, src, dst, pool, pool_offset);
   } else {
      memcpy(dst, src, type->size);
   }
}

void sdl_value_clone(const SdlTypeDesc *type, const void *src, void *dst,
   uint8_t *pool, size_t *pool_offset) {
   clone_value(type, src, dst, pool, pool_offset);
}

/* Wire value encoders return SIZE_MAX on invalid input or insufficient space. */
static size_t encode_value(const SdlTypeDesc *type, const void *value,
   uint8_t *buffer, size_t capacity);

static size_t encode_struct(const SdlTypeDesc *type, const void *value,
   uint8_t *buffer, size_t capacity) {
   size_t offset = 0;
   size_t i;
   for (i = 0; i < type->detail.structure.field_count; ++i) {
      const SdlFieldDesc *field = &type->detail.structure.fields[i];
      const void *field_data = field_value(field, value);
      uint32_t count = 1;
      size_t item;
      if (!field_present(field, value))
         continue;
      if ((field->flags & SDL_FIELD_REPEATED) != 0) {
         count = *(const uint32_t *)((const uint8_t *)value + field->count_offset);
         field_data = *(const void * const *)field_data;
         if (count != 0 && field_data == NULL)
            return SIZE_MAX;
         if ((field->flags & SDL_FIELD_PACKED) != 0) {
            if (count == 0) continue;
            size_t fixed_size = sdl_fixed_wire_size(field->type);
            size_t payload_size;
            if (fixed_size == 0 || count > SIZE_MAX / fixed_size)
               return SIZE_MAX;
            payload_size = (size_t)count * fixed_size;
            if (payload_size > UINT32_MAX || offset > capacity ||
                capacity - offset < 8 || payload_size > capacity - offset - 8)
               return SIZE_MAX;
            sdl_wire_write_u32(buffer + offset, field->id);
            sdl_wire_write_u32(buffer + offset + 4, (uint32_t)payload_size);
            for (item = 0; item < count; ++item) {
               if (!sdl_value_encode_fixed(field->type,
                     (const uint8_t *)field_data + item * field->type->size,
                     buffer + offset + 8 + item * fixed_size, fixed_size)) {
                  return SIZE_MAX;
               }
            }
            offset += 8 + payload_size;
            continue;
         }
      }
      for (item = 0; item < count; ++item) {
         size_t payload;
         const void *item_value = (field->flags & SDL_FIELD_REPEATED) != 0 ?
            (const uint8_t *)field_data + item * field->type->size : field_data;
         if (offset > capacity || capacity - offset < 8)
            return SIZE_MAX;
         payload = encode_value(field->type, item_value,
            buffer + offset + 8, capacity - offset - 8);
         if (payload == SIZE_MAX || payload > UINT32_MAX || payload > capacity - offset - 8)
            return SIZE_MAX;
         sdl_wire_write_u32(buffer + offset, field->id);
         sdl_wire_write_u32(buffer + offset + 4, (uint32_t)payload);
         offset += 8 + payload;
      }
   }
   return offset;
}

static size_t encode_value(const SdlTypeDesc *type, const void *value,
   uint8_t *buffer, size_t capacity) {
   size_t i;
   if (type->kind == SDL_TYPE_STRING) {
      const char *string = *(const char * const *)value;
      size_t length = string == NULL ? 0 : string_length(string);
      if (length == SIZE_MAX || length > capacity)
         return SIZE_MAX;
      if (length != 0)
         memcpy(buffer, string, length);
      return length;
   }
   if (type->kind == SDL_TYPE_STRUCT)
      return encode_struct(type, value, buffer, capacity);
   if (type->kind == SDL_TYPE_ARRAY) {
      size_t wire_size = sdl_fixed_wire_size(type);
      if (wire_size == 0 || !sdl_value_encode_fixed(type, value, buffer, capacity)) {
         return SIZE_MAX;
      }
      return wire_size;
   }
   if (type->kind == SDL_TYPE_BOOL) {
      if (capacity < 1) return SIZE_MAX;
      buffer[0] = *(const bool *)value ? 1U : 0U;
      return 1;
   }
   if (type->kind == SDL_TYPE_COMPLEX32) {
      float complex z;
      uint32_t part;
      float real_part, imag_part;
      if (capacity < 8) return SIZE_MAX;
      memcpy(&z, value, sizeof(z)); real_part = crealf(z); imag_part = cimagf(z);
      memcpy(&part, &real_part, 4); sdl_wire_write_u32(buffer, part);
      memcpy(&part, &imag_part, 4); sdl_wire_write_u32(buffer + 4, part);
      return 8;
   }
   if (type->kind == SDL_TYPE_COMPLEX64) {
      double complex z;
      double real_part, imag_part;
      if (capacity < 16) return SIZE_MAX;
      memcpy(&z, value, sizeof(z)); real_part = creal(z); imag_part = cimag(z);
      sdl_wire_encode_native(buffer, &real_part, 8);
      sdl_wire_encode_native(buffer + 8, &imag_part, 8);
      return 16;
   }
   if (type->kind == SDL_TYPE_ENUM) {
      int32_t value32 = 0;
      if (capacity < 4) return SIZE_MAX;
      if (type->size == 1) {
         int8_t value8; memcpy(&value8, value, 1); value32 = value8;
      } else if (type->size == 2) {
         int16_t value16; memcpy(&value16, value, 2); value32 = value16;
      } else if (type->size == 4) {
         memcpy(&value32, value, 4);
      } else {
         return SIZE_MAX;
      }
      sdl_wire_write_u32(buffer, (uint32_t)value32);
      return 4;
   }
   if (type->kind == SDL_TYPE_FLOAT32 || type->kind == SDL_TYPE_FLOAT64 ||
       type->kind == SDL_TYPE_INT8 || type->kind == SDL_TYPE_INT16 ||
       type->kind == SDL_TYPE_INT32 || type->kind == SDL_TYPE_INT64 ||
       type->kind == SDL_TYPE_BOOL) {
      const uint8_t *bytes = (const uint8_t *)value;
      size_t n = type->size;
      if (capacity < n) return SIZE_MAX;
      sdl_wire_encode_native(buffer, bytes, n);
      return n;
   }
   if (capacity < type->size) return SIZE_MAX;
   for (i = 0; i < type->size; ++i) buffer[i] = ((const uint8_t *)value)[i];
   return type->size;
}

size_t sdl_value_encode(const SdlTypeDesc *type, const void *value,
   uint8_t *buffer, size_t capacity) {
   return encode_value(type, value, buffer, capacity);
}

size_t sdl_value_decode_measure(const SdlTypeDesc *type,
   const uint8_t *buffer, size_t size) {
   size_t total = 0, offset = 0, i;
   size_t field_count;
   size_t counts[type->kind == SDL_TYPE_STRUCT && type->detail.structure.field_count != 0 ?
      type->detail.structure.field_count : 1];
   if (type->kind == SDL_TYPE_STRING)
      return size == SIZE_MAX ? SIZE_MAX : size + 1;
   if (type->kind == SDL_TYPE_ARRAY)
      return size == sdl_fixed_wire_size(type) ? 0 : SIZE_MAX;
   if (type->kind == SDL_TYPE_ENUM)
      return size == 4 ? 0 : SIZE_MAX;
   if (type->kind == SDL_TYPE_COMPLEX32)
      return size == 8 ? 0 : SIZE_MAX;
   if (type->kind == SDL_TYPE_COMPLEX64)
      return size == 16 ? 0 : SIZE_MAX;
   if (type->kind != SDL_TYPE_STRUCT)
      return size == type->size ? 0 : SIZE_MAX;
   field_count = type->detail.structure.field_count;
   memset(counts, 0, sizeof(counts));
   while (offset < size) {
      uint32_t id, length;
      size_t field_index;
      if (size - offset < 8) return SIZE_MAX;
      id = sdl_wire_read_u32(buffer + offset);
      length = sdl_wire_read_u32(buffer + offset + 4);
      offset += 8;
      if ((size_t)length > size - offset) return SIZE_MAX;
      for (field_index = 0; field_index < field_count; ++field_index)
         if (type->detail.structure.fields[field_index].id == id) break;
      if (field_index < field_count &&
          (type->detail.structure.fields[field_index].flags & SDL_FIELD_REPEATED) != 0) {
         const SdlFieldDesc *field = &type->detail.structure.fields[field_index];
         size_t item_count = 1;
         if ((field->flags & SDL_FIELD_PACKED) != 0) {
            size_t fixed_size = sdl_fixed_wire_size(field->type);
            if (fixed_size == 0 || (size_t)length % fixed_size != 0)
               return SIZE_MAX;
            item_count = (size_t)length / fixed_size;
         }
         if (counts[field_index] > SIZE_MAX - item_count) return SIZE_MAX;
         counts[field_index] += item_count;
      }
      offset += length;
   }
   for (i = 0; i < field_count; ++i) {
      const SdlFieldDesc *field = &type->detail.structure.fields[i];
      if ((field->flags & SDL_FIELD_REPEATED) != 0 && counts[i] != 0) {
         size_t padding = field->type->alignment > 1 ? field->type->alignment - 1 : 0;
         if (field->type->size == 0 || total > SIZE_MAX - padding)
            return SIZE_MAX;
         total += padding;
         if (counts[i] > (SIZE_MAX - total) / field->type->size)
            return SIZE_MAX;
         total += counts[i] * field->type->size;
      }
   }
   offset = 0;
   while (offset < size) {
      uint32_t id = sdl_wire_read_u32(buffer + offset);
      uint32_t length = sdl_wire_read_u32(buffer + offset + 4);
      size_t field_index;
      offset += 8;
      for (field_index = 0; field_index < field_count; ++field_index)
         if (type->detail.structure.fields[field_index].id == id) break;
      if (field_index < field_count) {
         const SdlFieldDesc *field = &type->detail.structure.fields[field_index];
         if ((field->flags & SDL_FIELD_PACKED) == 0) {
            size_t extra = sdl_value_decode_measure(field->type,
               buffer + offset, length);
            if (extra == SIZE_MAX || total > SIZE_MAX - extra) return SIZE_MAX;
            total += extra;
         }
      }
      offset += length;
   }
   return total;
}

bool sdl_value_decode(const SdlTypeDesc *type, const uint8_t *buffer,
   size_t size, void *value, uint8_t *pool, size_t *pool_offset) {
   size_t offset = 0;
   size_t i;
   if (type->kind == SDL_TYPE_STRING) {
      char *copy;
      if (size == SIZE_MAX) return false;
      copy = (char *)(pool + *pool_offset);
      if (size != 0) memcpy(copy, buffer, size);
      copy[size] = '\0';
      *(char **)value = copy;
      *pool_offset += size + 1;
      return true;
   }
   if (type->kind != SDL_TYPE_STRUCT) {
      if (type->kind == SDL_TYPE_ARRAY)
         return sdl_fixed_wire_size(type) == size &&
            sdl_value_decode_fixed(type, buffer, value);
      if (type->kind == SDL_TYPE_BOOL) {
         if (size != 1 || buffer[0] > 1) return false;
         *(bool *)value = buffer[0] != 0;
         return true;
      }
      if (type->kind == SDL_TYPE_ENUM) {
         int32_t enum_value = (int32_t)sdl_wire_read_u32(buffer);
         if (size != 4) return false;
         if (type->size == 1) {
            int8_t narrowed = (int8_t)enum_value;
            if (narrowed != enum_value) return false;
            memcpy(value, &narrowed, 1);
         } else if (type->size == 2) {
            int16_t narrowed = (int16_t)enum_value;
            if (narrowed != enum_value) return false;
            memcpy(value, &narrowed, 2);
         } else if (type->size == 4) {
            memcpy(value, &enum_value, 4);
         } else {
            return false;
         }
         return true;
      }
      if (type->kind == SDL_TYPE_COMPLEX32 || type->kind == SDL_TYPE_COMPLEX64) {
         if (size != (type->kind == SDL_TYPE_COMPLEX32 ? 8U : 16U)) return false;
      } else if (size != type->size) return false;
      if (type->kind == SDL_TYPE_COMPLEX32) {
         uint32_t bits;
         float components[2];
         float complex result;
         bits = sdl_wire_read_u32(buffer); memcpy(&components[0], &bits, 4);
         bits = sdl_wire_read_u32(buffer + 4); memcpy(&components[1], &bits, 4);
         memcpy(&result, components, sizeof(result));
         memcpy(value, &result, sizeof(result));
      } else if (type->kind == SDL_TYPE_COMPLEX64) {
         double components[2];
         double complex result;
         sdl_wire_decode_native(&components[0], buffer, 8);
         sdl_wire_decode_native(&components[1], buffer + 8, 8);
         memcpy(&result, components, sizeof(result));
         memcpy(value, &result, sizeof(result));
      } else {
         sdl_wire_decode_native(value, buffer, size);
      }
      return true;
   }
   {
      size_t field_count = type->detail.structure.field_count;
      size_t seen[field_count == 0 ? 1 : field_count];
      size_t counts[field_count == 0 ? 1 : field_count];
      memset(seen, 0, sizeof(seen));
      memset(counts, 0, sizeof(counts));
      while (offset < size) {
         uint32_t id = sdl_wire_read_u32(buffer + offset);
         uint32_t length = sdl_wire_read_u32(buffer + offset + 4);
         for (i = 0; i < field_count; ++i)
            if (type->detail.structure.fields[i].id == id &&
                (type->detail.structure.fields[i].flags & SDL_FIELD_REPEATED) != 0) {
               const SdlFieldDesc *field = &type->detail.structure.fields[i];
               if ((field->flags & SDL_FIELD_PACKED) != 0) {
                  size_t fixed_size = sdl_fixed_wire_size(field->type);
                  if (fixed_size == 0 || (size_t)length % fixed_size != 0)
                     return false;
                  counts[i] += (size_t)length / fixed_size;
               } else {
                  ++counts[i];
               }
            }
         offset += 8 + length;
      }
      for (i = 0; i < field_count; ++i) {
         const SdlFieldDesc *field = &type->detail.structure.fields[i];
         if ((field->flags & SDL_FIELD_REPEATED) != 0) {
            uint8_t *items = NULL;
            size_t aligned;
            if (counts[i] > UINT32_MAX ||
                counts[i] > SIZE_MAX / field->type->size) return false;
            *(uint32_t *)((uint8_t *)value + field->count_offset) = (uint32_t)counts[i];
            if (counts[i] != 0) {
               aligned = align_pool_offset(pool, *pool_offset, field->type->alignment);
               if (aligned == SIZE_MAX) return false;
               *pool_offset = aligned;
               items = pool + aligned;
               memset(items, 0, counts[i] * field->type->size);
               *pool_offset += counts[i] * field->type->size;
            }
            *(void **)((uint8_t *)value + field->offset) = items;
         }
      }
      offset = 0;
      while (offset < size) {
         uint32_t id = sdl_wire_read_u32(buffer + offset);
         uint32_t length = sdl_wire_read_u32(buffer + offset + 4);
         const SdlFieldDesc *field = NULL;
         size_t field_index;
         offset += 8;
         for (field_index = 0; field_index < field_count; ++field_index)
            if (type->detail.structure.fields[field_index].id == id) {
               field = &type->detail.structure.fields[field_index]; break;
            }
         if (field != NULL) {
            void *target = (uint8_t *)value + field->offset;
            if (field->presence_offset != SDL_NO_OFFSET)
               *((bool *)((uint8_t *)value + field->presence_offset)) = true;
            if ((field->flags & SDL_FIELD_REPEATED) != 0) {
               uint8_t *items = *(uint8_t **)target;
               if ((field->flags & SDL_FIELD_PACKED) != 0) {
                  size_t fixed_size = sdl_fixed_wire_size(field->type);
                  size_t item_count;
                  size_t item;
                  if (fixed_size == 0 || (size_t)length % fixed_size != 0)
                     return false;
                  item_count = (size_t)length / fixed_size;
                  for (item = 0; item < item_count; ++item) {
                     void *item_target = items +
                        (seen[field_index] + item) * field->type->size;
                     if (!sdl_value_decode_fixed(field->type,
                           buffer + offset + item * fixed_size, item_target))
                        return false;
                  }
                  seen[field_index] += item_count;
                  offset += length;
                  continue;
               }
               target = items + seen[field_index] * field->type->size;
               ++seen[field_index];
            }
            if (!sdl_value_decode(field->type, buffer + offset, length,
                  target, pool, pool_offset)) return false;
         }
         offset += length;
      }
      return true;
   }
}
