# Quadratic equation example (Ada)

This Ada 2012 example follows the same pipeline and SDL schema as
[`abc-c`](../abc-c/README.md):

1. The input task asks for the real coefficients `a`, `b` and `c`.
2. The solver task decodes and displays `EquationInput`, solves
   `a*x^2 + b*x + c = 0`, displays `EquationResult`, and sends it.
3. The display task decodes the result and prints the solutions.

The three Ada tasks communicate over two Unix-domain `socketpair` connections
using `GNAT.Sockets`. Each connection sends a length-prefixed UTF-8 SDL2
catalogue at opening, then one length-prefixed SDL2 data message. The four-byte
big-endian lengths are stream framing, separate from the SDL message contents.
The framing code handles partial reads/writes and limits frames to 1 MiB.

The application, equation solver and transport are Ada. The generated bindings
encode in Ada and use the C runtime for validated decoding, with automatic
context finalization. Each task owns its context and socket endpoints. Socket
closure propagates input/codec failures downstream; the main program waits for
all tasks and returns a nonzero status on failure.

## Build and run

From the repository root:

```sh
make -C samples/abc-ada run
```

Or from this directory:

```sh
make run
```

Enter three numbers when prompted, for example `1 -3 2` for
`x^2 - 3*x + 2 = 0`. The solver prints the decoded coefficients and outgoing
result before the display task prints the roots `1` and `2`.

Other examples:

| Input | Result |
| --- | --- |
| `1 2 1` | One real root: -1 |
| `1 0 1` | Complex roots: 0 +/- 1i |
| `0 2 -4` | Linear equation: one real root, 2 |
| `0 0 0` | Every real number is a solution |
| `0 0 1` | No solution |

Requires Python 3 for generation, a C99 compiler, GNAT with tasking and
`GNAT.Sockets`, and Unix-domain sockets. The Makefile uses
`GNAT_HOME=~/opt/gnat-21.1`, `ADA_RTS=sjlj` and `-O3` by default. The supplied
GNAT installation's native runtime lacks generic container bodies; its sjlj
runtime is complete. With another GNAT installation, override the settings:

```sh
make GNAT_HOME=/opt/gnat ADA_RTS=native run
```

`make generate` produces Ada bindings under `build/generated/ada`.
`make clean` removes generated files, object files and the executable.
See the [Ada backend documentation](../../docs/ada.md) for the typed codec API.
