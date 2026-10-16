/* JNI owns Java references; all networking and admission remain in the Rust SDK. */
#include <jni.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "chain_client_raw.h"
static JavaVM *vm;
JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM *jvm, void *reserved) {
    (void)reserved; vm = jvm; return JNI_VERSION_1_6;
}
static JNIEnv *attach(int *attached) {
    JNIEnv *env = NULL; *attached = 0;
    if ((*vm)->GetEnv(vm, (void **)&env, JNI_VERSION_1_6) == JNI_EDETACHED) {
        if ((*vm)->AttachCurrentThread(vm, (void **)&env, NULL) != JNI_OK) return NULL;
        *attached = 1;
    }
    return env;
}
static void detach(int attached) { if (attached) (*vm)->DetachCurrentThread(vm); }
static jbyteArray bytes(JNIEnv *env, const void *data, size_t size) {
    if (size > INT32_MAX) return NULL;
    jbyteArray b = (*env)->NewByteArray(env, (jsize)size);
    if (b && size) (*env)->SetByteArrayRegion(env, b, 0, (jsize)size, data);
    return b;
}
static char *copy(JNIEnv *env, jbyteArray a) {
    if (!a) return NULL;
    jsize n = (*env)->GetArrayLength(env, a);
    char *s = malloc((size_t)n + 1);
    if (!s) { (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/OutOfMemoryError"), "native string"); return NULL; }
    (*env)->GetByteArrayRegion(env, a, 0, n, (jbyte *)s); s[n] = 0; return s;
}
static void error(JNIEnv *env) {
    if ((*env)->ExceptionCheck(env)) return;
    /* Construct from UTF-8 bytes, not JNI's modified UTF-8. */
    jclass string = (*env)->FindClass(env, "java/lang/String");
    jmethodID ctor = (*env)->GetMethodID(env, string, "<init>", "([BLjava/lang/String;)V");
    const char *message = cenv_last_error();
    jbyteArray b = bytes(env, message, strlen(message));
    jstring charset = (*env)->NewStringUTF(env, "UTF-8");
    jobject text = (*env)->NewObject(env, string, ctor, b, charset);
    jclass ex = (*env)->FindClass(env, "java/io/IOException");
    jmethodID make = (*env)->GetMethodID(env, ex, "<init>", "(Ljava/lang/String;)V");
    jthrowable exception = (*env)->NewObject(env, ex, make, text);
    if (exception) (*env)->Throw(env, exception);
}
#define JNI_NAME(n) Java_org_sdlchain_client_Client_00024Native_##n
JNIEXPORT jlong JNICALL JNI_NAME(connect)(JNIEnv *env, jclass cls, jbyteArray host, jint port, jbyteArray id) {
    (void)cls; char *h = copy(env, host), *i = copy(env, id); CenvClient *c = NULL;
    if (h && i) c = cenv_connect(h, (uint16_t)port, i);
    free(h); free(i); if (!c) error(env); return (jlong)(intptr_t)c;
}
JNIEXPORT jbyteArray JNICALL JNI_NAME(call)(JNIEnv *env, jclass cls, jlong handle, jint op, jbyteArray a, jbyteArray b, jbyteArray c) {
    (void)cls; void *p = (void *)(intptr_t)handle;
    char *x = copy(env, a), *y = copy(env, b), *z = copy(env, c), *reply = NULL;
    jbyteArray result = NULL; int ok = 1; unsigned char flag = 0;
    if (!x || !y || !z) { free(x); free(y); free(z); return NULL; }
    switch (op) {
    case 0: reply = cenv_list_topics(p); ok = reply != NULL; break;
    case 1: ok = cenv_declare(p, x, y, z); break;
    case 2: ok = cenv_release_publisher(p, x); break;
    case 3: reply = cenv_publish_envelope(p, x, (uint8_t *)y, (size_t)(*env)->GetArrayLength(env, b)); ok = reply != NULL; break;
    case 4: ok = cenv_run(p); break;
    case 5: cenv_close(p); break;
    case 6: ok = cenv_unsubscribe(p); break;
    case 7:
        if ((*env)->GetArrayLength(env, a) != 4) { ok = 0; cenv_set_error("argument", "timeout size"); break; }
        flag = (unsigned char)cenv_wait_closed(p, ((uint32_t)(uint8_t)x[0]<<24)|((uint32_t)(uint8_t)x[1]<<16)|((uint32_t)(uint8_t)x[2]<<8)|(uint8_t)x[3]);
        result = bytes(env, &flag, 1); break;
    case 8: reply = cenv_subscription_stats(p); ok = reply != NULL; break;
    default: ok = 0; cenv_set_error("argument", "unknown JNI operation");
    }
    if (!ok) error(env);
    else if (!result) result = bytes(env, reply, reply ? strlen(reply) : 0);
    if (reply) cenv_string_free(reply);
    free(x); free(y); free(z); return result;
}
JNIEXPORT void JNICALL JNI_NAME(free)(JNIEnv *env, jclass cls, jlong handle, jboolean subscription) {
    (void)env; (void)cls;
    if (subscription) cenv_subscription_free((void *)(intptr_t)handle);
    else cenv_client_free((void *)(intptr_t)handle);
}
typedef struct { jobject state; jmethodID decode, deliver, failed; } Hooks;
static int failed(JNIEnv *env, Hooks *h) {
    jthrowable e = (*env)->ExceptionOccurred(env);
    if (!e) return 0;
    (*env)->ExceptionClear(env); (*env)->CallVoidMethod(env, h->state, h->failed, e);
    if ((*env)->ExceptionCheck(env)) (*env)->ExceptionClear(env);
    return 1;
}
static void *decode(void *user, const char *cat, const uint8_t *data, size_t size) {
    Hooks *h = user; int attached; JNIEnv *env = attach(&attached); if (!env) return NULL;
    jobject value = NULL;
    if ((*env)->PushLocalFrame(env, 16) == 0) {
        jbyteArray catalogue = bytes(env, cat, strlen(cat)), wire = bytes(env, data, size);
        if (!(*env)->ExceptionCheck(env)) {
            jobject local = (*env)->CallObjectMethod(env, h->state, h->decode, catalogue, wire);
            if (!failed(env, h) && local) value = (*env)->NewGlobalRef(env, local);
        }
        failed(env, h); (*env)->PopLocalFrame(env, NULL);
    } else failed(env, h);
    detach(attached); return value;
}
static void free_value(void *value) {
    int attached; JNIEnv *env = attach(&attached);
    if (env) { (*env)->DeleteGlobalRef(env, value); detach(attached); }
}
static int deliver(void *user, const void *value, const CenvMessageInfo *info) {
    Hooks *h = user; int attached, ok = 0; JNIEnv *env = attach(&attached); if (!env) return 0;
    if ((*env)->PushLocalFrame(env, 16) == 0) {
        jbyteArray id = bytes(env, info->publication_id, strlen(info->publication_id));
        jbyteArray topic = bytes(env, info->topic, strlen(info->topic)), epoch = bytes(env, info->epoch, strlen(info->epoch));
        if (!(*env)->ExceptionCheck(env)) (*env)->CallVoidMethod(env, h->state, h->deliver, (jobject)value, id, topic, epoch, (jlong)info->sequence);
        ok = !failed(env, h); (*env)->PopLocalFrame(env, NULL);
    } else failed(env, h);
    detach(attached); return ok;
}
static void destroy(void *user) {
    Hooks *h = user; if (!h) return;
    int attached; JNIEnv *env = attach(&attached);
    if (env) { (*env)->DeleteGlobalRef(env, h->state); detach(attached); } free(h);
}
JNIEXPORT jlong JNICALL JNI_NAME(subscribe)(JNIEnv *env, jclass cls, jlong client, jbyteArray topic, jbyteArray root, jbyteArray cat, jbyteArray options, jobject state) {
    (void)cls; char *t = copy(env, topic), *r = copy(env, root), *c = copy(env, cat), *o = copy(env, options);
    Hooks *h = calloc(1, sizeof(*h)); CenvSubscription *sub = NULL;
    if (h && t && r && c && o) {
        jclass type = (*env)->GetObjectClass(env, state);
        h->state = (*env)->NewGlobalRef(env, state);
        h->decode = (*env)->GetMethodID(env, type, "decode", "([B[B)Ljava/lang/Object;");
        h->deliver = (*env)->GetMethodID(env, type, "deliver", "(Ljava/lang/Object;[B[B[BJ)V");
        h->failed = (*env)->GetMethodID(env, type, "failed", "(Ljava/lang/Throwable;)V");
        if (h->state && h->decode && h->deliver && h->failed && !(*env)->ExceptionCheck(env)) {
            CenvHooks hooks = {h, decode, free_value, deliver, destroy};
            sub = cenv_subscribe_raw((void *)(intptr_t)client, t, r, c, o, hooks); h = NULL;
        }
    }
    if (h) destroy(h);
    free(t); free(r); free(c); free(o); if (!sub) error(env); return (jlong)(intptr_t)sub;
}
