/* C99 gateway for MATLAB R2017a and Octave, using the optional recording ABI. */
#include "mex.h"
#include "sdl_recording.h"
#include <stdlib.h>
#include <string.h>

typedef struct Handle {
    uint64_t id; int writer, busy; void *pointer; struct Handle *next;
} Handle;
static Handle *handles;
static uint64_t next_id = 1;
static int initialized;
static void fail(const char *message) { mexErrMsgIdAndTxt("SDL:Recording", "%s", message); }
static void check(int status) { if (status < 0) fail(sdl_recording_error()); }
static void cleanup(void) {
    while (handles) {
        Handle *h = handles; handles = h->next;
        if (h->writer) sdl_recording_writer_close(h->pointer); else sdl_recording_reader_close(h->pointer);
        free(h);
    }
}
static void arity(int nrhs, int want, int nlhs, int out) {
    if (nrhs != want || nlhs != out) fail("incorrect input/output count");
}
static SdlRecordingBytes bytes(const mxArray *a) {
    SdlRecordingBytes b;
    if (!mxIsUint8(a) || mxIsComplex(a) || mxIsSparse(a)) fail("expected dense uint8 bytes (text is UTF-8)");
    b.data = mxGetData(a); b.size = mxGetNumberOfElements(a); return b;
}
static uint64_t number(const mxArray *a, mxClassID cls) {
    if (mxGetClassID(a) != cls || mxIsComplex(a) || mxIsSparse(a) || mxGetNumberOfElements(a) != 1)
        fail("incorrect integer type or non-scalar input");
    switch (cls) {
    case mxUINT64_CLASS: return *(uint64_t *)mxGetData(a);
    case mxUINT32_CLASS: return *(uint32_t *)mxGetData(a);
    case mxUINT16_CLASS: return *(uint16_t *)mxGetData(a);
    case mxUINT8_CLASS: return *(uint8_t *)mxGetData(a);
    default: fail("unsupported integer type"); return 0;
    }
}
static uint8_t boolean(const mxArray *a) {
    if (!mxIsLogical(a) || mxGetNumberOfElements(a) != 1) fail("expected scalar logical");
    return mxIsLogicalScalarTrue(a) ? 1 : 0;
}
static mxArray *integer(uint64_t value, mxClassID cls) {
    mxArray *a = mxCreateNumericMatrix(1, 1, cls, mxREAL);
    switch (cls) {
    case mxUINT64_CLASS: *(uint64_t *)mxGetData(a) = value; break;
    case mxUINT32_CLASS: *(uint32_t *)mxGetData(a) = (uint32_t)value; break;
    case mxUINT16_CLASS: *(uint16_t *)mxGetData(a) = (uint16_t)value; break;
    default: fail("unsupported output integer type");
    }
    return a;
}
static Handle *find(const mxArray *token, int kind) {
    uint64_t id = number(token, mxUINT64_CLASS); Handle *h;
    for (h = handles; h; h = h->next)
        if (h->id == id && (kind < 0 || kind == h->writer) && !h->busy) return h;
    fail("invalid, closed or busy recording handle"); return NULL;
}
static mxArray *add(void *pointer, int writer) {
    Handle *h;
    if (!pointer) fail(sdl_recording_error());
    h = calloc(1, sizeof(*h));
    if (!h || !next_id) {
        free(h);
        if (writer) sdl_recording_writer_close(pointer); else sdl_recording_reader_close(pointer);
        fail("cannot allocate recording handle");
    }
    h->id = next_id++; h->writer = writer; h->pointer = pointer; h->next = handles; handles = h;
    mexLock(); return integer(h->id, mxUINT64_CLASS);
}
static mxArray *copy_bytes(SdlRecordingBytes b) {
    mxArray *a = mxCreateNumericMatrix(1, b.size, mxUINT8_CLASS, mxREAL);
    if (b.size) memcpy(mxGetData(a), b.data, b.size);
    return a;
}
static mxArray *record(const SdlRecordingRecord *r) {
    const char *names[] = { "channel_id", "schema_id", "topic", "root_type", "catalogue", "payload",
                           "metadata", "log_time_ns", "publish_time_ns", "sequence" };
    mxArray *a = mxCreateStructMatrix(1, 1, 10, names);
    mxArray *metadata = mxCreateCellMatrix(r->metadata_count, 2); size_t i;
    mxSetField(a, 0, "channel_id", integer(r->channel_id, mxUINT16_CLASS));
    mxSetField(a, 0, "schema_id", integer(r->schema_id, mxUINT16_CLASS));
    mxSetField(a, 0, "topic", copy_bytes(r->topic));
    mxSetField(a, 0, "root_type", copy_bytes(r->root_type));
    mxSetField(a, 0, "catalogue", copy_bytes(r->catalogue));
    mxSetField(a, 0, "payload", copy_bytes(r->payload));
    for (i = 0; i < r->metadata_count; i++) {
        mxSetCell(metadata, i, copy_bytes(r->metadata[i].key));
        mxSetCell(metadata, i + r->metadata_count, copy_bytes(r->metadata[i].value));
    }
    mxSetField(a, 0, "metadata", metadata);
    mxSetField(a, 0, "log_time_ns", integer(r->log_time_ns, mxUINT64_CLASS));
    mxSetField(a, 0, "publish_time_ns", integer(r->publish_time_ns, mxUINT64_CLASS));
    mxSetField(a, 0, "sequence", integer(r->sequence, mxUINT32_CLASS)); return a;
}
static SdlRecordingMetadata *metadata(const mxArray *a, size_t *count) {
    SdlRecordingMetadata *values; size_t i;
    if (!mxIsCell(a) || mxGetN(a) != 2 || mxGetNumberOfDimensions(a) != 2 || mxGetM(a) > 65535)
        fail("metadata must be an N-by-2 cell array of UTF-8 uint8 arrays");
    *count = mxGetM(a);
    /* Validate before allocating: mexErrMsg does not unwind C allocations. */
    for (i = 0; i < *count * 2; i++) {
        const mxArray *v = mxGetCell(a, i);
        if (!v) fail("empty metadata cell");
        bytes(v);
    }
    values = mxCalloc(*count, sizeof(*values));
    for (i = 0; i < *count; i++) {
        values[i].key = bytes(mxGetCell(a, i)); values[i].value = bytes(mxGetCell(a, i + *count));
    }
    return values;
}
typedef struct { mxArray *consumer, *exception; } Callback;
static int consume(const SdlRecordingRecord *r, void *userdata) {
    Callback *c = userdata; mxArray *args[2];
    args[0] = c->consumer; args[1] = record(r);
    {
        mxArray *failure = NULL;
        mxArray *trap = mexCallMATLABWithTrap(1, &failure, 2, args, "sdl_recording_callback");
        mxDestroyArray(args[1]);
        if (trap) {
            if (failure) mxDestroyArray(failure);
            c->exception = trap; return 1;
        }
        if (!failure) return 1;
        if (!mxIsEmpty(failure)) { c->exception = failure; return 1; }
        mxDestroyArray(failure); return 0;
    }
}
void mexFunction(int nlhs, mxArray *plhs[], int nrhs, const mxArray *prhs[]) {
    char command[32]; Handle *h; int status;
    if (!initialized) {
        if (sdl_recording_abi_version() != SDL_RECORDING_ABI_VERSION) fail("incompatible recording ABI");
        mexAtExit(cleanup); initialized = 1;
    }
    if (!nrhs || !mxIsChar(prhs[0]) || mxGetNumberOfElements(prhs[0]) >= sizeof(command)
        || mxGetString(prhs[0], command, sizeof(command))) fail("expected command");
    if (!strcmp(command, "open_writer")) {
        SdlRecordingBytes path; uint8_t overwrite; uint64_t chunk;
        arity(nrhs, 4, nlhs, 1); path = bytes(prhs[1]); overwrite = boolean(prhs[2]); chunk = number(prhs[3], mxUINT64_CLASS);
        plhs[0] = add(sdl_recording_writer_open(path, overwrite, chunk), 1);
    } else if (!strcmp(command, "open_writer_format")) {
        SdlRecordingBytes path, format; uint8_t overwrite, code; uint64_t chunk;
        arity(nrhs, 5, nlhs, 1); path = bytes(prhs[1]); overwrite = boolean(prhs[2]); chunk = number(prhs[3], mxUINT64_CLASS);
        format = bytes(prhs[4]);
        if (format.size == 4 && !memcmp(format.data, "mcap", 4)) code = SDL_RECORDING_FORMAT_MCAP;
        else if (format.size == 6 && !memcmp(format.data, "pcapng", 6)) code = SDL_RECORDING_FORMAT_PCAPNG;
        else { fail("format must be mcap or pcapng"); return; }
        plhs[0] = add(sdl_recording_writer_open_format(path, overwrite, chunk, code), 1);
    } else if (!strcmp(command, "open_reader")) {
        arity(nrhs, 2, nlhs, 1); plhs[0] = add(sdl_recording_reader_open(bytes(prhs[1])), 0);
    } else if (!strcmp(command, "channel")) {
        SdlRecordingBytes topic, catalogue, root; SdlRecordingMetadata *m; size_t count; uint16_t channel = 0;
        arity(nrhs, 6, nlhs, 1); h = find(prhs[1], 1);
        topic = bytes(prhs[2]); catalogue = bytes(prhs[3]); root = bytes(prhs[4]); m = metadata(prhs[5], &count);
        status = sdl_recording_writer_channel(h->pointer, topic, catalogue, root, m, count, &channel);
        mxFree(m); check(status); plhs[0] = integer(channel, mxUINT16_CLASS);
    } else if (!strcmp(command, "metadata")) {
        SdlRecordingBytes name; SdlRecordingMetadata *m; size_t count;
        arity(nrhs, 4, nlhs, 0); h = find(prhs[1], 1); name = bytes(prhs[2]); m = metadata(prhs[3], &count);
        status = sdl_recording_writer_metadata(h->pointer, name, m, count); mxFree(m); check(status);
    } else if (!strcmp(command, "validate_payload")) {
        arity(nrhs, 3, nlhs, 0); h = find(prhs[1], 1);
        check(sdl_recording_writer_validate_payload(h->pointer, boolean(prhs[2])));
    } else if (!strcmp(command, "write_encoded")) {
        uint16_t channel; SdlRecordingBytes payload; uint64_t log, publish; uint32_t sequence;
        arity(nrhs, 7, nlhs, 0); h = find(prhs[1], 1); channel = (uint16_t)number(prhs[2], mxUINT16_CLASS);
        payload = bytes(prhs[3]); log = number(prhs[4], mxUINT64_CLASS); publish = number(prhs[5], mxUINT64_CLASS);
        sequence = (uint32_t)number(prhs[6], mxUINT32_CLASS);
        check(sdl_recording_writer_write(h->pointer, channel, payload, log, publish, sequence));
    } else if (!strcmp(command, "flush")) {
        arity(nrhs, 3, nlhs, 0); h = find(prhs[1], 1); check(sdl_recording_writer_flush(h->pointer, boolean(prhs[2])));
    } else if (!strcmp(command, "close")) {
        Handle **link; char error[1024];
        arity(nrhs, 2, nlhs, 0); h = find(prhs[1], -1);
        status = h->writer ? sdl_recording_writer_close(h->pointer) : sdl_recording_reader_close(h->pointer);
        strncpy(error, sdl_recording_error(), sizeof(error) - 1); error[sizeof(error) - 1] = 0;
        for (link = &handles; *link != h; link = &(*link)->next) {}
        *link = h->next; free(h); mexUnlock(); if (status < 0) fail(error);
    } else if (!strcmp(command, "filter")) {
        SdlRecordingBytes topic; uint8_t flags; uint64_t start, end;
        arity(nrhs, 6, nlhs, 0); h = find(prhs[1], 0); topic = bytes(prhs[2]); flags = (uint8_t)number(prhs[3], mxUINT8_CLASS);
        start = number(prhs[4], mxUINT64_CLASS); end = number(prhs[5], mxUINT64_CLASS);
        check(sdl_recording_reader_filter(h->pointer, topic, flags, start, end));
    } else if (!strcmp(command, "next")) {
        const SdlRecordingRecord *r = NULL;
        arity(nrhs, 2, nlhs, 1); h = find(prhs[1], 0); status = sdl_recording_reader_next(h->pointer, &r);
        check(status); plhs[0] = status ? record(r) : mxCreateDoubleMatrix(0, 0, mxREAL);
    } else if (!strcmp(command, "validate")) {
        uint64_t count = 0; arity(nrhs, 2, nlhs, 1);
        check(sdl_recording_validate(bytes(prhs[1]), &count)); plhs[0] = integer(count, mxUINT64_CLASS);
    } else if (!strcmp(command, "now")) {
        uint64_t now = 0; arity(nrhs, 1, nlhs, 1); check(sdl_recording_now(&now)); plhs[0] = integer(now, mxUINT64_CLASS);
    } else if (!strcmp(command, "replay")) {
        Callback c; uint64_t count = 0; double speed;
        arity(nrhs, 4, nlhs, 2); h = find(prhs[1], 0);
        if (!mxIsDouble(prhs[2]) || mxIsComplex(prhs[2]) || mxGetNumberOfElements(prhs[2]) != 1)
            fail("speed must be a scalar double");
        if (!mxIsClass(prhs[3], "function_handle")) fail("expected consumer function handle");
        speed = mxGetScalar(prhs[2]); c.consumer = (mxArray *)prhs[3]; c.exception = NULL;
        h->busy = 1; status = sdl_recording_replay(h->pointer, speed, consume, &c, &count); h->busy = 0;
        if (!c.exception) check(status);
        plhs[0] = integer(count, mxUINT64_CLASS);
        plhs[1] = c.exception ? c.exception : mxCreateDoubleMatrix(0, 0, mxREAL);
    } else fail("unknown recording command");
}
