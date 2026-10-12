# Quadratic equation example (Matlab/Octave)

This simplified implementation mirrors the equation-solving pipeline in
[`abc-c`](../abc-c/README.md), using three sequential stages rather than
threads: input, solver, and display. Each stage receives or produces the same
SDL `EquationInput` and `EquationResult` messages. The messages are encoded and
decoded as SDL2 data buffers between stages; no sockets or threads are needed.

From this directory, run:

```sh
make run
```

The Makefile uses Matlab in batch mode when the `matlab` executable is on
`PATH`; otherwise it falls back to GNU Octave. Enter three finite real numbers
when prompted, for example `1 -3 2`.

Requires Python 3 for binding generation and either Matlab or GNU Octave.
`make clean` removes generated bindings.

## SDL2 catalogue

The sequential stages prepare a shared catalogue before encoding any data.
Description text and uint8 data buffers are passed separately to the runtime.
