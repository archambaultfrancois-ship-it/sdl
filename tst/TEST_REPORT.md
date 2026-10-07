# SDL test report

This report describes the test coverage and results for commit `0ff3a20`.

## What `make test` runs

The Makefile runs these components in order: C, Rust, Python, Matlab/Octave,
then Java. C, Rust, Python, Matlab/Octave, and Java run in both big- and
little-endian wire modes. The target does not run programs or smoke checks
from `samples/`.

Within C, the executable groups typed codec behavior, runtime storage and
dynamic descriptors, then malformed wire data and numeric boundaries. Rust
tests run serially and use `t01_codec`, `t02_invalid`, and `t03_limits`
prefixes. Python's `tst/python/run_tests.py` runs codec behavior, schema and
backend validation, malformed input, and limits in that order. Matlab/Octave
and Java run their codec checks once per wire byte order. The generator tests
also compile generated Java bindings; the Java codec suite is a separate
Makefile component.

## Coverage matrix

| Area | C | Rust | Python | Matlab/Octave and Java |
| --- | --- | --- | --- | --- |
| Typed round-trips and composite values | Scalars, optional values, enums, repeated and packed fields, fixed arrays, nested messages, and complex values | Scalars, optionals, enums, repeated and packed fields, fixed arrays, nested messages, and complex values | Scalars, optionals, enums, repeated and packed fields, fixed arrays, nested messages, and complex values | Both suites cover scalar and composite codec round-trips, arrays, enums, strings, and complex values |
| Empty and absent values | Empty messages; absent required values decode to defaults | Empty messages, absent optionals and arrays, and absent required values | Empty messages, absent optionals and arrays, and absent required values | Both cover empty messages and absent values; Java also checks default required values |
| Shared wire fixtures | C verifies root payload fixtures in both byte orders | Rust compares encoded data to the C fixtures and decodes them | Python compares encoded data to the C fixtures and decodes them | Java verifies the root payload fixture in each byte order; Matlab/Octave exercises codec round-trips |
| Field order and repeated occurrences | Reordered fields, duplicate singular fields, packed occurrence concatenation, unknown fields, and maximum field ID | Reordered fields, duplicate singular fields, packed occurrence concatenation, unknown fields, and maximum field ID | Reordered fields, duplicate singular fields, packed occurrence concatenation, unknown fields, and maximum field ID | Java checks reordered fields, duplicate and packed occurrences, and unknown fields; Matlab/Octave checks duplicate singular fields and repeated appends |
| Invalid wire data and bounds | Truncated frames, malformed lengths, primitive payload widths, invalid booleans, enums, and UTF-8 | Truncated frames, malformed lengths, primitive payload widths, invalid booleans, enums, and UTF-8 | Truncated frames, malformed lengths, primitive payload widths, invalid booleans, enums, and UTF-8 | Java checks malformed frames, primitive widths, booleans, enums, UTF-8, and descriptor limits; Matlab/Octave checks malformed frames, payloads, and fixed-array lengths |
| Dynamic descriptors | Recursive, NUL, invalid UTF-8, oversized and hash-mismatched descriptors; invalid fixed layouts, type-name collisions, and empty enums | Recursive, NUL, invalid UTF-8, and malformed descriptor layouts, including empty enums and type-name collisions | Matching malformed descriptor and invalid-layout cases | — |
| C storage validation | Rejects nonzero repeated counts with null storage across sizing, encode, clone, and display | — | — | — |
| Integer and enum boundaries | Signed integer and enum `int32` minimum and maximum values | Signed integer and enum `int32` boundaries, including negative enum values | Signed integer and enum `int32` boundaries, including negative enum values | Java checks integer and enum boundaries; Matlab/Octave checks enum `int32` boundaries and integer round-trips |
| Strings and floating-point edges | UTF-8 validation, embedded and trailing NUL, infinities, NaNs, subnormals, and signed zero | UTF-8 validation, embedded and trailing NUL, infinities, NaNs, subnormals, and signed zero | UTF-8 validation, embedded and trailing NUL, infinities, NaNs, subnormals, signed zero, and out-of-range encoder inputs | Java checks UTF-8, embedded NUL, infinities, NaNs, subnormals, and signed zero; Matlab/Octave checks ordinary strings and complex values |
| Generator validation | — | — | Schema parser and C, Rust, Python, Matlab, and Java backend validation, including safe regeneration, identifier collisions, and descriptor and array limits | Java source generation is compiled in the Python backend suite |

## Latest full run

Command: `make test` from the repository root, with the required environment
configured.

- C: both endian executables passed.
- Rust: 33 integration tests passed per byte order.
- Python: 71 tests passed per byte order (13 codec, 25 schema/backend,
  17 invalid-input, and 16 limits tests).
- Matlab/Octave: both byte orders passed.
- Java: generated and test sources compiled; the codec suite passed in both
  byte orders.

The full `make test` passed. Rust ran 33 integration tests per byte order;
Python ran 71 tests per byte order. Both Octave codec runs reported success.
No sample executable or sample smoke check was run.
