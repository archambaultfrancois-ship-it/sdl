# Multiformat schema compilation

Two symmetric round trips exercise the same conversions and checks:

| Command | Source | Converted | Restored |
| --- | --- | --- | --- |
| `make export` | `schemas/sdl` | Mixed sources | SDL sources |
| `make import` | `schemas/mixed` | SDL sources | Mixed sources |
| `make run` | Both workflows above | | |

```text
export: SDL   -> mixed -> SDL
import: mixed -> SDL   -> mixed
```

Both commands convert twice, generate bindings in six languages at all three
stages, and execute the same Python message round trip at every stage. They check
identical generated files, SDL2 catalogues and binary payloads against their own
source. Each command runs independently using only its own source directory.

From this directory:

```sh
make export
make import
make run
```

From the repository root, for example:

```sh
make -C sdl/examples/multiformat run
```

`make` also defaults to `run`. Python 3.9+ and pip are required. The first
invocation installs PyYAML under `build/deps` if it is not already available.
Only schema conversion needs PyYAML. No C, Rust, Java, Ada or Matlab toolchain is
needed: bindings are generated and compared in all six languages, and Python
executes the binary exchanges.

## The two representations

`schemas/sdl` contains four textual SDL units. The equivalent maintained inputs
under `schemas/mixed` use this unit-to-format mapping:

| Unit | Mixed format | Declarations and references |
| --- | --- | --- |
| [`common.sdl`](schemas/mixed/common.sdl) | SDL | Constant `AXES`, alias `Sample`, enum `State` |
| [`position.sdl.xsd`](schemas/mixed/position.sdl.xsd) | Annotated XSD | Message `Position` |
| [`sensor.sdl.json`](schemas/mixed/sensor.sdl.json) | JSON | Message `Sensor` referring to `Position` |
| [`reading.sdl.yaml`](schemas/mixed/reading.sdl.yaml) | YAML | Message `Reading` referring to `Sensor`, `State`, `Sample` and `AXES` |

The same mapping constructs mixed outputs in both workflows. All declarations
use package `multiformat`. The sample message includes a fixed three-axis array,
packed quantized samples, optional strings and an integer varint.

`SDL -> mixed` exports the units in the available representations, then selects
the four files above. `mixed -> SDL` uses the compiler's `-sdl` export. Formatting
is normalized, constant expressions become numeric values and fields use resolved
types; comments ignored by SDL are not recovered. Omitted field IDs, required
modifiers and source version 1 are restored implicitly by the readers.

The dedicated suffixes `.sdl.json`, `.sdl.yaml` and `.sdl.xsd` identify schema
sources. XSD uses the [SDL annotated profile](../../docs/schema_formats.md#annotated-xsd-profile).
These formats describe schemas; message exchange remains SDL2 binary data.

## Symmetric outputs

Both directions use the same layout beneath `build/<direction>`:

| Directory | Contents |
| --- | --- |
| `sdl/multiformat` | Converted or restored textual SDL units |
| `mixed/multiformat` | Converted or restored mixed units |
| `formats/{sdl,json,yaml,xsd}/multiformat` | Intermediate exports used to assemble the mixed directory |
| `generated/{source,converted,restored}/<language>` | C, Rust, Python, Java, Ada and Matlab/Octave bindings |
| `results/{source,converted,restored}` | `catalogue.sdl2`, `reading.bin` and `reading.hex` |

For example, `make export` creates `build/export/mixed` and then reconstructs
`build/export/sdl`. `make import` creates `build/import/sdl` and then reconstructs
`build/import/mixed`. Neither workflow writes into `schemas`.

Each successful workflow prints its route, the three stage results, and a final
`PASS`. `make run` executes exactly these two workflows. It supports parallel
execution with `make -j2 run` because their outputs are separate.

Additional commands:

| Command | Operation |
| --- | --- |
| `make deps` | Make the optional YAML dependency available |
| `make test` | Alias for `make run` |
| `make clean` | Remove the example's build directory |
| `make export BUILD_DIR=other-build` | Run a workflow with another output directory |
