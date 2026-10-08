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
contains no catalogue, hash, field IDs, field lengths, alignment, or padding.

## Catalogue text

The canonical rendering uses:

- `SDL2` followed by LF (`53 44 4c 32 0a`).
- Named messages sorted by UTF-8 bytes, then enums sorted the same way.
- `message Name {` or `enum Name {`, LF, declaration body, `}`, LF.
- Exactly two spaces before field/item declarations and no blank lines.
- Fields as `ID: modifier Type[dim] field;`, sorted by numeric field ID.
- Enum items as `NAME = signed_decimal;`, in declaration order.
- UTF-8 text, LF after every line, no BOM and no NUL terminator.

Inline messages become named declarations such as `Envelope$1`. A generated
catalogue contains the declarations from the corresponding SDL file, including
unused message declarations; `description(types)` merges their catalogues and
rejects conflicts. It does not depend on native struct layout. Field names
support diagnostics; IDs identify fields across revisions.

The generated representation is canonical. A decoder may accept additional
whitespace; senders must use the canonical rendering above.

## Values

| Value | Wire representation |
| --- | --- |
| Required | Value directly, including a variable-size string or message |
| Optional | Canonical ULEB128 count 0 or 1, then that many values |
| Repeated | Canonical ULEB128 element count, then contiguous values |
| Packed | Same representation as repeated; element must have fixed wire size |
| Fixed array | Elements in row-major order, dimensions from catalogue, no count |
| Bool | One byte, exactly 0 or 1 |
| int8/int16/int32/int64 | 1/2/4/8 bytes, signed two's complement, big endian |
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
UTF-8 bytes per identifier, 255 fixed dimensions, and 64 recursive processing levels. Native array limits and the accounting
of nested values can further constrain a backend.
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

The exact opening catalogue is 132 bytes. Its UTF-8 text is the source above
prefixed with `SDL2\n`. The fixture is
[`packet.sdl2`](../tst/fixtures/packet.sdl2). Every byte is shown below; offsets
are hexadecimal and ASCII dots denote LF:

```text
0000  53 44 4c 32 0a 6d 65 73 73 61 67 65 20 50 61 63  SDL2.message Pac
0010  6b 65 74 20 7b 0a 20 20 31 3a 20 72 65 71 75 69  ket {.  1: requi
0020  72 65 64 20 62 6f 6f 6c 20 61 63 74 69 76 65 3b  red bool active;
0030  0a 20 20 32 3a 20 6f 70 74 69 6f 6e 61 6c 20 69  .  2: optional i
0040  6e 74 31 36 20 63 6f 64 65 3b 0a 20 20 33 3a 20  nt16 code;.  3:
0050  72 65 71 75 69 72 65 64 20 73 74 72 69 6e 67 20  required string
0060  6c 61 62 65 6c 3b 0a 20 20 34 3a 20 70 61 63 6b  label;.  4: pack
0070  65 64 20 69 6e 74 31 36 20 73 61 6d 70 6c 65 73  ed int16 samples
0080  3b 0a 7d 0a                                      ;.}.
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

For the example stream transport, opening bytes are `00 00 00 84` followed by
the 132 catalogue bytes. Each instance of the data above is preceded by
`00 00 00 10`. Sending N instances costs `136 + 20*N` transport bytes; the
runtime's data buffers themselves cost `16*N`. No catalogue is repeated in data.
