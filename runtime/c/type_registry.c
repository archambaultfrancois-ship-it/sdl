#include "type_private.h"

#define SDL_REGISTRY_LIMIT 256

static const SdlTypeDesc *registry[SDL_REGISTRY_LIMIT];
static size_t registry_count;

bool sdl_register_type(const SdlTypeDesc *type) {
   size_t i;

   if (type == NULL || type->name == NULL || type->kind != SDL_TYPE_STRUCT)
      return false;
   for (i = 0; i < registry_count; ++i) {
      if (registry[i] == type || registry[i]->hash == type->hash)
         return false;
   }
   if (registry_count == SDL_REGISTRY_LIMIT)
      return false;
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

const SdlTypeDesc *sdl_lookup_hash(uint32_t hash) {
   size_t i;

   for (i = 0; i < registry_count; ++i)
      if (registry[i]->hash == hash)
         return registry[i];
   return NULL;
}
