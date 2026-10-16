#ifndef CENV_CHAIN_CLIENT_RAW_H
#define CENV_CHAIN_CLIENT_RAW_H
#include "chain_client.h"
/* Low-level ABI for additional codecs. subscribe_raw takes ownership of hooks.user
 * immediately, including on failure. destroy runs after all worker uses finish. */
typedef struct {
    void *user;
    void *(*decode)(void *, const char *catalogue, const uint8_t *, size_t);
    void (*free_value)(void *);
    CenvOnMessage on_message;
    void (*destroy)(void *);
} CenvHooks;
char *cenv_copy_string(const char *);
void cenv_set_error(const char *code, const char *message);
int cenv_declare(CenvClient *, const char *topic, const char *root, const char *catalogue);
int cenv_release_publisher(CenvClient *, const char *topic);
char *cenv_publish_envelope(CenvClient *, const char *, const uint8_t *, size_t);
CenvSubscription *cenv_subscribe_raw(CenvClient *, const char *topic, const char *root,
                                    const char *catalogue, const char *resolved_policy_json, CenvHooks);
#endif
