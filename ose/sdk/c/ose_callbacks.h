#ifndef OSE_CALLBACKS_H
#define OSE_CALLBACKS_H
#include <stddef.h>
#include <stdint.h>

/* config_json is the received INIT document, valid through on_stop.
 * user_data belongs to the user; on_stop can release it. */
typedef struct OseContext {
    const char *name;
    const char *config_json;
    void *user_data;
    void *private_runtime;
    uint64_t time_ns, tick_index;
} OseContext;

typedef struct {
    const char *name, *root, *catalogue;
    int required;
    double timeout; /* Seconds; zero means no timeout. Timed inputs may be NULL. */
} OsePortSpec;
typedef struct {
    const char *name, *root, *catalogue;
    int readable, writable;
} OseParameterSpec;
typedef int (*OseHook)(OseContext *);

int ose_run_callbacks(const char *name,
    const OsePortSpec *inputs, size_t input_count,
    const OsePortSpec *outputs, size_t output_count,
    OseHook start, OseHook dispatch, OseHook stop);
int ose_run_callbacks_parameters(const char *name,
    const OsePortSpec *inputs, size_t input_count,
    const OsePortSpec *outputs, size_t output_count,
    const OseParameterSpec *parameters, size_t parameter_count,
    OseHook start, OseHook dispatch, OseHook stop);
const void *ose_parameter_value(const OseContext *, size_t index);
int ose_set_parameter_value(OseContext *, size_t index, const void *value);
/* Borrowed decoded values last until the callback returns. A timed-out input
 * has an explicit INPUT frame and a NULL value. Missing required frames fail. */
const void *ose_input_value(OseContext *, size_t index);
int ose_publish_value(OseContext *, size_t index, const void *value);
/* Report a user error and fail the current initialization/tick. */
int ose_fail(OseContext *, const char *message);
double ose_now(const OseContext *);
uint64_t ose_time_ns(const OseContext *);
uint64_t ose_tick_index(const OseContext *);
typedef enum {
    OSE_RECORD_ERROR = -1, OSE_RECORD_DISABLED = 0,
    OSE_RECORD_QUEUED = 1, OSE_RECORD_DROPPED = 2
} OseRecordResult;
/* Context-owned hash registry; declaration only during on_start. */
int ose_declare_recording_value(OseContext *, const char *name, const char *root,
                               const char *catalogue, const char *description);
int ose_recording_enabled(OseContext *, const char *name);
OseRecordResult ose_record_value(OseContext *, const char *name, const char *root, const void *value);
#endif
