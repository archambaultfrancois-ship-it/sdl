#ifndef CHAIN_CALLBACKS_H
#define CHAIN_CALLBACKS_H
#include <stddef.h>

/* config_json is the received INIT document, valid through on_stop.
 * user_data belongs to the user; on_stop can release it. */
typedef struct ChainContext {
    const char *name;
    const char *config_json;
    void *user_data;
    void *private_runtime;
} ChainContext;

typedef struct {
    const char *name, *root, *catalogue;
    int required;
} ChainPortSpec;
typedef int (*ChainHook)(ChainContext *);

int chain_run_callbacks(const char *name,
    const ChainPortSpec *inputs, size_t input_count,
    const ChainPortSpec *outputs, size_t output_count,
    ChainHook start, ChainHook dispatch, ChainHook stop);
/* Borrowed decoded values last until the callback returns. */
const void *chain_input_value(ChainContext *, size_t index);
int chain_publish_value(ChainContext *, size_t index, const void *value);
/* Report a user error and fail the current initialization/tick. */
int chain_fail(ChainContext *, const char *message);
#endif
