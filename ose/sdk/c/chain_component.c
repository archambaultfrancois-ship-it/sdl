#include "chain_component.h"
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
int chain_ready(void) {
    const uint8_t capability = 1;
    return chain_send('R', &capability, 1);
}
int chain_tick(const ChainEvent *event, uint64_t *time_ns, uint64_t *tick_index) {
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
int chain_read(ChainEvent *event) {
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
void chain_release(ChainEvent *event) {
    free(event->data); event->data=NULL; event->size=0;
}
int chain_send(char op, const void *data, size_t size) {
    uint8_t b[4];
    if (size>=MAX_SIZE || (size && !data)) return 0;
    header(b,(uint32_t)size+1);
    return fwrite(b,1,4,stdout)==4 && fwrite(&op,1,1,stdout)==1 &&
           (!size || fwrite(data,1,size,stdout)==size) && fflush(stdout)==0;
}
int chain_publish(const char *port, const void *payload, size_t size) {
    size_t n=strlen(port);
    uint8_t *data;
    int ok;
    if (n>65535 || size>MAX_SIZE-3-n || !size || !payload) return 0;
    data=malloc(2+n+size);
    if (!data) return 0;
    data[0]=(uint8_t)(n>>8); data[1]=(uint8_t)n;
    memcpy(data+2,port,n); memcpy(data+2+n,payload,size);
    ok=chain_send('P',data,2+n+size); free(data); return ok;
}
int chain_input(const ChainEvent *event, const uint8_t **port, size_t *port_size,
                const uint8_t **catalogue, size_t *catalogue_size,
                const uint8_t **payload, size_t *payload_size) {
    const uint8_t *data;
    size_t n,c;
    if (event->op!='D' || event->size<6) return 0;
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
