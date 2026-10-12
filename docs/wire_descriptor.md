# SDL2 wire format

## Connection catalogue and data

The sender announces one UTF-8 catalogue when opening each direction of a
connection. The receiver calls `prepare` once with that catalogue and its local
types, and reuses the resulting context for subsequent data buffers. Sending
and receiving contexts can differ. Changing a catalogue requires reopening the
connection; adding a declaration can change message type IDs.

The runtime APIs handle description text and data separately. They do not
implement sockets, authentication, catalogue negotiation, or stream framing.
A stream transport must delimit both the opening catalogue and each data
buffer. The socket examples use a four-byte big-endian length before the
catalogue and before each data buffer. Those lengths belong to the transport.
The UDP demonstrations use one datagram for the catalogue and one per message.

Data is:

```
ULEB128(message_type_id) | field_1_value | field_2_value | ...
```

Message IDs are 1-based positions of **all message declarations** in the
catalogue, sorted by UTF-8 name bytes; enums receive no message IDs. Fields
follow ascending field ID. Nested messages carry only their fields. Data
contains no catalogue, hash, field IDs, or field lengths. Ordinary values have
no alignment or padding; explicit `wire(...)` clauses may insert zero padding.

## Catalogue text

The [formal syntax reference](formal_syntax.md) defines the EBNF grammars for
both source schemas and the single-line catalogue.

Source schemas may omit field IDs and the `required` modifier. The generator
resolves these defaults before rendering the compact catalogue. Equivalent
explicit and implicit source schemas produce identical catalogue bytes and hashes.
Source whitespace and line breaks do not affect the catalogue. Dimension constants
are resolved to decimal integers before rendering; the constants themselves are
absent from the catalogue. A named dimension and the same literal dimension
produce identical bytes and hashes.
Integer constant expressions are evaluated before rendering. Type aliases are
replaced by their ultimate built-in, message or enum targets; alias declarations
and names are absent from the catalogue. Equivalent expanded schemas produce
identical bytes and hashes.
Ordinary block comments and retained `/** ... */` documentation are source-only;
they are absent from the catalogue and cannot change its hash or binary layout.

The canonical catalogue is one UTF-8 string with **no whitespace or trailing
newline**. It starts with `SDL2|`; `|` separates declarations. Messages are sorted
by UTF-8 name bytes, followed by enums sorted the same way.

```text
SDL2|Position{1:int32:x;2:int32:y@b12;}|Vehicule{1:uint32:id;2:Position:position;}|#State{STOPPED=0;MOVING=1;}
```

- Messages use `Name{fields}`; enums use `#Name{items}`.
- A packaged type uses its complete dotted logical name, for example
  `navigation.geometry.Point`, both in declarations and field references.
  Global type names have no leading dot. Fields and enum items are unqualified.
- Fields use `ID:Type[dim]:name;`, sorted by numeric ID.
- A marker immediately after the ID encodes cardinality: none for `required`,
  `?` for `optional`, `*` for `repeated`, `+` for `packed`.
- An optional `@` suffix carries representation options, separated by commas:
  `bN` for `bits=N`, `qQ` for a normalized plain decimal quantum `q=Q`,
  `v` for `encoding=varint`, `z` for `encoding=zigzag`,
  `aN` for `align=N`, and `pN` for `pad_after=N`. Canonical option order is
  bits, quantum, encoding, alignment, padding; the semantic restrictions apply.
- Enum items use `NAME=signed_decimal;`, in declaration order.
- Decimal numbers use their shortest spelling; names retain their full spelling.
- Empty messages use `Name{}`. No BOM or terminating NUL is part of the catalogue.

For example, an optional field followed by packed values renders as
`1?:int16:code;2+:uint8:flags@b3;`.

Inline messages become named declarations such as `Envelope$1` or
`navigation.geometry.Envelope$1`. Nested types
are referenced by name and defined once. A generated catalogue contains all
declarations from its SDL file, including unused ones, plus the transitive
declarations referenced from other files; `description(types)`
merges catalogues and rejects conflicts. Changes to a nested declaration
therefore change the enclosing catalogue's hash. The catalogue does not depend
on native struct layout. Field names support diagnostics; IDs identify fields.

All backends emit and parse this compact form directly. The former readable
`SDL2` line-based catalogue is rejected. Source `.sdl` files keep their readable
syntax; they are distinct from runtime catalogues. For hashing or publication,
use the compact canonical output.

## Optional catalogue identity

The runtime identity algorithm `sha256-prefix32-v1` hashes the exact canonical
UTF-8 catalogue bytes, excluding any terminating NUL or transport framing.
Its unsigned 32-bit result is the first four SHA-256 digest bytes interpreted
in big-endian order. The input is the compact canonical string above. The identity API does not
normalize a received catalogue. Hashes from the former readable rendering change.

An identity is optional metadata for an application or transport profile. It
is not added to the SDL2 catalogue or message data by the core codecs. A
transport carrying it must specify the envelope and catalogue registry scope.
Registries using 32-bit identities must detect collisions by comparing full
canonical catalogue bytes before publication; a short identity alone cannot
resolve a collision. See [runtime APIs](runtime.md#catalogue-identity-and-message-inspection).

## Values

The table gives ordinary byte layouts. Field `wire(...)` clauses override the
integer encoding or add alignment/padding according to the catalogue. Clauses
also apply to scalar array elements; nested message bodies reset alignment.


| Value | Wire representation |
| --- | --- |
| Required | Value directly, including a variable-size string or message |
| Optional | Canonical ULEB128 count 0 or 1, then that many values |
| Repeated | Canonical ULEB128 element count, then contiguous values |
| Packed | Same representation as repeated; element must have fixed wire size |
| Fixed array | Elements in row-major order, dimensions from catalogue, no count |
| Bool | One byte, exactly 0 or 1 |
| int8/int16/int32/int64, uint8/uint16/uint32/uint64 | 1/2/4/8 bytes, signed two's complement or unsigned binary, big endian |
| Enum | Signed int32 big endian; value must belong to the declared enum |
| fl32/fl64 | 4/8 bytes, IEEE-754 binary32/binary64, big endian |
| c32/c64 | Real component followed by imaginary component, each fl32/fl64 |
| String | Canonical ULEB128 UTF-8 byte length, then that many bytes |
| Message | Its field values, recursively; no nested type ID |

Empty strings, empty sequences, and absent optionals each need a zero count
byte. An empty message has no body and needs only its top-level type ID.
Strings may contain NUL; their length counts bytes, not characters. There is
no configurable wire byte order.

All counters and type IDs are unsigned 32-bit canonical ULEB128, taking 1–5
bytes. Each byte holds seven low-order value bits; bit 7 means another byte
follows. Thus 127 is `7f`, 128 is `80 01`, and 300 is `ac 02`.
Overlong encodings (such as `80 00`), overflow, truncated counters, unknown
message IDs, trailing bytes, invalid UTF-8, bools, and enum values are errors.
Runtime catalogue limits are 1 MiB, 65,535 declarations/fields/items, 65,535
UTF-8 bytes per identifier, 255 fixed dimensions per field, and 64 nested message
references along any path (65 messages including the root). C payload validation
and decoding count message references independently of field visits, sequence
elements, and fixed-array dimensions. Native array limits can further constrain
a backend.
Variable-size sequence decoding is capped at 1,048,576 elements; fixed-size
sequences are additionally checked against remaining input before allocation.
The limits protect preparation and allocation, independently of transport size
limits. Fixed-array products must fit u32 wire size.

## Schema evolution

`prepare(remote_description, local_types)` matches message logical names and
field IDs. Renaming a field preserves compatibility. Unknown fields are
consumed and validated using the sender's layout. Missing local fields retain
the generated defaults; this also applies to a local required field added
after the sender's revision. A required field present in the sender catalogue
must have its value in data; truncation never supplies a default.

Matching IDs must preserve logical type name, dimensions, and cardinality.
Repeated and packed are compatible because their wire layouts coincide.
Changing required to optional, changing a primitive width, or reusing an ID for
a different type is rejected. Enum values must also be representable by the
local generated enum. Encoding requires a context describing the emitter's
layout; a context prepared for receiving an older layout is not a substitute.

This design saves per-message metadata but makes data dependent on the opening
catalogue. An isolated data buffer cannot describe itself. Consumers storing
or forwarding data must retain the catalogue. Unknown values cannot be skipped
from their own headers; the receiver follows the prepared layout, including
validating strings and nested values.

## Complete byte example

Source [`wire_example.sdl`](../sdl/wire_example.sdl):

```sdl
message Packet {
  1: required bool active;
  2: optional int16 code;
  3: required string label;
  4: packed int16 samples;
}
```

The exact opening catalogue is 73 bytes, without a newline:

```text
SDL2|Packet{1:bool:active;2?:int16:code;3:string:label;4+:int16:samples;}
```

The fixture is [`packet.sdl2`](../tst/fixtures/packet.sdl2). Every byte is shown
below; offsets are hexadecimal:

```text
0000  53 44 4c 32 7c 50 61 63 6b 65 74 7b 31 3a 62 6f  SDL2|Packet{1:bo
0010  6f 6c 3a 61 63 74 69 76 65 3b 32 3f 3a 69 6e 74  ol:active;2?:int
0020  31 36 3a 63 6f 64 65 3b 33 3a 73 74 72 69 6e 67  16:code;3:string
0030  3a 6c 61 62 65 6c 3b 34 2b 3a 69 6e 74 31 36 3a  :label;4+:int16:
0040  73 61 6d 70 6c 65 73 3b 7d                       samples;}
```

`Packet` is the only message, so its type ID is 1. Associated values are
`active=true`, `code=-2`, `label="été"`, `samples=[300, -1]`.
The complete data buffer is **16 bytes**
([`packet.bin`](../tst/fixtures/packet.bin)):

```text
01 01 01 ff fe 05 c3 a9 74 c3 a9 02 01 2c ff ff
```

| Decimal offset | Hex byte | Interpretation |
| --- | --- | --- |
| 0 | 01 | Message type ID 1 |
| 1 | 01 | active = true |
| 2 | 01 | code present (one value) |
| 3 | ff | code high byte |
| 4 | fe | code low byte; ff fe = -2 |
| 5 | 05 | label length: five UTF-8 bytes |
| 6 | c3 | First byte of é |
| 7 | a9 | Second byte of é |
| 8 | 74 | t |
| 9 | c3 | First byte of é |
| 10 | a9 | Second byte of é |
| 11 | 02 | samples element count: two |
| 12 | 01 | First sample high byte |
| 13 | 2c | First sample low byte; 01 2c = 300 |
| 14 | ff | Second sample high byte |
| 15 | ff | Second sample low byte; ff ff = -1 |

For the example stream transport, opening bytes are `00 00 00 49` followed by
the 73 catalogue bytes. Each instance of the data above is preceded by
`00 00 00 10`. Sending N instances costs `77 + 20*N` transport bytes; the
runtime's data buffers themselves cost `16*N`. No catalogue is repeated in data.
