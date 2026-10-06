# Throughput benchmarks

Run the three typed runtime benchmarks from the repository root with:

```sh
make bench
```

The benchmark message is created before timing. It contains a 200-byte ASCII
header and a packed array of 5,000 `c32` values. Its body is 40,216 bytes:
200 bytes of string data, 40,000 bytes of complex samples, and two 8-byte
field headers. The complete frame adds the 8-byte descriptor-length and hash
headers plus the schema descriptor, for `40,224 + descriptor_size` bytes per
message. The benchmark checks that this frame size is stable while encoding;
decoding checks successful completion without inspecting decoded values in
the timed loop.

`SDL_BENCH_ITERATIONS` controls the number of iterations; `make bench` uses
`BENCH_ITERATIONS` (default 200) to set it for all three runtimes. The value
must contain only ASCII decimal digits and represent a positive integer within
the platform range; otherwise it falls back to 200 iterations.
Output reports messages per second and MiB per second, where MiB is 1,048,576
bytes. If an iteration count is too small for the timer resolution, the
benchmark reports that instead of printing an infinite rate.
All three benchmark commands use the default big-endian wire mode.

These figures measure typed codec calls and their allocations. They do not
include message construction, transport, or application work. The C benchmark
uses `clock()` CPU time, while Rust `Instant` and Python `perf_counter()` use
elapsed wall time. Decode implementations also have different internal work;
for example, C first measures the storage required for a decoded value, then
decodes it. Treat the results as implementation-specific measurements on the
same machine and build configuration, not as a strict language ranking.
