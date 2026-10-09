# SDL2 throughput benchmarks

Run `make bench` (or an individual `bench-c`, `bench-rust`, `bench-python`,
`bench-matlab`, `bench-java` target). The aggregate checks identical wire sizes
across the five implementations and writes `build/benchmarks.json` with
catalogue sizes, preparation times, and throughput. The aggregate prints a
case-by-language matrix in **MB/s (Enc / Dec)**, rounded to integers; MB/s
means 1,000,000 useful bytes per second. A displayed zero means less than
0.5 MB/s. Columns correspond to the runtimes executed (Matlab or Octave for
`bench-matlab`). Individual targets and JSON retain the detailed MiB/s and
messages/s measurements.

## Method

C builds default to `-O3` for tests, benchmarks and samples. Rust development,
test and release profiles use `opt-level = 3`. Debug assertions and overflow
checks retain their normal development/test defaults. Java, Python and
Matlab/Octave use their runtime compilation/interpreter settings.

Each message is constructed before timing and checked with a round trip.
Catalogue rendering and preparation are outside the message timing. Each case
reports one preparation call separately; it includes parsing, schema checks,
and local mappings, and is not a steady-state preparation benchmark.

After warmup (C/Rust/Python: 100 calls of each operation; Java: 1,000;
Matlab/Octave: 20), encode and decode each run for three trials. Each trial has
at least 200 calls and 0.1 seconds; the median messages/s is reported. Set
`BENCH_ITERATIONS` to change the minimum, or `SDL_BENCH_ITERATIONS` for standalone
programs. Rust applies `black_box` to the context, input and returned value in timed
operations so compiler optimizations cannot rely on constant inputs. The same
message buffer is reused; these are warm-cache codec measurements.
Every backend uses elapsed wall time. C uses `CLOCK_MONOTONIC`, Rust
`Instant`, Python `perf_counter`, Java `nanoTime`, and Matlab/Octave `tic/toc`.

Timed operations include allocating encoded output or decoded values and
releasing/discarding them. There is no transport, message construction,
dynamic-tree decode, or application work. Java results depend on JIT warmup
and collection; Python reference counting and Octave interpreter overhead
are part of their measurements. C native decode validates/measures its storage
before filling one allocation. Rust prepares layout/enum compatibility checks
and uses generated fixed codecs for compatible packed records. The codecs use
binary offsets and endian conversion, preserving the native Rust layout without
raw struct copies. Fixed encoders are marked inline across crates so the
compiler can fuse endian conversions and output writes. Fixed complex decoders
also expose their scalar conversion to the compiler and fill a pre-sized output
slice instead of pushing each sample individually. This safe Rust path checks
the full input block before allocation, retains raw IEEE-754 bits and permits
vectorization of the big-endian conversion; fixed arrays inside
generated records require no temporary
heap allocations. Schema changes retain generic typed decoding;
Python prepares bulk binary layouts for fixed-size packed structures;
Java generates direct fixed-layout codecs and selects them during context
preparation when field IDs, types, dimensions and enum value sets match.
Field/enum labels can change without disabling the fixed path. Schema evolution
uses the generic decoder. Required numeric/bool values and fixed/packed numeric
arrays use primitive storage; optional scalars and repeated primitives retain
boxed types. Fixed record decoding avoids constructing defaults that would be
immediately overwritten. Fixed sequence sizes are measured with count times
record size; encoding still validates array shapes and required references
while writing. Primitive and complex arrays use specialized big-endian word
loops with block bounds checks. Matlab/Octave follow runtime metadata and local
mappings.

MiB/s counts **useful value bytes**, not catalogue, type IDs, string lengths,
element counts, optional flags, or transport framing. A MiB is 1,048,576 bytes.
Integer, bool, floating-point and string-content sizes determine useful bytes.

| Case | Values | Useful bytes | Data bytes | Catalogue bytes |
| --- | --- | ---: | ---: | ---: |
| small | bool, int32, int16 | 7 | 8 | 806 |
| optional_sparse | 1 of 8 optional int32 values | 4 | 13 | 806 |
| optional_dense | 8 optional int32 values | 32 | 41 | 806 |
| variable | 40-byte text + 8 entries (optional 6-byte text + int64) | 152 | 171 | 806 |
| packed | 200-byte text + 5,000 c32 samples | 40,200 | 40,205 | 84 |
| packed_struct | 1,000 nested fixed-size records | 40,000 | 40,003 | 806 |

The small/optional/variable/packed_struct catalogue includes all messages in
[`bench_cases.sdl`](../sdl/bench_cases.sdl); packed uses
[`benchmark.sdl`](../sdl/benchmark.sdl). The 40,205-byte packed data contains
one type ID byte, two string-length bytes, 200 text bytes, two element-count
bytes, and 40,000 sample bytes.

`packed_struct` is an array of 1,000 `BenchRecord` values (40 bytes each).
Each record contains an int32 ID, a `BenchPose` holding a `BenchVector`
position (three fl32 values) and a four-fl32 rotation, plus two fl32 measures.
This exercises two levels of nested structures and fixed arrays with mixed
integer/float fields. All backends construct the same values before timing.
The wire contains one type ID, a two-byte count and 40,000 record bytes.
Matlab/Octave represents the same records as component arrays, with fixed
array dimensions first and the record index last.

The historical measurements below predate this sixth case and its catalogue
additions; their preparation times describe the earlier catalogue.

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
and runtime dispatch dominate. C prepares fixed-record conversion spans during
catalogue preparation, merging adjacent components of equal width across nested
structures and fixed arrays. Compatible contiguous records can be converted as
one primitive span across the whole packed array. Scalar widths 2/4/8 use
specialized word swaps with alias-safe, unaligned `memcpy` loads/stores, enabling
compiler vectorization instead of a per-byte reversal loop. Native padding uses separate
offset spans; bool/enum validation is retained, and schema changes use the
generic decoder. Numeric records need only a bounds check during the
validation/size pass; Rust uses generated typed
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

## Java packed codec comparison

With JDK 17 on the same machine, medians of three JVM executions comparing
commit `f942d0d` with the generated fixed codecs and primitive-array API:

| Case | Before encode / decode MB/s | After encode / decode MB/s |
|---|---:|---:|
| packed | 1,249 / 1,239 | 2,081 / 2,419 |
| packed_struct | 11 / 17 | 1,753 / 1,281 |

MB/s here uses 1,000,000 useful bytes per second, rounded to integers. Each
execution retains the benchmark's 1,000 warmup calls and median of three
100 ms measurement trials. JVM compilation and garbage collection cause
variation between runs. Wire bytes and workloads are unchanged. The mixed
packed regression tests compare against independent big-endian wire oracles,
including raw floating-point bits, malformed values, truncations and evolving
schemas. The shared nested workload still constructs an object graph on decode;
primitive storage eliminates numeric boxing, but does not eliminate record,
substructure or array allocations.
