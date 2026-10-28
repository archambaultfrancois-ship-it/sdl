# Parameter directory sync

OSE provides a foreground daemon that synchronizes shared parameter blocks with
unit JSON files on Linux/Termux. It uses inotify for file changes and existing OSE
subscriptions for application changes. The design assumes one usual writer and
keeps synchronization state in memory, without conflict handling or a database.

## Usage

Install from the OSE project root, then run from the application directory:

```sh
python3 -m pip install '.[sync]'
ose-ctl parameters sync --dir ./parameters --client operator
ose-ctl parameters sync '^machines/press_1/' --dir ./parameters --client operator
```

After sourcing `setenv.sh`, the equivalent shortcut is
`params-sync --dir ./parameters` (or `params sync --dir ./parameters`). It uses
the loaded token and defaults to `operator` or `OSE_PARAMETER_CLIENT`.
The shortcut resolves `--dir` relative to the caller's current directory, while
using the application directory for the default plan and endpoint. For example,
`params-sync --dir .` manages files in the current terminal directory.
The client needs read and write access to the selected blocks. Existing token,
endpoint, SDL runtime and distributed TLS options apply.

| Option | Default | Purpose |
| --- | --- | --- |
| Positional pattern | All listed paths | Select paths by regex search. |
| `--exact PATH` | Disabled | Select one literal path instead of a regex. |
| `--dir DIR` | Required | JSON tree root; created if missing. |
| `--initial files\|app` | `files` | Source used on every connection, including reconnects. |
| `--debounce SECONDS` | `0.15` | Group file events for the same path. |
| `--max-json-size BYTES` | `16777216` | Maximum JSON file size: 16 MiB. |

## File layout

Each parameter path maps directly to a relative filename:

```text
machines/press_1/detection
  -> parameters/machines/press_1/detection.json
```

The file contains exactly one complete block in the existing export format:

```json
{
  "machines/press_1/detection": {
    "enabled": true,
    "threshold": 85.0,
    "hysteresis": 5.0,
    "window_ms": 200
  }
}
```

The embedded path must match the filename without `.json`. Only regular lowercase
`*.json` files are managed; symlinks are not followed. Unknown selected paths are
reported and ignored, and files outside the selection are left alone. The daemon
does not declare new OSE parameters. Files remain usable with `parameters set
--file FILE` or the existing directory importer.

## Synchronization rules

| Situation | Action |
| --- | --- |
| Connect with `--initial files` | Publish valid existing files; create missing files from OSE. |
| Connect with `--initial app` | Replace selected files with current OSE values. |
| File event with a different mtime | Read, validate and publish the complete block. |
| Application notification with a different value | Replace the corresponding file. |
| Managed file deleted | Recreate it from OSE. |
| Invalid JSON, SDL value or embedded path | Preserve the file until corrected; other paths continue. |
| Connection lost | Reconnect after 2 seconds and reapply the initial policy. |

A pending local edit takes priority over an application notification. Concurrent
writers are otherwise outside the design assumptions.

The daemon remembers each successfully processed `st_mtime_ns` and normalized SDL
value. File dates are compared for inequality, so an older but different date
also triggers publication. Recording the new mtime after an export suppresses
its inotify echo; comparing decoded values suppresses notifications of the
daemon's own `set`. An edit retaining the exact previous mtime may be ignored;
`touch FILE` forces publication.

## Event handling and recovery

- Directory watches handle writes, atomic saves by rename, timestamps and
  deletions. New or moved-in subdirectories are watched and scanned for JSONs.
- Events are grouped per path, then processed serially in an asyncio loop.
- One control connection performs reads/writes. Separate subscription connections
  each watch up to 256 paths, reusing prepared block codecs.
- Subscriptions provide current values on registration and later updates. Their
  existing 250 ms long polls remain; there is no cyclic `get` of each block.
- Tree scans run at startup, on directory changes and after inotify queue
  overflow. There is no periodic full-tree scan or polling fallback.
- Transient file errors schedule retries. File exports use a `.tmp` file followed
  by `os.replace`; logs go to stderr without values or tokens.
- SIGINT/SIGTERM close the connections and filesystem watches. An external
  supervisor can restart the process.

## Limits

| Scope | Default |
| --- | --- |
| JSON file or stdin input | 16 MiB, configurable with `--max-json-size`. |
| Total JSON for `parameters set --dir` | 64 MiB, configurable with `--max-total-json-size`. |
| Directory sync total | No aggregate import cap; blocks are processed separately. |

UTF-8, duplicate-key rejection and SDL validation remain mandatory. Increasing
JSON limits does not increase the existing SDL transport/CH/1 frame limits or
the service's 64 MiB encoded-value budget.

Files are the only persistent sync state. There are no revisions, transactions,
backups or conflict journals. Synchronization confirms service storage, not
consumption by every component, and does not guarantee restoration before
component `on_start`.

## Implementation and validation

[sync.py](../src/ose/parameters/sync.py) owns initialization, event processing,
caching and reconnects; [sync_fs.py](../src/ose/parameters/sync_fs.py) adapts
`inotify-simple` to asyncio. [files.py](../src/ose/parameters/files.py) shares
bounded JSON parsing with [cli.py](../src/ose/parameters/cli.py). The parameter
service, protocol, SDKs and application code require no changes.

[Unit tests](../tests/unit/test_parameter_sync.py) and
[integration tests](../tests/integration/test_parameter_sync.py) cover mtime
handling, validation, echo suppression, rename saves, new subdirectories, overflow
recovery, reconnects, SIGTERM and subscription grouping beyond 256 paths.
See [shared parameter documentation](parameters.md) for the complete CLI reference.
