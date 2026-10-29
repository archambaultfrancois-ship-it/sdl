#ifndef CENV_OSE_CLIENT_H
#define CENV_OSE_CLIENT_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct CenvClient CenvClient;
typedef struct CenvSubscription CenvSubscription;
typedef struct CenvPublisher CenvPublisher;
typedef struct {
    const char *publication_id, *topic, *epoch;
    uint64_t sequence;
} CenvMessageInfo;
/* Message and info are borrowed for this call only; return 1 on success, 0 on failure.
 * Callbacks are sequential per subscription and concurrent between subscriptions. */
typedef int (*CenvOnMessage)(void *user, const void *message, const CenvMessageInfo *info);
typedef struct {
    int latest;
    size_t capacity;
    const char *overflow; /* reject_new, drop_oldest, wait */
    int ack_received;
    uint32_t max_retries, ack_timeout_ms, backoff_multiplier, wait_timeout_ms;
    int udp;
} CenvOptions;
CenvOptions cenv_fifo(void);
CenvOptions cenv_latest(void);
CenvClient *cenv_connect(const char *host, uint16_t port, const char *client_id);
/* Thread-local error, borrowed until the next failed SDK call on this thread. */
const char *cenv_last_error(void);
void cenv_close(CenvClient *);
void cenv_client_free(CenvClient *);
int cenv_run(CenvClient *);
/* JSON strings returned by these functions must be released with cenv_string_free. */
char *cenv_list_topics(CenvClient *);
void cenv_string_free(char *);
/* Register generated SDL types before creating typed publishers/subscriptions. */
CenvPublisher *cenv_publisher(CenvClient *, const char *topic, const char *root);
char *cenv_publish(CenvPublisher *, const void *message);
int cenv_publisher_close(CenvPublisher *);
void cenv_publisher_free(CenvPublisher *);
CenvSubscription *cenv_subscribe(CenvClient *, const char *topic, const char *root,
                                const CenvOptions *, CenvOnMessage, void *user);
int cenv_unsubscribe(CenvSubscription *);
/* After a successful wait, user data is no longer accessed by callbacks. Do not
 * wait from the subscription's own callback. A running callback cannot be cancelled. */
int cenv_wait_closed(CenvSubscription *, uint32_t timeout_ms);
char *cenv_subscription_stats(CenvSubscription *);
void cenv_subscription_free(CenvSubscription *);
#ifdef __cplusplus
}
#endif
#endif
