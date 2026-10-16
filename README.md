# SDL and OSE

This repository contains two projects that evolve together:

- **[SDL](sdl/README.md)** (Schema Design Language) generates message codecs
  for C99, Rust, Python, Java, Ada, and Matlab/Octave. It also provides catalogue filtering,
  recording/replay tools, and a Wireshark adapter.
- **[OSE](ose/README.md)** (Orchestration, Simulation, Exécution) orchestrates
  and executes component graphs with SDL message contracts, bounded RAM delivery, external clients,
  deterministic parallel simulation, and [distributed real-time deployment](ose/docs/distributed.md).

OSE depends on SDL's public generator and runtime APIs. SDL builds and
runs independently of OSE. Each project owns its sources, tests,
examples, documentation, dependencies, and build outputs.

```text
sdl/                    SDL project root
  sdlc/                 SDL compiler and binding generators
  runtime/              Language runtimes and optional services
  schemas/              Shared source schemas
  tests/                Tests, fixtures, and benchmarks
  examples/             Runnable SDL examples
  tools/                Filter, recording, and Wireshark tools
  docs/                 SDL documentation
ose/                    OSE project root
  src/                  Modeling and execution engine
  sdk/                  Component and external client SDKs
  tests/                Unit and integration tests
  examples/             Component graphs and client examples
  docs/                 OSE documentation
.github/workflows/      Repository CI
```

## Build and test

From the repository root:

```sh
make check-sdl
make check-ose
make check             # Both projects, sequentially
make test              # Both projects, verbose output
```

The root Makefile also forwards other SDL targets, such as `make test-java`
and `make test-recording`. Project-specific commands remain available:

```sh
make -C sdl test-c
make -C sdl test-recording-native
make -C ose test-java
make -C ose demo
```

See each project's README for its toolchains and optional dependencies.
OSE uses `sdl/` by default. Set `SDL_ROOT` or pass `--sdl-root` to select
another SDL project directory containing `sdlc/` and `runtime/`.
The root Makefile also accepts `SDL_ROOT=/absolute/path/to/sdl` for OSE targets.
The former `check-chain` and `test-chain` targets remain aliases for
`check-ose` and `test-ose`.

## Migration from the previous layout

SDL's former root-level directories now live under `sdl/`. The schema directory
was renamed from `sdl/` to `sdl/schemas/`, `tst/` to `sdl/tests/`, and `samples/`
to `sdl/examples/`. The compiler lives in `sdl/sdlc/`.
The former `chain-env/` project directory is now `ose/`.
Run the commands in SDL documentation from `sdl/`.
For example:

```sh
cd sdl
python3 sdlc/generator.py -c -rust -python -java -ada -matlab schemas build/generated
make -C examples/abc-c run
```

Existing Git history is retained. Rebuild generated sources, executables and
OSE plans after moving a checkout: they can contain absolute paths.
SDL and OSE build products remain in `sdl/build/` and `ose/build/`.

The standalone OSE workflow reads the SDL reference and subdirectory from
`ose/sdl-dependency.json`. It follows `main` and uses its `sdl/` project,
with the compiler at `sdl/sdlc/generator.py`.
