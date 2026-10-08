/* Generic SDL wire dissector for Wireshark. */
#define HAVE_PLUGINS 1
#include <wsutil/plugins.h>
#include <gmodule.h>
#include <epan/packet.h>
#include <epan/proto.h>
#include <epan/prefs.h>
#include <epan/expert.h>

#include <math.h>
#include <stdint.h>
#include <string.h>

#define SDL_MAX_DEPTH 32
#define SDL_MAX_MESSAGES 4096

#ifndef VERSION
#define VERSION "0.1.0"
#endif

G_MODULE_EXPORT const char plugin_version[] = VERSION;
G_MODULE_EXPORT const int plugin_want_major = WIRESHARK_VERSION_MAJOR;
G_MODULE_EXPORT const int plugin_want_minor = WIRESHARK_VERSION_MINOR;

G_MODULE_EXPORT void plugin_register(void);

static int proto_sdl = -1;
static int hf_sdl_type = -1, hf_sdl_hash = -1, hf_sdl_field_id = -1;
static int hf_sdl_payload = -1, hf_sdl_value = -1;
static gint ett_sdl = -1, ett_sdl_field = -1;
static expert_field ei_sdl_malformed = EI_INIT;
static gboolean pref_little_endian = FALSE;

typedef struct { guint32 id; gchar *name; guint8 modifier; gchar *type;
   guint8 dimensions; guint32 *shape; } SdlField;
typedef struct { gchar *name; guint count; SdlField *fields; } SdlMessage;
typedef struct { gchar *name; gint32 value; } SdlEnumItem;
typedef struct { gchar *name; guint count; SdlEnumItem *items; } SdlEnum;
typedef struct { gchar *root; guint message_count; SdlMessage *messages;
   guint enum_count; SdlEnum *enums; } SdlSchema;
typedef struct { tvbuff_t *tvb; guint offset, end; } Reader;

static gboolean take(Reader *r, guint n, guint *at) {
   if (r->offset > r->end || n > r->end - r->offset) return FALSE;
   *at = r->offset; r->offset += n; return TRUE;
}
static gboolean ru8(Reader *r, guint8 *v) {
   guint p; if (!take(r, 1, &p)) return FALSE; *v = tvb_get_guint8(r->tvb,p); return TRUE;
}
static gboolean ru16be(Reader *r, guint16 *v) {
   guint p; if (!take(r,2,&p)) return FALSE; *v=tvb_get_ntohs(r->tvb,p); return TRUE;
}
static gboolean ru32be(Reader *r, guint32 *v) {
   guint p; if (!take(r,4,&p)) return FALSE; *v=tvb_get_ntohl(r->tvb,p); return TRUE;
}
static gboolean rstring(Reader *r, gchar **s) {
   guint16 n; guint p; guint i;
   if (!ru16be(r,&n) || !take(r,n,&p)) return FALSE;
   for(i=0;i<n;i++) if(tvb_get_guint8(r->tvb,p+i)==0) return FALSE;
   *s = (gchar *)tvb_get_string_enc(wmem_packet_scope(),r->tvb,p,n,ENC_UTF_8|ENC_NA);
   return *s != NULL;
}
static guint32 wire_u32(tvbuff_t *tvb, guint p) {
   return pref_little_endian ? tvb_get_letohl(tvb,p) : tvb_get_ntohl(tvb,p);
}
static guint32 fnv1a(tvbuff_t *tvb, guint p, guint n) {
   guint32 h=2166136261U; guint i;
   for(i=0;i<n;i++) h=(h ^ tvb_get_guint8(tvb,p+i))*16777619U;
   return h;
}
static void schema_free(SdlSchema *s) {
   /* All schema storage belongs to Wireshark's packet scope. */
   memset(s,0,sizeof(*s));
}
static gboolean parse_schema(tvbuff_t *tvb,guint offset,guint size,SdlSchema *s) {
   Reader r={tvb,offset,offset+size}; guint16 n; guint8 d; guint32 v; guint i,j,k;
   memset(s,0,sizeof(*s));
   if(size<4 || tvb_memeql(tvb,offset,(const guint8 *)"SDD1",4)!=0) return FALSE;
   r.offset+=4;
   if(!rstring(&r,&s->root)||!ru16be(&r,&n)||n>SDL_MAX_MESSAGES) goto bad;
   s->message_count=n;s->messages=wmem_alloc0(wmem_packet_scope(),n*sizeof(*s->messages));
   for(i=0;i<n;i++) { SdlMessage *m=&s->messages[i];
      guint16 fields;
      if(!rstring(&r,&m->name)||!ru16be(&r,&fields)) goto bad;
      m->count=fields;m->fields=wmem_alloc0(wmem_packet_scope(),fields*sizeof(*m->fields));
      for(j=0;j<fields;j++) { SdlField *f=&m->fields[j];
         if(!ru32be(&r,&f->id)||!rstring(&r,&f->name)||!ru8(&r,&f->modifier)||f->modifier>3||
            !rstring(&r,&f->type)||!ru8(&r,&d)) goto bad;
         f->dimensions=d;if(d) f->shape=wmem_alloc(wmem_packet_scope(),d*sizeof(*f->shape));
         for(k=0;k<d;k++) if(!ru32be(&r,&f->shape[k])) goto bad;
      }
   }
   if(!ru16be(&r,&n)||n>SDL_MAX_MESSAGES) goto bad;
   s->enum_count=n;s->enums=wmem_alloc0(wmem_packet_scope(),n*sizeof(*s->enums));
   for(i=0;i<n;i++) { SdlEnum *e=&s->enums[i];
      guint16 items;
      if(!rstring(&r,&e->name)||!ru16be(&r,&items)) goto bad;
      e->count=items;e->items=wmem_alloc0(wmem_packet_scope(),items*sizeof(*e->items));
      for(j=0;j<items;j++) { if(!rstring(&r,&e->items[j].name)||!ru32be(&r,&v)) goto bad;
         e->items[j].value=(gint32)v; }
   }
   if(r.offset!=r.end) goto bad;
   return TRUE;
bad: schema_free(s); return FALSE;
}
static SdlMessage *find_message(SdlSchema *s,const gchar *name) {
   guint i;for(i=0;i<s->message_count;i++) if(strcmp(s->messages[i].name,name)==0)return &s->messages[i];return NULL;
}
static SdlField *find_field(SdlMessage *m,guint32 id) {
   guint i;for(i=0;i<m->count;i++)if(m->fields[i].id==id)return &m->fields[i];return NULL;
}
static SdlEnum *find_enum(SdlSchema *s,const gchar *name) {
   guint i;for(i=0;i<s->enum_count;i++)if(strcmp(s->enums[i].name,name)==0)return &s->enums[i];return NULL;
}
static guint primitive_size(const gchar *t) {
   if(!strcmp(t,"bool")||!strcmp(t,"int8"))return 1;
   if(!strcmp(t,"int16"))return 2;
   if(!strcmp(t,"int32")||!strcmp(t,"fl32"))return 4;
   if(!strcmp(t,"int64")||!strcmp(t,"fl64"))return 8;
   if(!strcmp(t,"c32"))return 8;
   if(!strcmp(t,"c64"))return 16;
   return 0;
}
static gboolean known_type(SdlSchema *s,const gchar *name) {
   static const gchar *const builtins[]={"bool","int8","int16","int32","int64","fl32","fl64","c32","c64","string"};
   guint i;for(i=0;i<array_length(builtins);i++)if(!strcmp(name,builtins[i]))return TRUE;
   return find_enum(s,name)!=NULL||find_message(s,name)!=NULL;
}
static gboolean fixed_size(const gchar *t,SdlSchema *s,guint depth,guint *size) {
   guint z=primitive_size(t),i,j,count;SdlMessage *m;SdlEnum *e;
   if(z){*size=z;return TRUE;}if((e=find_enum(s,t))!=NULL){(void)e;*size=4;return TRUE;}
   if(depth>SDL_MAX_DEPTH||(m=find_message(s,t))==NULL)return FALSE;
   if(!m->count)return FALSE;
   z=0;for(i=0;i<m->count;i++){SdlField *f=&m->fields[i];guint item;
      if(f->modifier!=0||!strcmp(f->type,"string")||!fixed_size(f->type,s,depth+1,&item))return FALSE;
      count=1;for(j=0;j<f->dimensions;j++){if(!f->shape[j]||count>G_MAXUINT/f->shape[j])return FALSE;count*=f->shape[j];}
      if(item&&count>(G_MAXUINT-z)/item)return FALSE;
      z+=item*count;}
   *size=z;return TRUE;
}
static gboolean validate_message(SdlSchema *s,SdlMessage *m,guint depth) {
   guint i,j; if(depth>SDL_MAX_DEPTH)return FALSE;
   for(i=0;i<m->count;i++){SdlField *f=&m->fields[i];
      if(!known_type(s,f->type))return FALSE;
      if(f->dimensions||f->modifier==3){guint z;if(!fixed_size(f->type,s,depth+1,&z)||!z)return FALSE;}
      if(find_message(s,f->type)&&!validate_message(s,find_message(s,f->type),depth+1))return FALSE;
      for(j=0;j<i;j++)if(m->fields[j].id==f->id)return FALSE;
   }
   return TRUE;
}
static gboolean validate_schema(SdlSchema *s) {
   guint i,j;SdlMessage *root=find_message(s,s->root);
   if(!root)return FALSE;
   for(i=0;i<s->message_count;i++){
      if(!strcmp(s->messages[i].name,"bool")||primitive_size(s->messages[i].name)||!strcmp(s->messages[i].name,"string"))return FALSE;
      for(j=0;j<i;j++)if(!strcmp(s->messages[j].name,s->messages[i].name))return FALSE;
   }
   for(i=0;i<s->enum_count;i++){
      if(!s->enums[i].count||!strcmp(s->enums[i].name,"bool")||primitive_size(s->enums[i].name)||!strcmp(s->enums[i].name,"string"))return FALSE;
      for(j=0;j<i;j++)if(!strcmp(s->enums[j].name,s->enums[i].name))return FALSE;
      for(j=0;j<s->message_count;j++)if(!strcmp(s->enums[i].name,s->messages[j].name))return FALSE;
   }
   for(i=0;i<s->message_count;i++)if(!validate_message(s,&s->messages[i],0))return FALSE;
   return TRUE;
}
static gint64 signed_value(guint64 v,guint bits) {
   if(bits==64)return (gint64)v;
   if(v & (((guint64)1)<<(bits-1))) return (gint64)(v-(((guint64)1)<<bits));
   return (gint64)v;
}
static gchar *render_scalar(tvbuff_t *tvb,guint p,guint n,const gchar *t,SdlSchema *s) {
   guint z=primitive_size(t);guint64 x;SdlEnum *e;guint i;
   if(!strcmp(t,"string")) return (gchar *)tvb_get_string_enc(wmem_packet_scope(),tvb,p,n,ENC_UTF_8|ENC_NA);
   if((e=find_enum(s,t))!=NULL) {
      if(n!=4)return NULL;
      x=wire_u32(tvb,p);
      for(i=0;i<e->count;i++)if((guint32)e->items[i].value==(guint32)x)
         return wmem_strdup_printf(wmem_packet_scope(),"%s (%" G_GUINT32_FORMAT ")",e->items[i].name,(guint32)x);
      return wmem_strdup_printf(wmem_packet_scope(),"%" G_GUINT32_FORMAT,(guint32)x);
   }
   if(!z||n!=z)return NULL;
   if(!strcmp(t,"bool")){guint8 b=tvb_get_guint8(tvb,p);if(b>1)return NULL;return wmem_strdup(wmem_packet_scope(),b?"true":"false");}
   if(!strcmp(t,"fl32"))return wmem_strdup_printf(wmem_packet_scope(),"%.9g",pref_little_endian?tvb_get_letohieee_float(tvb,p):tvb_get_ntohieee_float(tvb,p));
   if(!strcmp(t,"fl64"))return wmem_strdup_printf(wmem_packet_scope(),"%.17g",pref_little_endian?tvb_get_letohieee_double(tvb,p):tvb_get_ntohieee_double(tvb,p));
   if(!strcmp(t,"c32")||!strcmp(t,"c64")){guint w=!strcmp(t,"c32")?4:8;
      double a=w==4?(pref_little_endian?tvb_get_letohieee_float(tvb,p):tvb_get_ntohieee_float(tvb,p)):(pref_little_endian?tvb_get_letohieee_double(tvb,p):tvb_get_ntohieee_double(tvb,p));
      double b=w==4?(pref_little_endian?tvb_get_letohieee_float(tvb,p+w):tvb_get_ntohieee_float(tvb,p+w)):(pref_little_endian?tvb_get_letohieee_double(tvb,p+w):tvb_get_ntohieee_double(tvb,p+w));
      return wmem_strdup_printf(wmem_packet_scope(),"(%.17g, %.17g)",a,b); }
   if(n==8){guint hi,lo;if(pref_little_endian){lo=tvb_get_letohl(tvb,p);hi=tvb_get_letohl(tvb,p+4);}else{hi=tvb_get_ntohl(tvb,p);lo=tvb_get_ntohl(tvb,p+4);}x=((guint64)hi<<32)|lo;}
   else x=n==1?tvb_get_guint8(tvb,p):n==2?(pref_little_endian?tvb_get_letohs(tvb,p):tvb_get_ntohs(tvb,p)):wire_u32(tvb,p);
   if(!strncmp(t,"int",3))return wmem_strdup_printf(wmem_packet_scope(),"%" G_GINT64_FORMAT,signed_value(x,n*8));
   return wmem_strdup_printf(wmem_packet_scope(),"%" G_GUINT64_FORMAT,x);
}
static void add_value_item(proto_tree *tree,tvbuff_t *tvb,guint p,guint n,
   const gchar *label,const gchar *value) {
   proto_tree_add_string_format(tree,hf_sdl_value,tvb,p,n,value,
      "%s: %s",label,value);
}
static gboolean decode_fixed_message(tvbuff_t *,proto_tree *,guint,guint,SdlMessage *,SdlSchema *,guint);
static gboolean decode_fixed_message(tvbuff_t *tvb,proto_tree *tree,guint p,guint n,SdlMessage *m,SdlSchema *s,guint depth) {
   guint i,j,offset=p,total; if(depth>SDL_MAX_DEPTH||!fixed_size(m->name,s,depth,&total)||total!=n)return FALSE;
   for(i=0;i<m->count;i++){SdlField *f=&m->fields[i];guint item,count=1,field_size;SdlMessage *nested=find_message(s,f->type);
      proto_item *it;proto_tree *sub;
      if(!fixed_size(f->type,s,depth+1,&item))return FALSE;
      for(j=0;j<f->dimensions;j++){if(!f->shape[j]||count>G_MAXUINT/f->shape[j])return FALSE;count*=f->shape[j];}
      if(item&&count>G_MAXUINT/item)return FALSE;
      field_size=item*count;
      if(nested||count>1){
         it=proto_tree_add_none_format(tree,hf_sdl_payload,tvb,offset,field_size,"%s (%s)",f->name,f->type);
         sub=proto_item_add_subtree(it,ett_sdl_field);
         for(j=0;j<count;j++){
            guint value_offset=offset+j*item;
            if(nested){if(!decode_fixed_message(tvb,sub,value_offset,item,nested,s,depth+1))return FALSE;}
            else {gchar *value=render_scalar(tvb,value_offset,item,f->type,s);gchar *label;
               if(!value)return FALSE;
               label=wmem_strdup_printf(wmem_packet_scope(),"%s[%u] (%s)",f->name,j,f->type);
               add_value_item(sub,tvb,value_offset,item,label,value);}
         }
      } else {
         gchar *value=render_scalar(tvb,offset,item,f->type,s);gchar *label;
         if(!value)return FALSE;
         label=wmem_strdup_printf(wmem_packet_scope(),"%s (%s)",f->name,f->type);
         add_value_item(tree,tvb,offset,item,label,value);
      }
      offset+=field_size;
   }
   return offset==p+n;
}
static gboolean decode_body(tvbuff_t *,proto_tree *,guint,guint,SdlMessage *,SdlSchema *,guint);
static gboolean add_payload(tvbuff_t *tvb,proto_tree *tree,guint p,guint n,SdlField *f,SdlSchema *s,guint depth) {
   guint i,count=1,item=0,expected=1,pos;proto_item *it;proto_tree *sub;SdlMessage *m;
   if(depth>SDL_MAX_DEPTH)return FALSE;
   m=find_message(s,f->type);
   if(!f->dimensions&&f->modifier!=3&&!m){gchar *value=render_scalar(tvb,p,n,f->type,s);gchar *label;
      if(!value)return FALSE;
      label=wmem_strdup_printf(wmem_packet_scope(),"%s (%s)",f->name,f->type);
      add_value_item(tree,tvb,p,n,label,value);return TRUE;}
   it=proto_tree_add_none_format(tree,hf_sdl_payload,tvb,p,n,"%s (%s)",f->name,f->type);
   sub=proto_item_add_subtree(it,ett_sdl_field);
   if(f->dimensions||f->modifier==3){if(!fixed_size(f->type,s,depth+1,&item)||!item||n%item)return FALSE;
      count=n/item;for(i=0;i<f->dimensions;i++){if(f->shape[i]&&expected>G_MAXUINT/f->shape[i])return FALSE;expected*=f->shape[i];}
      if(f->dimensions&&count!=expected)return FALSE;
      for(i=0;i<count;i++){pos=p+i*item;if(m){if(!decode_fixed_message(tvb,sub,pos,item,m,s,depth+1))return FALSE;}
         else {gchar *v=render_scalar(tvb,pos,item,f->type,s);gchar *label;if(!v)return FALSE;
            label=wmem_strdup_printf(wmem_packet_scope(),"%s[%u] (%s)",f->name,i,f->type);
            add_value_item(sub,tvb,pos,item,label,v);}}
      return TRUE;}
   if(m)return decode_body(tvb,sub,p,n,m,s,depth+1);
   return FALSE;
}
static gboolean decode_body(tvbuff_t *tvb,proto_tree *tree,guint p,guint n,SdlMessage *m,SdlSchema *s,guint depth) {
   guint end=p+n;while(p<end){guint32 id,len;SdlField *f;
      if(end-p<8)return FALSE;
      id=wire_u32(tvb,p);len=wire_u32(tvb,p+4);p+=8;
      if(len>end-p)return FALSE;
      f=find_field(m,id);if(f){if(!add_payload(tvb,tree,p,len,f,s,depth))return FALSE;}
      else proto_tree_add_uint_format(tree,hf_sdl_field_id,tvb,p,0,id,"Field ID: %u (unknown; skipped %u bytes)",id,len);
      p+=len;}
   return TRUE;
}
static int dissect_sdl(tvbuff_t *tvb,packet_info *pinfo,proto_tree *tree,void *data _U_) {
   guint len=tvb_captured_length(tvb),desc_len,pos;guint32 expected;SdlSchema schema;SdlMessage *root;
   proto_item *ti;proto_tree *st;gboolean ok=FALSE;
   col_set_str(pinfo->cinfo,COL_PROTOCOL,"SDL");col_clear(pinfo->cinfo,COL_INFO);
   ti=proto_tree_add_item(tree,proto_sdl,tvb,0,-1,ENC_NA);st=proto_item_add_subtree(ti,ett_sdl);
   if(len>=4){desc_len=wire_u32(tvb,0);if(desc_len<=len-8&&parse_schema(tvb,4,desc_len,&schema)&&validate_schema(&schema)){
      pos=4+desc_len;expected=wire_u32(tvb,pos);
      if(expected==fnv1a(tvb,4,desc_len)&&(root=find_message(&schema,schema.root))!=NULL){
         proto_tree_add_string(st,hf_sdl_type,tvb,4,desc_len,schema.root);
         proto_tree_add_uint(st,hf_sdl_hash,tvb,pos,4,expected);
         ok=decode_body(tvb,st,pos+4,len-pos-4,root,&schema,0);
         if(ok)col_set_str(pinfo->cinfo,COL_INFO,schema.root);
      }
      schema_free(&schema);
   }}
   if(!ok)expert_add_info_format(pinfo,ti,&ei_sdl_malformed,"Malformed SDL frame (truncated data, invalid descriptor, hash, or body)");
   return len;
}
static gboolean dissect_sdl_heur(tvbuff_t *tvb,packet_info *pinfo,proto_tree *tree,void *data) {
   guint n=tvb_captured_length(tvb),d;SdlSchema s;gboolean match=FALSE;
   if(n>=8){d=wire_u32(tvb,0);if(d<=n-8&&parse_schema(tvb,4,d,&s)&&validate_schema(&s)){match=TRUE;schema_free(&s);}}
   if(match){dissect_sdl(tvb,pinfo,tree,data);return TRUE;}return FALSE;
}
static void proto_register_sdl(void) {
   static hf_register_info hf[]={
      {&hf_sdl_type,{"Root message","sdl.type",FT_STRING,BASE_NONE,NULL,0,NULL,HFILL}},
      {&hf_sdl_hash,{"Schema hash","sdl.schema_hash",FT_UINT32,BASE_HEX,NULL,0,NULL,HFILL}},
      {&hf_sdl_field_id,{"Field ID","sdl.field_id",FT_UINT32,BASE_DEC,NULL,0,NULL,HFILL}},
      {&hf_sdl_payload,{"Field payload","sdl.payload",FT_NONE,BASE_NONE,NULL,0,NULL,HFILL}},
      {&hf_sdl_value,{"Value","sdl.value",FT_STRING,BASE_NONE,NULL,0,NULL,HFILL}}
   };
   static gint *ett[]={&ett_sdl,&ett_sdl_field};
   static ei_register_info ei[]={{&ei_sdl_malformed,{"sdl.malformed",PI_MALFORMED,PI_ERROR,"Malformed SDL frame",0,NULL,0,{0}}}};
   expert_module_t *em;
   module_t *pref;
   proto_sdl=proto_register_protocol("SDL Wire (C)","SDL","sdl");
   proto_register_field_array(proto_sdl,hf,array_length(hf));proto_register_subtree_array(ett,array_length(ett));
   em=expert_register_protocol(proto_sdl);expert_register_field_array(em,ei,array_length(ei));
   pref=prefs_register_protocol(proto_sdl,NULL);prefs_register_bool_preference(pref,"wire_endian","Little endian","Use little-endian SDL wire values",&pref_little_endian);
}
static void proto_reg_handoff_sdl(void) { dissector_handle_t h=create_dissector_handle(dissect_sdl,proto_sdl);
   dissector_add_for_decode_as("udp.port",h);heur_dissector_add("udp",dissect_sdl_heur,"SDL Wire heuristic", "sdl_udp",proto_sdl,HEURISTIC_ENABLE); }

G_MODULE_EXPORT void plugin_register(void) {
   static proto_plugin plug;
   plug.register_protoinfo=proto_register_sdl;
   plug.register_handoff=proto_reg_handoff_sdl;
   proto_register_plugin(&plug);
}
