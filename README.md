# SDL encoder

SDL is a schema-driven message encoder with C, Rust, and Python 3 runtimes.
The generator reads `.sdl` schemas and emits bindings for the selected
languages. The runtimes share a language-neutral wire descriptor; the wire
format does not depend on a target language's in-memory layout.

## Requirements

- Python 3 for code generation and the Python runtime tests.
- A C99 compiler with 8-bit bytes and IEEE-754 `float`/`double` support for
  the C runtime and examples.
- Rust and Cargo for the Rust tests and the cross-language sample.
- POSIX threads and Unix-domain sockets for the `abc` sample.

The Python runtime uses only the standard library. From the repository root,
run the complete test suite with:

```sh
make test
```

This runs the C, Rust, and Python unit tests in both wire byte orders. The
examples are intended for learning and can be run separately; their commands
are documented below. Run the throughput measurements with `make bench`; see
[`docs/benchmarks.md`](docs/benchmarks.md) for the measured message and timing
methodology.

## Examples

- [`samples/abc`](samples/abc/README.md) demonstrates SDL message exchange
  between three C threads using Unix-domain socket pairs.
- [`samples/radio_capture`](samples/radio_capture/README.md) uses one schema
  with all three backends and checks each encoder against the other two
  decoders.

For example, run the thread pipeline with `make -C samples/abc run`, or run the
radio capture round trips with `make -C samples/radio_capture demo`. Run its
cross-language checks with:

```sh
make -C samples/radio_capture interop
make -C samples/radio_capture WIRE_ENDIAN=little interop
```

See [`docs/schema.md`](docs/schema.md) for the accepted SDL syntax and its
constraints, and [`docs/runtime.md`](docs/runtime.md) for typed and
descriptor-driven runtime APIs.

## Generate bindings

Select one or more backends with `-c`, `-rust`, and `-python`. The input can
be one SDL file or a directory containing SDL files. Generated files are
written under a language-specific subdirectory of the output directory:

```sh
python3 gen/generator.py -c -rust -python sdl build/generated
```

This writes C, Rust, and Python bindings under `build/generated/c`,
`build/generated/rust`, and `build/generated/python`. Regeneration removes stale
source files left by SDL files that were deleted from the input directory.
Generated files should be treated as build artifacts; edit the SDL schema and
regenerate instead.

## Wire byte order

The default wire byte order is big endian. Little endian is a build/runtime
option, and communicating peers must use the same setting because frames do
not carry a byte-order marker. Select it with `-DSDL_WIRE_LITTLE_ENDIAN` when
building C, the Cargo feature `wire-little-endian` for Rust, or the environment
variable `SDL_WIRE_ENDIAN=little` for Python. The example Makefiles coordinate
the setting with `WIRE_ENDIAN=big` or `WIRE_ENDIAN=little`. See
[`docs/wire_descriptor.md`](docs/wire_descriptor.md) for the frame and
language-neutral descriptor format.
