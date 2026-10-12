#include "sdl_dynamic.h"
#include "sdl_wire.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

#define SDL_DYNAMIC_MAX_DESCRIPTOR (1024U * 1024U)
#define SDL_DYNAMIC_MAX_NESTING 64U

typedef struct {
   uint32_t id;
   char *name;
   uint8_t modifier;
   char *type_name;
   uint8_t dimension_count;
   uint32_t *dimensions;
} SdlDynamicFieldDesc;

typedef struct {
   char *name;
   size_t field_count;
   SdlDynamicFieldDesc *fields;
} SdlDynamicMessageDesc;

typedef struct {
   char *name;
   int32_t value;
} SdlDynamicEnumItem;

typedef struct {
   char *name;
   size_t item_count;
   SdlDynamicEnumItem *items;
} SdlDynamicEnumDesc;

typedef struct {
   const uint8_t *data;
   size_t size;
   size_t offset;
} SdlDynamicReader;

typedef struct {
   char *root_name;
   size_t message_count;
   SdlDynamicMessageDesc *messages;
   size_t enum_count;
   SdlDynamicEnumDesc *enums;
} SdlDynamicSchema;

static char *copy_string(const char *value) {
   size_t size = strlen(value) + 1;
   char *copy = (char *)malloc(size);
   if (copy != NULL)
      memcpy(copy, value, size);
   return copy;
}

static bool is_builtin_type(const char *name) {
   static const char *const names[] = {
      "bool", "int8", "int16", "int32", "int64", "fl32", "fl64",
      "c32", "c64", "string"
   };
   size_t i;
   for (i = 0; i < sizeof(names) / sizeof(names[0]); ++i)
      if (strcmp(name, names[i]) == 0)
         return true;
   return false;
}

static bool reader_read(SdlDynamicReader *reader, size_t size,
   const uint8_t **data) {
   if (size > reader->size - reader->offset)
      return false;
   *data = reader->data + reader->offset;
   reader->offset += size;
   return true;
}

static bool reader_u8(SdlDynamicReader *reader, uint8_t *value) {
   const uint8_t *data;
   if (!reader_read(reader, 1, &data))
      return false;
   *value = data[0];
   return true;
}

static bool reader_u16(SdlDynamicReader *reader, uint16_t *value) {
   const uint8_t *data;
   if (!reader_read(reader, 2, &data))
      return false;
   *value = (uint16_t)(((uint16_t)data[0] << 8) | data[1]);
   return true;
}

static bool reader_u32(SdlDynamicReader *reader, uint32_t *value) {
   const uint8_t *data;
   if (!reader_read(reader, 4, &data))
      return false;
   *value = ((uint32_t)data[0] << 24) | ((uint32_t)data[1] << 16) |
      ((uint32_t)data[2] << 8) | (uint32_t)data[3];
   return true;
}

static bool reader_i32(SdlDynamicReader *reader, int32_t *value) {
   uint32_t bits;
   if (!reader_u32(reader, &bits))
      return false;
   *value = bits <= INT32_MAX ? (int32_t)bits :
      -1 - (int32_t)(UINT32_MAX - bits);
   return true;
}

static char *reader_text(SdlDynamicReader *reader) {
   uint16_t size;
   const uint8_t *data;
   char *text;
   if (!reader_u16(reader, &size) || !reader_read(reader, size, &data) ||
       memchr(data, '\0', size) != NULL || !sdl_wire_valid_utf8(data, size))
      return NULL;
   text = (char *)malloc((size_t)size + 1);
   if (text == NULL)
      return NULL;
   memcpy(text, data, size);
   text[size] = '\0';
   return text;
}

static void value_clear(SdlDynamicValue *value);

static void message_clear(SdlDynamicMessage *message) {
   size_t i;
   if (message == NULL)
      return;
   free(message->type_name);
   if (message->fields != NULL)
      for (i = 0; i < message->field_count; ++i) {
         free(message->fields[i].name);
         value_clear(&message->fields[i].value);
      }
   free(message->fields);
   free(message);
}

static void value_clear(SdlDynamicValue *value) {
   size_t i;
   switch (value->kind) {
      case SDL_DYNAMIC_STRING:
         free(value->value.string.data);
         break;
      case SDL_DYNAMIC_ENUM:
         free(value->value.enumeration.type_name);
         free(value->value.enumeration.name);
         break;
      case SDL_DYNAMIC_ARRAY:
         if (value->value.array.items != NULL)
            for (i = 0; i < value->value.array.count; ++i)
               value_clear(&value->value.array.items[i]);
         free(value->value.array.items);
         break;
      case SDL_DYNAMIC_MESSAGE:
         message_clear(value->value.message);
         break;
      default:
         break;
   }
   memset(value, 0, sizeof(*value));
}

void type_dynamic_free(SdlDynamicMessage *message) {
   message_clear(message);
}

const SdlDynamicValue *type_dynamic_get(const SdlDynamicMessage *message,
   const char *name) {
   size_t i;
   if (message == NULL || name == NULL)
      return NULL;
   for (i = 0; i < message->field_count; ++i) {
      if (strcmp(message->fields[i].name, name) == 0)
         return &message->fields[i].value;
   }
   return NULL;
}

static void schema_clear(SdlDynamicSchema *schema) {
   size_t i, j;
   free(schema->root_name);
   if (schema->messages != NULL)
      for (i = 0; i < schema->message_count; ++i) {
         SdlDynamicMessageDesc *message = &schema->messages[i];
         free(message->name);
         if (message->fields != NULL)
            for (j = 0; j < message->field_count; ++j) {
               free(message->fields[j].name);
               free(message->fields[j].type_name);
               free(message->fields[j].dimensions);
            }
         free(message->fields);
      }
   free(schema->messages);
   if (schema->enums != NULL)
      for (i = 0; i < schema->enum_count; ++i) {
         SdlDynamicEnumDesc *enumeration = &schema->enums[i];
         free(enumeration->name);
         if (enumeration->items != NULL)
            for (j = 0; j < enumeration->item_count; ++j)
               free(enumeration->items[j].name);
         free(enumeration->items);
      }
   free(schema->enums);
   memset(schema, 0, sizeof(*schema));
}

static SdlDynamicMessageDesc *schema_message(SdlDynamicSchema *schema,
   const char *name) {
   size_t i;
   for (i = 0; i < schema->message_count; ++i)
      if (strcmp(schema->messages[i].name, name) == 0)
         return &schema->messages[i];
   return NULL;
}

static const SdlDynamicMessageDesc *schema_message_const(
   const SdlDynamicSchema *schema, const char *name) {
   size_t i;
   for (i = 0; i < schema->message_count; ++i)
      if (strcmp(schema->messages[i].name, name) == 0)
         return &schema->messages[i];
   return NULL;
}

static const SdlDynamicEnumDesc *schema_enum(const SdlDynamicSchema *schema,
   const char *name) {
   size_t i;
   for (i = 0; i < schema->enum_count; ++i)
      if (strcmp(schema->enums[i].name, name) == 0)
         return &schema->enums[i];
   return NULL;
}

static size_t sdl_fixed_type_size(const char *type_name,
   const SdlDynamicSchema *schema, const SdlDynamicMessageDesc **active,
   size_t depth);

static bool validate_message_dependencies(const SdlDynamicSchema *schema,
   size_t message_index, uint8_t *states, size_t depth) {
   const SdlDynamicMessageDesc *message;
   size_t i;
   if (depth > SDL_DYNAMIC_MAX_NESTING) return false;
   if (states[message_index] == 1) return false;
   if (states[message_index] == 2) return true;
   states[message_index] = 1;
   message = &schema->messages[message_index];
   for (i = 0; i < message->field_count; ++i) {
      const SdlDynamicMessageDesc *target = schema_message_const(schema,
         message->fields[i].type_name);
      if (target != NULL && !validate_message_dependencies(schema,
            (size_t)(target - schema->messages), states, depth + 1))
         return false;
   }
   states[message_index] = 2;
   return true;
}

static bool parse_descriptor(const uint8_t *data, size_t size,
   SdlDynamicSchema *schema) {
   SdlDynamicReader reader;
   const uint8_t *magic;
   uint16_t count16;
   size_t i, j, k;
   uint8_t *dependency_states = NULL;
   memset(schema, 0, sizeof(*schema));
   reader.data = data;
   reader.size = size;
   reader.offset = 0;
   if (!reader_read(&reader, 4, &magic) || memcmp(magic, "SDD1", 4) != 0)
      return false;
   schema->root_name = reader_text(&reader);
   if (schema->root_name == NULL || schema->root_name[0] == '\0' ||
       !reader_u16(&reader, &count16))
      goto fail;
   schema->message_count = count16;
   schema->messages = (SdlDynamicMessageDesc *)calloc(schema->message_count,
      sizeof(*schema->messages));
   if (schema->message_count != 0 && schema->messages == NULL)
      goto fail;
   for (i = 0; i < schema->message_count; ++i) {
      SdlDynamicMessageDesc *message = &schema->messages[i];
      message->name = reader_text(&reader);
      if (message->name == NULL || message->name[0] == '\0' ||
          is_builtin_type(message->name) ||
          !reader_u16(&reader, &count16))
         goto fail;
      message->field_count = count16;
      message->fields = (SdlDynamicFieldDesc *)calloc(message->field_count,
         sizeof(*message->fields));
      if (message->field_count != 0 && message->fields == NULL)
         goto fail;
      for (j = 0; j < message->field_count; ++j) {
         SdlDynamicFieldDesc *field = &message->fields[j];
         uint8_t dimension_count;
         uint32_t value32;
         if (!reader_u32(&reader, &field->id))
            goto fail;
         field->name = reader_text(&reader);
         if (field->name == NULL || field->name[0] == '\0' ||
             !reader_u8(&reader, &field->modifier))
            goto fail;
         field->type_name = reader_text(&reader);
         if (field->type_name == NULL || field->type_name[0] == '\0' ||
             !reader_u8(&reader, &dimension_count))
            goto fail;
         field->dimension_count = dimension_count;
         field->dimensions = (uint32_t *)calloc(dimension_count,
            sizeof(*field->dimensions));
         if (dimension_count != 0 && field->dimensions == NULL)
            goto fail;
         for (k = 0; k < dimension_count; ++k) {
            if (!reader_u32(&reader, &value32) || value32 == 0)
               goto fail;
            field->dimensions[k] = value32;
         }
         if (field->id == 0 || field->modifier > 3 ||
             (dimension_count != 0 && field->modifier != 0))
            goto fail;
         for (k = 0; k < j; ++k)
            if (message->fields[k].id >= field->id ||
                strcmp(message->fields[k].name, field->name) == 0)
               goto fail;
      }
      for (j = 0; j < i; ++j)
         if (strcmp(schema->messages[j].name, message->name) >= 0)
            goto fail;
   }
   if (!reader_u16(&reader, &count16))
      goto fail;
   schema->enum_count = count16;
   schema->enums = (SdlDynamicEnumDesc *)calloc(schema->enum_count,
      sizeof(*schema->enums));
   if (schema->enum_count != 0 && schema->enums == NULL)
      goto fail;
   for (i = 0; i < schema->enum_count; ++i) {
      SdlDynamicEnumDesc *enumeration = &schema->enums[i];
      enumeration->name = reader_text(&reader);
      if (enumeration->name == NULL || enumeration->name[0] == '\0' ||
          is_builtin_type(enumeration->name) ||
          schema_message(schema, enumeration->name) != NULL ||
          !reader_u16(&reader, &count16))
         goto fail;
      enumeration->item_count = count16;
      if (enumeration->item_count == 0)
         goto fail;
      enumeration->items = (SdlDynamicEnumItem *)calloc(enumeration->item_count,
         sizeof(*enumeration->items));
      if (enumeration->item_count != 0 && enumeration->items == NULL)
         goto fail;
      for (j = 0; j < enumeration->item_count; ++j) {
         enumeration->items[j].name = reader_text(&reader);
         if (enumeration->items[j].name == NULL ||
             enumeration->items[j].name[0] == '\0' ||
             !reader_i32(&reader, &enumeration->items[j].value))
            goto fail;
         for (k = 0; k < j; ++k)
            if (enumeration->items[k].value == enumeration->items[j].value ||
                strcmp(enumeration->items[k].name, enumeration->items[j].name) == 0)
               goto fail;
      }
      for (j = 0; j < i; ++j)
         if (strcmp(schema->enums[j].name, enumeration->name) >= 0)
            goto fail;
   }
   if (reader.offset != reader.size || schema_message(schema, schema->root_name) == NULL)
      goto fail;
   for (i = 0; i < schema->message_count; ++i) {
      const SdlDynamicMessageDesc *message = &schema->messages[i];
      for (j = 0; j < message->field_count; ++j) {
         const SdlDynamicFieldDesc *field = &message->fields[j];
         bool known = strcmp(field->type_name, "bool") == 0 ||
            strcmp(field->type_name, "int8") == 0 ||
            strcmp(field->type_name, "int16") == 0 ||
            strcmp(field->type_name, "int32") == 0 ||
            strcmp(field->type_name, "int64") == 0 ||
            strcmp(field->type_name, "fl32") == 0 ||
            strcmp(field->type_name, "fl64") == 0 ||
            strcmp(field->type_name, "c32") == 0 ||
            strcmp(field->type_name, "c64") == 0 ||
            strcmp(field->type_name, "string") == 0 ||
            schema_message_const(schema, field->type_name) != NULL ||
            schema_enum(schema, field->type_name) != NULL;
         if (!known)
            goto fail;
         if (field->dimension_count != 0 || field->modifier == 3) {
            size_t fixed_size = sdl_fixed_type_size(field->type_name,
               schema, NULL, 0);
            size_t dimension_index;
            if (fixed_size == 0)
               goto fail;
            for (dimension_index = 0; dimension_index < field->dimension_count;
                 ++dimension_index) {
               if (fixed_size > UINT32_MAX / field->dimensions[dimension_index])
                  goto fail;
               fixed_size *= field->dimensions[dimension_index];
            }
         }
      }
   }
   dependency_states = (uint8_t *)calloc(schema->message_count,
      sizeof(*dependency_states));
   if (schema->message_count != 0 && dependency_states == NULL)
      goto fail;
   for (i = 0; i < schema->message_count; ++i)
      if (!validate_message_dependencies(schema, i, dependency_states, 0))
         goto fail;
   free(dependency_states);
   return true;
fail:
   free(dependency_states);
   schema_clear(schema);
   return false;
}

static const SdlDynamicEnumItem *enum_item(const SdlDynamicSchema *schema,
   const char *type_name, int32_t value) {
   const SdlDynamicEnumDesc *enumeration = schema_enum(schema, type_name);
   size_t i;
   if (enumeration == NULL)
      return NULL;
   for (i = 0; i < enumeration->item_count; ++i)
      if (enumeration->items[i].value == value)
         return &enumeration->items[i];
   return NULL;
}

static size_t primitive_size(const char *type_name) {
   if (strcmp(type_name, "bool") == 0 || strcmp(type_name, "int8") == 0) return 1;
   if (strcmp(type_name, "int16") == 0) return 2;
   if (strcmp(type_name, "int32") == 0 || strcmp(type_name, "fl32") == 0) return 4;
   if (strcmp(type_name, "int64") == 0 || strcmp(type_name, "fl64") == 0) return 8;
   if (strcmp(type_name, "c32") == 0) return 8;
   if (strcmp(type_name, "c64") == 0) return 16;
   return 0;
}

static size_t sdl_fixed_type_size(const char *type_name,
   const SdlDynamicSchema *schema, const SdlDynamicMessageDesc **active,
   size_t depth) {
   const SdlDynamicMessageDesc *local_active[SDL_DYNAMIC_MAX_NESTING];
   const SdlDynamicMessageDesc *message;
   size_t size = primitive_size(type_name), total = 0, i, j;
   if (active == NULL)
      active = local_active;
   if (size != 0) return size;
   if (schema_enum(schema, type_name) != NULL) return 4;
   message = schema_message_const(schema, type_name);
   if (message == NULL || message->field_count == 0 ||
       depth >= SDL_DYNAMIC_MAX_NESTING)
      return 0;
   for (i = 0; i < depth; ++i)
      if (active[i] == message) return 0;
   active[depth] = message;
   for (i = 0; i < message->field_count; ++i) {
      const SdlDynamicFieldDesc *field = &message->fields[i];
      size_t field_size;
      if (field->modifier != 0) return 0;
      field_size = sdl_fixed_type_size(field->type_name, schema, active, depth + 1);
      if (field_size == 0) return 0;
      for (j = 0; j < field->dimension_count; ++j) {
         if (field_size > SIZE_MAX / field->dimensions[j]) return 0;
         field_size *= field->dimensions[j];
      }
      if (field_size > SIZE_MAX - total) return 0;
      total += field_size;
      if (total > UINT32_MAX) return 0;
   }
   return total;
}

static void decode_value(const char *type_name, const uint8_t *data, size_t size,
   const SdlDynamicSchema *schema, size_t depth, SdlDynamicValue *value);

static void decode_fixed(const char *type_name, const uint8_t *data, size_t size,
   const SdlDynamicSchema *schema, size_t depth, const uint32_t *dimensions,
   size_t dimension_count, SdlDynamicValue *value);

static SdlDynamicMessage *decode_message(const char *type_name,
   const uint8_t *data, size_t size, const SdlDynamicSchema *schema, size_t depth);

static void decode_fixed_plain(const char *type_name, const uint8_t *data,
   size_t size, const SdlDynamicSchema *schema, size_t depth,
   SdlDynamicValue *value) {
   size_t expected = sdl_fixed_type_size(type_name, schema, NULL, 0);
   const SdlDynamicEnumItem *item;
   if (depth > SDL_DYNAMIC_MAX_NESTING || expected == 0 || size != expected)
      return;
   item = NULL;
   if (schema_enum(schema, type_name) != NULL) {
      int32_t enum_value;
      sdl_wire_decode_native(&enum_value, data, 4);
      item = enum_item(schema, type_name, enum_value);
      if (item == NULL)
         return;
      value->kind = SDL_DYNAMIC_ENUM;
      value->value.enumeration.type_name = copy_string(type_name);
      value->value.enumeration.name = copy_string(item->name);
      value->value.enumeration.value = enum_value;
      if (value->value.enumeration.type_name == NULL ||
          value->value.enumeration.name == NULL)
         value_clear(value);
      return;
   }
   if (schema_message_const(schema, type_name) != NULL) {
      const SdlDynamicMessageDesc *message = schema_message_const(schema, type_name);
      SdlDynamicMessage *decoded = (SdlDynamicMessage *)calloc(1, sizeof(*decoded));
      size_t offset = 0, i;
      if (decoded == NULL) return;
      decoded->type_name = copy_string(type_name);
      decoded->field_count = message->field_count;
      decoded->fields = (SdlDynamicField *)calloc(decoded->field_count,
         sizeof(*decoded->fields));
      if (decoded->type_name == NULL ||
          (decoded->field_count != 0 && decoded->fields == NULL)) {
         message_clear(decoded);
         return;
      }
      for (i = 0; i < message->field_count; ++i) {
         const SdlDynamicFieldDesc *field = &message->fields[i];
         size_t field_size = sdl_fixed_type_size(field->type_name, schema, NULL, 0);
         size_t j;
         for (j = 0; j < field->dimension_count; ++j) {
            if (field_size > SIZE_MAX / field->dimensions[j]) {
               message_clear(decoded); return;
            }
            field_size *= field->dimensions[j];
         }
         decoded->fields[i].name = copy_string(field->name);
         if (decoded->fields[i].name == NULL || field_size > size - offset) {
            message_clear(decoded); return;
         }
         decode_fixed(field->type_name, data + offset, field_size, schema,
            depth + 1, field->dimensions, field->dimension_count,
            &decoded->fields[i].value);
         if (decoded->fields[i].value.kind == SDL_DYNAMIC_NULL) {
            message_clear(decoded); return;
         }
         offset += field_size;
      }
      if (offset != size) { message_clear(decoded); return; }
      value->kind = SDL_DYNAMIC_MESSAGE;
      value->value.message = decoded;
      return;
   }
   decode_value(type_name, data, size, schema, depth + 1, value);
}

static void decode_fixed_level(const char *type_name, const uint8_t *data,
   const SdlDynamicSchema *schema, size_t depth, const uint32_t *dimensions,
   size_t dimension_count, size_t level, size_t item_size,
   size_t *offset, SdlDynamicValue *value) {
   size_t i;
   if (level == dimension_count) {
      decode_fixed_plain(type_name, data + *offset, item_size, schema,
         depth + 1, value);
      if (value->kind != SDL_DYNAMIC_NULL)
         *offset += item_size;
      return;
   }
   value->kind = SDL_DYNAMIC_ARRAY;
   value->value.array.count = dimensions[level];
   value->value.array.items = (SdlDynamicValue *)calloc(dimensions[level],
      sizeof(*value->value.array.items));
   if (dimensions[level] != 0 && value->value.array.items == NULL) {
      value_clear(value);
      return;
   }
   for (i = 0; i < dimensions[level]; ++i) {
      decode_fixed_level(type_name, data, schema, depth + 1, dimensions,
         dimension_count, level + 1, item_size, offset,
         &value->value.array.items[i]);
      if (value->value.array.items[i].kind == SDL_DYNAMIC_NULL) {
         value_clear(value);
         return;
      }
   }
}

static void decode_fixed(const char *type_name, const uint8_t *data, size_t size,
   const SdlDynamicSchema *schema, size_t depth, const uint32_t *dimensions,
   size_t dimension_count, SdlDynamicValue *value) {
   const SdlDynamicMessageDesc *active[SDL_DYNAMIC_MAX_NESTING];
   size_t item_size = sdl_fixed_type_size(type_name, schema, active, 0);
   size_t count = 1, i, offset = 0;
   if (item_size == 0 || depth > SDL_DYNAMIC_MAX_NESTING)
      return;
   for (i = 0; i < dimension_count; ++i) {
      if (dimensions[i] == 0 || count > SIZE_MAX / dimensions[i])
         return;
      count *= dimensions[i];
   }
   if (count > SIZE_MAX / item_size || count * item_size != size)
      return;
   decode_fixed_level(type_name, data, schema, depth, dimensions,
      dimension_count, 0, item_size, &offset, value);
   if (offset != size)
      value_clear(value);
}

static bool value_array_append(SdlDynamicValue *array, SdlDynamicValue *item) {
   SdlDynamicValue *items;
   if (array->kind != SDL_DYNAMIC_ARRAY ||
       array->value.array.count >= SIZE_MAX / sizeof(*items))
      return false;
   items = (SdlDynamicValue *)realloc(array->value.array.items,
      (array->value.array.count + 1) * sizeof(*items));
   if (items == NULL)
      return false;
   array->value.array.items = items;
   items[array->value.array.count++] = *item;
   memset(item, 0, sizeof(*item));
   return true;
}

static bool value_string_set(SdlDynamicValue *value, const uint8_t *data,
   size_t size) {
   uint8_t *copy = NULL;
   if (size != 0) {
      copy = (uint8_t *)malloc(size);
      if (copy == NULL) return false;
      memcpy(copy, data, size);
   }
   value->kind = SDL_DYNAMIC_STRING;
   value->value.string.data = copy;
   value->value.string.size = size;
   return true;
}

static void decode_value(const char *type_name, const uint8_t *data, size_t size,
   const SdlDynamicSchema *schema, size_t depth, SdlDynamicValue *value) {
   size_t expected = primitive_size(type_name);
   const SdlDynamicEnumItem *item;
   if (depth > SDL_DYNAMIC_MAX_NESTING)
      return;
   if (strcmp(type_name, "string") == 0) {
      if (sdl_wire_valid_utf8(data, size))
         (void)value_string_set(value, data, size);
      return;
   }
   if (schema_enum(schema, type_name) != NULL) {
      int32_t enum_value;
      if (size != 4) return;
      sdl_wire_decode_native(&enum_value, data, 4);
      item = enum_item(schema, type_name, enum_value);
      if (item == NULL) return;
      value->kind = SDL_DYNAMIC_ENUM;
      value->value.enumeration.type_name = copy_string(type_name);
      value->value.enumeration.name = copy_string(item->name);
      value->value.enumeration.value = enum_value;
      if (value->value.enumeration.type_name == NULL ||
          value->value.enumeration.name == NULL)
         value_clear(value);
      return;
   }
   if (schema_message_const(schema, type_name) != NULL) {
      value->kind = SDL_DYNAMIC_MESSAGE;
      value->value.message = decode_message(type_name, data, size, schema, depth + 1);
      if (value->value.message == NULL)
         value_clear(value);
      return;
   }
   if (expected == 0 || size != expected)
      return;
   if (strcmp(type_name, "bool") == 0) {
      if (data[0] > 1) return;
      value->kind = SDL_DYNAMIC_BOOL;
      value->value.boolean = data[0] != 0;
   } else if (strcmp(type_name, "int8") == 0) {
      int8_t part;
      memcpy(&part, data, sizeof(part));
      value->kind = SDL_DYNAMIC_INTEGER;
      value->value.integer = part;
   } else if (strcmp(type_name, "int16") == 0) {
      int16_t part;
      sdl_wire_decode_native(&part, data, sizeof(part));
      value->kind = SDL_DYNAMIC_INTEGER;
      value->value.integer = part;
   } else if (strcmp(type_name, "int32") == 0) {
      int32_t part;
      sdl_wire_decode_native(&part, data, sizeof(part));
      value->kind = SDL_DYNAMIC_INTEGER;
      value->value.integer = part;
   } else if (strcmp(type_name, "int64") == 0) {
      int64_t part;
      sdl_wire_decode_native(&part, data, sizeof(part));
      value->kind = SDL_DYNAMIC_INTEGER;
      value->value.integer = part;
   } else if (strcmp(type_name, "fl32") == 0) {
      float part;
      sdl_wire_decode_native(&part, data, sizeof(part));
      value->kind = SDL_DYNAMIC_FLOAT;
      value->value.floating = part;
   } else if (strcmp(type_name, "fl64") == 0) {
      double part;
      sdl_wire_decode_native(&part, data, sizeof(part));
      value->kind = SDL_DYNAMIC_FLOAT;
      value->value.floating = part;
   } else if (strcmp(type_name, "c32") == 0) {
      float parts[2];
      sdl_wire_decode_native(&parts[0], data, 4);
      sdl_wire_decode_native(&parts[1], data + 4, 4);
      value->kind = SDL_DYNAMIC_COMPLEX;
      value->value.complex_value.real = parts[0];
      value->value.complex_value.imag = parts[1];
   } else if (strcmp(type_name, "c64") == 0) {
      double parts[2];
      sdl_wire_decode_native(&parts[0], data, 8);
      sdl_wire_decode_native(&parts[1], data + 8, 8);
      value->kind = SDL_DYNAMIC_COMPLEX;
      value->value.complex_value.real = parts[0];
      value->value.complex_value.imag = parts[1];
   }
}

static SdlDynamicMessage *decode_message(const char *type_name,
   const uint8_t *data, size_t size, const SdlDynamicSchema *schema, size_t depth) {
   const SdlDynamicMessageDesc *desc = schema_message_const(schema, type_name);
   SdlDynamicMessage *message;
   size_t i, offset = 0;
   if (desc == NULL || depth > SDL_DYNAMIC_MAX_NESTING)
      return NULL;
   message = (SdlDynamicMessage *)calloc(1, sizeof(*message));
   if (message == NULL) return NULL;
   message->type_name = copy_string(type_name);
   message->field_count = desc->field_count;
   message->fields = (SdlDynamicField *)calloc(message->field_count,
      sizeof(*message->fields));
   if (message->type_name == NULL ||
       (message->field_count != 0 && message->fields == NULL)) {
      message_clear(message); return NULL;
   }
   for (i = 0; i < desc->field_count; ++i) {
      const SdlDynamicFieldDesc *field = &desc->fields[i];
      message->fields[i].name = copy_string(field->name);
      if (message->fields[i].name == NULL) { message_clear(message); return NULL; }
      if (field->modifier == 2 || field->modifier == 3) {
         message->fields[i].value.kind = SDL_DYNAMIC_ARRAY;
      }
   }
   while (offset < size) {
      uint32_t field_id, payload_size;
      const uint8_t *payload;
      const SdlDynamicFieldDesc *field = NULL;
      size_t field_index;
      if (size - offset < 8) { message_clear(message); return NULL; }
      field_id = sdl_wire_read_u32(data + offset);
      payload_size = sdl_wire_read_u32(data + offset + 4);
      offset += 8;
      if ((size_t)payload_size > size - offset) { message_clear(message); return NULL; }
      payload = data + offset;
      for (i = 0; i < desc->field_count; ++i)
         if (desc->fields[i].id == field_id) { field = &desc->fields[i]; break; }
      if (field != NULL) {
         SdlDynamicValue decoded;
         memset(&decoded, 0, sizeof(decoded));
         field_index = i;
         if (field->modifier == 3) {
            size_t item_size = sdl_fixed_type_size(field->type_name,
               schema, NULL, 0);
            size_t item_offset;
            if (item_size == 0 || payload_size % item_size != 0) {
               message_clear(message); return NULL;
            }
            for (item_offset = 0; item_offset < payload_size; item_offset += item_size) {
               SdlDynamicValue item;
               memset(&item, 0, sizeof(item));
               decode_fixed_plain(field->type_name, payload + item_offset,
                  item_size, schema, depth + 1, &item);
               if (item.kind == SDL_DYNAMIC_NULL ||
                   !value_array_append(&message->fields[field_index].value, &item)) {
                  value_clear(&item); message_clear(message); return NULL;
               }
            }
         } else if (field->dimension_count != 0) {
            decode_fixed(field->type_name, payload, payload_size, schema,
               depth + 1, field->dimensions, field->dimension_count, &decoded);
            if (decoded.kind == SDL_DYNAMIC_NULL) { message_clear(message); return NULL; }
            value_clear(&message->fields[field_index].value);
            message->fields[field_index].value = decoded;
         } else {
            decode_value(field->type_name, payload, payload_size, schema,
               depth + 1, &decoded);
            if (decoded.kind == SDL_DYNAMIC_NULL) { message_clear(message); return NULL; }
            if (field->modifier == 2) {
               if (!value_array_append(&message->fields[field_index].value, &decoded)) {
                  value_clear(&decoded); message_clear(message); return NULL;
               }
            } else {
               value_clear(&message->fields[field_index].value);
               message->fields[field_index].value = decoded;
            }
         }
      }
      offset += payload_size;
   }
   return message;
}

static uint32_t fnv1a_32(const uint8_t *data, size_t size) {
   uint32_t hash = 2166136261U;
   size_t i;
   for (i = 0; i < size; ++i)
      hash = (hash ^ data[i]) * 16777619U;
   return hash;
}

SdlDynamicMessage *type_decode_dynamic(const void *encoded, size_t size) {
   const uint8_t *buffer = (const uint8_t *)encoded;
   uint32_t descriptor_size, type_hash;
   size_t header_size;
   SdlDynamicSchema schema;
   SdlDynamicMessage *message;
   if (buffer == NULL || size < 8)
      return NULL;
   descriptor_size = sdl_wire_read_u32(buffer);
   if (descriptor_size > SDL_DYNAMIC_MAX_DESCRIPTOR || descriptor_size > size - 8)
      return NULL;
   header_size = (size_t)descriptor_size + 8;
   type_hash = sdl_wire_read_u32(buffer + 4 + descriptor_size);
   if (fnv1a_32(buffer + 4, descriptor_size) != type_hash)
      return NULL;
   if (!parse_descriptor(buffer + 4, descriptor_size, &schema))
      return NULL;
   message = decode_message(schema.root_name, buffer + header_size,
      size - header_size, &schema, 0);
   schema_clear(&schema);
   return message;
}
