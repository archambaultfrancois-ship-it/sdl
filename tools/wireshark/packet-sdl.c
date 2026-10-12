/* SDL2 dissector: uses the same prepared catalogue and validated dynamic codec as C. */
#define HAVE_PLUGINS 1
#include <wsutil/plugins.h>
#include <gmodule.h>
#include <epan/packet.h>
#include <epan/proto.h>
#include <epan/expert.h>
#include <epan/to_str.h>
#include "sdl_context.h"
#include <string.h>
#ifndef VERSION
#define VERSION "0.2.0"
#endif
G_MODULE_EXPORT const char plugin_version[] = VERSION;
G_MODULE_EXPORT const int plugin_want_major = WIRESHARK_VERSION_MAJOR;
G_MODULE_EXPORT const int plugin_want_minor = WIRESHARK_VERSION_MINOR;
G_MODULE_EXPORT void plugin_register(void);
static int proto_sdl = -1, hf_type = -1, hf_description = -1, hf_value = -1;
static gint ett_sdl = -1, ett_value = -1;
static expert_field ei_malformed = EI_INIT;
static GHashTable *catalogues;
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
}
static void cleanup_catalogues(void) {
   if (catalogues)
      g_hash_table_destroy(catalogues);
   catalogues = NULL;
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
   if (length >= 5 && !memcmp(bytes, "SDL2\n", 5)) {
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
static gboolean dissect_heur(tvbuff_t *tvb, packet_info *pinfo, proto_tree *tree, void *data) {
   guint n = tvb_captured_length(tvb);
   gchar *key = channel(pinfo);
   gboolean known = catalogues && g_hash_table_contains(catalogues, key);
   g_free(key);
   if (known || (n >= 5 && !memcmp(tvb_get_ptr(tvb, 0, 5), "SDL2\n", 5))) {
      dissect_sdl(tvb, pinfo, tree, data);
      return TRUE;
   }
   return FALSE;
}
static void register_sdl(void) {
   static hf_register_info hf[] = {
       {&hf_type, {"Message type", "sdl.type", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}},
       {&hf_description,
        {"Catalogue", "sdl.description", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}},
       {&hf_value, {"Value", "sdl.value", FT_STRING, BASE_NONE, NULL, 0, NULL, HFILL}}};
   static gint *ett[] = {&ett_sdl, &ett_value};
   static ei_register_info ei[] = {
       {&ei_malformed,
        {"sdl.malformed", PI_MALFORMED, PI_ERROR, "Malformed SDL2 message", 0, NULL, 0, {0}}}};
   expert_module_t *em;
   proto_sdl = proto_register_protocol("SDL2 Wire (C)", "SDL2", "sdl");
   proto_register_field_array(proto_sdl, hf, array_length(hf));
   proto_register_subtree_array(ett, array_length(ett));
   em = expert_register_protocol(proto_sdl);
   expert_register_field_array(em, ei, array_length(ei));
   register_init_routine(reset_catalogues);
   register_cleanup_routine(cleanup_catalogues);
}
static void handoff_sdl(void) {
   dissector_handle_t h = create_dissector_handle(dissect_sdl, proto_sdl);
   dissector_add_for_decode_as("udp.port", h);
   heur_dissector_add("udp", dissect_heur, "SDL2 Wire heuristic", "sdl_udp", proto_sdl,
                      HEURISTIC_ENABLE);
}
G_MODULE_EXPORT void plugin_register(void) {
   static proto_plugin plugin;
   plugin.register_protoinfo = register_sdl;
   plugin.register_handoff = handoff_sdl;
   proto_register_plugin(&plugin);
}
