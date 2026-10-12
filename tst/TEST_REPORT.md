# SDL2 validation report

Validation performed on 2026-10-08 in the Termux development environment.
Java was run after sourcing `~/.bashrc`. SDL2 uses big endian throughout.

## Runtime suite

`make test` passed for all five runtimes:

- C: typed/dynamic fixture checks, scalar and composite values, schema
  evolution, one-allocation storage, cloning, and malformed data.
- Rust: 11 integration tests passed.
- Python: 47 tests passed (14 codec/limit tests and 33 generator tests).
- Matlab/Octave: fixture, catalogue, evolution, arrays, and malformed-input
  checks passed using Octave; proprietary Matlab was unavailable.
- Java: generated sources compiled and the runtime checks passed.

All five implementations check the same 132-byte catalogue and 16-byte data
fixture. Cases include optional presence, ULEB boundaries, big-endian signed
numbers, UTF-8, enums, complex values, nested messages, fixed arrays, and empty
messages. Evolution cases cover renamed fields, unknown validated fields,
missing local defaults, incompatible types/cardinality, and nested layouts.
Malformed cases include truncated data, trailing bytes, invalid counters,
booleans, enums, UTF-8, recursive schemas, and invalid fixed element types.
Shared-subtype and depth cases ensure preparation reuses computed graph values
and rejects overly deep graphs, including paths through previously visited
nodes. Generator tests cover naming, collisions, safe regeneration, and size
limits; large generated Java descriptions avoid the constant-pool string limit.

## Examples and tools

The C, Rust, Python, Java, and Octave equation examples produced roots 1 and 2
for input `1 -3 2`, with description exchange separate from data. Radio capture
interop passed all six C/Rust/Python encoder/decoder pairings; all emitted the
same 135-byte data and a separate catalogue sidecar.

The sample UDP captures were regenerated for SDL2. The Lua dissector passes
`luac -p`. A Lua API mock decoded the five valid data packets, rejected all
three malformed packets, and preserved the original catalogue during packet
redissection after a later announcement. This does not replace a Wireshark run. The C Wireshark plugin was not compiled: Wireshark development
headers/pkg-config metadata are unavailable in this environment. Runtime suite
success does not validate the Wireshark API integration.

## Performance

`make bench BENCH_ITERATIONS=200` covers small, sparse optional, dense optional,
variable nested, and packed complex workloads across all five runtimes. See
[benchmark methodology and results](../docs/benchmarks.md). Measurements are
local codec throughput with allocations, without network transport.
