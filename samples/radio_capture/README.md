# Radio capture sample

This sample models a receiver that reports a short complex I/Q capture to a
monitoring service. It uses one SDL schema and runs the same example in C,
Rust, and Python. The values are synthetic, but the message shape is typical
of a radio telemetry or sensor gateway.

## Try it

From this directory:

```sh
make demo
make INDENT_WIDTH=5 demo
make interop
```

`demo` generates all three backends and performs a local encode/decode round
trip in each language. `interop` writes messages in each language and asks the
other two runtimes to decode them, covering all six encoder/decoder pairings.
Numbers always use big endian. Each encoded `.sdlw` file contains only SDL2
data; the matching `.sdlw.sdl2` sidecar holds the opening catalogue. Decoders
prepare the sidecar separately before reading values.
The demos call the runtime `display` API on the in-memory SDL object. Its
`indent_width` argument sets the number of spaces per nesting level; it defaults
to 3 and can be changed with `INDENT_WIDTH`.

Requirements are a C99 compiler, Rust/Cargo, and Python 3. The Python runtime
uses only the standard library.

## Schema features shown

| SDL feature | Sample field | Why it is useful |
| --- | --- | --- |
| Enum | `CaptureMetadata.mode` | Gives capture modes stable named values. |
| Nested message | `RadioCapture.metadata` | Keeps receiver and acquisition metadata together. |
| Optional field | `RadioCapture.operator_note` | Omits a note when the operator has none. |
| Fixed-size array | `CaptureMetadata.dc_offset[2]` | Holds the two calibration offsets without per-element framing. |
| Packed complex array | `RadioCapture.iq_samples` | Sends each complex float as exactly two float32 values, with no per-sample field header. |
| Repeated values | `RadioCapture.trigger_offsets` | Encodes event offsets with one count followed by contiguous values. |
| Opening catalogue | Separate `.sdl2` sidecar | Describes logical types once, including fields for dynamic decoding. |

The sample checks both generated typed decoding and the language-neutral
dynamic decoder. Its `interop` target verifies that the C, Rust, and Python
implementations agree on the descriptor and wire representation. Java and
Matlab/Octave backends are available in the generator and have their own
runtime tests and benchmarks; this sample's Makefile currently exercises the
C, Rust, and Python implementations.

The public formatting entry points are `type_display("RadioCapture", &value,
indent_width)` in C, `sdl_runtime::display(&value, indent_width)` in Rust, and
`sdl_runtime.display(value, indent_width)` in Python. C returns an allocated
string that the caller releases with `type_free()`.

## Layout

- `schema/radio_capture.sdl` is the shared schema.
- `c/main.c`, `rust/src/main.rs`, and `python/main.py` are the three examples.
- `Makefile` generates language bindings under the ignored `build/` directory
  and builds/runs the sample programs.

Generated source belongs to the build directory; edit the SDL schema and
regenerate instead of editing generated files.
