#include "sdl_context.h"
#include "type_private.h"
#include <stdio.h>
#include <errno.h>
#include "sdl_wire.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

#define SDL_DYNAMIC_MAX_DESCRIPTOR (1024U * 1024U)
#define SDL_DYNAMIC_MAX_NESTING 64U

typedef struct {
   size_t native_offset, wire_offset, width, count;
   const SdlTypeDesc *checked_type; /* bool/enum values require validation. */
} SdlFixedSpan;

typedef struct {
   SdlFixedSpan *spans;
   size_t span_count, capacity, wire_size, native_size, steps;
   bool checked_values;
} SdlFixedPlan;

typedef struct {
   uint32_t id;
   char *name;
   uint8_t modifier;
   char *type_name;
   uint8_t dimension_count;
   uint32_t *dimensions;
   const SdlFieldDesc *local;
   const SdlFixedPlan *record_plan;
} SdlDynamicFieldDesc;

typedef struct {
   char *name;
   size_t field_count;
   SdlDynamicFieldDesc *fields;
   const SdlTypeDesc *local;
   size_t height, fixed_size;
   SdlFixedPlan plan;
   bool fixed_ready, emission_ready, can_encode;
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
   static const char *const names[] = {"bool", "int8", "int16", "int32", "int64",
                                       "fl32", "fl64", "c32",   "c64",   "string"};
   size_t i;
   for (i = 0; i < sizeof(names) / sizeof(names[0]); ++i)
      if (strcmp(name, names[i]) == 0)
         return true;
   return false;
}

static bool reader_read(SdlDynamicReader *reader, size_t size, const uint8_t **data) {
   if (size > reader->size - reader->offset)
      return false;
   *data = reader->data + reader->offset;
   reader->offset += size;
   return true;
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

void type_dynamic_free(SdlDynamicMessage *message) { message_clear(message); }

const SdlDynamicValue *type_dynamic_get(const SdlDynamicMessage *message, const char *name) {
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
         free(message->plan.spans);
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

static SdlDynamicMessageDesc *schema_message(SdlDynamicSchema *schema, const char *name) {
   size_t i;
   for (i = 0; i < schema->message_count; ++i)
      if (strcmp(schema->messages[i].name, name) == 0)
         return &schema->messages[i];
   return NULL;
}

static const SdlDynamicMessageDesc *schema_message_const(const SdlDynamicSchema *schema,
                                                         const char *name) {
   size_t i;
   for (i = 0; i < schema->message_count; ++i)
      if (strcmp(schema->messages[i].name, name) == 0)
         return &schema->messages[i];
   return NULL;
}

static const SdlDynamicEnumDesc *schema_enum(const SdlDynamicSchema *schema, const char *name) {
   size_t i;
   for (i = 0; i < schema->enum_count; ++i)
      if (strcmp(schema->enums[i].name, name) == 0)
         return &schema->enums[i];
   return NULL;
}

static size_t sdl_fixed_type_size(const char *type_name, const SdlDynamicSchema *schema,
                                  const SdlDynamicMessageDesc **active, size_t depth);

static bool validate_message_dependencies(const SdlDynamicSchema *schema, size_t message_index,
                                          uint8_t *states, size_t depth) {
   const SdlDynamicMessageDesc *message;
   size_t i, height = 0;
   if (depth > SDL_DYNAMIC_MAX_NESTING)
      return false;
   if (states[message_index] == 1)
      return false;
   if (states[message_index] == 2)
      return schema->messages[message_index].height <= SDL_DYNAMIC_MAX_NESTING - depth;
   states[message_index] = 1;
   message = &schema->messages[message_index];
   for (i = 0; i < message->field_count; ++i) {
      const SdlDynamicMessageDesc *target =
          schema_message_const(schema, message->fields[i].type_name);
      if (target != NULL && !validate_message_dependencies(
                                schema, (size_t)(target - schema->messages), states, depth + 1))
         return false;
      if (target && target->height + 1 > height)
         height = target->height + 1;
   }
   if (height > SDL_DYNAMIC_MAX_NESTING - depth)
      return false;
   schema->messages[message_index].height = height;
   states[message_index] = 2;
   return true;
}

struct SdlContext {
   SdlDynamicSchema schema;
};

typedef struct {
   const char *p, *end;
} Lexer;
static char *token(Lexer *l) {
   const char *start;
   size_t n;
   char *out;
   while (l->p < l->end && (*l->p == ' ' || *l->p == '\n'))
      ++l->p;
   if (l->p == l->end)
      return NULL;
   start = l->p++;
   if (strchr("{}[]:;=", *start) == NULL)
      while (l->p < l->end && *l->p != ' ' && *l->p != '\n' && strchr("{}[]:;=", *l->p) == NULL)
         ++l->p;
   n = (size_t)(l->p - start);
   out = (char *)malloc(n + 1);
   if (out) {
      memcpy(out, start, n);
      out[n] = 0;
   }
   return out;
}
static bool expect(Lexer *l, const char *s) {
   char *t = token(l);
   bool ok = t && strcmp(t, s) == 0;
   free(t);
   return ok;
}
static bool number(Lexer *l, uint32_t *v) {
   char *t = token(l), *end;
   unsigned long long n;
   size_t i;
   bool ok = false;
   if (!t || !t[0]) {
      free(t);
      return false;
   }
   for (i = 0; t[i]; ++i)
      if (t[i] < '0' || t[i] > '9')
         goto done;
   errno = 0;
   n = strtoull(t, &end, 10);
   if (!errno && !*end && n <= UINT32_MAX) {
      *v = (uint32_t)n;
      ok = true;
   }
done:
   free(t);
   return ok;
}
static bool identifier(const char *s) {
   size_t i;
   if (!s || !s[0] || strlen(s) > 65535)
      return false;
   for (i = 0; s[i]; ++i) {
      unsigned char c = (unsigned char)s[i];
      if (!(c >= 128 || c == '_' || c == '$' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9')))
         return false;
   }
   return true;
}
static bool parse_descriptor(const uint8_t *data, size_t size, SdlDynamicSchema *schema) {
   Lexer l;
   size_t i, j;
   uint8_t *dependency_states = NULL;
   memset(schema, 0, sizeof(*schema));
   if (!data || size < 5 || size > SDL_DYNAMIC_MAX_DESCRIPTOR || memcmp(data, "SDL2\n", 5) ||
       data[size - 1] != '\n' || memchr(data, 0, size) || !sdl_wire_valid_utf8(data, size))
      return false;
   l.p = (const char *)data + 5;
   l.end = (const char *)data + size;
   for (;;) {
      char *kind = token(&l), *name;
      if (!kind)
         break;
      name = token(&l);
      if (!identifier(name) || is_builtin_type(name) || !expect(&l, "{")) {
         free(kind);
         free(name);
         goto fail;
      }
      if (strcmp(kind, "message") == 0) {
         SdlDynamicMessageDesc *m, *next;
         if (schema->message_count >= 65535) {
            free(kind);
            free(name);
            goto fail;
         }
         next = (SdlDynamicMessageDesc *)realloc(schema->messages,
                                                 (schema->message_count + 1) * sizeof(*next));
         if (!next) {
            free(kind);
            free(name);
            goto fail;
         }
         schema->messages = next;
         m = &next[schema->message_count++];
         memset(m, 0, sizeof(*m));
         m->name = name;
         for (;;) {
            const char *saved = l.p;
            char *t = token(&l);
            SdlDynamicFieldDesc *f, *fs;
            if (t && !strcmp(t, "}")) {
               free(t);
               break;
            }
            free(t);
            l.p = saved;
            if (m->field_count >= 65535) {
               free(kind);
               goto fail;
            }
            fs = (SdlDynamicFieldDesc *)realloc(m->fields, (m->field_count + 1) * sizeof(*fs));
            if (!fs) {
               free(kind);
               goto fail;
            }
            m->fields = fs;
            f = &fs[m->field_count++];
            memset(f, 0, sizeof(*f));
            if (!number(&l, &f->id) || !f->id || !expect(&l, ":")) {
               free(kind);
               goto fail;
            }
            t = token(&l);
            if (t && !strcmp(t, "required"))
               f->modifier = 0;
            else if (t && !strcmp(t, "optional"))
               f->modifier = 1;
            else if (t && !strcmp(t, "repeated"))
               f->modifier = 2;
            else if (t && !strcmp(t, "packed"))
               f->modifier = 3;
            else {
               free(t);
               free(kind);
               goto fail;
            }
            free(t);
            f->type_name = token(&l);
            if (!identifier(f->type_name)) {
               free(kind);
               goto fail;
            }
            for (;;) {
               saved = l.p;
               t = token(&l);
               if (!t || strcmp(t, "[")) {
                  free(t);
                  l.p = saved;
                  break;
               }
               free(t);
               if (f->dimension_count == 255) {
                  free(kind);
                  goto fail;
               }
               {
                  uint32_t n, *ds;
                  if (!number(&l, &n) || !n || !expect(&l, "]")) {
                     free(kind);
                     goto fail;
                  }
                  ds = (uint32_t *)realloc(f->dimensions, (f->dimension_count + 1) * sizeof(*ds));
                  if (!ds) {
                     free(kind);
                     goto fail;
                  }
                  f->dimensions = ds;
                  ds[f->dimension_count++] = n;
               }
            }
            f->name = token(&l);
            if (!identifier(f->name) || !expect(&l, ";") || (f->dimension_count && f->modifier)) {
               free(kind);
               goto fail;
            }
            for (j = 0; j + 1 < m->field_count; ++j)
               if (m->fields[j].id >= f->id || !strcmp(m->fields[j].name, f->name)) {
                  free(kind);
                  goto fail;
               }
         }
         if (schema->message_count > 1 &&
             strcmp(schema->messages[schema->message_count - 2].name, name) >= 0) {
            free(kind);
            goto fail;
         }
      } else if (!strcmp(kind, "enum")) {
         SdlDynamicEnumDesc *e, *next;
         if (schema->enum_count >= 65535 || schema_message(schema, name)) {
            free(kind);
            free(name);
            goto fail;
         }
         next =
             (SdlDynamicEnumDesc *)realloc(schema->enums, (schema->enum_count + 1) * sizeof(*next));
         if (!next) {
            free(kind);
            free(name);
            goto fail;
         }
         schema->enums = next;
         e = &next[schema->enum_count++];
         memset(e, 0, sizeof(*e));
         e->name = name;
         for (;;) {
            char *t = token(&l), *v, *end;
            long long n;
            SdlDynamicEnumItem *items;
            if (t && !strcmp(t, "}")) {
               free(t);
               break;
            }
            if (!identifier(t) || !expect(&l, "=") || e->item_count >= 65535) {
               free(t);
               free(kind);
               goto fail;
            }
            v = token(&l);
            errno = 0;
            n = v ? strtoll(v, &end, 10) : 0;
            if (!v || errno || *end || n < INT32_MIN || n > INT32_MAX || !expect(&l, ";")) {
               free(t);
               free(v);
               free(kind);
               goto fail;
            }
            free(v);
            items = (SdlDynamicEnumItem *)realloc(e->items, (e->item_count + 1) * sizeof(*items));
            if (!items) {
               free(t);
               free(kind);
               goto fail;
            }
            e->items = items;
            items[e->item_count].name = t;
            items[e->item_count++].value = (int32_t)n;
            for (j = 0; j + 1 < e->item_count; ++j)
               if (items[j].value == (int32_t)n || !strcmp(items[j].name, t)) {
                  free(kind);
                  goto fail;
               }
         }
         if (!e->item_count || (schema->enum_count > 1 &&
                                strcmp(schema->enums[schema->enum_count - 2].name, name) >= 0)) {
            free(kind);
            goto fail;
         }
      } else {
         free(kind);
         free(name);
         goto fail;
      }
      free(kind);
   }
   if (!schema->message_count)
      goto fail;
   for (i = 0; i < schema->enum_count; ++i)
      if (schema_message(schema, schema->enums[i].name))
         goto fail;
   dependency_states = (uint8_t *)calloc(schema->message_count, sizeof(*dependency_states));
   if (!dependency_states)
      goto fail;
   for (i = 0; i < schema->message_count; ++i)
      if (!validate_message_dependencies(schema, i, dependency_states, 0))
         goto fail;
   for (i = 0; i < schema->message_count; ++i)
      sdl_fixed_type_size(schema->messages[i].name, schema, NULL, 0);
   for (i = 0; i < schema->message_count; ++i) {
      const SdlDynamicMessageDesc *message = &schema->messages[i];
      for (j = 0; j < message->field_count; ++j) {
         const SdlDynamicFieldDesc *field = &message->fields[j];
         bool known =
             strcmp(field->type_name, "bool") == 0 || strcmp(field->type_name, "int8") == 0 ||
             strcmp(field->type_name, "int16") == 0 || strcmp(field->type_name, "int32") == 0 ||
             strcmp(field->type_name, "int64") == 0 || strcmp(field->type_name, "fl32") == 0 ||
             strcmp(field->type_name, "fl64") == 0 || strcmp(field->type_name, "c32") == 0 ||
             strcmp(field->type_name, "c64") == 0 || strcmp(field->type_name, "string") == 0 ||
             schema_message_const(schema, field->type_name) != NULL ||
             schema_enum(schema, field->type_name) != NULL;
         if (!known)
            goto fail;
         if (field->dimension_count != 0 || field->modifier == 3) {
            size_t fixed_size = sdl_fixed_type_size(field->type_name, schema, NULL, 0);
            size_t dimension_index;
            if (fixed_size == 0)
               goto fail;
            for (dimension_index = 0; dimension_index < field->dimension_count; ++dimension_index) {
               if (fixed_size > UINT32_MAX / field->dimensions[dimension_index])
                  goto fail;
               fixed_size *= field->dimensions[dimension_index];
            }
         }
      }
   }
   free(dependency_states);
   return true;
fail:
   free(dependency_states);
   schema_clear(schema);
   return false;
}

static const SdlDynamicEnumItem *enum_item(const SdlDynamicSchema *schema, const char *type_name,
                                           int32_t value) {
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
   if (strcmp(type_name, "bool") == 0 || strcmp(type_name, "int8") == 0)
      return 1;
   if (strcmp(type_name, "int16") == 0)
      return 2;
   if (strcmp(type_name, "int32") == 0 || strcmp(type_name, "fl32") == 0)
      return 4;
   if (strcmp(type_name, "int64") == 0 || strcmp(type_name, "fl64") == 0)
      return 8;
   if (strcmp(type_name, "c32") == 0)
      return 8;
   if (strcmp(type_name, "c64") == 0)
      return 16;
   return 0;
}

static size_t sdl_fixed_type_size(const char *type_name, const SdlDynamicSchema *schema,
                                  const SdlDynamicMessageDesc **active, size_t depth) {
   const SdlDynamicMessageDesc *local_active[SDL_DYNAMIC_MAX_NESTING];
   const SdlDynamicMessageDesc *message;
   size_t size = primitive_size(type_name), total = 0, i, j;
   if (active == NULL)
      active = local_active;
   if (size != 0)
      return size;
   if (schema_enum(schema, type_name) != NULL)
      return 4;
   message = schema_message_const(schema, type_name);
   if (message == NULL || message->field_count == 0 || depth >= SDL_DYNAMIC_MAX_NESTING)
      return 0;
   for (i = 0; i < depth; ++i)
      if (active[i] == message)
         return 0;
   if (message->fixed_ready)
      return message->fixed_size;
   /* Preparation computes every message cache after cycle/depth validation. */
   schema->messages[message - schema->messages].fixed_ready = true;
   active[depth] = message;
   for (i = 0; i < message->field_count; ++i) {
      const SdlDynamicFieldDesc *field = &message->fields[i];
      size_t field_size;
      if (field->modifier != 0)
         return 0;
      field_size = sdl_fixed_type_size(field->type_name, schema, active, depth + 1);
      if (field_size == 0)
         return 0;
      for (j = 0; j < field->dimension_count; ++j) {
         if (field_size > SIZE_MAX / field->dimensions[j])
            return 0;
         field_size *= field->dimensions[j];
      }
      if (field_size > SIZE_MAX - total)
         return 0;
      total += field_size;
      if (total > UINT32_MAX)
         return 0;
   }
   schema->messages[message - schema->messages].fixed_size = total;
   return total;
}

static const char *native_name(const SdlTypeDesc *t) {
   static const char *const names[] = {"bool", "int8", "int16", "int32", "int64",
                                       "fl32", "fl64", "c32",   "c64"};
   return t->kind <= SDL_TYPE_COMPLEX64 ? names[t->kind] : t->name;
}

static bool bind_schema(SdlDynamicSchema *s) {
   size_t i, j, k;
   for (i = 0; i < s->message_count; ++i) {
      SdlDynamicMessageDesc *m = &s->messages[i];
      m->local = sdl_lookup_type(m->name);
      if (!m->local)
         continue;
      for (j = 0; j < m->field_count; ++j) {
         SdlDynamicFieldDesc *f = &m->fields[j];
         const SdlTypeDesc *t;
         for (k = 0; k < m->local->detail.structure.field_count; ++k)
            if (m->local->detail.structure.fields[k].id == f->id) {
               f->local = &m->local->detail.structure.fields[k];
               break;
            }
         if (!f->local)
            continue;
         t = f->local->type;
         if ((f->modifier == 1) != ((f->local->flags & SDL_FIELD_OPTIONAL) != 0) ||
             (f->modifier >= 2) != ((f->local->flags & SDL_FIELD_REPEATED) != 0))
            return false;
         for (k = 0; k < f->dimension_count; ++k) {
            if (t->kind != SDL_TYPE_ARRAY || t->detail.array.count != f->dimensions[k])
               return false;
            t = t->detail.array.element;
         }
         if (t->kind == SDL_TYPE_ARRAY || strcmp(native_name(t), f->type_name))
            return false;
      }
   }
   return true;
}

/* Flatten fixed native records during preparation. Adjacent components of the
   same width share a span, even across nested structures and fixed arrays.
   Offsets preserve native padding; packed wire data never contains padding. */
static bool fixed_plan_add(const SdlDynamicSchema *s, SdlFixedPlan *p, const SdlTypeDesc *t, size_t native,
                           size_t depth) {
   size_t i, width, count = 1;
   const SdlTypeDesc *checked = NULL;
   SdlFixedSpan *span;
   if (depth > SDL_DYNAMIC_MAX_NESTING || ++p->steps > 65536)
      return false;
   if (t->kind == SDL_TYPE_STRUCT) {
      const SdlDynamicMessageDesc *m = schema_message_const(s, native_name(t));
      if (!m || !m->can_encode)
         return false;
      for (i = 0; i < t->detail.structure.field_count; ++i) {
         const SdlFieldDesc *f = &t->detail.structure.fields[i];
         if (m->fields[i].local != f ||
             f->flags & (SDL_FIELD_OPTIONAL | SDL_FIELD_REPEATED) ||
             !fixed_plan_add(s, p, f->type, native + f->offset, depth + 1))
            return false;
      }
      return true;
   }
   if (t->kind == SDL_TYPE_ARRAY) {
      const SdlTypeDesc *element = t->detail.array.element;
      if (element->kind >= SDL_TYPE_INT8 && element->kind <= SDL_TYPE_COMPLEX64 &&
          t->detail.array.count) {
         size_t extra = t->detail.array.count - 1;
         if (!fixed_plan_add(s, p, element, native, depth + 1))
            return false;
         span = &p->spans[p->span_count - 1];
         span->count += extra * (element->size / span->width);
         p->wire_size += extra * element->size;
         return true;
      }
      if (t->detail.array.count > 65536)
         return false;
      for (i = 0; i < t->detail.array.count; ++i)
         if (!fixed_plan_add(s, p, element, native + i * element->size, depth + 1))
            return false;
      return true;
   }
   width = sdl_fixed_wire_size(t);
   if (!width)
      return false;
   if (t->kind == SDL_TYPE_BOOL || t->kind == SDL_TYPE_ENUM)
      checked = t;
   if (t->kind == SDL_TYPE_COMPLEX32 || t->kind == SDL_TYPE_COMPLEX64) {
      width /= 2;
      count = 2;
   }
   if (!checked && t->size != width * count)
      return false;
   if (!checked && p->span_count) {
      span = &p->spans[p->span_count - 1];
      if (!span->checked_type && span->width == width &&
          span->native_offset + span->width * span->count == native) {
         span->count += count;
         p->wire_size += width * count;
         return true;
      }
   }
   /* Bound plan memory for pathological fixed dimensions. Falling back to the
      generic codec is safe and does not reject otherwise valid catalogues. */
   if (p->span_count == 65536)
      return false;
   if (p->span_count == p->capacity) {
      size_t capacity = p->capacity ? p->capacity * 2 : 8;
      void *spans = realloc(p->spans, capacity * sizeof(*p->spans));
      if (!spans)
         return false;
      p->spans = (SdlFixedSpan *)spans;
      p->capacity = capacity;
   }
   span = &p->spans[p->span_count++];
   span->native_offset = native;
   span->wire_offset = p->wire_size;
   span->width = width;
   span->count = count;
   span->checked_type = checked;
   p->checked_values |= checked != NULL;
   p->wire_size += width * count;
   return true;
}

static bool emission_match(const SdlDynamicSchema *, const SdlDynamicMessageDesc *, size_t);
SdlContext *type_prepare(const void *text, size_t size) {
   size_t i;
   SdlContext *c = (SdlContext *)calloc(1, sizeof(*c));
   if (!c)
      return NULL;
   if (!parse_descriptor((const uint8_t *)text, size, &c->schema) || !bind_schema(&c->schema)) {
      type_context_free(c);
      return NULL;
   }
   for (i = 0; i < c->schema.message_count; ++i)
      emission_match(&c->schema, &c->schema.messages[i], 0);
   for (i = 0; i < c->schema.message_count; ++i) {
      SdlDynamicMessageDesc *m = &c->schema.messages[i];
      if (m->can_encode && m->fixed_size &&
          fixed_plan_add(&c->schema, &m->plan, m->local, 0, 0) && m->plan.wire_size == m->fixed_size) {
         m->plan.native_size = m->local->size;
      } else {
         free(m->plan.spans);
         memset(&m->plan, 0, sizeof(m->plan));
      }
   }
   for (i = 0; i < c->schema.message_count; ++i) {
      SdlDynamicMessageDesc *m = &c->schema.messages[i];
      size_t j;
      for (j = 0; j < m->field_count; ++j) {
         SdlDynamicFieldDesc *f = &m->fields[j];
         const SdlDynamicMessageDesc *child = schema_message_const(&c->schema, f->type_name);
         if (!f->dimension_count && child && child->plan.span_count)
            f->record_plan = &child->plan;
      }
   }
   return c;
}
void type_context_free(SdlContext *c) {
   if (c) {
      schema_clear(&c->schema);
      free(c);
   }
}

static bool read_count(SdlDynamicReader *r, uint32_t *out) {
   uint32_t n = 0;
   size_t i;
   for (i = 0; i < 5; ++i) {
      const uint8_t *b;
      if (!reader_read(r, 1, &b) || (i == 4 && *b > 15))
         return false;
      n |= (uint32_t)(*b & 127) << (7 * i);
      if (*b < 128) {
         if (i && !*b)
            return false;
         *out = n;
         return true;
      }
   }
   return false;
}
const char *type_message_name(const SdlContext *c, const void *data, size_t size) {
   SdlDynamicReader r;
   uint32_t id;
   if (!c || !data)
      return NULL;
   r.data = (const uint8_t *)data;
   r.size = size;
   r.offset = 0;
   if (!read_count(&r, &id) || !id || id > c->schema.message_count)
      return NULL;
   return c->schema.messages[id - 1].name;
}

typedef struct {
   uint8_t *data;
   size_t size, offset;
} Writer;
static bool write_bytes(Writer *w, const void *bytes, size_t n) {
   if (n > SIZE_MAX - w->offset || (w->data && n > w->size - w->offset))
      return false;
   if (w->data && n)
      memcpy(w->data + w->offset, bytes, n);
   w->offset += n;
   return true;
}
static bool write_count(Writer *w, size_t n) {
   uint8_t b[5];
   size_t i = 0;
   if (n > UINT32_MAX)
      return false;
   do {
      b[i] = (uint8_t)(n & 127);
      n >>= 7;
      if (n)
         b[i] |= 128;
      ++i;
   } while (n);
   return write_bytes(w, b, i);
}
static bool write_native_sequence(const SdlTypeDesc *t, const void *items, size_t count,
                                  Writer *w) {
   size_t size = sdl_fixed_wire_size(t), bytes, width = size, components = count, i;
   if (!size || count > SIZE_MAX / size)
      return false;
   bytes = count * size;
   if (bytes > SIZE_MAX - w->offset || (w->data && bytes > w->size - w->offset))
      return false;
   if (w->data && bytes) {
      if (t->kind == SDL_TYPE_BOOL) {
         for (i = 0; i < count; ++i)
            w->data[w->offset + i] = ((const bool *)items)[i] ? 1 : 0;
      } else {
         if (t->kind == SDL_TYPE_COMPLEX32 || t->kind == SDL_TYPE_COMPLEX64) {
            width /= 2;
            components *= 2;
         }
         sdl_wire_convert_array(w->data + w->offset, items, width, components);
      }
   }
   w->offset += bytes;
   return true;
}
static bool write_fixed_records(const SdlFixedPlan *p, const void *items,
                                 size_t count, Writer *w) {
   size_t bytes, i, j;
   uint8_t *dst;
   if (!count)
      return true;
   if (count > SIZE_MAX / p->wire_size || count > SIZE_MAX / p->native_size)
      return false;
   bytes = count * p->wire_size;
   if (bytes > SIZE_MAX - w->offset || (w->data && bytes > w->size - w->offset))
      return false;
   if (!w->data) {
      /* Preserve encode_size validation for enums without converting numbers. */
      for (j = 0; j < p->span_count; ++j) {
         const SdlFixedSpan *span = &p->spans[j];
         if (span->checked_type && span->checked_type->kind == SDL_TYPE_ENUM)
            for (i = 0; i < count; ++i) {
               uint8_t scratch[16];
               const uint8_t *src = (const uint8_t *)items + i * p->native_size + span->native_offset;
               if (!sdl_value_encode_fixed(span->checked_type, src, scratch, sizeof(scratch)))
                  return false;
            }
      }
      w->offset += bytes;
      return true;
   }
   dst = w->data + w->offset;
   if (p->span_count == 1 && !p->spans[0].checked_type &&
       p->spans[0].native_offset == 0 && p->native_size == p->wire_size) {
      sdl_wire_convert_array(dst, items, p->spans[0].width,
                             count * p->spans[0].count);
   } else {
      for (i = 0; i < count; ++i)
         for (j = 0; j < p->span_count; ++j) {
            const SdlFixedSpan *span = &p->spans[j];
            const uint8_t *src = (const uint8_t *)items + i * p->native_size + span->native_offset;
            uint8_t *wire = dst + i * p->wire_size + span->wire_offset;
            if (span->checked_type) {
               if (!sdl_value_encode_fixed(span->checked_type, src, wire, span->width))
                  return false;
            } else {
               sdl_wire_convert_array(wire, src, span->width, span->count);
            }
         }
   }
   w->offset += bytes;
   return true;
}

static bool write_native(const SdlDynamicSchema *s, const SdlTypeDesc *t,
                          const void *value, Writer *w, size_t length) {
   size_t i;
   uint8_t temp[16];
   if (t->kind == SDL_TYPE_STRUCT) {
      const SdlDynamicMessageDesc *m = schema_message_const(s, native_name(t));
      if (m && m->plan.span_count)
         return write_fixed_records(&m->plan, value, 1, w);
      for (i = 0; i < t->detail.structure.field_count; ++i) {
         const SdlFieldDesc *f = &t->detail.structure.fields[i];
         const uint8_t *base = (const uint8_t *)value;
         const void *items = base + f->offset;
         size_t count = 1, j;
         if (f->flags & SDL_FIELD_OPTIONAL) {
            count = *(const bool *)(base + f->presence_offset) ? 1 : 0;
            if (!write_count(w, count))
               return false;
         }
         if (f->flags & SDL_FIELD_REPEATED) {
            count = *(const uint32_t *)(base + f->count_offset);
            items = *(const void *const *)items;
            if ((count && !items) || !write_count(w, count))
               return false;
         }
         if (f->type->kind == SDL_TYPE_STRUCT) {
            const SdlDynamicMessageDesc *child = schema_message_const(s, native_name(f->type));
            if (child && child->plan.span_count) {
               if (!write_fixed_records(&child->plan, items, count, w))
                  return false;
               continue;
            }
         }
         if (f->type->kind <= SDL_TYPE_COMPLEX64) {
            if (!write_native_sequence(f->type, items, count, w))
               return false;
            continue;
         }
         for (j = 0; j < count; ++j) {
            size_t n = SIZE_MAX;
            const void *item = (f->flags & SDL_FIELD_REPEATED)
                                   ? (const uint8_t *)items + j * f->type->size
                                   : items;
            if (f->type->kind == SDL_TYPE_STRING && f->string_length_offset != SDL_NO_OFFSET) {
               if (f->flags & SDL_FIELD_REPEATED) {
                  const uint32_t *ls = *(const uint32_t *const *)(base + f->string_length_offset);
                  if (ls && ls[j])
                     n = ls[j];
               } else {
                  uint32_t v = *(const uint32_t *)(base + f->string_length_offset);
                  if (v)
                     n = v;
               }
            }
            if (!write_native(s, f->type, item, w, n))
               return false;
         }
      }
      return true;
   }
   if (t->kind == SDL_TYPE_ARRAY) {
      for (i = 0; i < t->detail.array.count; ++i)
         if (!write_native(s, t->detail.array.element,
                           (const uint8_t *)value + i * t->detail.array.element->size, w, SIZE_MAX))
            return false;
      return true;
   }
   if (t->kind == SDL_TYPE_STRING) {
      const char *s = *(const char *const *)value;
      if (!s && length != SIZE_MAX)
         return false;
      if (length == SIZE_MAX)
         length = s ? strlen(s) : 0;
      return length <= UINT32_MAX && sdl_wire_valid_utf8((const uint8_t *)s, length) &&
             write_count(w, length) && write_bytes(w, s, length);
   }
   i = sdl_fixed_wire_size(t);
   return i && i <= sizeof(temp) && sdl_value_encode_fixed(t, value, temp, sizeof(temp)) &&
          write_bytes(w, temp, i);
}
static bool emission_match(const SdlDynamicSchema *s, const SdlDynamicMessageDesc *m,
                           size_t depth) {
   size_t i;
   if (m->emission_ready)
      return m->can_encode;
   s->messages[m - s->messages].emission_ready = true;
   if (depth > 64 || !m->local || m->field_count != m->local->detail.structure.field_count)
      return false;
   for (i = 0; i < m->field_count; ++i) {
      const SdlDynamicMessageDesc *child = schema_message_const(s, m->fields[i].type_name);
      if (!m->fields[i].local || (child && !emission_match(s, child, depth + 1)))
         return false;
   }
   s->messages[m - s->messages].can_encode = true;
   return true;
}
size_t type_encode_size(const SdlContext *c, const char *name, const void *value) {
   const SdlDynamicMessageDesc *m;
   Writer w = {NULL, 0, 0};
   if (!c || !name || !value)
      return 0;
   m = schema_message_const(&c->schema, name);
   if (!m || !m->can_encode || !write_count(&w, (size_t)(m - c->schema.messages) + 1) ||
       !write_native(&c->schema, m->local, value, &w, SIZE_MAX))
      return 0;
   return w.offset;
}
void *type_encode(const SdlContext *c, const char *name, const void *value, size_t *size) {
   Writer w;
   const SdlDynamicMessageDesc *m;
   if (!size)
      return NULL;
   *size = 0;
   w.size = type_encode_size(c, name, value);
   if (!w.size)
      return NULL;
   w.data = (uint8_t *)malloc(w.size);
   w.offset = 0;
   if (!w.data)
      return NULL;
   m = schema_message_const(&c->schema, name);
   if (!write_count(&w, (size_t)(m - c->schema.messages) + 1) ||
       !write_native(&c->schema, m->local, value, &w, SIZE_MAX)) {
      free(w.data);
      return NULL;
   }
   *size = w.offset;
   return w.data;
}

/* Native decode uses a validation/size pass and one allocation, then a fill pass. */
typedef struct {
   uint8_t *data;
   size_t used;
} Pool;
static bool reserve_pool(Pool *p, size_t n, size_t alignment, void **out) {
   size_t padding = alignment > 1 ? alignment - 1 : 0;
   if (p->data && alignment > 1) {
      size_t rem = (size_t)((uintptr_t)(p->data + p->used) % alignment);
      padding = rem ? alignment - rem : 0;
   }
   if (p->used > SIZE_MAX - padding || n > SIZE_MAX - p->used - padding)
      return false;
   p->used += padding;
   if (out)
      *out = p->data ? p->data + p->used : NULL;
   p->used += n;
   return true;
}
static const SdlTypeDesc *primitive_native(const char *name) {
   const SdlTypeDesc *const ts[] = {&SDL_BOOL_DESC,    &SDL_INT8_DESC,      &SDL_INT16_DESC,
                                    &SDL_INT32_DESC,   &SDL_INT64_DESC,     &SDL_FLOAT32_DESC,
                                    &SDL_FLOAT64_DESC, &SDL_COMPLEX32_DESC, &SDL_COMPLEX64_DESC};
   size_t i;
   for (i = 0; i < sizeof(ts) / sizeof(ts[0]); ++i)
      if (!strcmp(native_name(ts[i]), name))
         return ts[i];
   return NULL;
}
static bool read_native_value(const SdlDynamicSchema *, const char *, const uint32_t *, size_t,
                              const SdlTypeDesc *, void *, uint32_t *, SdlDynamicReader *, Pool *,
                              size_t);
static bool read_fixed_records(const SdlDynamicSchema *s, const SdlFixedPlan *p,
                                size_t count, void *items, bool validate_local, SdlDynamicReader *r) {
   const uint8_t *bytes;
   size_t i, j;
   if (count > SIZE_MAX / p->wire_size || !reader_read(r, count * p->wire_size, &bytes))
      return false;
   if (!items && !p->checked_values)
      return true;
   if (p->span_count == 1 && !p->spans[0].checked_type &&
       p->spans[0].native_offset == 0 && p->native_size == p->wire_size) {
      if (items)
         sdl_wire_convert_array(items, bytes, p->spans[0].width,
                                count * p->spans[0].count);
      return true;
   }
   for (i = 0; i < count; ++i)
      for (j = 0; j < p->span_count; ++j) {
         const SdlFixedSpan *span = &p->spans[j];
         const uint8_t *wire = bytes + i * p->wire_size + span->wire_offset;
         uint8_t scratch[16];
         uint8_t *dst = items ? (uint8_t *)items + i * p->native_size + span->native_offset : NULL;
         if (span->checked_type) {
            if (span->checked_type->kind == SDL_TYPE_ENUM &&
                !enum_item(s, native_name(span->checked_type), (int32_t)sdl_wire_read_u32(wire)))
               return false;
            if (span->checked_type->kind == SDL_TYPE_ENUM && !validate_local)
               continue;
            if (!sdl_value_decode_fixed(span->checked_type, wire, dst ? dst : scratch))
               return false;
         } else if (dst) {
            sdl_wire_convert_array(dst, wire, span->width, span->count);
         }
      }
   return true;
}

static bool read_native_message(const SdlDynamicSchema *s, const SdlDynamicMessageDesc *m,
                                void *value, SdlDynamicReader *r, Pool *pool, size_t depth) {
   size_t i, j;
   if (depth > 64)
      return false;
   if (m->plan.span_count)
      return read_fixed_records(s, &m->plan, 1, value, pool != NULL, r);
   for (i = 0; i < m->field_count; ++i) {
      const SdlDynamicFieldDesc *f = &m->fields[i];
      const SdlFieldDesc *local = value ? f->local : NULL;
      uint32_t count = 1;
      void *items = NULL;
      uint32_t *lengths = NULL;
      if (f->modifier && !read_count(r, &count))
         return false;
      if (f->modifier == 1 && count > 1)
         return false;
      {
         size_t unit = sdl_fixed_type_size(f->type_name, s, NULL, 0), k;
         for (k = 0; unit && k < f->dimension_count; ++k)
            unit *= f->dimensions[k];
         if (unit && count > (r->size - r->offset) / unit)
            return false;
         if (!unit && count > 1048576)
            return false;
      }
      if (f->local && pool) {
         const SdlFieldDesc *lf = f->local;
         if (f->modifier >= 2 && count) {
            if (count > SIZE_MAX / lf->type->size ||
                !reserve_pool(pool, (size_t)count * lf->type->size, lf->type->alignment, &items))
               return false;
            if (lf->type->kind == SDL_TYPE_STRING &&
                !reserve_pool(pool, (size_t)count * sizeof(uint32_t), SDL_UINT32_ALIGNMENT,
                              (void **)&lengths))
               return false;
         }
         if (local) {
            uint8_t *base = (uint8_t *)value;
            if (f->modifier >= 2) {
               *(uint32_t *)(base + local->count_offset) = count;
               *(void **)(base + local->offset) = items;
               if (local->type->kind == SDL_TYPE_STRING)
                  *(uint32_t **)(base + local->string_length_offset) = lengths;
            } else {
               items = base + local->offset;
               if (f->modifier == 1)
                  *(bool *)(base + local->presence_offset) = count != 0;
               if (local->type->kind == SDL_TYPE_STRING)
                  lengths = (uint32_t *)(base + local->string_length_offset);
            }
         }
      }
      if (f->record_plan) {
         if (!read_fixed_records(s, f->record_plan, count, local ? items : NULL,
                                  f->local && pool, r))
            return false;
         continue;
      }
      /* Resolve once per field and validate a whole primitive span. No numeric
         conversion is needed in the storage sizing or unknown-field pass. */
      if (!f->dimension_count) {
         const SdlTypeDesc *primitive = primitive_native(f->type_name);
         if (primitive) {
            const uint8_t *bytes;
            size_t unit = primitive_size(f->type_name), n, width = unit, components = count;
            if (count > SIZE_MAX / unit || !reader_read(r, n = (size_t)count * unit, &bytes))
               return false;
            if (primitive->kind == SDL_TYPE_BOOL) {
               for (j = 0; j < count; ++j) {
                  if (bytes[j] > 1)
                     return false;
                  if (local)
                     ((bool *)items)[j] = bytes[j] != 0;
               }
            } else if (local && n) {
               if (primitive->kind == SDL_TYPE_COMPLEX32 || primitive->kind == SDL_TYPE_COMPLEX64) {
                  width /= 2;
                  components *= 2;
               }
               sdl_wire_convert_array(items, bytes, width, components);
            }
            continue;
         }
      }
      for (j = 0; j < count; ++j) {
         void *item =
             local ? (f->modifier >= 2 ? (uint8_t *)items + j * local->type->size : items) : NULL;
         if (!read_native_value(s, f->type_name, f->dimensions, f->dimension_count,
                                f->local && pool ? f->local->type : NULL, item,
                                lengths ? lengths + j : NULL, r, f->local ? pool : NULL, depth + 1))
            return false;
      }
   }
   return true;
}
static bool read_native_value(const SdlDynamicSchema *s, const char *name, const uint32_t *dims,
                              size_t nd, const SdlTypeDesc *local, void *value, uint32_t *length,
                              SdlDynamicReader *r, Pool *pool, size_t depth) {
   const uint8_t *bytes;
   size_t n, i;
   const SdlDynamicMessageDesc *m;
   const SdlTypeDesc *primitive;
   if (depth > 64)
      return false;
   if (nd) {
      size_t unit = sdl_fixed_type_size(name, s, NULL, 0);
      for (i = 1; i < nd; ++i)
         unit *= dims[i];
      if (!unit || dims[0] > (r->size - r->offset) / unit)
         return false;
      for (i = 0; i < dims[0]; ++i)
         if (!read_native_value(
                 s, name, dims + 1, nd - 1, local ? local->detail.array.element : NULL,
                 value ? (uint8_t *)value + i * local->detail.array.element->size : NULL, NULL, r,
                 pool, depth + 1))
            return false;
      return true;
   }
   m = schema_message_const(s, name);
   if (m)
      return read_native_message(s, m, value, r, pool, depth + 1);
   if (!strcmp(name, "string")) {
      uint32_t count;
      void *out = NULL;
      if (!read_count(r, &count) || !reader_read(r, count, &bytes) ||
          !sdl_wire_valid_utf8(bytes, count))
         return false;
      if (pool && !reserve_pool(pool, (size_t)count + 1, 1, &out))
         return false;
      if (value) {
         memcpy(out, bytes, count);
         ((char *)out)[count] = 0;
         *(void **)value = out;
         if (length)
            *length = count;
      }
      return true;
   }
   primitive = primitive_native(name);
   n = primitive ? primitive_size(name) : 4;
   if (!reader_read(r, n, &bytes))
      return false;
   if (!primitive) {
      int32_t v;
      sdl_wire_decode_native(&v, bytes, 4);
      if (!enum_item(s, name, v))
         return false;
      if (local && !sdl_value_decode_fixed(local, bytes, value ? value : (void *)&v))
         return false;
      return true;
   }
   {
      uint8_t scratch[32];
      return sdl_value_decode_fixed(primitive, bytes, value ? value : scratch);
   }
}
static const SdlDynamicMessageDesc *start_message(const SdlContext *c, const void *data,
                                                  size_t size, SdlDynamicReader *r) {
   uint32_t id;
   if (!c || !data)
      return NULL;
   r->data = (const uint8_t *)data;
   r->size = size;
   r->offset = 0;
   if (!read_count(r, &id) || !id || id > c->schema.message_count)
      return NULL;
   return &c->schema.messages[id - 1];
}
size_t type_decode_size(const SdlContext *c, const void *data, size_t size) {
   SdlDynamicReader r;
   Pool p = {NULL, 0};
   const SdlDynamicMessageDesc *m = start_message(c, data, size, &r);
   if (!m || !m->local || !read_native_message(&c->schema, m, NULL, &r, &p, 0) ||
       r.offset != size || m->local->size > SIZE_MAX - p.used)
      return 0;
   return m->local->size + p.used;
}
void *type_decode(const SdlContext *c, const void *data, size_t size) {
   size_t n = type_decode_size(c, data, size);
   SdlDynamicReader r;
   const SdlDynamicMessageDesc *m;
   void *out;
   Pool p;
   if (!n)
      return NULL;
   m = start_message(c, data, size, &r);
   out = calloc(1, n);
   if (!out)
      return NULL;
   p.data = (uint8_t *)out + m->local->size;
   p.used = 0;
   if (!read_native_message(&c->schema, m, out, &r, &p, 0) || r.offset != size) {
      free(out);
      return NULL;
   }
   return out;
}

static bool same_message(const SdlDynamicMessageDesc *a, const SdlDynamicMessageDesc *b) {
   size_t i, j;
   if (a->field_count != b->field_count)
      return false;
   for (i = 0; i < a->field_count; ++i) {
      const SdlDynamicFieldDesc *x = &a->fields[i], *y = &b->fields[i];
      if (x->id != y->id || x->modifier != y->modifier || strcmp(x->name, y->name) ||
          strcmp(x->type_name, y->type_name) || x->dimension_count != y->dimension_count)
         return false;
      for (j = 0; j < x->dimension_count; ++j)
         if (x->dimensions[j] != y->dimensions[j])
            return false;
   }
   return true;
}
static bool same_enum(const SdlDynamicEnumDesc *a, const SdlDynamicEnumDesc *b) {
   size_t i;
   if (a->item_count != b->item_count)
      return false;
   for (i = 0; i < a->item_count; ++i)
      if (a->items[i].value != b->items[i].value || strcmp(a->items[i].name, b->items[i].name))
         return false;
   return true;
}
static int compare_message(const void *a, const void *b) {
   return strcmp(((const SdlDynamicMessageDesc *)a)->name,
                 ((const SdlDynamicMessageDesc *)b)->name);
}
static int compare_enum(const void *a, const void *b) {
   return strcmp(((const SdlDynamicEnumDesc *)a)->name, ((const SdlDynamicEnumDesc *)b)->name);
}
static bool text_out(Writer *w, const char *s) { return write_bytes(w, s, strlen(s)); }
static bool decimal_out(Writer *w, long long n) {
   char b[32];
   snprintf(b, sizeof(b), "%lld", n);
   return text_out(w, b);
}
static bool render_schema(const SdlDynamicSchema *s, Writer *w) {
   size_t i, j, k;
   const char *mods[] = {"required", "optional", "repeated", "packed"};
   if (!text_out(w, "SDL2\n"))
      return false;
   for (i = 0; i < s->message_count; ++i) {
      const SdlDynamicMessageDesc *m = &s->messages[i];
      if (!text_out(w, "message ") || !text_out(w, m->name) || !text_out(w, " {\n"))
         return false;
      for (j = 0; j < m->field_count; ++j) {
         const SdlDynamicFieldDesc *f = &m->fields[j];
         if (!text_out(w, "  ") || !decimal_out(w, f->id) || !text_out(w, ": ") ||
             !text_out(w, mods[f->modifier]) || !text_out(w, " ") || !text_out(w, f->type_name))
            return false;
         for (k = 0; k < f->dimension_count; ++k)
            if (!text_out(w, "[") || !decimal_out(w, f->dimensions[k]) || !text_out(w, "]"))
               return false;
         if (!text_out(w, " ") || !text_out(w, f->name) || !text_out(w, ";\n"))
            return false;
      }
      if (!text_out(w, "}\n"))
         return false;
   }
   for (i = 0; i < s->enum_count; ++i) {
      const SdlDynamicEnumDesc *e = &s->enums[i];
      if (!text_out(w, "enum ") || !text_out(w, e->name) || !text_out(w, " {\n"))
         return false;
      for (j = 0; j < e->item_count; ++j)
         if (!text_out(w, "  ") || !text_out(w, e->items[j].name) || !text_out(w, " = ") ||
             !decimal_out(w, e->items[j].value) || !text_out(w, ";\n"))
            return false;
      if (!text_out(w, "}\n"))
         return false;
   }
   return true;
}
char *type_description(const char *const *types, size_t count, size_t *size) {
   SdlDynamicSchema merged = {0}, part = {0};
   size_t i, j;
   Writer w = {NULL, 0, 0};
   char *result = NULL;
   if (!size || !types || !count)
      return NULL;
   *size = 0;
   for (i = 0; i < count; ++i) {
      const SdlTypeDesc *t = sdl_lookup_type(types[i]);
      if (!t || !parse_descriptor(t->schema_descriptor, t->schema_descriptor_size, &part))
         goto done;
      for (j = 0; j < part.message_count; ++j) {
         SdlDynamicMessageDesc *existing = schema_message(&merged, part.messages[j].name), *next;
         if (existing) {
            if (!same_message(existing, &part.messages[j]))
               goto done;
            continue;
         }
         next = (SdlDynamicMessageDesc *)realloc(merged.messages,
                                                 (merged.message_count + 1) * sizeof(*next));
         if (!next)
            goto done;
         merged.messages = next;
         next[merged.message_count++] = part.messages[j];
         memset(&part.messages[j], 0, sizeof(part.messages[j]));
      }
      for (j = 0; j < part.enum_count; ++j) {
         const SdlDynamicEnumDesc *existing = schema_enum(&merged, part.enums[j].name);
         SdlDynamicEnumDesc *next;
         if (existing) {
            if (!same_enum(existing, &part.enums[j]))
               goto done;
            continue;
         }
         next =
             (SdlDynamicEnumDesc *)realloc(merged.enums, (merged.enum_count + 1) * sizeof(*next));
         if (!next)
            goto done;
         merged.enums = next;
         next[merged.enum_count++] = part.enums[j];
         memset(&part.enums[j], 0, sizeof(part.enums[j]));
      }
      schema_clear(&part);
   }
   if (merged.message_count > 1)
      qsort(merged.messages, merged.message_count, sizeof(*merged.messages), compare_message);
   if (merged.enum_count > 1)
      qsort(merged.enums, merged.enum_count, sizeof(*merged.enums), compare_enum);
   if (!render_schema(&merged, &w) || w.offset > SDL_DYNAMIC_MAX_DESCRIPTOR)
      goto done;
   w.size = w.offset;
   w.offset = 0;
   w.data = (uint8_t *)malloc(w.size + 1);
   if (!w.data)
      goto done;
   if (!render_schema(&merged, &w)) {
      free(w.data);
      goto done;
   }
   w.data[w.offset] = 0;
   *size = w.offset;
   result = (char *)w.data;
done:
   schema_clear(&part);
   schema_clear(&merged);
   return result;
}

static bool value_string_set(SdlDynamicValue *value, const uint8_t *data, size_t size) {
   uint8_t *copy = NULL;
   if (size != 0) {
      copy = (uint8_t *)malloc(size);
      if (copy == NULL)
         return false;
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
      if (size != 4)
         return;
      sdl_wire_decode_native(&enum_value, data, 4);
      item = enum_item(schema, type_name, enum_value);
      if (item == NULL)
         return;
      value->kind = SDL_DYNAMIC_ENUM;
      value->value.enumeration.type_name = copy_string(type_name);
      value->value.enumeration.name = copy_string(item->name);
      value->value.enumeration.value = enum_value;
      if (value->value.enumeration.type_name == NULL || value->value.enumeration.name == NULL)
         value_clear(value);
      return;
   }
   if (expected == 0 || size != expected)
      return;
   if (strcmp(type_name, "bool") == 0) {
      if (data[0] > 1)
         return;
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

static bool dynamic_value(const SdlDynamicSchema *, const char *, const uint32_t *, size_t,
                          SdlDynamicReader *, SdlDynamicValue *, size_t);
static SdlDynamicMessage *dynamic_message(const SdlDynamicSchema *s, const SdlDynamicMessageDesc *m,
                                          SdlDynamicReader *r, size_t depth) {
   SdlDynamicMessage *out;
   size_t i, j;
   if (depth > 64)
      return NULL;
   out = (SdlDynamicMessage *)calloc(1, sizeof(*out));
   if (!out)
      return NULL;
   out->type_name = copy_string(m->name);
   out->field_count = m->field_count;
   out->fields = (SdlDynamicField *)calloc(m->field_count, sizeof(*out->fields));
   if (!out->type_name || (m->field_count && !out->fields))
      goto fail;
   for (i = 0; i < m->field_count; ++i) {
      const SdlDynamicFieldDesc *f = &m->fields[i];
      uint32_t count = 1;
      SdlDynamicValue *v = &out->fields[i].value;
      out->fields[i].name = copy_string(f->name);
      if (!out->fields[i].name)
         goto fail;
      if (f->modifier && !read_count(r, &count))
         goto fail;
      if (f->modifier == 1 && count > 1)
         goto fail;
      if (f->modifier >= 2) {
         if (count && sizeof(*v->value.array.items) > SIZE_MAX / count)
            goto fail;
         v->kind = SDL_DYNAMIC_ARRAY;
         v->value.array.count = count;
         v->value.array.items = (SdlDynamicValue *)calloc(count, sizeof(*v->value.array.items));
         if (count && !v->value.array.items)
            goto fail;
         for (j = 0; j < count; ++j)
            if (!dynamic_value(s, f->type_name, f->dimensions, f->dimension_count, r,
                               &v->value.array.items[j], depth + 1))
               goto fail;
      } else if (count && !dynamic_value(s, f->type_name, f->dimensions, f->dimension_count, r, v,
                                         depth + 1))
         goto fail;
   }
   return out;
fail:
   message_clear(out);
   return NULL;
}
static bool dynamic_value(const SdlDynamicSchema *s, const char *name, const uint32_t *dims,
                          size_t nd, SdlDynamicReader *r, SdlDynamicValue *out, size_t depth) {
   const SdlDynamicMessageDesc *m;
   const uint8_t *bytes;
   uint32_t len;
   size_t n, i;
   if (depth > 64)
      return false;
   if (nd) {
      out->kind = SDL_DYNAMIC_ARRAY;
      out->value.array.count = dims[0];
      out->value.array.items = (SdlDynamicValue *)calloc(dims[0], sizeof(*out->value.array.items));
      if (!out->value.array.items)
         return false;
      for (i = 0; i < dims[0]; ++i)
         if (!dynamic_value(s, name, dims + 1, nd - 1, r, &out->value.array.items[i], depth + 1))
            return false;
      return true;
   }
   m = schema_message_const(s, name);
   if (m) {
      out->kind = SDL_DYNAMIC_MESSAGE;
      out->value.message = dynamic_message(s, m, r, depth + 1);
      return out->value.message != NULL;
   }
   if (!strcmp(name, "string")) {
      if (!read_count(r, &len))
         return false;
      n = len;
   } else {
      n = primitive_size(name);
      if (!n)
         n = 4;
   }
   if (!reader_read(r, n, &bytes))
      return false;
   decode_value(name, bytes, n, s, depth, out);
   return out->kind != SDL_DYNAMIC_NULL;
}
SdlDynamicMessage *type_decode_dynamic(const SdlContext *c, const void *data, size_t size) {
   SdlDynamicReader r, check;
   const SdlDynamicMessageDesc *m = start_message(c, data, size, &r);
   SdlDynamicMessage *out;
   if (!m)
      return NULL;
   check = r;
   if (!read_native_message(&c->schema, m, NULL, &check, NULL, 0) || check.offset != size)
      return NULL;
   out = dynamic_message(&c->schema, m, &r, 0);
   if (out && r.offset != size) {
      message_clear(out);
      return NULL;
   }
   return out;
}

/* Descriptor-only queries used by foreign-language typed bindings. */
bool type_context_validate(const SdlContext *c, const void *data, size_t size) {
   SdlDynamicReader r;
   const SdlDynamicMessageDesc *m = start_message(c, data, size, &r);
   return m && read_native_message(&c->schema, m, NULL, &r, NULL, 0) && r.offset == size;
}
uint32_t type_context_message_id(const SdlContext *c, const char *name) {
   const SdlDynamicMessageDesc *m = c ? schema_message_const(&c->schema, name) : NULL;
   return m ? (uint32_t)(m - c->schema.messages + 1) : 0;
}
static bool foreign_field_match(const SdlDynamicFieldDesc *a, const SdlDynamicFieldDesc *b) {
   return !strcmp(a->type_name, b->type_name) &&
      (a->modifier == b->modifier || (a->modifier >= 2 && b->modifier >= 2)) &&
      a->dimension_count == b->dimension_count &&
      (!a->dimension_count || !memcmp(a->dimensions, b->dimensions,
                                      a->dimension_count * sizeof(uint32_t)));
}
bool type_context_compatible(const SdlContext *remote, const SdlContext *local) {
   size_t i, j, k;
   if (!remote || !local)
      return false;
   /* A shared type name must not change between enum and message. */
   for (i = 0; i < local->schema.enum_count; ++i)
      if (schema_message_const(&remote->schema, local->schema.enums[i].name))
         return false;
   for (i = 0; i < local->schema.message_count; ++i) {
      const SdlDynamicMessageDesc *l = &local->schema.messages[i];
      const SdlDynamicMessageDesc *r = schema_message_const(&remote->schema, l->name);
      if (schema_enum(&remote->schema, l->name))
         return false;
      if (!r)
         continue;
      for (j = 0; j < l->field_count; ++j)
         for (k = 0; k < r->field_count; ++k)
            if (l->fields[j].id == r->fields[k].id &&
                !foreign_field_match(&l->fields[j], &r->fields[k]))
               return false;
   }
   return true;
}
bool type_context_exact_message(const SdlContext *remote, const SdlContext *local, const char *name) {
   const SdlDynamicMessageDesc *r = remote ? schema_message_const(&remote->schema, name) : NULL;
   const SdlDynamicMessageDesc *l = local ? schema_message_const(&local->schema, name) : NULL;
   size_t i;
   if (!r || !l || r->field_count != l->field_count)
      return false;
   for (i = 0; i < l->field_count; ++i)
      if (l->fields[i].id != r->fields[i].id || !foreign_field_match(&l->fields[i], &r->fields[i]))
         return false;
   return true;
}
const SdlDynamicValue *type_dynamic_get_id(const SdlContext *c, const SdlDynamicMessage *message,
                                          uint32_t id) {
   const SdlDynamicMessageDesc *m = c && message ?
      schema_message_const(&c->schema, message->type_name) : NULL;
   size_t i;
   if (m)
      for (i = 0; i < m->field_count; ++i)
         if (m->fields[i].id == id)
            return type_dynamic_get(message, m->fields[i].name);
   return NULL;
}

/* Memoize the reachable type graph once, including shared substructures. */
static bool direct_layout_match(const SdlContext *remote, const SdlContext *local,
                                const SdlDynamicMessageDesc *m, uint8_t *memo) {
   size_t index = (size_t)(m - local->schema.messages), i;
   if (memo[index])
      return memo[index] == 2;
   memo[index] = 1; /* A cycle is incompatible as well. */
   if (!type_context_exact_message(remote, local, m->name))
      return false;
   for (i = 0; i < m->field_count; ++i) {
      const SdlDynamicMessageDesc *child = schema_message_const(&local->schema, m->fields[i].type_name);
      const SdlDynamicEnumDesc *e = schema_enum(&local->schema, m->fields[i].type_name);
      if (child && !direct_layout_match(remote, local, child, memo))
         return false;
      if (e) {
         const SdlDynamicEnumDesc *r = schema_enum(&remote->schema, e->name);
         size_t j;
         if (!r || r->item_count != e->item_count)
            return false;
         for (j = 0; j < e->item_count; ++j)
            if (!enum_item(&remote->schema, e->name, e->items[j].value))
               return false;
      }
   }
   memo[index] = 2;
   return true;
}
uint8_t *type_context_direct_layouts(const SdlContext *remote, const SdlContext *local, size_t *count) {
   uint8_t *flags, *memo;
   size_t i;
   if (!remote || !local || !count)
      return NULL;
   *count = remote->schema.message_count;
   flags = calloc(*count ? *count : 1, 1);
   memo = calloc(local->schema.message_count ? local->schema.message_count : 1, 1);
   if (!flags || !memo) {
      free(flags);
      free(memo);
      return NULL;
   }
   for (i = 0; i < *count; ++i) {
      const SdlDynamicMessageDesc *m = schema_message_const(&local->schema, remote->schema.messages[i].name);
      flags[i] = m && direct_layout_match(remote, local, m, memo);
   }
   free(memo);
   return flags;
}
