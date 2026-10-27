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

## Local application environment

For local application development in Bash, source the framework environment in
each terminal from the application repository root. The current directory is
the default application:

```sh
cd /path/to/application
source /path/to/framework/setenv.sh
# Or select another application before sourcing:
export OSE_APP_ROOT=/path/to/application
source /path/to/framework/setenv.sh
ose-env
```

This exports `FRAMEWORK_ROOT`, `SDL_ROOT`, `OSE_ROOT`, `OSE_APP_ROOT`, and
`OSE_PYTHON`, and adds the source runtimes and optional recording/filter modules
to `PYTHONPATH`. `OSE_PYTHON` defaults to `python3`; set it to an interpreter path
before sourcing to use a virtual environment. Sourcing again does not duplicate
the added paths. No dependencies are built or installed when sourcing.
To switch applications in the same terminal, run `unset OSE_APP_ROOT`, enter the
other repository, and source the script again.

With Vibration Watch, run the application and recorder in separate terminals:

```sh
app-run
# In another Bash terminal, source setenv.sh again, then:
record
```

At launch, `record` checks the recording and filter modules with `OSE_PYTHON`.
It automatically installs/builds only missing services, then opens the console.
Later launches reuse them; `--help` skips preparation. The first preparation
uses the existing Makefile targets and may require pip/network access for MCAP,
plus Cargo and a C compiler for the native filter extension. `record-deps`
remains available to manually rebuild/reinstall both services.

`record` uses the application's `build/endpoint.json` when present and accepts all
record CLI options, including `--host`, `--port`, TLS settings, and `--command`.
In the recorder, enter `select alarm.event`, `filter alarm.event machine_id == 2`,
then `start` on separate lines. `start` generates a fresh timestamped filename.
Finish with `stop` and `quit`; use `record-info FILE`, `record-inspect FILE --limit 5`,
or `record-inspect FILE --check` afterwards. Recording paths are relative to the application
directory. Each invocation of `record` opens an independent recorder.
Sessions have the informational label `recorder` by default; use
`record --label moteur-1` (or `-l moteur-1`) to customize it. The console's
`label` command shows or changes it while stopped. Labels appear in capture
status and file metadata, and can be shared by independent sessions.
In the console, `max-size 100MiB` enables numbered file rotation and `cycle 3`
retains only the three latest files, with indices that always increase. Use
`cycle off` and `max-size off` to disable them while stopped.
`record-inspect` displays every selected message unless `--limit N` is supplied.
Use `--summary` for counts by topic, log-time bounds and `duration_seconds`, or `--check` to also
validate selected message bodies. Both modes accept `--topic`, `--from`, `--to`
and `--format text|json|table`; `--limit` only limits message display.
Time bounds are absolute timestamps (`1700000000s`) or offsets from the earliest
recording log time when prefixed with `+` (`+1s`). Use `--duration 4s` instead of
`--to` to select four seconds from `--from`, or from the recording beginning
when `--from` is omitted. Reported `duration_seconds` spans the selected messages
and is zero for an empty selection or a single message.
Options with values also accept `=`, such as `--from=+1s --duration=4s`.
Short example: `record-inspect FILE -cs -b +1s -d 4s -f table`.
`record-check` remains a compatibility shortcut for `inspect --check`, with JSON
output by default.

[`sdl-recording transform`](sdl/docs/recording_transform.md) combines filtering,
concatenation/rebasing and splitting, preserving PCAPNG or MCAP input formats:

```sh
sdl-recording transform capture-*.pcapng --concat --filter 'state == 1' \
  --topic 'alarm.*' --split-size 100MB --output 'alarms-{index:04d}.pcapng'
```

Add `--dry-run` to validate the inputs and preview output files without writing.

`app-build`, `app-validate`, `app-simulate`, `app-distributed`, and `app-make`
forward to the application's Makefile targets; its targets must exist.
`app-run` builds the application with `make build`, then runs its `build/plan.json`
through the framework CLI with no time limit. Stop with Ctrl+C, or use
`app-run DURATION=60` for a bounded run. Other Makefile assignments, such as
`SCENARIO=normal`, are forwarded to the build. This avoids time limits imposed by
an application's `make run` target. `app-distributed` defaults to `DURATION=300`,
with command-line assignments overriding that value. `app-cd` enters the application directory;
`ose-ctl` runs this checkout's CLI with the selected Python interpreter.

Shared parameters have shortcuts that run from `OSE_APP_ROOT`, so the default
plan and endpoint are found even when invoked from another directory:

```sh
params-list
params-get machines/press_1/detection
params-get '/detection$' > detection-export.json
params-get > parameters.json
params-set --file parameters.json
params-set machines/press_1/detection --file detection.json
params-watch machines/press_1/detection --count 3
```

These commands use client `operator` by default. Set `OSE_PARAMETER_CLIENT` or
pass `--client NAME` to choose another identity. When sourcing, the environment
automatically loads that client's token from `build/plan.json`, or, before a
build, the first existing `deployment.local.json` / `deployment.json`. An existing
plan is authoritative even if the client has no token. Set `OSE_PARAMETER_CLIENT`
before sourcing to load another client's token. Explicit `OSE_PARAMETER_TOKEN`
values are preserved, including an empty value; tokens are never printed by the
script. Sourcing again refreshes an automatically loaded token for the selected
application and client. Relative JSON filenames resolve
from the application directory. All parameter CLI options, including `--plan`,
`--endpoint`, `--token-env`, host/port and distributed TLS options, pass through.
`params` shows help; `params ACTION ...` also accepts the ordinary CLI syntax.
`params-get` always exports a JSON object `{path: block}`, including for a single
selection. Its optional positional argument is a Python regular expression that
searches block paths; no argument selects every listed block. Use
`params-get --exact PATH` for a literal path. Quote patterns to avoid shell
expansion. `params-get-all` remains a synonym for `params-get` without a pattern.
Every selected block must be readable. `params-set --file FILE` imports this format
without requiring a separate path or an `--all` option. Supplying an explicit path
to `params-set` still writes a bare block. The ordinary
`ose-ctl parameters get PATH` keeps its value-only output for existing scripts;
use `get --export [PATTERN]` for the same path-keyed pattern export as the shell
shortcuts, or `get --export --exact PATH` to export one literal path.
Writes replace complete typed blocks. See the [parameter guide](ose/docs/parameters.md).

Exports can be sent directly back to the service:

```sh
params-get '/detection$' | params-set --file -
```

All imported blocks are decoded and validated before writing. Reads and writes
are sequential, without an atomic snapshot or transaction across blocks. If a
service write fails, the error lists the blocks already updated. JSON input is
limited to 1 MiB in total, including grouped imports.

Use `--file -` to read JSON from stdin, with the same validation and 1 MiB limit:

```sh
printf '%s\n' '{"enabled":true,"threshold":85.0,"hysteresis":5.0,"window_ms":200}' \
  | params-set machines/press_1/detection --file -
```

Supply every required field of the block; stdin also accepts redirection or a
heredoc.

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
