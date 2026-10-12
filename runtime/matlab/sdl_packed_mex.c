#define _POSIX_C_SOURCE 200809L
/* Optional MATLAB separate-complex MEX bridge to the SDL C wire runtime. */
#include "mex.h"
#include "sdl_wire.h"
#include <math.h>
#include <stdint.h>
#include <string.h>

/* Outputs are newly allocated mxArrays, distinct from all input planes. */
static void encode_samples(uint8_t *restrict out, const float *restrict re,
                           const float *restrict im, size_t count) {
   size_t i;
   if (im) {
      for (i = 0; i < count; ++i) {
         uint32_t rb, ib; memcpy(&rb, re + i, 4); memcpy(&ib, im + i, 4);
         sdl_wire_write_u32(out + i * 8, rb); sdl_wire_write_u32(out + i * 8 + 4, ib);
      }
   } else {
      for (i = 0; i < count; ++i) {
         uint32_t rb; memcpy(&rb, re + i, 4);
         sdl_wire_write_u32(out + i * 8, rb); sdl_wire_write_u32(out + i * 8 + 4, 0);
      }
   }
}
static void decode_samples(float *restrict re, float *restrict im,
                           const uint8_t *restrict in, size_t count) {
   size_t i;
   for (i = 0; i < count; ++i) {
      uint32_t rb = sdl_wire_read_u32(in + i * 8), ib = sdl_wire_read_u32(in + i * 8 + 4);
      memcpy(re + i, &rb, 4); memcpy(im + i, &ib, 4);
   }
}

static size_t index_arg(const mxArray *value) {
   double n;
   if (!mxIsDouble(value) || mxIsComplex(value) || mxIsSparse(value) ||
       mxGetNumberOfElements(value) != 1)
      mexErrMsgIdAndTxt("SDL:InvalidValue", "Expected a scalar double index.");
   n = mxGetScalar(value);
   if (!mxIsFinite(n) || n < 0 || n != floor(n) || n > 9007199254740991.0 ||
       n > (double)(SIZE_MAX / 8))
      mexErrMsgIdAndTxt("SDL:InvalidValue", "Invalid index or count.");
   return (size_t)n;
}

/* Complete BenchPayload adapter: native contexts and direct mxArray outputs. */
#include "sdl_context.h"
#include "benchmark.h"
#include "bench_cases.h"
#include <stdlib.h>
#include <time.h>

typedef struct NativeContext {
   uint64_t id;
   int records;
   SdlContext *codec;
   struct NativeContext *next;
} NativeContext;
static NativeContext *contexts;
static uint64_t next_id = 1;
static int initialized;
static const uint8_t supported_catalogue[] =
   "SDL2\nmessage BenchPayload {\n"
   "  1: required string header;\n"
   "  2: packed c32 samples;\n}\n";

static const uint8_t records_catalogue[] =
   "SDL2\n"
   "message BenchEntry {\n"
   "  1: optional string label;\n"
   "  2: required int64 number;\n"
   "}\n"
   "message BenchOptionals {\n"
   "  1: optional int32 a;\n"
   "  2: optional int32 b;\n"
   "  3: optional int32 c;\n"
   "  4: optional int32 d;\n"
   "  5: optional int32 e;\n"
   "  6: optional int32 f;\n"
   "  7: optional int32 g;\n"
   "  8: optional int32 h;\n"
   "}\n"
   "message BenchPose {\n"
   "  1: required BenchVector position;\n"
   "  2: required fl32[4] rotation;\n"
   "}\n"
   "message BenchRecord {\n"
   "  1: required int32 id;\n"
   "  2: required BenchPose pose;\n"
   "  3: required fl32[2] measures;\n"
   "}\n"
   "message BenchRecordBatch {\n"
   "  1: packed BenchRecord records;\n"
   "}\n"
   "message BenchSmall {\n"
   "  1: required bool active;\n"
   "  2: required int32 sequence;\n"
   "  3: required int16 code;\n"
   "}\n"
   "message BenchVariable {\n"
   "  1: required string header;\n"
   "  2: repeated BenchEntry entries;\n"
   "}\n"
   "message BenchVector {\n"
   "  1: required fl32[3] values;\n"
   "}\n"
;

static void cleanup_contexts(void) {
   while (contexts) {
      NativeContext *next = contexts->next;
      type_context_free(contexts->codec); free(contexts); contexts = next;
   }
}
static uint64_t context_id(const mxArray *arg) {
   if (!mxIsUint64(arg) || mxIsComplex(arg) || mxIsSparse(arg) || mxGetNumberOfElements(arg) != 1)
      mexErrMsgIdAndTxt("SDL:InvalidContext", "Expected a scalar uint64 context token.");
   return *(const uint64_t *)mxGetData(arg);
}
static NativeContext *find_context(const mxArray *arg) {
   uint64_t id = context_id(arg);
   NativeContext *ctx;
   for (ctx = contexts; ctx; ctx = ctx->next) if (ctx->id == id) return ctx;
   mexErrMsgIdAndTxt("SDL:InvalidContext", "Unknown or released native context.");
   return NULL;
}
static mxArray *prepare_context(const mxArray *arg) {
   mxArray *out;
   uint64_t id = 0;
   size_t length;
   int records;
   if (!mxIsUint8(arg) || mxIsComplex(arg) || mxIsSparse(arg))
      mexErrMsgIdAndTxt("SDL:Descriptor", "Expected uint8 catalogue bytes.");
   length = mxGetNumberOfElements(arg);
   out = mxCreateNumericMatrix(1, 1, mxUINT64_CLASS, mxREAL);
   records = length == sizeof(records_catalogue)-1 &&
      length == BENCHRECORDBATCH_SCHEMA_DESCRIPTOR_SIZE &&
      !memcmp(BENCHRECORDBATCH_SCHEMA_DESCRIPTOR, records_catalogue, length) &&
      !memcmp(mxGetData(arg), records_catalogue, length);
   /* Match both the compiled descriptor and the adapter layout. */
   if (records || (length == sizeof(supported_catalogue) - 1 &&
       length == BENCHPAYLOAD_SCHEMA_DESCRIPTOR_SIZE &&
       !memcmp(BENCHPAYLOAD_SCHEMA_DESCRIPTOR, supported_catalogue, length) &&
       !memcmp(mxGetData(arg), supported_catalogue, length))) {
      NativeContext *ctx;
      if (!initialized) {
         struct timespec stamp;
         if (clock_gettime(CLOCK_MONOTONIC, &stamp))
            mexErrMsgIdAndTxt("SDL:InvalidContext", "Cannot initialize context token epoch.");
         /* Prevent released tokens from matching a new context after MEX reload. */
         next_id = (uint64_t)stamp.tv_sec * UINT64_C(1000000000) + (uint64_t)stamp.tv_nsec;
         register_benchmark_types(); register_bench_cases_types(); mexAtExit(cleanup_contexts); initialized = 1;
      }
      if (next_id == UINT64_MAX)
         mexErrMsgIdAndTxt("SDL:InvalidContext", "Context token space exhausted.");
      ctx = (NativeContext *)calloc(1, sizeof(*ctx));
      if (!ctx) mexErrMsgIdAndTxt("SDL:Memory", "Cannot allocate native context.");
      ctx->codec = type_prepare(mxGetData(arg), length);
      if (!ctx->codec) { free(ctx); mexErrMsgIdAndTxt("SDL:Descriptor", "Cannot prepare C context."); }
      ctx->records = records;
      id = ctx->id = next_id++;
      ctx->next = contexts; contexts = ctx; mexLock();
   }
   *(uint64_t *)mxGetData(out) = id;
   return out;
}
static mxArray *release_context(const mxArray *arg) {
   uint64_t id = context_id(arg);
   NativeContext **link = &contexts;
   int released = 0;
   while (*link) {
      NativeContext *ctx = *link;
      if (ctx->id == id) {
         *link = ctx->next; type_context_free(ctx->codec); free(ctx); mexUnlock(); released = 1; break;
      }
      link = &ctx->next;
   }
   return mxCreateLogicalScalar(released != 0);
}
static size_t count_size(uint32_t n) {
   size_t size = 1;
   while (n >= 128) { n >>= 7; ++size; }
   return size;
}
static size_t put_count(uint8_t *out, uint32_t n) {
   size_t i = 0;
   do { uint8_t b = (uint8_t)(n & 127); n >>= 7; out[i++] = b | (n ? 128 : 0); } while (n);
   return i;
}
static uint32_t take_count(const uint8_t *bytes, size_t length, size_t *pos) {
   uint32_t n = 0;
   size_t i;
   for (i = 0; i < 5; ++i) {
      uint8_t b;
      if (*pos >= length) mexErrMsgIdAndTxt("SDL:Malformed", "Truncated counter.");
      b = bytes[(*pos)++];
      if (i == 4 && b > 15) mexErrMsgIdAndTxt("SDL:Malformed", "Counter overflow.");
      n |= (uint32_t)(b & 127) << (7 * i);
      if (!(b & 128)) {
         if (i && !b) mexErrMsgIdAndTxt("SDL:Malformed", "Noncanonical counter.");
         return n;
      }
   }
   mexErrMsgIdAndTxt("SDL:Malformed", "Invalid counter."); return 0;
}
static uint32_t next_utf16(const mxChar *text, size_t length, size_t *pos) {
   uint32_t c = text[(*pos)++];
   if (c >= 0xd800 && c <= 0xdbff) {
      uint32_t low;
      if (*pos == length) mexErrMsgIdAndTxt("SDL:InvalidValue", "Unpaired Unicode surrogate.");
      low = text[(*pos)++];
      if (low < 0xdc00 || low > 0xdfff) mexErrMsgIdAndTxt("SDL:InvalidValue", "Unpaired Unicode surrogate.");
      c = 0x10000 + ((c - 0xd800) << 10) + low - 0xdc00;
   } else if (c >= 0xdc00 && c <= 0xdfff) {
      mexErrMsgIdAndTxt("SDL:InvalidValue", "Unpaired Unicode surrogate.");
   }
   return c;
}
static size_t utf8_size(const mxArray *header) {
   const mxChar *text = mxGetChars(header);
   size_t length = mxGetNumberOfElements(header), pos = 0, bytes = 0;
   while (pos < length) {
      uint32_t c = next_utf16(text, length, &pos);
      size_t unit = c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
      if (bytes > UINT32_MAX - unit) mexErrMsgIdAndTxt("SDL:InvalidValue", "String length overflow.");
      bytes += unit;
   }
   return bytes;
}
static void encode_utf8(uint8_t *out, const mxArray *header) {
   const mxChar *text = mxGetChars(header);
   size_t length = mxGetNumberOfElements(header), pos = 0, written = 0;
   while (pos < length) {
      uint32_t c = next_utf16(text, length, &pos);
      if (c < 0x80) out[written++] = (uint8_t)c;
      else if (c < 0x800) {
         out[written++] = (uint8_t)(0xc0 | (c >> 6)); out[written++] = (uint8_t)(0x80 | (c & 63));
      } else if (c < 0x10000) {
         out[written++] = (uint8_t)(0xe0 | (c >> 12)); out[written++] = (uint8_t)(0x80 | ((c >> 6) & 63)); out[written++] = (uint8_t)(0x80 | (c & 63));
      } else {
         out[written++] = (uint8_t)(0xf0 | (c >> 18)); out[written++] = (uint8_t)(0x80 | ((c >> 12) & 63));
         out[written++] = (uint8_t)(0x80 | ((c >> 6) & 63)); out[written++] = (uint8_t)(0x80 | (c & 63));
      }
   }
}
/* Called only after the C runtime has validated the complete UTF-8 string. */
static uint32_t next_utf8(const uint8_t *bytes, size_t *pos) {
   uint32_t c = bytes[(*pos)++];
   size_t remaining;
   if (c < 128) return c;
   if (c < 224) { c &= 31; remaining = 1; }
   else if (c < 240) { c &= 15; remaining = 2; }
   else { c &= 7; remaining = 3; }
   while (remaining--) c = (c << 6) | (bytes[(*pos)++] & 63);
   return c;
}
static mxArray *decode_utf8(const uint8_t *bytes, size_t length) {
   size_t pos = 0, chars = 0, written = 0;
   mwSize dims[2];
   mxArray *out;
   mxChar *text;
   while (pos < length) { uint32_t c = next_utf8(bytes, &pos); chars += c > 0xffff ? 2 : 1; }
   dims[0] = chars ? 1 : 0; dims[1] = (mwSize)chars;
   out = mxCreateCharArray(2, dims); text = mxGetChars(out); pos = 0;
   while (pos < length) {
      uint32_t c = next_utf8(bytes, &pos);
      if (c <= 0xffff) text[written++] = (mxChar)c;
      else { c -= 0x10000; text[written++] = (mxChar)(0xd800 | (c >> 10)); text[written++] = (mxChar)(0xdc00 | (c & 1023)); }
   }
   return out;
}

/* Four contiguous SoA leaves; wire records contain ten big-endian words. */
static const mxArray *struct_field(const mxArray *obj, const char *name) {
   const mxArray *v;
   if (!obj || !mxIsStruct(obj) || mxGetNumberOfElements(obj)!=1 ||
       !(v=mxGetField(obj,0,name)))
      mexErrMsgIdAndTxt("SDL:InvalidValue", "Missing scalar structure field %s.",name);
   return v;
}
static mxArray *encode_records(const mxArray *message) {
   const mxArray *r=struct_field(message,"records"), *pose=struct_field(r,"pose");
   const mxArray *id=struct_field(r,"id"), *position=struct_field(struct_field(pose,"position"),"values");
   const mxArray *rotation=struct_field(pose,"rotation"), *measures=struct_field(r,"measures");
   const mxArray *leaves[]={id,position,rotation,measures};
   const size_t widths[]={1,3,4,2};
   const uint32_t *data[4]; size_t n=mxGetNumberOfElements(id),i,j,k,pos;
   mxArray *out; uint8_t *wire;
   if (n>UINT32_MAX || n>(SIZE_MAX-6)/40)
      mexErrMsgIdAndTxt("SDL:InvalidValue","Record count overflow.");
   for(k=0;k<4;++k) {
      if (mxGetClassID(leaves[k])!=(k?mxSINGLE_CLASS:mxINT32_CLASS) ||
          mxIsComplex(leaves[k]) || mxIsSparse(leaves[k]) || mxGetNumberOfElements(leaves[k])!=n*widths[k])
         mexErrMsgIdAndTxt("SDL:InvalidValue","Expected full int32 IDs and single SoA leaves with equal record counts.");
      data[k]=(const uint32_t *)mxGetData(leaves[k]);
   }
   out=mxCreateNumericMatrix(1+count_size((uint32_t)n)+n*40,1,mxUINT8_CLASS,mxREAL);
   wire=(uint8_t *)mxGetData(out);wire[0]=5;pos=1+put_count(wire+1,(uint32_t)n);
   for(i=0;i<n;++i) for(k=0;k<4;++k) for(j=0;j<widths[k];++j) {
      uint32_t bits; memcpy(&bits,(const uint8_t *)data[k]+4*(i*widths[k]+j),4);
      sdl_wire_write_u32(wire+pos,bits);pos+=4;
   }
   return out;
}
static mxArray *decode_records(NativeContext *ctx,const mxArray *arg) {
   const char *root_fields[]={"records"}, *record_fields[]={"id","pose","measures"};
   const char *pose_fields[]={"position","rotation"}, *position_fields[]={"values"};
   const size_t widths[]={1,3,4,2};
   mxArray *out,*r,*pose,*position,*leaves[4]; uint8_t *data[4];
   const uint8_t *wire;size_t length,pos=0,n,i,j,k;
   if(!mxIsUint8(arg)||mxIsComplex(arg)||mxIsSparse(arg))
      mexErrMsgIdAndTxt("SDL:InvalidValue","Expected full uint8 message bytes.");
   wire=(const uint8_t *)mxGetData(arg);length=mxGetNumberOfElements(arg);
   if(!type_decode_size(ctx->codec,wire,length))
      mexErrMsgIdAndTxt("SDL:Malformed","Invalid or truncated record batch.");
   if(take_count(wire,length,&pos)!=5)
      mexErrMsgIdAndTxt("SDL:Malformed","Expected BenchRecordBatch message ID.");
   n=take_count(wire,length,&pos);
   out=mxCreateStructMatrix(1,1,1,root_fields);r=mxCreateStructMatrix(1,1,3,record_fields);
   pose=mxCreateStructMatrix(1,1,2,pose_fields);position=mxCreateStructMatrix(1,1,1,position_fields);
   mxSetField(out,0,"records",r);mxSetField(r,0,"pose",pose);mxSetField(pose,0,"position",position);
   for(k=0;k<4;++k){
      leaves[k]=mxCreateNumericMatrix(widths[k]==1?n:widths[k],widths[k]==1?1:n,k?mxSINGLE_CLASS:mxINT32_CLASS,mxREAL);
      data[k]=(uint8_t *)mxGetData(leaves[k]);
   }
   mxSetField(r,0,"id",leaves[0]);mxSetField(position,0,"values",leaves[1]);
   mxSetField(pose,0,"rotation",leaves[2]);mxSetField(r,0,"measures",leaves[3]);
   for(i=0;i<n;++i)for(k=0;k<4;++k)for(j=0;j<widths[k];++j){
      uint32_t bits=sdl_wire_read_u32(wire+pos);pos+=4;
      memcpy(data[k]+4*(i*widths[k]+j),&bits,4);
   }
   return out;
}

static mxArray *encode_message(NativeContext *ctx, const mxArray *message) {
   const mxArray *header, *samples;
   const float *re, *im;
   size_t count, header_size, size, pos, i;
   mxArray *out;
   uint8_t *wire;
   (void)ctx; /* Prepared plan: singleton catalogue, root ID 1, string then c32. */
   if (!mxIsStruct(message) || mxGetNumberOfElements(message) != 1)
      mexErrMsgIdAndTxt("SDL:InvalidValue", "Expected a scalar BenchPayload struct.");
   header = mxGetField(message, 0, "header"); samples = mxGetField(message, 0, "samples");
   if (!header || !mxIsChar(header) ||
       !samples || (!mxIsSingle(samples) && !mxIsDouble(samples)) || mxIsSparse(samples))
      mexErrMsgIdAndTxt("SDL:InvalidValue", "Expected a character array and full single/double samples.");
   count = mxGetNumberOfElements(samples); header_size = utf8_size(header);
   if (header_size > SIZE_MAX - 11 || count > UINT32_MAX || count > (SIZE_MAX - header_size - 11) / 8)
      mexErrMsgIdAndTxt("SDL:InvalidValue", "Packed message size overflow.");
   size = 1 + count_size((uint32_t)header_size) + header_size + count_size((uint32_t)count) + count * 8;
   out = mxCreateNumericMatrix((mwSize)size, 1, mxUINT8_CLASS, mxREAL); wire = (uint8_t *)mxGetData(out);
   wire[0] = 1; pos = 1; pos += put_count(wire + pos, (uint32_t)header_size);
   encode_utf8(wire + pos, header); pos += header_size; pos += put_count(wire + pos, (uint32_t)count);
   if (mxIsDouble(samples)) {
      const double *real = (const double *)mxGetData(samples), *imag = (const double *)mxGetImagData(samples);
      for (i = 0; i < count; ++i) {
         float r = (float)real[i], z = imag ? (float)imag[i] : 0.0f;
         uint32_t rb, ib; memcpy(&rb, &r, 4); memcpy(&ib, &z, 4);
         sdl_wire_write_u32(wire + pos + i * 8, rb); sdl_wire_write_u32(wire + pos + i * 8 + 4, ib);
      }
      return out;
   }
   re = (const float *)mxGetData(samples); im = (const float *)mxGetImagData(samples);
   encode_samples(wire + pos, re, im, count);
   return out;
}
static mxArray *decode_message(NativeContext *ctx, const mxArray *arg) {
   const uint8_t *wire;
   size_t length, pos = 0, header_pos, sample_pos;
   uint32_t header_size, count;
   const char *fields[] = {"header", "samples"};
   mxArray *out, *samples;
   float *re, *im;
   if (!mxIsUint8(arg) || mxIsComplex(arg) || mxIsSparse(arg))
      mexErrMsgIdAndTxt("SDL:InvalidValue", "Expected full uint8 message bytes.");
   length = mxGetNumberOfElements(arg); wire = (const uint8_t *)mxGetData(arg);
   /* Reuse complete C runtime validation, including canonical counts, UTF-8 and trailing bytes. */
   if (!type_decode_size(ctx->codec, wire, length))
      mexErrMsgIdAndTxt("SDL:Malformed", "Invalid or truncated BenchPayload message.");
   if (take_count(wire, length, &pos) != 1)
      mexErrMsgIdAndTxt("SDL:Malformed", "Invalid message ID.");
   header_size = take_count(wire, length, &pos); header_pos = pos; pos += header_size;
   count = take_count(wire, length, &pos); sample_pos = pos;
   out = mxCreateStructMatrix(1, 1, 2, fields);
   mxSetField(out, 0, "header", decode_utf8(wire + header_pos, header_size));
   samples = mxCreateNumericMatrix((mwSize)count, 1, mxSINGLE_CLASS, mxCOMPLEX);
   mxSetField(out, 0, "samples", samples);
   re = (float *)mxGetData(samples); im = (float *)mxGetImagData(samples);
   decode_samples(re, im, wire + sample_pos, count);
   return out;
}

void mexFunction(int nlhs, mxArray *plhs[], int nrhs, const mxArray *prhs[]) {
   char action[32];
   size_t count;
   if (nlhs != 1 || nrhs < 2 || !mxIsChar(prhs[0]) ||
       mxGetString(prhs[0], action, sizeof(action)))
      mexErrMsgIdAndTxt("SDL:InvalidAction", "Expected one output and a conversion action.");
   if (!strcmp(action, "prepare")) {
      if (nrhs != 2) mexErrMsgIdAndTxt("SDL:InvalidAction", "Expected catalogue bytes.");
      plhs[0] = prepare_context(prhs[1]);
   } else if (!strcmp(action, "release")) {
      if (nrhs != 2) mexErrMsgIdAndTxt("SDL:InvalidAction", "Expected a context token.");
      plhs[0] = release_context(prhs[1]);
   } else if (!strcmp(action, "encode") || !strcmp(action, "decode")) {
      NativeContext *ctx;
      if (nrhs != 3) mexErrMsgIdAndTxt("SDL:InvalidAction", "Expected context and message data.");
      ctx = find_context(prhs[1]);
      if(ctx->records) plhs[0] = !strcmp(action,"encode") ? encode_records(prhs[2]) : decode_records(ctx,prhs[2]);
      else plhs[0] = !strcmp(action, "encode") ? encode_message(ctx, prhs[2]) : decode_message(ctx, prhs[2]);
   } else if (!strcmp(action, "encode_c32")) {
      const float *re, *im;
      uint8_t *out;
      if (nrhs != 2 || !mxIsSingle(prhs[1]) || mxIsSparse(prhs[1]))
         mexErrMsgIdAndTxt("SDL:InvalidValue", "Expected a full single array.");
      count = mxGetNumberOfElements(prhs[1]);
      if (count > SIZE_MAX / 8)
         mexErrMsgIdAndTxt("SDL:InvalidValue", "Packed array size overflow.");
      re = (const float *)mxGetData(prhs[1]);
      im = (const float *)mxGetImagData(prhs[1]);
      plhs[0] = mxCreateNumericMatrix((mwSize)(count * 8), 1, mxUINT8_CLASS, mxREAL);
      out = (uint8_t *)mxGetData(plhs[0]);
      encode_samples(out, re, im, count);
   } else if (!strcmp(action, "decode_c32")) {
      const uint8_t *in;
      float *re, *im;
      size_t offset, length;
      if (nrhs != 4 || !mxIsUint8(prhs[1]) || mxIsComplex(prhs[1]) || mxIsSparse(prhs[1]))
         mexErrMsgIdAndTxt("SDL:InvalidValue", "Expected uint8 bytes, offset and count.");
      offset = index_arg(prhs[2]); count = index_arg(prhs[3]);
      length = mxGetNumberOfElements(prhs[1]);
      /* One-based offset; check all bounds before allocating output. */
      if (!offset || offset - 1 > length || count > (length - (offset - 1)) / 8)
         mexErrMsgIdAndTxt("SDL:Malformed", "Truncated packed c32 payload.");
      in = (const uint8_t *)mxGetData(prhs[1]);
      plhs[0] = mxCreateNumericMatrix((mwSize)count, 1, mxSINGLE_CLASS, mxCOMPLEX);
      re = (float *)mxGetData(plhs[0]); im = (float *)mxGetImagData(plhs[0]);
      decode_samples(re, im, in ? in + offset - 1 : in, count);
   } else {
      mexErrMsgIdAndTxt("SDL:InvalidAction", "Unknown packed conversion action.");
   }
}
