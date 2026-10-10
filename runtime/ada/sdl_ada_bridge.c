/* Stable opaque handles: Ada does not depend on the C union layout. */
#include "sdl_context.h"
#include "sdl_wire.h"
#include <stdlib.h>
typedef struct { SdlContext *remote,*local;size_t refs;uint8_t *direct;size_t direct_count; } AdaContext;
void *sdl_ada_prepare(const void *remote,size_t rn,const void *local,size_t ln) {
   AdaContext *c=calloc(1,sizeof(*c));if(!c)return NULL;
   c->remote=type_prepare(remote,rn);c->local=type_prepare(local,ln);c->refs=1;
   if(!type_context_compatible(c->remote,c->local)){type_context_free(c->remote);type_context_free(c->local);free(c);return NULL;}
   c->direct=type_context_direct_layouts(c->remote,c->local,&c->direct_count);
   if(!c->direct){type_context_free(c->remote);type_context_free(c->local);free(c);return NULL;}
   return c;
}
void sdl_ada_retain(AdaContext *c){if(c)++c->refs;}
void sdl_ada_release(AdaContext *c){if(c && !--c->refs){type_context_free(c->remote);type_context_free(c->local);free(c->direct);free(c);}}
uint32_t sdl_ada_id(AdaContext *c,const char *name){return c?type_context_message_id(c->remote,name):0;}
int sdl_ada_exact(AdaContext *c,const char *name){return c&&type_context_exact_message(c->remote,c->local,name);}
int sdl_ada_direct(AdaContext *c,const char *name){uint32_t id=sdl_ada_id(c,name);return id&&id<=c->direct_count&&c->direct[id-1];}
int sdl_ada_validate(AdaContext *c,const void *data,size_t size){return c&&type_context_validate(c->remote,data,size);}
void *sdl_ada_decode(AdaContext *c,const void *data,size_t size){return c?type_decode_dynamic(c->remote,data,size):NULL;}
void sdl_ada_free(void *m){type_dynamic_free(m);}
const char *sdl_ada_name(SdlDynamicMessage *m){return m?m->type_name:NULL;}
const void *sdl_ada_field(AdaContext *c,SdlDynamicMessage *m,uint32_t id){return c?type_dynamic_get_id(c->remote,m,id):NULL;}
int sdl_ada_kind(const SdlDynamicValue *v){return v?(int)v->kind:0;}
int64_t sdl_ada_integer(const SdlDynamicValue *v){return v->kind==SDL_DYNAMIC_ENUM?v->value.enumeration.value:v->value.integer;}
double sdl_ada_float(const SdlDynamicValue *v){return v->value.floating;}
double sdl_ada_real(const SdlDynamicValue *v){return v->value.complex_value.real;}
double sdl_ada_imag(const SdlDynamicValue *v){return v->value.complex_value.imag;}
int sdl_ada_bool(const SdlDynamicValue *v){return v->value.boolean;}
const char *sdl_ada_string(const SdlDynamicValue *v){return (const char *)v->value.string.data;}
size_t sdl_ada_string_size(const SdlDynamicValue *v){return v->value.string.size;}
size_t sdl_ada_length(const SdlDynamicValue *v){return v->value.array.count;}
const void *sdl_ada_item(const SdlDynamicValue *v,size_t index){return index<v->value.array.count?v->value.array.items+index:NULL;}
void *sdl_ada_message(const SdlDynamicValue *v){return v->value.message;}
int sdl_ada_utf8(const void *data,size_t size){return sdl_wire_valid_utf8(data,size);}
