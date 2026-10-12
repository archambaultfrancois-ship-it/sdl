# SDL2 recording profile, version 1

This reference defines `sdl2-recording-v1`, an SDL project profile of the
[MCAP container format](https://mcap.dev/spec). The profile and `sdl2` encoding
names are project conventions, not entries in MCAP's official registry.
Recordings use `.mcap` and contain both catalogues and SDL2 payloads. No schema
sidecar, generated class or registry service is required for dynamic decoding.

## Container mapping

| MCAP record | SDL profile requirement |
| --- | --- |
| Header | `profile` is exactly `sdl2-recording-v1`. |
| Schema | Nonzero local ID; `encoding` is `sdl2`; `data` is the exact UTF-8 SDL2 catalogue. |
| Channel | Nonempty `topic`; `message_encoding` is `sdl2`; `schema_id` refers to a catalogue; string metadata `root_type` names a message in that catalogue. |
| Message | `data` is a complete SDL2 payload, including its root message ID. That ID must select the channel's `root_type`. |
| Metadata | Optional string maps for application version, equipment and acquisition configuration. The Python writer uses the name `sdl.recording`. |

The [formal SDL syntax](formal_syntax.md) and [wire reference](wire_descriptor.md)
define the schema and payload bytes. There is no additional S2PS envelope in
`Message.data`. Recording an already encoded message must preserve every byte,
including custom wire representations and quantized floating point values.
Replay must not decode and re-encode the payload before passing it to a consumer.

MCAP schema/channel declarations precede messages that reference them. An unused
declaration may appear only in the summary. Repeated declarations with the same
local ID must be identical. The Python and native writers emit indexed, uncompressed chunks,
data and chunk CRCs, a summary with its CRC, and final file magic. This initial
reader rejects compressed chunks. Compression and indexed seeking are future work;
the current reader scans sequentially, even when a time range is requested.

## Catalogues and evolution

Local MCAP schema IDs identify catalogues within a file. Equality and deduplication
use the complete catalogue text, never the 32-bit SDL catalogue hash. Distinct
catalogues remain distinct even when those hashes collide. A hash reported by
`info` is informational.

Each channel has one immutable catalogue and one root type. Register a new channel
when either changes; the topic may remain the same. Readers prepare a context for
each declaration and decode each message using its own recorded catalogue.
Different versions of a topic can therefore coexist in one file. Registering an
identical catalogue on another channel reuses its schema ID.

With `RecordingReader(path, local_types=[...])`, channel declarations are checked
directionally against the local catalogue, using the SDL compatibility API. Unknown
remote fields can be skipped and missing local fields retain SDL defaults. Every
channel must be compatible with the supplied types, including unused channels.
`record.decode(dynamic=False)` returns a generated object; `record.decode()` returns
an `SdlDynamicMessage`. Wire options are always taken from the recorded catalogue.

## Timestamps and ordering

`log_time_ns` is recording/arrival time and `publish_time_ns` is production or
acquisition time. Both are unsigned 64-bit nanoseconds. Applications should use
Unix time for both, and document any other clock domain in channel metadata.
Log times across selected channels must share a clock domain for meaningful replay.
When a publish time is unavailable, it equals log time. Zero is a timestamp, not a
missing-value marker. The writer's omitted log time defaults to `time.time_ns()`.
Sequence is an unsigned 32-bit application counter; omitted sequence defaults to zero.

Records are returned in file order, retaining timestamp ties. Time filters select
log times in `[start, end)`. The Python API takes absolute nanosecond timestamps;
CLI `--from` and `--to` take offsets from the earliest log time in the whole file,
before topic filtering. CLI durations accept `ns`, `us`, `ms` and `s` with decimal
fractions that resolve to an integral number of nanoseconds.

Paced replay delivers the first selected record immediately and schedules later
ones against absolute deadlines on a monotonic clock. `speed=1` uses recorded log
time intervals, `speed=0.5` doubles them, and `speed=0` delivers without waiting.
Slow consumers cause late delivery; records are neither dropped nor reordered.
Paced replay rejects decreasing log times; unpaced replay preserves their file
order. Publish time and sequence remain unchanged.

Replay preserves recorded inputs. Algorithms that call the current wall clock,
depend on external state or generate random values must use an injected clock or
recorded state if their output must be reproducible.

## Installation and recording API

The core runtime remains dependency free. Optional native recording services for
C, Rust, Java JNI, Ada and MATLAB/Octave C MEX are described in the
[native recording reference](recording_native.md).
The Python implementation requires Python 3.8 or newer
and is an optional module with
a pinned Python MCAP SDK; this version does not need compression plugins:

```sh
make recording-deps
export PYTHONPATH="build/recording-deps:runtime/python:$PYTHONPATH"
make test-recording
```

Alternatively install `tools/recording/requirements.txt` in your Python environment
with `python3 -m pip install --no-deps -r tools/recording/requirements.txt`. The
[MCAP Python writer](https://mcap.dev/docs/python/mcap-apidoc/mcap.writer) supplies
the container implementation. SDL does not introduce a second container format.

```python
from sdl_runtime import description, encode, prepare
from sdl_recording import RecordingWriter
from radio_capture import RadioCapture  # generated application bindings

catalogue = description(RadioCapture)
context = prepare(catalogue, [RadioCapture])
with RecordingWriter("capture.mcap", metadata={"producer": "receiver-v1"}) as recording:
    channel = recording.register_channel(
        topic="radio/iq", catalogue=catalogue, root_type="RadioCapture")
    for capture in acquire_captures():  # application's acquisition source
        payload = encode(context, capture)
        recording.write_encoded(channel, payload,
            publish_time_ns=capture.metadata.captured_at_ns,
            sequence=capture.metadata.sequence)
```

`write_encoded()` validates the message body by default. For an application that
already validates its SDL2 output, `RecordingWriter(..., validate_payload=False)`
avoids that extra decoding pass; size, ownership and root checks still apply.
`write(channel, generated_message, ...)` encodes using the channel's catalogue.
Both methods accept `log_time_ns`, `publish_time_ns` and `sequence`.

Files are created exclusively by default; use `overwrite=True` explicitly to
replace one. Context exit calls `close()`, finishing chunks and indexes. Binary
streams supplied by the caller remain open; streams opened from paths are closed.
`flush()` finishes the current chunk and flushes the stream. `flush(sync=True)`
also calls `os.fsync()` and requires a stream with a file descriptor. Neither
ordinary flush nor close promises survival of a power loss. An interrupted file
is incomplete until recovered, even if it contains readable complete chunks.

Writes are synchronous: recording IO errors propagate to the application. There
is no background queue or silent drop policy. The default chunk target is 1 MiB
and the default payload limit is 32 MiB. A chunk can exceed its target by its final
record. The SDK retains index entries until close; memory can grow with chunk,
channel and catalogue counts, rather than with all message payloads.

## Inspection, validation and replay

From the repository root, with the dependency on `PYTHONPATH`:

```sh
python3 tools/recording/sdl-recording info capture.mcap
python3 tools/recording/sdl-recording inspect capture.mcap --topic radio/iq --from 300ms --limit 5
python3 tools/recording/sdl-recording inspect capture.mcap --format text --topic radio/iq --limit 1
python3 tools/recording/sdl-recording validate capture.mcap
PYTHONPATH="samples/record_replay:$PYTHONPATH" \
  python3 tools/recording/sdl-recording replay capture.mcap \
  --topic radio/iq --from 300ms --to 800ms --speed 0.5 --consumer pipeline:on_record
```

`info` prints JSON containing catalogues, channels, metadata, counts and the minimum
and maximum log time. It checks container framing and roots without decoding
bodies. `inspect` prints one JSON object per selected message. Arrays show their
length and at most eight preview elements by default; `--full` prints all elements.
Enums include their type, name and integer; complex values have `real` and `imag`.
Nonfinite floats are strings (`nan`, `inf`, `-inf`). JSON integers retain their
exact decimal spelling; consumers using binary64 numbers must handle large integers
appropriately. Inspection truncates long strings and limits display nesting.

`inspect --format text` uses the runtime `display()` format, following the C
display routines' structure: named message blocks, braces, indented `field: value`
lines and lists in square brackets. A `RecordingRecord` block contains timestamps,
topic, sequence and the decoded message. Known enums use their names; unknown enum
values use their integers. Complex values are `(real, imag)`, absent optionals are
`null`, and strings are quoted with escaped control characters. Arrays show at most
eight elements and an omitted-element count by default; `--full` renders all values.
Strings are displayed in full in this mode. `--indent-width` selects spaces per level
(default: three). The Python `display()` API accepts typed or dynamic messages and
an optional `max_items` bound; its default continues to render all elements.

`inspect --format table` prints an aligned `FIELD | TYPE | VALUE` table for each
message. Recording attributes use an `@` prefix (for example `@log_time_ns`);
message fields use dotted paths such as `metadata.sequence`. Array rows show
their length, indexed elements and the number of omitted elements. The same
eight-element preview limit applies at each array level; `--full` displays all
elements and complete strings. Enums appear as `NAME (integer)`, complex values
as `(real, imag)`, and absent optional fields as `null`. Strings are quoted and
control characters escaped so tabs and newlines do not disrupt the table.
Long cells wrap within their columns. Topic/time filters and `--limit` work
identically for both formats; the default remains JSON.

`validate` scans to the final magic, checks available data, chunk and summary CRCs,
rejects conflicting declarations and invalid roots, and dynamically decodes every
payload. It fails on malformed or truncated files. It does not certify the semantic
correctness of every index entry. Sequential scanning ignores indexes.

The reader limits each MCAP record to 64 MiB by default, isolates inner field
lengths to that record, and rejects oversized or compressed chunks. The API allows
`max_record_size` to be changed for recordings with larger payloads. It retains
prepared catalogues and channels and at most one chunk of encoded data per scan;
decoded objects can use more memory than their encoded bytes.

`inspect --limit` stops early; it does not validate the rest of the file. CLI replay
performs complete validation before calling application code. It then imports the
explicit `module:function` and calls it once per selected `RecordingRecord`.
Consumers receive topic, catalogue/context, unchanged payload, both timestamps and
sequence; errors stop replay and propagate to the CLI. A consumer can decode, feed
an existing raw-message pipeline, or enqueue the record itself.

Python applications can call the same replay function directly:

```python
from sdl_recording import RecordingReader, replay

reader = RecordingReader("capture.mcap")
reader.validate()  # optional preflight before application effects
replay(reader.iter_records(topics="radio/iq"), on_record, speed=0)
```

The iterator validates records as it reads them; consuming only a prefix cannot
validate the remainder. Direct `replay()` streams its input without an automatic
preflight. Do not modify a file between preflight and replay.

## Demonstration and next steps

[The radio recording demo](../samples/record_replay/README.md) uses one analysis
function for live and replayed IQ frames, and compares their output byte for byte.
It provides a complete acquisition-to-file-to-analysis example without hardware.

CSV/JSON export, self-contained clipping, compression, indexed range reads and
network replay adapters are subsequent additions. MCAP's separate
[CLI](https://mcap.dev/guides/cli) can inspect the container and recover complete
records from damaged files (`mcap recover damaged.mcap -o recovered.mcap`); SDL's
`validate` does not silently repair or skip damaged data. Generic MCAP tools need
an SDL decoder to interpret the message fields.
