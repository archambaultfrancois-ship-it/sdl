# SDL2 throughput benchmarks

Run `make bench` (or an individual `bench-c`, `bench-rust`, `bench-python`,
`bench-matlab`, `bench-java`, `bench-ada` target). The aggregate checks identical wire sizes
across the six implementations and writes `build/benchmarks.json` with
catalogue sizes, preparation times, and throughput. The aggregate prints a
case-by-language matrix in **MB/s (Enc / Dec)**, rounded to integers; MB/s
means 1,000,000 useful bytes per second. A displayed zero means less than
0.5 MB/s. Columns correspond to the runtimes executed (Matlab or Octave for
`bench-matlab`). Individual targets and JSON retain the detailed MiB/s and
messages/s measurements.

## Method

Ada builds use Ada 2012, `-O3` and enabled range checks.
C builds default to `-O3` for tests, benchmarks and samples. Rust development,
test and release profiles use `opt-level = 3`. Debug assertions and overflow
checks retain their normal development/test defaults. Java, Python and
Matlab/Octave use their runtime compilation/interpreter settings.

Each message is constructed before timing and checked with a round trip.
Catalogue rendering and preparation are outside the message timing. Each case
reports one preparation call separately; it includes parsing, schema checks,
and local mappings, and is not a steady-state preparation benchmark.

After warmup (C/Rust/Python/Ada: 100 calls of each operation; Java: 1,000;
Matlab/Octave: 20), encode and decode each run for three trials. Each trial has
at least 200 calls and 0.1 seconds; the median messages/s is reported. Set
`BENCH_ITERATIONS` to change the minimum, or `SDL_BENCH_ITERATIONS` for standalone
programs. Rust applies `black_box` to the context, input and returned value in timed
operations so compiler optimizations cannot rely on constant inputs. The same
message buffer is reused; these are warm-cache codec measurements.
Every backend uses elapsed wall time. C uses `CLOCK_MONOTONIC`, Rust
`Instant`, Python `perf_counter`, Java `nanoTime`, Matlab/Octave `tic/toc`, and Ada `Ada.Real_Time.Clock`.

Timed operations include allocating encoded output or decoded values and
releasing/discarding them. There is no transport, message construction,
application work. Ada decoding includes C dynamic-tree construction,
conversion to typed Ada records/vectors, and tree destruction for the generic
fallback. Compatible Packed messages decode directly into Ada values. Java results depend on JIT warmup
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

The Ada backend selects direct codecs for messages containing Packed fields
when all reachable record layouts and enum value sets match. Eligibility is
cached at preparation. Exact sizing and a single byte array replace per-byte
vector appends; decoding reserves typed vector storage and bypasses C dynamic
trees. Generated fixed subrecord codecs and endian conversions are inlined with
`-gnatn`, with range and message-validity checks enabled. `SDL_ADA_NO_DIRECT=1 make bench-ada` measures the same API and messages using the generic fallback.
Its catalogue contains every type in its schema file, so catalogue sizes and
preparation times can differ from backends that render only the root type and its
dependencies. All data wire sizes and useful byte counts remain comparable.

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

## Optional MATLAB full-message MEX adapter

Run `make matlab-mex` to compile `sdl_packed_mex` into the generated MATLAB
folder, then run `make test-matlab bench-matlab` (or `make bench`). The Linux
prototype uses the separate-complex C Matrix API, validated on MATLAB R2017a,
and builds with `-O3` and link-time optimization. Build the MEX binary for the
MATLAB installation in use. It links the SDL C runtime and the generated
`benchmark` descriptors.

Prepared contexts select a complete native path for the exact, singleton
`BenchPayload` catalogue. The MATLAB context owns a handle object that retains
a prepared C context. Context copies share the owner; its destructor releases
the native resources. Opaque uint64 tokens are checked in a native registry,
released tokens are rejected, a monotonic token epoch prevents reuse across
MEX reloads, and `mexLock`/`mexAtExit` handle module lifetime and shutdown.
Tokens are cached in the MATLAB context to avoid property access on every call.

Generated `BenchPayload` bindings call MEX directly for the complete message.
Encode measures UTF-8 directly from MATLAB UTF-16 characters, allocates one
final uint8 buffer, and writes framing, counts, string bytes and packed c32
samples into it. Single and double sample arrays are supported. Decode reuses
`type_decode_size` to validate the complete wire message before allocating the
final MATLAB structure, UTF-16 string and complex single component planes.
No decoded native object tree or intermediate sample array is constructed.

Other catalogues and schema evolution retain MATLAB decoding. The earlier c32
payload converter remains available for those paths. Packed types and
structures outside the complete adapters retain their existing conversion.
Cell samples use the MATLAB path.
The complete adapters currently cover `BenchPayload` and `BenchRecordBatch`;
other message types retain the existing paths.

Set `SDL_MATLAB_NO_MEX=1` before preparing a context to disable MEX entirely.
Set `SDL_MATLAB_MEX_PAYLOAD_ONLY=1` to retain only payload acceleration. For
example, `SDL_MATLAB_NO_MEX=1 make bench-matlab` measures the MATLAB fallback.
Without a MEX binary, MATLAB and Octave continue to use their normal runtime.

Regression tests cover independent wire oracles, NaN/negative-zero raw bits,
unaligned payload offsets, all message truncations, malicious counters, UTF-8
and UTF-16 validity, embedded NUL and supplementary Unicode characters,
unsupported schema fallback, context sharing/release, and stale tokens after
module reload. `make test-matlab` runs these when the binary is present.

On the same machine, medians of three executions of the standard MATLAB
benchmark give these decimal MB/s, rounded to integers:

| Packed c32 path | Encode MB/s | Decode MB/s |
|---|---:|---:|
| MATLAB runtime | 110 | 78 |
| Payload-only MEX | 185 | 135 |
| Complete-message MEX | 891 | 1060 |

The complete path improves this workload by approximately 8 times on encode
and 14 times on decode over MATLAB alone. Other benchmark cases retain their
existing paths. The build sets `COPTIMFLAGS=-O3` and `LDOPTIMFLAGS=-O3`
explicitly, because MATLAB's default optimization flags otherwise override
an `-O3` supplied through `CFLAGS`.

For a separate fixed-loop diagnostic, add `build/generated/matlab` and
`tst/matlab` to the MATLAB path and run `bench_mex` (default: 20000 iterations,
three trials). With the same 5000-sample message, it measured 1717 / 2589 MB/s
through the generated MATLAB binding and 2883 / 7122 MB/s through direct MEX
calls. These rates exclude the standard harness's repeated timing checks and
dispatch, so they must not replace its measurements in the language matrix.

The `packed_struct` adapter selects the exact compiled `bench_cases` catalogue
and local schema. `BenchRecordBatch` has root ID 5 in this catalogue; other
roots retain normal MATLAB encode/decode. Four SoA leaves (`id`,
`pose.position.values`, `pose.rotation`, `measures`) are interleaved directly
into 40-byte wire records on encode. Decode validates the complete message
with the persistent C context, then creates the nested scalar structures and
final int32/single arrays directly. No per-record MATLAB structures or
intermediate byte matrices are created. Full int32 IDs and real single leaves
use this adapter; other input classes retain MATLAB conversion.

The packed-struct tests compare raw wire bytes against an independent
big-endian oracle, including NaNs and negative zero, empty arrays, counter
boundaries and 1000-record batches. They also verify all truncations of a
small message, trailing bytes, invalid/overflowing counters, mismatched leaf
lengths, other roots in the same catalogue and changed-schema fallback.

The full language benchmark measured `packed_struct` at 708 / 675 MB/s
(encode / decode) with this adapter, versus 24 / 27 MB/s in the preceding
MATLAB measurement, approximately 30 times / 25 times faster. The same run
measured `packed` at 894 / 1064 MB/s. These are standard harness measurements
with unchanged useful-byte workloads, rounded to integers; normal machine
load and measurement variation still apply.

## Optional Python native Packed adapter

Run `make python-native` to build `_sdl_native` in the generated Python folder
using the invoking CPython's headers and ABI suffix. The current build is
validated on Linux with CPython 3.9 and GCC, using `-O3` and LTO; it requires
Python development headers, but no NumPy or setuptools. Add both
`runtime/python` and the generated Python folder to `PYTHONPATH`, as the
Makefile already does. Build again after changing the C extension or Python
installation. `make bench` uses the extension when present; it does not build
optional native adapters automatically.

Contexts retain opaque capsule-owned fixed conversion plans, with cached
field names, local classes and nested array dimensions. The C extension uses
the SDL C wire helpers to write and read big-endian words directly. It handles
primitive numeric/complex Packed leaves, fixed arrays, and exact fixed
structures with nested structures. Fixed-size repeated fields can also use
these plans. No benchmark-specific message name or catalogue is embedded.
For the list/message API, framing and strings remain in the Python runtime.
The buffer API below also prepares complete native roots containing required
strings and Packed fields. Plans derive from the validated catalogue and exact
local bindings, without generated benchmark-specific C descriptors. They use
SDL C wire and UTF-8 helpers, rather than the C context/object-tree API.

The public list/message API is preserved: encode walks Python objects in C,
and decode constructs the final Python list, nested messages and component
values. It avoids intermediate flattened Python component lists and per-record
Python conversion closures. Generated fixed messages and the runtime's complex
value objects are populated directly. Native plans retain the classes they
need and release their allocations when the owning capsules are destroyed.
The GIL remains held because conversion accesses and creates Python objects.

Enums, schema evolution requiring unmatched local fields, zero-size records,
variable-size fields, and fixed records beyond the native plan limits retain
the ordinary Python path. Disable native selection with
`SDL_PYTHON_NO_NATIVE=1` before preparing a context. An unavailable extension
also selects the ordinary runtime.

Three alternating executions per mode of the unchanged standard Python
benchmark produced these median decimal MB/s, rounded to integers:

| Case | Python encode / decode | Native encode / decode |
|---|---:|---:|
| packed | 59 / 20 | 160 / 37 |
| packed_struct | 12 / 8 | 108 / 34 |

Decode still allocates a Python object graph, limiting its throughput compared
with runtimes using contiguous numeric storage. Rates vary with machine load;
these comparisons use runs from the same measurement session.

Tests compare independent big-endian primitive wire oracles, signed integer
limits, floating-point signs/nonfinite values, nested records, empty arrays,
counter boundaries, complete-message truncations, trailing bytes, malformed
native plans, invalid booleans, overflow, buffer bounds, context lifetime,
schema evolution and input mutation during numeric conversion. The Python
suite runs with native selection enabled and disabled. Address/undefined
sanitizer validation was attempted, but this host lacks the sanitizer runtime
libraries, so no sanitizer result is claimed.

## Python Packed buffer API

`PackedArray` stores immutable, tightly packed numeric records and their
prepared native layout. `decode(ctx, wire, packed='view')` returns PackedArray
leaves for supported Packed fields. For immutable `bytes` input, the views
retain the original message and do not copy or convert the numeric payload.
Mutable input, including a readonly view of mutable storage, is snapshotted
before validation. Ordinary `decode(ctx, wire)` still returns lists/objects.
Unsupported layouts and schema evolution retain the ordinary reader.

`PackedArray.from_values(ctx, type_name, values)` converts an existing list
once. `PackedArray.from_buffer(ctx, type_name, buffer, byteorder='native')`
accepts contiguous buffer-protocol storage, including `array.array`, and
converts its scalar words in bulk. Input records must follow SDL field order,
without padding, with every scalar using the specified byte order. Conversion
preserves raw floating-point bits, including signaling NaN payloads. Mutable
input is copied into immutable storage; later mutations do not change the
PackedArray.

With `defer=True`, from_buffer retains an immutable snapshot in the supplied
byte order. Complete native encoders swap its words directly into the final
message on every call, avoiding an intermediate converted payload. `buffer`
exposes source storage in `byteorder`; `wire_buffer` provides big-endian bytes
and converts when necessary. Slicing retains the representation, indexing
materializes one record, and `tolist()` builds the ordinary object graph.
Views retain their message bytes and layout independently of the Context.

For exact roots made only of required strings and supported Packed fields,
native encoding allocates one final bytes object, or writes directly into
caller storage with `encode_into(ctx, message, output, offset=0)`. The return
value is the number of bytes written. The general encode_into fallback may
leave a prefix on error. Complete native decoding validates message ID,
canonical uint32 counters, strict UTF-8, payload bounds, boolean values and
trailing bytes before creating the returned message and PackedArray views.
Numeric types accept every wire bit pattern, so those payloads need only size
validation; booleans are scanned. Nested/general roots retain Python framing
while benefiting from Packed views and bulk conversion where supported.

For example, interleaved native-order c32 components can be supplied without
creating a Complex32 object for each sample:

```python
from array import array
from benchmark import BenchPayload
from sdl_runtime import PackedArray, description, prepare, encode, decode

ctx = prepare(description(BenchPayload), BenchPayload)
message = BenchPayload(header='samples', samples=PackedArray.from_buffer(
    ctx, 'c32', array('f', [1, 2, 3, 4]), defer=True))
wire = encode(ctx, message)
received = decode(ctx, wire, packed='view')
assert received.samples.tolist() == message.samples.tolist()
```

`make bench-python-buffers` uses the same messages, useful-byte counts and
wire bytes as the ordinary benchmark. It reports this alternate API separately;
`make bench` also prints this table when the extension is enabled. Measurements
are stored in `build/python-buffer-benchmarks.json`, separate from the ordinary
language matrix in `build/benchmarks.json`. Three executions gave these median
decimal MB/s, rounded to integers:

| Case | Encode wire buffer | Encode native buffer | Encode native into | Decode view |
|---|---:|---:|---:|---:|
| packed | 22750 | 17074 | 16286 | 26172 |
| packed_struct | 23536 | 16784 | 15965 | 29844 |

The native-buffer encoders include endian conversion on every call. Input
creation and freezing occur before timing, as message creation does in the
other workloads. Wire-buffer encode reuses already big-endian input. Decode
view keeps the numeric wire representation; subsequent host-order conversion
or object materialization is outside this measurement. These rates therefore
must be distinguished from ordinary object API decode, which constructs all
values. The full-message path and buffer conversions hold the GIL.

Additional regressions cover independent complex/nested-record wire oracles,
both byte orders, mixed scalar widths, deferred direct conversion, raw NaN
bits, signed zero, mutable-buffer snapshots, zero-copy byte ownership,
slices/indexing, context release, native/generic nested roots, every truncation
of small messages, noncanonical/overflowing counters, invalid UTF-8, invalid
booleans, layout mismatches, output offsets/capacity, multi-byte message IDs,
and disabled/absent native binaries. Incompatible old extension versions are
ignored so that stale binaries preserve the ordinary Python fallback.
