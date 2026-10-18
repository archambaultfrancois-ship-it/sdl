# SDL feature overview

## Schemas and code generation

- **[Language bindings](../README.md#generate-bindings)**: connect C99, Rust, Python, Java, Ada and MATLAB/Octave applications from one schema.
- **[Rich data types](schema.md#field-types-and-modifiers)**: exchange numbers, Unicode text, enums, complex values, nested records and arrays.
- **[Optional fields and collections](schema.md#field-types-and-modifiers)**: represent missing values and variable-length data.
- **[Reusable schemas](schema.md#packages-and-type-references)**: share packages, aliases and constants across files.
- **[Schema shortcuts](schema.md#automatic-field-numbers)**: omit field numbers and required modifiers when defaults suffice.
- **[Generated documentation](schema.md#comments-and-generated-documentation)**: carry schema comments into all six languages.
- **[Binding regeneration](../README.md#generate-bindings)**: update interfaces and remove obsolete generated files.

## Message exchange and compatibility

- **[Compact binary exchange](wire_descriptor.md)**: share the catalogue once, then send compact messages.
- **[Message validation](runtime.md)**: reject malformed payloads, including invalid skipped fields.
- **[Schema evolution](runtime.md#directional-compatibility-and-diagnostics)**: exchange compatible versions with renamed, added or missing fields.
- **[Compatibility checks](runtime.md#directional-compatibility-and-diagnostics)**: check consumer readability and locate the first mismatch.
- **[Dynamic decoding](runtime.md)**: read messages without generated classes in C, Rust and Python.
- **[Message identification](pubsub_envelope.md)**: discover types and reference catalogues through message envelopes.

## Performance and filtering

- **[Custom encoding](schema.md#field-wire-representation)**: shrink messages with bit widths and variable-length integers.
- **[Float quantization](schema.md#field-wire-representation)**: trade measurement precision for smaller payloads.
- **[Automatic codec optimization](runtime.md#automatic-fixed-layout-codecs)**: accelerate eligible fixed records and arrays.
- **[Python buffers](benchmarks.md#python-packed-buffer-api)**: view supported packed arrays and encode into caller storage.
- **[Content filtering](filtering.md)**: select messages using expressions over fields and arrays.
- **[Encoded-data filtering](filtering.md)**: evaluate criteria before fully decoding messages.

## Recording and replay

- **[Self-contained recordings](recording.md)**: store messages, schemas, metadata and timestamps in PCAPNG (default) or MCAP.
- **[Multiple schema versions](recording.md#catalogues-and-evolution)**: record and read different topic versions in one file.
- **[Recording inspection](recording.md#inspection-validation-and-replay)**: browse messages by topic and time range as JSON, text or tables from the CLI.
- **[Recording summaries](recording.md#inspection-validation-and-replay)**: report per-topic counts, time bounds and duration for a selection.
- **[Recording validation](recording.md#inspection-validation-and-replay)**: check selected message bodies and container integrity for malformed or incomplete recordings.
- **[Selective replay](recording.md#timestamps-and-ordering)**: replay topics and time ranges at original, adjusted or maximum speed.
- **[Recording conversion](recording_pcapng.md#recording-and-tools)**: convert MCAP ↔ PCAPNG while preserving SDL data and metadata.

## Network integration and inspection

- **[Wireshark inspection](../tools/wireshark/README.md)**: inspect catalogued SDL UDP traffic and SDL USER0 recordings.
- **[TCP pub/sub](../examples/PoC_PubSub/README.md)** *(Python demo)*: share one topic, resolve catalogues and replay bounded in-memory history.
- **[Topic contracts](../examples/PoC_PubSub/README.md#topic-contract-manifest-and-offline-validation)** *(demo)*: check catalogues offline or during broker registration.
