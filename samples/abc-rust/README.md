# Quadratic equation example (Rust)

This Rust implementation mirrors [`abc-c`](../abc-c/README.md): three threads
perform input, solving, and display stages. The stages exchange the same SDL
`EquationInput` and `EquationResult` messages over two Unix-domain socket
pairs, with a four-byte length prefix around each encoded SDL frame.

Build and run from this directory:

```sh
make run
```

Enter three finite real numbers when prompted, for example `1 -3 2` for the
equation `x^2 - 3x + 2 = 0`. The solver displays the decoded input and encoded
result messages before the display thread prints the solution.

Requires Rust/Cargo, Python 3 for binding generation, and a Unix platform that
provides `UnixStream::pair`. `make clean` removes generated bindings and build
artifacts. Use `make WIRE_ENDIAN=big run` or `make WIRE_ENDIAN=little run` to
select the SDL wire byte order.
