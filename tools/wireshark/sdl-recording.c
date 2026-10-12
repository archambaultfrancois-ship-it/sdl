/* SDL recording USER0 adapter, independent of the Wireshark API. */
#include "sdl-recording.h"
#include "sdl_wire.h"
#include <stdlib.h>
#include <string.h>

#define MAX_TEXT (1024u * 1024u)
#define MAX_PAYLOAD (32u * 1024u * 1024u)
#define MAX_PACKET (64u * 1024u * 1024u - 32u)
#define PROFILE "sdl2-recording-pcapng-v1"
typedef struct { const uint8_t *data; size_t size, pos; const char *error; } Cursor;
static bool fail(const char **error, const char *reason) { *error = reason; return false; }
static const uint8_t *take(Cursor *c, size_t n) {
   const uint8_t *value;
   if (c->error) return NULL;
   if (n > c->size - c->pos) { c->error = "Field exceeds recording packet"; return NULL; }
   value = c->data + c->pos; c->pos += n; return value;
}
static uint16_t u16(Cursor *c) {
   const uint8_t *p = take(c, 2);
   return p ? (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8)) : 0;
}
static uint32_t u32(Cursor *c) {
   const uint8_t *p = take(c, 4);
   return p ? (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24) : 0;
}
static uint64_t u64(Cursor *c) {
   uint64_t low = u32(c), high = u32(c);
   return low | (high << 32);
}
static SdlRecBytes blob(Cursor *c, size_t n) {
   SdlRecBytes b = {NULL, 0};
   if (n > MAX_TEXT) c->error = "Recording text/catalogue exceeds 1 MiB";
   b.data = take(c, n); if (b.data) b.size = n; return b;
}
static SdlRecBytes text(Cursor *c) {
   size_t n = u32(c);
   SdlRecBytes b = blob(c, n);
   if (b.data && !sdl_wire_valid_utf8(b.data, b.size)) c->error = "Invalid recording UTF-8";
   return b;
}
static bool equal(SdlRecBytes a, SdlRecBytes b) {
   return a.size == b.size && (!a.size || !memcmp(a.data, b.data, a.size));
}
static bool literal(SdlRecBytes b, const char *s) {
   SdlRecBytes expected = {(const uint8_t *)s, strlen(s)};
   return equal(b, expected);
}
static int pair_compare(const void *a, const void *b) {
   const SdlRecPair *x = a, *y = b;
   size_t n = x->key.size < y->key.size ? x->key.size : y->key.size;
   int result = n ? memcmp(x->key.data, y->key.data, n) : 0;
   return result ? result : (x->key.size > y->key.size) - (x->key.size < y->key.size);
}
static void metadata(Cursor *c, SdlRecPacket *r) {
   size_t n = u32(c), count = 0, i;
   const uint8_t *data;
   Cursor sub;
   if (n > MAX_TEXT) c->error = "Recording metadata exceeds 1 MiB";
   data = take(c, n); if (!data) return;
   sub = (Cursor){data, n, 0, NULL};
   /* Count and validate before allocating; even empty key/value pairs take 8 bytes. */
   while (sub.pos < sub.size && !sub.error) {
      text(&sub); text(&sub);
      if (++count > 65535) sub.error = "Too many recording metadata keys";
   }
   if (sub.error) { c->error = sub.error; return; }
   if (!count) return;
   r->metadata = calloc(count, sizeof(*r->metadata));
   if (!r->metadata) { c->error = "Recording metadata allocation failed"; return; }
   r->metadata_count = count;
   sub.pos = 0;
   for (i = 0; i < count; ++i) {
      r->metadata[i].key = text(&sub); r->metadata[i].value = text(&sub);
   }
   qsort(r->metadata, count, sizeof(*r->metadata), pair_compare);
   for (i = 0; i < count; ++i) {
      if (i && equal(r->metadata[i - 1].key, r->metadata[i].key)) {
         c->error = "Duplicate recording metadata key"; return;
      }
      if (literal(r->metadata[i].key, "root_type")) r->root = r->metadata[i].value;
   }
}
/* CRC32/IEEE, zlib convention. No external recording library is linked. */
static uint32_t crc32(uint32_t crc, const uint8_t *data, size_t size) {
   size_t i; unsigned j;
   crc ^= UINT32_MAX;
   for (i = 0; i < size; ++i) {
      crc ^= data[i];
      for (j = 0; j < 8; ++j) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
   }
   return crc ^ UINT32_MAX;
}
const char *sdlrec_kind_name(uint8_t kind) {
   switch (kind) {
   case 1: return "HEADER"; case 3: return "CATALOGUE"; case 4: return "CHANNEL";
   case 5: return "MESSAGE"; case 12: return "METADATA"; case 15: return "END";
   default: return NULL;
   }
}
void sdlrec_packet_clear(SdlRecPacket *r) { free(r->metadata); memset(r, 0, sizeof(*r)); }
bool sdlrec_parse(const uint8_t *data, size_t size, SdlRecPacket *r, const char **error) {
   Cursor c; uint32_t body_size; size_t n;
   memset(r, 0, sizeof(*r)); *error = NULL;
   if (!data || size < 16 || size > MAX_PACKET) return fail(error, "Invalid recording packet size");
   if (memcmp(data, "S2RC", 4) || data[4] != 1) return fail(error, "Unsupported recording magic/version");
   r->kind = data[5];
   if (!sdlrec_kind_name(r->kind)) return fail(error, "Unknown recording packet kind");
   c = (Cursor){data, size, 6, NULL};
   if (u16(&c)) return fail(error, "Unsupported recording packet flags");
   body_size = u32(&c); r->crc = u32(&c);
   if (body_size != size - 16) return fail(error, "Recording packet body length mismatch");
   if (crc32(crc32(0, data, 12), data + 16, size - 16) != r->crc)
      return fail(error, "Recording packet CRC mismatch");
   switch (r->kind) {
   case 1:
      r->profile = text(&c); r->library = text(&c);
      if (!c.error && !literal(r->profile, PROFILE)) c.error = "Unsupported recording profile";
      break;
   case 3:
      r->id = u16(&c); r->name = text(&c); r->encoding = text(&c);
      n = u32(&c); r->catalogue = blob(&c, n);
      if (!c.error && (!r->id || !literal(r->encoding, "sdl2") || n < 5 ||
          memcmp(r->catalogue.data, "SDL2|", 5) || !sdl_wire_valid_utf8(r->catalogue.data, n)))
         c.error = "Invalid SDL recording catalogue";
      break;
   case 4:
      r->id = u16(&c); r->schema_id = u16(&c); r->topic = text(&c); r->encoding = text(&c);
      metadata(&c, r);
      if (!c.error && (!r->id || !r->schema_id || !r->topic.size || !r->root.size || !literal(r->encoding, "sdl2")))
         c.error = "Invalid SDL recording channel";
      break;
   case 5:
      r->id = u16(&c); r->sequence = u32(&c); r->log_time = u64(&c); r->publish_time = u64(&c);
      n = size - c.pos;
      r->payload.data = take(&c, n); r->payload.size = n;
      if (!c.error && (!r->id || !n || n > MAX_PAYLOAD)) c.error = "Invalid SDL recording payload size/channel";
      break;
   case 12: r->name = text(&c); metadata(&c, r); break;
   case 15: r->packet_count = u64(&c); r->message_count = u64(&c); r->stream_crc = u32(&c); break;
   }
   if (c.error) return fail(error, c.error);
   if (c.pos != size) return fail(error, "Trailing recording packet bytes");
   return true;
}

typedef struct {
   uint32_t frame;
   uint8_t *data;
   SdlRecPacket packet;
   SdlContext *context;
} Declaration;
struct SdlRecCapture {
   Declaration **schemas, **channels;
   uint32_t header_frame, end_frame, last_frame, stream_crc;
   uint64_t packets, messages;
   const char *end_error;
};
SdlRecCapture *sdlrec_capture_new(void) {
   SdlRecCapture *c = calloc(1, sizeof(*c));
   if (!c) return NULL;
   c->schemas = calloc(65536, sizeof(*c->schemas));
   c->channels = calloc(65536, sizeof(*c->channels));
   if (!c->schemas || !c->channels) { sdlrec_capture_free(c); return NULL; }
   return c;
}
static void declaration_free(Declaration *d) {
   if (!d) return;
   type_context_free(d->context); sdlrec_packet_clear(&d->packet); free(d->data); free(d);
}
void sdlrec_capture_free(SdlRecCapture *c) {
   size_t i;
   if (!c) return;
   for (i = 0; i < 65536; ++i) {
      if (c->channels) declaration_free(c->channels[i]);
      if (c->schemas) declaration_free(c->schemas[i]);
   }
   free(c->schemas); free(c->channels); free(c);
}
void sdlrec_result_clear(SdlRecResult *r) {
   sdlrec_packet_clear(&r->packet); type_dynamic_free(r->message); memset(r, 0, sizeof(*r));
}
static bool declaration_equal(const SdlRecPacket *a, const SdlRecPacket *b) {
   size_t i;
   if (a->kind == 3) return equal(a->name, b->name) && equal(a->encoding, b->encoding) && equal(a->catalogue, b->catalogue);
   if (a->schema_id != b->schema_id || !equal(a->topic, b->topic) || !equal(a->encoding, b->encoding) ||
       a->metadata_count != b->metadata_count) return false;
   for (i = 0; i < a->metadata_count; ++i)
      if (!equal(a->metadata[i].key, b->metadata[i].key) || !equal(a->metadata[i].value, b->metadata[i].value)) return false;
   return true;
}
static Declaration *declaration(uint32_t frame, const uint8_t *data, size_t size, const char **error) {
   Declaration *d = calloc(1, sizeof(*d));
   if (!d) { fail(error, "Recording declaration allocation failed"); return NULL; }
   d->data = malloc(size);
   if (!d->data) { declaration_free(d); fail(error, "Recording declaration allocation failed"); return NULL; }
   memcpy(d->data, data, size); d->frame = frame;
   if (!sdlrec_parse(d->data, size, &d->packet, error)) { declaration_free(d); return NULL; }
   return d;
}
static bool known_root(SdlContext *ctx, SdlRecBytes root) {
   size_t i;
   for (i = 1; i <= type_context_message_count(ctx); ++i)
      if (literal(root, type_context_message_name(ctx, (uint32_t)i))) return true;
   return false;
}
bool sdlrec_process(SdlRecCapture *c, uint32_t frame, const uint8_t *data, size_t size,
                   SdlRecResult *out, const char **error) {
   SdlRecPacket *r; Declaration *d, *schema, *ch; bool advance;
   memset(out, 0, sizeof(*out));
   if (!sdlrec_parse(data, size, &out->packet, error)) return false;
   out->parsed = true;
   if (!c || !frame) return fail(error, "Invalid recording capture/frame");
   r = &out->packet; advance = frame > c->last_frame;
   if (advance) c->last_frame = frame;
   if (c->end_frame && frame > c->end_frame) return fail(error, "Recording packet follows END");
   if (!c->header_frame || c->header_frame > frame) {
      if (r->kind != 1 || !advance) return fail(error, "Recording HEADER must come first");
      c->header_frame = frame;
   }
   if (advance && r->kind != 15) {
      c->stream_crc = crc32(c->stream_crc, data, size); ++c->packets;
      if (r->kind == 5) ++c->messages;
   }
   switch (r->kind) {
   case 1:
      if (frame != c->header_frame) return fail(error, "Duplicate recording HEADER");
      break;
   case 3:
      d = c->schemas[r->id];
      if (d) {
         if (d->frame > frame) return fail(error, "Catalogue declaration is from a later frame");
         if (!declaration_equal(&d->packet, r)) return fail(error, "Conflicting recording catalogue ID");
      } else {
         d = declaration(frame, data, size, error); if (!d) return false;
         d->context = type_prepare(r->catalogue.data, r->catalogue.size);
         if (!d->context) { declaration_free(d); return fail(error, "Invalid SDL2 catalogue syntax"); }
         c->schemas[r->id] = d;
      }
      out->schema = &d->packet; break;
   case 4:
      schema = c->schemas[r->schema_id];
      if (!schema || schema->frame > frame) return fail(error, "Catalogue declaration missing before channel");
      if (!known_root(schema->context, r->root)) return fail(error, "Channel root_type is not a catalogue message");
      d = c->channels[r->id];
      if (d) {
         if (d->frame > frame) return fail(error, "Channel declaration is from a later frame");
         if (!declaration_equal(&d->packet, r)) return fail(error, "Conflicting recording channel ID");
      } else {
         d = declaration(frame, data, size, error); if (!d) return false;
         c->channels[r->id] = d;
      }
      out->channel = &d->packet; out->schema = &schema->packet; break;
   case 5:
      ch = c->channels[r->id];
      if (!ch || ch->frame > frame) return fail(error, "Channel declaration missing before message");
      schema = c->schemas[ch->packet.schema_id];
      out->channel = &ch->packet; out->schema = &schema->packet;
      out->message = type_decode_dynamic(schema->context, r->payload.data, r->payload.size);
      if (!out->message) return fail(error, "Malformed SDL2 message body");
      if (!literal(ch->packet.root, out->message->type_name)) return fail(error, "Payload root differs from channel root_type");
      break;
   case 15:
      if (advance) {
         c->end_frame = frame;
         if (r->packet_count != c->packets || r->message_count != c->messages || r->stream_crc != c->stream_crc)
            c->end_error = "Recording END count or stream CRC mismatch";
      }
      if (frame != c->end_frame) return fail(error, "Duplicate or out-of-order recording END");
      if (c->end_error) return fail(error, c->end_error);
      break;
   default: break;
   }
   return true;
}
