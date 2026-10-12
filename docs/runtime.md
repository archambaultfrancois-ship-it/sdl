# Runtime APIs

All codecs require a prepared SDL2 context. Descriptions and data use separate
API arguments. At connection opening, send `description(local_types)` and
prepare your sending context with it. Receive the peer's description and
prepare a receiving context with your local generated types. Reuse contexts
for the lifetime of that direction. See [wire format](wire_descriptor.md).

Typed decode maps the sender layout to generated local values. Dynamic decode
returns values described by the catalogue and needs no generated local classes;
C, Rust, and Python provide this API. Both paths validate consumed data,
including skipped unknown fields.

## Catalogue identity and message inspection

Catalogue emission uses the compact, whitespace-free canonical rendering documented in the
[wire format](wire_descriptor.md). The public identity API hashes the exact
UTF-8 bytes supplied by the caller; it does not parse, normalize whitespace, or
change the catalogue at reception. Use the output of `description` (or the
generated Ada `Description`) to obtain an interoperable catalogue identity.

The algorithm is `sha256-prefix32-v1`: take the first four bytes of SHA-256 and
interpret them as an unsigned big-endian 32-bit integer. Zero is a valid result.
The API accepts UTF-8 input up to 1 MiB; hashing does not validate SDL2 syntax.
A C input length excludes the terminating NUL, as with `type_description`.
Do not hash a native string representation such as UTF-16 or include a transport
length prefix. A wire ID uses four big-endian bytes; a textual ID uses eight
lowercase hexadecimal digits, including leading zeroes.

| Runtime | Catalogue identity | Message names in wire ID order |
| --- | --- | --- |
| C | `type_catalogue_id(text, size, &id)` → bool, uint32 output | `type_context_message_count(ctx)` and `type_context_message_name(ctx, id)` |
| Rust | `catalogue_id(&text)` → `Result<u32, CodecError>` | `ctx.message_names()` → borrowed slice |
| Python | `catalogue_id(text)` → int (str or UTF-8 bytes) | `message_names(ctx)` → tuple |
| Java | `SdlCodec.catalogueId(text)` → nonnegative long | `SdlCodec.messageNames(ctx)` → copied array |
| Matlab/Octave | `sdl_matlab_runtime('catalogue_id', text)` → uint32 | `sdl_matlab_runtime('message_names', ctx)` → cell array |
| Ada | `SDL_Runtime.Catalogue_Id(Description)` → Unsigned_32 | `Message_Count(C)` and `Message_Name(C, Id)` |

Enumeration includes all message declarations, including nested messages, but
excludes enums. C and Ada accept 1-based wire IDs; sequence-returning APIs list
names in the same order. C returns borrowed names valid until the context is
freed, NULL for an invalid ID, and a zero count for a NULL context. Ada raises
`Codec_Error` for an invalid ID. Dynamic contexts can also be inspected without
registering local generated types.

To obtain a generated type's logical name, use Python `message_name(value)` or
`message_name(Class)`, Java `SdlCodec.messageName(Class)`, Rust `T::NAME` or
`TypeInfo.name`, or Matlab `Type('name')`. C descriptors expose their public
`name` member; Ada can resolve names through its context.

A 32-bit identity is scoped to a catalogue registry and can collide. When an
identity is already registered, compare the full canonical catalogue bytes.
Identical bytes reuse the registration; different bytes must be rejected or
resolved before any data is published. Lookup and registration must be atomic.
Never overwrite the catalogue behind an existing identity. Independent
registries need an explicit scope when messages cross between them. This short
identity is not a cryptographic integrity or authentication guarantee.

For example, Python can publish metadata without reading private attributes:

```python
from sdl_runtime import catalogue_id, description, message_name, message_names, prepare

text = description([Packet])
identity = catalogue_id(text)
ctx = prepare(text, [Packet])
assert message_name(Packet) == 'Packet'
assert message_names(ctx) == ('Packet',)
wire_identity = identity.to_bytes(4, 'big')
text_identity = '%08x' % identity
```

Shared identity fixtures in `tst/fixtures/catalogue_ids.tsv` exercise all six
runtimes, including UTF-8, embedded NUL, empty input, SHA-256 block/padding
boundaries, and the canonical `Packet` catalogue. Empty input and embedded NUL
are hash test vectors; they are not valid SDL2 catalogues.

## Directional compatibility and diagnostics

The public comparison API answers: **can the consumer read every valid value
allowed by the publisher catalogue?** Arguments are always publisher first,
consumer second. It parses catalogue metadata only; generated classes and local
registry entries are not required. The comparison is an optional preflight;
existing `prepare` APIs retain their current behavior and exceptions. Message
data must still be validated by a decoder.

Select a root message to check that message and its matched nested types. With
no root, every publisher message declaration is treated as a root in wire ID
order, including declarations that are unused or nested. This stricter default
requires the consumer to know every such message. Topic contracts should
normally select their published root explicitly. Unused consumer declarations
are ignored. Reversing the arguments can change the result, notably for enums.

Fields match by ID. Renaming a field or enum member does not affect compatibility.
Unknown publisher fields are skipped; their nested types are not compared under
a selected root. Missing consumer fields retain generated defaults. Shared field
IDs must preserve logical type name, dimensions, and cardinality; `repeated`
and `packed` remain compatible. Matched nested messages are checked recursively.
Every numeric enum value allowed by the publisher must also be allowed by the
consumer. A message/enum kind change is incompatible.

The optional `require_required` policy requires each consumer `required` field
to exist in the publisher catalogue, including within matched nested messages.
It is disabled by default and is a business policy beyond SDL2's missing-field
defaults. Optional consumer fields may still be absent. Cardinality changes are
incompatible independently of this policy.

| Runtime | Public comparison API |
| --- | --- |
| C | `type_compare_catalogues(p, pn, c, cn, root, require_required)` |
| Rust | `compare_catalogues(p, c, root, require_required)` |
| Python | `compare_catalogues(p, c, root=None, require_required=False)` |
| Java | `SdlCodec.compareCatalogues(p, c, root, requireRequired)`; a two-argument overload uses defaults |
| Matlab/Octave | `sdl_matlab_runtime('compare_catalogues', p, c, root, require_required)`; last two arguments are optional |
| Ada | `Compare_Catalogues(Publisher, Consumer, Root => "", Require_Required => False)` |

An absent root is NULL in C/Java, `None` in Python/Rust, and an empty string in
Matlab/Ada. C input sizes exclude terminating NULs. Each API returns the first
incompatibility as a structured diagnostic, or `COMPATIBLE`. Publisher catalogue
validation takes precedence over consumer validation. Valid catalogues are
visited in publisher root order, then depth-first by ascending field ID; enum
values follow declaration order. Missing required fields are checked in consumer
field ID order after matching publisher fields. Successful shared types are
memoized to avoid expanding a shared type graph repeatedly.

Diagnostics contain `code`, root `message`, `path`, `field_id`, `publisher_type`,
`consumer_type`, and `enum_value`. Paths start with the selected root and use
publisher field names, except that missing required fields use consumer names.
Types are logical type names; paths and IDs locate the declarations when the
code reports dimensions or cardinality changes. An enum value is meaningful
only for `ENUM_VALUE_UNKNOWN`. Unused numeric fields are zero and unused strings
are empty. Java and Ada expose equivalent fields with their naming conventions.

| Stable code (C/Ada ordinal) | Meaning |
| --- | --- |
| `COMPATIBLE` (0) | The selected roots are readable |
| `INVALID_PUBLISHER_CATALOGUE` (1) | Publisher metadata failed validation |
| `INVALID_CONSUMER_CATALOGUE` (2) | Consumer metadata failed validation |
| `ROOT_UNKNOWN` (3) | Selected root is absent from either message catalogue, or absent from the consumer entirely |
| `TYPE_KIND_MISMATCH` (4) | A shared logical name changed between message and enum |
| `FIELD_TYPE_MISMATCH` (5) | A matched field changed logical type |
| `FIELD_DIMENSIONS_MISMATCH` (6) | A matched field changed fixed dimensions |
| `FIELD_CARDINALITY_MISMATCH` (7) | A matched field changed required/optional/sequence cardinality |
| `ENUM_VALUE_UNKNOWN` (8) | The consumer cannot represent a publisher enum value |
| `REQUIRED_FIELD_MISSING` (9) | The enabled business policy found a missing required field |

If a publisher message root exists as an enum in the consumer, the result is
`TYPE_KIND_MISMATCH`. Roots must be messages in the publisher. Parser-invalid
catalogues return a diagnostic rather than a parser exception; those diagnostics
have no root/path or field details. This API currently returns one issue rather
than an aggregate list.

In C, diagnostics own their strings and survive the caller's catalogue buffers.
Free them with `type_compatibility_free`. NULL means allocation failure, not a
compatible result. `type_compatibility_code_name(d->code)` returns the stable
string code. Rust/Java/Python expose `compatible()`/`compatible` helpers; Matlab
returns a struct with a `compatible` field and Ada exposes `Is_Compatible(D)`.

```python
from sdl_runtime import compare_catalogues

report = compare_catalogues(publisher_text, consumer_text, root='Temperature')
if not report.compatible:
    print(report.code, report.path, report.field_id)
# Example: FIELD_TYPE_MISMATCH Temperature.celsius 2

strict = compare_catalogues(publisher_text, consumer_text,
                           root='Temperature', require_required=True)
```

All six runtimes execute the same cases in `tst/fixtures/compatibility/cases.tsv`.
The fixtures cover directional enum evolution, nested diagnostics, unknown
fields, missing-field defaults and policy, type/kind/dimension/cardinality
changes, malformed catalogues, and unsigned field ID / signed enum boundaries.

## C99

Include the generated header, `sdl_registry.h`, `type_engine.h`, and
`sdl_context.h`. Register types **before** preparing typed contexts.
The process-wide registry grows as needed. Generated `register_all_types()` and
`register_<schema>_types()` return `bool`: check for allocation failure or a
conflicting type name. Re-registering the same static descriptor succeeds;
registering another descriptor with the same logical name fails. Descriptors
must remain valid for the lifetime of the process. Regenerate C bindings to use
the checked registration API.

```c
if (!register_all_types()) {
   /* Handle registration failure before using typed codecs. */
   return EXIT_FAILURE;
}
const char *types[] = { "Packet" };
size_t description_size = 0, data_size = 0;
char *description = type_description(types, 1, &description_size);
SdlContext *ctx = type_prepare(description, description_size);
/* Exchange description[0..description_size) at connection opening. */
void *data = type_encode(ctx, "Packet", &packet, &data_size);
if (data) {
   Packet *copy = type_decode(ctx, data, data_size);
   type_free(copy);
   type_free(data);
}
type_context_free(ctx);
type_free(description);
```

Check `description` and `ctx` for NULL in production code. Sizing/codec calls
return 0/NULL on invalid data or allocation failure. `type_encode_size(ctx,
name, value)` returns complete data size. `type_decode_size(ctx, data, size)`
returns native storage needed. Typed decode validates/measures storage, then
fills one allocated object containing its strings and sequences.
`type_message_name(ctx, data, size)` inspects the leading type ID; it does not
validate the entire body. Check it before casting when several root types can
arrive. A context snapshots mappings to the registered local descriptions.

`type_prepare(text, size)` also works without registration for dynamic decode:
`type_decode_dynamic(ctx, data, size)` returns an owned `SdlDynamicMessage *`.
`type_dynamic_get(message, name)` borrows a field; inspect its value kind.
Strings carry byte lengths. Free the entire tree with `type_dynamic_free`.

Optional fields have presence flags. Sequences have a uint32 count and pointer.
Fixed arrays are inline and row-major. Strings have generated byte-length
companions: zero selects `strlen`, while an explicit positive length preserves
embedded/trailing NUL. A null pointer with positive length is invalid. Encoder
input storage remains owned by the caller. Zero-initialize generated structs.

`type_clone(name, value)` and `type_display(name, value, indent_width)` use the
registry and require no context. Free their outputs with `type_free`, as for
encoded and decoded typed data. Contexts use `type_context_free`.
C requires IEEE-754 floats with the same native byte order as integers.

## Rust

Add `sdl-runtime = { path = "../runtime/rust" }` under dependencies and expose
generated modules in your crate. Generated messages implement `SdlMessage`.

```rust
use sdl_runtime::{SdlMessage, description, prepare, encode, decode, decode_dynamic};
let types = [Packet::type_info()];
let text = description(&types)?;
let ctx = prepare(&text, &types)?;
let bytes = encode(&ctx, &packet)?;
let copy: Packet = decode(&ctx, &bytes)?;
let dynamic = decode_dynamic(&ctx, &bytes)?;
```

Results are `Result<_, CodecError>` and Rust owns all allocations. Optional
values use `Option`, sequences `Vec`, fixed arrays Rust arrays, and complex
values `Complex32`/`Complex64`. `prepare(&text, &[])` suffices for dynamic
values. `decode_any(ctx, bytes)` dispatches to registered `TypeInfo` entries;
its `TypedMessage` exposes the logical name and a boxed value to downcast.
`display(value, indent_width)` remains independent of wire contexts.

## Python 3

Put `runtime/python` and generated sources on `PYTHONPATH`.

```python
from sdl_runtime import description, prepare, encode, decode, decode_dynamic
from wire_example import Packet

text = description([Packet])
ctx = prepare(text, [Packet])
packet = Packet(active=True, code=-2, label="été", samples=[300, -1])
data = encode(ctx, packet)
copy = decode(ctx, data)  # dispatches by catalogue message ID
neutral = decode_dynamic(ctx, data)
```

Optional values use `None`, sequences and fixed arrays lists. Codec failures
raise `CodecError`. Nested generated classes are discovered from root types.
`prepare(text)` suffices for `decode_dynamic`; its result has `type_name` and a
`fields` dictionary. Text is `str` (UTF-8 bytes also accepted by preparation),
and encoded data is `bytes`. `display(value, indent_width)` needs no context.

## Java 7+

Generation produces a wrapper per SDL file, public nested message/enum classes,
and the dependency-free `SdlCodec.java`. For `wire_example.sdl`:

```java
String text = SdlCodec.description(wire_example.Packet.class);
SdlCodec.Context ctx = SdlCodec.prepare(text, wire_example.Packet.class);
wire_example.Packet packet = new wire_example.Packet();
packet.active = true;
packet.code = Short.valueOf((short)-2);
packet.label = "été";
packet.samples.add(Short.valueOf((short)300));
packet.samples.add(Short.valueOf((short)-1));
byte[] data = packet.encode(ctx);
wire_example.Packet copy = wire_example.Packet.decode(ctx, data);
```

`SdlCodec.encode(ctx, message)` and `SdlCodec.decode(ctx, bytes)` are generic
entry points. Descriptions are Strings; send their UTF-8 bytes independently.
Preparation discovers dependencies and caches reflection fields. Optional
values are nullable boxed values, most sequences are mutable Lists, and fixed
arrays native arrays. Complex scalars use `Complex32`/`Complex64`; packed complex
fields use `Complex32Array`/`Complex64Array` with equal-length primitive `re`
and `im` arrays to avoid an object per element. Codec errors throw
`SdlCodec.CodecException`. The tests use the compiler’s default source/target settings.

## Matlab / GNU Octave

Add the generated Matlab directory to the path. A generated message is a
function with actions; the runtime accepts description text separately:

```matlab
text = sdl_matlab_runtime('description', {'Packet'});
ctx = sdl_matlab_runtime('prepare', text, {'Packet'});
packet = Packet('new');
packet.active = true;
packet.code = int16(-2);
packet.label = 'été';
packet.samples = int16([300; -1]);
data = Packet('encode', ctx, packet);
copy = Packet('decode', ctx, data);
```

The generic decoder returns `[value, logical_name] =
sdl_matlab_runtime('decode', ctx, data)`. Data is uint8; preparation accepts
text or UTF-8 uint8. Optional values use empty storage for absence, except
strings where empty character text is a present empty string. Use the generated
`new` value as the authoritative shape. Packed fixed messages use a structure
of arrays; their generated field metadata supplies row-major wire traversal.
Packed primitives and complex values use bulk conversion. `make test-matlab`
uses Matlab if available, otherwise Octave; override `matlab` or `OCTAVE` paths.

## Ada 2012

Generated packages expose typed `T_<message>` records, a `Description`
catalogue, `Prepare`, and overloaded `Encode`/`Decode`. Use
`SDL_Runtime.Context` to retain the prepared catalogue, with automatic cleanup.
Fixed arrays are nested Ada arrays; repeated/Packed fields are typed vectors;
optional fields have `H_<field>` presence flags. See the [Ada API](ada.md)
for examples, compiler settings, validation and schema evolution behavior.

## Optional pub/sub envelope

All six runtimes provide fixed S2PS/2 packing and unpacking independently of
the existing raw SDL2 codecs. See [the profile and APIs](pubsub_envelope.md).
