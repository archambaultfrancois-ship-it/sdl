# Quadratic equation example (Python)

This Python implementation mirrors [`abc-c`](../abc-c/README.md): three threads
perform input, solving, and display stages. They exchange the same SDL
`EquationInput` and `EquationResult` messages over two Unix-domain socket
pairs, with a four-byte length prefix around each encoded SDL frame.

Build and run from this directory:

```sh
make run
```

Enter three finite real numbers when prompted, for example `1 -3 2` for the
equation `x^2 - 3x + 2 = 0`. The solver displays the decoded input and result
messages before the display thread prints the solution.

Requires Python 3. Choose the same wire order as communicating SDL peers with
`make WIRE_ENDIAN=big run` or `make WIRE_ENDIAN=little run`. `make clean`
removes generated bindings.
