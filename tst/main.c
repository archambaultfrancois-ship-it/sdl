#include "type_engine.h"
#include "type_private.h"
#include "type_registry.h"
#include "sdl_wire.h"
#include "sdl_registry.h"
#include "wire_example.h"
#include "codec_cases.h"
#include "schema.h"
#include "empty_message.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const unsigned char fixture[] = {1,   1,   1,   255, 254, 5,  195, 169,
                                        116, 195, 169, 2,   1,   44, 255, 255};
static void registry_cases(void) {
   static SdlTypeDesc types[1025];
   static char names[1025][32];
   SdlTypeDesc conflicting = {0};
   size_t i;
   assert(!sdl_register_type(NULL));
   assert(!sdl_register_type(&SDL_INT32_DESC));
   assert(register_all_types());
   for (i = 0; i < 1025; ++i) {
      snprintf(names[i], sizeof(names[i]), "RegistryTest%zu", i);
      types[i].kind = SDL_TYPE_STRUCT;
      types[i].name = names[i];
      assert(sdl_register_type(&types[i]));
   }
   for (i = 0; i < 1025; ++i) {
      assert(sdl_lookup_type(names[i]) == &types[i]);
      assert(sdl_register_type(&types[i]));
   }
   conflicting.kind = SDL_TYPE_STRUCT;
   conflicting.name = names[0];
   assert(!sdl_register_type(&conflicting));
   assert(sdl_lookup_type(names[0]) == &types[0]);
   conflicting.name = "Packet";
   assert(!sdl_register_type(&conflicting));
   assert(register_all_types());
   assert(!sdl_lookup_type("RegistryTestMissing"));
}
static void envelope_cases(void) {
   const unsigned char payload[]={1,0};
   const uint32_t ids[]={0,0x89abcdefu,0xffffffffu};
   size_t i,j,n,pn;uint32_t id;const void *p;
   for(i=0;i<3;i++) {
      unsigned char *wire=type_envelope_pack(ids[i],payload,2,&n);
      assert(wire && n==11 && !memcmp(wire,"S2PS\002",5));
      assert(wire[5]==(unsigned char)(ids[i]>>24) && wire[8]==(unsigned char)ids[i]);
      assert(type_envelope_unpack(wire,n,&id,&p,&pn) && id==ids[i] && pn==2 && p==wire+9 && !memcmp(p,payload,2));
      for(j=0;j<=9;j++) assert(!type_envelope_unpack(wire,j,&id,&p,&pn));
      for(j=0;j<5;j++){wire[j]^=1;assert(!type_envelope_unpack(wire,n,&id,&p,&pn));wire[j]^=1;}
      free(wire);
   }
   assert(!type_envelope_pack(0,payload,0,&n));
   assert(!type_envelope_pack(0,payload,SIZE_MAX,&n));
   assert(!type_envelope_unpack(NULL,11,&id,&p,&pn));
}
static void compatibility_cases(void) {
   FILE *file = fopen("tst/fixtures/compatibility/cases.tsv", "r");
   char line[1024];
   assert(file);
   while (fgets(line, sizeof(line), file)) {
      char *columns[12], filename[256], p[1024], c[1024];
      size_t i, pn, cn;
      FILE *schema;
      SdlCompatibilityDiagnostic *d;
      columns[0] = strtok(line, "\t\n");
      for (i = 1; i < 12; ++i) { columns[i] = strtok(NULL, "\t\n"); assert(columns[i]); }
      snprintf(filename, sizeof(filename), "tst/fixtures/compatibility/%s", columns[1]);
      schema = fopen(filename, "rb"); assert(schema); pn = fread(p, 1, sizeof(p), schema); fclose(schema);
      snprintf(filename, sizeof(filename), "tst/fixtures/compatibility/%s", columns[2]);
      schema = fopen(filename, "rb"); assert(schema); cn = fread(c, 1, sizeof(c), schema); fclose(schema);
      d = type_compare_catalogues(p, pn, c, cn, !strcmp(columns[3], "-") ? NULL : columns[3], !strcmp(columns[4], "1"));
      assert(d);
      assert(!strcmp(type_compatibility_code_name(d->code), columns[5]));
      assert(!strcmp(d->message, !strcmp(columns[6], "-") ? "" : columns[6]));
      assert(!strcmp(d->path, !strcmp(columns[7], "-") ? "" : columns[7]));
      assert(d->field_id == (uint32_t)strtoul(columns[8], NULL, 10));
      assert(!strcmp(d->publisher_type, !strcmp(columns[9], "-") ? "" : columns[9]));
      assert(!strcmp(d->consumer_type, !strcmp(columns[10], "-") ? "" : columns[10]));
      assert(d->enum_value == (int32_t)strtol(columns[11], NULL, 10));
      type_compatibility_free(d);
   }
   fclose(file);
}
static void catalogue_api_cases(void) {
   FILE *file = fopen("tst/fixtures/catalogue_ids.tsv", "r");
   char line[1024];
   assert(file);
   while (fgets(line, sizeof(line), file)) {
      char *tab = strchr(line, '\t');
      uint8_t data[256];
      size_t n, i;
      uint32_t id = 0;
      unsigned long expected;
      assert(tab);
      n = (size_t)(tab - line) / 2;
      for (i = 0; i < n; ++i) {
         unsigned byte;
         assert(sscanf(line + 2 * i, "%2x", &byte) == 1);
         data[i] = (uint8_t)byte;
      }
      assert(sscanf(tab + 1, "%lx", &expected) == 1);
      assert(type_catalogue_id(data, n, &id) && id == (uint32_t)expected);
   }
   fclose(file);
   {
      uint32_t id = 123;
      uint8_t invalid = 255;
      assert(!type_catalogue_id(&invalid, 1, &id) && id == 123);
      assert(!type_catalogue_id(NULL, 1, &id));
      assert(!type_catalogue_id("a", 1, NULL));
      assert(!type_catalogue_id("a", 1024U * 1024U + 1, &id));
      assert(type_catalogue_id(NULL, 0, &id) && id == 0xe3b0c442U);
      assert(type_context_message_count(NULL) == 0);
      assert(!type_context_message_name(NULL, 1));
   }
}
static SdlContext *context(const char *name) {
   const char *names[] = {name};
   size_t size;
   char *d = type_description(names, 1, &size);
   SdlContext *ctx;
   assert(d);
   if (!strcmp(name, "Packet")) {
      char expected[4096];
      size_t expected_size;
      FILE *file = fopen("tst/fixtures/packet.sdl2", "rb");
      assert(file);
      expected_size = fread(expected, 1, sizeof(expected), file);
      assert(fgetc(file) == EOF);
      fclose(file);
      assert(size == expected_size && !memcmp(d, expected, size));
   }
   ctx = type_prepare(d, size);
   type_free(d);
   assert(ctx);
   return ctx;
}
static void packet_cases(void) {
   SdlContext *ctx = context("Packet");
   catalogue_api_cases();
   compatibility_cases();
   assert(type_context_message_count(ctx) == 1);
   assert(!strcmp(type_context_message_name(ctx, 1), "Packet"));
   assert(!type_context_message_name(ctx, 0));
   assert(!type_context_message_name(ctx, 2));
   int16_t samples[] = {300, -1};
   Packet m = {0}, *copy;
   size_t n;
   void *data;
   SdlDynamicMessage *dynamic;
   m.active = true;
   m.has_code = true;
   m.code = -2;
   m.label = "\xc3\xa9t\xc3\xa9";
   m.samples = samples;
   m.samples_count = 2;
   data = type_encode(ctx, "Packet", &m, &n);
   assert(data && n == sizeof(fixture) && !memcmp(data, fixture, n));
   assert(!strcmp(type_message_name(ctx, data, n), "Packet"));
   copy = type_decode(ctx, data, n);
   assert(copy && copy->code == -2 && copy->samples_count == 2 && copy->samples[0] == 300);
   assert(copy->sdl_string_length_3 == 5 && !memcmp(copy->label, m.label, 5));
   type_free(copy);
   dynamic = type_decode_dynamic(ctx, data, n);
   assert(dynamic);
   assert(type_dynamic_get(dynamic, "samples")->value.array.count == 2);
   type_dynamic_free(dynamic);
   {
      size_t i;
      for (i = 0; i < n; ++i)
         assert(!type_decode(ctx, data, i));
   }
   {
      unsigned char bad[32];
      memcpy(bad, fixture, n);
      bad[n] = 0;
      assert(!type_decode(ctx, bad, n + 1));
      bad[1] = 2;
      assert(!type_decode(ctx, bad, n));
      memcpy(bad, fixture, n);
      bad[2] = 2;
      assert(!type_decode(ctx, bad, n));
   }
   type_free(data);
   m.has_code = false;
   m.label = "a\0b";
   m.sdl_string_length_3 = 3;
   m.samples_count = 0;
   m.samples = NULL;
   data = type_encode(ctx, "Packet", &m, &n);
   copy = type_decode(ctx, data, n);
   assert(copy && !copy->has_code && copy->sdl_string_length_3 == 3 &&
          !memcmp(copy->label, "a\0b", 3));
   type_free(copy);
   type_free(data);
   m.samples_count = 1;
   assert(!type_encode(ctx, "Packet", &m, &n));
   type_context_free(ctx);
}
static void evolution(void) {
   const char text[] =
       "SDL2|Packet{1:bool:enabled;2?:int16:code;3:string:label;4*:int16:samples;5:string:extra;}";
   SdlContext *ctx = type_prepare(text, strlen(text));
   unsigned char data[20];
   Packet *p;
   assert(ctx);
   memcpy(data, fixture, 16);
   data[16] = 2;
   data[17] = 'o';
   data[18] = 'k';
   p = type_decode(ctx, data, 19);
   assert(p && p->active && p->samples_count == 2);
   type_free(p);
   data[18] = 255;
   assert(!type_decode(ctx, data, 19));
   type_context_free(ctx);
   {
      const char removed[] = "SDL2|Packet{1:bool:active;}";
      unsigned char small[] = {1, 1};
      ctx = type_prepare(removed, strlen(removed));
      assert(ctx);
      p = type_decode(ctx, small, 2);
      assert(p && p->active && !p->has_code && !p->samples_count);
      type_free(p);
      type_context_free(ctx);
   }
   {
      const char incompatible[] = "SDL2|Packet{1:int32:active;}";
      assert(!type_prepare(incompatible, strlen(incompatible)));
   }
   {
      const char nested[] =
          "SDL2|FixedItem{1:fl32:renamed_x;2:fl32:y;3:int32:extra;}|RootPayload{1?:string:header;2+:FixedItem:fixed_array;3*:VarItem:var_array;}|VarItem{1?:string:name;2:int64:id;}";
      unsigned char body[] = {2, 0, 1, 0x3f, 0x80, 0, 0, 0x40, 0, 0, 0, 0, 0, 0, 7, 0};
      RootPayload *root;
      ctx = type_prepare(nested, strlen(nested));
      assert(ctx);
      root = type_decode(ctx, body, sizeof(body));
      assert(root && root->fixed_array_count == 1 && root->fixed_array[0].x == 1.0f &&
             root->fixed_array[0].y == 2.0f);
      type_free(root);
      type_context_free(ctx);
   }
}
static void composites(void) {
   SdlContext *ctx = context("CodecCases");
   CodecCases m = {0}, *p;
   size_t n;
   void *data;
   int16_t xs[] = {-32768, 32767};
   bool flags[] = {true, false};
   char *labels[] = {"", "x"};
   int32_t enums[] = {STATE_READY, STATE_NEGATIVE};
   m.has_tiny = true;
   m.tiny = -128;
   m.has_small = true;
   m.small = 32767;
   m.has_signed_value = true;
   m.signed_value = INT32_MIN;
   m.has_wide = true;
   m.wide = INT64_MIN;
   m.has_precise = true;
   m.precise = -0.0;
   m.has_ratio = true;
   m.ratio = INFINITY;
   m.has_state = true;
   m.state = STATE_MINIMUM;
   m.samples = xs;
   m.samples_count = 2;
   m.labels = (const char **)labels;
   m.labels_count = 2;
   m.required_enabled = true;
   m.bool_flags = flags;
   m.bool_flags_count = 2;
   m.fixed_states[0] = STATE_UNKNOWN;
   m.fixed_states[1] = STATE_READY;
   m.fixed_states[2] = STATE_NEGATIVE;
   m.packed_states = enums;
   m.packed_states_count = 2;
   data = type_encode(ctx, "CodecCases", &m, &n);
   assert(data);
   p = type_decode(ctx, data, n);
   assert(p && p->wide == INT64_MIN && p->tiny == -128 && signbit(p->precise) && isinf(p->ratio));
   assert(p->labels_count == 2 && p->sdl_string_length_14[0] == 0 && !strcmp(p->labels[1], "x"));
   assert(p->packed_states[1] == STATE_NEGATIVE);
   type_free(p);
   p = type_clone("CodecCases", &m);
   assert(p && p->samples != m.samples && p->samples[0] == -32768);
   type_free(p);
   {
      char *text = type_display("CodecCases", &m, 2);
      assert(text);
      type_free(text);
   }
   type_free(data);
   m.state = 99;
   assert(!type_encode(ctx, "CodecCases", &m, &n));
   type_context_free(ctx);
   {
      SdlContext *empty = context("EmptyMessage");
      EmptyMessage e = {0};
      data = type_encode(empty, "EmptyMessage", &e, &n);
      assert(data && n == 1);
      p = type_decode(empty, data, n);
      assert(p);
      type_free(p);
      type_free(data);
      type_context_free(empty);
   }
}
static void graph_limits(void) {
   char text[16384];
   unsigned count, shared;
   for (shared = 0; shared < 2; ++shared) {
      size_t used = 5;
      SdlContext *ctx;
      memcpy(text, "SDL2|", 5);
      for (count = 0; count < (shared ? 30u : 66u); ++count) {
         used += (size_t)sprintf(text + used, "%sN%03u{", count ? "|" : "", count);
         if (!count)
            used += (size_t)sprintf(text + used, "1:int8:value;");
         else {
            used += (size_t)sprintf(text + used, "1?:N%03u:left;", count - 1);
            if (shared)
               used += (size_t)sprintf(text + used, "2?:N%03u:right;", count - 1);
         }
         used += (size_t)sprintf(text + used, "}");
      }
      ctx = type_prepare(text, used);
      assert(shared ? ctx != NULL : ctx == NULL);
      type_context_free(ctx);
   }
}
static void deep_messages(void) {
   typedef struct { char *data; uint32_t length; } StringValue;
   typedef struct { char prefix; StringValue value; } StringValueAlign;
   static SdlTypeDesc types[2][65];
   static SdlFieldDesc fields[2][65];
   static char names[2][65][32];
   char text[16384];
   unsigned variable, i, count;
   for (variable = 0; variable < 2; ++variable) {
      size_t used = 5;
      SdlContext *ctx;
      memcpy(text, "SDL2|", 5);
      for (i = 0; i < 65; ++i)
         snprintf(names[variable][i], sizeof(names[variable][i]), "Deep%s%03u", variable ? "String" : "Bool", i);
      for (i = 0; i < 65; ++i) {
         SdlTypeDesc *t = &types[variable][i];
         SdlFieldDesc *f = &fields[variable][i];
         t->kind = SDL_TYPE_STRUCT;
         t->name = names[variable][i];
         t->size = variable ? sizeof(StringValue) : sizeof(bool);
         t->alignment = variable ? offsetof(StringValueAlign, value) : 1;
         t->detail.structure.field_count = 1;
         t->detail.structure.fields = f;
         f->id = 1;
         f->name = "value";
         f->type = i < 64 ? &types[variable][i + 1] : variable ? &SDL_STRING_DESC : &SDL_BOOL_DESC;
         f->presence_offset = f->count_offset = f->string_length_offset = SDL_NO_OFFSET;
         if (variable && i == 64)
            f->string_length_offset = offsetof(StringValue, length);
         assert(sdl_register_type(t));
         used += (size_t)sprintf(text + used, "%s%s{1:%s:value;}", i ? "|" : "", t->name,
            i < 64 ? names[variable][i + 1] : variable ? "string" : "bool");
      }
      ctx = type_prepare(text, used);
      assert(ctx);
      for (count = 32; count <= 65; ++count) {
         unsigned char wire[] = {(unsigned char)(66 - count), 1, 'x'};
         size_t size = variable ? 3 : 2, encoded_size;
         bool value = true;
         StringValue string = {"x", 1};
         void *copy, *encoded;
         SdlDynamicMessage *decoded, *m;
         assert(type_context_validate(ctx, wire, size));
         decoded = type_decode_dynamic(ctx, wire, size);
         assert(decoded);
         m = decoded;
         for (i = 1; i < count; ++i) {
            assert(m->field_count == 1 && m->fields[0].value.kind == SDL_DYNAMIC_MESSAGE);
            m = m->fields[0].value.value.message;
         }
         assert(m->fields[0].value.kind == (variable ? SDL_DYNAMIC_STRING : SDL_DYNAMIC_BOOL));
         if (variable)
            assert(m->fields[0].value.value.string.size == 1 && m->fields[0].value.value.string.data[0] == 'x');
         else
            assert(m->fields[0].value.value.boolean);
         type_dynamic_free(decoded);
         copy = type_decode(ctx, wire, size);
         assert(copy && (variable ? !strcmp(((StringValue *)copy)->data, "x") && ((StringValue *)copy)->length == 1 : *(bool *)copy));
         type_free(copy);
         encoded = type_encode(ctx, names[variable][65 - count], variable ? (void *)&string : (void *)&value, &encoded_size);
         assert(encoded && encoded_size == size && !memcmp(encoded, wire, size));
         type_free(encoded);
         assert(!type_context_validate(ctx, wire, size - 1));
         assert(!type_decode_dynamic(ctx, wire, size - 1));
         assert(!type_decode(ctx, wire, size - 1));
         wire[size - 1] = variable ? 0xff : 2;
         assert(!type_context_validate(ctx, wire, size));
         assert(!type_decode_dynamic(ctx, wire, size));
         assert(!type_decode(ctx, wire, size));
      }
      type_context_free(ctx);
   }
}
static void deep_arrays(void) {
   static SdlTypeDesc arrays[255];
   static SdlFieldDesc field = {1, "value", arrays, 0, SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0};
   static const SdlTypeDesc message = {
      .kind = SDL_TYPE_STRUCT, .size = 1, .alignment = 1, .name = "DeepArray",
      .detail.structure = {1, &field}
   };
   const unsigned ranks[] = {63, 64, 65, 255, 256};
   const unsigned char wire[] = {1, 7};
   char text[1024];
   size_t k;
   for (k = 0; k < 255; ++k) {
      arrays[k].kind = SDL_TYPE_ARRAY;
      arrays[k].size = arrays[k].alignment = 1;
      arrays[k].detail.array.count = 1;
      arrays[k].detail.array.element = k < 254 ? &arrays[k + 1] : &SDL_INT8_DESC;
   }
   assert(sdl_register_type(&message));
   for (k = 0; k < sizeof(ranks) / sizeof(ranks[0]); ++k) {
      unsigned i, rank = ranks[k];
      size_t used = (size_t)sprintf(text, "SDL2|%s{1:int8", rank == 255 ? "DeepArray" : "UnknownDeepArray");
      SdlContext *ctx;
      SdlDynamicMessage *decoded;
      const SdlDynamicValue *value;
      for (i = 0; i < rank; ++i) {
         memcpy(text + used, "[1]", 3);
         used += 3;
      }
      used += (size_t)sprintf(text + used, ":value;}");
      ctx = type_prepare(text, used);
      if (rank > 255) {
         assert(!ctx);
         continue;
      }
      assert(ctx && type_context_validate(ctx, wire, sizeof(wire)));
      decoded = type_decode_dynamic(ctx, wire, sizeof(wire));
      assert(decoded);
      value = &decoded->fields[0].value;
      for (i = 0; i < rank; ++i) {
         assert(value->kind == SDL_DYNAMIC_ARRAY && value->value.array.count == 1);
         value = &value->value.array.items[0];
      }
      assert(value->kind == SDL_DYNAMIC_INTEGER && value->value.integer == 7);
      type_dynamic_free(decoded);
      assert(!type_context_validate(ctx, wire, sizeof(wire) - 1));
      assert(!type_decode_dynamic(ctx, wire, sizeof(wire) - 1));
      if (rank == 255) {
         int8_t original = 7, *copy;
         size_t size;
         void *encoded = type_encode(ctx, "DeepArray", &original, &size);
         assert(encoded && size == sizeof(wire) && !memcmp(encoded, wire, size));
         copy = type_decode(ctx, encoded, size);
         assert(copy && *copy == original);
         type_free(copy);
         type_free(encoded);
      }
      type_context_free(ctx);
   }
}
static void deep_array_messages(void) {
   char text[65536];
   const unsigned char wire[] = {1, 7};
   size_t used = 5;
   unsigned i, j;
   SdlContext *ctx;
   SdlDynamicMessage *decoded, *m;
   memcpy(text, "SDL2|", 5);
   for (i = 0; i < 65; ++i) {
      used += (size_t)sprintf(text + used, "%sArrayMessage%03u{1:", i ? "|" : "", i);
      used += (size_t)sprintf(text + used, i < 64 ? "ArrayMessage%03u" : "int8", i + 1);
      for (j = 0; j < 255; ++j) {
         memcpy(text + used, "[1]", 3);
         used += 3;
      }
      used += (size_t)sprintf(text + used, ":value;}");
   }
   ctx = type_prepare(text, used);
   assert(ctx && type_context_validate(ctx, wire, sizeof(wire)));
   decoded = type_decode_dynamic(ctx, wire, sizeof(wire));
   assert(decoded);
   m = decoded;
   for (i = 0; i < 65; ++i) {
      const SdlDynamicValue *value = &m->fields[0].value;
      for (j = 0; j < 255; ++j) {
         assert(value->kind == SDL_DYNAMIC_ARRAY && value->value.array.count == 1);
         value = &value->value.array.items[0];
      }
      if (i < 64) {
         assert(value->kind == SDL_DYNAMIC_MESSAGE);
         m = value->value.message;
      } else
         assert(value->kind == SDL_DYNAMIC_INTEGER && value->value.integer == 7);
   }
   type_dynamic_free(decoded);
   type_context_free(ctx);
}
static void quantum_catalogue_merge_cases(void) {
   static const uint8_t catalogue_a[] =
      "SDL2|QuantumMergeA{}|QuantumMergePlain{1:int32:value;}|QuantumMergeShared{1:fl64:value@b12,q0.1;}";
   static const uint8_t catalogue_b[] =
      "SDL2|QuantumMergeB{}|QuantumMergePlain{1:int32:value;}|QuantumMergeShared{1:fl64:value@b12,q0.10000000000000001;}";
   static const uint8_t catalogue_c[] =
      "SDL2|QuantumMergeC{}|QuantumMergePlain{1:int32:value;}|QuantumMergeShared{1:fl64:value@b12,q0.1;}";
   static const SdlTypeDesc types[] = {
      {.kind = SDL_TYPE_STRUCT, .name = "QuantumMergeA", .schema_descriptor = catalogue_a,
       .schema_descriptor_size = sizeof(catalogue_a) - 1},
      {.kind = SDL_TYPE_STRUCT, .name = "QuantumMergeB", .schema_descriptor = catalogue_b,
       .schema_descriptor_size = sizeof(catalogue_b) - 1},
      {.kind = SDL_TYPE_STRUCT, .name = "QuantumMergeC", .schema_descriptor = catalogue_c,
       .schema_descriptor_size = sizeof(catalogue_c) - 1}
   };
   const char *conflicting[][2] = {{"QuantumMergeA", "QuantumMergeB"}, {"QuantumMergeB", "QuantumMergeA"}};
   const char *matching[][2] = {{"QuantumMergeA", "QuantumMergeC"}, {"QuantumMergeC", "QuantumMergeA"}};
   const char expected[] =
      "SDL2|QuantumMergeA{}|QuantumMergeC{}|QuantumMergePlain{1:int32:value;}|QuantumMergeShared{1:fl64:value@b12,q0.1;}";
   size_t i, size;
   uint32_t expected_id;
   /* Distinct canonical decimal texts can denote the same binary64 value. */
   assert(strtod("0.1", NULL) == strtod("0.10000000000000001", NULL));
   for (i = 0; i < sizeof(types) / sizeof(types[0]); ++i) {
      const char *name = types[i].name;
      char *description;
      assert(sdl_register_type(&types[i]));
      description = type_description(&name, 1, &size);
      assert(description && size == types[i].schema_descriptor_size &&
         !memcmp(description, types[i].schema_descriptor, size));
      type_free(description);
   }
   for (i = 0; i < sizeof(conflicting) / sizeof(conflicting[0]); ++i) {
      size = 123;
      assert(!type_description(conflicting[i], 2, &size));
      assert(size == 0);
   }
   assert(type_catalogue_id(expected, sizeof(expected) - 1, &expected_id));
   for (i = 0; i < sizeof(matching) / sizeof(matching[0]); ++i) {
      char *description = type_description(matching[i], 2, &size);
      uint32_t id;
      assert(description && size == sizeof(expected) - 1 && !memcmp(description, expected, size));
      assert(type_catalogue_id(description, size, &id) && id == expected_id);
      type_free(description);
   }
}
static void quantum_cases(void) {
   const char *invalid[] = {"q0.1", "b12,q0", "b12,qNaN", "b65,q0.1",
      "b12,q0.1,v", "b12,q0.1,q0.1", "b12,q1e-1", "b12,q-1", "b12,q1.", "b12,q.1"};
   const unsigned widths[] = {1, 32, 64};
   char descriptor[256];
   size_t i;
   for (i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
      size_t size = (size_t)snprintf(descriptor, sizeof(descriptor), "SDL2|QuantumBoundary{1:fl64:value@%s;}", invalid[i]);
      assert(!type_prepare(descriptor, size));
   }
   {
      const char *integers[] = {"int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"};
      const char *markers[] = {"", "?", "*", "+", ""};
      const char *suffixes[] = {"q0.1", "b1,q0.1", "q0.1,b1", "v,q0.1"};
      size_t t, shape, option;
      for (t = 0; t < sizeof(integers) / sizeof(integers[0]); ++t) {
         for (shape = 0; shape < sizeof(markers) / sizeof(markers[0]); ++shape) {
            const char *dimensions = shape == 4 ? "[2]" : "";
            size_t ds = (size_t)snprintf(descriptor, sizeof(descriptor),
               "SDL2|QuantumBoundary{1%s:%s%s:value@b1;}", markers[shape], integers[t], dimensions);
            SdlContext *ctx = type_prepare(descriptor, ds);
            assert(ctx);
            type_context_free(ctx);
            for (option = 0; option < sizeof(suffixes) / sizeof(suffixes[0]); ++option) {
               ds = (size_t)snprintf(descriptor, sizeof(descriptor),
                  "SDL2|QuantumBoundary{1%s:%s%s:value@%s;}", markers[shape], integers[t], dimensions, suffixes[option]);
               assert(!type_prepare(descriptor, ds));
            }
         }
      }
   }
   for (i = 0; i < sizeof(widths) / sizeof(widths[0]); ++i) {
      unsigned bits = widths[i], scalar = bits == 32 ? 4 : 8;
      size_t ds = (size_t)snprintf(descriptor, sizeof(descriptor),
         "SDL2|QuantumBoundary{1:%s:value@b%u,q0.25;}", scalar == 4 ? "fl32" : "fl64", bits);
      SdlContext *ctx = type_prepare(descriptor, ds);
      unsigned char plain[9] = {1}, expected[9] = {1, 0x80};
      size_t size, wire_size = 1 + (bits + 7) / 8;
      double x = -ldexp(0.25, (int)bits - 1);
      float single = (float)x;
      void *encoded;
      SdlDynamicMessage *decoded;
      assert(ctx);
      sdl_wire_encode_native(plain + 1, scalar == 4 ? (void *)&single : (void *)&x, scalar);
      encoded = type_wire_transform(ctx, plain, 1 + scalar, true, &size);
      assert(encoded && size == wire_size && !memcmp(encoded, expected, size));
      assert(type_context_validate(ctx, encoded, size));
      decoded = type_decode_dynamic(ctx, encoded, size);
      assert(decoded && decoded->fields[0].value.kind == SDL_DYNAMIC_FLOAT && decoded->fields[0].value.value.floating == x);
      type_dynamic_free(decoded);
      type_free(encoded);
      x = -x; single = (float)x;
      sdl_wire_encode_native(plain + 1, scalar == 4 ? (void *)&single : (void *)&x, scalar);
      assert(!type_wire_transform(ctx, plain, 1 + scalar, true, &size));
      x = NAN; single = NAN;
      sdl_wire_encode_native(plain + 1, scalar == 4 ? (void *)&single : (void *)&x, scalar);
      assert(!type_wire_transform(ctx, plain, 1 + scalar, true, &size));
      type_context_free(ctx);
   }
}
/* Mixed widths and native padding exercise the non-contiguous span path. */
typedef struct {
   bool enabled;
   int16_t code;
   double precise;
   State state;
   float complex point;
} PackedTestRecord;
typedef struct { char prefix; PackedTestRecord value; } PackedTestRecordAlign;
typedef struct { uint32_t count; PackedTestRecord *records; } PackedTestBatch;
typedef struct { char prefix; PackedTestBatch value; } PackedTestBatchAlign;
static const SdlFieldDesc packed_test_record_fields[] = {
   {1, "enabled", &SDL_BOOL_DESC, offsetof(PackedTestRecord, enabled), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
   {2, "code", &SDL_INT16_DESC, offsetof(PackedTestRecord, code), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
   {3, "precise", &SDL_FLOAT64_DESC, offsetof(PackedTestRecord, precise), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
   {4, "state", &SDL_ENUM_STATE_DESC, offsetof(PackedTestRecord, state), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
   {5, "point", &SDL_COMPLEX32_DESC, offsetof(PackedTestRecord, point), SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0},
};
static const SdlTypeDesc packed_test_record_desc = {
   .kind = SDL_TYPE_STRUCT, .size = sizeof(PackedTestRecord),
   .alignment = offsetof(PackedTestRecordAlign, value), .name = "PackedTestRecord",
   .detail.structure = {5, packed_test_record_fields}
};
static const SdlFieldDesc packed_test_batch_fields[] = {
   {1, "records", &packed_test_record_desc, offsetof(PackedTestBatch, records), SDL_NO_OFFSET,
    offsetof(PackedTestBatch, count), SDL_NO_OFFSET, SDL_FIELD_REPEATED | SDL_FIELD_PACKED}
};
static const SdlTypeDesc packed_test_batch_desc = {
   .kind = SDL_TYPE_STRUCT, .size = sizeof(PackedTestBatch),
   .alignment = offsetof(PackedTestBatchAlign, value), .name = "PackedTestBatch",
   .detail.structure = {1, packed_test_batch_fields}
};
static void packed_records(void) {
   static const char text[] =
      "SDL2|#State{READY=1;NEGATIVE=-7;}|PackedTestBatch{1+:PackedTestRecord:records;}|PackedTestRecord{1:bool:enabled;2:int16:code;3:fl64:precise;4:State:state;5:c32:point;}";
   PackedTestRecord records[2] = {{0}};
   const float points[2][2] = {{1.f, -2.f}, {-3.f, 4.f}};
   PackedTestBatch batch = {2, records}, *copy;
   unsigned char expected[48] = {1, 2};
   void *wire;
   size_t size, i;
   SdlContext *ctx;
   assert(sdl_register_type(&packed_test_record_desc));
   assert(sdl_register_type(&packed_test_batch_desc));
   ctx = type_prepare(text, strlen(text));
   assert(ctx);
   records[0].enabled = true;
   records[0].code = INT16_MIN;
   records[0].precise = -0.0;
   records[0].state = STATE_NEGATIVE;
   memcpy(&records[0].point, points[0], sizeof(records[0].point));
   records[1].code = INT16_MAX;
   records[1].precise = INFINITY;
   records[1].state = STATE_READY;
   memcpy(&records[1].point, points[1], sizeof(records[1].point));
   for (i = 0; i < 2; ++i)
      assert(sdl_value_encode_fixed(&packed_test_record_desc, &records[i], expected + 2 + i * 23, 23));
   wire = type_encode(ctx, "PackedTestBatch", &batch, &size);
   assert(wire && size == sizeof(expected) && !memcmp(wire, expected, size));
   copy = type_decode(ctx, wire, size);
   assert(copy && copy->count == 2 && copy->records[0].enabled && !copy->records[1].enabled);
   assert(copy->records[0].code == INT16_MIN && copy->records[1].code == INT16_MAX);
   assert(signbit(copy->records[0].precise) && isinf(copy->records[1].precise));
   assert(copy->records[0].state == STATE_NEGATIVE && copy->records[1].state == STATE_READY);
   assert(copy->records[0].point == records[0].point && copy->records[1].point == records[1].point);
   type_free(copy);
   for (i = 0; i < size; ++i) {
      assert(!type_decode_size(ctx, wire, i));
      assert(!type_decode(ctx, wire, i));
   }
   expected[2] = 2;
   assert(!type_decode(ctx, expected, sizeof(expected)));
   expected[2] = 1;
   sdl_wire_write_u32(expected + 13, 123);
   assert(!type_decode(ctx, expected, sizeof(expected)));
   /* Unknown fields validate against the remote enum, not the local subset. */
   {
      char remote[1024];
      const char *closing = strstr(text, "}|PackedTestBatch");
      SdlContext *changed;
      assert(closing);
      snprintf(remote, sizeof(remote), "%.*sOTHER=123;%s",
               (int)(closing - text), text, closing);
      changed = type_prepare(remote, strlen(remote));
      assert(changed && !type_decode(changed, expected, sizeof(expected)));
      type_context_free(changed);
      strstr(remote, "1+:PackedTestRecord")[0] = '2';
      changed = type_prepare(remote, strlen(remote));
      assert(changed);
      copy = type_decode(changed, expected, sizeof(expected));
      assert(copy && copy->count == 0 && !copy->records);
      type_free(copy);
      type_context_free(changed);
   }
   records[0].state = 123;
   assert(!type_encode_size(ctx, "PackedTestBatch", &batch));
   assert(!type_encode(ctx, "PackedTestBatch", &batch, &size));
   records[0].state = STATE_NEGATIVE;
   batch.count = 0;
   batch.records = NULL;
   {
      void *empty = type_encode(ctx, "PackedTestBatch", &batch, &size);
      assert(empty && size == 2 && !memcmp(empty, "\1\0", 2));
      copy = type_decode(ctx, empty, size);
      assert(copy && copy->count == 0);
      type_free(copy);
      type_free(empty);
   }
   /* Added remote fields disable the plan and retain schema evolution. */
   {
      char remote[1024];
      unsigned char evolved[50] = {1, 2};
      SdlContext *changed;
      memcpy(remote, text, strlen(text) - 1);
      strcpy(remote + strlen(text) - 1, "6:int8:extra;}");
      changed = type_prepare(remote, strlen(remote));
      assert(changed);
      for (i = 0; i < 2; ++i) {
         memcpy(evolved + 2 + i * 24, (const unsigned char *)wire + 2 + i * 23, 23);
         evolved[2 + i * 24 + 23] = 7;
      }
      copy = type_decode(changed, evolved, sizeof(evolved));
      assert(copy && copy->count == 2 && copy->records[0].state == STATE_NEGATIVE &&
             copy->records[1].point == records[1].point);
      type_free(copy);
      assert(!type_encode_size(changed, "PackedTestBatch", &batch));
      type_context_free(changed);
   }
   type_free(wire);
   type_context_free(ctx);
   /* Nested fixed arrays use several spans with different component widths. */
   {
      FixedRow rows[2] = {0};
      FixedRowBatch nested = {2, rows}, *result;
      unsigned char reference[82];
      ctx = context("FixedRowBatch");
      rows[0].vectors[0].coords[0] = -0.0f;
      rows[0].vectors[1].coords[1] = INFINITY;
      rows[1].vectors[1].grid[1][2] = INT16_MIN;
      wire = type_encode(ctx, "FixedRowBatch", &nested, &size);
      assert(wire && size == sizeof(reference));
      memcpy(reference, wire, 2);
      for (i = 0; i < 2; ++i)
         assert(sdl_value_encode_fixed(&FIXEDROW_DESC, &rows[i], reference + 2 + 40 * i, 40));
      assert(!memcmp(reference, wire, size));
      result = type_decode(ctx, wire, size);
      assert(result && result->rows_count == 2 && !memcmp(result->rows, rows, sizeof(rows)));
      type_free(result);
      type_free(wire);
      type_context_free(ctx);
   }
}

static void wire_word_arrays(void) {
   const size_t widths[] = {1, 2, 3, 4, 8, 16};
   const size_t counts[] = {0, 1, 2, 3, 7, 15, 16, 17, 64, 1000};
   unsigned char source[16005], actual[16005], expected[16005];
   uint32_t state = 123456789;
   size_t w, c, i;
   for (i = 0; i < sizeof(source); ++i) {
      state ^= state << 13; state ^= state >> 17; state ^= state << 5;
      source[i] = (unsigned char)state;
   }
   for (w = 0; w < sizeof(widths) / sizeof(widths[0]); ++w)
      for (c = 0; c < sizeof(counts) / sizeof(counts[0]); ++c) {
         size_t width = widths[w], count = counts[c];
         memset(actual, 0xa5, sizeof(actual));
         memset(expected, 0xa5, sizeof(expected));
         /* Both buffers are deliberately unaligned; canaries detect overruns. */
         for (i = 0; i < count; ++i)
            sdl_wire_encode_native(expected + 3 + i * width, source + 1 + i * width, width);
         sdl_wire_convert_array(actual + 3, source + 1, width, count);
         assert(!memcmp(actual, expected, sizeof(actual)));
         sdl_wire_convert_array(NULL, NULL, width, 0);
      }
}

static void invalid(void) {
   const char *bad[] = {"SDL1\n",
                        "SDL2|A{1:A:a;}",
                        "SDL2|A{1:Missing:a;}",
                        "SDL2|A{1:bool:a;1:bool:b;}",
                        "SDL2|A{1+:string:a;}",
                        "SDL2|#E{}|A{}"};
   size_t i;
   for (i = 0; i < sizeof(bad) / sizeof(bad[0]); ++i)
      assert(!type_prepare(bad[i], strlen(bad[i])));
   {
      SdlContext *ctx = context("Packet");
      unsigned char badid[] = {0x81, 0};
      unsigned char over[] = {255, 255, 255, 255, 16};
      assert(!type_decode(ctx, badid, sizeof(badid)));
      assert(!type_decode(ctx, over, sizeof(over)));
      type_context_free(ctx);
   }
}
int main(void) {
   assert(register_all_types());
   envelope_cases();
   packet_cases();
   evolution();
   composites();
   packed_records();
   wire_word_arrays();
   invalid();
   graph_limits();
   deep_messages();
   deep_arrays();
   deep_array_messages();
   quantum_cases();
   quantum_catalogue_merge_cases();
   registry_cases();
   puts("C SDL2 codec, evolution, malformed input and storage checks passed.");
   return 0;
}
