# Native recording services

The optional `runtime/recording` crate implements the
[SDL2 recording profile](recording.md) with the Rust MCAP SDK. It builds an `rlib`,
a shared library and a static library. Applications written in C, Rust, Java,
Ada and MATLAB/Octave use the same engine; Python retains its independent MCAP
implementation. Both implementations exchange the same catalogues and payloads.

No generator change or generated Rust binding is required. An application passes
its exact SDL2 catalogue, root message name and existing encoded byte buffer to
recording services. Its normal backend still owns message structures and encoding.
The recording engine uses the Rust dynamic runtime to validate these bytes; it
never decodes and re-encodes them for recording or replay.

## Building

From the repository root:

```sh
make recording-native           # Rust library and C ABI
make recording-jni              # additionally Java class and C JNI gateway
make recording-mex              # additionally C MEX, built with mkoctfile
```

Rust 1.83 or newer is required. Rust dependencies are pinned by `runtime/recording/Cargo.lock`. Compression features
are disabled. Native libraries go to `build/recording/release`; Java classes go to
`build/recording/java`; the Octave MEX goes to `build/recording/mex`.
These targets are independent of generation and of the core runtime's build.

The automatic gateway builder supports Linux/Android and macOS. `CARGO`, `CC`,
`JAVAC`, `JAVA_HOME` and `MKOCTFILE` can select tools. JNI uses the JDK containing
`javac`, or `JAVA_HOME`. Its source uses Java 7 features; the default build targets
Java 8 because recent JDKs no longer accept target 7. With a suitable JDK, set
`SDL_JAVA_RELEASE=7`. The core Java runtime's requirements are unchanged.

MATLAB users run the following after `make recording-native`:

```matlab
addpath('runtime/recording/matlab');
build_recording_mex(pwd);
addpath('build/recording/mex');
```

The gateway is C99 and uses the separate-complex MEX API available in MATLAB
R2017a. Put the Rust shared library on the OS loader path before starting MATLAB
(for example, `LD_LIBRARY_PATH` on Linux). The Octave builder embeds a relative
loader path. MATLAB execution still requires validation on an actual MATLAB
installation; Octave tests do not certify every MATLAB platform/compiler pair.

Ada applications add `runtime/recording/ada` to the GNAT source path and link
`libsdl_recording`. The binding uses standard Ada packages and no generated types.
For example, add `-Iruntime/recording/ada` and linker arguments
`-L/path/to/build/recording/release -lsdl_recording`, with a corresponding loader
path. Its counted slice type uses GNAT's
[`C_Pass_By_Copy` convention](https://gcc.gnu.org/onlinedocs/gnat_rm/Pragma-C_005fPass_005fBy_005fCopy.html)
to match C structs passed by value. GNAT is unavailable in the current development
environment: this binding and its test are supplied but have not been compiled here.

## Shared behavior

Writers create files exclusively unless overwrite is explicit. They record indexed,
uncompressed chunks and compute chunk, data and summary CRCs. The default chunk
target is 1 MiB. Chunk targets must be positive and at most 32 MiB minus 1024 bytes.
An individual record larger than the target gets its own chunk. Declaration writes
also respect the target, even before any message is written.

Native inputs are bounded: payloads at 32 MiB, catalogue/text arguments at 1 MiB,
and channel/application metadata at 1 MiB. The reader limits an outer MCAP record
to 64 MiB and an SDL2 payload to 32 MiB. It rejects compressed chunks and checks
available CRCs and the final magic. These are implementation resource limits,
not a 255-byte limit: a payload is an array of octets, each with a value from 0 to 255.

Each channel contains immutable topic, catalogue, root type and metadata. Registering
a new channel allocates a new ID, even for identical parameters. Complete catalogue
text is deduplicated; hashes are never used as identity. `root_type` is inserted
into channel metadata; an explicitly conflicting value is rejected. Root IDs and
message bodies are validated before writing. Applications may disable body validation
when their encoder already guarantees valid output; root and size checks remain.

Readers scan in file order and validate bodies, including messages outside their
filter. Topic and absolute log-time filters select `[start,end)` among remaining
records; setting a filter does not rewind the reader. Empty topic selects all topics.
A read prefix is not full validation. Native `validate` scans and decodes the entire
file before returning its message count. Its count and replay's count are unsigned
64-bit values. The SDK retains index entries while writing; readers retain prepared
catalogues/channels and a bounded chunk, rather than all message payloads.

Replay passes unchanged payloads to the consumer. Speed zero skips waits; positive
speed uses absolute monotonic deadlines and rejects decreasing selected log times.
Callback errors stop replay and propagate. Replay starts at the reader's current
position, performs no automatic preflight, and does not reopen or rewind a file.
Call `validate(path)` before application effects when a full preflight is required.

`flush(sync=false)` finishes the current chunk and flushes buffered output;
`flush(sync=true)` additionally syncs the file. Close finishes indexes and reports
IO errors. Rust drop only releases an unclosed writer, leaving an incomplete file.
Java requires explicit close or try-with-resources. Ada finalization and MEX cleanup
try to close, but cannot report errors reliably: applications must close explicitly.

## C ABI and Rust

[The public C header](../runtime/recording/include/sdl_recording.h) defines ABI version
1. `SdlRecordingBytes` is a pointer plus `size_t` length. Text is counted UTF-8 and
may contain embedded zero bytes; a path must still satisfy OS pathname rules.
Input pointers are borrowed during each call. A null pointer is valid only for an
empty slice. Caller-provided pointers must be valid for their declared size.

Handles are opaque, owned by the library and used by one thread at a time. Never
use a handle after close. Functions return `0` on success and `-1` on failure;
open returns null on failure. `reader_next` returns `1` for a record, `0` for complete
EOF and `-1` for failure. `sdl_recording_error()` returns a thread-local UTF-8 string
valid until the next API operation; copy it if it must outlive that operation.
A writer close consumes its handle even when it reports an error.

A returned C record view, including metadata, remains borrowed until the next
next/filter/replay/close call on its reader. Copy bytes before retaining them.
Replay callback views last only for that callback. A callback returns zero to
continue and a nonzero value to stop with an error. It may use other handles, but
cannot next/filter/close/replay its own reader. No foreign exception may unwind
through the C ABI. Rust panics inside exported operations become API errors;
invalid caller pointers remain outside the API contract.

A C application links only the recording library for these services:

```c
#include "sdl_recording.h"
/* path/topic/catalogue/root are SdlRecordingBytes containing counted UTF-8.
   payload is a SdlRecordingBytes view of the existing encoder's output. */
SdlRecordingWriter *writer = sdl_recording_writer_open(path, 0, 1048576);
if (!writer) { /* report sdl_recording_error() */ }
uint16_t channel;
int status = sdl_recording_writer_channel(writer, topic, catalogue, root,
                                          NULL, 0, &channel);
if (status == 0)
    status = sdl_recording_writer_write(writer, channel, payload,
                                       log_ns, publish_ns, sequence);
/* Capture any write error before close clears the thread-local error. */
int close_status = sdl_recording_writer_close(writer);
```

For a complete compiled C client, see
[`tst/recording/native/client.c`](../tst/recording/native/client.c).
Rust applications depend on `sdl-recording` as a separate crate and use
`RecordingWriter`, `RecordingReader`, `Filter`, `validate`, `now_ns` and `replay`.
Returned Rust `Record` objects own their payload and share their channel through
`Arc`; `Record::decode()` returns a dynamic SDL message. Explicitly call writer
`close()` to finish a recording and report errors.

## Java JNI

Include `build/recording/java` in the classpath and
`-Djava.library.path=build/recording/release` when starting Java.

```java
try (SdlRecording.Writer recording = new SdlRecording.Writer("capture.mcap")) {
    int channel = recording.channel("radio/iq", catalogue, "RadioCapture");
    recording.write(channel, encodedPayload, logNs, publishNs, sequence);
}
SdlRecording.validate("capture.mcap");
try (SdlRecording.Reader reader = new SdlRecording.Reader("capture.mcap")) {
    reader.filter("radio/iq", null, null);
    reader.replay(0, new SdlRecording.Consumer() {
        public void accept(SdlRecording.Record record) {
            pipeline(record.payload, record.catalogue); // application consumer
        }
    });
}
```

`write(channel, payload)` defaults both timestamps to Unix nanoseconds and sequence
to zero. Metadata is a `Map<String,String>`; writer `metadata(name,map)` records
application configuration. `validatePayload(false)` disables writer body validation.
Records own copied arrays and strings, so they survive another read or reader close.
JNI converts standard UTF-8 byte arrays, preserving supplementary Unicode and NUL;
unpaired UTF-16 surrogates are rejected on input.

Timestamps retain the unsigned `uint64` bit pattern in Java `long`; negative Java
values represent timestamps above `Long.MAX_VALUE`. IDs are nonnegative `int`, and
sequence is a nonnegative `long` limited to `0xffffffffL`. IO/format errors raise
`IOException`. Consumer exceptions retain their original type and message.
The gateway serializes native operations to protect its handle registry; callbacks
run synchronously on the calling thread. Explicit writer/reader lifetime remains
required. A reader cannot be operated on or closed from its own replay callback.

## MATLAB and Octave C MEX

Add `runtime/recording/matlab` and `build/recording/mex` to the MATLAB/Octave path:

```matlab
writer = sdl_recording('open_writer', 'capture.mcap');
try
    channel = sdl_recording('channel', writer, 'radio/iq', catalogue, 'RadioCapture');
    sdl_recording('write_encoded', writer, channel, uint8(encodedPayload), ...
                  logNs, publishNs, uint32(sequence));
    sdl_recording('close', writer);
catch err
    try, sdl_recording('close', writer); catch, end
    rethrow(err);
end
count = sdl_recording('validate', 'capture.mcap');
reader = sdl_recording('open_reader', 'capture.mcap');
try
    sdl_recording('filter', reader, 'radio/iq', [], []);
    count = sdl_recording('replay', reader, 0, @(record) pipeline(record.payload, record.catalogue));
    sdl_recording('close', reader);
catch err
    try, sdl_recording('close', reader); catch, end
    rethrow(err);
end
```

Handles and timestamps are scalar `uint64`, channels are `uint16` and sequence is
`uint32`. Keep timestamps in integer form throughout; casting an already rounded
`double` cannot recover lost nanoseconds. Payloads are dense `uint8` arrays.
Text inputs are character row vectors; metadata is an N-by-2 cell array of character
vectors. The wrapper converts them to counted UTF-8 for the C gateway and converts
record text back to characters. Native record structs own their payload/text data.
`next` returns `[]` at complete EOF. Runtime errors use `SDL:Recording`; callback
exceptions retain their original identifier/message, including under Octave.

`open_writer(path,overwrite,chunkSize)` accepts optional logical overwrite and
`uint64` chunk size. `write_encoded` defaults omitted log time to `now`, publish
time to log time and sequence to zero. `flush(writer,sync)` defaults sync to false.
`metadata(writer,name,pairs)` and `validate_payload(writer,enabled)` match C services.
Filters use optional absolute start/end timestamps; `[]` means an omitted bound.
The C gateway uses checked tokens, locks the MEX while handles exist, and rejects
stale handles and reentrant access to a replaying reader.

## Ada

`SDL_Recording` provides limited controlled `Writer` and `Reader` types,
`Register_Channel`, `Write_Encoded`, `Write_Metadata`, `Validate_Payload`, `Flush`,
`Close`, `Filter`, `Next`, `Replay`, `Validate` and `Now`. UTF-8 text uses Ada `String`
on input and `Unbounded_String` in returned records. Timestamps are
`Interfaces.Unsigned_64`, sequence is `Unsigned_32`, and metadata is a key/value
array. Returned records own their byte and metadata vectors.

Use `SDL_Recording.Bytes(encoded)` to convert a core Ada runtime byte array for
writing; convert `SDL_Recording.To_Bytes(record.Payload)` back to `SDL_Runtime.Bytes`
for normal decoding. The recording package itself has no dependency on the
core Ada codec. It raises `Recording_Error` for IO/format errors, catches exceptions
inside replay callbacks before they cross C, and reraises the original occurrence
in Ada after replay returns. `Write_Encoded(writer,channel,payload)` supplies Unix
nanoseconds and sequence zero; its full overload takes both timestamps explicitly.

## Tests and current scope

```sh
make recording-deps
make test-recording-native       # Rust API, C client, Python/native interoperability
make test-recording-jni          # JNI execution and Python compatibility
make test-recording-mex          # Octave C MEX execution and Python compatibility
make test-recording-ada          # requires GNAT; not run in the current environment
```

For a native GNAT installation, override `RECORDING_GNATMAKE=gnatmake` and
`RECORDING_ADA_FLAGS='-gnat2012 -gnata -O2'`. Native tests cover exact catalogue and
quantized payload preservation, revisions, unsigned integer extrema, UTF-8/NUL,
exclusive file creation, filtering, callback failures/reentrant access, CRCs,
truncation and declaration conflicts. Compression, indexed seeks, background IO
queues, automatic compatibility mapping to local generated types and file recovery
remain outside these native APIs. Decode raw records with the existing backend;
use the Python recording CLI for dynamic inspection of recordings from any producer.
