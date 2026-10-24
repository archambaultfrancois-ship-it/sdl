#ifndef CHAIN_CALLBACKS_H
#define CHAIN_CALLBACKS_H
#include <stddef.h>
#include <stdint.h>

/* config_json is the received INIT document, valid through on_stop.
 * user_data belongs to the user; on_stop can release it. */
typedef struct ChainContext {
    const char *name;
    const char *config_json;
    void *user_data;
    void *private_runtime;
    uint64_t time_ns, tick_index;
} ChainContext;

typedef struct {
    const char *name, *root, *catalogue;
    int required;
    double timeout; /* Seconds; zero means no timeout. Timed inputs may be NULL. */
} ChainPortSpec;
typedef struct {
    const char *name, *root, *catalogue;
    int readable, writable;
} ChainParameterSpec;
typedef int (*ChainHook)(ChainContext *);

int chain_run_callbacks(const char *name,
    const ChainPortSpec *inputs, size_t input_count,
    const ChainPortSpec *outputs, size_t output_count,
    ChainHook start, ChainHook dispatch, ChainHook stop);
int chain_run_callbacks_parameters(const char *name,
    const ChainPortSpec *inputs, size_t input_count,
    const ChainPortSpec *outputs, size_t output_count,
    const ChainParameterSpec *parameters, size_t parameter_count,
    ChainHook start, ChainHook dispatch, ChainHook stop);
const void *chain_parameter_value(const ChainContext *, size_t index);
int chain_set_parameter_value(ChainContext *, size_t index, const void *value);
/* Borrowed decoded values last until the callback returns. A timed-out input
 * has an explicit INPUT frame and a NULL value. Missing required frames fail. */
const void *chain_input_value(ChainContext *, size_t index);
int chain_publish_value(ChainContext *, size_t index, const void *value);
/* Report a user error and fail the current initialization/tick. */
int chain_fail(ChainContext *, const char *message);
double chain_now(const ChainContext *);
uint64_t chain_time_ns(const ChainContext *);
uint64_t chain_tick_index(const ChainContext *);
#endif
