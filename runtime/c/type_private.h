/* ============================================================================
   PRIVATE COMPILATION UNIT SHAREABLE HEADERS
   ============================================================================ */

#ifndef TYPE_PRIVATE_H
#define TYPE_PRIVATE_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* Node definition for the dynamic schema repository database */
typedef struct TypeNode {
   char* name;
   uint32_t hash;
   char* format;
   size_t struct_size;
   struct TypeNode* next;
} TypeNode;

/* Registry lookup routines */
const TypeNode* lookup_by_name(const char* name);
const TypeNode* lookup_by_hash(uint32_t hash);

/* Internal codec plumbing routines */
size_t get_type_size(char type);
size_t internal_encode(const void* decoded, const char* format, uint8_t* buf, size_t max_size);
size_t internal_measure_buffer(const uint8_t* buffer, size_t buffer_size);
size_t internal_measure_struct(const void* struct_ptr, const char* format);
void internal_decode_into(const uint8_t* buffer, size_t buffer_size, const char* format, uint8_t* struct_ptr, uint8_t* pool, size_t* pool_offset);
void internal_clone_into(const void* src_struct, const char* format, uint8_t* dst_struct, uint8_t* pool, size_t* pool_offset);

#endif /* TYPE_PRIVATE_H */
