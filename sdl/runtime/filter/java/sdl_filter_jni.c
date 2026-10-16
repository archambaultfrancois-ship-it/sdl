#include <jni.h>
#include "sdl_filter.h"
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
typedef struct {
    uint32_t id;
    jfieldID field;
    int primitive;
    char *type;
} Field;
typedef struct Binding {
    jclass type;
    char *owner;
    Field *fields;
    size_t count;
    struct Binding *next;
} Binding;
enum {
    C_STRING,C_BOOL,C_NUMBER,C_LIST,C_ARRAYLIST,C_OBJECTARRAY,C_BOOLARRAY,C_BYTEARRAY,C_SHORTARRAY,C_INTARRAY,C_LONGARRAY,C_FLOATARRAY,C_DOUBLEARRAY,C_ENUM,C_COMPLEX32,C_COMPLEX64,C_COMPLEX32ARRAY,C_COMPLEX64ARRAY,C_COUNT
};
typedef struct Handle {
    uint64_t id;
    SdlFilter *plan;
    Binding *bindings;
    int initialized;
    jclass classes[C_COUNT];
    jmethodID list_size,list_get,number_long,number_double,bool_value,enum_value;
    jfieldID list_data,list_count,re32,im32,re64,im64,re32array,im32array,re64array,im64array;
    struct Handle *next;
} Handle;
typedef struct {
    JNIEnv *env;
    Handle *handle;
    jobject root;
    Binding *root_binding;
    char *strings[128];
    uint64_t string_slots[2];
} Evaluation;
static Handle *handles;
static pthread_mutex_t registry_lock=PTHREAD_MUTEX_INITIALIZER;
static uint64_t next_id=1;
static void throw(JNIEnv *e,const char *text) {
    if(!(*e)->ExceptionCheck(e)) {
        jclass c=(*e)->FindClass(e,"java/lang/IllegalArgumentException");
        if(c)(*e)->ThrowNew(e,c,text);
    }
}
static Handle *find(JNIEnv *e,jlong id) {
    pthread_mutex_lock(&registry_lock);
    for(Handle *h=handles;h;h=h->next)if(h->id==(uint64_t)id) {
        pthread_mutex_unlock(&registry_lock);
        return h;
    }
    pthread_mutex_unlock(&registry_lock);
    throw(e,"invalid filter handle");
    return NULL;
}
static SdlFilterBytes array(JNIEnv *e,jbyteArray a,jbyte **data) {
    SdlFilterBytes b= {
        NULL,0
    };
    if(!a) {
        throw(e,"null byte array");
        return b;
    }
    b.size=(size_t)(*e)->GetArrayLength(e,a);
    *data=(*e)->GetByteArrayElements(e,a,NULL);
    b.data=(const uint8_t *)*data;
    return b;
}
static char *utf8(JNIEnv *e,jstring string,size_t *size) {
    if(!string)return NULL;
    jsize n=(*e)->GetStringLength(e,string);
    const jchar *s=(*e)->GetStringChars(e,string,NULL);
    if(!s)return NULL;
    char *out=malloc((size_t)n*3+1);
    if(!out) {
        (*e)->ReleaseStringChars(e,string,s);
        return NULL;
    }
    size_t p=0;
    for(jsize i=0;i<n;++i) {
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
    (*e)->ReleaseStringChars(e,string,s);
    return out;
    invalid:free(out);
    (*e)->ReleaseStringChars(e,string,s);
    return NULL;
}
static int initialize(JNIEnv *e,Handle *h) {
    if(h->initialized)return 0;
    const char *names[C_COUNT]= {
        "java/lang/String","java/lang/Boolean","java/lang/Number","java/util/List","java/util/ArrayList","[Ljava/lang/Object;","[Z","[B","[S","[I","[J","[F","[D","sdl/runtime/SdlCodec$EnumValue","sdl/runtime/SdlCodec$Complex32","sdl/runtime/SdlCodec$Complex64","sdl/runtime/SdlCodec$Complex32Array","sdl/runtime/SdlCodec$Complex64Array"
    };
    for(int i=0;i<C_COUNT;++i) {
        jclass c=(*e)->FindClass(e,names[i]);
        if(!c)return -1;
        h->classes[i]=(*e)->NewGlobalRef(e,c);
        (*e)->DeleteLocalRef(e,c);
        if(!h->classes[i])return -1;
    }
    h->list_size=(*e)->GetMethodID(e,h->classes[C_LIST],"size","()I");
    h->list_get=(*e)->GetMethodID(e,h->classes[C_LIST],"get","(I)Ljava/lang/Object;");
    h->list_data=(*e)->GetFieldID(e,h->classes[C_ARRAYLIST],"elementData","[Ljava/lang/Object;");
    h->list_count=(*e)->GetFieldID(e,h->classes[C_ARRAYLIST],"size","I");
    h->number_long=(*e)->GetMethodID(e,h->classes[C_NUMBER],"longValue","()J");
    h->number_double=(*e)->GetMethodID(e,h->classes[C_NUMBER],"doubleValue","()D");
    h->bool_value=(*e)->GetMethodID(e,h->classes[C_BOOL],"booleanValue","()Z");
    h->enum_value=(*e)->GetMethodID(e,h->classes[C_ENUM],"wireValue","()I");
    h->re32=(*e)->GetFieldID(e,h->classes[C_COMPLEX32],"real","F");
    h->im32=(*e)->GetFieldID(e,h->classes[C_COMPLEX32],"imag","F");
    h->re64=(*e)->GetFieldID(e,h->classes[C_COMPLEX64],"real","D");
    h->im64=(*e)->GetFieldID(e,h->classes[C_COMPLEX64],"imag","D");
    h->re32array=(*e)->GetFieldID(e,h->classes[C_COMPLEX32ARRAY],"re","[F");
    h->im32array=(*e)->GetFieldID(e,h->classes[C_COMPLEX32ARRAY],"im","[F");
    h->re64array=(*e)->GetFieldID(e,h->classes[C_COMPLEX64ARRAY],"re","[D");
    h->im64array=(*e)->GetFieldID(e,h->classes[C_COMPLEX64ARRAY],"im","[D");
    if((*e)->ExceptionCheck(e))return -1;
    h->initialized=1;
    return 0;
}
static Binding *binding(JNIEnv *e,Handle *h,jobject object) {
    for(Binding *b=h->bindings;b;b=b->next)if((*e)->IsInstanceOf(e,object,b->type))return b;
    jclass type=(*e)->GetObjectClass(e,object);
    jfieldID fs=(*e)->GetStaticFieldID(e,type,"SDL_FIELDS","[Lsdl/runtime/SdlCodec$Field;");
    if(!fs)return NULL;
    jfieldID ns=(*e)->GetStaticFieldID(e,type,"SDL_NAME","Ljava/lang/String;");
    if(!fs||!ns)return NULL;
    jobjectArray fields=(*e)->GetStaticObjectField(e,type,fs);
    jstring owner=(*e)->GetStaticObjectField(e,type,ns);
    size_t size;
    char *name=utf8(e,owner,&size);
    if(!fields||!name)return NULL;
    Binding *b=calloc(1,sizeof(*b));
    if(!b) {
        free(name);
        return NULL;
    }
    b->owner=name;
    b->type=(*e)->NewGlobalRef(e,type);
    b->count=(size_t)(*e)->GetArrayLength(e,fields);
    b->fields=calloc(b->count?b->count:1,sizeof(Field));
    if(!b->type||!b->fields) {
        if(b->type)(*e)->DeleteGlobalRef(e,b->type);
        free(b->fields);
        free(name);
        free(b);
        return NULL;
    }
    jclass meta=(*e)->FindClass(e,"sdl/runtime/SdlCodec$Field");
    jfieldID mid=(*e)->GetFieldID(e,meta,"id","J"),mname=(*e)->GetFieldID(e,meta,"name","Ljava/lang/String;"),mtype=(*e)->GetFieldID(e,meta,"typeName","Ljava/lang/String;");
    jclass klass=(*e)->FindClass(e,"java/lang/Class");
    jmethodID get_field=(*e)->GetMethodID(e,klass,"getField","(Ljava/lang/String;)Ljava/lang/reflect/Field;"),get_name=(*e)->GetMethodID(e,klass,"getName","()Ljava/lang/String;");
    jclass reflected=(*e)->FindClass(e,"java/lang/reflect/Field");
    jmethodID get_type=(*e)->GetMethodID(e,reflected,"getType","()Ljava/lang/Class;");
    for(size_t i=0;i<b->count;++i) {
        jobject f=(*e)->GetObjectArrayElement(e,fields,(jsize)i);
        jstring fname=(*e)->GetObjectField(e,f,mname),ftype=(*e)->GetObjectField(e,f,mtype);
        jobject rf=(*e)->CallObjectMethod(e,type,get_field,fname);
        if((*e)->ExceptionCheck(e))goto invalid_binding;
        jobject rt=rf?(*e)->CallObjectMethod(e,rf,get_type):NULL;
        if((*e)->ExceptionCheck(e))goto invalid_binding;
        jstring rn=rt?(*e)->CallObjectMethod(e,rt,get_name):NULL;
        if((*e)->ExceptionCheck(e))goto invalid_binding;
        char *primitive=rn?utf8(e,rn,&size):NULL;
        b->fields[i].id=(uint32_t)(*e)->GetLongField(e,f,mid);
        b->fields[i].field=rf?(*e)->FromReflectedField(e,rf):NULL;
        b->fields[i].type=utf8(e,ftype,&size);
        if(!primitive||!b->fields[i].field||!b->fields[i].type) {
            free(primitive);
            goto invalid_binding;
        }
        if(primitive) {
            const char *pn[]= {
                "","boolean","byte","short","int","long","float","double"
            };
            for(int k=1;k<=7;++k)if(!strcmp(primitive,pn[k]))b->fields[i].primitive=k;
            free(primitive);
        }
        (*e)->DeleteLocalRef(e,f);
        if(fname)(*e)->DeleteLocalRef(e,fname);
        if(ftype)(*e)->DeleteLocalRef(e,ftype);
        if(rf)(*e)->DeleteLocalRef(e,rf);
        if(rt)(*e)->DeleteLocalRef(e,rt);
        if(rn)(*e)->DeleteLocalRef(e,rn);
    }
    b->next=h->bindings;
    h->bindings=b;
    return (*e)->ExceptionCheck(e)?NULL:b;
    invalid_binding: for(size_t i=0;i<b->count;++i)free(b->fields[i].type);
    (*e)->DeleteGlobalRef(e,b->type);
    free(b->fields);
    free(b->owner);
    free(b);
    return NULL;
}
static void integer_value(SdlFilterValue *out,int64_t value,const char *type) {
    if(type&&type[0]=='u') {
        out->kind=3;
        out->unsigned_value=(uint64_t)value;
        if(!strcmp(type,"uint8"))out->unsigned_value&=255;
        else if(!strcmp(type,"uint16"))out->unsigned_value&=65535;
        else if(!strcmp(type,"uint32"))out->unsigned_value&=UINT32_MAX;
    } else {
        out->kind=2;
        out->signed_value=value;
    }
}
static int instance(JNIEnv *e,Handle *h,jobject object,int type) {
    return (*e)->IsInstanceOf(e,object,h->classes[type]);
}
static int collection_size(JNIEnv *e,Handle *h,jobject object) {
    if(instance(e,h,object,C_ARRAYLIST))return (*e)->GetIntField(e,object,h->list_count);
    if(instance(e,h,object,C_LIST))return (*e)->CallIntMethod(e,object,h->list_size);
    if(instance(e,h,object,C_COMPLEX32ARRAY)||instance(e,h,object,C_COMPLEX64ARRAY)) {
        jobject a=(*e)->GetObjectField(e,object,instance(e,h,object,C_COMPLEX32ARRAY)?h->re32array:h->re64array);
        int n=(*e)->GetArrayLength(e,a);
        (*e)->DeleteLocalRef(e,a);
        return n;
    }
    for(int k=C_OBJECTARRAY;k<=C_DOUBLEARRAY;++k)if(instance(e,h,object,k))return (*e)->GetArrayLength(e,object);
    return -1;
}
static int read_object(void *user,const SdlFilterStep *path,size_t length,uint32_t slot,SdlFilterValue *out) {
    Evaluation *ev=user;
    JNIEnv *e=ev->env;
    Handle *h=ev->handle;
    if(length==1&&path[0].kind==0) {
        Binding *b=ev->root_binding;
        Field *f=NULL;
        for(size_t i=0;i<b->count;++i)if(b->fields[i].id==path[0].id) {
            f=&b->fields[i];
            break;
        }
        if(f&&f->primitive) {
            switch(f->primitive) {
            case 1:
                out->kind=1;
                out->unsigned_value=(*e)->GetBooleanField(e,ev->root,f->field);
                break;
            case 2:
                integer_value(out,(*e)->GetByteField(e,ev->root,f->field),f->type);
                break;
            case 3:
                integer_value(out,(*e)->GetShortField(e,ev->root,f->field),f->type);
                break;
            case 4:
                integer_value(out,(*e)->GetIntField(e,ev->root,f->field),f->type);
                break;
            case 5:
                integer_value(out,(*e)->GetLongField(e,ev->root,f->field),f->type);
                break;
            case 6:
                out->kind=4;
                out->real=(*e)->GetFloatField(e,ev->root,f->field);
                break;
            default:
                out->kind=4;
                out->real=(*e)->GetDoubleField(e,ev->root,f->field);
                break;
            }
            return (*e)->ExceptionCheck(e)?-1:0;
        }
    }
    if((*e)->PushLocalFrame(e,64)<0)return -1;
    jobject object=ev->root;
    const char *type=NULL;
    int scalar=0,status=0;
    for(size_t p=0;p<length;++p) {
        const SdlFilterStep *step=&path[p];
        if(scalar) {
            if(out->kind==8&&step->kind==0&&(step->id==1||step->id==2)) {
                out->real=step->id==1?out->real:out->imag;
                out->kind=4;
                continue;
            }
            status=-1;
            break;
        }
        if(!object) {
            out->kind=0;
            scalar=1;
            break;
        }
        if(step->kind==0) {
            if(step->owner.size==8&&!memcmp(step->owner.data,"$complex",8)) {
                if(instance(e,h,object,C_COMPLEX32)) {
                    out->real=(*e)->GetFloatField(e,object,step->id==1?h->re32:h->im32);
                    out->kind=4;
                    scalar=1;
                    continue;
                }
                if(instance(e,h,object,C_COMPLEX64)) {
                    out->real=(*e)->GetDoubleField(e,object,step->id==1?h->re64:h->im64);
                    out->kind=4;
                    scalar=1;
                    continue;
                }
                status=-1;
                break;
            }
            Binding *b=binding(e,h,object);
            if(!b||strlen(b->owner)!=step->owner.size||memcmp(b->owner,step->owner.data,step->owner.size)) {
                status=-1;
                break;
            }
            Field *f=NULL;
            for(size_t i=0;i<b->count;++i)if(b->fields[i].id==step->id) {
                f=&b->fields[i];
                break;
            }
            if(!f) {
                out->kind=0;
                scalar=1;
                break;
            }
            type=f->type;
            switch(f->primitive) {
            case 0:
                {
                    jobject child=(*e)->GetObjectField(e,object,f->field);
                    if(object!=ev->root)(*e)->DeleteLocalRef(e,object);
                    object=child;
                    break;
                }
            case 1:
                out->kind=1;
                out->unsigned_value=(*e)->GetBooleanField(e,object,f->field);
                scalar=1;
                break;
            case 2:
                integer_value(out,(*e)->GetByteField(e,object,f->field),type);
                scalar=1;
                break;
            case 3:
                integer_value(out,(*e)->GetShortField(e,object,f->field),type);
                scalar=1;
                break;
            case 4:
                integer_value(out,(*e)->GetIntField(e,object,f->field),type);
                scalar=1;
                break;
            case 5:
                integer_value(out,(*e)->GetLongField(e,object,f->field),type);
                scalar=1;
                break;
            case 6:
                out->kind=4;
                out->real=(*e)->GetFloatField(e,object,f->field);
                scalar=1;
                break;
            default:
                out->kind=4;
                out->real=(*e)->GetDoubleField(e,object,f->field);
                scalar=1;
                break;
            }
        }else {
            int n=collection_size(e,h,object);
            if((*e)->ExceptionCheck(e)||n<0) {
                status=-1;
                break;
            }
            if(step->index>=(size_t)n) {
                out->kind=0;
                scalar=1;
                break;
            }
            jsize i=(jsize)step->index;
            jobject child=NULL;
            if(instance(e,h,object,C_ARRAYLIST)) {
                jobject a=(*e)->GetObjectField(e,object,h->list_data);
                child=(*e)->GetObjectArrayElement(e,a,i);
                (*e)->DeleteLocalRef(e,a);
            } else if(instance(e,h,object,C_LIST)) {
                child=(*e)->CallObjectMethod(e,object,h->list_get,i);
                if((*e)->ExceptionCheck(e)) {
                    status=-1;
                    break;
                }
            } else if(instance(e,h,object,C_OBJECTARRAY))child=(*e)->GetObjectArrayElement(e,object,i);
            else if(instance(e,h,object,C_COMPLEX32ARRAY)||instance(e,h,object,C_COMPLEX64ARRAY)) {
                int single=instance(e,h,object,C_COMPLEX32ARRAY);
                jobject re=(*e)->GetObjectField(e,object,single?h->re32array:h->re64array),im=(*e)->GetObjectField(e,object,single?h->im32array:h->im64array);
                if(single) {
                    jfloat r,v;
                    (*e)->GetFloatArrayRegion(e,re,i,1,&r);
                    (*e)->GetFloatArrayRegion(e,im,i,1,&v);
                    out->real=r;
                    out->imag=v;
                }else {
                    (*e)->GetDoubleArrayRegion(e,re,i,1,&out->real);
                    (*e)->GetDoubleArrayRegion(e,im,i,1,&out->imag);
                }
                out->kind=8;
                scalar=1;
                (*e)->DeleteLocalRef(e,re);
                (*e)->DeleteLocalRef(e,im);
            }else if(instance(e,h,object,C_BOOLARRAY)) {
                jboolean v;
                (*e)->GetBooleanArrayRegion(e,object,i,1,&v);
                out->kind=1;
                out->unsigned_value=v;
                scalar=1;
            } else if(instance(e,h,object,C_BYTEARRAY)) {
                jbyte v;
                (*e)->GetByteArrayRegion(e,object,i,1,&v);
                integer_value(out,v,type);
                scalar=1;
            } else if(instance(e,h,object,C_SHORTARRAY)) {
                jshort v;
                (*e)->GetShortArrayRegion(e,object,i,1,&v);
                integer_value(out,v,type);
                scalar=1;
            } else if(instance(e,h,object,C_INTARRAY)) {
                jint v;
                (*e)->GetIntArrayRegion(e,object,i,1,&v);
                integer_value(out,v,type);
                scalar=1;
            } else if(instance(e,h,object,C_LONGARRAY)) {
                jlong v;
                (*e)->GetLongArrayRegion(e,object,i,1,&v);
                integer_value(out,v,type);
                scalar=1;
            } else if(instance(e,h,object,C_FLOATARRAY)) {
                jfloat v;
                (*e)->GetFloatArrayRegion(e,object,i,1,&v);
                out->kind=4;
                out->real=v;
                scalar=1;
            } else if(instance(e,h,object,C_DOUBLEARRAY)) {
                (*e)->GetDoubleArrayRegion(e,object,i,1,&out->real);
                out->kind=4;
                scalar=1;
            } else {
                status=-1;
                break;
            }
            if(child) {
                if(object!=ev->root)(*e)->DeleteLocalRef(e,object);
                object=child;
            }
        }
        if((*e)->ExceptionCheck(e)) {
            status=-1;
            break;
        }
    }
    if(!scalar&&status==0) {
        if(!object)out->kind=0;
        else if(instance(e,h,object,C_STRING)) {
            size_t size;
            char *s=utf8(e,object,&size);
            if(!s||slot>=128) {
                free(s);
                status=-1;
            }else {
                if(ev->string_slots[slot/64]&(UINT64_C(1)<<(slot%64)))free(ev->strings[slot]);
                ev->strings[slot]=s;
                ev->string_slots[slot/64]|=UINT64_C(1)<<(slot%64);
                out->kind=5;
                out->bytes.data=(const uint8_t *)s;
                out->bytes.size=size;
            }
        } else if(instance(e,h,object,C_BOOL)) {
            out->kind=1;
            out->unsigned_value=(*e)->CallBooleanMethod(e,object,h->bool_value);
        } else if(instance(e,h,object,C_NUMBER)) {
            if(type&&(!strcmp(type,"fl32")||!strcmp(type,"fl64"))) {
                out->kind=4;
                out->real=(*e)->CallDoubleMethod(e,object,h->number_double);
            }else integer_value(out,(*e)->CallLongMethod(e,object,h->number_long),type);
        } else if(instance(e,h,object,C_ENUM)) {
            out->kind=2;
            out->signed_value=(*e)->CallIntMethod(e,object,h->enum_value);
        } else if(instance(e,h,object,C_COMPLEX32)) {
            out->kind=8;
            out->real=(*e)->GetFloatField(e,object,h->re32);
            out->imag=(*e)->GetFloatField(e,object,h->im32);
        } else if(instance(e,h,object,C_COMPLEX64)) {
            out->kind=8;
            out->real=(*e)->GetDoubleField(e,object,h->re64);
            out->imag=(*e)->GetDoubleField(e,object,h->im64);
        } else {
            int n=collection_size(e,h,object);
            if(n>=0) {
                out->kind=6;
                out->count=(size_t)n;
            }else out->kind=7;
        }
    }
    if((*e)->ExceptionCheck(e))status=-1;
    (*e)->PopLocalFrame(e,NULL);
    return status;
}
JNIEXPORT jlong JNICALL Java_sdl_filter_SdlFilter_nPrepare(JNIEnv *e,jclass c,jbyteArray catalogue,jbyteArray root,jbyteArray expression) {
    (void)c;
    jbyte *a=NULL,*b=NULL,*d=NULL;
    SdlFilterBytes ca=array(e,catalogue,&a),ro=array(e,root,&b),ex=array(e,expression,&d);
    SdlFilter *p=NULL;
    if(!(*e)->ExceptionCheck(e))p=sdl_filter_prepare(ca,ro,ex);
    if(a)(*e)->ReleaseByteArrayElements(e,catalogue,a,JNI_ABORT);
    if(b)(*e)->ReleaseByteArrayElements(e,root,b,JNI_ABORT);
    if(d)(*e)->ReleaseByteArrayElements(e,expression,d,JNI_ABORT);
    if(!p) {
        throw(e,sdl_filter_error());
        return 0;
    }
    Handle *h=calloc(1,sizeof(*h));
    pthread_mutex_lock(&registry_lock);
    if(!h||!next_id) {
        pthread_mutex_unlock(&registry_lock);
        free(h);
        sdl_filter_free(p);
        throw(e,"filter allocation failed");
        return 0;
    }
    h->id=next_id++;
    h->plan=p;
    h->next=handles;
    handles=h;
    pthread_mutex_unlock(&registry_lock);
    return (jlong)h->id;
}
JNIEXPORT jint JNICALL Java_sdl_filter_SdlFilter_nWire(JNIEnv *e,jclass c,jlong id,jbyteArray data,jint size) {
    (void)c;
    Handle *h=find(e,id);
    if(!h)return -1;
    if(!data||size<0) {
        throw(e,"invalid byte array");
        return -1;
    }
    if(size<=256) {
        jbyte small[256];
        (*e)->GetByteArrayRegion(e,data,0,size,small);
        return (*e)->ExceptionCheck(e)?-1:sdl_filter_evaluate_wire(h->plan,(SdlFilterBytes) {
            (const uint8_t *)small,(size_t)size
        });
    }
    jbyte *b=NULL;
    SdlFilterBytes bytes=array(e,data,&b);
    int status=(*e)->ExceptionCheck(e)?-1:sdl_filter_evaluate_wire(h->plan,bytes);
    if(b)(*e)->ReleaseByteArrayElements(e,data,b,JNI_ABORT);
    return status;
}
JNIEXPORT jint JNICALL Java_sdl_filter_SdlFilter_nObject(JNIEnv *e,jclass c,jlong id,jobject root) {
    (void)c;
    Handle *h=find(e,id);
    if(!h||!root||initialize(e,h)<0)return -1;
    if((*e)->PushLocalFrame(e,64)<0)return -1;
    Binding *b=binding(e,h,root);
    SdlFilterBytes expected=sdl_filter_root(h->plan);
    if(!b||strlen(b->owner)!=expected.size||memcmp(b->owner,expected.data,expected.size)) {
        (*e)->PopLocalFrame(e,NULL);
        throw(e,"object root does not match filter type");
        return -1;
    }
    (*e)->PopLocalFrame(e,NULL);
    Evaluation evaluation;
    evaluation.env=e;
    evaluation.handle=h;
    evaluation.root=root;
    evaluation.root_binding=b;
    evaluation.string_slots[0]=0;
    evaluation.string_slots[1]=0;
    int status=sdl_filter_evaluate_object(h->plan,read_object,&evaluation);
    for(size_t word=0;word<2;++word) {
        uint64_t slots=evaluation.string_slots[word];
        while(slots) {
            unsigned bit=(unsigned)__builtin_ctzll(slots);
            free(evaluation.strings[word*64+bit]);
            slots&=slots-1;
        }
    }
    return status;
}
JNIEXPORT jbyteArray JNICALL Java_sdl_filter_SdlFilter_nError(JNIEnv *e,jclass c) {
    (void)c;
    const char *s=sdl_filter_error();
    jsize n=(jsize)strlen(s);
    jbyteArray a=(*e)->NewByteArray(e,n);
    if(a)(*e)->SetByteArrayRegion(e,a,0,n,(const jbyte *)s);
    return a;
}
JNIEXPORT void JNICALL Java_sdl_filter_SdlFilter_nFree(JNIEnv *e,jclass c,jlong id) {
    (void)c;
    pthread_mutex_lock(&registry_lock);
    Handle **at=&handles;
    while(*at&&(*at)->id!=(uint64_t)id)at=&(*at)->next;
    if(!*at) {
        pthread_mutex_unlock(&registry_lock);
        throw(e,"invalid filter handle");
        return;
    }
    Handle *h=*at;
    *at=h->next;
    pthread_mutex_unlock(&registry_lock);
    sdl_filter_free(h->plan);
    while(h->bindings) {
        Binding *b=h->bindings;
        h->bindings=b->next;
        for(size_t i=0;i<b->count;++i)free(b->fields[i].type);
        free(b->fields);
        free(b->owner);
        (*e)->DeleteGlobalRef(e,b->type);
        free(b);
    }
    for(int i=0;i<C_COUNT;++i)if(h->classes[i])(*e)->DeleteGlobalRef(e,h->classes[i]);
    free(h);
}
