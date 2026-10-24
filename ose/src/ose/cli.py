"""Standalone project command line."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
from ose.modeling.compiler import build, validate
from ose.recording import DEFAULT_LABEL, session_label


def main():
    parser = argparse.ArgumentParser(prog="ose-ctl")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "build"):
        command = commands.add_parser(name)
        command.add_argument("--model", required=True)
        command.add_argument("--deployment", required=True)
        command.add_argument("--sdl-root", default=os.environ.get("SDL_ROOT", str(Path(__file__).resolve().parents[3] / "sdl")),
                             help="SDL checkout (default: SDL_ROOT or the sibling sdl directory of the OSE source checkout)")
        command.add_argument("--build-dir", default="build")
        if name == "build":
            command.add_argument("--component", help="compile only this component, retaining the complete plan")
    for name in ("serve", "worker", "cluster-status"):
        command = commands.add_parser(name)
        command.add_argument("--host", default="127.0.0.1", help="listen address for serve; coordinator address otherwise")
        command.add_argument("--port", type=int, default=7000)
        command.add_argument("--tls-ca")
        command.add_argument("--tls-cert")
        command.add_argument("--tls-key")
        command.add_argument("--insecure", action="store_true", help="explicit loopback-only development mode")
        if name != "cluster-status":
            command.add_argument("--plan", default="build/plan.json")
        if name == "serve":
            command.add_argument("--lease-seconds", type=float, default=6)
            command.add_argument("--kubernetes-lease", help="pre-created Kubernetes Lease for coordinator fencing")
            command.add_argument("--namespace", default="default")
        if name == "worker":
            command.add_argument("--node", required=True)
            command.add_argument("--listen-host", default="127.0.0.1")
            command.add_argument("--listen-port", type=int, default=0)
            command.add_argument("--advertise-host", default="127.0.0.1")
    command = commands.add_parser("export-kubernetes")
    command.add_argument("--plan", default="build/plan.json")
    command.add_argument("--images", required=True)
    command.add_argument("--namespace", default="ose-app")
    command.add_argument("--image-pull-secret", action="append", default=[])
    command.add_argument("--output", required=True)
    command = commands.add_parser("run")
    command.add_argument("--plan", default="build/plan.json")
    command.add_argument("--duration", type=float)
    command.add_argument("--port", type=int)
    command.add_argument("--mode", choices=("realtime", "simulated"), default="realtime")
    command.add_argument("--workers", type=int)
    command.add_argument("--scenario")
    command.add_argument("--trace")
    command.add_argument("--max-tick-output-bytes", type=int)
    command = commands.add_parser("replay", help="replay a recording into simulated or live external inputs")
    command.add_argument("recording", help="SDL PCAPNG or MCAP capture")
    command.add_argument("--plan", default="build/plan.json")
    command.add_argument("--map", dest="mappings", action="append", required=True,
                         metavar="RECORDED_TOPIC=EXTERNAL_TOPIC", help="select and map a topic; repeat for each input")
    command.add_argument("--mode", choices=("simulated", "realtime"), default="simulated")
    command.add_argument("--loops", type=int, default=1, help="total playback passes (default: 1; 0: infinite)")
    command.add_argument("--duration", type=float, help="recorded-time horizon in seconds; required for simulation")
    command.add_argument("--workers", type=int)
    command.add_argument("--start-ns", type=int, help="inclusive recorded log timestamp in nanoseconds")
    command.add_argument("--end-ns", type=int, help="exclusive recorded log timestamp in nanoseconds")
    command.add_argument("--trace", help="write the deterministic simulation trace")
    command.add_argument("--speed", type=float, help="real-time playback rate (default: 1; 0: maximum speed)")
    command.add_argument("--interactive", action="store_true", help="live speed, pause, resume and stop controls")
    command.add_argument("--command", action="append",
                         help="execute a live replay command in order; repeat to script a session (wait SECONDS supported)")
    command.add_argument("--endpoint", help="live endpoint JSON; defaults to the local plan's endpoint.json")
    command.add_argument("--host")
    command.add_argument("--port", type=int)
    command.add_argument("--distributed", action="store_true", help="publish through the distributed coordinator")
    command.add_argument("--tls-ca")
    command.add_argument("--tls-cert", help="distributed client certificate with common name replay")
    command.add_argument("--tls-key")
    command.add_argument("--insecure", action="store_true", help="distributed loopback-only development mode")
    command = commands.add_parser("inspect")
    command.add_argument("--endpoint", default="build/endpoint.json")
    command.add_argument("--last", action="store_true")
    command = commands.add_parser("top", help="live component, queue and delivery diagnostics")
    command.add_argument("--endpoint", help="local endpoint JSON (default: build/endpoint.json)")
    command.add_argument("--host")
    command.add_argument("--port", type=int)
    command.add_argument("--distributed", action="store_true")
    command.add_argument("--tls-ca")
    command.add_argument("--tls-cert")
    command.add_argument("--tls-key")
    command.add_argument("--insecure", action="store_true", help="distributed loopback-only development mode")
    command.add_argument("--interval", type=float, default=1, help="refresh seconds (default: 1)")
    command.add_argument("--window", type=float, default=5, help="rate window seconds (default: 5)")
    command.add_argument("--count", type=int, help="exit after N samples")
    command.add_argument("--format", choices=("text", "jsonl"), default="text")
    command.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    command.add_argument("--no-color", dest="color", action="store_const", const="never")
    command = commands.add_parser("record", help="discover and interactively record live application outputs")
    command.add_argument("-l", "--label", type=session_label, default=DEFAULT_LABEL,
                         help="informational session label (default: recorder)")
    command.add_argument("--endpoint", help="local broker endpoint JSON (default: build/endpoint.json when present)")
    command.add_argument("--host")
    command.add_argument("--port", type=int)
    command.add_argument("--distributed", action="store_true", help="discover remote workers through the coordinator")
    command.add_argument("--tls-ca")
    command.add_argument("--tls-cert")
    command.add_argument("--tls-key")
    command.add_argument("--insecure", action="store_true", help="distributed loopback-only development mode")
    command.add_argument("--sdl-root", default=os.environ.get("SDL_ROOT"))
    command.add_argument("--capacity", type=int, default=256, help="capture queue messages per endpoint")
    command.add_argument("--byte-budget", type=int, default=8 * 1024 * 1024, help="capture queue bytes per endpoint")
    command.add_argument("--max-frame", type=int, default=1024 * 1024)
    command.add_argument("--command", action="append", help="execute a console command; repeat to script a session")
    command = commands.add_parser("adapter", help=argparse.SUPPRESS)
    command.add_argument("--plan", required=True)
    command.add_argument("--node", required=True)
    command.add_argument("--port", type=int, required=True)
    from ose.parameters import cli as parameter_cli
    parameter_cli.parser(commands)
    args = parser.parse_args()
    try:
        if args.action == "parameters":
            try:
                asyncio.run(parameter_cli.run(args))
            except KeyboardInterrupt:
                return 130
        elif args.action in ("build", "validate"):
            if not args.sdl_root:
                raise ValueError("set SDL_ROOT or pass --sdl-root explicitly")
            if args.action == "validate":
                validate(args.model, args.deployment, args.sdl_root)
                print(json.dumps({"valid": True}))
            else:
                print(build(args.model, args.deployment, args.sdl_root, args.build_dir, args.component))
        elif args.action == "export-kubernetes":
            from ose.modeling.kubernetes import export
            export(args.plan, args.images, args.namespace, args.output, args.image_pull_secret)
        elif args.action in ("serve", "worker", "cluster-status"):
            from ose.transport.secure import contexts, connect
            hosts = [args.host]
            if args.action == "worker":
                hosts += [args.listen_host, args.advertise_host]
            server_context, client_context = contexts(args.tls_ca, args.tls_cert, args.tls_key,
                                                     insecure=args.insecure, hosts=hosts)
            if args.action == "serve":
                from ose.execution.distributed import serve
                asyncio.run(serve(args.plan, args.host, args.port, server_context, args.lease_seconds,
                                  args.kubernetes_lease, args.namespace))
            elif args.action == "worker":
                from ose.execution.distributed import work
                asyncio.run(work(args.plan, args.node, args.host, args.port, listen_host=args.listen_host,
                    listen_port=args.listen_port, advertise_host=args.advertise_host,
                    server_context=server_context, client_context=client_context))
            else:
                async def status():
                    client = await connect(args.host, args.port, 1024 * 1024, client_context, "coordinator")
                    try:
                        result, _ = await client.request("status")
                        print(json.dumps({k: v for k, v in result.items() if k not in ("op", "request", "content")}, indent=2))
                    finally:
                        await client.close()
                asyncio.run(status())
        elif args.action == "run":
            if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
                raise ValueError("duration must be finite and positive")
            if args.mode == "simulated":
                from ose.execution.simulation import run
                from ose.execution.simulation.coordinator import DEFAULT_OUTPUT_LIMIT
                if args.duration is None:
                    raise ValueError("simulated execution requires --duration in virtual seconds")
                if args.port is not None:
                    raise ValueError("--port is unavailable in simulated mode")
                summary = asyncio.run(run(args.plan, args.duration,
                    workers=1 if args.workers is None else args.workers, scenario=args.scenario, trace=args.trace,
                    max_tick_output_bytes=DEFAULT_OUTPUT_LIMIT if args.max_tick_output_bytes is None else args.max_tick_output_bytes))
                print(json.dumps(summary), flush=True)
            else:
                from ose.execution.supervisor import run
                if json.loads(Path(args.plan).read_text()).get("execution") == "distributed":
                    raise ValueError("use serve and worker for a distributed plan")
                if any(value is not None for value in (args.workers, args.scenario, args.trace, args.max_tick_output_bytes)):
                    raise ValueError("--workers, --scenario, --trace and --max-tick-output-bytes require simulated mode")
                asyncio.run(run(args.plan, args.duration, args.port or 0))
        elif args.action == "replay":
            if args.mode == "simulated":
                from ose.recording.replay import run
                if args.duration is None:
                    raise ValueError("simulated replay requires --duration")
                if (args.interactive or args.command is not None or args.distributed or args.insecure or any(value is not None for value in
                        (args.speed, args.endpoint, args.host, args.port, args.tls_ca, args.tls_cert, args.tls_key))):
                    raise ValueError("speed, replay commands, interactive controls and connection options require --mode realtime")
                summary = asyncio.run(run(args.recording, args.plan, args.duration, mappings=args.mappings,
                    start_ns=args.start_ns, end_ns=args.end_ns,
                    workers=1 if args.workers is None else args.workers, trace=args.trace, loops=args.loops))
            else:
                from ose.recording.live_replay import run
                if args.workers is not None or args.trace is not None:
                    raise ValueError("--workers and --trace require simulated replay")
                async def replay():
                    task = asyncio.create_task(run(args.recording, args.plan, args.duration, mappings=args.mappings,
                        start_ns=args.start_ns, end_ns=args.end_ns, speed=1 if args.speed is None else args.speed,
                        endpoint=args.endpoint, host=args.host, port=args.port, distributed=args.distributed,
                        tls_ca=args.tls_ca, tls_cert=args.tls_cert, tls_key=args.tls_key,
                        insecure=args.insecure, interactive=args.interactive, commands=args.command, loops=args.loops))
                    loop = asyncio.get_running_loop()
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        loop.add_signal_handler(sig, task.cancel)
                    try:
                        return await task
                    except asyncio.CancelledError:
                        return None
                    finally:
                        for sig in (signal.SIGINT, signal.SIGTERM):
                            loop.remove_signal_handler(sig)
                summary = asyncio.run(replay())
                if summary is None:
                    return 130
            print(json.dumps(summary), flush=True)
            if summary.get("refused", 0):
                return 1
        elif args.action == "top":
            from ose.top import run
            async def top():
                task = asyncio.create_task(run(args))
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, task.cancel)
                try:
                    return await task
                except asyncio.CancelledError:
                    return 130
                finally:
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        loop.remove_signal_handler(sig)
            return asyncio.run(top())
        elif args.action == "record":
            from ose.recording.console import run
            async def recording():
                task = asyncio.create_task(run(args))
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, task.cancel)
                try:
                    return await task
                except asyncio.CancelledError:
                    return 130
            return asyncio.run(recording())
        elif args.action == "inspect":
            if args.last:
                print((Path(args.endpoint).parent / "last-status.json").read_text())
            else:
                from ose.transport.tcp import Client
                endpoint = json.loads(Path(args.endpoint).read_text())
                async def inspect():
                    client = await Client.connect(endpoint["host"], endpoint["port"])
                    try:
                        result, _ = await client.request("status")
                        print(json.dumps(result, indent=2))
                    finally:
                        await client.close()
                asyncio.run(inspect())
        else:
            from ose.execution.adapter import Adapter
            plan = json.loads(Path(args.plan).read_text())
            adapter = Adapter(plan, args.node)
            async def execute():
                task = asyncio.create_task(adapter.run("127.0.0.1", args.port))
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, task.cancel)
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            asyncio.run(execute())
    except (ValueError, OSError, EOFError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ose-ctl: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
