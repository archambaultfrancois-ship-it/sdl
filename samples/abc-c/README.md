# Quadratic equation example (C)

This C implementation uses three POSIX threads and the SDL C runtime. Matching
Rust, Java, and Python programs are in [`abc-rust`](../abc-rust/README.md),
[`abc-java`](../abc-java/README.md), and
[`abc-python`](../abc-python/README.md). Each follows the same input, solve,
and display pipeline with the same SDL message types. The simplified,
sequential Matlab/Octave version is in
[`abc-matlab`](../abc-matlab/README.md).

1. The input thread asks for the real coefficients `a`, `b`, and `c`.
2. The solver thread decodes and displays the input SDL message, solves
   `a*x^2 + b*x + c = 0`, displays the result SDL message, and sends it.
3. The display thread decodes the result message and prints the solutions.

The threads exchange length-prefixed SDL2 data buffers over two Unix-domain
`socketpair` connections. The four-byte frame length is transport framing;
the SDL runtime encodes and decodes the message contents. All application code
and runtime code used by the executable are C. Python 3 is used only to run the
existing SDL C code generator during the build.

Build and run from this directory:

```sh
make run
```

Run it in a terminal and enter three numbers when prompted, for example
`1 -3 2` for the equation `x^2 - 3x + 2 = 0`. The solver displays the SDL
messages it receives and sends before the display thread prints the solution.

Requires a C99 compiler, POSIX threads and Unix-domain sockets, and Python 3 for generation.
Use `make clean` to remove generated bindings and the executable.

## SDL2 catalogue

Each socket direction sends one length-prefixed UTF-8 catalogue at opening,
then length-prefixed SDL2 data. The receiving stage prepares the catalogue
once. This example sends one message per connection. The four-byte big-endian
lengths belong to the stream transport; the runtime receives data separately.
