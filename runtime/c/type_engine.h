/* ============================================================================
   AUTO-DESCRIPTIVE TYPE RUNTIME ENGINE (API)
   ============================================================================ */

#ifndef TYPE_ENGINE_H
#define TYPE_ENGINE_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* Register a new schema descriptor into the runtime database */
bool type_register(const char* name, uint32_t hash, const char* format, size_t struct_size);

/* Calculate conservative maximum binary encoded size */
size_t type_encode_size(const char* type, const void* decoded);

/* Encode structure. Returns allocated buffer and exports its size */
void* type_encode(const char* type, const void* decoded, size_t* size);

/* Calculate required memory block size for decoding */
size_t type_decode_size(const void* encoded, size_t size);

/* Decode binary stream without needing the type name argument */
void* type_decode(const void* encoded, size_t* size);

/* Create an exact deep copy of any structured message */
void* type_clone(const char* type, const void* decoded);

/* Free any single-block allocated message or binary buffer */
void type_free(void* ptr);

#endif /* TYPE_ENGINE_H */
