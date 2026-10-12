#include "sdl_filter_native.h"
#include "type_private.h"
#include <string.h>
#include <stdbool.h>
typedef struct { const SdlTypeDesc *type; const void *value; SdlFilterSpan *span; } Native;
static int span_result(const Native *s,const uint8_t *data,uint32_t kind,size_t width,
    const uint8_t *base,size_t count,size_t stride) {
    if(!s->span)return 0;
    if(!base||!count||kind<2||kind>4||!width||(uintptr_t)data<(uintptr_t)base||
        (uintptr_t)data+width>(uintptr_t)base+stride)return -1;
    *s->span=(SdlFilterSpan){data,count,stride,kind,(uint32_t)width,0,0,0.0};return 0;
}
const SdlTypeDesc *sdl_filter_native_descriptor(const char *name) { return sdl_lookup_type(name); }
static int same_name(const char *name, SdlFilterBytes expected) {
    return name && strlen(name)==expected.size && !memcmp(name,expected.data,expected.size);
}
static int read_native(void *user,const SdlFilterStep *path,size_t length,uint32_t slot,SdlFilterValue *out) {
    (void)slot;
    const Native *source=user;
    const SdlTypeDesc *type=source->type;
    const uint8_t *data=source->value;
    const uint32_t *string_lengths=NULL;
    size_t count=0;
    int sequence=0;
    const uint8_t *span_base=NULL;size_t span_count=0,span_stride=0;
    for(size_t p=0;p<length;++p) {
        const SdlFilterStep *step=&path[p];
        if(sequence) {
            if(step->kind!=1)return -1;
            if(source->span&&step->index==SIZE_MAX){if(span_base||!count)return -1;span_base=data;span_count=count;span_stride=type->size;sequence=0;continue;}
            if(step->index>=count){out->kind=0;return 0;}
            if(string_lengths)string_lengths+=step->index;
            data+=step->index*type->size;sequence=0;
        } else if(type->kind==SDL_TYPE_ARRAY) {
            if(step->kind!=1)return -1;
            if(source->span&&step->index==SIZE_MAX){if(span_base||!type->detail.array.count)return -1;span_base=data;span_count=type->detail.array.count;type=type->detail.array.element;span_stride=type->size;continue;}
            if(step->index>=type->detail.array.count){out->kind=0;return 0;}
            type=type->detail.array.element;data+=step->index*type->size;
        } else if(type->kind==SDL_TYPE_STRUCT) {
            if(step->kind!=0||(p!=0&&!same_name(type->name,step->owner)))return -1;
            const SdlFieldDesc *field=NULL;
            for(size_t i=0;i<type->detail.structure.field_count;++i)if(type->detail.structure.fields[i].id==step->id){field=&type->detail.structure.fields[i];break;}
            if(!field){out->kind=0;return 0;}
            if(span_base&&(field->flags&(SDL_FIELD_OPTIONAL|SDL_FIELD_REPEATED|SDL_FIELD_PACKED)))return -1;
            if((field->flags&SDL_FIELD_OPTIONAL)&&!*(const bool *)(data+field->presence_offset)){out->kind=0;return 0;}
            string_lengths=NULL;
            if(field->string_length_offset!=SDL_NO_OFFSET) {
                if(field->flags&(SDL_FIELD_REPEATED|SDL_FIELD_PACKED))memcpy(&string_lengths,data+field->string_length_offset,sizeof(string_lengths));
                else string_lengths=(const uint32_t *)(data+field->string_length_offset);
            }
            if(field->flags&(SDL_FIELD_REPEATED|SDL_FIELD_PACKED)) {
                uint32_t n;memcpy(&n,data+field->count_offset,sizeof(n));count=n;
                const uint8_t *items;memcpy(&items,data+field->offset,sizeof(items));data=items;sequence=1;
                if(count&&!data)return -1;
            }else data+=field->offset;
            type=field->type;
        } else if(type->kind==SDL_TYPE_COMPLEX32||type->kind==SDL_TYPE_COMPLEX64) {
            if(step->kind!=0||step->id<1||step->id>2||p+1!=length)return -1;
            if(type->kind==SDL_TYPE_COMPLEX32){float parts[2];memcpy(parts,data,sizeof(parts));out->real=parts[step->id-1];}
            else {double parts[2];memcpy(parts,data,sizeof(parts));out->real=parts[step->id-1];}
            out->kind=4;
            size_t width=type->kind==SDL_TYPE_COMPLEX32?sizeof(float):sizeof(double);
            return span_result(source,data+(step->id-1)*width,4,width,span_base,span_count,span_stride);
        }else return -1;
    }
    if(sequence){out->kind=6;out->count=count;return 0;}
    if(type->kind==SDL_TYPE_ARRAY){out->kind=6;out->count=type->detail.array.count;return 0;}
    if(type->kind==SDL_TYPE_STRUCT){out->kind=7;return 0;}
    if(!data)return -1;
#define SIGNED(T) do { T v;memcpy(&v,data,sizeof(v));out->kind=2;out->signed_value=v; } while(0)
#define UNSIGNED(T) do { T v;memcpy(&v,data,sizeof(v));out->kind=3;out->unsigned_value=v; } while(0)
    switch(type->kind) {
    case SDL_TYPE_BOOL:out->kind=1;out->unsigned_value=*(const bool *)data;break;
    case SDL_TYPE_INT8:SIGNED(int8_t);break;
    case SDL_TYPE_INT16:SIGNED(int16_t);break;
    case SDL_TYPE_INT32:case SDL_TYPE_ENUM:SIGNED(int32_t);break;
    case SDL_TYPE_INT64:SIGNED(int64_t);break;
    case SDL_TYPE_UINT8:UNSIGNED(uint8_t);break;
    case SDL_TYPE_UINT16:UNSIGNED(uint16_t);break;
    case SDL_TYPE_UINT32:UNSIGNED(uint32_t);break;
    case SDL_TYPE_UINT64:UNSIGNED(uint64_t);break;
    case SDL_TYPE_FLOAT32:{float v;memcpy(&v,data,sizeof(v));out->kind=4;out->real=v;break;}
    case SDL_TYPE_FLOAT64:out->kind=4;memcpy(&out->real,data,sizeof(double));break;
    case SDL_TYPE_COMPLEX32:{float v[2];memcpy(v,data,sizeof(v));out->kind=8;out->real=v[0];out->imag=v[1];break;}
    case SDL_TYPE_COMPLEX64:{double v[2];memcpy(v,data,sizeof(v));out->kind=8;out->real=v[0];out->imag=v[1];break;}
    case SDL_TYPE_STRING:{const char *s;memcpy(&s,data,sizeof(s));if(!s&&string_lengths&&*string_lengths)return -1;out->kind=5;out->bytes.data=(const uint8_t *)s;out->bytes.size=string_lengths&&*string_lengths?*string_lengths:s?strlen(s):0;break;}
    default:return -1;
    }
    return span_result(source,data,out->kind,type->size,span_base,span_count,span_stride);
}
static int read_span(void *user,const SdlFilterStep *path,size_t length,uint32_t slot,SdlFilterSpan *out){
    Native *original=user;Native source={original->type,original->value,out};SdlFilterValue value={0};
    return read_native(&source,path,length,slot,&value);
}
int sdl_filter_evaluate_native(const SdlFilter *filter,const SdlTypeDesc *type,const void *value) {
    if(!filter||!type||!value||!same_name(type->name,sdl_filter_root(filter))){
        const char *diagnostic="object root does not match filter type, or null input";
        return sdl_filter_failure((SdlFilterBytes){(const uint8_t *)diagnostic,strlen(diagnostic)});
    }
    Native source={type,value,NULL};return sdl_filter_evaluate_object_spans(filter,read_native,read_span,&source);
}
