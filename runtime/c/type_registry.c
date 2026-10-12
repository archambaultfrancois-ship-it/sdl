#include "type_private.h"
#include "type_registry.h"
#include <stdlib.h>
#include <string.h>

static const SdlTypeDesc **registry;
static size_t registry_count;
static size_t registry_capacity;

bool sdl_register_type(const SdlTypeDesc *type) {
   size_t i;

   if (type == NULL || type->name == NULL || type->kind != SDL_TYPE_STRUCT)
      return false;
   for (i = 0; i < registry_count; ++i) {
      if (registry[i] == type)
         return true;
      if (strcmp(registry[i]->name, type->name) == 0)
         return false;
   }
   if (registry_count == registry_capacity) {
      size_t capacity = registry_capacity ? registry_capacity * 2 : 256;
      const SdlTypeDesc **resized;
      if (capacity < registry_capacity || capacity > SIZE_MAX / sizeof(*registry))
         return false;
      resized = realloc(registry, capacity * sizeof(*registry));
      if (resized == NULL)
         return false;
      registry = resized;
      registry_capacity = capacity;
   }
   registry[registry_count++] = type;
   return true;
}

const SdlTypeDesc *sdl_lookup_type(const char *name) {
   size_t i;

   if (name == NULL)
      return NULL;
   for (i = 0; i < registry_count; ++i) {
      const char *candidate = registry[i]->name;
      size_t n = 0;
      while (candidate[n] != '\0' && name[n] == candidate[n])
         ++n;
      if (candidate[n] == '\0' && name[n] == '\0')
         return registry[i];
   }
   return NULL;
}
