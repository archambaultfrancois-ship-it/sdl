#include "ose_client_raw.h"
#include "type_engine.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct CenvPublisher {
    CenvClient *client;
    char *topic, *root;
    SdlContext *encoder;
    uint32_t identity;
    int closed;
};
typedef struct {
    char *root, *local, *remote;
    SdlContext *decoder;
    CenvOnMessage callback;
    void *user;
} Consumer;
static char *copy(const char *s) {
    size_t n;
    char *p;
    if (!s) return NULL;
    n = strlen(s) + 1; p = malloc(n);
    if (p) memcpy(p, s, n);
    return p;
}
CenvOptions cenv_fifo(void) {
    CenvOptions p = {0,256,"reject_new",0,0,100,2,100,0};
    return p;
}
CenvOptions cenv_latest(void) {
    CenvOptions p = cenv_fifo();
    p.latest = 1; p.capacity = 1; p.overflow = "drop_oldest";
    return p;
}
CenvPublisher *cenv_publisher(CenvClient *client, const char *topic, const char *root) {
    CenvPublisher *p;
    char *cat;
    size_t size;
    if (!client || !topic || !root) { cenv_set_error("argument", "client, topic and root required"); return NULL; }
    cat = type_description(&root, 1, &size);
    if (!cat) { cenv_set_error("schema", "root is not registered with SDL"); return NULL; }
    p = calloc(1, sizeof(*p));
    if (p) {
        p->client = client; p->topic = copy(topic); p->root = copy(root);
        p->encoder = type_prepare(cat, size);
        if (!p->topic || !p->root || !p->encoder || !type_catalogue_id(cat, size, &p->identity) ||
            !cenv_declare(client, topic, root, cat)) {
            /* Declaration is the last operation, so failure never leaves a lease. */
            type_context_free(p->encoder); free(p->topic); free(p->root); free(p); p = NULL;
        }
    }
    type_free(cat);
    return p;
}
char *cenv_publish(CenvPublisher *p, const void *value) {
    void *payload, *envelope;
    size_t size, envelope_size;
    char *result;
    if (!p || p->closed || !value) { cenv_set_error("argument", "publisher is closed or value is NULL"); return NULL; }
    payload = type_encode(p->encoder, p->root, value, &size);
    if (!payload) { cenv_set_error("schema", "SDL encode failed"); return NULL; }
    envelope = type_envelope_pack(p->identity, payload, size, &envelope_size);
    type_free(payload);
    if (!envelope) { cenv_set_error("schema", "SDL envelope encoding failed"); return NULL; }
    result = cenv_publish_envelope(p->client, p->topic, envelope, envelope_size);
    type_free(envelope);
    return result;
}
int cenv_publisher_close(CenvPublisher *p) {
    if (!p || p->closed) return 1;
    if (!cenv_release_publisher(p->client, p->topic)) return 0;
    p->closed = 1; return 1;
}
void cenv_publisher_free(CenvPublisher *p) {
    if (!p) return;
    (void)cenv_publisher_close(p);
    type_context_free(p->encoder); free(p->topic); free(p->root); free(p);
}
static void destroy(void *pointer) {
    Consumer *c = pointer;
    type_context_free(c->decoder); free(c->root); type_free(c->local); free(c->remote); free(c);
}
static void *decode(void *pointer, const char *cat, const uint8_t *envelope, size_t n) {
    Consumer *c = pointer;
    uint32_t identity, expected;
    const void *payload;
    size_t size;
    const char *root;
    if (!type_envelope_unpack(envelope, n, &identity, &payload, &size) ||
        !type_catalogue_id(cat, strlen(cat), &expected) || identity != expected) return NULL;
    if (!c->remote || strcmp(c->remote, cat)) {
        SdlCompatibilityDiagnostic *check = type_compare_catalogues(cat, strlen(cat),
            c->local, strlen(c->local), c->root, true);
        int compatible = check && check->code == SDL_COMPAT_COMPATIBLE;
        type_compatibility_free(check);
        if (!compatible) return NULL;
        type_context_free(c->decoder); free(c->remote);
        c->decoder = type_prepare(cat, strlen(cat)); c->remote = copy(cat);
        if (!c->decoder || !c->remote) return NULL;
    }
    root = type_message_name(c->decoder, payload, size);
    if (!root || strcmp(root, c->root)) return NULL;
    return type_decode(c->decoder, payload, size);
}
static int callback(void *pointer, const void *message, const CenvMessageInfo *info) {
    Consumer *c = pointer;
    return c->callback(c->user, message, info);
}
CenvSubscription *cenv_subscribe(CenvClient *client, const char *topic, const char *root,
    const CenvOptions *options, CenvOnMessage on_message, void *user) {
    CenvOptions o = options ? *options : cenv_fifo();
    Consumer *c;
    CenvHooks hooks;
    char policy[512];
    size_t n;
    if (!client || !topic || !root || !on_message || !o.overflow ||
        (strcmp(o.overflow,"reject_new") && strcmp(o.overflow,"drop_oldest") && strcmp(o.overflow,"wait"))) { cenv_set_error("argument", "invalid subscription arguments or overflow"); return NULL; }
    c = calloc(1, sizeof(*c));
    if (!c) { cenv_set_error("resource", "allocation failed"); return NULL; }
    c->root = copy(root); c->local = type_description(&root, 1, &n);
    c->callback = on_message; c->user = user;
    if (!c->root || !c->local) { cenv_set_error("schema", "root is not registered with SDL"); destroy(c); return NULL; }
    snprintf(policy, sizeof(policy),
        "{\"buffer\":\"%s\",\"capacity\":%zu,\"overflow\":\"%s\",\"ack\":\"%s\","
        "\"max_retries\":%u,\"ack_timeout_ms\":%u,\"backoff_multiplier\":%u,\"wait_timeout_ms\":%u,\"transport\":\"%s\"}",
        o.latest ? "latest" : "fifo", o.capacity, o.overflow, o.ack_received ? "received" : "none",
        o.max_retries, o.ack_timeout_ms, o.backoff_multiplier, o.wait_timeout_ms, o.udp ? "udp" : "tcp");
    hooks.user = c; hooks.decode = decode; hooks.free_value = type_free;
    hooks.on_message = callback; hooks.destroy = destroy;
    return cenv_subscribe_raw(client, topic, root, c->local, policy, hooks);
}

#include "ose_parameters.h"
struct CenvParameterBlock {
    CenvParameterClient *client;
    SdlContext *codec;
    char *path, *root;
};
void cenv_parameter_block_free(CenvParameterBlock *block) {
    if (!block) return;
    type_context_free(block->codec); free(block->path); free(block->root); free(block);
}
CenvParameterBlock *cenv_parameter_block(CenvParameterClient *client, const char *path, const char *root) {
    CenvParameterPacket *packet;
    CenvParameterBlock *block;
    char *actual;
    char *local;
    size_t local_size;
    SdlCompatibilityDiagnostic *check;
    int compatible;
    if (!client || !path || !root) { cenv_set_error("argument", "invalid parameter block"); return NULL; }
    packet = cenv_parameter_path_request(client, "parameters_describe", path, NULL, 0);
    if (!packet) return NULL;
    actual = cenv_parameter_packet_text(packet, "root");
    if (!actual || strcmp(actual, root)) {
        cenv_string_free(actual); cenv_parameter_packet_free(packet);
        cenv_set_error("schema", "wrong parameter root"); return NULL;
    }
    cenv_string_free(actual);
    local = type_description(&root, 1, &local_size);
    check = local ? type_compare_catalogues(cenv_parameter_packet_data(packet), cenv_parameter_packet_size(packet),
        local, local_size, root, true) : NULL;
    compatible = check && check->code == SDL_COMPAT_COMPATIBLE;
    type_compatibility_free(check); type_free(local);
    if (!compatible) {
        cenv_parameter_packet_free(packet);
        cenv_set_error("schema", "incompatible local parameter type; register the matching SDL types"); return NULL;
    }
    block = calloc(1, sizeof(*block));
    if (block) {
        block->client = client;
        block->codec = type_prepare(cenv_parameter_packet_data(packet), cenv_parameter_packet_size(packet));
        block->path = malloc(strlen(path) + 1); block->root = malloc(strlen(root) + 1);
        if (block->path) memcpy(block->path, path, strlen(path) + 1);
        if (block->root) memcpy(block->root, root, strlen(root) + 1);
    }
    cenv_parameter_packet_free(packet);
    if (!block || !block->codec || !block->path || !block->root) {
        cenv_parameter_block_free(block); cenv_set_error("schema", "cannot prepare parameter block"); return NULL;
    }
    return block;
}
void *cenv_parameter_decode(CenvParameterBlock *block, const CenvParameterPacket *packet) {
    char *path;
    const char *root;
    void *value = NULL;
    if (!block || !packet) { cenv_set_error("argument", "invalid parameter packet"); return NULL; }
    path = cenv_parameter_packet_text(packet, "path");
    root = type_message_name(block->codec, cenv_parameter_packet_data(packet), cenv_parameter_packet_size(packet));
    if (path && !strcmp(path, block->path) && root && !strcmp(root, block->root))
        value = type_decode(block->codec, cenv_parameter_packet_data(packet), cenv_parameter_packet_size(packet));
    cenv_string_free(path);
    if (!value) cenv_set_error("schema", "cannot decode parameter block");
    return value;
}
void *cenv_parameter_get(CenvParameterBlock *block) {
    CenvParameterPacket *packet;
    void *value;
    if (!block) { cenv_set_error("argument", "NULL parameter block"); return NULL; }
    packet = cenv_parameter_path_request(block->client, "parameters_get", block->path, NULL, 0);
    if (!packet) return NULL;
    value = cenv_parameter_decode(block, packet); cenv_parameter_packet_free(packet); return value;
}
int cenv_parameter_set(CenvParameterBlock *block, const void *message) {
    CenvParameterPacket *packet;
    void *payload;
    size_t size;
    if (!block || !message) { cenv_set_error("argument", "invalid parameter value"); return 0; }
    payload = type_encode(block->codec, block->root, message, &size);
    if (!payload) { cenv_set_error("schema", "cannot encode parameter block"); return 0; }
    packet = cenv_parameter_path_request(block->client, "parameters_set", block->path, payload, size);
    type_free(payload);
    if (!packet) return 0;
    cenv_parameter_packet_free(packet); return 1;
}
