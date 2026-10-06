# SDL runtime test coverage

`make test` runs the C, Rust, and Python unit suites in big- and little-endian
wire modes. It does not run programs or smoke checks from `samples/`; those
examples are kept focused on demonstrating usage.

## Execution order and labels

`make test` runs the language runtimes in a fixed component order: C, Rust,
then Python. Within each runtime, ordinary codec behavior and wire/composite semantics
run before malformed-input cases; explicit numeric, descriptor-size, and array
limits form the final phase. C prints `C1` (typed codec), `C2` (storage and
dynamic descriptors), and `C3` (wire rejection and boundaries). Rust test names
use `t01_codec`, `t02_invalid`, and `t03_limits` prefixes and run serially.
`tst/python/run_tests.py` groups runtime codec, schema/backend validation,
malformed input, and limits in that order; test names carry matching component
and phase prefixes.

## Coverage matrix

| Area | C | Rust | Python |
| --- | --- | --- | --- |
| Empty messages | `test_empty_message_round_trip` covers typed, clone, and dynamic decoding | `empty_message_round_trip` covers typed and dynamic decoding | `test_empty_message_round_trip` covers typed and dynamic decoding |
| Typed and dynamic round-trips | `test_codec_cases`, `test_fixed_nested_arrays`, `test_packed_field_occurrences_concatenate` | `codec_scalars_optionals_enums_arrays_and_empty_values`, `nested_fixed_arrays_round_trip`, `packed_field_occurrences_concatenate_in_wire_order` | `test_scalars_optionals_enums_arrays_and_empty_values`, `test_nested_fixed_arrays_round_trip`, `test_packed_field_occurrences_concatenate_in_wire_order` |
| Shared cross-language wire fixture | `assert_root_wire_fixture` compares the C encoding byte-for-byte with big- and little-endian fixtures | `root_payload_deep_clone_and_wire_round_trip` compares Rust encoding with the C fixture and decodes the C frame | `test_root_payload_matches_c_fixture_and_round_trips` compares Python encoding with the C fixture and decodes the fixture |
| Field IDs, wire order, unknown fields, and occurrence concatenation | `test_extended_usage_cases`, `test_duplicate_field_semantics` (malformed earlier singular occurrence rejected), and `test_packed_field_occurrences_concatenate` (including field ID `UINT32_MAX`); typed/dynamic decoding preserves known values after zero-length and repeated unknown field occurrences; a malformed known field after a zero-length unknown field is rejected | `s01_field_order_is_independent_of_schema_order` (including field ID `UINT32_MAX`), `unknown_fields_are_skipped_and_truncated_fields_fail` (including duplicate unknown IDs), `duplicate_singular_fields_use_last_value_and_repeated_fields_append` (malformed earlier singular occurrence rejected), `packed_field_occurrences_concatenate_in_wire_order`, and `zero_length_packed_occurrence_decodes_as_an_empty_array`; malformed known fields after zero-length unknown fields are rejected | `test_s01_field_order_is_independent_of_schema_order` (including field ID `UINT32_MAX`), `test_unknown_fields_are_skipped_and_truncation_fails` (including duplicate unknown IDs), `test_duplicate_singular_fields_use_last_value_and_repeated_fields_append` (malformed earlier singular occurrence rejected), `test_packed_field_occurrences_concatenate_in_wire_order`, and `test_zero_length_packed_occurrence_decodes_as_an_empty_array`; malformed known fields after zero-length unknown fields are rejected |
| Truncation and length bounds | `test_extended_usage_cases` checks truncated frames and oversized lengths; `test_primitive_payload_widths` rejects undersized and oversized bool, integer, float, and complex fields in typed and dynamic decoding | `s03_invalid_frame_and_field_lengths_are_rejected` and `primitive_payload_widths_are_checked` reject truncated frames and primitive fields with incorrect widths in typed and dynamic decoding | `test_s03_invalid_frame_and_field_lengths_are_rejected` and `test_primitive_payload_widths_are_checked` reject truncated frames and primitive fields with incorrect widths in typed and dynamic decoding |
| Descriptor validation | Recursive, NUL, invalid UTF-8, oversized and hash-mismatch cases; rejects variable-size fixed-array elements, zero dimensions, packed strings, array-size overflow, built-in type-name collisions, and empty enums | Matching malformed descriptor cases, including invalid fixed layouts, built-in type-name collisions, and empty enums | Matching malformed descriptor cases, including invalid fixed layouts, built-in type-name collisions, and empty enums |
| Invalid C message storage | `test_repeated_fields_reject_null_storage` rejects nonzero counts with null storage for unpacked, packed, and string arrays across sizing, encode, clone, and display | — | — |
| Signed integer limits | Minimum and maximum values for `int8`, `int16`, `int32`, and `int64` round-trip through typed and dynamic decoders in `test_signed_integer_boundaries` | `signed_integer_minimum_and_maximum_values_round_trip` checks typed and dynamic values | `test_signed_integer_minimum_and_maximum_values_round_trip` checks typed and dynamic values |
| Optional and absent singular values | `test_codec_cases`, `test_missing_required_fields_default` | `absent_optionals_and_empty_arrays_round_trip`, `absent_required_fields_decode_to_default_values` | corresponding codec tests |
| Enums and canonical booleans | Enum `int32` minimum and maximum values round-trip through typed and dynamic decoding in `test_enum_int32_boundaries`; encoding rejects invalid scalar, fixed-array, packed, and nested packed-message enum values, and typed/dynamic decoding rejects invalid values. Scalar and packed bool round-trips plus noncanonical scalar and packed bool rejection are covered in `tst/main.c`. | `enum_int32_boundary_values_round_trip` checks both enum limits through typed and dynamic decoding. Typed and dynamic decoding reject invalid scalar, fixed-array, packed, and nested packed-message enum values. Rust’s typed enum API cannot construct undeclared variants for encoding; scalar and packed bool round-trips and noncanonical scalar and packed bool rejection are covered in `tests/codec.rs`. | `test_enum_int32_boundary_values_round_trip` checks both enum limits through typed and dynamic decoding; encoding rejects invalid scalar, fixed-array, packed, and nested packed-message enum values, and typed/dynamic decoding rejects invalid values. Scalar and packed bool round-trips plus noncanonical scalar and packed bool rejection are covered in `test_codec.py`. |
| Strings and floating-point edge values | Encode and typed/dynamic decode validate UTF-8: overlong encodings, truncated sequences, encoded surrogates, code points above U+10FFFF, and isolated continuation bytes; embedded and trailing NUL preservation, explicit C string lengths, infinities, NaNs, smallest subnormals, and signed zero in `tst/main.c` | `s02_unicode_strings_and_float_special_values_round_trip`; typed/dynamic decoders reject overlong UTF-8, truncated sequences, encoded surrogates, code points above U+10FFFF, and isolated continuation bytes; typed and dynamic values preserve embedded and trailing NUL (Rust `String` values are valid UTF-8) | `test_python_runtime_rejects_unknown_wire_byte_order` validates invalid Python configuration. Corresponding round-trip tests preserve embedded and trailing NUL and verify infinities, NaNs, smallest subnormals, and signed zero; `test_float_encoder_rejects_values_outside_wire_ranges` checks `CodecError` for out-of-range `fl32` and `c32` components; encoding rejects lone surrogates and typed/dynamic decoders reject overlong UTF-8, truncated sequences, encoded surrogates, code points above U+10FFFF, and isolated continuation bytes |
| Fixed and packed arrays | Typed and dynamic decoding of nested fixed arrays and packed fixed messages; fixed enum arrays; rejects malformed repeated and packed field lengths even when a valid occurrence follows | Matching typed and dynamic nested fixed-array and packed-message cases, fixed enum arrays, and malformed repeated and packed lengths in `tests/codec.rs` | Matching typed and dynamic nested fixed-array and packed-message cases, fixed enum arrays, and malformed repeated and packed lengths in `test_codec.py` |
| Generator validation | — | — | `tst/python/test_generator.py`, including malformed declaration-brace rejection, schema-content regeneration and type-name collision rejection across files, Rust/Python case-colliding module rejection without overwriting existing outputs, deleted-schema cleanup, custom-source preservation, invalid-input preservation, `uint16` field-name length, the inclusive 1 MiB descriptor limit, and nested fixed-message arrays at the `uint32` wire-size limit |

## Latest execution

Run from the repository root:

```sh
make test
```

The latest full `make test` passed: both C executables, 33 Rust integration
tests per byte order, and 70 Python tests per byte order. The C and Rust
executables and the Python suites all passed in big- and little-endian modes.
A separate forced C build with
`-std=c99 -Wall -Wextra -pedantic -Werror` passed on Clang 21. The available
`gcc` command resolves to Clang; GCC 4.8 was not available for direct
validation.
No sample executable or sample smoke check was run.
