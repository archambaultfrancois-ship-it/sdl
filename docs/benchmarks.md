# SDL2 throughput benchmarks

Run `make bench` (or an individual `bench-c`, `bench-rust`, `bench-python`,
`bench-matlab`, `bench-java` target). The aggregate checks identical wire sizes
across the five implementations and writes `build/benchmarks.json` with
catalogue sizes, preparation times, and throughput.

## Method

Each message is constructed before timing and checked with a round trip.
Catalogue rendering and preparation are outside the message timing. Each case
reports one preparation call separately; it includes parsing, schema checks,
and local mappings, and is not a steady-state preparation benchmark.

After warmup (C/Rust/Python: 100 calls of each operation; Java: 1,000;
Matlab/Octave: 20), encode and decode each run for three trials. Each trial has
at least 200 calls and 0.1 seconds; the median messages/s is reported. Set
`BENCH_ITERATIONS` to change the minimum, or `SDL_BENCH_ITERATIONS` for standalone
programs. Every backend uses elapsed wall time. C uses `CLOCK_MONOTONIC`, Rust
`Instant`, Python `perf_counter`, Java `nanoTime`, and Matlab/Octave `tic/toc`.

Timed operations include allocating encoded output or decoded values and
releasing/discarding them. There is no transport, message construction,
dynamic-tree decode, or application work. Java results depend on JIT warmup
and collection; Python reference counting and Octave interpreter overhead
are part of their measurements. C native decode validates/measures its storage
before filling one allocation. Rust typed decode follows generated code;
Java, Python, and Octave follow runtime metadata and local mappings.

MiB/s counts **useful value bytes**, not catalogue, type IDs, string lengths,
element counts, optional flags, or transport framing. A MiB is 1,048,576 bytes.
Integer, bool, floating-point and string-content sizes determine useful bytes.

| Case | Values | Useful bytes | Data bytes | Catalogue bytes |
| --- | --- | ---: | ---: | ---: |
| small | bool, int32, int16 | 7 | 8 | 490 |
| optional_sparse | 1 of 8 optional int32 values | 4 | 13 | 490 |
| optional_dense | 8 optional int32 values | 32 | 41 | 490 |
| variable | 40-byte text + 8 entries (optional 6-byte text + int64) | 152 | 171 | 490 |
| packed | 200-byte text + 5,000 c32 samples | 40,200 | 40,205 | 84 |

The small/optional/variable catalogue includes all messages in
[`bench_cases.sdl`](../sdl/bench_cases.sdl); packed uses
[`benchmark.sdl`](../sdl/benchmark.sdl). The 40,205-byte packed data contains
one type ID byte, two string-length bytes, 200 text bytes, two element-count
bytes, and 40,000 sample bytes.

## Local measurements, 2026-10-08

Android 16 / Termux, aarch64; Clang 21.1.8 (`-O2` C99), Rust 1.96.0
(release profile), Python 3.13.13, OpenJDK 21.0.11, GNU Octave fallback.
Command: `make bench BENCH_ITERATIONS=200`, after sourcing `~/.bashrc` for Java.
Backends ran consecutively on the same device. Rust was measured again with
`make bench-rust BENCH_ITERATIONS=200` after adding local type identity checks. These medians are observations
under that device's load and thermal conditions, not language ceilings or
network throughput guarantees. No matched previous-format benchmark was
completed, so these results do not establish a speedup over that format.

### Messages per second

Each cell is encode / decode. Rounded to a whole message/s.

| Backend | Small | Optional sparse | Optional dense | Variable nested | Packed |
| --- | ---: | ---: | ---: | ---: | ---: |
| C | 2,770,244 / 1,060,654 | 1,951,980 / 353,007 | 1,676,905 / 337,274 | 525,775 / 91,990 | 15,680 / 15,253 |
| Rust | 3,965,828 / 4,048,981 | 3,231,170 / 2,074,939 | 2,661,830 / 1,969,586 | 1,902,658 / 426,703 | 26,241 / 24,074 |
| Python | 60,608 / 22,989 | 43,776 / 18,893 | 23,494 / 11,976 | 9,887 / 4,036 | 416 / 208 |
| Octave | 89 / 68 | 118 / 56 | 36 / 28 | 15 / 11 | 110 / 91 |
| Java | 128,246 / 136,949 | 127,256 / 175,679 | 183,397 / 287,608 | 43,904 / 35,443 | 7,266 / 11,662 |

### Packed useful throughput and preparation

| Backend | Encode MiB/s | Decode MiB/s | Catalogue preparation µs |
| --- | ---: | ---: | ---: |
| C | 601.15 | 584.77 | 5.2 |
| Rust | 1006.01 | 922.93 | 18.4 |
| Python | 15.96 | 7.98 | 107.1 |
| Octave | 4.23 | 3.49 | 61,456.0 |
| Java | 278.58 | 447.09 | 592.3 |

## Interpretation and format tradeoffs

Removing per-field headers is effective on small required values. On the
packed case, metadata is already only five bytes: further wire reductions
would barely change bandwidth. Bulk byte conversion, allocation strategy,
and runtime dispatch dominate. C converts primitive spans in bulk and skips
numeric conversion during its validation/size pass; Rust uses generated typed
loops; Python still creates/validates complex objects; Java stores packed
complex values in primitive component arrays. Octave's bulk sample conversion
helps packed data, while small nested messages spend most time in interpreted
metadata handling. Measurements taken with Octave say nothing conclusive
about Matlab's JIT throughput.

Sparse optionals remain expensive on the wire: eight count bytes plus one type
ID surround just four useful bytes. A presence bitmap would reduce that
specific case, but requires another message layout rule; SDL2 uses the same
count convention for each optional/sequence. Packed and repeated share their
wire layout; the packed declaration primarily enables fixed-size conversion.
Changing a declaration to packed alone does not guarantee a faster runtime.

The text catalogue is amortized only when a connection carries multiple
messages. Short connections must include its opening cost. The equation
examples each send one message per connection and illustrate API separation,
not efficient connection reuse. Production callers should retain their prepared
contexts and connections. Preparation and schema graph traversal are cached;
per-message calls do not parse the description again.

For scale, 1 Gbit/s corresponds to 119.2 MiB/s before framing and wire
overhead. These packed measurements put the current C/Rust/Java codecs above
that threshold and Python/Octave below it on this device.

For any workload, useful network throughput cannot exceed either the codec's
measured useful throughput or link capacity after wire/framing overhead. Tiny
messages are normally limited by messages/s and packet/syscall overhead;
large numeric arrays by conversion, memory traffic, and allocations. Neither
these tests nor the wire specification establish end-to-end line rate.
