#include "type_private.h"
#include "sdl_wire.h"

#include <complex.h>
#include <stdlib.h>
#include <string.h>

const SdlTypeDesc SDL_BOOL_DESC = {SDL_TYPE_BOOL, sizeof(bool), sizeof(bool), "bool", NULL, 0,
                                   {{0, NULL}}};
const SdlTypeDesc SDL_INT8_DESC = {SDL_TYPE_INT8, sizeof(int8_t), sizeof(int8_t), "int8", NULL, 0,
                                   {{0, NULL}}};
const SdlTypeDesc SDL_INT16_DESC = {
    SDL_TYPE_INT16, sizeof(int16_t), sizeof(int16_t), "int16", NULL, 0, {{0, NULL}}};
const SdlTypeDesc SDL_INT32_DESC = {
    SDL_TYPE_INT32, sizeof(int32_t), sizeof(int32_t), "int32", NULL, 0, {{0, NULL}}};
const SdlTypeDesc SDL_INT64_DESC = {
    SDL_TYPE_INT64, sizeof(int64_t), sizeof(int64_t), "int64", NULL, 0, {{0, NULL}}};
const SdlTypeDesc SDL_FLOAT32_DESC = {
    SDL_TYPE_FLOAT32, sizeof(float), sizeof(float), "float32", NULL, 0, {{0, NULL}}};
const SdlTypeDesc SDL_FLOAT64_DESC = {
    SDL_TYPE_FLOAT64, sizeof(double), sizeof(double), "float64", NULL, 0, {{0, NULL}}};
const SdlTypeDesc SDL_COMPLEX32_DESC = {
    SDL_TYPE_COMPLEX32, sizeof(float complex), sizeof(float complex), "complex32", NULL, 0,
    {{0, NULL}}};
const SdlTypeDesc SDL_COMPLEX64_DESC = {
    SDL_TYPE_COMPLEX64, sizeof(double complex), sizeof(double complex), "complex64", NULL, 0,
    {{0, NULL}}};
const SdlTypeDesc SDL_ENUM_DESC = {SDL_TYPE_ENUM, sizeof(int32_t), sizeof(int32_t), "enum", NULL, 0,
                                   {{0, NULL}}};
const SdlTypeDesc SDL_STRING_DESC = {
    SDL_TYPE_STRING, sizeof(char *), sizeof(char *), "string", NULL, 0, {{0, NULL}}};

void *type_clone(const char *name, const void *decoded) {
   const SdlTypeDesc *type = sdl_lookup_type(name);
   size_t extra;
   void *block;
   size_t offset = 0;
   if (type == NULL || decoded == NULL)
      return NULL;
   extra = sdl_value_measure(type, decoded);
   if (extra == SIZE_MAX || type->size > SIZE_MAX - extra)
      return NULL;
   block = calloc(1, type->size + extra);
   if (block == NULL)
      return NULL;
   sdl_value_clone(type, decoded, block, (uint8_t *)block + type->size, &offset);
   return block;
}

void type_free(void *ptr) { free(ptr); }
