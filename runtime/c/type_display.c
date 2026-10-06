#include "type_private.h"

#include <complex.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SDL_DISPLAY_MAX_DEPTH 128U

typedef struct {
   char *data;
   size_t length;
   size_t capacity;
} SdlDisplayBuffer;

static int reserve(SdlDisplayBuffer *buffer, size_t extra) {
   size_t needed;
   size_t capacity;
   char *resized;
   if (buffer->length == SIZE_MAX || extra > SIZE_MAX - buffer->length - 1)
      return 0;
   needed = buffer->length + extra + 1;
   if (needed <= buffer->capacity) return 1;
   capacity = buffer->capacity == 0 ? 256 : buffer->capacity;
   while (capacity < needed) {
      if (capacity > SIZE_MAX / 2) {
         capacity = needed;
         break;
      }
      capacity *= 2;
   }
   resized = (char *)realloc(buffer->data, capacity);
   if (resized == NULL) return 0;
   buffer->data = resized;
   buffer->capacity = capacity;
   return 1;
}

static int append_bytes(SdlDisplayBuffer *buffer, const char *text, size_t size) {
   if (!reserve(buffer, size)) return 0;
   if (size != 0) memcpy(buffer->data + buffer->length, text, size);
   buffer->length += size;
   buffer->data[buffer->length] = '\0';
   return 1;
}

static int append_text(SdlDisplayBuffer *buffer, const char *text) {
   return append_bytes(buffer, text, strlen(text));
}

static int append_format(SdlDisplayBuffer *buffer, const char *format, ...) {
   va_list arguments;
   va_list copy;
   int size;
   va_start(arguments, format);
   va_copy(copy, arguments);
   size = vsnprintf(NULL, 0, format, copy);
   va_end(copy);
   if (size < 0 || !reserve(buffer, (size_t)size)) {
      va_end(arguments);
      return 0;
   }
   if (vsnprintf(buffer->data + buffer->length,
         buffer->capacity - buffer->length, format, arguments) != size) {
      va_end(arguments);
      return 0;
   }
   va_end(arguments);
   buffer->length += (size_t)size;
   return 1;
}

static int append_indent(SdlDisplayBuffer *buffer, size_t depth,
   size_t indent_width) {
   size_t count;
   if (indent_width != 0 && depth > SIZE_MAX / indent_width) return 0;
   count = depth * indent_width;
   if (!reserve(buffer, count)) return 0;
   while (count != 0) {
      buffer->data[buffer->length++] = ' ';
      --count;
   }
   buffer->data[buffer->length] = '\0';
   return 1;
}

static int append_quoted(SdlDisplayBuffer *buffer, const char *value,
   size_t length) {
   const unsigned char *cursor = (const unsigned char *)value;
   size_t index;
   if (!append_text(buffer, "\"")) return 0;
   for (index = 0; index < length; ++index) {
      const char *escape = NULL;
      size_t escape_size = 0;
      char control_escape[7];
      switch (*cursor) {
      case '"': escape = "\\\""; escape_size = 2; break;
      case '\\': escape = "\\\\"; escape_size = 2; break;
      case '\n': escape = "\\n"; escape_size = 2; break;
      case '\r': escape = "\\r"; escape_size = 2; break;
      case '\t': escape = "\\t"; escape_size = 2; break;
      default:
         if (*cursor < 0x20) {
            (void)snprintf(control_escape, sizeof(control_escape),
               "\\u%04x", (unsigned int)*cursor);
            escape = control_escape;
            escape_size = 6;
         }
      }
      if (escape != NULL) {
         if (!append_bytes(buffer, escape, escape_size)) return 0;
      } else if (!append_bytes(buffer, (const char *)cursor, 1)) {
         return 0;
      }
      ++cursor;
   }
   return append_text(buffer, "\"");
}

static int render_sequence(SdlDisplayBuffer *buffer, const SdlTypeDesc *type,
   const void *items, size_t count, const uint32_t *string_lengths,
   size_t indent_width, size_t depth);

static int render_value(SdlDisplayBuffer *buffer, const SdlTypeDesc *type,
   const void *value, size_t string_length, size_t indent_width, size_t depth);

static int render_struct_fields(SdlDisplayBuffer *buffer,
   const SdlTypeDesc *type, const void *value, size_t indent_width,
   size_t fields_depth) {
   size_t index;
   for (index = 0; index < type->detail.structure.field_count; ++index) {
      const SdlFieldDesc *field = &type->detail.structure.fields[index];
      const uint8_t *field_value = (const uint8_t *)value + field->offset;
      if (!append_indent(buffer, fields_depth, indent_width) ||
          !append_format(buffer, "%s: ", field->name)) return 0;
      if (field->presence_offset != SDL_NO_OFFSET &&
          !*((const bool *)((const uint8_t *)value + field->presence_offset))) {
         if (!append_text(buffer, "null\n")) return 0;
         continue;
      }
      if ((field->flags & SDL_FIELD_REPEATED) != 0) {
         uint32_t count;
         const void *items;
         memcpy(&count, (const uint8_t *)value + field->count_offset,
            sizeof(count));
         memcpy(&items, field_value, sizeof(items));
         if (count != 0 && items == NULL) return 0;
         const uint32_t *string_lengths = NULL;
         if (field->type->kind == SDL_TYPE_STRING &&
             field->string_length_offset != SDL_NO_OFFSET)
            string_lengths = *(const uint32_t * const *)
               ((const uint8_t *)value + field->string_length_offset);
         if (!render_sequence(buffer, field->type, items, count, string_lengths,
               indent_width, fields_depth)) return 0;
      } else {
         size_t string_length = SIZE_MAX;
         if (field->type->kind == SDL_TYPE_STRING &&
             field->string_length_offset != SDL_NO_OFFSET) {
            uint32_t length = *(const uint32_t *)((const uint8_t *)value +
               field->string_length_offset);
            if (length != 0) string_length = length;
         }
         if (!render_value(buffer, field->type, field_value, string_length,
               indent_width, fields_depth)) return 0;
      }
      if (!append_text(buffer, "\n")) return 0;
   }
   return 1;
}

static int render_sequence(SdlDisplayBuffer *buffer, const SdlTypeDesc *type,
   const void *items, size_t count, const uint32_t *string_lengths,
   size_t indent_width, size_t depth) {
   size_t index;
   if (type == NULL || (count != 0 && (items == NULL || type->size == 0 ||
         count > SIZE_MAX / type->size))) return 0;
   if (count == 0) return append_text(buffer, "[]");
   if (depth >= SDL_DISPLAY_MAX_DEPTH || !append_text(buffer, "[\n")) return 0;
   for (index = 0; index < count; ++index) {
      const void *item = (const uint8_t *)items + index * type->size;
      size_t string_length = string_lengths == NULL ? SIZE_MAX :
         (string_lengths[index] == 0 ? SIZE_MAX : string_lengths[index]);
      if (!append_indent(buffer, depth + 1, indent_width) ||
          !render_value(buffer, type, item, string_length, indent_width,
             depth + 1)) return 0;
      if (index + 1 < count && !append_text(buffer, ",")) return 0;
      if (!append_text(buffer, "\n")) return 0;
   }
   return append_indent(buffer, depth, indent_width) && append_text(buffer, "]");
}

static int render_enum(SdlDisplayBuffer *buffer, const SdlTypeDesc *type,
   const void *value) {
   int32_t number = 0;
   size_t index;
   if (type->size == 1) {
      int8_t part;
      memcpy(&part, value, sizeof(part));
      number = part;
   } else if (type->size == 2) {
      int16_t part;
      memcpy(&part, value, sizeof(part));
      number = part;
   } else if (type->size == 4) {
      memcpy(&number, value, sizeof(number));
   } else {
      return 0;
   }
   for (index = 0; index < type->detail.enumeration.value_count; ++index) {
      const SdlEnumValueDesc *item = &type->detail.enumeration.values[index];
      if (item->value == number) return append_text(buffer, item->name);
   }
   return append_format(buffer, "%" PRId32, number);
}

static int render_value(SdlDisplayBuffer *buffer, const SdlTypeDesc *type,
   const void *value, size_t string_length, size_t indent_width, size_t depth) {
   if (type == NULL || value == NULL || depth > SDL_DISPLAY_MAX_DEPTH) return 0;
   switch (type->kind) {
   case SDL_TYPE_BOOL: {
      bool part;
      memcpy(&part, value, sizeof(part));
      return append_text(buffer, part ? "true" : "false");
   }
   case SDL_TYPE_INT8: {
      int8_t part;
      memcpy(&part, value, sizeof(part));
      return append_format(buffer, "%" PRId8, part);
   }
   case SDL_TYPE_INT16: {
      int16_t part;
      memcpy(&part, value, sizeof(part));
      return append_format(buffer, "%" PRId16, part);
   }
   case SDL_TYPE_INT32: {
      int32_t part;
      memcpy(&part, value, sizeof(part));
      return append_format(buffer, "%" PRId32, part);
   }
   case SDL_TYPE_INT64: {
      int64_t part;
      memcpy(&part, value, sizeof(part));
      return append_format(buffer, "%" PRId64, part);
   }
   case SDL_TYPE_FLOAT32: {
      float part;
      memcpy(&part, value, sizeof(part));
      return append_format(buffer, "%.9g", (double)part);
   }
   case SDL_TYPE_FLOAT64: {
      double part;
      memcpy(&part, value, sizeof(part));
      return append_format(buffer, "%.17g", part);
   }
   case SDL_TYPE_COMPLEX32: {
      float complex part;
      memcpy(&part, value, sizeof(part));
      return append_format(buffer, "(%.9g, %.9g)", (double)crealf(part),
         (double)cimagf(part));
   }
   case SDL_TYPE_COMPLEX64: {
      double complex part;
      memcpy(&part, value, sizeof(part));
      return append_format(buffer, "(%.17g, %.17g)", creal(part), cimag(part));
   }
   case SDL_TYPE_ENUM:
      return render_enum(buffer, type, value);
   case SDL_TYPE_STRING: {
      const char *part;
      memcpy(&part, value, sizeof(part));
      if (part == NULL) return append_text(buffer, "null");
      if (string_length == SIZE_MAX) string_length = strlen(part);
      return append_quoted(buffer, part, string_length);
   }
   case SDL_TYPE_ARRAY:
      return render_sequence(buffer, type->detail.array.element, value,
         type->detail.array.count, NULL, indent_width, depth);
   case SDL_TYPE_STRUCT:
      if (!append_format(buffer, "%s {\n", type->name) ||
          !render_struct_fields(buffer, type, value, indent_width, depth + 1) ||
          !append_indent(buffer, depth, indent_width)) return 0;
      return append_text(buffer, "}");
   }
   return 0;
}

/* The named type must be registered; NULL reports an unknown type or failure. */
char *type_display(const char *name, const void *decoded, size_t indent_width) {
   const SdlTypeDesc *type = sdl_lookup_type(name);
   SdlDisplayBuffer output = { NULL, 0, 0 };
   if (type == NULL || decoded == NULL) return NULL;
   if (!append_format(&output, "%s {\n", type->name) ||
       !render_struct_fields(&output, type, decoded, indent_width, 1) ||
       !append_text(&output, "}\n")) {
      free(output.data);
      return NULL;
   }
   return output.data;
}
