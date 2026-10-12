#include "mex.h"
#include "sdl_filter.h"
#include <stdlib.h>
#include <string.h>
typedef struct {
    uint32_t id;
    char *name;
    const mxArray *dims,*terminal;
    int modifier;
    int message;
    int complex_value;
} Field;
typedef struct {
    char *owner;
    Field *fields;
    size_t count;
} Binding;
typedef struct Handle {
    uint64_t id;
    SdlFilter *plan;
    mxArray *metadata;
    Binding *bindings;
    size_t count;
    struct Handle *next;
} Handle;
typedef struct {
    Handle *handle;
    const mxArray *root;
    char *strings[128];
} Evaluation;
static Handle *handles;
static uint64_t next_id=1;
static int initialized;
static void fail(const char *text) {
    mexErrMsgIdAndTxt("SDL:Filter","%s",text);
}
static char *string(const mxArray *a) {
    return a&&mxIsChar(a)?mxArrayToString(a):NULL;
}
static void dispose(Handle *h) {
    sdl_filter_free(h->plan);
    for(size_t i=0;h->bindings&&i<h->count;++i) {
        Binding *b=&h->bindings[i];
        for(size_t j=0;b->fields&&j<b->count;++j)mxFree(b->fields[j].name);
        free(b->fields);
        mxFree(b->owner);
    }
    free(h->bindings);
    if(h->metadata)mxDestroyArray(h->metadata);
    free(h);
}
static void cleanup(void) {
    while(handles) {
        Handle *h=handles;
        handles=h->next;
        dispose(h);
    }
}
static SdlFilterBytes bytes(const mxArray *a) {
    if(!mxIsUint8(a)||mxIsComplex(a)||mxIsSparse(a))fail("expected dense uint8 data");
    return (SdlFilterBytes) {
        mxGetData(a),mxGetNumberOfElements(a)
    };
}
static Handle *find(const mxArray *a) {
    if(!mxIsUint64(a)||mxGetNumberOfElements(a)!=1)fail("invalid filter token");
    uint64_t id=*(uint64_t *)mxGetData(a);
    for(Handle *h=handles;h;h=h->next)if(h->id==id)return h;
    fail("filter is closed or invalid");
    return NULL;
}
static Binding *owner(Handle *h,SdlFilterBytes name) {
    for(size_t i=0;i<h->count;++i)if(strlen(h->bindings[i].owner)==name.size&&!memcmp(h->bindings[i].owner,name.data,name.size))return &h->bindings[i];
    return NULL;
}
static int is_message(Handle *h,const char *name) {
    for(size_t i=0;i<h->count;++i)if(!strcmp(h->bindings[i].owner,name))return 1;
    return 0;
}
static int setup(Handle *h,const mxArray *metadata) {
    if(!mxIsCell(metadata))return -1;
    h->metadata=mxDuplicateArray(metadata);
    mexMakeArrayPersistent(h->metadata);
    h->count=mxGetNumberOfElements(metadata);
    h->bindings=calloc(h->count?h->count:1,sizeof(Binding));
    if(!h->bindings)return -1;
    for(size_t i=0;i<h->count;++i) {
        const mxArray *row=mxGetCell(h->metadata,i);
        if(!row||!mxIsStruct(row)||mxGetNumberOfElements(row)!=1)return -1;
        const mxArray *fs=mxGetField(row,0,"fields");
        Binding *b=&h->bindings[i];
        b->owner=string(mxGetField(row,0,"owner"));
        if(b->owner)mexMakeMemoryPersistent(b->owner);
        if(!b->owner||!fs||!mxIsCell(fs))return -1;
        b->count=mxGetNumberOfElements(fs);
        b->fields=calloc(b->count?b->count:1,sizeof(Field));
        if(!b->fields)return -1;
        for(size_t j=0;j<b->count;++j) {
            const mxArray *f=mxGetCell(fs,j);
            if(!f||!mxIsStruct(f)||mxGetNumberOfElements(f)!=1)return -1;
            Field *field=&b->fields[j];
            const mxArray *id=mxGetField(f,0,"id");
            if(!id)return -1;
            field->id=(uint32_t)mxGetScalar(id);
            field->name=string(mxGetField(f,0,"name"));
            if(field->name)mexMakeMemoryPersistent(field->name);
            field->dims=mxGetField(f,0,"dims");
            field->terminal=mxGetField(f,0,"terminal");
            char *modifier=string(mxGetField(f,0,"modifier"));
            if(!field->name||!modifier)return -1;
            field->modifier=!strcmp(modifier,"optional")?1:!strcmp(modifier,"repeated")?2:!strcmp(modifier,"packed")?3:0;
            mxFree(modifier);
        }
    }
    for(size_t i=0;i<h->count;++i) {
        const mxArray *fs=mxGetField(mxGetCell(h->metadata,i),0,"fields");
        for(size_t j=0;j<h->bindings[i].count;++j) {
            char *name=string(mxGetField(mxGetCell(fs,j),0,"type"));
            h->bindings[i].fields[j].message=name&&is_message(h,name);
            h->bindings[i].fields[j].complex_value=name&&(!strcmp(name,"c32")||!strcmp(name,"c64"));
            mxFree(name);
        }
    }
    return 0;
}
static size_t dim_product(const mxArray *dims) {
    size_t n=1;
    if(dims) {
        double *d=mxGetPr(dims);
        for(size_t i=0;i<mxGetNumberOfElements(dims);++i)n*=(size_t)d[i];
    }
    return n;
}
static const mxArray *get_path(const mxArray *a,const char *path) {
    char name[256];
    const char *p=path;
    while(*p) {
        const char *dot=strchr(p,'.');
        size_t n=dot?(size_t)(dot-p):strlen(p);
        if(n>=sizeof(name)||!mxIsStruct(a))return NULL;
        memcpy(name,p,n);
        name[n]=0;
        a=mxGetField(a,0,name);
        if(!a)return NULL;
        if(!dot)break;
        p=dot+1;
    }
    return a;
}
static int soa_count(const mxArray *value,const mxArray *terminal,size_t *count) {
    if(!terminal||!mxIsCell(terminal)||mxGetNumberOfElements(terminal)==0)return -1;
    *count=SIZE_MAX;
    for(size_t i=0;i<mxGetNumberOfElements(terminal);++i) {
        const mxArray *t=mxGetCell(terminal,i);
        char *path=string(mxGetField(t,0,"path"));
        if(!path)return -1;
        const mxArray *a=get_path(value,path);
        mxFree(path);
        if(!a)return -1;
        size_t size=dim_product(mxGetField(t,0,"dims"));
        size_t n=mxGetNumberOfElements(a);
        if(!size||n%size)return -1;
        n/=size;
        if(*count!=SIZE_MAX&&*count!=n)return -1;
        *count=n;
    }
    return 0;
}
static char *utf8(const mxArray *a,size_t *size) {
#ifdef HAVE_OCTAVE
    *size=mxGetNumberOfElements(a);
    char *copy=malloc(*size+1);
    if(!copy)return NULL;
    memcpy(copy,mxGetChars(a),*size);
    copy[*size]=0;
    return copy;
#else
    size_t n=mxGetNumberOfElements(a);
    const mxChar *s=mxGetChars(a);
    char *out=malloc(n*3+1);
    if(!out)return NULL;
    size_t p=0;
    for(size_t i=0;i<n;++i) {
        uint32_t c=s[i];
        if(c>=0xd800&&c<=0xdbff) {
            if(i+1>=n||s[i+1]<0xdc00||s[i+1]>0xdfff)goto invalid;
            c=0x10000+((c-0xd800)<<10)+(s[++i]-0xdc00);
        }else if(c>=0xdc00&&c<=0xdfff)goto invalid;
        if(c<128)out[p++]=(char)c;
        else if(c<2048) {
            out[p++]=(char)(0xc0|(c>>6));
            out[p++]=(char)(0x80|(c&63));
        }else if(c<65536) {
            out[p++]=(char)(0xe0|(c>>12));
            out[p++]=(char)(0x80|((c>>6)&63));
            out[p++]=(char)(0x80|(c&63));
        }else {
            out[p++]=(char)(0xf0|(c>>18));
            out[p++]=(char)(0x80|((c>>12)&63));
            out[p++]=(char)(0x80|((c>>6)&63));
            out[p++]=(char)(0x80|(c&63));
        }
    }
    out[p]=0;
    *size=p;
    return out;
    invalid:free(out);
    return NULL;
#endif
}
static int scalar(const mxArray *a,size_t index,int complex_value,SdlFilterValue *out) {
    if(index>=mxGetNumberOfElements(a)||mxIsSparse(a))return -1;
    void *data=mxGetData(a);
#define SIGNED(T) do {out->kind=2;out->signed_value=((const T *)data)[index];} while(0)
#define UNSIGNED(T) do {out->kind=3;out->unsigned_value=((const T *)data)[index];} while(0)
    switch(mxGetClassID(a)) {
    case mxLOGICAL_CLASS:
        out->kind=1;
        out->unsigned_value=((const mxLogical *)data)[index]!=0;
        break;
    case mxINT8_CLASS:
        SIGNED(int8_t);
        break;
    case mxINT16_CLASS:
        SIGNED(int16_t);
        break;
    case mxINT32_CLASS:
        SIGNED(int32_t);
        break;
    case mxINT64_CLASS:
        SIGNED(int64_t);
        break;
    case mxUINT8_CLASS:
        UNSIGNED(uint8_t);
        break;
    case mxUINT16_CLASS:
        UNSIGNED(uint16_t);
        break;
    case mxUINT32_CLASS:
        UNSIGNED(uint32_t);
        break;
    case mxUINT64_CLASS:
        UNSIGNED(uint64_t);
        break;
    case mxSINGLE_CLASS:
        out->real=((const float *)data)[index];
        out->kind=complex_value||mxIsComplex(a)?8:4;
        if(out->kind==8){const float *imag=mxGetImagData(a);out->imag=imag?imag[index]:0.0;}
        break;
    case mxDOUBLE_CLASS:
        out->real=((const double *)data)[index];
        out->kind=complex_value||mxIsComplex(a)?8:4;
        if(out->kind==8){const double *imag=mxGetImagData(a);out->imag=imag?imag[index]:0.0;}
        break;
    default:
        return -1;
    }
    return 0;
}
static int read_object(void *user,const SdlFilterStep *path,size_t length,uint32_t slot,SdlFilterValue *out) {
    Evaluation *ev=user;
    const mxArray *a=ev->root;
    const mxArray *dims=NULL,*terminal=NULL;
    size_t axis=0,offset=0,record=SIZE_MAX;
    int collection=0,soa=0,complex_value=0;
    for(size_t p=0;p<length;++p) {
        const SdlFilterStep *s=&path[p];
        if(!a||mxIsEmpty(a)) {
            out->kind=0;
            return 0;
        }
        if(s->kind==0) {
            if(s->owner.size==8&&!memcmp(s->owner.data,"$complex",8)) {
                if(p+1!=length||scalar(a,offset+(record==SIZE_MAX?0:record*dim_product(dims)),complex_value,out)<0||out->kind!=8)return -1;
                out->real=s->id==1?out->real:out->imag;
                out->kind=4;
                return 0;
            }
            if(!mxIsStruct(a)||collection)return -1;
            Binding *b=owner(ev->handle,s->owner);
            if(!b)return -1;
            Field *f=NULL;
            for(size_t i=0;i<b->count;++i)if(b->fields[i].id==s->id) {
                f=&b->fields[i];
                break;
            }
            if(!f)return -1;
            size_t element=0;
            if(mxGetNumberOfElements(a)>1){
                element=offset+(record==SIZE_MAX?0:record*dim_product(dims));
                if(element>=mxGetNumberOfElements(a)){out->kind=0;return 0;}
                // The selected struct owns the child. Consume its index rather
                // than carrying it into another scalar/struct field.
                record=SIZE_MAX;
            }
            const mxArray *child=mxGetField(a,element,f->name);
            if(!child) {
                out->kind=0;
                return 0;
            }
            a=child;
            dims=f->dims;
            axis=0;
            offset=0;
            terminal=f->terminal;
            collection=f->modifier>=2;
            soa=f->modifier==3&&f->message;
            complex_value=f->complex_value;
        }else if(collection) {
            size_t n;
            if(soa) {
                if(soa_count(a,terminal,&n)<0)return -1;
            }else n=mxGetNumberOfElements(a);
            if(s->index>=n) {
                out->kind=0;
                return 0;
            }
            collection=0;
            if(soa)record=s->index;
            else if(mxIsCell(a)) {
                a=mxGetCell(a,s->index);
                record=SIZE_MAX;
            }else offset=s->index;
        }else if(dims&&axis<mxGetNumberOfElements(dims)) {
            size_t n=(size_t)mxGetPr(dims)[axis++];
            if(s->index>=n) {
                out->kind=0;
                return 0;
            }
            offset=offset*n+s->index;
        }else if(mxIsCell(a)) {
            if(s->index>=mxGetNumberOfElements(a)) {
                out->kind=0;
                return 0;
            }
            a=mxGetCell(a,s->index);
            offset=0;
            record=SIZE_MAX;
        } else return -1;
    }
    if(!a) {
        out->kind=0;
        return 0;
    }
    if(collection) {
        out->kind=6;
        if(soa)return soa_count(a,terminal,&out->count);
        out->count=mxGetNumberOfElements(a);
        return 0;
    }
    if(dims&&axis<mxGetNumberOfElements(dims)) {
        out->kind=6;
        out->count=(size_t)mxGetPr(dims)[axis];
        return 0;
    }
    if(mxIsStruct(a)) {
        out->kind=7;
        return 0;
    }
    if(mxIsChar(a)) {
        if(slot>=128)return -1;
        size_t size;
        char *s=utf8(a,&size);
        if(!s)return -1;
        free(ev->strings[slot]);
        ev->strings[slot]=s;
        out->kind=5;
        out->bytes.data=(const uint8_t *)s;
        out->bytes.size=size;
        return 0;
    }
    if(mxIsEmpty(a)) {
        out->kind=0;
        return 0;
    }
    return scalar(a,offset+(record==SIZE_MAX?0:record*dim_product(dims)),complex_value,out);
}
void mexFunction(int nlhs,mxArray *plhs[],int nrhs,const mxArray *prhs[]) {
    if(!initialized) {
        mexAtExit(cleanup);
        initialized=1;
    }
    if(nrhs<1)fail("missing action");
    char *action=string(prhs[0]);
    if(!action)fail("invalid action");
    if(!strcmp(action,"prepare")) {
        if(nrhs!=5||nlhs!=1) {
            mxFree(action);
            fail("prepare expects catalogue, root, expression and bindings");
        }
        SdlFilter *plan=sdl_filter_prepare(bytes(prhs[1]),bytes(prhs[2]),bytes(prhs[3]));
        if(!plan) {
            mxFree(action);
            fail(sdl_filter_error());
        }
        Handle *h=calloc(1,sizeof(*h));
        if(!h) {
            sdl_filter_free(plan);
            mxFree(action);
            fail("allocation failed");
        }
        h->plan=plan;
        if(setup(h,prhs[4])<0) {
            dispose(h);
            mxFree(action);
            fail("invalid generated object metadata");
        }
        h->id=next_id++;
        h->next=handles;
        handles=h;
        mexLock();
        plhs[0]=mxCreateNumericMatrix(1,1,mxUINT64_CLASS,mxREAL);
        *(uint64_t *)mxGetData(plhs[0])=h->id;
    }else if(!strcmp(action,"close")) {
        if(nrhs!=2||nlhs!=0) {
            mxFree(action);
            fail("close expects one filter");
        }
        Handle *h=find(prhs[1]);
        Handle **at=&handles;
        while(*at!=h)at=&(*at)->next;
        *at=h->next;
        dispose(h);
        mexUnlock();
    }else if(!strcmp(action,"wire")||!strcmp(action,"evaluate")) {
        if(nrhs!=3||nlhs!=2) {
            mxFree(action);
            fail("evaluate expects filter and data");
        }
        Handle *h=find(prhs[1]);
        int status;
        if(!strcmp(action,"wire"))status=sdl_filter_evaluate_wire(h->plan,bytes(prhs[2]));
        else {
            Evaluation e= {
                h,prhs[2], {
                    NULL
                }
            };
            status=sdl_filter_evaluate_object(h->plan,read_object,&e);
            for(size_t i=0;i<128;++i)free(e.strings[i]);
        }
        plhs[0]=mxCreateDoubleScalar(status);
        plhs[1]=mxCreateString(status<0?sdl_filter_error():"");
    }else {
        mxFree(action);
        fail("unknown filter action");
    }
    mxFree(action);
}
