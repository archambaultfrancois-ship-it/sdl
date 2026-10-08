#ifndef SDL_DYNAMIC_H
#define SDL_DYNAMIC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct SdlDynamicMessage SdlDynamicMessage;
typedef struct SdlDynamicValue SdlDynamicValue;

typedef enum {
   SDL_DYNAMIC_NULL,
   SDL_DYNAMIC_BOOL,
   SDL_DYNAMIC_INTEGER,
   SDL_DYNAMIC_FLOAT,
   SDL_DYNAMIC_COMPLEX,
   SDL_DYNAMIC_STRING,
   SDL_DYNAMIC_ENUM,
   SDL_DYNAMIC_ARRAY,
   SDL_DYNAMIC_MESSAGE
} SdlDynamicKind;

typedef struct {
   double real;
   double imag;
} SdlDynamicComplex;

typedef struct {
   char *type_name;
   char *name;
   int32_t value;
} SdlDynamicEnum;

typedef struct {
   uint8_t *data;
   size_t size;
} SdlDynamicString;

typedef struct {
   size_t count;
   SdlDynamicValue *items;
} SdlDynamicArray;

struct SdlDynamicValue {
   SdlDynamicKind kind;
   union {
      bool boolean;
      int64_t integer;
      double floating;
      SdlDynamicComplex complex_value;
      SdlDynamicString string;
      SdlDynamicEnum enumeration;
      SdlDynamicArray array;
      SdlDynamicMessage *message;
   } value;
};

typedef struct {
   char *name;
   SdlDynamicValue value;
} SdlDynamicField;

struct SdlDynamicMessage {
   char *type_name;
   size_t field_count;
   SdlDynamicField *fields;
};

/* Decode a message without generated C type declarations. */

void type_dynamic_free(SdlDynamicMessage *message);

/* Look up a field in a descriptor-decoded message by its SDL field name. */
const SdlDynamicValue *type_dynamic_get(const SdlDynamicMessage *message,
   const char *name);

#endif /* SDL_DYNAMIC_H */
