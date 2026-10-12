# SDL schema language

This page describes the SDL syntax accepted by the current generator and the
constraints shared by its C, Rust, Python 3, Matlab/Octave, and Java backends. SDL schemas describe
logical messages; target-language memory layout is generated separately. The
wire representation is described in [wire_descriptor.md](wire_descriptor.md).

## Declarations

A schema contains named messages and enums. The keywords `message`, `structure`,
and `struct` are interchangeable for named message declarations:

```sdl
structure Point {
   fl64 x;
   fl64 y;
}

struct Packet {
   Point position;
}
```

These aliases have identical semantics and generate identical code, catalogue
bytes, and hashes. The compact canonical SDL2 catalogue omits the declaration keyword, regardless
of the source spelling.

Put one declaration on each line;
`//` starts a line comment. Braces delimit declarations, and fields and enum
items end with semicolons. When the generator reads a directory, each `.sdl`
file is parsed as a separate schema: types referenced by a message must be
declared in the same file. Type names must also remain unique across all files
after C identifier normalization, including case folding.
Message dependencies must be acyclic and contain at most 64 nested message
references along any path. The generator checks this limit before emitting
bindings, including paths through shared subtypes. Text after an enum item's
semicolon must be whitespace or a `//` comment.

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
digits. Leading zeros do not change their decimal value; generated bindings
emit normalized decimal literals. Field IDs are positive unsigned 32-bit integers written with ASCII
decimal digits and must be unique within their message. The generator and runtimes limit declaration counts and UTF-8 identifier
lengths to 65535; a field can have at most 255
fixed-array dimensions. The complete textual catalogue is limited to 1 MiB.

## Automatic field numbers

The field number and colon may be omitted in source schemas. The modifier may
also be omitted independently, in which case it defaults to `required`:

| Source field | Resolved field, when first in its message |
| --- | --- |
| `int32 value;` | `1: required int32 value;` |
| `required int32 value;` | `1: required int32 value;` |
| `7: int32 value;` | `7: required int32 value;` |
| `optional int32 value;` | `1: optional int32 value;` |

Explicit and implicit declarations may be mixed:

```sdl
message Packet {
   required bool active;       // 1
   optional int16 code;       // 2
   10: required string label;
   packed int16 samples;      // 11
}
```

An unnumbered first field receives ID 1. Each subsequent unnumbered field
receives the previous field's ID plus one, including when the previous ID was
explicit. Each named or anonymous nested message has an independent sequence.
Duplicate IDs and values exceeding uint32 remain errors; automatic numbering
does not skip occupied IDs. The canonical SDL2 catalogue always includes the
resolved numeric IDs, so equivalent explicit and implicit schemas generate
identical code, catalogue bytes, and catalogue hashes. Reordering or inserting implicitly numbered
fields can change their IDs; use explicit IDs to preserve field identity across
schema revisions.

For example, `2: bool first;`, followed by `1: bool second;` and `bool third;`,
assigns ID 2 to both `first` and `third` and is rejected. An implicit field after
ID 4294967295 is also rejected because its ID would exceed uint32.

## Field types and modifiers

Built-in types are `bool`, `int8`, `int16`, `int32`, `int64`, `uint8`, `uint16`,
`uint32`, `uint64`, `fl32`, `fl64`,
`c32`, `c64`, and `string`. A field may also use a declared message or enum
type. SDL `bool` values encode as one byte (`0` for false and `1` for true);
Python encoding requires actual `bool` objects, and all decoders reject other
wire byte values. Omitting the field modifier defaults to `required`, independently
of whether the field number is explicit or automatic. This also applies to fixed
arrays and inline `struct` fields:

```sdl
message Position {
   fl64 longitude;          // 1: required fl64 longitude;
   10: fl64 latitude;       // 10: required fl64 latitude;
   optional fl64 altitude;  // 11: optional fl64 altitude;
}
```

These defaults are source-language conveniences shared by all six backends;
runtime catalogues require explicit field IDs. The compact canonical catalogue
encodes required fields without a marker and the other cardinalities with
`?`, `*`, or `+`. The four
field modifiers are:

- `required`: a singular field without a presence flag. Every field declared required by the sender must be
  present in data. A new local field absent from an older sender catalogue
  retains its generated default.
- `optional`: one value may be present or absent, with absence represented
  explicitly (`has_` flag in C, `Option` in Rust, and `None` in Python).
- `repeated`: zero or more contiguous values following one ULEB128 element count.
- `packed`: the same wire representation as repeated, constrained to fixed-width
  elements so runtimes can use bulk conversion.

For example, `4: repeated int16 samples;` declares a dynamic-length list, while
`5: packed int16 samples;` uses the same positional sequence layout. `packed` requires an
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
followed by its name. Inline declarations accept `struct`, `structure`, or
`message` interchangeably:

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
decimal digits. The total wire size of an array field must fit in an unsigned 32-bit wire size. Empty messages are valid as standalone messages but do not
qualify as fixed-size array elements. The C backend adds a
`uint8_t sdl_empty_placeholder` member to an empty generated struct because
C99 does not permit a struct with no members; the placeholder is not part of
the SDL schema or wire representation.

## Current target support

The generator currently has C99, Rust, Python 3, Matlab/Octave, and Java
backends. Schema syntax and the wire descriptor are language-neutral, so each
backend maps the same logical schema to its target language without inheriting
another language's memory-layout assumptions. Additional backends can implement the same catalogue and positional values.

## Unsigned integers

`uint8`, `uint16`, `uint32`, and `uint64` represent integers from zero through
2^N - 1, with N the type's width. Their ordinary wire encoding uses exactly
N/8 bytes in big-endian order, including when used in arrays or nested messages.
Signed and unsigned types remain distinct logical types for schema compatibility.

| SDL | C | Rust | Python | Java | Ada | Matlab/Octave |
| --- | --- | --- | --- | --- | --- | --- |
| `uint8` | `uint8_t` | `u8` | `int` | `byte` | `Unsigned_8` | `uint8` |
| `uint16` | `uint16_t` | `u16` | `int` | `short` | `Unsigned_16` | `uint16` |
| `uint32` | `uint32_t` | `u32` | `int` | `int` | `Unsigned_32` | `uint32` |
| `uint64` | `uint64_t` | `u64` | `int` | `long` | `Unsigned_64` | `uint64` |

Java preserves unsigned values as raw bits in primitives of the same width. For
example, SDL `uint64` maximum is stored as Java `-1L`; encoding preserves all 64
bits. Ordinary Java arithmetic and comparisons remain signed. The Java 7 codec
provides `SdlCodec.unsignedString(value, width)` and
`SdlCodec.compareUnsigned(a, b, width)` for unsigned display and ordering. Scalar
unsigned fields use unsigned decimal formatting in generated message displays.
Python and Matlab/Octave reject unsigned inputs outside the native type's range.
Dynamic C values use `SDL_DYNAMIC_UNSIGNED` and `unsigned_integer`; Rust uses
`DynamicValue::Unsigned(u64)`; Python returns positive `int` values.

## Field wire representation

An optional `wire(...)` clause follows the field name and precedes its semicolon.
It changes the transmitted representation while retaining the logical type and
native generated structure. It combines with implicit field IDs and `required`:

```sdl
message Sample {
   bool active wire(bits=1);
   bool alarm wire(bits=1);
   int16 level wire(bits=6);
   uint64 counter wire(encoding=varint);
   int64 delta wire(encoding=zigzag);
   fl64 value wire(align=8,pad_after=2);
}
```

Here the first three fields occupy one byte. `level` accepts -32 through 31 at
encoding; `counter` uses unsigned ULEB128, `delta` uses ZigZag followed by ULEB128,
and `value` begins on an 8-byte boundary and is followed by two zero bytes.

| Option | Rules |
| --- | --- |
| `bits=N` | Integers and enums: 1 through their native width; `bool`: 1. Applies to every scalar element of a sequence or fixed array. |
| `encoding=varint` | Integers and enums, as canonical unsigned ULEB128. Signed inputs must be nonnegative. Up to 10 bytes for `uint64`. |
| `encoding=zigzag` | Signed integers and enums, mapped to unsigned ULEB128. Covers the complete signed range. |
| `align=N` | Aligns the start of the field, including any optional/sequence count, to N bytes from the enclosing message body's start. N is a power of two from 1 through 256. |
| `pad_after=N` | Inserts N zero bytes after the complete field, including all its elements. N is 0 through 65535; zero has no effect. |

`bits` and `encoding` are mutually exclusive. Unknown keys, duplicate keys,
empty clauses, invalid widths and invalid option combinations are rejected.
Options may be written in any order with spaces in source schemas. Canonical
catalogues emit them in order `bits`, `encoding`, `align`, `pad_after`, separated
by commas without spaces.

Signed bit fields use two's complement. For example,
`int32 temperature wire(bits=12);` accepts -2048 through 2047 on encoding and
sign-extends on decoding. Unsigned bit fields accept zero through 2^N - 1.
Out-of-range values raise codec errors, never silently truncate. Java performs
these checks according to the SDL signedness, rather than the Java storage type.

Bits are written most-significant first. Consecutive bit fields share bytes.
Ordinary fields, optional/sequence counters, explicit alignment (including
`align=1`), positive `pad_after`, and the end of a message finish the current
byte with zero bits. Sequence and fixed-array elements with `bits` remain
contiguous. Nested messages start and finish on byte boundaries and use their
own body-relative alignment origin. The top-level message ID is outside that
origin. Every explicit and implicit padding bit or byte must be zero; decoders
reject nonzero padding, truncated values, overflowing or noncanonical varints,
and trailing bytes.

Aligning a nested message field or appending padding to it is written after its
closing field name, for example:

```sdl
message Envelope {
   struct {
      int32 code wire(bits=12);
   } result wire(align=4,pad_after=1);
}
```

Representation clauses appear as compact suffixes (`@b12`, `@v`, `@z`,
`@a4,p1`) in canonical catalogues, participate in their
hashes, and must be understood by receiving runtimes. Older runtimes reject the
extended clauses and new unsigned primitive names. Without clauses, existing
schemas retain their previous bytes. Logical compatibility checks compare
field types and cardinalities; representation differences alone do not make
messages incompatible, since decoding uses the sender's catalogue.

Custom representations currently convert through the ordinary byte layout at
the message API boundary; they do not provide direct native memory access.
Low-level generated fixed codecs describe that ordinary layout. Use the public
context-based message encode/decode APIs to apply representation clauses.
Fixed-array and `packed` eligibility is checked against the ordinary logical
layout; a custom varint clause can make the actual transmitted element width
variable. Conversion buffers are limited to 64 MiB, each sequence or array dimension to
1,048,576 elements, and nesting to 64. Python native packed views are unavailable
for contexts containing custom representations. Native memory alignment clauses
are separate from `wire(align=...)` and are not implemented here.
