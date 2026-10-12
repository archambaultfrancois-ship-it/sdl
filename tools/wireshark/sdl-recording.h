#ifndef SDLREC_DISSECTION_H
#define SDLREC_DISSECTION_H
#include "sdl_context.h"
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/* Internal C99 adapter shared by the Wireshark plugin and standalone tests.
 * Packet text/payload views borrow the input; metadata entries are owned.
 * Clear every parsed packet/result, including on failure. Error strings are static.
 */
typedef struct { const uint8_t *data; size_t size; } SdlRecBytes;
typedef struct { SdlRecBytes key, value; } SdlRecPair;
typedef struct {
   uint8_t kind;
   uint16_t id, schema_id;
   uint32_t crc, sequence, stream_crc;
   uint64_t log_time, publish_time, packet_count, message_count;
   SdlRecBytes profile, library, name, encoding, catalogue, topic, root, payload;
   SdlRecPair *metadata;
   size_t metadata_count;
} SdlRecPacket;
typedef struct SdlRecCapture SdlRecCapture;
typedef struct {
   SdlRecPacket packet;
   bool parsed;
   /* Declarations borrow the capture; decoded message is owned by this result. */
   const SdlRecPacket *channel, *schema;
   SdlDynamicMessage *message;
} SdlRecResult;

bool sdlrec_parse(const uint8_t *data, size_t size, SdlRecPacket *, const char **error);
void sdlrec_packet_clear(SdlRecPacket *);
SdlRecCapture *sdlrec_capture_new(void);
void sdlrec_capture_free(SdlRecCapture *);
/* Frame numbers increase on the initial pass. Revisits never advance counters
 * or make future declarations visible to an earlier message. One capture state
 * represents one SDL recording section/interface, independently of UDP caches.
 */
bool sdlrec_process(SdlRecCapture *, uint32_t frame, const uint8_t *, size_t,
                   SdlRecResult *, const char **error);
void sdlrec_result_clear(SdlRecResult *);
const char *sdlrec_kind_name(uint8_t kind);
#endif
