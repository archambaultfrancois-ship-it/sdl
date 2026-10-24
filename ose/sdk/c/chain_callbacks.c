#include "chain_callbacks.h"
#include "chain_component.h"
#include "type_engine.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    SdlContext *codec;
    char *catalogue;
    size_t catalogue_size;
    void *message;
    int present;
} Input;
typedef struct {
    const ChainPortSpec *inputs, *outputs;
    size_t input_count, output_count;
    Input *selected;
    SdlContext **encoders;
    const ChainParameterSpec *parameters;
    size_t parameter_count;
    Input *parameter_values;
    SdlContext **parameter_codecs;
    int active, failed, parameter_writes;
} Runtime;

double chain_now(const ChainContext *ctx) { return (double)ctx->time_ns / 1000000000.0; }
uint64_t chain_time_ns(const ChainContext *ctx) { return ctx->time_ns; }
uint64_t chain_tick_index(const ChainContext *ctx) { return ctx->tick_index; }

int chain_fail(ChainContext *ctx, const char *message) {
    Runtime *runtime = ctx->private_runtime;
    runtime->failed = 1;
    fprintf(stderr, "%s: %s\n", ctx->name, message);
    return 0;
}

const void *chain_input_value(ChainContext *ctx, size_t index) {
    Runtime *runtime = ctx->private_runtime;
    return index < runtime->input_count ? runtime->selected[index].message : NULL;
}

int chain_publish_value(ChainContext *ctx, size_t index, const void *value) {
    Runtime *runtime = ctx->private_runtime;
    size_t size;
    void *payload;
    int result;
    if (!runtime->active || index >= runtime->output_count || !value)
        return chain_fail(ctx, "invalid publication outside a tick or output port");
    payload = type_encode(runtime->encoders[index], runtime->outputs[index].root, value, &size);
    if (!payload) return chain_fail(ctx, "SDL output encoding failed");
    result = chain_publish(runtime->outputs[index].name, payload, size);
    type_free(payload);
    return result ? 1 : chain_fail(ctx, "output IPC failed");
}

const void *chain_parameter_value(const ChainContext *ctx, size_t index) {
    Runtime *runtime = ctx->private_runtime;
    return index < runtime->parameter_count && runtime->parameters[index].readable
        ? runtime->parameter_values[index].message : NULL;
}
int chain_set_parameter_value(ChainContext *ctx, size_t index, const void *value) {
    Runtime *runtime = ctx->private_runtime;
    void *payload;
    size_t size;
    int ok;
    if (!runtime->parameter_writes || index >= runtime->parameter_count || !runtime->parameters[index].writable || !value) return 0;
    payload = type_encode(runtime->parameter_codecs[index], runtime->parameters[index].root, value, &size);
    if (!payload) return 0;
    ok = chain_set_parameters(runtime->parameters[index].name, payload, size);
    type_free(payload);
    return ok;
}
static int parameter_update(ChainContext *ctx, const ChainEvent *event, int initial) {
    Runtime *runtime = ctx->private_runtime;
    const uint8_t *name, *text, *payload;
    const char *root;
    size_t n, text_size, payload_size, i;
    void *value;
    if (event->op != 'U' || !chain_input(event, &name, &n, &text, &text_size, &payload, &payload_size)) return 0;
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

static int admit(ChainContext *ctx, const ChainEvent *event) {
    Runtime *runtime = ctx->private_runtime;
    const uint8_t *port, *text, *payload;
    size_t n, text_size, payload_size, index;
    Input *input;
    const ChainPortSpec *spec;
    const char *root;
    if (!chain_input(event, &port, &n, &text, &text_size, &payload, &payload_size))
        return chain_fail(ctx, "invalid INPUT frame");
    for (index = 0; index < runtime->input_count; ++index) {
        const char *name = runtime->inputs[index].name;
        if (strlen(name) == n && !memcmp(name, port, n)) break;
    }
    if (index == runtime->input_count) return chain_fail(ctx, "unknown input port");
    input = &runtime->selected[index];
    spec = &runtime->inputs[index];
    if (input->present) return chain_fail(ctx, "duplicate input in tick");
    if (!text_size && !payload_size) {
        if (!spec->required || spec->timeout <= 0)
            return chain_fail(ctx, "null input requires a timeout");
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
        if (!compatible) return chain_fail(ctx, "incompatible input catalogue");
        codec = type_prepare(text, text_size);
        catalogue = malloc(text_size ? text_size : 1);
        if (!codec || !catalogue) {
            type_context_free(codec); free(catalogue);
            return chain_fail(ctx, "input codec initialization failed");
        }
        memcpy(catalogue, text, text_size);
        type_context_free(input->codec); free(input->catalogue);
        input->codec = codec; input->catalogue = catalogue; input->catalogue_size = text_size;
    }
    root = type_message_name(input->codec, payload, payload_size);
    if (!root || strcmp(root, spec->root)) return chain_fail(ctx, "wrong input message type");
    input->message = type_decode(input->codec, payload, payload_size);
    input->present = input->message != NULL;
    return input->message ? 1 : chain_fail(ctx, "SDL input decoding failed");
}

int chain_run_callbacks_parameters(const char *name,
    const ChainPortSpec *inputs, size_t input_count,
    const ChainPortSpec *outputs, size_t output_count,
    const ChainParameterSpec *parameters, size_t parameter_count,
    ChainHook start, ChainHook dispatch, ChainHook stop) {
    Runtime runtime;
    ChainContext ctx;
    ChainEvent event;
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
    if (!chain_read(&event)) goto cleanup;
    if (event.op != 'I' || memchr(event.data, 0, event.size)) { chain_release(&event); goto cleanup; }
    config = malloc(event.size + 1);
    if (!config) { chain_release(&event); goto cleanup; }
    memcpy(config, event.data, event.size); config[event.size] = 0;
    chain_release(&event); ctx.config_json = config;
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
        if (!chain_read(&event)) goto cleanup;
        valid = parameter_update(&ctx, &event, 1);
        chain_release(&event);
        if (!valid) goto cleanup;
    }
    initialized = 1; runtime.parameter_writes = 1;
    if ((start && !start(&ctx)) || runtime.failed) goto cleanup;
    runtime.parameter_writes = 0;
    { const uint8_t capabilities = parameter_count ? 3 : 1;
      if (!chain_send('R', &capabilities, 1)) goto cleanup; }
    while (chain_read(&event)) {
        char op = event.op;
        int valid = 1;
        if (op == 'U') valid = parameter_update(&ctx, &event, 0);
        else if (op == 'D') valid = admit(&ctx, &event);
        else if (op == 'T') {
            if (!chain_tick(&event, &ctx.time_ns, &ctx.tick_index)) valid = chain_fail(&ctx, "invalid tick context");
        } else if (event.size || op != 'X') valid = chain_fail(&ctx, "unexpected component event");
        chain_release(&event);
        if (!valid) goto cleanup;
        if (op == 'X') { result = 0; break; }
        if (op != 'T') continue;
        for (index = 0; index < input_count; ++index) {
            if (inputs[index].required && !runtime.selected[index].present) {
                chain_fail(&ctx, "missing required input"); goto cleanup;
            }
        }
        runtime.active = 1; runtime.parameter_writes = 1;
        valid = dispatch(&ctx);
        runtime.active = 0; runtime.parameter_writes = 0;
        if (!valid || runtime.failed || !chain_send('F', NULL, 0)) goto cleanup;
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
    free(runtime.selected); free(runtime.encoders); free(config);
    return result;
}

int chain_run_callbacks(const char *name,
    const ChainPortSpec *inputs, size_t input_count,
    const ChainPortSpec *outputs, size_t output_count,
    ChainHook start, ChainHook dispatch, ChainHook stop) {
    return chain_run_callbacks_parameters(name, inputs, input_count, outputs, output_count,
        NULL, 0, start, dispatch, stop);
}
