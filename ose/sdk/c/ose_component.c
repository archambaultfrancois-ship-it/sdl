#include "ose_component.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MAX_SIZE (1024U * 1024U)
static uint32_t number(const uint8_t *b) {
    return ((uint32_t)b[0]<<24)|((uint32_t)b[1]<<16)|((uint32_t)b[2]<<8)|b[3];
}
static void header(uint8_t *b, uint32_t n) {
    b[0]=(uint8_t)(n>>24); b[1]=(uint8_t)(n>>16); b[2]=(uint8_t)(n>>8); b[3]=(uint8_t)n;
}
int ose_ready(void) {
    const uint8_t capability = 1;
    return ose_send('R', &capability, 1);
}
int ose_tick(const OseEvent *event, uint64_t *time_ns, uint64_t *tick_index) {
    if (event->op != 'T') return 0;
    if (!event->size) { *time_ns = 0; ++*tick_index; return 1; }
    if (event->size != 16) return 0;
    *time_ns = ((uint64_t)number(event->data) << 32) | number(event->data + 4);
    *tick_index = ((uint64_t)number(event->data + 8) << 32) | number(event->data + 12);
    return *tick_index != 0;
}
static int read_all(void *data, size_t size) {
    size_t offset=0;
    while (offset<size) {
        size_t n=fread((uint8_t *)data+offset,1,size-offset,stdin);
        if (!n) return 0;
        offset+=n;
    }
    return 1;
}
int ose_read(OseEvent *event) {
    uint8_t b[4], *data;
    uint32_t n;
    if (!event || !read_all(b,4)) return 0;
    n=number(b);
    if (!n || n>MAX_SIZE) return 0;
    data=malloc(n);
    if (!data) return 0;
    if (!read_all(data,n)) { free(data); return 0; }
    event->op=(char)data[0]; memmove(data,data+1,n-1); event->data=data; event->size=n-1;
    return 1;
}
void ose_release(OseEvent *event) {
    free(event->data); event->data=NULL; event->size=0;
}
int ose_send(char op, const void *data, size_t size) {
    uint8_t b[4];
    if (size>=MAX_SIZE || (size && !data)) return 0;
    header(b,(uint32_t)size+1);
    return fwrite(b,1,4,stdout)==4 && fwrite(&op,1,1,stdout)==1 &&
           (!size || fwrite(data,1,size,stdout)==size) && fflush(stdout)==0;
}
static int named_send(char op, const char *port, const void *payload, size_t size) {
    size_t n=strlen(port);
    uint8_t *data;
    int ok;
    if (n>65535 || size>MAX_SIZE-3-n || !size || !payload) return 0;
    data=malloc(2+n+size);
    if (!data) return 0;
    data[0]=(uint8_t)(n>>8); data[1]=(uint8_t)n;
    memcpy(data+2,port,n); memcpy(data+2+n,payload,size);
    ok=ose_send(op,data,2+n+size); free(data); return ok;
}
int ose_publish(const char *port, const void *payload, size_t size) {
    return named_send('P', port, payload, size);
}
int ose_record_bytes(const char *name, const void *payload, size_t size) {
    return named_send('C', name, payload, size);
}
int ose_declare_recording(const char *name, const char *root, const char *catalogue, const char *description) {
    size_t r = strlen(root), c = strlen(catalogue), d = strlen(description);
    char *body;
    int ok;
    if (r + c + d > MAX_SIZE - 3) return 0;
    body = malloc(r + c + d + 2);
    if (!body) return 0;
    memcpy(body, root, r + 1); memcpy(body + r + 1, description, d + 1);
    memcpy(body + r + d + 2, catalogue, c);
    ok = named_send('N', name, body, r + c + d + 2);
    free(body); return ok;
}
int ose_set_parameters(const char *name, const void *payload, size_t size) {
    OseEvent result;
    int ok;
    if (!named_send('S', name, payload, size) || !ose_read(&result)) return 0;
    ok = result.op == 'A' && result.size == 1 && result.data[0] == 1;
    if (!ok && result.op == 'A' && result.size > 1 && result.data[0] == 0)
        fprintf(stderr, "parameter write: %.*s\n", (int)(result.size - 1), (const char *)result.data + 1);
    ose_release(&result);
    return ok;
}
int ose_input(const OseEvent *event, const uint8_t **port, size_t *port_size,
                const uint8_t **catalogue, size_t *catalogue_size,
                const uint8_t **payload, size_t *payload_size) {
    const uint8_t *data;
    size_t n,c;
    if ((event->op!='D' && event->op!='U') || event->size<6) return 0;
    data=event->data;
    n=((size_t)data[0]<<8)|data[1];
    if (n>event->size-6) return 0;
    c=number(data+2+n);
    if (c>event->size-6-n) return 0;
    *port=data+2; *port_size=n;
    *catalogue=data+6+n; *catalogue_size=c;
    *payload=data+6+n+c; *payload_size=event->size-6-n-c;
    return 1;
}
