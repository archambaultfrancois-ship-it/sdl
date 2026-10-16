#include <stdio.h>
#include <string.h>
#include "sdl_recording.h"
static SdlRecordingBytes text(const char *s) {
    SdlRecordingBytes b = { (const uint8_t *)s, strlen(s) }; return b;
}
#define CHECK(expression) do { if ((expression) < 0) { fprintf(stderr, "%s\n", sdl_recording_error()); return 1; } } while (0)
static int consume(const SdlRecordingRecord *record, void *userdata) {
    unsigned *count = userdata;
    if (record->payload.size != 1 || record->payload.data[0] != 1 || record->log_time_ns != UINT64_MAX) return 1;
    (*count)++; return 0;
}
int main(int argc, char **argv) {
    SdlRecordingWriter *writer; SdlRecordingReader *reader;
    const uint8_t payload[] = { 1 }; SdlRecordingBytes bytes = { payload, sizeof(payload) };
    uint16_t channel; uint64_t count; unsigned seen = 0;
    if (argc != 2 || sdl_recording_abi_version() != SDL_RECORDING_ABI_VERSION) return 1;
    writer = sdl_recording_writer_open(text(argv[1]), 0, 128);
    if (!writer) return 1;
    CHECK(sdl_recording_writer_channel(writer, text("c/packet"), text("SDL2|Packet{}"), text("Packet"), NULL, 0, &channel));
    CHECK(sdl_recording_writer_write(writer, channel, bytes, UINT64_MAX, UINT64_MAX, UINT32_MAX));
    CHECK(sdl_recording_writer_flush(writer, 1)); CHECK(sdl_recording_writer_close(writer));
    CHECK(sdl_recording_validate(text(argv[1]), &count)); if (count != 1) return 1;
    reader = sdl_recording_reader_open(text(argv[1])); if (!reader) return 1;
    CHECK(sdl_recording_replay(reader, 0, consume, &seen, &count));
    CHECK(sdl_recording_reader_close(reader)); return count == 1 && seen == 1 ? 0 : 1;
}
