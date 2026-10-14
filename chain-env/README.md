# SDL Chain

Executable component graphs with SDL2 contracts and per-subscription quality of
service. This directory is a standalone project designed for extraction into a
separate repository. By default, it uses the enclosing SDL checkout; an explicit
path supports a separate dependency checkout.

Requirements: Python 3.10+, a C99 compiler, Rust/Cargo, and a compatible SDL
checkout. The Python engine uses only the standard library. All delivery queues,
catalogues, retry state and deduplication history live in bounded RAM structures.
The dependency revision and repository are recorded in `sdl-dependency.json`.
Development accepts an explicit compatible checkout; CI should use the pinned
revision. Neither building nor running automatically downloads SDL.

## Quick start

From this project's root inside an SDL checkout:

```sh
make validate
make demo
make test
```

`SDL_ROOT` defaults to the parent of this project (`..`). To use another checkout,
set `SDL_ROOT=/absolute/path/to/sdl` in the environment or on the `make` command
line. The CLI also accepts `--sdl-root`, which takes precedence over `SDL_ROOT`.
For a separately extracted or installed project, set the dependency path explicitly.

The example runs a C measurement source, a Rust transformation doubling values,
and two Python observers. All four use generated containers and user callbacks;
the Python observers share their callback implementation. The transformation consumes a TCP FIFO with receipt
ACKs and retries. An observer uses a RAM UDP FIFO with receipt ACKs
and retries. A slower display reads the latest UDP state without retries.

Results are printed as JSON lines to stderr; the example does not write message
files. The display can skip intermediate states. Restarting discards pending
messages and the last state. Each broker run has a fresh identity, so a restarted
source may restart its own sequence without colliding with prior message IDs.
Receipt ACKs confirm admission in RAM, not survival of a crash or power loss.

You can also install the CLI with `python3 -m pip install .`. Without installing:

```sh
export PYTHONPATH="$PWD/src"
python3 -m chain_env.cli validate --model examples/acquisition/model.json \
  --deployment examples/acquisition/deployment.json
python3 -m chain_env.cli build --model examples/acquisition/model.json \
  --deployment examples/acquisition/deployment.json
python3 -m chain_env.cli run --plan build/plan.json
```

In another terminal, with the same environment:

```sh
python3 -m chain_env.cli inspect
```

Ctrl+C stops the chain and its component processes. `run --duration 2` provides
a finite execution; `inspect --last` reads the final saved counters after shutdown.
Counters include queue depths, age, replacement, refusal, duplicate, retry and
failure counts, as well as observed average publication and processing rates.

## Model and SDK

See [architecture](docs/architecture.md) for document fields and process layout,
[delivery](docs/delivery.md) for FIFO/latest semantics and transport guarantees,
and [component protocol](sdk/protocol.md) for the Python, C and Rust SDK boundary.
The example documents are complete runnable model/deployment templates.

Components can provide only user callbacks: declare
`"container": {"language": "python", "handler": "components/analyse/logic.py"}`
in their deployment entry. Use `language: "c"` with a `.c` handler or
`language: "rust"` with a `.rs` handler for native containers. The builder generates
the executable wrapper, native compilation commands, typed decoding and
publication methods, and creates a handler skeleton only
if the file is missing. See [callback containers](docs/callbacks.md) for the API,
snapshot callbacks, lifecycle hooks and regeneration rules.

The public Python component entry point is
`from chain_env.execution.component import ComponentSession`.
The C stdio SDK is compiled from `sdk/c`; its optional callback runner uses SDL.
The dependency-free Rust stdio crate lives in
`sdk/rust`. They expose the same binary lifecycle while generated SDL bindings
perform the actual message encoding and decoding.

## Tests and extraction

`make test-unit` needs only Python. `make test-sdk` runs the Rust SDK checks.
`make test-integration` uses sockets, injects
packet/ACK loss, tests RAM bounds, volatility and schema compatibility, and copies this
entire project into a temporary standalone directory before building and running
the C → Rust → Python chain. Integration tests require SDL_ROOT; invoking them
directly without it skips SDL-dependent tests.

Backpressure regressions also cover 1100 queued TCP messages with a delayed
catalogue reply, independent input admission and sender retries, per-port schema
checks, complete snapshot reservation and bounded shutdown of a non-reading child.
Delivery-limit regressions cover a real blocked TCP reader, outgoing packet
size checks, a 20-second admission wait and status/metrics exceeding 64 KiB.
Generated-container tests cover typed publication, complete snapshots, optional
inputs, schema evolution, initialization failures and preservation of user code.
Native-container tests compile and run C/Rust sources, named callbacks and
snapshots, check cleanup after failures and Rust panics, and test initial scaffolds.
RAM tests verify shared binary payloads, bounded retry indexes, wakeups and loss
of queued state on restart. Delivery has no SQLite dependency or file I/O.

Legacy `durability: "memory"` declarations are accepted and omitted from new
resolved policies. `durability: "disk"` is rejected explicitly: there is no persistent
backend and old database files are not used. Rebuild previous plans before running.
Build artefacts, `endpoint.json` and `last-status.json` remain diagnostic files;
they do not store messages or restore delivery state.

To extract, copy this project root into the new repository, retaining its SDKs,
examples, tests and dependency lock. The nested `.github/workflows` directory
becomes active when this directory becomes the repository root. No source paths
need changing; build plans are regenerated for the new checkout location.

The [Python, C and Rust external client SDKs](docs/clients.md) support publishing
and subscribing to the broker without a client-side model. The application model
can also declare `external_topics` and connect a topic to an input using
`from_topic`. See the examples in `examples/external/`.
