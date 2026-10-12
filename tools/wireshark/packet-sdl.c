/* SDL2 dissector: uses the same prepared catalogue and validated dynamic codec as C. */
#define HAVE_PLUGINS 1
#include <ws_version.h>
#if WIRESHARK_VERSION_MAJOR >= 4
#include <wireshark.h>
#else
#include <glib.h>
#include <stdint.h>
#include <stdbool.h>
#include <ws_attributes.h>
#endif
#if WIRESHARK_VERSION_MAJOR > 4 || (WIRESHARK_VERSION_MAJOR == 4 && WIRESHARK_VERSION_MINOR >= 3)
#include <wsutil/plugins.h>
#define SDL_HEUR_RESULT bool
#else
#define SDL_HEUR_RESULT gboolean
#endif
#include <gmodule.h>
#include <epan/packet.h>
#include <epan/proto.h>
#include <epan/expert.h>
#include <epan/to_str.h>
#include <wiretap/wtap.h>
#include "sdl_context.h"
#include "sdl-recording.h"
#include <string.h>
#ifndef VERSION
#define VERSION "0.3.0"
#endif
G_MODULE_EXPORT const char plugin_version[] = VERSION;
G_MODULE_EXPORT const int plugin_want_major = WIRESHARK_VERSION_MAJOR;
G_MODULE_EXPORT const int plugin_want_minor = WIRESHARK_VERSION_MINOR;
G_MODULE_EXPORT void plugin_register(void);
#ifdef WS_PLUGIN_DESC_DISSECTOR
G_MODULE_EXPORT uint32_t plugin_describe(void);
G_MODULE_EXPORT uint32_t plugin_describe(void) { return WS_PLUGIN_DESC_DISSECTOR; }
#endif
static int proto_sdl = -1, hf_type = -1, hf_description = -1, hf_value = -1;
static gint ett_sdl = -1, ett_value = -1;
static expert_field ei_malformed = EI_INIT;
static GHashTable *catalogues;
static SdlRecCapture *recording;
static int proto_rec = -1, hf_rec_kind = -1, hf_rec_crc = -1, hf_rec_id = -1,
   hf_rec_schema = -1, hf_rec_catalogue = -1, hf_rec_topic = -1, hf_rec_root = -1,
   hf_rec_sequence = -1, hf_rec_log = -1, hf_rec_publish = -1, hf_rec_text = -1,
   hf_rec_payload = -1, hf_rec_packets = -1, hf_rec_messages = -1, hf_rec_stream_crc = -1;
static gint ett_rec = -1;
static expert_field ei_rec_malformed = EI_INIT;
static const value_string recording_kinds[] = {
   {1, "HEADER"}, {3, "CATALOGUE"}, {4, "CHANNEL"}, {5, "MESSAGE"},
   {12, "METADATA"}, {15, "END"}, {0, NULL}
};
typedef struct {
   guint32 frame;
   SdlContext *context;
} Announcement;
static void announcement_destroy(gpointer value) {
   Announcement *announcement = (Announcement *)value;
   type_context_free(announcement->context);
   g_free(announcement);
}
static void context_destroy(gpointer value) { g_ptr_array_free((GPtrArray *)value, TRUE); }
static SdlContext *catalogue_at(const char *key, guint32 frame) {
   GPtrArray *flow = (GPtrArray *)g_hash_table_lookup(catalogues, key);
   Announcement *latest = NULL;
   guint i;
   if (!flow)
      return NULL;
   for (i = 0; i < flow->len; ++i) {
      Announcement *a = (Announcement *)g_ptr_array_index(flow, i);
      if (a->frame <= frame && (!latest || a->frame > latest->frame))
         latest = a;
   }
   return latest ? latest->context : NULL;
}
static void announce(const char *key, guint32 frame, SdlContext *context) {
   GPtrArray *flow = (GPtrArray *)g_hash_table_lookup(catalogues, key);
   Announcement *a;
   guint i;
   if (!flow) {
      flow = g_ptr_array_new_with_free_func(announcement_destroy);
      g_hash_table_insert(catalogues, g_strdup(key), flow);
   }
   for (i = 0; i < flow->len; ++i) {
      a = (Announcement *)g_ptr_array_index(flow, i);
      if (a->frame == frame) {
         type_context_free(context);
         return;
      }
   }
   a = g_new(Announcement, 1);
   a->frame = frame;
   a->context = context;
   g_ptr_array_add(flow, a);
}
static void reset_catalogues(void) {
   if (catalogues)
      g_hash_table_destroy(catalogues);
   catalogues = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, context_destroy);
   sdlrec_capture_free(recording);
   recording = sdlrec_capture_new();
}
static void cleanup_catalogues(void) {
   if (catalogues)
      g_hash_table_destroy(catalogues);
   catalogues = NULL;
   sdlrec_capture_free(recording);
   recording = NULL;
}
static gchar *channel(packet_info *pinfo) {
   return g_strdup_printf("%s:%u>%s:%u", address_to_str(wmem_packet_scope(), &pinfo->src),
                          pinfo->srcport, address_to_str(wmem_packet_scope(), &pinfo->dst),
                          pinfo->destport);
}
static void add_dynamic(proto_tree *tree, tvbuff_t *tvb, const char *label,
                        const SdlDynamicValue *value, guint depth) {
   gchar *text = NULL;
   proto_item *item;
   proto_tree *child;
   size_t i;
   if (depth > 64)
      return;
   switch (value->kind) {
   case SDL_DYNAMIC_NULL:
      text = g_strdup("absent");
      break;
   case SDL_DYNAMIC_BOOL:
      text = g_strdup(value->value.boolean ? "true" : "false");
      break;
   case SDL_DYNAMIC_INTEGER:
      text = g_strdup_printf("%" G_GINT64_FORMAT, (gint64)value->value.integer);
      break;
   case SDL_DYNAMIC_UNSIGNED:
      text = g_strdup_printf("%" G_GUINT64_FORMAT, (guint64)value->value.unsigned_integer);
      break;
   case SDL_DYNAMIC_FLOAT:
      text = g_strdup_printf("%.17g", value->value.floating);
      break;
   case SDL_DYNAMIC_COMPLEX:
      text = g_strdup_printf("(%.17g, %.17g)", value->value.complex_value.real,
                             value->value.complex_value.imag);
      break;
   case SDL_DYNAMIC_ENUM:
      text =
          g_strdup_printf("%s (%d)", value->value.enumeration.name, value->value.enumeration.value);
      break;
   case SDL_DYNAMIC_STRING: {
      GString *s = g_string_new("\"");
      for (i = 0; i < value->value.string.size; ++i) {
         unsigned char b = value->value.string.data[i];
         if (b < 32 || b == 127 || b == '"' || b == '\\')
            g_string_append_printf(s, "\\x%02x", b);
         else
            g_string_append_c(s, (char)b);
      }
      g_string_append_c(s, '"');
      text = g_string_free(s, FALSE);
      break;
   }
   case SDL_DYNAMIC_ARRAY:
      item = proto_tree_add_string_format(tree, hf_value, tvb, 0, 0, label, "%s: %zu elements",
                                          label, value->value.array.count);
      child = proto_item_add_subtree(item, ett_value);
      for (i = 0; i < value->value.array.count; ++i) {
         gchar *name = g_strdup_printf("[%zu]", i);
         add_dynamic(child, tvb, name, &value->value.array.items[i], depth + 1);
         g_free(name);
      }
      return;
   case SDL_DYNAMIC_MESSAGE:
      item = proto_tree_add_string_format(tree, hf_value, tvb, 0, 0, label, "%s (%s)", label,
                                          value->value.message->type_name);
      child = proto_item_add_subtree(item, ett_value);
      for (i = 0; i < value->value.message->field_count; ++i)
         add_dynamic(child, tvb, value->value.message->fields[i].name,
                     &value->value.message->fields[i].value, depth + 1);
      return;
   }
   proto_tree_add_string_format(tree, hf_value, tvb, 0, 0, text ? text : "", "%s = %s", label,
                                text ? text : "");
   g_free(text);
}
static int dissect_sdl(tvbuff_t *tvb, packet_info *pinfo, proto_tree *tree, void *data _U_) {
   guint length = tvb_captured_length(tvb);
   gchar *key = channel(pinfo);
   SdlContext *ctx;
   const guint8 *bytes = tvb_get_ptr(tvb, 0, length);
   SdlDynamicMessage *message;
   size_t i;
   proto_item *item = proto_tree_add_item(tree, proto_sdl, tvb, 0, -1, ENC_NA);
   proto_tree *sub = proto_item_add_subtree(item, ett_sdl);
   col_set_str(pinfo->cinfo, COL_PROTOCOL, "SDL2");
   col_clear(pinfo->cinfo, COL_INFO);
   if (!catalogues)
      reset_catalogues();
   if (length >= 5 && !memcmp(bytes, "SDL2|", 5)) {
      ctx = type_prepare(bytes, length);
      if (!ctx)
         expert_add_info_format(pinfo, item, &ei_malformed, "Invalid SDL2 catalogue");
      else {
         gchar *text = g_strndup((const gchar *)bytes, length);
         announce(key, pinfo->num, ctx);
         proto_tree_add_string(sub, hf_description, tvb, 0, length, text);
         g_free(text);
         col_set_str(pinfo->cinfo, COL_INFO, "Catalogue announcement");
      }
   } else {
      ctx = catalogue_at(key, pinfo->num);
      if (!ctx)
         expert_add_info_format(pinfo, item, &ei_malformed,
                                "SDL2 catalogue announcement missing from capture");
      else if ((message = type_decode_dynamic(ctx, bytes, length)) == NULL)
         expert_add_info_format(pinfo, item, &ei_malformed,
                                "Malformed SDL2 data: invalid counters, values or boundaries");
      else {
         proto_tree_add_string(sub, hf_type, tvb, 0, length, message->type_name);
         col_set_str(pinfo->cinfo, COL_INFO, message->type_name);
         for (i = 0; i < message->field_count; ++i)
            add_dynamic(sub, tvb, message->fields[i].name, &message->fields[i].value, 0);
         type_dynamic_free(message);
      }
   }
   g_free(key);
   return length;
}
static SDL_HEUR_RESULT dissect_heur(tvbuff_t *tvb, packet_info *pinfo, proto_tree *tree, void *data) {
   guint n = tvb_captured_length(tvb);
   gchar *key = channel(pinfo);
   gboolean known = catalogues && g_hash_table_contains(catalogues, key);
   g_free(key);
   if (known || (n >= 5 && !memcmp(tvb_get_ptr(tvb, 0, 5), "SDL2|", 5))) {
      dissect_sdl(tvb, pinfo, tree, data);
      return TRUE;
   }
   return FALSE;
}
/* Counted UTF-8 can contain NUL. Escape it before handing text to C string APIs. */
static gchar *recording_text(SdlRecBytes value) {
   GString *s = g_string_new("");
   size_t i;
   for (i = 0; i < value.size; ++i) {
      uint8_t b = value.data[i];
      if (b < 32 || b == 127 || b == '"' || b == '\\') g_string_append_printf(s, "\\x%02x", b);
      else g_string_append_c(s, (char)b);
   }
   return g_string_free(s, FALSE);
}
static void recording_field(proto_tree *tree, int hf, tvbuff_t *tvb,
                            const uint8_t *base, SdlRecBytes value) {
   gchar *text = recording_text(value);
   int offset = base && value.data ? (int)(value.data - base) : 0;
   int size = base ? (int)value.size : 0;
   proto_tree_add_string(tree, hf, tvb, offset, size, text);
   g_free(text);
}
static int dissect_recording(tvbuff_t *tvb, packet_info *pinfo, proto_tree *tree, void *data _U_) {
   guint length = tvb_captured_length(tvb);
   const uint8_t *bytes = tvb_get_ptr(tvb, 0, length);
   const char *error = NULL;
   SdlRecResult result;
   SdlRecPacket *r = &result.packet;
   size_t i;
   gboolean valid;
   proto_item *item = proto_tree_add_item(tree, proto_rec, tvb, 0, -1, ENC_NA);
   proto_tree *sub = proto_item_add_subtree(item, ett_rec);
   col_set_str(pinfo->cinfo, COL_PROTOCOL, "SDLREC");
   col_set_str(pinfo->cinfo, COL_INFO, "Invalid SDL recording packet");
   if (!recording) recording = sdlrec_capture_new();
   valid = sdlrec_process(recording, pinfo->num, bytes, length, &result, &error);
   if (result.parsed) {
      uint64_t expected = r->kind == 5 ? r->log_time : 0;
      if (pinfo->abs_ts.secs < 0 || (uint64_t)pinfo->abs_ts.secs != expected / 1000000000u ||
          pinfo->abs_ts.nsecs < 0 || (uint32_t)pinfo->abs_ts.nsecs != expected % 1000000000u) {
         valid = FALSE; error = "Capture timestamp differs from recording log time";
      }
   }
   if (tvb_reported_length(tvb) != length) {
      valid = FALSE; error = "Truncated captured recording packet";
   }
   if (!valid) expert_add_info_format(pinfo, item, &ei_rec_malformed, "%s", error ? error : "Invalid recording");
   if (result.parsed) {
      col_set_str(pinfo->cinfo, COL_INFO, sdlrec_kind_name(r->kind));
      proto_tree_add_uint(sub, hf_rec_kind, tvb, 5, 1, r->kind);
      proto_tree_add_uint(sub, hf_rec_crc, tvb, 12, 4, r->crc);
      if (r->kind == 3 || r->kind == 4 || r->kind == 5)
         proto_tree_add_uint(sub, hf_rec_id, tvb, 16, 2, r->id);
      switch (r->kind) {
      case 1:
         recording_field(sub, hf_rec_text, tvb, bytes, r->profile);
         recording_field(sub, hf_rec_text, tvb, bytes, r->library); break;
      case 3:
         recording_field(sub, hf_rec_catalogue, tvb, bytes, r->catalogue); break;
      case 4:
         proto_tree_add_uint(sub, hf_rec_schema, tvb, 18, 2, r->schema_id);
         recording_field(sub, hf_rec_topic, tvb, bytes, r->topic);
         recording_field(sub, hf_rec_root, tvb, bytes, r->root); break;
      case 5:
         proto_tree_add_uint(sub, hf_rec_sequence, tvb, 18, 4, r->sequence);
         proto_tree_add_uint64(sub, hf_rec_log, tvb, 22, 8, r->log_time);
         proto_tree_add_uint64(sub, hf_rec_publish, tvb, 30, 8, r->publish_time);
         proto_tree_add_item(sub, hf_rec_payload, tvb, 38, (int)r->payload.size, ENC_NA);
         if (result.channel) {
            gchar *topic = recording_text(result.channel->topic), *root = recording_text(result.channel->root);
            recording_field(sub, hf_rec_topic, tvb, NULL, result.channel->topic);
            recording_field(sub, hf_rec_root, tvb, NULL, result.channel->root);
            proto_tree_add_uint(sub, hf_rec_schema, tvb, 0, 0, result.channel->schema_id);
            col_add_fstr(pinfo->cinfo, COL_INFO, "MESSAGE %s / %s", topic, root);
            g_free(topic); g_free(root);
         }
         if (result.message && valid) {
            proto_tree_add_string(sub, hf_type, tvb, 38, (int)r->payload.size, result.message->type_name);
            for (i = 0; i < result.message->field_count; ++i)
               add_dynamic(sub, tvb, result.message->fields[i].name, &result.message->fields[i].value, 0);
         }
         break;
      case 12: recording_field(sub, hf_rec_text, tvb, bytes, r->name); break;
      case 15:
         proto_tree_add_uint64(sub, hf_rec_packets, tvb, 16, 8, r->packet_count);
         proto_tree_add_uint64(sub, hf_rec_messages, tvb, 24, 8, r->message_count);
         proto_tree_add_uint(sub, hf_rec_stream_crc, tvb, 32, 4, r->stream_crc); break;
      }
      for (i = 0; i < r->metadata_count; ++i) {
         gchar *key = recording_text(r->metadata[i].key), *value = recording_text(r->metadata[i].value);
         proto_tree_add_string_format(sub, hf_rec_text, tvb, 0, 0, value, "%s = %s", key, value);
         g_free(key); g_free(value);
      }
   }
   sdlrec_result_clear(&result);
   return length;
}
static void register_sdl(void) {
   static hf_register_info hf[] = {
       {&hf_type, {"Message type", "sdl.type", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}},
       {&hf_description,
        {"Catalogue", "sdl.description", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}},
       {&hf_value, {"Value", "sdl.value", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}}};
   static hf_register_info rec_hf[] = {
       {&hf_rec_kind, {"Record kind", "sdlrec.kind", FT_UINT8, BASE_DEC, VALS(recording_kinds), 0, NULL, HFILL}},
       {&hf_rec_crc, {"Packet CRC32", "sdlrec.crc", FT_UINT32, BASE_HEX, NULL, 0, NULL, HFILL}},
       {&hf_rec_id, {"Local ID", "sdlrec.id", FT_UINT16, BASE_DEC, NULL, 0, NULL, HFILL}},
       {&hf_rec_schema, {"Catalogue ID", "sdlrec.schema_id", FT_UINT16, BASE_DEC, NULL, 0, NULL, HFILL}},
       {&hf_rec_catalogue, {"SDL2 catalogue", "sdlrec.catalogue", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}},
       {&hf_rec_topic, {"Topic", "sdlrec.topic", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}},
       {&hf_rec_root, {"Root type", "sdlrec.root_type", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}},
       {&hf_rec_sequence, {"Sequence", "sdlrec.sequence", FT_UINT32, BASE_DEC, NULL, 0, NULL, HFILL}},
       {&hf_rec_log, {"Log time (ns)", "sdlrec.log_time_ns", FT_UINT64, BASE_DEC, NULL, 0, NULL, HFILL}},
       {&hf_rec_publish, {"Publish time (ns)", "sdlrec.publish_time_ns", FT_UINT64, BASE_DEC, NULL, 0, NULL, HFILL}},
       {&hf_rec_text, {"Information", "sdlrec.text", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}},
       {&hf_rec_payload, {"Unchanged SDL2 payload", "sdlrec.payload", FT_BYTES, BASE_NONE, NULL, 0, NULL, HFILL}},
       {&hf_rec_packets, {"Packets before END", "sdlrec.packet_count", FT_UINT64, BASE_DEC, NULL, 0, NULL, HFILL}},
       {&hf_rec_messages, {"Messages", "sdlrec.message_count", FT_UINT64, BASE_DEC, NULL, 0, NULL, HFILL}},
       {&hf_rec_stream_crc, {"Stream CRC32", "sdlrec.stream_crc", FT_UINT32, BASE_HEX, NULL, 0, NULL, HFILL}}};
   static gint *ett[] = {&ett_sdl, &ett_value, &ett_rec};
   static ei_register_info ei[] = {
       {&ei_malformed,
        {"sdl.malformed", PI_MALFORMED, PI_ERROR, "Malformed SDL2 message", EXPFILL}}};
   expert_module_t *em;
   proto_sdl = proto_register_protocol("SDL2 Wire (C)", "SDL2", "sdl");
   proto_register_field_array(proto_sdl, hf, array_length(hf));
   proto_register_subtree_array(ett, array_length(ett));
   register_dissector("sdl_c", dissect_sdl, proto_sdl);
   em = expert_register_protocol(proto_sdl);
   expert_register_field_array(em, ei, array_length(ei));
   proto_rec = proto_register_protocol("SDL Recording USER0 (C)", "SDLREC", "sdlrec");
   proto_register_field_array(proto_rec, rec_hf, array_length(rec_hf));
   register_dissector("sdlrec", dissect_recording, proto_rec);
   {
      static ei_register_info rec_ei[] = {
         {&ei_rec_malformed, {"sdlrec.malformed", PI_MALFORMED, PI_ERROR,
          "Malformed SDL recording packet", EXPFILL}}};
      em = expert_register_protocol(proto_rec);
      expert_register_field_array(em, rec_ei, array_length(rec_ei));
   }
   register_init_routine(reset_catalogues);
   register_cleanup_routine(cleanup_catalogues);
}
static void handoff_sdl(void) {
   dissector_handle_t h = create_dissector_handle(dissect_sdl, proto_sdl);
   dissector_add_for_decode_as("udp.port", h);
   heur_dissector_add("udp", dissect_heur, "SDL2 Wire heuristic", "sdl_udp", proto_sdl,
                      HEURISTIC_ENABLE);
   dissector_add_uint("wtap_encap", WTAP_ENCAP_USER0, find_dissector("sdlrec"));
}
G_MODULE_EXPORT void plugin_register(void) {
   static proto_plugin plugin;
   plugin.register_protoinfo = register_sdl;
   plugin.register_handoff = handoff_sdl;
   proto_register_plugin(&plugin);
}
