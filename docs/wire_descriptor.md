# SDL wire descriptor, version 1

The wire descriptor is the language-neutral schema carried in each SDL message
frame. It describes logical fields and wire types only. It never describes C,
Rust, Java, Ada, or Python memory layout.

## Frame

The frame is a payload-endian `u32` descriptor length, that many descriptor
bytes, a payload-endian `u32` FNV-1a fingerprint of the descriptor bytes, and
the message body. The fingerprint uses the 32-bit FNV-1a offset basis
`2166136261` and prime `16777619`; it is an identifier and consistency check,
not a cryptographic authenticator.

The frame length, fingerprint, body field IDs, body field lengths, and primitive
payloads use the selected SDL wire byte order. The descriptor itself always
uses big-endian integers, independent of the selected wire byte order.

## Descriptor encoding

All strings are UTF-8 preceded by a big-endian `u16` byte length. Integers are
unsigned unless specified otherwise.

| Order | Value | Encoding |
| --- | --- | --- |
| 1 | Magic and version | Four bytes: ASCII `SDD1` |
| 2 | Root message name | String |
| 3 | Reachable message count | Big-endian `u16` |
| 4 | Messages | Sorted by type name |
| 5 | Reachable enum count | Big-endian `u16` |
| 6 | Enums | Sorted by type name |

Each message is a string type name, a big-endian `u16` field count, and its
fields sorted by ascending field ID. Each field contains a big-endian `u32`
field ID, a string field name, a one-byte modifier, a string type name, a
one-byte fixed-array dimension count, and that many big-endian `u32` dimensions.

Modifier codes are `0 = required`, `1 = optional`, `2 = repeated`, and
`3 = packed`. Primitive type names are `bool`, `int8`, `int16`, `int32`,
`int64`, `fl32`, `fl64`, `c32`, `c64`, and `string`. Other names refer to a
message or enum declared in the descriptor.

Each enum contains its string type name, a big-endian `u16` item count, then
each item in SDL declaration order as a string item name and big-endian signed
`i32` value.

## Body interpretation

The body is a sequence of payload-endian `u32` field ID, payload-endian `u32`
payload length, and payload bytes. Unknown IDs can be skipped by length.

Primitive integers and enums use their declared fixed-width wire size. Floats
use IEEE-754 binary32 or binary64. Complex values contain the real component
followed by the imaginary component. Strings are UTF-8 bytes without a
terminator. A nested message is another field sequence. A fixed array is a
row-major sequence of its elements without count or per-element headers. A
packed field uses that same contiguous representation for its repeated values.

Generated schemas reject fixed arrays and packed fields whose element wire
size is variable. This keeps every fixed-array element independently
decodable and permits a generic decoder to construct language-neutral values
from the frame alone.
