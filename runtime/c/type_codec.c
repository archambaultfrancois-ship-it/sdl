/* ============================================================================
   LOW-LEVEL BINARY STREAM SERIALIZATION PLUMBER
   ============================================================================ */

#include "type_private.h"
#include <string.h>
#include <complex.h>

#define MAKE_HEADER(id, wire) ((uint8_t)(((id) << 3) | ((wire) & 0x07)))

size_t get_type_size(char type) {
   switch (type) {
      case 'b': case 'B': return 1;
      case 'h': case 'H': return 2;
      case 'i': case 'I': case 'e': return 4;
      case 'l': case 'L': return 8;
      case 'f': case 'F': return 4;
      case 'd': case 'D': return 8;
      case 'c': case 'C': return sizeof(float complex);
      case 'z': case 'Z': return sizeof(double complex);
      default: return 0;
   }
}

size_t internal_encode(const void* decoded, const char* format, uint8_t* buf, size_t max_size) {
   size_t offset = 0; const uint8_t* p = (const uint8_t*)decoded; uint8_t field_id = 1; size_t i = 0;
   while (format[i] != '\0') {
      char type = format[i];
      if (type == 's') {
         bool has_str = *(const bool*)p; p += sizeof(bool);
         const char* str = *(const char* const *)p; p += sizeof(const char*);
         if (has_str && str) {
            size_t len = strlen(str);
            if (offset + 1 + 4 + len > max_size) return 0;
            buf[offset++] = MAKE_HEADER(field_id, 2);
            *(uint32_t*)&buf[offset] = (uint32_t)len; offset += 4;
            memcpy(&buf[offset], str, len); offset += len;
         }
         field_id++; i++;
      }
      else if (type == 'a') {
         uint32_t count = *(const uint32_t*)p; p += sizeof(uint32_t);
         const uint8_t* arr_ptr = *(const uint8_t* const *)p; p += sizeof(void*); i++;
         bool explicit_mode = (format[i] == '('); size_t element_size = 0;
         if (explicit_mode) {
            int depth = 0; i++;
            while (format[i] != '\0') {
               if (format[i] == '(') depth++;
               if (format[i] == ')') { if (depth == 0) { i++; break; } depth--; }
               element_size += get_type_size(format[i]); i++;
            }
         } else { element_size = get_type_size(format[i]); i++; }
         if (count > 0 && arr_ptr && element_size > 0) {
            size_t data_size = count * element_size; size_t total_payload = 4 + data_size;
            if (offset + 1 + 4 + total_payload > max_size) return 0;
            buf[offset++] = MAKE_HEADER(field_id, 2);
            *(uint32_t*)&buf[offset] = (uint32_t)total_payload; offset += 4;
            *(uint32_t*)&buf[offset] = count; offset += 4;
            memcpy(&buf[offset], arr_ptr, data_size); offset += data_size;
         }
         field_id++;
      }
      else if (type == '(') { i++; }
      else if (type == ')') { i++; field_id++; }
      else {
         size_t t_size = get_type_size(type); uint8_t wire = (t_size >= 8) ? 1 : 0;
         if (offset + 1 + t_size > max_size) return 0;
         buf[offset++] = MAKE_HEADER(field_id, wire);
         memcpy(&buf[offset], p, t_size);
         offset += t_size; p += t_size; field_id++; i++;
      }
   }
   return offset;
}

size_t internal_measure_buffer(const uint8_t* buffer, size_t buffer_size) {
   size_t offset = 0; size_t total_needed = 0;
   while (offset < buffer_size) {
      uint8_t header = buffer[offset++]; uint8_t wire_type = header & 0x07;
      if (wire_type == 2) {
         if (offset + 4 > buffer_size) return 0;
         uint32_t len = *(uint32_t*)&buffer[offset]; offset += 4;
         if (offset + len > buffer_size) return 0;
         total_needed += (len + 1); offset += len;
      } else if (wire_type == 1) { if (offset + 8 > buffer_size) return 0; offset += 8; }
      else if (wire_type == 0) { if (offset + 4 > buffer_size) return 0; offset += 4; }
      else return 0;
   }
   return total_needed;
}

size_t internal_measure_struct(const void* struct_ptr, const char* format) {
   size_t total_needed = 0; const uint8_t* p = (const uint8_t*)struct_ptr; size_t i = 0;
   while (format[i] != '\0') {
      char type = format[i];
      if (type == 's') {
         bool has_str = *(const bool*)p; p += sizeof(bool);
         const char* str = *(const char* const *)p; p += sizeof(const char*);
         if (has_str && str) total_needed += (strlen(str) + 1);
         i++;
      }
      else if (type == 'a') {
         uint32_t count = *(const uint32_t*)p; p += sizeof(uint32_t); p += sizeof(void*); i++;
         bool explicit_mode = (format[i] == '('); size_t element_size = 0;
         if (explicit_mode) {
            int depth = 0; i++;
            while (format[i] != '\0') {
               if (format[i] == '(') depth++;
               if (format[i] == ')') { if (depth == 0) { i++; break; } depth--; }
               element_size += get_type_size(format[i]); i++;
            }
         } else { element_size = get_type_size(format[i]); i++; }
         if (count > 0 && element_size > 0) total_needed += (count * element_size);
      }
      else if (type == '(' || type == ')') i++;
      else { p += get_type_size(type); i++; }
   }
   return total_needed;
}

void internal_decode_into(const uint8_t* buffer, size_t buffer_size, const char* format, uint8_t* struct_ptr, uint8_t* pool, size_t* pool_offset) {
   size_t offset = 0; uint8_t* p = struct_ptr; uint8_t target_field_id = 1; size_t i = 0;
   while (format[i] != '\0') {
      char type = format[i]; if (type == '(') { i++; continue; } if (type == ')') { i++; target_field_id++; continue; }
      size_t scan = 0; bool found = false; uint32_t b_len = 0; size_t b_data_pos = 0; uint8_t b_raw_data = {0};
      while (scan < buffer_size) {
         uint8_t header = buffer[scan++]; uint8_t fid = header >> 3; uint8_t wire = header & 0x07;
         if (fid == target_field_id) {
            found = true;
            if (wire == 2) { b_len = *(uint32_t*)&buffer[scan]; b_data_pos = scan + 4; }
            else { memcpy(b_raw_data, &buffer[scan], (wire == 1) ? 8 : 4); }
            break;
         }
         if (wire == 2) scan += 4 + *(uint32_t*)&buffer[scan]; else scan += (wire == 1) ? 8 : 4;
      }
      if (type == 's') {
         bool* has_str = (bool*)p; p += sizeof(bool); char** str_ptr = (char**)p; p += sizeof(char*);
         if (found) {
            *has_str = true; char* dst = (char*)&pool[*pool_offset];
            memcpy(dst, &buffer[b_data_pos], b_len); dst[b_len] = '\0';
            *str_ptr = dst; *pool_offset += (b_len + 1);
         }
      }
      else if (type == 'a') {
         uint32_t* count_ptr = (uint32_t*)p; p += sizeof(uint32_t); void** arr_ptr = (void**)p; p += sizeof(void*);
         if (format[i+1] == '(') {
            i++; int d = 0; Authorities: while (format[i] != '\0') { if (format[i] == '(') d++; if (format[i] == ')') { d--; if (d == 0) { i++; break; } } i++; } i--;
         }
         if (found) {
            uint32_t items = *(uint32_t*)&buffer[b_data_pos]; *count_ptr = items;
            size_t payload_bytes = b_len - 4; uint8_t* dst = &pool[*pool_offset];
            memcpy(dst, &buffer[b_data_pos + 4], payload_bytes); *arr_ptr = dst; *pool_offset += payload_bytes;
         }
      }
      else { size_t t_size = get_type_size(type); if (found) { memcpy(p, b_raw_data, t_size); } p += t_size; }
      target_field_id++; i++;
   }
}

void internal_clone_into(const void* src_struct, const char* format, uint8_t* dst_struct, uint8_t* pool, size_t* pool_offset) {
   const uint8_t* src = (const uint8_t*)src_struct; uint8_t* dst = dst_struct; size_t i = 0;
   while (format[i] != '\0') {
      char type = format[i];
      if (type == 's') {
         bool has_str = *(const bool*)src; *(bool*)dst = has_str; src += sizeof(bool); dst += sizeof(bool);
         const char* src_str = *(const char* const *)src; char** dst_str_ptr = (char**)dst;
         if (has_str && src_str) {
            size_t len = strlen(src_str); char* target_pool_ptr = (char*)&pool[*pool_offset];
            memcpy(target_pool_ptr, src_str, len + 1); *dst_str_ptr = target_pool_ptr; *pool_offset += (len + 1);
         } else *dst_str_ptr = NULL;
         src += sizeof(char*); dst += sizeof(char*); i++;
      }
      else if (type == 'a') {
         uint32_t count = *(const uint32_t*)src; *(uint32_t*)dst = count; src += sizeof(uint32_t); dst += sizeof(uint32_t);
         const uint8_t* src_arr = *(const uint8_t* const *)src; void** dst_arr_ptr = (void**)dst; i++;
         bool explicit_mode = (format[i] == '('); size_t element_size = 0;
         if (explicit_mode) {
            int depth = 0; i++; while (format[i] != '\0') { if (format[i] == '(') depth++; if (format[i] == ')') { if (depth == 0) { i++; break; } depth--; } element_size += get_type_size(format[i]); i++; }
         } else { element_size = get_type_size(format[i]); i++; }
         if (count > 0 && src_arr && element_size > 0) {
            size_t data_size = count * element_size; uint8_t* target_pool_ptr = &pool[*pool_offset];
            memcpy(target_pool_ptr, src_arr, data_size); *dst_arr_ptr = target_pool_ptr; *pool_offset += data_size;
         } else *dst_arr_ptr = NULL;
      }
      else if (type == '(' || type == ')') i++;
      else { size_t t_size = get_type_size(type); memcpy(dst, src, t_size); src += t_size; dst += t_size; i++; }
   }
}
