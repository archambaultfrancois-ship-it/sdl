#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "sdl_filter.h"
#include <string.h>
typedef struct { uint32_t id; PyObject *name; } Field;
typedef struct { PyTypeObject *type; Field *fields; size_t count; PyObject *owner; } Binding;
typedef struct { SdlFilter *plan; Binding *bindings; size_t count; } Plan;
typedef struct { Plan *plan; PyObject *root; PyObject *strings[128]; } Evaluation;
static void destroy(PyObject *capsule) {
    Plan *p=PyCapsule_GetPointer(capsule,"sdl.filter");if(!p){PyErr_Clear();return;}
    sdl_filter_free(p->plan);
    for(size_t i=0;i<p->count;++i){Binding *b=&p->bindings[i];Py_DECREF(b->type);Py_XDECREF(b->owner);for(size_t j=0;j<b->count;++j)Py_DECREF(b->fields[j].name);PyMem_Free(b->fields);}
    PyMem_Free(p->bindings);PyMem_Free(p);
}
static Binding *binding(Plan *p,PyObject *object) {
    PyTypeObject *type=Py_TYPE(object);
    for(size_t i=0;i<p->count;++i)if(p->bindings[i].type==type)return &p->bindings[i];
    PyObject *metadata=PyObject_GetAttrString((PyObject *)type,"_SDL_FIELDS");if(!metadata)return NULL;
    PyObject *fields=PySequence_Fast(metadata,"invalid SDL fields");Py_DECREF(metadata);if(!fields)return NULL;
    PyObject *owner=PyObject_GetAttrString((PyObject *)type,"_SDL_NAME");if(!owner){Py_DECREF(fields);return NULL;}
    size_t count=(size_t)PySequence_Fast_GET_SIZE(fields);Field *items=PyMem_Calloc(count?count:1,sizeof(*items));if(!items){Py_DECREF(fields);Py_DECREF(owner);PyErr_NoMemory();return NULL;}
    size_t filled=0;
    for(;filled<count;++filled){PyObject *row=PySequence_Fast_GET_ITEM(fields,(Py_ssize_t)filled);
        if(!PyTuple_Check(row)||PyTuple_GET_SIZE(row)<2)break;
        unsigned long id=PyLong_AsUnsignedLong(PyTuple_GET_ITEM(row,0));if(PyErr_Occurred()||id>UINT32_MAX)break;
        items[filled].id=(uint32_t)id;items[filled].name=PyTuple_GET_ITEM(row,1);Py_INCREF(items[filled].name);
    }
    Py_DECREF(fields);
    if(filled!=count){for(size_t i=0;i<filled;++i)Py_DECREF(items[i].name);PyMem_Free(items);Py_DECREF(owner);if(!PyErr_Occurred())PyErr_SetString(PyExc_ValueError,"invalid SDL metadata");return NULL;}
    Binding *resized=PyMem_Realloc(p->bindings,(p->count+1)*sizeof(*resized));if(!resized){for(size_t i=0;i<count;++i)Py_DECREF(items[i].name);PyMem_Free(items);Py_DECREF(owner);PyErr_NoMemory();return NULL;}
    p->bindings=resized;Binding *b=&p->bindings[p->count++];b->type=type;Py_INCREF(type);b->fields=items;b->count=count;b->owner=owner;return b;
}
static int same(PyObject *name,SdlFilterBytes expected){Py_ssize_t size;const char *s=PyUnicode_AsUTF8AndSize(name,&size);return s&&size==(Py_ssize_t)expected.size&&!memcmp(s,expected.data,expected.size);}
static PyObject *field_value(Plan *p,PyObject *object,const SdlFilterStep *step) {
    if(PyDict_Check(object)){PyObject *name=PyUnicode_DecodeUTF8((const char *)step->name.data,(Py_ssize_t)step->name.size,"strict");if(!name)return NULL;PyObject *value=PyDict_GetItemWithError(object,name);Py_DECREF(name);if(!value)value=Py_None;Py_INCREF(value);return value;}
    if(step->owner.size==8&&!memcmp(step->owner.data,"$complex",8))return PyObject_GetAttrString(object,step->id==1?"real":"imag");
    Binding *b=binding(p,object);if(!b)return NULL;if(!same(b->owner,step->owner)){PyErr_SetString(PyExc_ValueError,"object type does not match catalogue path");return NULL;}
    for(size_t i=0;i<b->count;++i)if(b->fields[i].id==step->id)return PyObject_GetAttr(object,b->fields[i].name);
    Py_INCREF(Py_None);return Py_None;
}
static int read_object(void *user,const SdlFilterStep *path,size_t count,uint32_t slot,SdlFilterValue *out) {
    Evaluation *evaluation=user;PyObject *value=evaluation->root;Py_INCREF(value);
    for(size_t i=0;i<count&&value!=Py_None;++i){PyObject *next;
        if(path[i].kind==0)next=field_value(evaluation->plan,value,&path[i]);
        else {Py_ssize_t size=PySequence_Size(value);if(size<0){Py_DECREF(value);return -1;}if(path[i].index>=(size_t)size){next=Py_None;Py_INCREF(next);}
            else if(PyList_Check(value)){next=PyList_GET_ITEM(value,(Py_ssize_t)path[i].index);Py_INCREF(next);}
            else if(PyTuple_Check(value)){next=PyTuple_GET_ITEM(value,(Py_ssize_t)path[i].index);Py_INCREF(next);}
            else next=PySequence_GetItem(value,(Py_ssize_t)path[i].index);
        }
        Py_DECREF(value);if(!next)return -1;value=next;
    }
    int status=0;
    if(value==Py_None)out->kind=0;
    else if(PyBool_Check(value)){out->kind=1;out->unsigned_value=value==Py_True;}
    else if(PyLong_Check(value)){int overflow=0;long long signed_value=PyLong_AsLongLongAndOverflow(value,&overflow);if(overflow>0){out->kind=3;out->unsigned_value=PyLong_AsUnsignedLongLong(value);}else if(overflow<0){PyErr_SetString(PyExc_OverflowError,"integer is below int64 minimum");status=-1;}else{out->kind=2;out->signed_value=signed_value;}}
    else if(PyFloat_Check(value)){out->kind=4;out->real=PyFloat_AS_DOUBLE(value);}
    else if(PyUnicode_Check(value)){Py_ssize_t size=0;out->kind=5;out->bytes.data=(const uint8_t *)PyUnicode_AsUTF8AndSize(value,&size);out->bytes.size=(size_t)size;
        if(slot>=128)status=-1;else{Py_XDECREF(evaluation->strings[slot]);evaluation->strings[slot]=value;Py_INCREF(value);}}
    else if(PyComplex_Check(value)){out->kind=8;out->real=PyComplex_RealAsDouble(value);out->imag=PyComplex_ImagAsDouble(value);}
    else if(PySequence_Check(value)){Py_ssize_t n=PySequence_Size(value);if(n<0)status=-1;else{out->kind=6;out->count=(size_t)n;}}
    else if(PyDict_Check(value))out->kind=7;
    else {PyObject *metadata=PyObject_GetAttrString((PyObject *)Py_TYPE(value),"_SDL_FIELDS");
        if(metadata){Py_DECREF(metadata);out->kind=7;}
        else {PyErr_Clear();PyObject *r=PyObject_GetAttrString(value,"real"),*i=PyObject_GetAttrString(value,"imag");
            if(r&&i){out->kind=8;out->real=PyFloat_AsDouble(r);out->imag=PyFloat_AsDouble(i);}else status=-1;Py_XDECREF(r);Py_XDECREF(i);}
    }
    if(PyErr_Occurred())status=-1;
    /* Generated SDL attributes and collection items remain owned by the root. */
    Py_DECREF(value);return status;
}
static PyObject *prepare(PyObject *self,PyObject *args) {
    (void)self;const char *catalogue,*root,*expression;Py_ssize_t cn,rn,en;
    if(!PyArg_ParseTuple(args,"s#s#s#",&catalogue,&cn,&root,&rn,&expression,&en))return NULL;
    SdlFilter *filter=sdl_filter_prepare((SdlFilterBytes){(const uint8_t *)catalogue,(size_t)cn},(SdlFilterBytes){(const uint8_t *)root,(size_t)rn},(SdlFilterBytes){(const uint8_t *)expression,(size_t)en});
    if(!filter){PyErr_SetString(PyExc_ValueError,sdl_filter_error());return NULL;}
    Plan *p=PyMem_Calloc(1,sizeof(*p));if(!p){sdl_filter_free(filter);return PyErr_NoMemory();}p->plan=filter;
    PyObject *capsule=PyCapsule_New(p,"sdl.filter",destroy);if(!capsule){sdl_filter_free(filter);PyMem_Free(p);}return capsule;
}
static PyObject *finish(int result){
    if(PyErr_Occurred()){PyObject *t,*v,*trace;PyErr_Fetch(&t,&v,&trace);PyObject *message=v?PyObject_Str(v):PyUnicode_FromString("object accessor failed");Py_XDECREF(t);Py_XDECREF(v);Py_XDECREF(trace);if(!message)return NULL;PyObject *out=Py_BuildValue("iO",-1,message);Py_DECREF(message);return out;}
    return Py_BuildValue("is",result,result<0?sdl_filter_error():"");
}
static PyObject *evaluate(PyObject *self,PyObject *args){
    (void)self;PyObject *capsule,*object;if(!PyArg_ParseTuple(args,"OO",&capsule,&object))return NULL;
    Plan *p=PyCapsule_GetPointer(capsule,"sdl.filter");if(!p)return NULL;
    if(!PyDict_Check(object)){Binding *b=binding(p,object);if(!b)return finish(-1);if(!same(b->owner,sdl_filter_root(p->plan))){PyErr_SetString(PyExc_ValueError,"object root does not match filter type");return finish(-1);}}
    Evaluation evaluation={p,object,{NULL}};int code=sdl_filter_evaluate_object(p->plan,read_object,&evaluation);
    for(size_t i=0;i<128;++i)Py_XDECREF(evaluation.strings[i]);
    return finish(code);
}
static PyObject *evaluate_wire(PyObject *self,PyObject *args){
    (void)self;PyObject *capsule;Py_buffer buffer;if(!PyArg_ParseTuple(args,"Oy*",&capsule,&buffer))return NULL;
    Plan *p=PyCapsule_GetPointer(capsule,"sdl.filter");if(!p){PyBuffer_Release(&buffer);return NULL;}
    int code=sdl_filter_evaluate_wire(p->plan,(SdlFilterBytes){buffer.buf,(size_t)buffer.len});PyBuffer_Release(&buffer);return finish(code);
}
static PyMethodDef methods[]={{"prepare",prepare,METH_VARARGS,NULL},{"evaluate",evaluate,METH_VARARGS,NULL},{"evaluate_wire",evaluate_wire,METH_VARARGS,NULL},{NULL,NULL,0,NULL}};
static struct PyModuleDef module={PyModuleDef_HEAD_INIT,"_sdl_filter_native",NULL,-1,methods,NULL,NULL,NULL,NULL};
PyMODINIT_FUNC PyInit__sdl_filter_native(void){if(sdl_filter_abi_version()!=1){PyErr_SetString(PyExc_ImportError,"incompatible filter ABI");return NULL;}return PyModule_Create(&module);}
