# Optional pub/sub envelope: S2PS/2

S2PS/2 is a fixed, transport-independent envelope around an existing SDL2
message. It is optional: direct SDL2 encoding and decoding APIs remain unchanged.
A direct SDL2 connection must associate the received bytes with a catalogue by
configuration or connection setup. No automatic format detection is required;
the application explicitly selects raw SDL2 or S2PS/2.

## Wire layout

| Byte offset | Size | Meaning |
| --- | --- | --- |
| 0 | 4 | ASCII `S2PS` (`53 32 50 53` in hexadecimal) |
| 4 | 1 | Envelope version: `02` |
| 5 | 4 | Unsigned catalogue ID, big endian, `sha256-prefix32-v1` |
| 9 | Remaining bytes | Nonempty, unchanged SDL2 payload |

Example: catalogue ID `89abcdef`, payload `01 00`:

```text
53 32 50 53 02 89 ab cd ef 01 00
```

There are no extensions, header-length field, embedded catalogue, topic, or
checksum. Version 2 identifies the envelope layout, not a schema revision.
Version 1 of the PoC used a full SHA-256 digest; readers reject version 1 and
all unknown versions. Catalogue ID zero is valid.

The envelope parser only checks the fixed header and presence of a payload.
It does not validate SDL2 bytes, resolve catalogues, or check compatibility.
After extraction, the caller resolves and verifies the catalogue, prepares a
context, and calls the ordinary SDL2 decoder. Unknown IDs and 32-bit collisions
remain application responsibilities; compare full catalogue texts when registering.

## Public APIs

- C (`sdl_context.h`): `type_envelope_pack(id, payload, size, &envelope_size)`
  returns an owned allocation, released with `type_free()`. NULL indicates
  invalid arguments, size overflow, or allocation failure.
  `type_envelope_unpack(data, size, &id, &payload, &payload_size)` returns a
  Boolean and borrows its payload from the input. Failure leaves outputs unchanged.
- Python: `pack_envelope(id, payload) -> bytes` and
  `unpack_envelope(data) -> (id, payload_bytes)`. Errors raise `CodecError`.
- Rust: `pack_envelope(u32, &[u8]) -> Result<Vec<u8>, CodecError>` and
  `unpack_envelope(&[u8]) -> Result<(u32, &[u8]), CodecError>`.
  The extracted payload borrows the input.
- Java: `SdlCodec.packEnvelope(long, byte[]) -> byte[]` and
  `SdlCodec.unpackEnvelope(byte[]) -> Envelope`, with `catalogueId` and a
  copied `payload`. IDs must be in `0..0xffffffffL`; errors raise `CodecException`.
- Matlab/Octave: `sdl_matlab_runtime('pack_envelope', uint32_id, uint8_payload)`
  and `[id, payload] = sdl_matlab_runtime('unpack_envelope', uint8_data)`.
  Results are row vectors; errors use `SDL:Envelope`.
- Ada: `Pack_Envelope(Catalogue, Payload) return Bytes` and
  `Unpack_Envelope(Data) return Envelope`, with `Catalogue` and copied `Payload`.
  Errors raise `Codec_Error`.

Header size constants are 9 in C, Python, Rust, Java and Ada. Envelope functions
have no transport-specific maximum; applications must bound record sizes before
allocation or reception. Existing SDL2 decoder limits still apply. The PoC
retains its separate 4096-byte envelope limit.

## Transport mapping

For Kafka, the topic and partition key remain Kafka record metadata; the whole
S2PS/2 envelope occupies the record value. Publish catalogues to a dedicated
compacted topic keyed by catalogue ID. Consumers must handle data arriving before
the matching catalogue is locally available: reading separate topics does not
establish a shared reception order. Catalogue retention must cover data replay.

Keep the catalogue ID in the envelope as the authoritative reference. If broker
headers duplicate it, adapters must reject inconsistencies rather than silently
choose another catalogue.

Kafka and message-oriented transports provide record boundaries. TCP requires
separate framing, such as a length prefix, for either raw SDL2 or S2PS/2. The
PoC's TCP framing is an application choice, not part of this envelope profile.
