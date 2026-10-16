/* Internal SDL2 wire representation conversion; included by sdl_dynamic.c. */
#define REPRESENTATION_LIMIT (64U * 1024U * 1024U)
typedef struct { const uint8_t *data; size_t size, bit; Writer *out; } RepStream;
static bool rep_bits(RepStream *s, unsigned width, uint64_t *v) {
   unsigned i;
   if(s->bit>REPRESENTATION_LIMIT*8ULL || width>REPRESENTATION_LIMIT*8ULL-s->bit) return false;
   if(!s->out) {
      if(s->bit/8>s->size || width>s->size*8-s->bit) return false;
      *v=0;
      for(i=0;i<width;++i) { *v=(*v<<1)|((s->data[s->bit/8]>>(7-s->bit%8))&1); ++s->bit; }
   } else for(i=width;i>0;--i) {
      if(!(s->bit%8)) { uint8_t zero=0; if(!write_bytes(s->out,&zero,1)) return false; }
      if(s->out->data) s->out->data[s->bit/8]|=(uint8_t)(((*v>>(i-1))&1)<<(7-s->bit%8));
      ++s->bit;
   }
   return true;
}
static bool rep_zeros(RepStream *s, size_t n) {
   uint64_t v;
   while(n) { unsigned k=n>64?64:(unsigned)n; v=0; if(!rep_bits(s,k,&v)||v) return false; n-=k; }
   return true;
}
static bool rep_align(RepStream *s) { return rep_zeros(s,(8-s->bit%8)%8); }
static bool rep_copy(RepStream *source, RepStream *dest, size_t n) {
   if(source->bit%8||dest->bit%8||source->bit/8>source->size||n>source->size-source->bit/8||
      n>REPRESENTATION_LIMIT||dest->bit/8>REPRESENTATION_LIMIT-n) return false;
   if(!write_bytes(dest->out,source->data+source->bit/8,n)) return false;
   source->bit+=n*8; dest->bit+=n*8; return true;
}
static bool rep_varint(RepStream *s, uint64_t *value, unsigned width) {
   unsigned i; uint64_t v=*value;
   if(!s->out) {
      *value=0;
      for(i=0;i<(width+6)/7;++i) {
         uint64_t byte=0;
         if(!rep_bits(s,8,&byte)||(i==9&&byte>1)) return false;
         *value|=(byte&127)<<(7*i);
         if(width<64&&(*value>>width)) return false;
         if(byte<128) return !i||byte!=0;
      }
      return false;
   }
   do { uint64_t byte=(v&127)|(v>=128?128:0); if(!rep_bits(s,8,&byte))return false; v>>=7; } while(v);
   return true;
}
static bool rep_count(RepStream *plain, RepStream *custom, bool encode, uint64_t *n) {
   return rep_varint(encode?plain:custom,n,32)&&rep_varint(encode?custom:plain,n,32);
}
static bool rep_message(const SdlDynamicSchema *, const SdlDynamicMessageDesc *, RepStream *,RepStream *,bool,size_t);
static bool rep_quantized_float(const SdlDynamicFieldDesc *f, size_t size,
                                 RepStream *plain, RepStream *custom, bool encode) {
   uint64_t raw = 0;
   int64_t code;
   double x, scaled, rounded, fraction, limit = ldexp(1.0, (int)f->wire_bits - 1);
   if (encode) {
      if (!rep_bits(plain, (unsigned)size * 8, &raw)) return false;
      if (size == 4) {
         uint32_t bits = (uint32_t)raw; float value;
         memcpy(&value, &bits, 4); x = value;
      } else memcpy(&x, &raw, 8);
      scaled = x / f->wire_quantum;
      if (!isfinite(scaled)) return false;
      rounded = floor(scaled); fraction = scaled - rounded;
      if (fraction > 0.5 || (fraction == 0.5 && fmod(rounded, 2.0) != 0)) rounded += 1;
      if (rounded < -limit || rounded >= limit) return false;
      raw = (uint64_t)(int64_t)rounded;
      return rep_bits(custom, f->wire_bits, &raw);
   }
   if (!rep_bits(custom, f->wire_bits, &raw)) return false;
   if (f->wire_bits < 64 && (raw & (UINT64_C(1) << (f->wire_bits - 1))))
      raw |= UINT64_MAX << f->wire_bits;
   memcpy(&code, &raw, 8);
   x = (double)code * f->wire_quantum;
   if (size == 4) {
      float value = (float)x; uint32_t bits;
      memcpy(&bits, &value, 4); raw = bits;
   } else memcpy(&raw, &x, 8);
   return rep_bits(plain, (unsigned)size * 8, &raw);
}
static bool rep_value(const SdlDynamicSchema *schema, const SdlDynamicFieldDesc *f, size_t dim,
                      RepStream *plain, RepStream *custom, bool encode, size_t depth) {
   size_t i, size; uint64_t n=0; unsigned width,bits; bool sign;
   const SdlDynamicMessageDesc *child;
   if(dim<f->dimension_count) {
      if(f->dimensions[dim]>1048576) return false;
      for(i=0;i<f->dimensions[dim];++i) if(!rep_value(schema,f,dim+1,plain,custom,encode,depth))return false;
      return true;
   }
   child=schema_message_const(schema,f->type_name);
   if(child) return rep_message(schema,child,plain,custom,encode,depth+1);
   if(!strcmp(f->type_name,"string")) {
      if(!rep_count(plain,custom,encode,&n)) return false;
      return rep_copy(encode?plain:custom,encode?custom:plain,(size_t)n);
   }
   size=primitive_size(f->type_name); if(!size)size=4;
   if (f->wire_flags & 16) return rep_quantized_float(f, size, plain, custom, encode);
   if(!(f->wire_flags&3)) return rep_copy(encode?plain:custom,encode?custom:plain,size);
   width=(unsigned)size*8;bits=f->wire_flags&1?f->wire_bits:width;
   sign=!strncmp(f->type_name,"int",3)||schema_enum(schema,f->type_name)!=NULL;
   if(encode) {
      if(!rep_bits(plain,width,&n))return false;
      if(sign&&width<64&&(n&(UINT64_C(1)<<(width-1)))) n|=UINT64_MAX<<width;
      if(f->wire_flags&1) {
         uint64_t mask=bits==64?UINT64_MAX:(UINT64_C(1)<<bits)-1;
         uint64_t narrowed=n&mask, extended=narrowed;
         if(sign&&bits<64&&(narrowed&(UINT64_C(1)<<(bits-1)))) extended|=~mask;
         if((sign?extended:narrowed)!=n)return false;
         return rep_bits(custom,bits,&n);
      }
      if(f->wire_encoding==2) n=(n<<1)^(UINT64_C(0)-(n>>63));
      else if(sign&&(n>>63))return false;
      return rep_varint(custom,&n,64);
   }
   if(f->wire_flags&1) {
      if(!rep_bits(custom,bits,&n))return false;
      if(sign&&bits<64&&(n&(UINT64_C(1)<<(bits-1))))n|=UINT64_MAX<<bits;
   } else {
      if(!rep_varint(custom,&n,width))return false;
      if(f->wire_encoding==2)n=(n>>1)^(UINT64_C(0)-(n&1));
      else if(sign&&(n>>(width-1)))return false;
   }
   return rep_bits(plain,width,&n);
}
static bool rep_message(const SdlDynamicSchema *schema,const SdlDynamicMessageDesc *m,
                        RepStream *plain,RepStream *custom,bool encode,size_t depth) {
   size_t i,j,base=custom->bit;
   if(depth>64)return false;
   for(i=0;i<m->field_count;++i) {
      const SdlDynamicFieldDesc *f=&m->fields[i]; uint64_t count=1;
      if(!(f->wire_flags&1)||f->modifier||(f->wire_flags&4)) if(!rep_align(custom))return false;
      if(f->wire_flags&4) {
         size_t offset=(custom->bit-base)/8;
         if(!rep_zeros(custom,((f->wire_align-offset%f->wire_align)%f->wire_align)*8))return false;
      }
      if(f->modifier&&!rep_count(plain,custom,encode,&count))return false;
      if(count>1048576||(f->modifier==1&&count>1))return false;
      for(j=0;j<count;++j)if(!rep_value(schema,f,0,plain,custom,encode,depth))return false;
      if(f->wire_pad&&(!rep_align(custom)||!rep_zeros(custom,(size_t)f->wire_pad*8)))return false;
   }
   return rep_align(custom);
}
static bool rep_pass(const SdlContext *c,const void *data,size_t size,bool encode,Writer *out) {
   RepStream source={(const uint8_t *)data,size,0,NULL},dest={NULL,0,0,out};
   RepStream *plain=encode?&source:&dest,*custom=encode?&dest:&source; uint64_t id=0;
   if(!data||size>REPRESENTATION_LIMIT||!rep_count(plain,custom,encode,&id)||!id||id>c->schema.message_count)return false;
   return rep_message(&c->schema,&c->schema.messages[id-1],plain,custom,encode,0)&&source.bit==size*8;
}
static void *rep_transform(const SdlContext *c,const void *data,size_t size,bool encode,size_t *n) {
   Writer out={NULL,0,0};
   if(!c||!rep_pass(c,data,size,encode,&out))return NULL;
   out.size=out.offset;out.offset=0;out.data=malloc(out.size?out.size:1);
   if(!out.data||!rep_pass(c,data,size,encode,&out)){free(out.data);return NULL;}
   *n=out.offset;return out.data;
}
void *type_wire_transform(const SdlContext *c,const void *data,size_t size,bool encode,size_t *n) {
   if(!c||!n||!data||!size)return NULL;
   if(c->has_representation)return rep_transform(c,data,size,encode,n);
   { void *out=malloc(size?size:1);if(!out)return NULL;memcpy(out,data,size);*n=size;return out; }
}
size_t type_encode_size(const SdlContext *c,const char *name,const void *value) {
   size_t n=0;void *data;
   if(!c||!c->has_representation)return type_encode_size_plain(c,name,value);
   data=type_encode(c,name,value,&n);free(data);return n;
}
void *type_encode(const SdlContext *c,const char *name,const void *value,size_t *size) {
   void *plain,*out;size_t n;
   if(!c||!c->has_representation)return type_encode_plain(c,name,value,size);
   if(!size)return NULL;
   *size=0;
   plain=type_encode_plain(c,name,value,&n);if(!plain)return NULL;
   out=rep_transform(c,plain,n,true,size);free(plain);return out;
}
#define REP_DECODE_WRAPPER(result,name,failure) \
result name(const SdlContext *c,const void *data,size_t size) { \
   void *plain;size_t n;result out; \
   if(!c||!c->has_representation)return name##_plain(c,data,size); \
   plain=rep_transform(c,data,size,false,&n);if(!plain)return failure; \
   out=name##_plain(c,plain,n);free(plain);return out; \
}
REP_DECODE_WRAPPER(size_t,type_decode_size,0)
REP_DECODE_WRAPPER(void *,type_decode,NULL)
REP_DECODE_WRAPPER(SdlDynamicMessage *,type_decode_dynamic,NULL)
REP_DECODE_WRAPPER(bool,type_context_validate,false)
#undef REP_DECODE_WRAPPER
