# SDL encoder

SDL generates message bindings for C99, Rust, Python 3, Java 7+, Ada 2012, and
Matlab/Octave from `.sdl` schemas.

SDL2 exchanges a compact UTF-8 schema catalogue when a connection opens.
The canonical description is one string without whitespace, for example
`SDL2|Point{1:int32:x;2:int32:y@b12;}`; catalogue hashes use these exact bytes. The runtime
prepares that catalogue separately from message data. Each data buffer carries
a compact message type ID followed by positional values. Numbers use big endian.
Field IDs remain in the catalogue to support schema evolution.

In source schemas, field IDs may be omitted: numbering starts at 1 and continues
with the previous field's ID plus one, including after an explicit ID. Omitting
the modifier defaults to `required`, so `int32 value;` as a first field is
equivalent to `1: required int32 value;`. Both defaults apply independently to
nested messages as well. See [automatic field numbers](docs/schema.md#automatic-field-numbers)
and [field modifiers](docs/schema.md#field-types-and-modifiers) for examples and
the implications for schema evolution.

Source schemas also support `uint8` through `uint64` and per-field `wire(...)`
clauses for bit widths, varint/ZigZag encoding, alignment, and padding. For example,
`int32 value wire(bits=12);` encodes values from -2048 through 2047 in 12 bits.
See [unsigned integers](docs/schema.md#unsigned-integers) and
[field wire representation](docs/schema.md#field-wire-representation).

## Generate bindings

```sh
python3 gen/generator.py -c -rust -python -matlab -java -ada sdl build/generated
```

Select any combination of backends. The input can be a file or directory;
outputs go under `build/generated/<language>`. Regeneration removes stale
bindings. Edit schemas and regenerate instead of editing generated files.
An optional `package navigation.geometry;` gives types complete logical names
and places generated files under the corresponding package hierarchy (GNAT
files remain flat). `.Point` selects a global type explicitly; short names
resolve only in the current package. See [packages](docs/schema.md#packages-and-type-references).

## Build and validation

The generator and Python runtime use Python 3 and its standard library. Other
backends need a C99 compiler, Rust/Cargo, a JDK, GNAT, and Matlab or GNU Octave.
C requires 8-bit bytes and IEEE-754 binary32/binary64. Java bindings target Java
7 features; tests use the compiler’s default source/target settings. Java required
numeric/bool fields and fixed numeric/bool arrays use primitive types;
optional scalars remain boxed to represent absence. Packed numeric/bool
sequences use primitive arrays (`short[]`, `float[]`, etc.), while repeated
sequences and packed records/enums use lists. Packed complex sequences use
`Complex32Array`/`Complex64Array` with primitive component arrays. Regenerate
Java bindings and adapt list operations when migrating to this API.

```sh
make test
make bench
```

The suite covers all six runtimes with the shared SDL2 fixture. Benchmarks
report messages/s, useful MiB/s, catalogue size, and preparation time across
six workloads. See [benchmarks](docs/benchmarks.md) for methodology and limits.
Python has an optional native Packed adapter. Build it with
`make python-native`, then run `make test-python bench-python`. The list/message
API remains available, with a Python fallback when native code is unavailable.
The optional `PackedArray` buffer API and `decode(..., packed='view')` avoid
per-value Python objects; `encode_into` writes into caller storage. `make bench`
prints these buffer measurements separately; `make bench-python-buffers` runs
only that comparison. See [benchmarks](docs/benchmarks.md) for the API and limits.
Set `SDL_PYTHON_NO_NATIVE=1` before preparing contexts to compare the Python path.
On Android/Termux, the native build links explicitly to the invoking CPython's
shared library. The build also checks that the resulting extension can be
imported, so loader errors fail `make python-native` immediately.

MATLAB has an optional MEX adapter for the complete `BenchPayload` and `BenchRecordBatch` messages.
Build it with `make matlab-mex`, then run `make test-matlab bench-matlab`.
It retains a native C context and creates MATLAB outputs directly. Other
schemas retain the normal MATLAB path. See [benchmarks](docs/benchmarks.md)
for the supported platform and switches to compare the full MEX, payload-only
MEX and MATLAB implementations.

Ada bindings use typed records, fixed arrays, `Ada.Containers.Vectors` for
repeated/Packed fields and UTF-8 `Unbounded_String` values. Optional fields
have an explicit presence flag. Messages containing Packed fields use generated
Ada codecs for matching layouts; schema evolution retains C dynamic decoding through an opaque context
with automatic lifetime management. See [Ada API and build instructions](docs/ada.md).
`make test-ada bench-ada` uses `~/opt/gnat-21.1` by default, with `-O3` and
range checks enabled. Override `GNAT_HOME`, `GNATMAKE` or `ADA_RTS` as needed.

On the Termux development environment, source `~/.bashrc` before using the JDK.

## Examples

- [C](samples/abc-c/README.md), [Rust](samples/abc-rust/README.md),
  [Python](samples/abc-python/README.md), [Java](samples/abc-java/README.md),
  and [Ada](samples/abc-ada/README.md)
  equation solvers exchange a catalogue at socket opening, then message data.
- [Matlab/Octave](samples/abc-matlab/README.md) runs the same stages sequentially.
- [Radio capture](samples/radio_capture/README.md) checks all six C/Rust/Python
  encoder/decoder pairings using separate catalogue files.
- [Record and replay](samples/record_replay/README.md) records autonomous SDL2
  MCAP or PCAPNG files and compares live radio analysis with replay. The optional Python
  module and CLI support inspection, validation and timed replay; see the
  [recording reference](docs/recording.md) and the optional
  [native services](docs/recording_native.md) for C, Rust, Java JNI, Ada and C MEX.
  The [PCAPNG profile](docs/recording_pcapng.md) adds USER0 captures and MCAP conversion.
  Run `make recording-deps test-recording` to install the optional dependency and test it.
- [Wireshark](tools/wireshark/README.md) decodes catalogue announcements and
  subsequent UDP messages, and inspects SDL USER0 recordings.

```sh
make -C samples/abc-c run
make -C samples/abc-ada run
make -C samples/radio_capture interop
```

Read the [schema syntax](docs/schema.md), [runtime APIs](docs/runtime.md), and
[complete wire layout](docs/wire_descriptor.md), including a byte-by-byte
catalogue and data example. SDL2 replaces the previous format completely.
The [formal syntax reference](docs/formal_syntax.md) gives EBNF grammars for
source files and the single-line SDL2 catalogue, with validation rules and
matching examples.
