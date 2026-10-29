#include "ose_callbacks.h"
#include "ose_component.h"
#include "type_engine.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char *name, *root;
    SdlContext *encoder;
    int active;
} RecordingPoint;
typedef struct {
    SdlContext *codec;
    char *catalogue;
    size_t catalogue_size;
    void *message;
    int present;
} Input;
typedef struct {
    const OsePortSpec *inputs, *outputs;
    size_t input_count, output_count;
    Input *selected;
    SdlContext **encoders;
    const OseParameterSpec *parameters;
    size_t parameter_count;
    Input *parameter_values;
    SdlContext **parameter_codecs;
    int active, failed, parameter_writes;
    RecordingPoint recording[256];
    size_t recording_count, recording_bytes;
    int ready;
} Runtime;

static RecordingPoint *recording_slot(Runtime *runtime, const char *name) {
    uint32_t hash = 2166136261u;
    const unsigned char *p = (const unsigned char *)name;
    size_t index;
    while (*p) { hash ^= *p++; hash *= 16777619u; }
    index = hash % 256;
    while (runtime->recording[index].name && strcmp(runtime->recording[index].name, name))
        index = (index + 1) % 256;
    return &runtime->recording[index];
}
static int recording_name(const char *name) {
    const unsigned char *p = (const unsigned char *)name;
    int first = 1;
    if (!name || !*name || strlen(name) > 200) return 0;
    for (; *p; ++p) {
        if (*p == '.') { if (first) return 0; first = 1; continue; }
        if (!((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') || *p == '_' ||
              (!first && *p >= '0' && *p <= '9'))) return 0;
        first = 0;
    }
    return !first;
}
static char *recording_copy(const char *text) {
    size_t size = strlen(text) + 1;
    char *copy = malloc(size);
    if (copy) memcpy(copy, text, size);
    return copy;
}
int ose_declare_recording_value(OseContext *ctx, const char *name, const char *root,
                               const char *catalogue, const char *description) {
    Runtime *runtime = ctx->private_runtime;
    RecordingPoint *point;
    if (runtime->ready || !runtime->parameter_writes || !recording_name(name) || !root || !catalogue ||
        !description || strlen(description) > 1024 || runtime->recording_count >= 128)
        return ose_fail(ctx, "invalid recording declaration");
    point = recording_slot(runtime, name);
    if (point->name) return ose_fail(ctx, "duplicate recording point");
    point->name = recording_copy(name); point->root = recording_copy(root);
    point->encoder = type_prepare(catalogue, strlen(catalogue));
    if (!point->name || !point->root || !point->encoder ||
        !ose_declare_recording(name, root, catalogue, description))
        return ose_fail(ctx, "recording declaration failed");
    runtime->recording_count++;
    return 1;
}
int ose_recording_enabled(OseContext *ctx, const char *name) {
    RecordingPoint *point;
    if (!recording_name(name)) return ose_fail(ctx, "invalid recording name");
    point = recording_slot(ctx->private_runtime, name);
    if (!point->name) return ose_fail(ctx, "unknown recording point");
    return point->active;
}
OseRecordResult ose_record_value(OseContext *ctx, const char *name, const char *root, const void *value) {
    Runtime *runtime = ctx->private_runtime;
    RecordingPoint *point;
    size_t size, overhead;
    void *payload;
    int ok;
    if (!runtime->active || !recording_name(name) || !root || !value) goto invalid;
    point = recording_slot(runtime, name);
    if (!point->name || strcmp(point->root, root)) goto invalid;
    if (!point->active) return OSE_RECORD_DISABLED;
    overhead = strlen(name) + 2;
    if (runtime->recording_bytes + overhead >= 256 * 1024) return OSE_RECORD_DROPPED;
    payload = type_encode(point->encoder, root, value, &size);
    if (!payload) goto invalid;
    if (size > 256 * 1024 - runtime->recording_bytes - overhead) {
        type_free(payload); return OSE_RECORD_DROPPED;
    }
    ok = ose_record_bytes(name, payload, size); type_free(payload);
    if (!ok) goto invalid;
    runtime->recording_bytes += size + overhead;
    return OSE_RECORD_QUEUED;
invalid:
    ose_fail(ctx, "invalid recording emission"); return OSE_RECORD_ERROR;
}
static int recording_update(OseContext *ctx, const OseEvent *event) {
    Runtime *runtime = ctx->private_runtime;
    size_t i, offset = 0;
    char name[201];
    for (i = 0; i < 256; ++i) runtime->recording[i].active = 0;
    while (offset < event->size) {
        size_t end = offset;
        RecordingPoint *point;
        while (end < event->size && event->data[end]) ++end;
        if (end == offset || end - offset > 200 || (end < event->size && end + 1 == event->size)) return 0;
        memcpy(name, event->data + offset, end - offset); name[end - offset] = 0;
        point = recording_slot(runtime, name);
        if (!point->name || point->active) return 0;
        point->active = 1; offset = end + 1;
    }
    return 1;
}

double ose_now(const OseContext *ctx) { return (double)ctx->time_ns / 1000000000.0; }
uint64_t ose_time_ns(const OseContext *ctx) { return ctx->time_ns; }
uint64_t ose_tick_index(const OseContext *ctx) { return ctx->tick_index; }

int ose_fail(OseContext *ctx, const char *message) {
    Runtime *runtime = ctx->private_runtime;
    runtime->failed = 1;
    fprintf(stderr, "%s: %s\n", ctx->name, message);
    return 0;
}

const void *ose_input_value(OseContext *ctx, size_t index) {
    Runtime *runtime = ctx->private_runtime;
    return index < runtime->input_count ? runtime->selected[index].message : NULL;
}

int ose_publish_value(OseContext *ctx, size_t index, const void *value) {
    Runtime *runtime = ctx->private_runtime;
    size_t size;
    void *payload;
    int result;
    if (!runtime->active || index >= runtime->output_count || !value)
        return ose_fail(ctx, "invalid publication outside a tick or output port");
    payload = type_encode(runtime->encoders[index], runtime->outputs[index].root, value, &size);
    if (!payload) return ose_fail(ctx, "SDL output encoding failed");
    result = ose_publish(runtime->outputs[index].name, payload, size);
    type_free(payload);
    return result ? 1 : ose_fail(ctx, "output IPC failed");
}

const void *ose_parameter_value(const OseContext *ctx, size_t index) {
    Runtime *runtime = ctx->private_runtime;
    return index < runtime->parameter_count && runtime->parameters[index].readable
        ? runtime->parameter_values[index].message : NULL;
}
int ose_set_parameter_value(OseContext *ctx, size_t index, const void *value) {
    Runtime *runtime = ctx->private_runtime;
    void *payload;
    size_t size;
    int ok;
    if (!runtime->parameter_writes || index >= runtime->parameter_count || !runtime->parameters[index].writable || !value) return 0;
    payload = type_encode(runtime->parameter_codecs[index], runtime->parameters[index].root, value, &size);
    if (!payload) return 0;
    ok = ose_set_parameters(runtime->parameters[index].name, payload, size);
    type_free(payload);
    return ok;
}
static int parameter_update(OseContext *ctx, const OseEvent *event, int initial) {
    Runtime *runtime = ctx->private_runtime;
    const uint8_t *name, *text, *payload;
    const char *root;
    size_t n, text_size, payload_size, i;
    void *value;
    if (event->op != 'U' || !ose_input(event, &name, &n, &text, &text_size, &payload, &payload_size)) return 0;
    for (i = 0; i < runtime->parameter_count; ++i)
        if (strlen(runtime->parameters[i].name) == n && !memcmp(runtime->parameters[i].name, name, n)) break;
    if (i == runtime->parameter_count || !runtime->parameters[i].readable ||
        (initial && runtime->parameter_values[i].present) ||
        strlen(runtime->parameters[i].catalogue) != text_size ||
        memcmp(runtime->parameters[i].catalogue, text, text_size)) return 0;
    root = type_message_name(runtime->parameter_codecs[i], payload, payload_size);
    if (!root || strcmp(root, runtime->parameters[i].root)) return 0;
    value = type_decode(runtime->parameter_codecs[i], payload, payload_size);
    if (!value) return 0;
    type_free(runtime->parameter_values[i].message);
    runtime->parameter_values[i].message = value;
    runtime->parameter_values[i].present = 1;
    return 1;
}

static int admit(OseContext *ctx, const OseEvent *event) {
    Runtime *runtime = ctx->private_runtime;
    const uint8_t *port, *text, *payload;
    size_t n, text_size, payload_size, index;
    Input *input;
    const OsePortSpec *spec;
    const char *root;
    if (!ose_input(event, &port, &n, &text, &text_size, &payload, &payload_size))
        return ose_fail(ctx, "invalid INPUT frame");
    for (index = 0; index < runtime->input_count; ++index) {
        const char *name = runtime->inputs[index].name;
        if (strlen(name) == n && !memcmp(name, port, n)) break;
    }
    if (index == runtime->input_count) return ose_fail(ctx, "unknown input port");
    input = &runtime->selected[index];
    spec = &runtime->inputs[index];
    if (input->present) return ose_fail(ctx, "duplicate input in tick");
    if (!text_size && !payload_size) {
        if (!spec->required || spec->timeout <= 0)
            return ose_fail(ctx, "null input requires a timeout");
        input->present = 1;
        return 1;
    }
    if (!input->codec || input->catalogue_size != text_size || memcmp(input->catalogue, text, text_size)) {
        SdlCompatibilityDiagnostic *check = type_compare_catalogues(text, text_size,
            spec->catalogue, strlen(spec->catalogue), spec->root, true);
        SdlContext *codec;
        char *catalogue;
        int compatible = check && check->code == SDL_COMPAT_COMPATIBLE;
        type_compatibility_free(check);
        if (!compatible) return ose_fail(ctx, "incompatible input catalogue");
        codec = type_prepare(text, text_size);
        catalogue = malloc(text_size ? text_size : 1);
        if (!codec || !catalogue) {
            type_context_free(codec); free(catalogue);
            return ose_fail(ctx, "input codec initialization failed");
        }
        memcpy(catalogue, text, text_size);
        type_context_free(input->codec); free(input->catalogue);
        input->codec = codec; input->catalogue = catalogue; input->catalogue_size = text_size;
    }
    root = type_message_name(input->codec, payload, payload_size);
    if (!root || strcmp(root, spec->root)) return ose_fail(ctx, "wrong input message type");
    input->message = type_decode(input->codec, payload, payload_size);
    input->present = input->message != NULL;
    return input->message ? 1 : ose_fail(ctx, "SDL input decoding failed");
}

int ose_run_callbacks_parameters(const char *name,
    const OsePortSpec *inputs, size_t input_count,
    const OsePortSpec *outputs, size_t output_count,
    const OseParameterSpec *parameters, size_t parameter_count,
    OseHook start, OseHook dispatch, OseHook stop) {
    Runtime runtime;
    OseContext ctx;
    OseEvent event;
    char *config = NULL;
    size_t index;
    int result = 1, initialized = 0;
    memset(&runtime, 0, sizeof(runtime));
    memset(&ctx, 0, sizeof(ctx));
    runtime.parameters = parameters; runtime.parameter_count = parameter_count;
    runtime.parameter_values = calloc(parameter_count ? parameter_count : 1, sizeof(Input));
    runtime.parameter_codecs = calloc(parameter_count ? parameter_count : 1, sizeof(SdlContext *));
    runtime.inputs = inputs; runtime.outputs = outputs;
    runtime.input_count = input_count; runtime.output_count = output_count;
    ctx.name = name; ctx.private_runtime = &runtime;
    runtime.selected = calloc(input_count ? input_count : 1, sizeof(Input));
    runtime.encoders = calloc(output_count ? output_count : 1, sizeof(SdlContext *));
    if (!runtime.selected || !runtime.encoders || !runtime.parameter_values || !runtime.parameter_codecs) goto cleanup;
    if (!ose_read(&event)) goto cleanup;
    if (event.op != 'I' || memchr(event.data, 0, event.size)) { ose_release(&event); goto cleanup; }
    config = malloc(event.size + 1);
    if (!config) { ose_release(&event); goto cleanup; }
    memcpy(config, event.data, event.size); config[event.size] = 0;
    ose_release(&event); ctx.config_json = config;
    for (index = 0; index < output_count; ++index) {
        runtime.encoders[index] = type_prepare(outputs[index].catalogue, strlen(outputs[index].catalogue));
        if (!runtime.encoders[index]) goto cleanup;
    }
    for (index = 0; index < parameter_count; ++index) {
        runtime.parameter_codecs[index] = type_prepare(parameters[index].catalogue, strlen(parameters[index].catalogue));
        if (!runtime.parameter_codecs[index]) goto cleanup;
    }
    for (index = 0; index < parameter_count; ++index) {
        int valid;
        if (!parameters[index].readable) continue;
        if (!ose_read(&event)) goto cleanup;
        valid = parameter_update(&ctx, &event, 1);
        ose_release(&event);
        if (!valid) goto cleanup;
    }
    initialized = 1; runtime.parameter_writes = 1;
    if ((start && !start(&ctx)) || runtime.failed) goto cleanup;
    runtime.parameter_writes = 0;
    runtime.ready = 1;
    { const uint8_t capabilities = parameter_count ? 3 : 1;
      if (!ose_send('R', &capabilities, 1)) goto cleanup; }
    while (ose_read(&event)) {
        char op = event.op;
        int valid = 1;
        if (op == 'E') valid = recording_update(&ctx, &event);
        else if (op == 'U') valid = parameter_update(&ctx, &event, 0);
        else if (op == 'D') valid = admit(&ctx, &event);
        else if (op == 'T') {
            if (!ose_tick(&event, &ctx.time_ns, &ctx.tick_index)) valid = ose_fail(&ctx, "invalid tick context");
        } else if (event.size || op != 'X') valid = ose_fail(&ctx, "unexpected component event");
        ose_release(&event);
        if (!valid) goto cleanup;
        if (op == 'X') { result = 0; break; }
        if (op != 'T') continue;
        for (index = 0; index < input_count; ++index) {
            if (inputs[index].required && !runtime.selected[index].present) {
                ose_fail(&ctx, "missing required input"); goto cleanup;
            }
        }
        runtime.active = 1; runtime.parameter_writes = 1;
        runtime.recording_bytes = 0;
        valid = dispatch(&ctx);
        runtime.active = 0; runtime.parameter_writes = 0;
        if (!valid || runtime.failed || !ose_send('F', NULL, 0)) goto cleanup;
        for (index = 0; index < input_count; ++index) {
            type_free(runtime.selected[index].message); runtime.selected[index].message = NULL;
            runtime.selected[index].present = 0;
        }
    }
cleanup:
    runtime.active = 0; runtime.parameter_writes = 0;
    if (initialized && stop && !stop(&ctx)) result = 1;
    if (runtime.failed) result = 1;
    if (result) fprintf(stderr, "%s: component initialization, callback or IPC failed\n", name);
    for (index = 0; runtime.selected && index < input_count; ++index) {
        type_free(runtime.selected[index].message);
        type_context_free(runtime.selected[index].codec); free(runtime.selected[index].catalogue);
    }
    for (index = 0; runtime.encoders && index < output_count; ++index)
        type_context_free(runtime.encoders[index]);
    for (index = 0; index < parameter_count; ++index) {
        if (runtime.parameter_values) type_free(runtime.parameter_values[index].message);
        if (runtime.parameter_codecs) type_context_free(runtime.parameter_codecs[index]);
    }
    free(runtime.parameter_values); free(runtime.parameter_codecs);
    for (index = 0; index < 256; ++index) {
        free(runtime.recording[index].name); free(runtime.recording[index].root);
        type_context_free(runtime.recording[index].encoder);
    }
    free(runtime.selected); free(runtime.encoders); free(config);
    return result;
}

int ose_run_callbacks(const char *name,
    const OsePortSpec *inputs, size_t input_count,
    const OsePortSpec *outputs, size_t output_count,
    OseHook start, OseHook dispatch, OseHook stop) {
    return ose_run_callbacks_parameters(name, inputs, input_count, outputs, output_count,
        NULL, 0, start, dispatch, stop);
}
