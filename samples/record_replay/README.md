# Record and replay radio captures

This Python demo reuses the [radio capture schema](../radio_capture/schema/radio_capture.sdl).
Ten synthetic frames contain 4096 complex IQ samples each. Every fifth frame
contains an interference burst. The live path and replay call the same
`pipeline.on_record(record)` function with the same encoded SDL2 bytes.

From the repository root:

```sh
make -C samples/record_replay demo
# Or explicitly select MCAP:
make -C samples/record_replay demo RECORDING_FORMAT=mcap
```

The demo checks for the pinned optional `mcap` dependency and installs it in
`build/recording-deps` when missing. The first run requires pip and network access
or a cached wheel; subsequent runs reuse the installation. Override `RECORDING_DEPS`
to choose another dependency directory, or run `make recording-deps` beforehand.

The demo creates `build/radio.pcapng` by default, prints file information and a bounded
structured display of the burst at 300 ms, validates the entire file, and replays without
waiting. It compares `build/live.log` with `build/replayed.log` byte for byte.
It then verifies topic and time filtering (300 ms included, 800 ms excluded),
an unknown topic and an empty time range. The optional catalogue filter service
is built with `make filter-python` and activated by `main.py --filter-log`:

```text
metadata.receiver_id == "demo-rx-01" && exists(operator_note) &&
any(iq_samples, s => real(s) * real(s) + imag(s) * imag(s) > 4)
```

The predicate checks nested metadata, an optional field and the power of complex
IQ samples. Live evaluation uses generated objects; replay evaluation reads the
recorded SDL2 payloads directly. Both must match sequences 3 and 8 and reject the
other eight captures. All ten captures remain in the recording and the full logs.
The demo compares `build/live-filtered.log` and `build/replayed-filtered.log`
byte for byte. This step needs Cargo, a C compiler and Python development headers;
see [catalogue filtering](../../docs/filtering.md).

Analysis logs use the runtime `display()` style, with one indented block per capture:

```text
CaptureAnalysis {
   sequence: 0
   captured_at_ns: 1791724536610867471
   sample_count: 4096
   peak_power: 0.25
   burst_detected: false
}
```

The recording contains its catalogue; decoding it does not need generated classes.
With `RECORDING_FORMAT=mcap`, the same workflow creates `build/radio.mcap`.
The application writer accepts `--format mcap|pcapng`, defaulting to PCAPNG;
readers detect the container.

For a structured display of one recorded capture:

```sh
PYTHONPATH=build/recording-deps \
  python3 tools/recording/sdl-recording inspect samples/record_replay/build/radio.pcapng \
  --format text --topic radio/iq --from 300ms --limit 1
```

This command runs from the repository root. It shows recording timestamps,
nested messages and the first eight IQ samples; add `--full` to show every sample.
Use `--indent-width 2` to change indentation or `--format table` for field/type/value columns.

To replay at half speed:

```sh
PYTHONPATH=build/recording-deps:samples/record_replay \
  python3 tools/recording/sdl-recording replay samples/record_replay/build/radio.pcapng \
  --speed 0.5 --consumer pipeline:on_record
```

Replace `acquire_captures()` with the application's acquisition source, keeping
its timestamps in `CaptureMetadata`. The demo synthesizes acquisition and log
times 100 ms apart without waiting during recording. A real recorder can omit
`log_time_ns` to record wall-clock arrival time while retaining acquisition time
in `publish_time_ns` and the SDL message. The analysis uses recorded acquisition
time, so its result does not depend on replay speed or the current wall clock.

See [the profile and API reference](../../docs/recording.md) for schema evolution,
durability, validation and replay semantics.
