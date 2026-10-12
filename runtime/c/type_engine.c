/* ============================================================================
   TOP-LEVEL TYPE ENGINE API ROUTINES
   ============================================================================ */

#include "type_engine.h"
#include "type_private.h"
#include <stdlib.h>
#include <string.h>

size_t type_encode_size(const char* type, const void* decoded) {
   const TypeNode* desc = lookup_by_name(type);
   if (!desc || !decoded) return 0;
   return 4 + desc->struct_size + internal_measure_struct(decoded, desc->format) + 64;
}

void* type_encode(const char* type, const void* decoded, size_t* size) {
   if (!size) return NULL; *size = 0;
   const TypeNode* desc = lookup_by_name(type);
   if (!desc) return NULL;

   size_t max_buffer_needed = type_encode_size(type, decoded);
   uint8_t* encoded_buffer = (uint8_t*)malloc(max_buffer_needed);
   if (!encoded_buffer) return NULL;

   *(uint32_t*)&encoded_buffer = desc->hash;

   size_t actual_written = internal_encode(decoded, desc->format, &encoded_buffer, max_buffer_needed - 4);
   if (actual_written == 0) { free(encoded_buffer); return NULL; }

   *size = 4 + actual_written;
   return encoded_buffer;
}

size_t type_decode_size(const void* encoded, size_t size) {
   if (!encoded || size < 4) return 0;
   uint32_t hash = *(const uint32_t*)encoded;
   const TypeNode* desc = lookup_by_hash(hash);
   if (!desc) return 0;

   size_t dyn_mem_needed = internal_measure_buffer(((const uint8_t*)encoded) + 4, size - 4);
   return desc->struct_size + dyn_mem_needed;
}

void* type_decode(const void* encoded, size_t* size) {
   if (!encoded || !size || *size < 4) return NULL;
   uint32_t hash = *(const uint32_t*)encoded;
   const TypeNode* desc = lookup_by_hash(hash);
   if (!desc) return NULL;

   size_t total_allocation = type_decode_size(encoded, *size);
   if (total_allocation == 0) return NULL;

   void* block = malloc(total_allocation);
   if (!block) return NULL;

   memset(block, 0, desc->struct_size);
   uint8_t* pool = (uint8_t*)block + desc->struct_size;
   size_t pool_offset = 0;

   internal_decode_into(((const uint8_t*)encoded) + 4, *size - 4, desc->format, (uint8_t*)block, pool, &pool_offset);
   return block;
}

void* type_clone(const char* type, const void* decoded) {
   const TypeNode* desc = lookup_by_name(type);
   if (!desc || !decoded) return NULL;

   size_t mem_needed = internal_measure_struct(decoded, desc->format);
   void* block = malloc(desc->struct_size + mem_needed);
   if (!block) return NULL;

   memset(block, 0, desc->struct_size);
   uint8_t* pool = (uint8_t*)block + desc->struct_size;
   size_t pool_offset = 0;

   internal_clone_into(decoded, desc->format, (uint8_t*)block, pool, &pool_offset);
   return block;
}

void type_free(void* ptr) { if (ptr) free(ptr); }
