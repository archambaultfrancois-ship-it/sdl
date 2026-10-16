# Catalogue predicates

The optional filter service prepares an expression from a runtime **SDL2 catalogue**
and a fully qualified root type. Reuse the prepared predicate: parsing, type checks,
name resolution and field binding happen once.

Object evaluation uses the current logical values. Buffer evaluation reads only
the required wire values and reconstructs quantized values locally. For example,
`temperature > 12.32` can match an object containing `12.34` and fail after wire
quantization to `12.3`. This difference is part of the model.

| Status | ABI code | Meaning |
| --- | ---: | --- |
| Match | 1 | Predicate is true. |
| NoMatch | 0 | Predicate is false. |
| Indeterminate | 2 | A required value is missing, an index is out of bounds, or a comparison involves NaN. |
| Error | -1 | Invalid data, incompatible object values or arithmetic; includes a diagnostic. |

Invalid expressions fail at preparation. Evaluation does not modify a message or
reject its encoding/decoding; the application decides which statuses to keep.

## Expressions

```text
active && temperature >= 12.3
position.x * position.x + position.y * position.y <= 25
exists(detail) && detail.code == 0
points[0].x > 0 && matrix[1][2] == 6
state == telemetry.State.ON
label == 'café' && len(label) == 4
any(points, p => p.x > 0 && p.y < 10)
all(samples, s => abs(s) <= 100)
any(points, p => any(points, q => p.x == q.x))
```

Precedence, highest first: unary `+ - !`, `* / %`, `+ -`, `< <= > >=`, `== !=`,
`&&`, `||`. Parentheses override precedence. Logic short circuits: missing
`&& false` is false, missing `|| true` is true; otherwise missing values propagate.
Errors in an evaluated branch remain errors.

Indices are zero based. Quantifiers support nested element variables. On an empty
array, `any` is false and `all` is true. Functions are `exists`, `len`, `abs`,
`real`, `imag`, `isfinite`. String `len` counts Unicode scalar values. Complex
values support `.real`, `.imag`, `real`, `imag` and `abs` (magnitude).

Literals include booleans, decimal integers, reals with optional exponents, and
single/double quoted UTF-8 strings with `\\`, `\'`, `\"`, `\n`, `\r`, `\t` escapes.
Enum constants can be fully qualified, relative to the root package (`State.ON`),
or explicitly global (`.State.ON`). Source SDL constants and aliases have already
been substituted before catalogue generation and are not expression names.

Integer arithmetic is exact in `[-2^63, 2^64-1]`, with checked overflow. Integer/real
comparisons retain integer precision, including `uint64` above `2^53`. `/` produces
a real result; `%` requires integers. Division by zero and nonfinite arithmetic
results are errors. NaN comparisons are indeterminate. Real arithmetic uses
binary64; incoming binary32 values retain their existing rounding when widened.

Limits: 64 KiB expression, 2,048 nodes, 64 nesting levels, 128 distinct loads and
64 quantifier scopes. The engine never calls host language expression evaluation.

## APIs and deployment

Rust calls the implementation in `runtime/rust/src/filter` directly. Other
backends use the ABI in `runtime/filter/include/sdl_filter.h` and `libsdl_filter`.
Ordinary generated codecs work without this optional shared library.

| Backend | Prepare / object / buffer | Build and deployment |
| --- | --- | --- |
| Rust | `filter::Filter::prepare` / `evaluate` / `evaluate_wire` | Depend on `runtime/rust`; generated messages implement `FilterValue`. |
| C | `sdl_filter_prepare` / `sdl_filter_evaluate_native` / `sdl_filter_evaluate_wire` | `make filter-native`; link the shared library and compile `runtime/filter/c/sdl_filter_native.c` with the C runtime and registry. |
| Python | `sdl_filter.Filter` / `evaluate` / `evaluate_wire` | `make filter-python`; install the wrapper, CPython extension and shared library together. |
| Java | `new sdl.filter.SdlFilter` / `evaluate` / `evaluateWire` | `make filter-jni`; add `build/filter/sdl-filter.jar` to the classpath and native libraries to `java.library.path`. Java 7+. |
| Ada | `SDL_Filter.Prepare` / instantiated `SDL_Filter.Typed.Evaluate` / `Evaluate_Wire` | `make filter-native`; compile `runtime/filter/ada`, generated accessors and the existing Ada runtime; link the shared library. |
| MATLAB | `sdl_filter('prepare',…)` / `('evaluate',…)` / `('wire',…)` | `make filter-matlab-mex`; add the wrapper, generated bindings and `build/filter/matlab-mex` to the path. |
| Octave | Same interface as MATLAB | `make filter-mex`; add `build/filter/mex` instead. |

The Python and JNI libraries use relative loader paths: keep them beside
`libsdl_filter`. The MEX directory sits beside `release`. Rebuild extensions for
the target interpreter. Native builds and MATLAB R2017a / Octave are verified
on Linux.

Python example, with generated `telemetry.Sample`:

```python
from telemetry.model import Sample
from sdl_runtime import description, prepare
from sdl_filter import Filter, encode_filtered, decode_filtered

catalogue = description(Sample)
codec = prepare(catalogue, Sample)
with Filter(catalogue, 'telemetry.Sample', 'temperature > 12.32') as predicate:
    wire, object_status = encode_filtered(codec, message, predicate)
    decoded, wire_status = decode_filtered(codec, wire, predicate)
```

Helpers return the codec output plus the filter status. The decode helper evaluates
at the wire stage before decoding the complete message, currently in two passes.
Use `predicate.evaluate(decoded)` to select the decoded object stage instead.

C supports custom field readers through `sdl_filter_evaluate_object`: bound field
IDs, owners and indices are supplied by the engine. Borrowed strings remain alive
until evaluation returns. Virtual complex components use owner `$complex` and
field IDs 1 (real), 2 (imag). An optional reader exposes immutable strided numeric
columns; unsupported spans fall back to scalar reads. Rust offers corresponding
`Source` and `FilterValue` traits. Python accepts generated messages and dictionaries;
Java/Ada use generated messages; MATLAB uses generated structures and columns.

Rust `Span` metadata is read only. Its unsafe constructor requires all cells to
remain initialized, readable and immutable for the borrowed lifetime; safe code
cannot alter the pointer, bounds or scalar representation afterward.

Release predicates with C `sdl_filter_free`, Python `close`/context managers,
Java try-with-resources, Ada controlled objects, or MATLAB
`sdl_filter('close', token)`. Rust predicates are immutable; Java serializes access
to an instance. C diagnostics are thread local, read after an Error. Do not close
a C predicate or mutate its source during evaluation.

Wire evaluation validates the root and accessed values. It is a targeted reader:
a matching filter does not certify untouched variable fields. Use the normal
decoder for full validation.

## Verification and performance

`make test-filter` runs the engine tests, differential tests against the general
evaluator, and a shared object/wire corpus for all six languages, with Octave
exercising the MATLAB representation. The corpus covers optionals, exact unsigned
comparisons, quantization, arrays, nested quantifiers, UTF-8, enum packages and
complex values. Regression tests also cover empty string literals, quoted syntax,
span privacy, large fixed wire sequences, MATLAB fixed struct arrays and complex
fields stored as real values. For MATLAB itself, build its MEX and run
`tests/filter/matlab/filter_checks.m` with the generated fixture on the path.

`make bench-filter` measures one scalar comparison, `x*x+y*y<=25` on a two-field
message, and a full scan of 256 points. Preparation is excluded; bindings and
the JVM are warmed. Timings are medians of five trials, including codec output
allocation/release. Per-backend logs and `build/filter/benchmarks.json` record the
results. Add `--backends matlab --matlab matlab` to the Python benchmark runner
to measure MATLAB itself after building its MEX.

Object ratios divide filter cost by encoding cost; wire ratios divide it by
decoding cost. The target is a filter cost of at most three times the codec.
The combined wrappers add both costs. This is a workload target: expression
complexity can exceed codec work considerably. Timings do not gate unit tests.

Development measurements on Linux/WSL2, with release/O3 native builds, JDK 7,
GNAT 21.1 and MATLAB R2017a, give the following ratios for the small arithmetic
predicate `x*x+y*y<=25`:

| Backend | Object filter / encode | Wire filter / decode |
| --- | ---: | ---: |
| C | 2.98 | 1.39 |
| Rust | 1.92 | 0.88 |
| Python | 0.67 | 0.35 |
| Java | 2.28 | 2.30 |
| Ada | 0.25 | 0.36 |
| MATLAB | 0.09 | 0.08 |
| Octave | 0.03 | 0.03 |

All three measured cases meet the target. For the full 256-point scan, the largest
measured ratios are 0.97 for objects (Java) and 0.55 for wire data (Rust). These
figures compare against the current codecs, including their existing interpreter
or conversion overhead; they are not interchangeable absolute latencies between
languages. C and Java retain the narrowest margin on tiny messages.

The review fixes were checked against a preceding run using the same cases.
Raw measurements are retained in `build/filter/review-before.json` and
`review-after.json`; Rust was remeasured alone in `review-rust-recheck.json`
after an initial timing increase. `review-comparison.json` records that comparison.

Optimizations include prepared IDs/fixed wire offsets, cached loads, specialized
numeric programs, short circuit logic, numeric column scans for Rust/C objects
and fixed wire elements, and a position cache for variable wire arrays. Adapters
read requested fields without materializing a generic tree. JNI uses cached field
IDs and a stack copy for short buffers. General readers handle layouts that cannot
safely expose a scalar column.
