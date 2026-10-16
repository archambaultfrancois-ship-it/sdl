#include "chain_client.h"
#include "measurement.h"
#include "sdl_registry.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
static Measurement received;
static int done;
static int on_message(void *user, const void *value, const CenvMessageInfo *info) {
    (void)user; (void)info;
    pthread_mutex_lock(&lock);
    received = *(const Measurement *)value;
    done = 1; pthread_cond_signal(&changed);
    pthread_mutex_unlock(&lock);
    return 1;
}
int main(int argc, char **argv) {
    CenvClient *client;
    CenvPublisher *publisher;
    CenvSubscription *sub;
    CenvOptions qos = cenv_fifo();
    char *result;
    struct timespec deadline;
    setbuf(stdout, NULL);
    if (!register_all_types()) return 1;
    client = cenv_connect("127.0.0.1", argc > 1 ? (uint16_t)atoi(argv[1]) : 7000, "external-c");
    if (!client) goto error;
    publisher = cenv_publisher(client, "c.result", "Measurement");
    if (!publisher) goto error;
    qos.ack_received = 1; qos.max_retries = 3;
    qos.udp = argc > 2 && !strcmp(argv[2], "udp");
    sub = cenv_subscribe(client, "external.input", "Measurement", &qos, on_message, NULL);
    if (!sub) goto error;
    puts("READY");
    timespec_get(&deadline, TIME_UTC); deadline.tv_sec += 10;
    pthread_mutex_lock(&lock);
    while (!done) {
        if (pthread_cond_timedwait(&changed, &lock, &deadline)) {
            pthread_mutex_unlock(&lock); goto error;
        }
    }
    pthread_mutex_unlock(&lock);
    received.value *= 2.;
    result = cenv_publish(publisher, &received);
    if (!result) goto error;
    puts(result); cenv_string_free(result);
    cenv_unsubscribe(sub);
    if (!cenv_wait_closed(sub, 2000)) goto error;
    cenv_subscription_free(sub); cenv_publisher_free(publisher);
    cenv_client_free(client);
    return 0;
error:
    fprintf(stderr, "%s\n", cenv_last_error());
    return 1;
}
