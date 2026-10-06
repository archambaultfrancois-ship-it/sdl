# SDL runtime coverage report

This report maps the additional usage and malformed-input cases to the C,
Rust, and Python test suites. `make test` runs the C executable and Rust/Python
suites with both the default big-endian wire mode and little-endian mode.

## Identified test steps

| Step | Coverage | C | Rust | Python |
| --- | --- | --- | --- | --- |
| S01 | Decode fields in reverse wire order, independent of schema order | `test_extended_usage_cases` | `s01_field_order_is_independent_of_schema_order` | `test_s01_field_order_is_independent_of_schema_order` |
| S02 | Long UTF-8 strings; positive/negative infinity and NaN in scalar and complex floats | `test_extended_usage_cases` | `s02_unicode_strings_and_float_special_values_round_trip` | `test_s02_unicode_strings_and_float_special_values_round_trip` |
| S03 | Reject truncated frames and impossible descriptor/field lengths in typed and dynamic decoders | `test_extended_usage_cases` | `s03_invalid_frame_and_field_lengths_are_rejected` | `test_s03_invalid_frame_and_field_lengths_are_rejected` |
| S04 | Reject duplicate field IDs and references to unknown types | — | — | `test_s04_duplicate_field_ids_and_unknown_types_are_rejected` |
| S05 | Reject recursive message dependencies | — | — | `test_s05_recursive_message_dependencies_are_rejected` |
| S06 | Existing coverage: unknown fields, truncated payloads, invalid booleans, descriptor hashes, packed lengths, optional values, fixed arrays, and round-trips | `test_codec_cases`, `test_fixed_nested_arrays`, `test_anonymous_nested_structs` | corresponding codec integration tests | corresponding codec and generator tests |

## Execution record — 2026-10-06

Run from the repository root:

```sh
make test
```

Command: `make test`

| Step | Result |
| --- | --- |
| S01–S03 | Passed in C, Rust, and Python; C/Rust/Python suites exercised both big- and little-endian wire modes |
| S04–S05 | Passed in Python generator validation |
| S06 | Passed in all runtime suites |
| C suite | Both big- and little-endian executables passed |
| Rust suite | 12 integration tests passed per endian mode |
| Python suite | 19 tests passed per endian mode |

During S05, a direct recursive message declaration was initially accepted by
the parser. The dependency walk now rejects message cycles, and the regression
test passes. The same `make test` command was rerun after the correction.
