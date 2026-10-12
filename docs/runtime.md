# Runtime APIs

The runtimes provide two ways to read an SDL frame:

- **Typed decoding** maps values to types generated from a schema. Use it when
  the schema is known at build time.
- **Dynamic decoding** reads the schema descriptor carried by the frame and
  returns neutral values. It does not require generated message classes or a
  C type registry.

Both paths validate the frame and reject malformed known fields. See
[wire_descriptor.md](wire_descriptor.md) for field occurrence rules, byte
order, and the descriptor format.

## C

Include the generated message header, `sdl_registry.h`, and `type_engine.h`.
Call `register_all_types()` before typed encoding, decoding, cloning, or display.
Generated C headers define the message structs; for example, a schema file
named `telemetry.sdl` generates `telemetry.h`.

```c
register_all_types();

size_t frame_size = 0;
void *frame = type_encode("Telemetry", &message, &frame_size);
if (frame != NULL) {
   void *decoded = type_decode(frame, &frame_size);
   if (decoded != NULL) {
      /* Use decoded as the generated Telemetry type. */
      type_free(decoded);
   }
   type_free(frame);
}
```

`type_decode_dynamic(frame, size)` decodes without generated structs or
registration. It returns an `SdlDynamicMessage *`, or `NULL` on invalid input
or allocation failure. Use `type_dynamic_get(message, "field_name")` to look
up a borrowed field value. Release the complete result with
`type_dynamic_free(message)`; do not free fields returned by the lookup
separately. Values inside arrays and messages are represented recursively by
`SdlDynamicValue`.

Buffers returned by `type_encode`, `type_decode`, `type_clone`, and
`type_display` are owned by the caller and released with `type_free()`.

The C runtime requires native `float` and `double` bit patterns to use IEEE-754
binary32 and binary64 encoding, with 8-bit bytes. `sdl_wire.c` checks the
byte width plus their radix, precision, exponent ranges, and storage widths
at compile time. C99 does not provide a portable compile-time test for the
exact bit encoding, so the target compiler and platform must provide the
IEEE-754 representation as well. C wire conversion also assumes floating-point
objects use the same byte order as host integers; mixed-endian float layouts
are unsupported.

### Generated field storage

Generated C structs use a presence flag for each optional field, followed by
the value. A repeated or packed field has a `uint32_t <field>_count` and a
pointer to its elements. A fixed array is an inline C array. An empty SDL
message gets a `uint8_t sdl_empty_placeholder` member so its C struct is valid
under C99; the member has no wire representation. For example:

```sdl
message Reading {
   1: optional fl64 temperature;
   2: repeated int16 samples;
   3: packed c32 spectrum;
   4: required int8[3] tag;
}
```

has fields conceptually equivalent to `bool has_temperature; double
temperature;`, `uint32_t samples_count; int16_t *samples;`,
`uint32_t spectrum_count; float complex *spectrum;`, and
`int8_t tag[3];`. Use the generated declarations as the authoritative C types;
names and helper fields can vary to avoid collisions. Zero-initialize messages
before setting fields. For dynamically sized fields, the caller supplies the
count and storage before encoding; the encoder does not take ownership of
those input pointers. Generated string fields also have byte-length companions.
A zero length selects `strlen` for ordinary C strings; set an explicit length
to preserve embedded or trailing NUL bytes. A null string pointer encodes as an
empty string unless paired with a nonzero explicit length, which is rejected.
Wire string lengths count UTF-8 bytes and exclude only the C storage
terminator.

`type_encode_size(type, &message)` returns the complete frame size, or zero if
the type is not registered or the value cannot be sized. `type_encode` returns
an allocated frame or `NULL`. `type_decode_size(frame, size)` reports the C
storage required for a typed decode, or zero if the frame is invalid.
`type_decode(frame, &size)` decodes a complete frame into one allocation;
the input size is available bytes and is not modified. Release decoded values
and encoded frames with `type_free()`.

Call `type_clone(type, &message)` to deep-copy a message, including its
strings and arrays, into one allocation. `type_display(type, &message,
indent_width)` returns an allocated diagnostic string and uses spaces per
nested level. These registry-based calls return failure values (`0` or `NULL`)
for unknown types, invalid values or frames, and allocation failures.

### Descriptor-driven C decoding

`type_decode_dynamic(frame, size)` returns an owned `SdlDynamicMessage` and
does not require generated headers or `register_all_types()`. Look up a field
with `type_dynamic_get(message, "field")`; the returned pointer is borrowed
from the message. Inspect `SdlDynamicValue.kind` before reading the matching
union member. Dynamic arrays and nested messages are represented recursively;
strings carry a byte pointer and byte length, so they need not be NUL
terminated. Free the complete tree with `type_dynamic_free()`.

## Rust

Generate Rust modules with `gen/generator.py -rust`. For
`sdl/schema.sdl`, the output includes `schema.rs` and a `mod.rs` that declares
that module. From the repository root, run:

```sh
python3 gen/generator.py -rust sdl/schema.sdl build/generated/rust
```

Add this repository's `runtime/rust` crate as the `sdl-runtime` dependency in
the consumer's `Cargo.toml` (adjust the relative path):

```toml
[dependencies]
sdl-runtime = { path = "../runtime/rust" }
```

Expose the generated module tree from the consumer crate. With generated files
in `build/generated/rust` at the crate root, `src/main.rs` can use:

```rust
#[path = "../build/generated/rust/mod.rs"]
mod generated;

use generated::schema::RootPayload;
```

Generated Rust modules implement `SdlMessage`:

```rust
let message = RootPayload::default();
let frame = sdl_runtime::encode(&message)?;
let decoded: RootPayload = sdl_runtime::decode(&frame)?;
let dynamic = sdl_runtime::decode_dynamic(&frame)?;
```

`decode_dynamic` returns a `DynamicMessage` with its SDL type name and a map
of field names to `DynamicValue`. Rust owns these values, so no explicit
release call is needed. Codec operations return `Result<_, CodecError>`.
Generated fields use `Option<T>` for optional values, `Vec<T>` for repeated and
packed values, and Rust arrays such as `[i8; 3]` for fixed dimensions. The
generated declarations are the authoritative type definitions.

## Python 3

Generate bindings with `gen/generator.py -python`. For `sdl/schema.sdl`, the
output includes `schema.py`. Add both the generated Python directory and
`runtime/python` to `PYTHONPATH`, then import the message class from that
module. From the repository root, for example:

```sh
python3 gen/generator.py -python sdl/schema.sdl build/generated/python
PYTHONPATH=runtime/python:build/generated/python python3 app.py
```

```python
from sdl_runtime import decode, decode_dynamic, encode
from schema import RootPayload

message = RootPayload(header="demo")
frame = encode(message)
decoded = decode(frame, RootPayload)
dynamic = decode_dynamic(frame)
```

`decode_dynamic` returns an `SdlDynamicMessage`; its `fields` dictionary maps
SDL field names to Python values, including nested dynamic messages and enum
values. Codec errors are reported as `sdl_runtime.CodecError`.
Generated optional fields default to `None`; repeated and packed fields
default to empty lists. Fixed arrays are nested lists with the declared
dimensions. The generated class definitions are the authoritative Python
field types.

## Matlab

Generate the Matlab backend with `gen/generator.py -matlab`. The generated
codec is intended to work with both Matlab and GNU Octave. `make test-matlab`
uses Matlab in batch mode when `matlab` is available in `PATH`, and otherwise
falls back to Octave. Override the executable names with `matlab=...` or
`OCTAVE=...` when needed.

## Java 7+

Generate Java sources with `gen/generator.py -java`. Each `.sdl` file produces
one wrapper class in the default Java package, with its enums and messages as
public static nested types. `SdlCodec.java` is the dependency-free runtime.
From the repository root:

```sh
python3 gen/generator.py -java sdl build/generated
mkdir -p build/java-classes
javac -d build/java-classes build/generated/java/*.java
```

For a schema file named `codec_cases.sdl`, use `codec_cases.CodecCases` and
`codec_cases.State`. Message fields are public and strongly typed: optional
fields are nullable boxed values, repeated and packed fields are mutable
`java.util.List` instances, and fixed arrays use Java arrays. Complex numbers
are represented by `SdlCodec.Complex32` and `SdlCodec.Complex64`.
Packed complex fields use `SdlCodec.Complex32Array` or
`SdlCodec.Complex64Array`; each stores its real and imaginary components in
primitive `re` and `im` arrays. Assign equal-length arrays directly to avoid
allocating one Java object per complex value.

```java
codec_cases.CodecCases message = new codec_cases.CodecCases();
message.state = codec_cases.State.READY;
message.samples.add(Short.valueOf((short) 12));
message.points = new SdlCodec.Complex32Array(
    new float[] { 1.0f, 2.0f }, new float[] { -1.0f, 0.5f });
byte[] frame = message.encode();
codec_cases.CodecCases copy = codec_cases.CodecCases.decode(frame);
```

Generated code and runtime use Java 7 language and library features. The local
`make test-java` target defaults to compiler source/target 8 because newer JDKs
removed Java 7 source mode; on a JDK that still accepts Java 7, pass
`JAVA_SOURCE=1.7`. The runtime reads `SDL_WIRE_ENDIAN=little` for little endian;
otherwise it uses big endian.

## Wire byte order

All peers exchanging frames must use the same configured byte order. The
runtime defaults to big endian. See [wire_descriptor.md](wire_descriptor.md)
for the wire descriptor and per-language configuration options.
