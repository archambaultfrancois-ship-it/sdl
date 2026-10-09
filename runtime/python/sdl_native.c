#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "sdl_wire.h"
#include <stdint.h>
#include <string.h>
#include <math.h>

typedef enum { BOOL, I8, I16, I32, I64, F32, F64, C32, C64, ARRAY, RECORD } Kind;
typedef struct Node {
   Kind kind;
   size_t size, length;
   size_t swap_unit;
   int has_bool;
   PyObject *cls;
   PyObject *real_name, *imag_name;
   struct Node **children;
   PyObject **names;
} Node;
static void free_node(Node *n) {
   size_t i;
   if(!n)return;
   for(i=0;i<(n->kind==ARRAY?1:n->length) && n->children;i++) {
      free_node(n->children[i]);
      if(n->names)Py_XDECREF(n->names[i]);
   }
   Py_XDECREF(n->real_name);Py_XDECREF(n->imag_name);
   Py_XDECREF(n->cls);PyMem_Free(n->children);PyMem_Free(n->names);PyMem_Free(n);
}
static Node *compile_node(PyObject *spec,int depth) {
   static const char *types[]={"bool","int8","int16","int32","int64","fl32","fl64","c32","c64"};
   static const size_t sizes[]={1,1,2,4,8,4,8,8,16};
   Node *n=NULL;const char *kind;size_t i;
   if(depth>64 || !PyTuple_Check(spec) || PyTuple_GET_SIZE(spec)<1 ||
      !(kind=PyUnicode_AsUTF8(PyTuple_GET_ITEM(spec,0))))goto invalid;
   n=PyMem_Calloc(1,sizeof(*n));if(!n)return (Node *)PyErr_NoMemory();
   for(i=0;i<9;i++)if(!strcmp(kind,types[i])) {
      if(PyTuple_GET_SIZE(spec)!=(i>=7?2:1))goto invalid;
      n->kind=(Kind)i;n->size=sizes[i];
      n->swap_unit=i>=7?sizes[i]/2:sizes[i];n->has_bool=i==BOOL;
      if(i>=7){if(!PyType_Check(PyTuple_GET_ITEM(spec,1)))goto invalid;n->cls=PyTuple_GET_ITEM(spec,1);Py_INCREF(n->cls);
         n->real_name=PyUnicode_InternFromString("real");n->imag_name=PyUnicode_InternFromString("imag");
         if(!n->real_name||!n->imag_name)goto fail;
      }
      return n;
   }
   if(!strcmp(kind,"array")) {
      Py_ssize_t count;
      if(PyTuple_GET_SIZE(spec)!=3)goto invalid;
      count=PyLong_AsSsize_t(PyTuple_GET_ITEM(spec,1));if(count<=0||PyErr_Occurred())goto invalid;
      n->kind=ARRAY;n->length=1;n->children=PyMem_Calloc(1,sizeof(Node *));
      if(!n->children)goto memory;
      n->children[0]=compile_node(PyTuple_GET_ITEM(spec,2),depth+1);if(!n->children[0])goto fail;
      if((size_t)count>65536/n->children[0]->size)goto invalid;
      n->size=(size_t)count*n->children[0]->size;
      n->swap_unit=n->children[0]->swap_unit;n->has_bool=n->children[0]->has_bool;
      /* ARRAY length stores element count; free_node uses one child. */
      n->length=(size_t)count;return n;
   }
   if(!strcmp(kind,"record")) {
      PyObject *fields;
      if(PyTuple_GET_SIZE(spec)!=3 || !PyType_Check(PyTuple_GET_ITEM(spec,1)))goto invalid;
      fields=PyTuple_GET_ITEM(spec,2);if(!PyTuple_Check(fields)||!PyTuple_GET_SIZE(fields))goto invalid;
      n->kind=RECORD;n->length=(size_t)PyTuple_GET_SIZE(fields);
      n->cls=PyTuple_GET_ITEM(spec,1);Py_INCREF(n->cls);
      n->children=PyMem_Calloc(n->length,sizeof(Node *));n->names=PyMem_Calloc(n->length,sizeof(PyObject *));
      if(!n->children||!n->names)goto memory;
      for(i=0;i<n->length;i++) {
         PyObject *field=PyTuple_GET_ITEM(fields,i);
         if(!PyTuple_Check(field)||PyTuple_GET_SIZE(field)!=2||!PyUnicode_Check(PyTuple_GET_ITEM(field,0)))goto invalid;
         n->names[i]=PyTuple_GET_ITEM(field,0);Py_INCREF(n->names[i]);
         n->children[i]=compile_node(PyTuple_GET_ITEM(field,1),depth+1);if(!n->children[i])goto fail;
         if(n->children[i]->size>65536-n->size)goto invalid;
         n->size+=n->children[i]->size;
         n->has_bool|=n->children[i]->has_bool;
         if(!i)n->swap_unit=n->children[i]->swap_unit;
         else if(n->swap_unit!=n->children[i]->swap_unit)n->swap_unit=0;
      }
      return n;
   }
invalid:
   if(!PyErr_Occurred())PyErr_SetString(PyExc_ValueError,"Invalid native Packed plan");
   goto fail;
memory:PyErr_NoMemory();
fail:free_node(n);return NULL;
}
static void capsule_free(PyObject *capsule) {free_node(PyCapsule_GetPointer(capsule,"SDL.PackedPlan"));}
static PyObject *prepare_plan(PyObject *self,PyObject *spec) {
   Node *n;PyObject *out;(void)self;
   n=compile_node(spec,0);if(!n)return NULL;
   out=PyCapsule_New(n,"SDL.PackedPlan",capsule_free);if(!out)free_node(n);return out;
}
static int write_float(uint8_t *out,PyObject *value,int single) {
   double d=PyFloat_AsDouble(value);if(PyErr_Occurred())return -1;
   if(single){float f=(float)d;uint32_t bits;
      if(isfinite(d)&&!isfinite(f)){PyErr_SetString(PyExc_OverflowError,"float32 overflow");return -1;}
      memcpy(&bits,&f,4);sdl_wire_write_u32(out,bits);
   }else{uint64_t bits;memcpy(&bits,&d,8);sdl_wire_write_u32(out,(uint32_t)(bits>>32));sdl_wire_write_u32(out+4,(uint32_t)bits);}
   return 0;
}
static int write_node(const Node *n,PyObject *v,uint8_t *out) {
   size_t i,pos=0;
   if(n->kind==ARRAY){
      PyObject *seq=PySequence_Tuple(v);int ok=0;
      if(!seq)return -1;
      if((size_t)PyTuple_GET_SIZE(seq)!=n->length){PyErr_SetString(PyExc_ValueError,"Invalid fixed array dimensions");ok=-1;}
      else for(i=0;i<n->length;i++)if(write_node(n->children[0],PyTuple_GET_ITEM(seq,i),out+i*n->children[0]->size)){ok=-1;break;}
      Py_DECREF(seq);return ok;
   }
   if(n->kind==RECORD){
      int match=PyObject_IsInstance(v,n->cls);if(match<0)return -1;
      if(!match){PyErr_SetString(PyExc_TypeError,"Wrong Packed message type");return -1;}
      for(i=0;i<n->length;i++){
         PyObject *child=PyObject_GetAttr(v,n->names[i]);int status;
         if(!child)return -1;
         status=write_node(n->children[i],child,out+pos);Py_DECREF(child);
         if(status)return -1;
         pos+=n->children[i]->size;
      }return 0;
   }
   if(n->kind==BOOL){if(!PyBool_Check(v)){PyErr_SetString(PyExc_TypeError,"Boolean field requires bool");return -1;}out[0]=(v==Py_True);return 0;}
   if(n->kind>=I8 && n->kind<=I64){
      PyObject *index=PyNumber_Index(v);long long x;int bits=(int)n->size*8;
      if(!index)return -1;
      x=PyLong_AsLongLong(index);Py_DECREF(index);if(PyErr_Occurred())return -1;
      if(bits<64&&(x<-(INT64_C(1)<<(bits-1))||x>((INT64_C(1)<<(bits-1))-1))){PyErr_SetString(PyExc_OverflowError,"Packed integer overflow");return -1;}
      for(i=0;i<n->size;i++)out[i]=(uint8_t)((uint64_t)x>>(8*(n->size-1-i)));
      return 0;
   }
   if(n->kind==F32||n->kind==F64)return write_float(out,v,n->kind==F32);
   {
      PyObject *re=PyObject_GetAttr(v,n->real_name),*im;int status;
      if(!re)return -1;
      im=PyObject_GetAttr(v,n->imag_name);if(!im){Py_DECREF(re);return -1;}
      status=write_float(out,re,n->kind==C32);if(!status)status=write_float(out+n->size/2,im,n->kind==C32);
      Py_DECREF(re);Py_DECREF(im);return status;
   }
}
static PyObject *read_float(const uint8_t *in,int single){
   double d;
   if(single){uint32_t bits=sdl_wire_read_u32(in);float f;memcpy(&f,&bits,4);d=f;}
   else{uint64_t bits=((uint64_t)sdl_wire_read_u32(in)<<32)|sdl_wire_read_u32(in+4);memcpy(&d,&bits,8);}
   return PyFloat_FromDouble(d);
}
static PyObject *new_record(PyObject *cls) {
   PyTypeObject *type=(PyTypeObject *)cls;
   if(type->tp_new==PyBaseObject_Type.tp_new)return type->tp_alloc(type,0);
   return PyObject_CallMethod(cls,"__new__","O",cls);
}
static PyObject *read_node(const Node *n,const uint8_t *in){
   size_t i,pos=0;PyObject *out;
   if(n->kind==ARRAY){
      out=PyList_New((Py_ssize_t)n->length);if(!out)return NULL;
      for(i=0;i<n->length;i++){
         PyObject *v=read_node(n->children[0],in+i*n->children[0]->size);if(!v){Py_DECREF(out);return NULL;}
         PyList_SET_ITEM(out,i,v);
      }return out;
   }
   if(n->kind==RECORD){
      out=new_record(n->cls);if(!out)return NULL;
      for(i=0;i<n->length;i++){
         PyObject *v=read_node(n->children[i],in+pos);int status;
         if(!v){Py_DECREF(out);return NULL;}status=PyObject_SetAttr(out,n->names[i],v);Py_DECREF(v);
         if(status){Py_DECREF(out);return NULL;}pos+=n->children[i]->size;
      }return out;
   }
   if(n->kind==BOOL){if(in[0]>1){PyErr_SetString(PyExc_ValueError,"Invalid boolean value");return NULL;}return PyBool_FromLong(in[0]);}
   if(n->kind>=I8&&n->kind<=I64){
      uint64_t bits=0;int64_t signed_value;
      for(i=0;i<n->size;i++)bits=(bits<<8)|in[i];
      if(n->size<8&&(in[0]&128))bits|=UINT64_MAX<<(8*n->size);
      memcpy(&signed_value,&bits,8);return PyLong_FromLongLong(signed_value);
   }
   if(n->kind==F32||n->kind==F64)return read_float(in,n->kind==F32);
   {
      PyObject *re=read_float(in,n->kind==C32),*im,*v;
      if(!re)return NULL;
      im=read_float(in+n->size/2,n->kind==C32);if(!im){Py_DECREF(re);return NULL;}
      v=new_record(n->cls);
      if(v && (PyObject_SetAttr(v,n->real_name,re)||PyObject_SetAttr(v,n->imag_name,im))) {Py_DECREF(v);v=NULL;}
      Py_DECREF(re);Py_DECREF(im);return v;
   }
}
static PyObject *encode_plan(PyObject *self,PyObject *args){
   PyObject *capsule,*values,*seq,*out;Node *n;Py_ssize_t count,i;uint8_t *wire;(void)self;
   if(!PyArg_ParseTuple(args,"OO",&capsule,&values))return NULL;
   n=PyCapsule_GetPointer(capsule,"SDL.PackedPlan");if(!n)return NULL;
   seq=PySequence_Tuple(values);if(!seq)return NULL;count=PyTuple_GET_SIZE(seq);
   if((size_t)count>UINT32_MAX || count>PY_SSIZE_T_MAX/(Py_ssize_t)n->size){Py_DECREF(seq);PyErr_SetString(PyExc_OverflowError,"Packed size overflow");return NULL;}
   out=PyBytes_FromStringAndSize(NULL,count*(Py_ssize_t)n->size);if(!out){Py_DECREF(seq);return NULL;}
   wire=(uint8_t *)PyBytes_AS_STRING(out);
   for(i=0;i<count;i++)if(write_node(n,PyTuple_GET_ITEM(seq,i),wire+i*n->size)){Py_DECREF(seq);Py_DECREF(out);return NULL;}
   Py_DECREF(seq);return out;
}
static PyObject *decode_plan(PyObject *self,PyObject *args){
   PyObject *capsule,*out;Py_buffer buffer;Node *n;Py_ssize_t count,i;(void)self;
   if(!PyArg_ParseTuple(args,"Oy*n",&capsule,&buffer,&count))return NULL;
   n=PyCapsule_GetPointer(capsule,"SDL.PackedPlan");if(!n){PyBuffer_Release(&buffer);return NULL;}
   if(count<0||(size_t)count>UINT32_MAX||count>PY_SSIZE_T_MAX/(Py_ssize_t)n->size||buffer.len!=count*(Py_ssize_t)n->size){
      PyBuffer_Release(&buffer);PyErr_SetString(PyExc_ValueError,"Invalid Packed payload size");return NULL;
   }
   out=PyList_New(count);if(!out){PyBuffer_Release(&buffer);return NULL;}
   for(i=0;i<count;i++){
      PyObject *v=read_node(n,(const uint8_t *)buffer.buf+i*n->size);
      if(!v){Py_DECREF(out);PyBuffer_Release(&buffer);return NULL;}PyList_SET_ITEM(out,i,v);
   }
   PyBuffer_Release(&buffer);return out;
}

/* No Python object graph is needed to validate fixed numeric wire records. */
static int validate_node(const Node *n,const uint8_t *in) {
   size_t i,pos=0;
   if(!n->has_bool)return 0;
   if(n->kind==BOOL){
      if(in[0]>1){PyErr_SetString(PyExc_ValueError,"Invalid boolean value");return -1;}
   }else if(n->kind==ARRAY){
      for(i=0;i<n->length;i++)if(validate_node(n->children[0],in+i*n->children[0]->size))return -1;
   }else if(n->kind==RECORD){
      for(i=0;i<n->length;i++){
         if(validate_node(n->children[i],in+pos))return -1;
         pos+=n->children[i]->size;
      }
   }
   return 0;
}
static int validate_buffer(const Node *n,const Py_buffer *buffer,Py_ssize_t count) {
   Py_ssize_t i;
   if(count<0||(size_t)count>UINT32_MAX||count>PY_SSIZE_T_MAX/(Py_ssize_t)n->size||buffer->len!=count*(Py_ssize_t)n->size){
      PyErr_SetString(PyExc_ValueError,"Invalid Packed payload size");return -1;
   }
   if(n->has_bool)for(i=0;i<count;i++)if(validate_node(n,(const uint8_t *)buffer->buf+i*n->size))return -1;
   return 0;
}
static PyObject *validate_plan(PyObject *self,PyObject *args) {
   PyObject *capsule;Py_buffer buffer;Node *n;Py_ssize_t count;int status;(void)self;
   if(!PyArg_ParseTuple(args,"Oy*n",&capsule,&buffer,&count))return NULL;
   n=PyCapsule_GetPointer(capsule,"SDL.PackedPlan");
   status=n?validate_buffer(n,&buffer,count):-1;
   PyBuffer_Release(&buffer);if(status)return NULL;Py_RETURN_NONE;
}
static void swap_words(uint8_t *restrict out,const uint8_t *restrict in,size_t length,size_t unit) {
   size_t i,j;
   if(unit==4){
      for(i=0;i<length;i+=4){uint32_t v;memcpy(&v,in+i,4);v=__builtin_bswap32(v);memcpy(out+i,&v,4);}
   }else if(unit==8){
      for(i=0;i<length;i+=8){uint64_t v;memcpy(&v,in+i,8);v=__builtin_bswap64(v);memcpy(out+i,&v,8);}
   }else for(i=0;i<length;i+=unit)for(j=0;j<unit;j++)out[i+j]=in[i+unit-1-j];
}
static void swap_record(const Node *n,uint8_t *out,const uint8_t *in) {
   size_t i,pos=0;
   if(n->swap_unit){swap_words(out,in,n->size,n->swap_unit);return;}
   if(n->kind==ARRAY){
      for(i=0;i<n->length;i++)swap_record(n->children[0],out+i*n->children[0]->size,in+i*n->children[0]->size);
   }else for(i=0;i<n->length;i++){
      swap_record(n->children[i],out+pos,in+pos);pos+=n->children[i]->size;
   }
}
static PyObject *convert_buffer(PyObject *self,PyObject *args) {
   PyObject *capsule,*out;Py_buffer buffer;Node *n;const char *order;
   Py_ssize_t count,i;int reverse;const uint16_t endian=1;(void)self;
   if(!PyArg_ParseTuple(args,"Oy*s",&capsule,&buffer,&order))return NULL;
   n=PyCapsule_GetPointer(capsule,"SDL.PackedPlan");if(!n)goto fail;
   if(!strcmp(order,"native"))reverse=*(const uint8_t *)&endian;
   else if(!strcmp(order,"little"))reverse=1;
   else if(!strcmp(order,"big"))reverse=0;
   else{PyErr_SetString(PyExc_ValueError,"Expected native, little or big byteorder");goto fail;}
   count=buffer.len/(Py_ssize_t)n->size;
   if(validate_buffer(n,&buffer,count))goto fail;
   out=PyBytes_FromStringAndSize(NULL,buffer.len);if(!out)goto fail;
   if(!reverse){if(buffer.len)memcpy(PyBytes_AS_STRING(out),buffer.buf,(size_t)buffer.len);}
   else if(n->swap_unit)swap_words((uint8_t *)PyBytes_AS_STRING(out),buffer.buf,(size_t)buffer.len,n->swap_unit);
   else for(i=0;i<count;i++)swap_record(n,(uint8_t *)PyBytes_AS_STRING(out)+i*n->size,(const uint8_t *)buffer.buf+i*n->size);
   PyBuffer_Release(&buffer);return out;
fail:PyBuffer_Release(&buffer);return NULL;
}

/* Full-message buffer path for exact roots containing strings and Packed fields. */
typedef struct {
   uint32_t id;
   Py_ssize_t length;
   PyObject *cls,*packed_cls,*fields,*wire_name,*spec_name,*count_name,*plan_name,*little_name;
} MessagePlan;
typedef struct {
   PyObject *owner;
   Py_buffer buffer;
   const uint8_t *data;
   Py_ssize_t length;
   uint32_t count;
   int reverse;
   Node *node;
} MessageValue;
static void free_message(MessagePlan *p) {
   if(!p)return;
   Py_XDECREF(p->cls);Py_XDECREF(p->packed_cls);Py_XDECREF(p->fields);
   Py_XDECREF(p->wire_name);Py_XDECREF(p->spec_name);Py_XDECREF(p->count_name);Py_XDECREF(p->plan_name);Py_XDECREF(p->little_name);
   PyMem_Free(p);
}
static void message_capsule_free(PyObject *capsule){free_message(PyCapsule_GetPointer(capsule,"SDL.MessagePlan"));}
static PyObject *prepare_message(PyObject *self,PyObject *args){
   PyObject *id_arg,*cls,*packed_cls,*fields,*out;unsigned long id;Py_ssize_t i;
   MessagePlan *p=NULL;(void)self;
   if(!PyArg_ParseTuple(args,"OOOO",&id_arg,&cls,&packed_cls,&fields))return NULL;
   id=PyLong_AsUnsignedLong(id_arg);if(PyErr_Occurred())return NULL;
   if(!id||id>UINT32_MAX||!PyType_Check(cls)||!PyType_Check(packed_cls)||!PyTuple_Check(fields)||
      PyTuple_GET_SIZE(fields)<1||PyTuple_GET_SIZE(fields)>65536)goto invalid;
   for(i=0;i<PyTuple_GET_SIZE(fields);i++){
      PyObject *f=PyTuple_GET_ITEM(fields,i),*capsule;
      if(!PyTuple_Check(f)||PyTuple_GET_SIZE(f)!=3||!PyUnicode_Check(PyTuple_GET_ITEM(f,0)))goto invalid;
      capsule=PyTuple_GET_ITEM(f,1);
      if(capsule!=Py_None&&!PyCapsule_GetPointer(capsule,"SDL.PackedPlan"))return NULL;
   }
   p=PyMem_Calloc(1,sizeof(*p));if(!p)return PyErr_NoMemory();
   p->id=(uint32_t)id;p->length=PyTuple_GET_SIZE(fields);
   p->cls=cls;Py_INCREF(cls);p->packed_cls=packed_cls;Py_INCREF(packed_cls);p->fields=fields;Py_INCREF(fields);
   p->wire_name=PyUnicode_InternFromString("_wire");p->spec_name=PyUnicode_InternFromString("_spec");
   p->count_name=PyUnicode_InternFromString("_count");p->plan_name=PyUnicode_InternFromString("_plan");p->little_name=PyUnicode_InternFromString("_little");
   if(!p->wire_name||!p->spec_name||!p->count_name||!p->plan_name||!p->little_name){free_message(p);return NULL;}
   out=PyCapsule_New(p,"SDL.MessagePlan",message_capsule_free);if(!out)free_message(p);return out;
invalid:PyErr_SetString(PyExc_ValueError,"Invalid native message plan");return NULL;
}
static size_t counter_size(uint32_t n){size_t size=1;while(n>=128){n>>=7;size++;}return size;}
static size_t put_counter(uint8_t *out,uint32_t n){
   size_t i=0;do{uint8_t b=n&127;n>>=7;out[i++]=b|(n?128:0);}while(n);return i;
}
static int get_counter(const uint8_t *in,size_t length,size_t *pos,uint32_t *result){
   size_t i;uint32_t value=0;
   for(i=0;i<5;i++){
      uint8_t b;
      if(*pos==length)goto invalid;
      b=in[(*pos)++];if(i==4&&b>15)goto invalid;
      value|=(uint32_t)(b&127)<<(7*i);
      if(!(b&128)){if(i&&!b)goto invalid;*result=value;return 0;}
   }
invalid:PyErr_SetString(PyExc_ValueError,"Invalid or truncated canonical counter");return -1;
}
static void free_values(MessageValue *values,Py_ssize_t length){
   Py_ssize_t i;
   if(!values)return;
   for(i=0;i<length;i++){if(values[i].buffer.obj)PyBuffer_Release(&values[i].buffer);Py_XDECREF(values[i].owner);}
   PyMem_Free(values);
}
static PyObject *encode_message_buffer(PyObject *self,PyObject *args){
   PyObject *capsule,*message,*target=Py_None,*out=NULL;Py_ssize_t offset=0,i,size,pos;
   MessagePlan *p;MessageValue *values=NULL;Py_buffer output={0};uint8_t *wire;(void)self;
   if(!PyArg_ParseTuple(args,"OO|On",&capsule,&message,&target,&offset))return NULL;
   p=PyCapsule_GetPointer(capsule,"SDL.MessagePlan");if(!p)return NULL;
   values=PyMem_Calloc(p->length,sizeof(*values));if(!values)return PyErr_NoMemory();
   size=(Py_ssize_t)counter_size(p->id);
   for(i=0;i<p->length;i++){
      PyObject *field=PyTuple_GET_ITEM(p->fields,i),*plan=PyTuple_GET_ITEM(field,1),*value;
      MessageValue *v=values+i;
      value=PyObject_GetAttr(message,PyTuple_GET_ITEM(field,0));if(!value)goto done;
      v->owner=value;
      if(plan==Py_None){
         if(!PyUnicode_Check(value)){PyErr_SetString(PyExc_TypeError,"Expected a string field");goto done;}
         v->data=(const uint8_t *)PyUnicode_AsUTF8AndSize(value,&v->length);if(!v->data)goto done;
         if((size_t)v->length>UINT32_MAX){PyErr_SetString(PyExc_OverflowError,"String length overflow");goto done;}
         v->count=(uint32_t)v->length;
      }else{
         PyObject *spec,*storage,*little;Node *node=PyCapsule_GetPointer(plan,"SDL.PackedPlan");int match;
         if(!node)goto done;
         if(!PyObject_TypeCheck(value,(PyTypeObject *)p->packed_cls)){Py_INCREF(Py_NotImplemented);out=Py_NotImplemented;goto done;}
         spec=PyObject_GetAttr(value,p->spec_name);if(!spec)goto done;
         match=PyObject_RichCompareBool(spec,PyTuple_GET_ITEM(field,2),Py_EQ);Py_DECREF(spec);
         if(match<0)goto done;
         if(!match){PyErr_SetString(PyExc_ValueError,"Packed buffer layout mismatch");goto done;}
         storage=PyObject_GetAttr(value,p->wire_name);if(!storage)goto done;
         match=PyObject_GetBuffer(storage,&v->buffer,PyBUF_SIMPLE);Py_DECREF(storage);if(match)goto done;
         v->length=v->buffer.len;v->data=v->buffer.buf;
         if(!v->buffer.readonly){PyErr_SetString(PyExc_ValueError,"Packed storage must be readonly");goto done;}
         if(validate_buffer(node,&v->buffer,v->length/(Py_ssize_t)node->size))goto done;
         v->count=(uint32_t)(v->length/(Py_ssize_t)node->size);v->node=node;
         little=PyObject_GetAttr(value,p->little_name);if(!little)goto done;
         if(!PyBool_Check(little)){Py_DECREF(little);PyErr_SetString(PyExc_ValueError,"Invalid byteorder flag");goto done;}
         v->reverse=little==Py_True;Py_DECREF(little);
      }
      if(v->length>PY_SSIZE_T_MAX-size-(Py_ssize_t)counter_size(v->count)){
         PyErr_SetString(PyExc_OverflowError,"Message size overflow");goto done;
      }
      size+=(Py_ssize_t)counter_size(v->count)+v->length;
   }
   if(target==Py_None){
      out=PyBytes_FromStringAndSize(NULL,size);if(!out)goto done;
      wire=(uint8_t *)PyBytes_AS_STRING(out);
   }else{
      if(PyObject_GetBuffer(target,&output,PyBUF_WRITABLE|PyBUF_C_CONTIGUOUS))goto done;
      if(offset<0||offset>output.len||size>output.len-offset){PyErr_SetString(PyExc_ValueError,"Output buffer too small or invalid offset");goto done;}
      out=PyLong_FromSsize_t(size);if(!out)goto done;
      wire=(uint8_t *)output.buf+offset;
   }
   pos=(Py_ssize_t)put_counter(wire,p->id);
   for(i=0;i<p->length;i++){
      pos+=(Py_ssize_t)put_counter(wire+pos,values[i].count);
      if(values[i].reverse){
         Node *node=values[i].node;size_t j;
         if(node->swap_unit)swap_words(wire+pos,values[i].data,(size_t)values[i].length,node->swap_unit);
         else for(j=0;j<values[i].count;j++)swap_record(node,wire+pos+j*node->size,values[i].data+j*node->size);
      }else if(values[i].length)memcpy(wire+pos,values[i].data,(size_t)values[i].length);
      pos+=values[i].length;
   }
done:
   if(output.obj)PyBuffer_Release(&output);
   free_values(values,p->length);return out;
}
static PyObject *decode_message_buffer(PyObject *self,PyObject *args){
   PyObject *capsule,*data,*out=NULL,*view=NULL;MessagePlan *p;MessageValue *values=NULL;
   const uint8_t *wire;size_t length,pos=0;uint32_t id;Py_ssize_t i;(void)self;
   if(!PyArg_ParseTuple(args,"OO",&capsule,&data))return NULL;
   p=PyCapsule_GetPointer(capsule,"SDL.MessagePlan");if(!p)return NULL;
   if(!PyBytes_Check(data)){PyErr_SetString(PyExc_TypeError,"Expected immutable bytes");return NULL;}
   wire=(const uint8_t *)PyBytes_AS_STRING(data);length=(size_t)PyBytes_GET_SIZE(data);
   if(get_counter(wire,length,&pos,&id))return NULL;
   if(id!=p->id){PyErr_SetString(PyExc_ValueError,"Wrong message ID");return NULL;}
   values=PyMem_Calloc(p->length,sizeof(*values));if(!values)return PyErr_NoMemory();
   /* Validate the whole message before allocating returned objects. */
   for(i=0;i<p->length;i++){
      PyObject *plan=PyTuple_GET_ITEM(PyTuple_GET_ITEM(p->fields,i),1);MessageValue *v=values+i;
      size_t bytes;
      if(get_counter(wire,length,&pos,&v->count))goto fail;
      if(plan==Py_None){
         bytes=v->count;
         if(bytes>length-pos||!sdl_wire_valid_utf8(wire+pos,bytes)){PyErr_SetString(PyExc_ValueError,"Invalid UTF-8 or truncated string");goto fail;}
      }else{
         Node *n=PyCapsule_GetPointer(plan,"SDL.PackedPlan");Py_buffer buffer={0};
         if(!n)goto fail;
         if(v->count>(length-pos)/n->size){PyErr_SetString(PyExc_ValueError,"Truncated Packed payload");goto fail;}
         bytes=(size_t)v->count*n->size;buffer.buf=(void *)(wire+pos);buffer.len=(Py_ssize_t)bytes;
         if(validate_buffer(n,&buffer,v->count))goto fail;
      }
      v->length=(Py_ssize_t)bytes;v->data=wire+pos;pos+=bytes;
   }
   if(pos!=length){PyErr_SetString(PyExc_ValueError,"Trailing message bytes");goto fail;}
   out=new_record(p->cls);if(!out)goto fail;
   view=PyMemoryView_FromObject(data);if(!view)goto fail;
   for(i=0;i<p->length;i++){
      PyObject *field=PyTuple_GET_ITEM(p->fields,i),*plan=PyTuple_GET_ITEM(field,1),*value=NULL;
      MessageValue *v=values+i;
      if(plan==Py_None)value=PyUnicode_DecodeUTF8((const char *)v->data,v->length,"strict");
      else{
         PyObject *slice=PySequence_GetSlice(view,v->data-wire,v->data-wire+v->length),*count;
         if(!slice)goto fail;
         value=new_record(p->packed_cls);count=PyLong_FromUnsignedLong(v->count);
         if(!value||!count||PyObject_GenericSetAttr(value,p->wire_name,slice)||
            PyObject_GenericSetAttr(value,p->count_name,count)||PyObject_GenericSetAttr(value,p->plan_name,plan)||
            PyObject_GenericSetAttr(value,p->spec_name,PyTuple_GET_ITEM(field,2))||
            PyObject_GenericSetAttr(value,p->little_name,Py_False)){
            Py_XDECREF(value);value=NULL;
         }
         Py_XDECREF(count);Py_DECREF(slice);
      }
      if(!value)goto fail;
      if(PyObject_SetAttr(out,PyTuple_GET_ITEM(field,0),value)){Py_DECREF(value);goto fail;}
      Py_DECREF(value);
   }
   Py_DECREF(view);free_values(values,p->length);return out;
fail:Py_XDECREF(view);Py_XDECREF(out);free_values(values,p->length);return NULL;
}

static PyMethodDef methods[]={
   {"prepare_message",prepare_message,METH_VARARGS,"Prepare an exact string/Packed root."},
   {"encode_message",encode_message_buffer,METH_VARARGS,"Encode Packed buffers into one final message allocation."},
   {"decode_message",decode_message_buffer,METH_VARARGS,"Validate and decode a complete message into Packed views."},
   {"validate",validate_plan,METH_VARARGS,"Validate a fixed wire buffer without constructing objects."},
   {"convert",convert_buffer,METH_VARARGS,"Convert tightly packed numeric storage to big-endian wire bytes."},
   {"prepare",prepare_plan,METH_O,"Prepare a fixed Packed conversion plan."},
   {"encode",encode_plan,METH_VARARGS,"Encode a sequence with a prepared plan."},
   {"decode",decode_plan,METH_VARARGS,"Decode validated-size Packed bytes."},
   {NULL,NULL,0,NULL}
};
static struct PyModuleDef module={PyModuleDef_HEAD_INIT,"_sdl_native",NULL,-1,methods,NULL,NULL,NULL,NULL};
PyMODINIT_FUNC PyInit__sdl_native(void){
   PyObject *out=PyModule_Create(&module);
   if(!out)return NULL;
   if(PyModule_AddIntConstant(out,"API_VERSION",2)){Py_DECREF(out);return NULL;}
   return out;
}
