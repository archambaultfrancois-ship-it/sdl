# Quadratic equation example (Java)

This Java implementation mirrors [`abc-c`](../abc-c/README.md): three threads
perform input, solving, and display stages. The stages exchange the same SDL
`EquationInput` and `EquationResult` messages over two local byte-stream pipe
pairs, with a four-byte length prefix around each encoded SDL frame. Like C,
the transport uses local Unix-domain stream sockets; Java 16 or newer is
required for its standard Unix-domain socket API.

Build and run from this directory:

```sh
make run
```

Enter three finite real numbers when prompted, for example `1 -3 2` for the
equation `x^2 - 3x + 2 = 0`. The solver displays the decoded input and encoded
result messages before the display thread prints the solution.

Requires Java 16 or newer and Python 3 for binding generation. Choose the same
wire order as communicating SDL peers with `make WIRE_ENDIAN=big run` or
`make WIRE_ENDIAN=little run`. `make clean` removes generated bindings and
class files.
