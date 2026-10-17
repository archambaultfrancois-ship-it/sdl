# SDL2 PCAPNG recording profile, version 1

`sdl2-recording-pcapng-v1` stores self-contained SDL recordings in
[PCAPNG](https://datatracker.ietf.org/doc/draft-ietf-opsawg-pcapng/06/).
LINKTYPE_USER0 (147) is the local convention for this application transport;
it is not a registered SDL link type. Packets carry SDL recording records directly,
without synthetic Ethernet, IP or UDP headers. PCAPNG is the default recording
format; MCAP remains available through explicit format selection.

Catalogue, channel, payload, timestamp, ordering, filtering and replay semantics
are shared with the [MCAP recording profile](recording.md). A file includes exact
canonical UTF-8 SDL2 catalogues and complete raw SDL2 payloads; generated classes
are unnecessary for dynamic inspection. All recording readers detect MCAP or
SDL PCAPNG from file magic, independently of the filename extension. Writers
default to PCAPNG independently of the filename extension. Select `mcap`
explicitly to write MCAP; a `.mcap` suffix alone does not select it.

## Recording and tools

After `make recording-deps`, the Python API writes PCAPNG by default:

```python
from sdl_recording import RecordingWriter

with RecordingWriter("capture.pcapng") as writer:
    channel = writer.register_channel(topic="radio/iq", catalogue=catalogue,
                                      root_type="RadioCapture")
    writer.write_encoded(channel, encoded_payload, log_time_ns=arrival_ns,
                         publish_time_ns=acquisition_ns, sequence=sequence)
```

The [native reference](recording_native.md) describes selection in C, Rust,
Java JNI, Ada and MATLAB/Octave C MEX. The same channel and write APIs work in
both containers. `chunk_size` remains accepted and validated for API compatibility;
it has no effect on PCAPNG, which writes one Enhanced Packet Block per record.
Flush writes buffered output; close writes the required END record. Explicit close
reports IO failures. Dropping an unclosed Rust writer leaves an incomplete file.

From the repository root:

```sh
export PYTHONPATH="build/recording-deps:runtime/python:$PYTHONPATH"
python3 tools/recording/sdl-recording convert capture.mcap capture.pcapng
python3 tools/recording/sdl-recording info capture.pcapng
python3 tools/recording/sdl-recording inspect capture.pcapng --format text --limit 1
python3 tools/recording/sdl-recording inspect capture.pcapng --format table --limit 1
python3 tools/recording/sdl-recording validate capture.pcapng
python3 tools/recording/sdl-recording convert capture.pcapng restored.mcap --format mcap
make -C examples/record_replay demo
```

CLI replay also accepts PCAPNG, with the existing `--consumer module:function`,
`--topic`, `--from`, `--to`, `--duration` and `--speed` options. Prefix a time
bound with `+` for an offset from recording start; otherwise it is absolute.
Inspection's `--format` chooses
the display style; conversion's `--format` chooses the output container
(default: `pcapng`).

`convert_recording(source, output, format="pcapng", overwrite=False)` is the Python
conversion API. Conversion validates the entire source before creating output,
then streams records in file order. It preserves catalogue text, channels (including
unused channels), application/channel metadata, raw payloads, both timestamps and
sequence. Local schema/channel IDs may change. Container indexes, library strings,
schema display names, attachments and unknown MCAP extensions are not copied.
Source and destination must differ, including aliases through symlinks or hard
links. Existing output is rejected unless overwrite is explicit. Keep the source
unchanged between preflight and conversion. An IO failure may leave incomplete output.

## PCAPNG container requirements

This version accepts exactly one little-endian section and one USER0 interface:

| Block | Required content |
| --- | --- |
| Section Header (SHB, `0x0a0d0d0a`) | Byte-order magic `0x1a2b3c4d`; version 1.0; section length -1; no options. |
| Interface Description (IDB, 1) | Link type 147; reserved 0; snaplen 0; `if_tsresol` option 9 with one-byte value 9, zero padding, then end-of-options. |
| Enhanced Packet (EPB, 6) | Interface ID 0; captured length equals original length; one complete S2RC packet; zero padding to four bytes; no options. |

Each block has the standard type/total-length prefix and matching total-length
trailer. Its total length includes both headers, is divisible by four, and is at
most 64 MiB. EPB timestamps combine the high and low 32-bit words and are unsigned
64-bit nanoseconds. For MESSAGE they equal `log_time_ns` exactly; every control
packet has timestamp zero. Consequently Wireshark's time/duration views should
be used with the `sdlrec.kind == 5` message filter.

Readers reject multiple sections/interfaces, big-endian sections, truncated
packets, nonzero padding, extra options, unknown blocks and other USER0 protocols.
This is an SDL profile reader, not a general-purpose PCAPNG reader. PCAPNG itself
permits more arrangements than this profile. Generic capture tools can open these
files, but rewriting their blocks/options may take them outside the SDL profile.

## S2RC packet grammar

All integer fields in the application packet are little-endian. `u16`, `u32` and
`u64` are unsigned integers of that width. `string` means `u32 byte_count` followed
by exactly that many UTF-8 bytes; embedded zero bytes are allowed. `map` means
`u32 encoded_byte_count` followed by zero or more `string key, string value` pairs
that consume that region exactly, with unique keys. It is a byte count, not an
entry count. Text/catalogue/map regions are limited to 1 MiB, maps to 65,535 pairs,
and SDL payloads to 32 MiB in both implementations. No field may cross its packet.

| Packet header offset | Width | Value |
| --- | --- | --- |
| 0 | 4 bytes | ASCII `S2RC` |
| 4 | u8 | Version 1 |
| 5 | u8 | Record kind from the table below |
| 6 | u16 | Flags 0 |
| 8 | u32 | Body byte count, excluding this 16-byte header |
| 12 | u32 | CRC32 over bytes `[0,12)` followed by the complete body |
| 16 | variable | Record body |

CRC32 uses the IEEE polynomial and the standard zlib/CRC32 convention (empty input
CRC zero). A packet's CRC field is excluded from its own calculation. CRC fields
are always checked, including a value of zero. The following bodies reuse the
primitive [MCAP record layouts](https://mcap.dev/spec), without MCAP record headers
or chunk framing, except for the profile-specific END body:

| Kind | Name | Body, in order |
| --- | --- | --- |
| 1 | HEADER | `string profile`, `string library`; profile exactly `sdl2-recording-pcapng-v1`. |
| 3 | CATALOGUE | `u16 schema_id`, `string name`, `string encoding`, `u32 catalogue_byte_count`, exact catalogue bytes; nonzero ID, encoding `sdl2`. |
| 4 | CHANNEL | `u16 channel_id`, `u16 schema_id`, `string topic`, `string message_encoding`, `map metadata`; nonzero channel ID, existing schema, nonempty topic, encoding `sdl2`, valid `root_type` metadata. |
| 5 | MESSAGE | `u16 channel_id`, `u32 sequence`, `u64 log_time_ns`, `u64 publish_time_ns`, all remaining bytes as one SDL2 payload. |
| 12 | METADATA | `string name`, `map metadata`. |
| 15 | END | `u64 packet_count`, `u64 message_count`, `u32 stream_crc32`. |

HEADER must be the first application packet and occur exactly once. Schema and
channel declarations precede use; reusing an ID requires an identical declaration.
The payload root ID must select the channel's declared root message. END occurs
exactly once and must be the last packet and last block. Its `packet_count` counts
all packets before END, including HEADER and control records. Its `message_count`
counts only MESSAGE packets. `stream_crc32` is CRC32 over concatenated exact S2RC
packets before END, including their packet CRC fields; it excludes PCAPNG framing,
padding and END itself. END has its own ordinary packet CRC.

Reading a prefix can inspect complete packets from an interrupted recording.
Full validation requires END, matching counts/stream CRC, physical EOF, and valid
SDL message bodies. Missing END detects truncation even exactly at a block boundary.
Changing, dropping or reordering records requires regenerating END. Generic
Wireshark exports of selected packets do not produce a new complete SDL recording.
Use SDL validation before replaying a file into an application; CLI replay does this
preflight automatically.

## Wireshark inspection

Build and install the [native C plugin](../tools/wireshark/README.md), then open
an SDL USER0 capture. The same plugin handles UDP captures and SDL recordings;
Wireshark Lua support and the Rust recording library are unnecessary for dissection.
It registers `sdlrec` for `WTAP_ENCAP_USER0` and uses the existing C dynamic runtime
to display SDL fields, including wire options and quantized floats.

The inspector displays packet kind, catalogue, channel metadata, topic/root,
sequence, exact unsigned 64-bit times, raw SDL bytes and END counters. It checks
packet CRC, counted lengths, UTF-8, duplicate metadata keys, declaration conflicts,
root agreement and SDL message bodies. An initial sequential pass also checks END
counts and stream CRC; revisiting a frame does not increment counters or expose
future declarations to an earlier message. Capture timestamps must equal the
recorded log time (or zero for control records). UDP and recording caches are separate.

Useful display filters include `sdlrec.kind == 5`, `sdlrec.id == 1` and
`sdlrec.topic contains "radio/iq"`. Control bytes in text are escaped; ordinary
topics are stored without surrounding quotes. The plugin assumes the supported
single-section/single-interface profile. Wireshark supplies packet data rather
than complete PCAPNG blocks, so the dissector cannot certify container options,
physical EOF or missing END. Run `sdl-recording validate` for full-file validation.

The exact C parser, declaration state and runtime decoder used by the plugin are
compiled and tested independently of Wireshark. Native loading and UDP/USER0 dissection in one and two passes are also covered by
`make test-wireshark-tshark`, validated with TShark 3.4.10 without Lua.
The 4.0.17/4.4.19 SDK compilation checks do not establish runtime compatibility
with those versions. GUI execution remains untested; see
[build validation](../tools/wireshark/README.md).

## Tests and scope

`make test-recording` includes independent framing/CRC fixtures, conversion,
schema revisions, quantum text, metadata, unsigned extrema, corruption, truncation
and C dissector/native interoperability tests. `make test-recording-native`,
`make test-recording-jni` and `make test-recording-mex` exercise both containers;
the Ada test also selects both formats when GNAT is installed.

This version streams sequentially, with no PCAPNG message index or compression.
Writers retain channel/catalogue declarations rather than per-message PCAPNG
indexes; readers retain declarations and a bounded block. Indexed seeks, clipping,
capture recovery and other link types remain future work.
