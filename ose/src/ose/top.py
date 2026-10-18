"""Read-only live diagnostics for local brokers and distributed coordinators."""
import asyncio
from collections import deque
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import ssl
import sys
import termios
import time
import tty

from ose.transport.secure import connect, contexts, finish_cleanup


COUNTERS = ("accepted", "refused", "dropped_oldest", "failed", "retries", "replaced")
COLORS = {"ok": "32", "warning": "33", "error": "31", "waiting": "36", "idle": "2"}


def color_enabled(mode, output, environ=None):
    environ = os.environ if environ is None else environ
    if mode != "auto":
        return mode == "always"
    return output.isatty() and "NO_COLOR" not in environ and environ.get("TERM") != "dumb"


def safe_text(value):
    # Names and remote errors must never inject terminal control sequences.
    return "".join(char if char.isprintable() else "?" for char in str(value))


class Observer:
    def __init__(self, window=5):
        self.window = window
        self.histories = {}
        self.epoch = None

    def sample(self, snapshot, now=None, error=None):
        now = time.monotonic() if now is None else now
        if snapshot.get("epoch") != self.epoch:
            self.histories.clear()
            self.epoch = snapshot.get("epoch")
        observation = snapshot.get("observation", {})
        inventory = observation.get("components", {})
        if observation.get("version") != 1:
            raise ValueError("live diagnostics require an updated OSE broker/coordinator")
        rows = []
        for name, desc in sorted(inventory.items()):
            raw = snapshot.get("components", {}).get(name, {})
            metrics = raw.get("metrics", raw)
            queues = []
            for port, values in metrics.get("inputs", {}).items():
                sub = next((sub for sub in observation["subscriptions"].values()
                            if sub["to"] == f"{name}.{port}"), {})
                queues.append({"name": port, "scope": "input", "policy": sub.get("policy", {}), **values})
            for identity, sub in observation["subscriptions"].items():
                if sub["from"].split(".")[0] != name:
                    continue
                values = (metrics.get("direct", {}) if sub.get("routing") == "direct"
                          else snapshot.get("subscriptions", {})).get(identity)
                if values is not None:
                    queues.append({"name": identity, "scope": "delivery", "policy": sub["policy"], **values})
            counters = {"in": sum(queue.get("accepted", 0) for queue in queues if queue["scope"] == "input"),
                        "out": metrics.get("publications", 0),
                        "ticks": metrics.get("completed_ticks", metrics.get("ticks", 0))}
            for key in ("invalid", "network_refused", "unavailable", "refused", "delivery_refused"):
                counters[key] = metrics.get(key, 0)
            for queue in queues:
                for key in COUNTERS:
                    counters[f"{queue['scope']}:{queue['name']}:{key}"] = queue.get(key, 0)
            generation, history = self.histories.get(name, (None, deque(maxlen=1024)))
            if generation != desc.get("generation"):
                history = deque(maxlen=1024)
            self.histories[name] = desc.get("generation"), history
            fresh = desc.get("report_age_ms") is not None and desc["report_age_ms"] <= 3000
            available = desc.get("connected") and fresh and not error
            if not available:
                history.clear()
            elif history and any(value < history[-1][1].get(key, 0) for key, value in counters.items()):
                history.clear()
            if available:
                history.append((now, counters))
                while len(history) > 2 and history[1][0] <= now - self.window:
                    history.popleft()
            elapsed = now - history[0][0] if len(history) > 1 else 0
            delta = {key: max(0, value - history[0][1].get(key, value)) if elapsed else 0
                     for key, value in counters.items()}
            alerts = []
            def alert(severity, message):
                alerts.append({"severity": severity, "message": message})
            for queue in queues:
                prefix = f"{queue['scope']}:{queue['name']}:"
                queue["recent"] = {key: delta[prefix + key] for key in COUNTERS}
                label = f"{queue['scope']} {queue['name']}"
                losses = queue["recent"]["dropped_oldest"] + queue["recent"]["failed"]
                if losses:
                    alert("error", f"{label}: {losses} dropped/failed")
                if queue["recent"]["refused"]:
                    alert("warning", f"{label}: {queue['recent']['refused']} admission refusals (attempts)")
                if queue["recent"]["retries"]:
                    alert("warning", f"{label}: {queue['recent']['retries']} retries")
                policy = queue["policy"]
                if policy.get("buffer") == "fifo" and queue.get("depth", 0) >= .8 * policy.get("capacity", float("inf")):
                    alert("warning", f"{label}: queue {queue['depth']}/{policy['capacity']}")
            for key in ("invalid", "network_refused", "unavailable", "refused", "delivery_refused"):
                if delta[key]:
                    alert("warning" if key in ("refused", "delivery_refused") else "error", f"{key}: +{delta[key]}")
            p95 = metrics.get("tick_p95_ms") if fresh and not error else None
            period = desc["trigger"].get("period_ms")
            if period and p95 is not None and p95 > period:
                alert("warning", f"tick p95 {p95:.1f} ms exceeds period {period} ms")
            busy = metrics.get("busy_ms") if available else None
            if period and busy is not None and busy > period:
                alert("warning", f"tick in progress {busy:.1f} ms exceeds period {period} ms (including delivery waits)")
            waiting = metrics.get("waiting_inputs", []) if available else []
            if error:
                state, severity = "UNAVAILABLE", "error"
            elif not desc.get("connected"):
                state, severity = ("UNAVAILABLE", "error") if snapshot.get("enabled") else ("STARTING", "idle")
            elif not desc.get("ready") or not metrics.get("running"):
                state, severity = "STARTING", "idle"
            elif not fresh:
                state, severity = "STALE", "error"
            elif any(item["severity"] == "error" for item in alerts):
                state, severity = "LOSS/ERROR", "error"
            elif alerts:
                state, severity = "ATTENTION", "warning"
            elif waiting:
                state, severity = "WAIT INPUT", "waiting"
            elif delta["ticks"] or metrics.get("busy_ms") is not None:
                state, severity = "ACTIVE", "ok"
            else:
                state, severity = "IDLE", "idle"
            rows.append({"name": name, "state": state, "severity": severity,
                         "rates": {key: delta[key] / elapsed if elapsed and available else None
                                   for key in ("in", "out", "ticks")},
                         "rate_span_seconds": elapsed,
                         "tick_p95_ms": p95, "busy_ms": busy,
                         "report_age_ms": desc.get("report_age_ms"), "waiting_inputs": waiting,
                         "generation": desc.get("generation"), "trigger": desc["trigger"],
                         "queues": queues, "alerts": alerts,
                         "counters": {key: value for key, value in counters.items() if ":" not in key}})
        return {"timestamp": datetime.now(timezone.utc).isoformat(), "epoch": self.epoch,
                "enabled": snapshot.get("enabled", False), "window_seconds": self.window,
                "error": error, "components": rows,
                "broker_errors": {"udp_errors": snapshot.get("udp_errors", 0),
                                  "errors": snapshot.get("errors", [])}}


def render(report, selected=0, details=False, frozen=False, color=False, width=120, height=None, interactive=True):
    def paint(text, severity):
        return f"\033[{COLORS[severity]}m{text}\033[0m" if color else text
    def number(value):
        return "-" if value is None else f"{value:.1f}"
    lines = [f"OSE live | {'FROZEN' if frozen else 'LIVE'} | rates over {report['window_seconds']:g}s | {report['timestamp']}"]
    if report["error"]:
        lines.append(paint("UNAVAILABLE: " + safe_text(report["error"]), "error"))
    errors = report["broker_errors"]
    if errors["udp_errors"] or errors["errors"]:
        lines.append(paint(f"Broker error counters (since start): {safe_text(errors)}", "error"))
    name_width = max(12, min(32, width - 64))
    lines.append(f"  {'COMPONENT':<{name_width}} {'STATE':<12} {'IN/s':>7} {'OUT/s':>7} {'TICK/s':>7} {'FIFO':>9} {'LOSS':>5} {'P95 ms':>8}")
    rows = report["components"]
    selected = min(selected, max(0, len(rows) - 1))
    # Keep the selected row visible even when the graph exceeds terminal height.
    limit = max(1, height - len(lines) - (10 if details else 5)) if height else len(rows)
    start = max(0, min(selected - limit + 1, len(rows) - limit))
    for index in range(start, min(len(rows), start + limit)):
        row = rows[index]
        name = safe_text(row["name"])[:name_width]
        text = f"{'>' if index == selected else ' '} {name:<{name_width}} {row['state']:<12}"
        text += "".join(f" {number(row['rates'][key]):>7}" for key in ("in", "out", "ticks"))
        fifo = [queue for queue in row["queues"] if queue["policy"].get("buffer") == "fifo"]
        fullest = max(fifo, key=lambda queue: queue.get("depth", 0) / queue["policy"]["capacity"], default=None)
        depth = f"{fullest.get('depth', 0)}/{fullest['policy']['capacity']}" if fullest else "-"
        losses = sum(queue["recent"]["dropped_oldest"] + queue["recent"]["failed"] for queue in row["queues"])
        text += f" {depth:>9} {losses:>5} {number(row['tick_p95_ms']):>8}"
        lines.append(paint(text, row["severity"]))
    if rows:
        row = rows[selected]
        if details:
            lines.append(f"Details: {safe_text(row['name'])} | report age {number(row['report_age_ms'])} ms | busy {number(row['busy_ms'])} ms")
            lines.append("  Trigger: " + safe_text(row["trigger"]) + "; waiting inputs: " + safe_text(", ".join(row["waiting_inputs"]) or "none"))
            for queue in row["queues"]:
                policy = queue["policy"]
                lines.append(f"  {safe_text(queue['scope'] + ' ' + queue['name'])}: {queue.get('depth', 0)}/{policy.get('capacity', '?')} "
                             f"{policy.get('buffer', '?')} {policy.get('overflow', '?')} age {queue.get('age_ms', 0):.1f} ms")
                lines.append("    totals " + " ".join(f"{key}={queue.get(key, 0)}" for key in COUNTERS))
        for alert in row["alerts"]:
            lines.append(paint(f"  [{alert['severity'].upper()}] {safe_text(alert['message'])}", alert["severity"]))
    lines.extend(["Green ACTIVE | amber ATTENTION | red LOSS/ERROR/UNAVAILABLE/STALE | cyan WAIT INPUT",
                  "Up/Down: select | Enter: details | Space: freeze display | q: quit" if interactive else
                  "Ctrl+C: quit | --count N: limit samples | --format jsonl: structured output"])
    def crop(line):
        # Preserve our color sequences, count only printable columns.
        pieces, visible = [], 0
        for part in re.split(r"(\x1b\[[0-9;]*m)", line):
            if part.startswith("\033["):
                pieces.append(part)
            else:
                pieces.append(part[:max(0, width - visible)])
                visible += len(part)
        return "".join(pieces)
    if height:
        lines = lines[:height]
    return "\n".join(crop(line) for line in lines)


class Keyboard:
    """Immediate terminal keys; never consume piped stdin."""
    def __init__(self, stream, enabled=True):
        self.stream = stream
        self.enabled = enabled
        self.queue = asyncio.Queue()
        self.pending = b""
        self.attributes = None

    def __enter__(self):
        if self.enabled and self.stream.isatty():
            self.fd = self.stream.fileno()
            self.attributes = termios.tcgetattr(self.fd)
            try:
                tty.setcbreak(self.fd)
                asyncio.get_running_loop().add_reader(self.fd, self.read)
            except BaseException:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.attributes)
                self.attributes = None
                raise
        return self

    def read(self):
        data = os.read(self.fd, 64)
        if not data:
            asyncio.get_running_loop().remove_reader(self.fd)
            return
        self.pending += data
        while self.pending:
            if self.pending.startswith(b"\033"):
                if len(self.pending) >= 2 and self.pending[1:2] not in (b"[", b"O"):
                    self.pending = self.pending[1:]
                    continue
                if len(self.pending) < 3:
                    return
                key, self.pending = self.pending[:3], self.pending[3:]
            else:
                key, self.pending = self.pending[:1], self.pending[1:]
            self.queue.put_nowait(key)

    def __exit__(self, *args):
        if self.attributes is not None:
            asyncio.get_running_loop().remove_reader(self.fd)
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.attributes)


async def run(args, output=None, stdin=None):
    output, stdin = output or sys.stdout, stdin or sys.stdin
    if not math.isfinite(args.interval) or args.interval < .1:
        raise ValueError("interval must be finite and at least 0.1 seconds")
    if not math.isfinite(args.window) or not .1 <= args.window <= 60:
        raise ValueError("window must be finite and between 0.1 and 60 seconds")
    if args.count is not None and args.count < 1:
        raise ValueError("count must be positive")
    if args.endpoint and (args.host or args.port):
        raise ValueError("choose --endpoint or --host/--port")
    distributed = args.distributed or args.insecure or any((args.tls_ca, args.tls_cert, args.tls_key))
    def address():
        endpoint_path = args.endpoint or ("build/endpoint.json" if not args.host and not args.port and not distributed else None)
        if endpoint_path:
            endpoint = json.loads(Path(endpoint_path).read_text())
            host, port = endpoint.get("host"), endpoint.get("port")
        else:
            host, port = args.host if args.host is not None else "127.0.0.1", args.port if args.port is not None else 7000
        if not isinstance(host, str) or not host or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("endpoint requires a host and a port between 1 and 65535")
        return host, port
    # Validate authentication before starting a retrying dashboard.
    host, port = address()
    context = contexts(args.tls_ca, args.tls_cert, args.tls_key, insecure=args.insecure, hosts=[host])[1] if distributed else None
    color = color_enabled(args.color, output) and args.format == "text"
    screen = output.isatty() and os.environ.get("TERM") != "dumb" and args.format == "text" and args.count is None
    observer = Observer(args.window)
    client, snapshot, report = None, None, None
    selected, details, frozen, count = 0, False, False, 0
    next_sample = 0
    failed = False
    keyboard = Keyboard(stdin, enabled=screen)
    try:
        with keyboard:
            if screen:
                output.write("\033[?1049h\033[?25l")
                output.flush()
            while True:
                if time.monotonic() >= next_sample:
                    error = None
                    try:
                        if client is None:
                            host, port = address()
                            if distributed and args.insecure:
                                contexts(insecure=True, hosts=[host])
                            client = await connect(host, port, 1024 * 1024, context,
                                                   "coordinator" if distributed else None, timeout=3)
                        snapshot, _ = await client.request("status", timeout=3)
                    except ssl.SSLError:
                        raise
                    except (OSError, EOFError, ConnectionError, asyncio.TimeoutError) as exc:
                        error = str(exc) or type(exc).__name__
                        if client is not None:
                            await client.close()
                            client = None
                    failed = error is not None
                    if snapshot is None:
                        snapshot = {"observation": {"version": 1, "components": {}, "subscriptions": {}}}
                    live_report = observer.sample(snapshot, error=error)
                    if not frozen or report is None:
                        report = live_report
                    count += 1
                    next_sample = time.monotonic() + args.interval
                    if args.format == "jsonl":
                        print(json.dumps(live_report, allow_nan=False), file=output, flush=True)
                    elif not screen:
                        print(render(report, details=True, color=color, interactive=False), file=output, flush=True)
                if screen:
                    size = shutil.get_terminal_size((120, 30))
                    output.write("\033[H\033[2J" + render(report, selected, details, frozen, color, size.columns, size.lines))
                    output.flush()
                if args.count is not None and count >= args.count:
                    return 1 if failed else 0
                try:
                    key = await asyncio.wait_for(keyboard.queue.get(), max(.01, next_sample - time.monotonic()))
                except asyncio.TimeoutError:
                    continue
                if key in (b"q", b"Q"):
                    return 0
                if key in (b"\033[A", b"\033OA", b"k"):
                    selected = max(0, selected - 1)
                elif key in (b"\033[B", b"\033OB", b"j"):
                    selected = min(max(0, len(report["components"]) - 1), selected + 1)
                elif key in (b"\r", b"\n"):
                    details = not details
                elif key == b" ":
                    frozen = not frozen
                    if not frozen:
                        report = live_report
    finally:
        try:
            if screen:
                output.write("\033[0m\033[?25h\033[?1049l")
                output.flush()
        finally:
            if client is not None:
                await finish_cleanup(client.close())
