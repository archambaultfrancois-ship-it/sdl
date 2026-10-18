# Transform recording series

`sdl-recording transform` combines **validation → filtering → optional
concatenation/rebasing → splitting → publication** in one command, without an OSE
model or generated message classes. It preserves the input container format.

```sh
sdl-recording transform capture-*.pcapng \
  --concat --timeline preserve \
  --topic 'alarm.*' --filter 'state == 1' \
  --split-duration 10m --split-size 100MB \
  --output 'result-{index:04d}.pcapng'
```

From the SDL directory, install `make recording-deps` and set
`PYTHONPATH=build/recording-deps:runtime/python`, then invoke
`python3 tools/recording/sdl-recording`. The shared framework `setenv.sh` exposes
the command directly. Content predicates additionally require `make filter-python`
with the same interpreter. The CLI locates the checkout's filter modules lazily;
other transformations do not require that service.

## Inputs and filenames

Inputs are processed in command-line order; the shell expands unquoted globs.
Without `--concat`, each input is treated independently. With `--concat`, all
inputs form one logical stream and must have the same container format. Use
`convert` first to normalize mixed formats. Detection uses file magic, so filename
extensions never select the output format.

`--output` accepts a literal path for a single output, or a filename pattern:

| Field | Meaning |
| --- | --- |
| `{index}` / `{index:04d}` | Global output index, starting at one, across all inputs. |
| `{source}` | Filename stem of the first source in the current logical stream. |
| `{format}` | Actual container: `pcapng` or `mcap`. |

The parent directory must exist. Fields belong in the filename; splitting requires
`{index}`. Duplicate output names are errors. Separate processing supports mixed
formats, for example:

```sh
sdl-recording transform a.pcapng b.mcap \
  --split-messages 1000 --output 'out/{source}-{index:04d}.{format}'
```

Each logical stream produces at least one file, including empty selections.
Channels/catalogues are declared when used; unused declarations are omitted.
Empty outputs still contain application metadata and transformation provenance.

## Filtering

Repeat `--topic GLOB` or `--type GLOB` for alternative topic/root type names.
Each category uses OR; topic and type selections combine with AND.
Repeated `--filter EXPRESSION` predicates combine with AND and use the existing
[SDL filter language](filtering.md), evaluated on the original wire payload.
Indeterminate results are skipped and counted; compilation/evaluation errors
abort. Expressions must be valid for every selected channel, including unused
channels. Select appropriate topics/types before applying field-specific filters.

`--from TIME` is inclusive; `--to TIME` is exclusive. Prefix a bound with `+` for
an offset from the earliest log time of each **whole input**, before other filters.
`--duration DURATION` sets the end to the resolved start plus that duration, or
to each input's origin plus duration when `--from` is absent. It excludes `--to`.
Selections use original log times before rebasing. Zero duration selects nothing.

Time values accept seconds without a suffix, or `ns`, `us`, `ms`, `s`, `m`, `h`.
Fractions must resolve to whole uint64 nanoseconds. All input payloads are fully
validated, including messages excluded by filters.

## Timeline

`--timeline preserve` is the default: timestamps and gaps remain unchanged.
Overlapping or backwards input ranges are reported, while records remain in file
order. There is no sort or message deduplication. SDL paced replay requires
nondecreasing log times; OSE replay orders events by log time.

`--concat --timeline append` keeps the first nonempty selected input's origin.
Each next nonempty input receives a constant offset so its earliest retained
message starts at the previous assembled maximum plus **1 ns + gap**. Empty
selected inputs add no gap. Append uses the selected bounds, after filtering.
Offsets may be negative, but resulting times must fit uint64.

```sh
sdl-recording transform normal.pcapng fault.pcapng \
  --concat --timeline append --gap 2s --output scenario.pcapng
```

`--gap` defaults to zero and requires append concatenation. Only `log_time_ns`
changes. Publication/acquisition times, sequences, message bytes and timestamps
inside messages remain intact. Identical channels (topic, exact catalogue, root,
all metadata) are reused; revisions remain distinct. There is no schema conversion.

Original metadata records are preserved, including repeated names. Each output
adds `sdl.transform` provenance: source paths/indices, offsets, selection, timeline
and output index. Earlier provenance is preserved when transforming again.

## Splitting and size limits

Limits apply after filtering/rebasing, across source boundaries for concatenation.
The first reached starts a new output before the next whole message:

- `--split-messages N`: at most N messages.
- `--split-duration DURATION`: each nonempty output's maximum minus minimum log
  time is strictly below the duration; a message on the boundary starts a new file.
  This also bounds out-of-order times. Gaps do not create empty files.
- `--split-size SIZE`: maximum complete file bytes, including declarations,
  metadata, indexes and final checks. Units B/KB/MB/GB use factors of 1000.

Limits must be positive and can be combined. Every output is independently
validated; MCAP retains uncompressed chunks and indexes. If metadata plus one
message cannot fit the size limit, the command fails without publishing outputs.

## Dry runs, errors and reports

`--dry-run` validates/filter/serializes to a byte counter and reports exact output
names, sizes, counts and time bounds, without creating files or directories.

Inputs cannot also be outputs, including aliases through symlinks/hard links.
Existing outputs require `--overwrite`; symlink outputs are always rejected.
Keep sources unchanged: detected changes during processing abort.

Real runs stage all outputs in the destination directory, validate them, then
publish. Existing outputs are backed up before replacement. Publication errors
roll back this command's changes. If rollback itself fails, the error identifies
the staging directory retained for recovery. A series is not a single filesystem
transaction: completed files appear individually. Without hard-link support, new
names are briefly reserved by empty files before replacement. This is error
rollback, not recovery of an entire batch after a crash or power loss.

Success emits one JSON report: per-input read/selected/rejection counts
(selection, time, filter, indeterminate), offsets, per-output format, bytes, topic
counts and bounds, totals and overlap warnings. Errors use stderr and status 1;
invalid CLI syntax uses 2. Empty selections and overlap warnings are successful.
Catalogues, payloads and application metadata are preserved; indexes are rebuilt.
Attachments and unknown MCAP extensions are not copied.
