"""Interactive and scriptable recorder console; all commands stay outside models."""
import asyncio
from datetime import datetime, timezone
import fnmatch
import json
import math
from pathlib import Path
import shlex
import sys

from ose.recording.recorder import Recorder
from ose.recording.command_input import CommandInput
from ose.recording.rotation import parse_size
from ose.transport.secure import contexts


HELP = """list [PATTERN]                discover/refresh recording points
reconnect                     reconnect after an application/coordinator restart
select POINT_OR_PATTERN ...    select by point name, message type, number, or glob
select all                    select all discovered recording points
unselect POINT_OR_PATTERN ...  same matching rules; all clears the selection
selected                      show the selection and its filters
label [NAME]                  show/set the session label (default: recorder)
max-size [SIZE|off]            rotate at SIZE bytes (B/KB/MB/GB; default: off)
cycle [N|off]                 keep N files with increasing indices (default: off)
show POINT                    show its SDL catalogue and applicable field types
filter POINT EXPRESSION        prepare an SDL filter for a selected point
filter POINT off               remove its filter
start [PATH] [pcapng|mcap]      start a new capture with the current file settings
status                        show a text summary of files, counts, losses and errors
stop                          stop capture, drain queues and finalize the file
wait SECONDS                  keep recording (also useful in scripts)
help
quit                          stop cleanly and exit

Examples: select sensor_*.signal alarm.event
          select Signal* Analys?s
          select all
          filter alarm.event state == 1 && rms > 1.0
          max-size 100MB
          cycle 3
          start capture.pcapng
Only matching messages are written. Indeterminate results are counted and skipped.
Filters run on original encoded values at the source, before capture queueing and transfer.
Size units: KB/MB/GB use powers of 1000.
"""


class Console:
    def __init__(self, recorder, output=None):
        self.recorder = recorder
        self.output = output or sys.stdout
        self.failed = False

    def print(self, value):
        print(value, file=self.output, flush=True)

    def names(self, patterns, *, unselect=False, match_types=False):
        names = list(sorted(self.recorder.points))
        found = []
        for pattern in patterns:
            if pattern.isdigit():
                number = int(pattern)
                matches = [names[number - 1]] if 1 <= number <= len(names) else []
            else:
                candidates = sorted(self.recorder.selected) if unselect else names
                matches = [name for name in candidates
                           if fnmatch.fnmatchcase(name, pattern) or
                           (match_types and fnmatch.fnmatchcase(
                               self.recorder.points.get(name, {}).get("root", ""), pattern))]
            if not matches:
                raise ValueError(f"no recording point matches {pattern!r}")
            found.extend(matches)
        return sorted(set(found))

    def print_status(self, status):
        max_size, cycle = status["max_size"], status["cycle"]
        lines = [f"State: {status['state']}", f"Label: {status['label']}",
                 f"Max size: {str(max_size) + ' B' if max_size is not None else 'off'}",
                 f"Cycle: {str(cycle) + ' files' if cycle is not None else 'off'}"]
        if "file" in status:
            lines.extend([f"File: {status['file']}", f"Format: {status['format']}",
                          f"Base file: {status['base_file']}",
                          f"File index: {status['file_index']}",
                          f"Files retained: {status['file_count']}",
                          f"Rotations: {status['rotations']}",
                          f"Messages in current file: {status['file_message_count']}"])
            for title, field, defaults in (
                ("Messages", "counts", ("received", "written", "filtered", "indeterminate", "filter_errors")),
                ("Source capture", "capture_counts", ("observed", "delivered", "filtered", "indeterminate",
                                                       "filter_errors", "dropped", "oversize")),
            ):
                counts = status[field]
                lines.append(f"{title}:")
                for key in dict.fromkeys((*defaults, *sorted(counts))):
                    lines.append(f"  {key.replace('_', ' ').capitalize()}: {counts.get(key, 0)}")
        selected = status["selected"]
        lines.append(f"Selected points ({len(selected)}):")
        if not selected:
            lines.append("  none")
        for name, expression in sorted(selected.items()):
            lines.append(f"  {name} — filter: {expression or 'off'}")
            if "file" in status:
                lines.append(f"    Written: {status['topics'].get(name, 0)}; "
                             f"in current file: {status['file_topics'].get(name, 0)}")
        lines.append(f"Error: {status.get('error') or 'none'}")
        self.print("\n".join(lines))

    def completed(self, status):
        self.print_status(status)
        if status["error"]:
            self.failed = True

    async def execute(self, line):
        parts = line.strip().split(None, 1)
        command, rest = (parts[0], parts[1] if len(parts) == 2 else "") if parts else ("", "")
        if not command or command.startswith("#"):
            return True
        if command == "reconnect":
            await self.recorder.reconnect()
            await self.execute("list")
        elif command in ("list", "refresh"):
            await self.recorder.refresh()
            pattern = rest.strip() or "*"
            for number, (name, point) in enumerate(sorted(self.recorder.points.items()), 1):
                if fnmatch.fnmatchcase(name, pattern):
                    mark = "*" if name in self.recorder.selected else " "
                    state = "available" if point["available"] else "unavailable"
                    self.print(f"{number:3} {mark} {name}  {point['root']}  {state}")
        elif command in ("select", "unselect"):
            self.recorder.editable()
            patterns = shlex.split(rest)
            if not patterns:
                raise ValueError(f"{command} requires a point, message type, list number, pattern or all")
            patterns = ["*" if pattern == "all" else pattern for pattern in patterns]
            for name in self.names(patterns, unselect=command == "unselect", match_types=True):
                if command == "select":
                    self.recorder.selected.setdefault(name, None)
                else:
                    self.recorder.selected.pop(name, None)
            self.print(json.dumps(self.recorder.selected, sort_keys=True))
        elif command == "selected":
            self.print(json.dumps(self.recorder.selected, sort_keys=True))
        elif command == "label":
            values = shlex.split(rest)
            if len(values) > 1:
                raise ValueError('label [NAME]; quote names containing spaces')
            if values:
                self.recorder.set_label(values[0])
            self.print(json.dumps({"label": self.recorder.label}, ensure_ascii=False))
        elif command in ("max-size", "cycle"):
            values = shlex.split(rest)
            if len(values) > 1:
                raise ValueError(f"{command} expects one value or off")
            if values:
                if command == "max-size":
                    self.recorder.set_max_size(parse_size(values[0]))
                else:
                    self.recorder.set_cycle(None if values[0].lower() == "off" else int(values[0]))
            value = self.recorder.max_size if command == "max-size" else self.recorder.cycle
            self.print(json.dumps({command: value if value is not None else "off"}))
        elif command == "show":
            names = self.names([rest.strip()])
            for name in names:
                desc = await self.recorder.describe(name)
                self.print(f"{name}: {desc['root']}\n{desc['catalogue']}")
        elif command == "filter":
            point, separator, expression = rest.strip().partition(" ")
            if not separator or not expression.strip():
                raise ValueError("filter requires POINT EXPRESSION (or POINT off)")
            names = self.names([point])
            if len(names) != 1:
                raise ValueError("filter requires exactly one point")
            await self.recorder.set_filter(names[0], None if expression.strip() == "off" else expression)
            self.print(json.dumps(self.recorder.selected, sort_keys=True))
        elif command == "start":
            values = shlex.split(rest)
            if len(values) > 2:
                raise ValueError("start [PATH] [pcapng|mcap]")
            path = values[0] if values else datetime.now(timezone.utc).strftime("recording-%Y%m%dT%H%M%S-%f.pcapng")
            format = values[1] if len(values) == 2 else "pcapng"
            if format not in ("pcapng", "mcap"):
                raise ValueError("format must be pcapng or mcap")
            await self.recorder.start(path, format=format, finished=self.completed)
            self.failed = False
            self.print(f"Recording to {self.recorder.capture.path}")
        elif command == "status":
            self.print_status(self.recorder.status())
        elif command == "stop":
            await self.recorder.stop()
        elif command == "wait":
            seconds = float(rest)
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("wait requires finite nonnegative seconds")
            await asyncio.sleep(seconds)
        elif command in ("quit", "exit"):
            return False
        elif command in ("help", "?"):
            self.print(HELP)
        else:
            raise ValueError(f"unknown command: {command}; type help")
        return True


async def run(args):
    from ose.codec.sdl2 import SDL2
    from ose.codec.runtime import Runtime
    sdl_root = args.sdl_root
    if not sdl_root:
        try:
            Runtime()
        except ImportError:
            sdl_root = Path(__file__).resolve().parents[4] / "sdl"
            if not sdl_root.is_dir():
                raise ValueError("make the SDL Python runtime importable or pass --sdl-root")
    if sdl_root:
        root = SDL2(sdl_root).root
        # Discover optional services built in this checkout without importing them.
        # Recording without filters still works when the filter library is absent.
        for relative in ("build/recording-deps", "runtime/filter/python", "build/filter/release"):
            directory = root / relative
            if directory.is_dir():
                sys.path.insert(0, str(directory))
    if not 1 <= args.capacity <= 4096 or not 1024 <= args.byte_budget <= 64 * 1024 * 1024:
        raise ValueError("capacity must be 1..4096 and byte budget 1024..67108864")
    if args.endpoint and (args.host or args.port):
        raise ValueError("choose --endpoint or --host/--port")
    host, port = args.host or "127.0.0.1", args.port or 7000
    if args.endpoint or (not args.host and not args.port and Path("build/endpoint.json").is_file()):
        endpoint = json.loads(Path(args.endpoint or "build/endpoint.json").read_text())
        host, port = endpoint["host"], endpoint["port"]
    distributed = args.distributed or args.insecure or any((args.tls_ca, args.tls_cert, args.tls_key))
    context = None
    if distributed:
        _, context = contexts(args.tls_ca, args.tls_cert, args.tls_key, insecure=args.insecure, hosts=[host])
    recorder = Recorder(host, port, context=context, distributed=distributed,
                        capacity=args.capacity, byte_budget=args.byte_budget, max_frame=args.max_frame,
                        label=args.label)
    console = Console(recorder)
    commands = CommandInput()
    try:
        await recorder.connect()
        console.print("OSE recorder — type help; * marks a selected point")
        await console.execute("list")
        if args.command:
            for command in args.command:
                if not await console.execute(command):
                    break
        else:
            while True:
                line = await commands.readline()
                if not line:
                    break
                try:
                    if not await console.execute(line):
                        break
                except (ValueError, OSError, RuntimeError) as error:
                    console.failed = True
                    console.print(f"Error: {error}")
    finally:
        commands.close()
        from ose.transport.secure import finish_cleanup
        await finish_cleanup(recorder.close())
    return 1 if console.failed or (recorder.capture and recorder.capture.error) else 0
