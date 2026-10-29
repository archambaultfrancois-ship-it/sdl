#ifndef CENV_OSE_PARAMETERS_H
#define CENV_OSE_PARAMETERS_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct CenvParameterClient CenvParameterClient;
typedef struct CenvParameterPacket CenvParameterPacket;
typedef struct CenvParameterBlock CenvParameterBlock;
/* NULL ca/certificate/key selects local TCP + token. Supply all three for mTLS.
 * TLS requires an OpenSSL shared library at runtime and the coordinator CN.
 * Calls on a client are serialized. Free only after all calls have returned. */
CenvParameterClient *cenv_parameters_connect(const char *host, uint16_t port,
    const char *client, const char *token, const char *ca, const char *certificate, const char *key);
void cenv_parameters_free(CenvParameterClient *);
/* Arguments are a JSON object; payload is ordinary SDL2 bytes (no envelope).
 * Returns NULL on error: cenv_last_error() supplies the thread-local message. */
CenvParameterPacket *cenv_parameters_request(CenvParameterClient *, const char *operation,
    const char *arguments_json, const uint8_t *payload, size_t size);
CenvParameterPacket *cenv_parameter_path_request(CenvParameterClient *, const char *operation,
    const char *path, const uint8_t *payload, size_t size);
const char *cenv_parameter_packet_header(const CenvParameterPacket *);
const uint8_t *cenv_parameter_packet_data(const CenvParameterPacket *);
size_t cenv_parameter_packet_size(const CenvParameterPacket *);
void cenv_parameter_packet_free(CenvParameterPacket *);
/* Owned UTF-8 field; release using cenv_string_free (ose_client.h). */
char *cenv_parameter_packet_text(const CenvParameterPacket *, const char *key);
/* Typed SDL helpers, linked from ose_client.c. Register generated SDL types
 * first. Blocks borrow the client; free blocks before freeing their client.
 * get/decode return owned SDL messages, released with type_free. */
CenvParameterBlock *cenv_parameter_block(CenvParameterClient *, const char *path, const char *root);
void *cenv_parameter_get(CenvParameterBlock *);
void *cenv_parameter_decode(CenvParameterBlock *, const CenvParameterPacket *);
int cenv_parameter_set(CenvParameterBlock *, const void *message);
void cenv_parameter_block_free(CenvParameterBlock *);
#ifdef __cplusplus
}
#endif
#endif
