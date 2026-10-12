/* ============================================================================
   DYNAMIC SCHEMA REPOSITORY ENGINE
   ============================================================================ */

#include "type_engine.h"
#include "type_private.h"
#include <stdlib.h>
#include <string.h>

static TypeNode* REGISTRY_HEAD = NULL;

bool type_register(const char* name, uint32_t hash, const char* format, size_t struct_size) {
   if (!name || !format || struct_size == 0) return false;

   TypeNode* current = REGISTRY_HEAD;
   while (current != NULL) {
      if (current->hash == hash || strcmp(current->name, name) == 0) {
         return false; /* Duplicate collision safety guard */
      }
      current = current->next;
   }

   TypeNode* new_node = (TypeNode*)malloc(sizeof(TypeNode));
   if (!new_node) return false;

   new_node->name = strdup(name);
   new_node->format = strdup(format);
   new_node->struct_size = struct_size;
   new_node->hash = hash;
   new_node->next = REGISTRY_HEAD;

   REGISTRY_HEAD = new_node;
   return true;
}

const TypeNode* lookup_by_name(const char* name) {
   if (!name) return NULL;
   TypeNode* current = REGISTRY_HEAD;
   while (current != NULL) {
      if (strcmp(current->name, name) == 0) return current;
      current = current->next;
   }
   return NULL;
}

const TypeNode* lookup_by_hash(uint32_t hash) {
   TypeNode* current = REGISTRY_HEAD;
   while (current != NULL) {
      if (current->hash == hash) return current;
      current = current->next;
   }
   return NULL;
}
