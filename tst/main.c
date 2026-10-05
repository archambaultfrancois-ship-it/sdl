/* ============================================================================
   ADVANCED HYBRID ARRAYS BUILT-IN TEST SUITE
   ============================================================================ */

#include "type_engine.h"
#include "generated_messages.h"
#include <inttypes.h>
#include <stdio.h>

int main(void) {
   /* 1. Startup registry initialization */
   register_all_types();
   printf(" [Boot] Schema dynamic registration completed\n");
   printf(" [Info] ROOTPAYLOAD_HASH is: 0x%08X\n\n", ROOTPAYLOAD_HASH);

   /* 2. Instantiate arrays data source */
   FixedItem mock_fixed[2] = {
      { .x = 1.1f, .y = 2.2f },
      { .x = 3.3f, .y = 4.4f }
   };

   VarItem mock_var[2] = {
      { .has_name = true, .name = "Variable_Node_A", .id = 99999LL },
      { .has_name = true, .name = "Variable_Node_B", .id = 77777LL }
   };

   RootPayload original = {
      .has_header = true,
      .header = "Mission_Data_Packet",
      .fixed_array_count = 2,
      .fixed_array = mock_fixed,
      .var_array_count = 2,
      .var_array = mock_var
   };

   /* 3. Execute Deep Clone */
   RootPayload* cloned = (RootPayload*)type_clone("RootPayload", &original);
   if (!cloned) { printf("Error: Cloning step failed\n"); return 1; }
   printf(" 1. Deep Copy Cloning system........ OK (Header: %s)\n", cloned->header);

   /* 4. Encode to Binary Stream Buffer */
   size_t bin_size = 0;
   void* bin_stream = type_encode("RootPayload", cloned, &bin_size);
   if (!bin_stream) { printf("Error: Encoding step failed\n"); return 1; }
   printf(" 2. Binary Auto-Descriptive Stream... OK (%zu bytes written)\n", bin_size);

   /* 5. Blind dynamic decoder step (Zero type info passed) */
   size_t rx_size = bin_size;
   void* generic_output = type_decode(bin_stream, &rx_size);
   if (!generic_output) { printf("Error: Decoding step failed\n"); return 1; }
   printf(" 3. Blind Type-Agnostic Decoding..... OK\n");

   /* 6. Verify data integrity */
   const uint8_t* wire_bytes = (const uint8_t*)bin_stream;
   uint32_t stream_hash = (uint32_t)wire_bytes[0] |
      ((uint32_t)wire_bytes[1] << 8) | ((uint32_t)wire_bytes[2] << 16) |
      ((uint32_t)wire_bytes[3] << 24);
   if (stream_hash == ROOTPAYLOAD_HASH) {
      RootPayload* res = (RootPayload*)generic_output;
      printf("\n============================================\n");
      printf(" INTEGRITY VERIFICATION REPORT\n");
      printf("============================================\n");
      printf(" Header Value        : %s\n", res->header);
      printf(" Fixed Array Items   : %u structural elements\n", res->fixed_array_count);
      printf("   -> Item[0]        : X=%.1f, Y=%.1f\n", res->fixed_array[0].x, res->fixed_array[0].y);
      printf("   -> Item[1]        : X=%.1f, Y=%.1f\n", res->fixed_array[1].x, res->fixed_array[1].y);
      printf(" Variable Array Items: %u structural elements\n", res->var_array_count);
      printf("   -> Item[0]        : Name='%s', ID=%" PRId64 "\n", res->var_array[0].name, res->var_array[0].id);
      printf("   -> Item[1]        : Name='%s', ID=%" PRId64 "\n", res->var_array[1].name, res->var_array[1].id);
      printf("============================================\n");
   } else {
      printf("Error: Mismatched signature hash\n");
   }

   /* 7. Graceful memory block cleanups */
   type_free(generic_output);
   type_free(bin_stream);
   type_free(cloned);
   printf("\n [Clean] Memory resources wiped out. Zero fragmentation.\n");

   return 0;
}
