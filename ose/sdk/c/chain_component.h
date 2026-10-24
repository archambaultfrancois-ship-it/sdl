#ifndef CHAIN_COMPONENT_H
#define CHAIN_COMPONENT_H
#include <stddef.h>
#include <stdint.h>
/* Returned frames own their allocation; call chain_release after use. */
typedef struct { char op; uint8_t *data; size_t size; } ChainEvent;
int chain_read(ChainEvent *event);
void chain_release(ChainEvent *event);
int chain_send(char op, const void *data, size_t size);
int chain_publish(const char *port, const void *payload, size_t size);
int chain_set_parameters(const char *name, const void *payload, size_t size);
/* Opt in to timestamped ticks. Historical chain_send('R', NULL, 0) stays valid. */
int chain_ready(void);
/* Empty historical TICKs return time zero and the next local index. */
int chain_tick(const ChainEvent *event, uint64_t *time_ns, uint64_t *tick_index);
/* Borrowed fields remain valid until event is released. Catalogue is not NUL terminated. */
int chain_input(const ChainEvent *event, const uint8_t **port, size_t *port_size,
                const uint8_t **catalogue, size_t *catalogue_size,
                const uint8_t **payload, size_t *payload_size);
#endif
