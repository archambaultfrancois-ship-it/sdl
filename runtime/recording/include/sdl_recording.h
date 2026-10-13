#ifndef SDL_RECORDING_H
#define SDL_RECORDING_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define SDL_RECORDING_ABI_VERSION 1u
#define SDL_RECORDING_FORMAT_MCAP 0u
#define SDL_RECORDING_FORMAT_PCAPNG 1u
#define SDL_RECORDING_FILTER_START 1u
#define SDL_RECORDING_FILTER_END 2u

/* Optional native extension: no dependency on generated SDL structures.
 * Text is counted UTF-8; payload is opaque SDL2. NULL is valid only for size=0.
 * All inputs are borrowed for the call; handles are single-threaded.
 * Status: 0=success, -1=error. error() is thread-local, valid until the next call.
 * Call close explicitly to report write errors. Writer close consumes its handle
 * even on error; reader close rejects reentrant calls during replay.
 */
typedef struct { const uint8_t *data; size_t size; } SdlRecordingBytes;
typedef struct { SdlRecordingBytes key, value; } SdlRecordingMetadata;
typedef struct SdlRecordingWriter SdlRecordingWriter;
typedef struct SdlRecordingReader SdlRecordingReader;
typedef struct {
    uint16_t channel_id, schema_id;
    SdlRecordingBytes topic, root_type, catalogue, payload;
    const SdlRecordingMetadata *metadata;
    size_t metadata_count;
    uint64_t log_time_ns, publish_time_ns;
    uint32_t sequence;
} SdlRecordingRecord;

uint32_t sdl_recording_abi_version(void);
const char *sdl_recording_error(void);
int sdl_recording_now(uint64_t *time_ns);
/* Default container is PCAPNG, independently of the filename extension. */
SdlRecordingWriter *sdl_recording_writer_open(SdlRecordingBytes path,
    uint8_t overwrite, uint64_t chunk_size);
/* Explicit container selection: SDL_RECORDING_FORMAT_MCAP or _PCAPNG.
 * Readers automatically detect MCAP or SDL PCAPNG by file magic. */
SdlRecordingWriter *sdl_recording_writer_open_format(SdlRecordingBytes path,
    uint8_t overwrite, uint64_t chunk_size, uint8_t format);
int sdl_recording_writer_channel(SdlRecordingWriter *, SdlRecordingBytes topic,
    SdlRecordingBytes catalogue, SdlRecordingBytes root_type,
    const SdlRecordingMetadata *metadata, size_t count, uint16_t *channel_id);
int sdl_recording_writer_metadata(SdlRecordingWriter *, SdlRecordingBytes name,
    const SdlRecordingMetadata *metadata, size_t count);
int sdl_recording_writer_validate_payload(SdlRecordingWriter *, uint8_t validate);
int sdl_recording_writer_write(SdlRecordingWriter *, uint16_t channel_id,
    SdlRecordingBytes payload, uint64_t log_time_ns, uint64_t publish_time_ns,
    uint32_t sequence);
int sdl_recording_writer_flush(SdlRecordingWriter *, uint8_t sync);
int sdl_recording_writer_close(SdlRecordingWriter *);

SdlRecordingReader *sdl_recording_reader_open(SdlRecordingBytes path);
/* Absolute log-time range [start,end); empty topic selects every topic. */
int sdl_recording_reader_filter(SdlRecordingReader *, SdlRecordingBytes topic,
    uint8_t flags, uint64_t start_time_ns, uint64_t end_time_ns);
/* 1=record, 0=EOF, -1=error; sets *record=NULL on EOF/error.
 * Returned view is borrowed until next/filter/replay/close on this reader.
 * EOF means complete framing/available CRC checks; a prefix is not validation.
 */
int sdl_recording_reader_next(SdlRecordingReader *, const SdlRecordingRecord **record);
int sdl_recording_reader_close(SdlRecordingReader *);
int sdl_recording_validate(SdlRecordingBytes path, uint64_t *message_count);
/* Callback view is borrowed only during the callback. Return 0 to continue.
 * The callback may use other handles, but not this reader. speed=0 skips waiting.
 * Streaming replay has no automatic preflight. validate() provides one.
 */
typedef int (*SdlRecordingConsumer)(const SdlRecordingRecord *, void *);
int sdl_recording_replay(SdlRecordingReader *, double speed,
    SdlRecordingConsumer consumer, void *userdata, uint64_t *message_count);

#ifdef __cplusplus
}
#endif
#endif
