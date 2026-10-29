#ifndef OSE_COMPONENT_H
#define OSE_COMPONENT_H
#include <stddef.h>
#include <stdint.h>
/* Returned frames own their allocation; call ose_release after use. */
typedef struct { char op; uint8_t *data; size_t size; } OseEvent;
int ose_read(OseEvent *event);
void ose_release(OseEvent *event);
int ose_send(char op, const void *data, size_t size);
int ose_publish(const char *port, const void *payload, size_t size);
int ose_record_bytes(const char *name, const void *payload, size_t size);
int ose_declare_recording(const char *name, const char *root, const char *catalogue, const char *description);
int ose_set_parameters(const char *name, const void *payload, size_t size);
/* Opt in to timestamped ticks. Historical ose_send('R', NULL, 0) stays valid. */
int ose_ready(void);
/* Empty historical TICKs return time zero and the next local index. */
int ose_tick(const OseEvent *event, uint64_t *time_ns, uint64_t *tick_index);
/* Borrowed fields remain valid until event is released. Catalogue is not NUL terminated. */
int ose_input(const OseEvent *event, const uint8_t **port, size_t *port_size,
                const uint8_t **catalogue, size_t *catalogue_size,
                const uint8_t **payload, size_t *payload_size);
#endif
