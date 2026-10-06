# SDL schema language

This page describes the SDL syntax accepted by the current generator and the
constraints shared by its C, Rust, Python 3, Matlab/Octave, and Java backends. SDL schemas describe
logical messages; target-language memory layout is generated separately. The
wire representation is described in [wire_descriptor.md](wire_descriptor.md).

## Declarations

A schema contains named messages and enums. Put one declaration on each line;
`//` starts a line comment. Braces delimit declarations, and fields and enum
items end with semicolons. When the generator reads a directory, each `.sdl`
file is parsed as a separate schema: types referenced by a message must be
declared in the same file. Type names must also remain unique across all files
after C identifier normalization, including case folding.

```sdl
enum State {
   READY = 1;
   FAILED = -1;
}

message Point {
   1: required fl64 x;
   2: required fl64 y;
}

message Packet {
   1: required string label;
   2: optional State state;
   3: repeated Point points;
}
```

Message and enum names, field names, and enum item names use word characters
(`A-Z`, `a-z`, digits, and underscore in ordinary schemas). The C backend
requires portable ASCII identifiers that do not conflict with C or runtime
names for emitted types, fields, and enum items. Rust validates identifiers
after its name conversion. Python rejects names that collide with runtime
methods or metadata, invalid normalized identifiers, and reserved `Enum` names.
Matlab/Octave and Java apply their target-language identifier and reserved-name
rules during generation.
Backends reject schema filenames that collide with generated package or registry
files, or that produce invalid module names. Type names must be unique across
the input schema set and cannot reuse built-in type names. Enum item names and
numeric values must each be unique within their enum. Enums must declare at
least one item; values are signed 32-bit integers written with ASCII decimal
digits. Field IDs are positive unsigned 32-bit integers written with ASCII
decimal digits and must be unique within their message. The wire descriptor
stores counts and UTF-8 string lengths in `u16` fields, so declaration counts
and encoded names must each fit in 65535; a field can have at most 255
fixed-array dimensions. The complete descriptor for any message is limited to
1 MiB to match the dynamic decoders.

## Field types and modifiers

Built-in types are `bool`, `int8`, `int16`, `int32`, `int64`, `fl32`, `fl64`,
`c32`, `c64`, and `string`. A field may also use a declared message or enum
type. SDL `bool` values encode as one byte (`0` for false and `1` for true);
Python encoding requires actual `bool` objects, and all decoders reject other
wire byte values. The four field modifiers are:

- `required`: a singular field without a presence flag. Encoders emit it, but
  decoders currently accept it when absent and leave the target-language
  default value.
- `optional`: one value may be present or absent, with absence represented
  explicitly (`has_` flag in C, `Option` in Rust, and `None` in Python).
- `repeated`: zero or more values, each in its own wire field occurrence.
- `packed`: zero or more fixed-width values contiguous in one field payload.

For example, `4: repeated int16 samples;` declares a dynamic-length list, while
`5: packed int16 samples;` uses a contiguous payload. `packed` requires an
element type with a fixed wire size. Primitive values (including one-byte
`bool` values), enums, and fixed-size messages can be packed; strings and
dynamically sized messages cannot.

In C, optional fields have a generated presence flag, and repeated or packed
fields have a generated count member. Rust and Python use their native optional
and sequence representations. Java uses nullable values for optional fields,
lists for repeated and packed fields, and native arrays for fixed arrays.
Matlab/Octave maps values to its generated structures and arrays. The generated
bindings document target-specific member names and value representations.

## Anonymous nested messages

A message may be declared inline as a field type. The field's closing brace is
followed by its name:

```sdl
message Envelope {
   1: required struct {
      1: required int32 code;
      2: required string detail;
   } result;
}
```

The generator assigns stable logical names such as `Envelope$1` to these
messages. Backends map those names to valid target-language identifiers.
Recursive message references are rejected, including cycles spanning several
named or anonymous messages.

## Fixed-size arrays

Append one or more positive dimensions to the type name to declare a
multidimensional fixed-size array:

```sdl
message Matrix {
   1: required fl32[3][4] values;
}
```

Fixed arrays use row-major order on the wire and carry no element count or
per-element headers. Their fields must be `required`. Every element must have
a statically known wire size. A message used as an element must itself consist
only of required fixed-size fields, including any nested fixed arrays; it may
not contain strings, optional fields, repeated fields, or other variable-size
values. The generator reports the field whose fixed-size constraint fails.

Fixed-array dimensions are positive unsigned 32-bit values written with ASCII
decimal digits. The total wire size of an array field must fit in an unsigned
32-bit field length. Empty messages are valid as standalone messages but do not
qualify as fixed-size array elements. The C backend adds a
`uint8_t sdl_empty_placeholder` member to an empty generated struct because
C99 does not permit a struct with no members; the placeholder is not part of
the SDL schema or wire representation.

## Current target support

The generator currently has C99, Rust, Python 3, Matlab/Octave, and Java
backends. Schema syntax and the wire descriptor are language-neutral, so each
backend maps the same logical schema to its target language without inheriting
another language's memory-layout assumptions. The wire descriptor leaves room
for additional future backends.
