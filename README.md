# SDL encoder

SDL generates message bindings for C99, Rust, Python 3, Java 7+, and
Matlab/Octave from `.sdl` schemas.

SDL2 exchanges a UTF-8 schema catalogue when a connection opens. The runtime
prepares that catalogue separately from message data. Each data buffer carries
a compact message type ID followed by positional values. Numbers use big endian.
Field IDs remain in the catalogue to support schema evolution.

## Generate bindings

```sh
python3 gen/generator.py -c -rust -python -matlab -java sdl build/generated
```

Select any combination of backends. The input can be a file or directory;
outputs go under `build/generated/<language>`. Regeneration removes stale
bindings. Edit schemas and regenerate instead of editing generated files.

## Build and validation

The generator and Python runtime use Python 3 and its standard library. Other
backends need a C99 compiler, Rust/Cargo, a JDK, and Matlab or GNU Octave.
C requires 8-bit bytes and IEEE-754 binary32/binary64. Java bindings target Java
7 features; tests default to source/target 8 for recent JDKs.

```sh
make test
make bench
```

The suite covers all five runtimes with the shared SDL2 fixture. Benchmarks
report messages/s, useful MiB/s, catalogue size, and preparation time across
five workloads. See [benchmarks](docs/benchmarks.md) for methodology and limits.
On the Termux development environment, source `~/.bashrc` before using the JDK.

## Examples

- [C](samples/abc-c/README.md), [Rust](samples/abc-rust/README.md),
  [Python](samples/abc-python/README.md), and [Java](samples/abc-java/README.md)
  equation solvers exchange a catalogue at socket opening, then message data.
- [Matlab/Octave](samples/abc-matlab/README.md) runs the same stages sequentially.
- [Radio capture](samples/radio_capture/README.md) checks all six C/Rust/Python
  encoder/decoder pairings using separate catalogue files.
- [Wireshark](tools/wireshark/README.md) decodes catalogue announcements and
  subsequent UDP messages.

```sh
make -C samples/abc-c run
make -C samples/radio_capture interop
```

Read the [schema syntax](docs/schema.md), [runtime APIs](docs/runtime.md), and
[complete wire layout](docs/wire_descriptor.md), including a byte-by-byte
catalogue and data example. SDL2 replaces the previous format completely.
