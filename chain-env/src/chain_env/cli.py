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
from chain_env.modeling.compiler import build, validate


def main():
    parser = argparse.ArgumentParser(prog="sdl-chain")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "build"):
        command = commands.add_parser(name)
        command.add_argument("--model", required=True)
        command.add_argument("--deployment", required=True)
        command.add_argument("--sdl-root", default=os.environ.get("SDL_ROOT", str(Path(__file__).resolve().parents[3])),
                             help="SDL checkout (default: SDL_ROOT or the parent of the chain-env source checkout)")
        command.add_argument("--build-dir", default="build")
    command = commands.add_parser("run")
    command.add_argument("--plan", default="build/plan.json")
    command.add_argument("--duration", type=float)
    command.add_argument("--port", type=int)
    command.add_argument("--mode", choices=("realtime", "simulated"), default="realtime")
    command.add_argument("--workers", type=int)
    command.add_argument("--scenario")
    command.add_argument("--trace")
    command.add_argument("--max-tick-output-bytes", type=int)
    command = commands.add_parser("inspect")
    command.add_argument("--endpoint", default="build/endpoint.json")
    command.add_argument("--last", action="store_true")
    command = commands.add_parser("adapter", help=argparse.SUPPRESS)
    command.add_argument("--plan", required=True)
    command.add_argument("--node", required=True)
    command.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.action in ("build", "validate"):
            if not args.sdl_root:
                raise ValueError("set SDL_ROOT or pass --sdl-root explicitly")
            if args.action == "validate":
                validate(args.model, args.deployment, args.sdl_root)
                print(json.dumps({"valid": True}))
            else:
                print(build(args.model, args.deployment, args.sdl_root, args.build_dir))
        elif args.action == "run":
            if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
                raise ValueError("duration must be finite and positive")
            if args.mode == "simulated":
                from chain_env.execution.simulation import run
                from chain_env.execution.simulation.coordinator import DEFAULT_OUTPUT_LIMIT
                if args.duration is None:
                    raise ValueError("simulated execution requires --duration in virtual seconds")
                if args.port is not None:
                    raise ValueError("--port is unavailable in simulated mode")
                summary = asyncio.run(run(args.plan, args.duration,
                    workers=1 if args.workers is None else args.workers, scenario=args.scenario, trace=args.trace,
                    max_tick_output_bytes=DEFAULT_OUTPUT_LIMIT if args.max_tick_output_bytes is None else args.max_tick_output_bytes))
                print(json.dumps(summary), flush=True)
            else:
                from chain_env.execution.supervisor import run
                if any(value is not None for value in (args.workers, args.scenario, args.trace, args.max_tick_output_bytes)):
                    raise ValueError("--workers, --scenario, --trace and --max-tick-output-bytes require simulated mode")
                asyncio.run(run(args.plan, args.duration, args.port or 0))
        elif args.action == "inspect":
            if args.last:
                print((Path(args.endpoint).parent / "last-status.json").read_text())
            else:
                from chain_env.transport.tcp import Client
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
            from chain_env.execution.adapter import Adapter
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
        print(f"sdl-chain: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
