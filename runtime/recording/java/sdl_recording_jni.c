/* JNI transfers counted UTF-8 byte arrays, never JNI modified UTF-8 strings. */
#include <jni.h>
#include <stdlib.h>
#include <limits.h>
#include <string.h>
#include "sdl_recording.h"

typedef struct Handle {
    uint64_t id;
    int writer, busy;
    void *pointer;
    struct Handle *next;
} Handle;
static Handle *handles;
static uint64_t next_id = 1;
static void error(JNIEnv *e, const char *message) {
    jclass string_cls, exception_cls;
    jmethodID string_ctor, exception_ctor;
    jbyteArray data;
    jstring encoding;
    jobject text, exception;
    size_t size = strlen(message);
    if ((*e)->ExceptionCheck(e) || (*e)->PushLocalFrame(e, 8) < 0) return;
    string_cls = (*e)->FindClass(e, "java/lang/String");
    exception_cls = (*e)->FindClass(e, "java/io/IOException");
    if (!string_cls || !exception_cls) goto done;
    string_ctor = (*e)->GetMethodID(e, string_cls, "<init>", "([BLjava/lang/String;)V");
    exception_ctor = (*e)->GetMethodID(e, exception_cls, "<init>", "(Ljava/lang/String;)V");
    if (!string_ctor || !exception_ctor) goto done;
    data = (*e)->NewByteArray(e, (jsize)size);
    encoding = (*e)->NewStringUTF(e, "UTF-8");
    if (!data || !encoding) goto done;
    if (size) (*e)->SetByteArrayRegion(e, data, 0, (jsize)size, (const jbyte *)message);
    if ((*e)->ExceptionCheck(e)) goto done;
    text = (*e)->NewObject(e, string_cls, string_ctor, data, encoding);
    if (!text) goto done;
    exception = (*e)->NewObject(e, exception_cls, exception_ctor, text);
    if (exception) (*e)->Throw(e, (jthrowable)exception);
done:
    (*e)->PopLocalFrame(e, NULL);
}
static void check(JNIEnv *e, int status) { if (status < 0) error(e, sdl_recording_error()); }
static Handle *find(JNIEnv *e, jlong id, int writer) {
    Handle *h;
    for (h = handles; h; h = h->next)
        if (h->id == (uint64_t)id && h->writer == writer && !h->busy) return h;
    error(e, "invalid, closed or busy recording handle"); return NULL;
}
typedef struct { jbyteArray array; jbyte *data; SdlRecordingBytes bytes; } Input;
static Input input(JNIEnv *e, jbyteArray array) {
    Input i = { array, NULL, { NULL, 0 } };
    if ((*e)->ExceptionCheck(e)) return i;
    if (!array) { error(e, "null input array"); return i; }
    i.bytes.size = (size_t)(*e)->GetArrayLength(e, array);
    if (i.bytes.size) { i.data = (*e)->GetByteArrayElements(e, array, NULL); i.bytes.data = (uint8_t *)i.data; }
    return i;
}
static void release(JNIEnv *e, Input *i) {
    if (i->data) (*e)->ReleaseByteArrayElements(e, i->array, i->data, JNI_ABORT);
}
static jbyteArray bytes(JNIEnv *e, SdlRecordingBytes value) {
    jbyteArray a;
    if (value.size > INT_MAX) { error(e, "array exceeds Java limit"); return NULL; }
    a = (*e)->NewByteArray(e, (jsize)value.size);
    if (a && value.size) (*e)->SetByteArrayRegion(e, a, 0, (jsize)value.size, (const jbyte *)value.data);
    return a;
}
static jobject record(JNIEnv *e, const SdlRecordingRecord *r) {
    jclass cls, byte_cls;
    jmethodID constructor;
    jbyteArray topic, root, catalogue, payload;
    jobjectArray metadata;
    jobject result = NULL;
    size_t i;
    if ((*e)->PushLocalFrame(e, 16) < 0) return NULL;
    cls = (*e)->FindClass(e, "SdlRecording$Record");
    byte_cls = (*e)->FindClass(e, "[B");
    if (!cls || !byte_cls) goto done;
    constructor = (*e)->GetMethodID(e, cls, "<init>", "(II[B[B[B[B[[BJJJ)V");
    if (!constructor) goto done;
    topic = bytes(e, r->topic); root = bytes(e, r->root_type);
    catalogue = bytes(e, r->catalogue); payload = bytes(e, r->payload);
    metadata = (*e)->NewObjectArray(e, (jsize)(r->metadata_count * 2), byte_cls, NULL);
    if ((*e)->ExceptionCheck(e) || !metadata) goto done;
    for (i = 0; i < r->metadata_count; i++) {
        jbyteArray key = bytes(e, r->metadata[i].key), value = bytes(e, r->metadata[i].value);
        if ((*e)->ExceptionCheck(e)) goto done;
        (*e)->SetObjectArrayElement(e, metadata, (jsize)(i * 2), key);
        (*e)->SetObjectArrayElement(e, metadata, (jsize)(i * 2 + 1), value);
        (*e)->DeleteLocalRef(e, key); (*e)->DeleteLocalRef(e, value);
        if ((*e)->ExceptionCheck(e)) goto done;
    }
    result = (*e)->NewObject(e, cls, constructor, (jint)r->channel_id, (jint)r->schema_id,
        topic, root, catalogue, payload, metadata, (jlong)r->log_time_ns, (jlong)r->publish_time_ns, (jlong)r->sequence);
done:
    return (*e)->PopLocalFrame(e, result);
}
typedef struct { Input *inputs; SdlRecordingMetadata *entries; size_t count; } Meta;
static Meta meta(JNIEnv *e, jobjectArray array) {
    Meta m = { NULL, NULL, 0 };
    jsize n, i;
    if ((*e)->ExceptionCheck(e)) return m;
    if (!array) { error(e, "null metadata"); return m; }
    n = (*e)->GetArrayLength(e, array);
    if (n % 2 || n > 131070) { error(e, "invalid metadata count"); return m; }
    m.count = (size_t)n / 2;
    if (!n || (*e)->EnsureLocalCapacity(e, n + 4) < 0) return m;
    m.inputs = calloc((size_t)n, sizeof(Input)); m.entries = calloc(m.count, sizeof(SdlRecordingMetadata));
    if (!m.inputs || !m.entries) { error(e, "out of memory"); return m; }
    for (i = 0; i < n; i++) {
        jbyteArray a = (jbyteArray)(*e)->GetObjectArrayElement(e, array, i);
        m.inputs[i] = input(e, a);
        if (i % 2) m.entries[i / 2].value = m.inputs[i].bytes;
        else m.entries[i / 2].key = m.inputs[i].bytes;
        if ((*e)->ExceptionCheck(e)) break;
    }
    return m;
}
static void free_meta(JNIEnv *e, Meta *m) {
    size_t i;
    if (m->inputs) for (i = 0; i < m->count * 2; i++) {
        release(e, &m->inputs[i]);
        if (m->inputs[i].array) (*e)->DeleteLocalRef(e, m->inputs[i].array);
    }
    free(m->inputs); free(m->entries);
}
static jlong store(JNIEnv *e, void *pointer, int writer) {
    Handle *h = calloc(1, sizeof(*h));
    if (!h || next_id == 0) {
        free(h); error(e, "recording handle allocation failed");
        if (writer) sdl_recording_writer_close(pointer); else sdl_recording_reader_close(pointer);
        return 0;
    }
    h->id = next_id++; h->writer = writer != 0; h->pointer = pointer; h->next = handles; handles = h;
    return (jlong)h->id;
}
#define JNI(name) JNIEXPORT JNICALL Java_SdlRecording_##name
jlong JNI(nOpen)(JNIEnv *e, jclass cls, jbyteArray path, jboolean writer, jboolean overwrite, jlong chunk) {
    Input p = input(e, path);
    void *pointer = NULL;
    (void)cls;
    if (!(*e)->ExceptionCheck(e)) {
        pointer = writer ? (void *)sdl_recording_writer_open(p.bytes, overwrite ? 1 : 0, (uint64_t)chunk)
                         : (void *)sdl_recording_reader_open(p.bytes);
        if (!pointer) error(e, sdl_recording_error());
    }
    release(e, &p);
    if (!pointer) return 0;
    return store(e, pointer, writer != 0);
}
jlong JNI(nOpenFormat)(JNIEnv *e, jclass cls, jbyteArray path, jboolean overwrite, jlong chunk, jint format) {
    Input p = input(e, path); void *pointer = NULL; (void)cls;
    if (!(*e)->ExceptionCheck(e)) {
        if (format < 0 || format > 1) error(e, "unknown recording format");
        else {
            pointer = sdl_recording_writer_open_format(p.bytes, overwrite ? 1 : 0, (uint64_t)chunk, (uint8_t)format);
            if (!pointer) error(e, sdl_recording_error());
        }
    }
    release(e, &p); return pointer ? store(e, pointer, 1) : 0;
}
jint JNI(nChannel)(JNIEnv *e, jclass cls, jlong id, jbyteArray topic, jbyteArray catalogue, jbyteArray root, jobjectArray metadata) {
    Handle *h = find(e, id, 1);
    Input t = input(e, topic), c = input(e, catalogue), r = input(e, root);
    Meta m = meta(e, metadata); uint16_t out = 0; (void)cls;
    if (h && !(*e)->ExceptionCheck(e)) check(e, sdl_recording_writer_channel(h->pointer, t.bytes, c.bytes, r.bytes, m.entries, m.count, &out));
    release(e, &t); release(e, &c); release(e, &r); free_meta(e, &m); return out;
}
void JNI(nMetadata)(JNIEnv *e, jclass cls, jlong id, jbyteArray name, jobjectArray metadata) {
    Handle *h = find(e, id, 1); Input n = input(e, name); Meta m = meta(e, metadata); (void)cls;
    if (h && !(*e)->ExceptionCheck(e)) check(e, sdl_recording_writer_metadata(h->pointer, n.bytes, m.entries, m.count));
    release(e, &n); free_meta(e, &m);
}
void JNI(nValidatePayload)(JNIEnv *e, jclass cls, jlong id, jboolean flag) {
    Handle *h = find(e, id, 1); (void)cls;
    if (h) check(e, sdl_recording_writer_validate_payload(h->pointer, flag ? 1 : 0));
}
void JNI(nWrite)(JNIEnv *e, jclass cls, jlong id, jint channel, jbyteArray payload, jlong log, jlong publish, jlong sequence) {
    Handle *h = find(e, id, 1); Input p = input(e, payload); (void)cls;
    if (h && !(*e)->ExceptionCheck(e)) check(e, sdl_recording_writer_write(h->pointer, (uint16_t)channel, p.bytes, (uint64_t)log, (uint64_t)publish, (uint32_t)sequence));
    release(e, &p);
}
void JNI(nFlush)(JNIEnv *e, jclass cls, jlong id, jboolean sync) {
    Handle *h = find(e, id, 1); (void)cls;
    if (h) check(e, sdl_recording_writer_flush(h->pointer, sync ? 1 : 0));
}
void JNI(nClose)(JNIEnv *e, jclass cls, jlong id, jboolean writer) {
    Handle *h = find(e, id, writer != 0), **link; int status; (void)cls;
    if (!h) return;
    status = writer ? sdl_recording_writer_close(h->pointer) : sdl_recording_reader_close(h->pointer);
    check(e, status);
    for (link = &handles; *link != h; link = &(*link)->next) {}
    *link = h->next; free(h);
}
void JNI(nFilter)(JNIEnv *e, jclass cls, jlong id, jbyteArray topic, jint flags, jlong start, jlong end) {
    Handle *h = find(e, id, 0); Input t = input(e, topic); (void)cls;
    if (h && !(*e)->ExceptionCheck(e)) check(e, sdl_recording_reader_filter(h->pointer, t.bytes, (uint8_t)flags, (uint64_t)start, (uint64_t)end));
    release(e, &t);
}
jobject JNI(nNext)(JNIEnv *e, jclass cls, jlong id) {
    Handle *h = find(e, id, 0); const SdlRecordingRecord *r = NULL; int status; (void)cls;
    if (!h) return NULL;
    status = sdl_recording_reader_next(h->pointer, &r); check(e, status);
    return status == 1 ? record(e, r) : NULL;
}
typedef struct { JNIEnv *env; jobject consumer; jmethodID accept; } Callback;
static int consume(const SdlRecordingRecord *r, void *userdata) {
    Callback *c = userdata; jobject value = record(c->env, r);
    if (!value) return 1;
    (*c->env)->CallVoidMethod(c->env, c->consumer, c->accept, value);
    (*c->env)->DeleteLocalRef(c->env, value);
    return (*c->env)->ExceptionCheck(c->env) ? 1 : 0;
}
jlong JNI(nReplay)(JNIEnv *e, jclass cls, jlong id, jdouble speed, jobject consumer) {
    Handle *h = find(e, id, 0); Callback c; jclass consumer_cls; uint64_t count = 0; int status; (void)cls;
    if (!h || !consumer) { if (!consumer) error(e, "null consumer"); return 0; }
    consumer_cls = (*e)->GetObjectClass(e, consumer);
    c.env = e; c.consumer = consumer; c.accept = (*e)->GetMethodID(e, consumer_cls, "accept", "(LSdlRecording$Record;)V");
    if (!c.accept) return 0;
    h->busy = 1;
    status = sdl_recording_replay(h->pointer, speed, consume, &c, &count);
    h->busy = 0; check(e, status); return (jlong)count;
}
jlong JNI(nValidate)(JNIEnv *e, jclass cls, jbyteArray path) {
    Input p = input(e, path); uint64_t count = 0; (void)cls;
    if (!(*e)->ExceptionCheck(e)) check(e, sdl_recording_validate(p.bytes, &count));
    release(e, &p); return (jlong)count;
}
jlong JNI(nNow)(JNIEnv *e, jclass cls) {
    uint64_t out = 0; (void)cls; check(e, sdl_recording_now(&out)); return (jlong)out;
}

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM *vm, void *reserved) {
    (void)vm; (void)reserved;
    return sdl_recording_abi_version() == SDL_RECORDING_ABI_VERSION ? JNI_VERSION_1_6 : JNI_ERR;
}
JNIEXPORT void JNICALL JNI_OnUnload(JavaVM *vm, void *reserved) {
    (void)vm; (void)reserved;
    while (handles) {
        Handle *h = handles; handles = h->next;
        if (h->writer) sdl_recording_writer_close(h->pointer); else sdl_recording_reader_close(h->pointer);
        free(h);
    }
}
