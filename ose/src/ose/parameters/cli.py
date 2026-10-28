"""CLI for complete parameter blocks; all access goes through the service."""
import asyncio
import json
import math
import os
import re
from pathlib import Path
from ose.modeling.model import unique_pairs

from ose.parameters.files import (MAX_JSON_SIZE, MAX_TOTAL_JSON_SIZE, read_source, read_value, validate_import)


def parser(commands):
    command = commands.add_parser('parameters', help='read, write or watch shared SDL parameter blocks')
    actions = command.add_subparsers(dest='parameter_action', required=True)
    for name in ('list', 'get', 'set', 'watch', 'sync'):
        action = actions.add_parser(name)
        if name in ('get', 'set', 'sync'):
            action.add_argument('path', nargs='?')
        elif name == 'watch':
            action.add_argument('path')
        if name == 'get':
            action.add_argument('--exact', action='store_true', help='read one literal path instead of matching a pattern')
            action.add_argument('--export', action='store_true', help='export {path: block}; PATH is a regex by default, omitted selects all')
        if name == 'set':
            source = action.add_mutually_exclusive_group(required=True)
            source.add_argument('--file', help='block JSON with PATH, or {path: block} export without PATH; - for stdin')
            source.add_argument('--dir', help='recursively import all *.json exports in a directory; no PATH')
        if name == 'watch':
            action.add_argument('--count', type=int)
        if name == 'sync':
            action.add_argument('--dir', required=True, help='tree of unit {path: block} JSON files')
            action.add_argument('--exact', action='store_true', help='select one literal path')
            action.add_argument('--initial', choices=('files', 'app'), default='files')
            action.add_argument('--debounce', type=float, default=.15, help='file event grouping seconds (default: 0.15)')
        if name in ('set', 'sync'):
            action.add_argument('--max-json-size', type=int, default=MAX_JSON_SIZE,
                                help='maximum bytes per JSON input (default: 16777216 / 16 MiB)')
        if name == 'set':
            action.add_argument('--max-total-json-size', type=int, default=MAX_TOTAL_JSON_SIZE,
                                help='maximum total JSON bytes for --dir (default: 67108864 / 64 MiB)')
        action.add_argument('--client', required=True)
        action.add_argument('--token-env', default='OSE_PARAMETER_TOKEN')
        action.add_argument('--endpoint', default='build/endpoint.json')
        action.add_argument('--host')
        action.add_argument('--port', type=int)
        action.add_argument('--plan', default='build/plan.json')
        action.add_argument('--sdl-root', default=os.environ.get('SDL_ROOT'))
        action.add_argument('--distributed', action='store_true')
        action.add_argument('--tls-ca')
        action.add_argument('--tls-cert')
        action.add_argument('--tls-key')
        action.add_argument('--insecure', action='store_true')


def read_directory(directory, max_size=MAX_JSON_SIZE, max_total=MAX_TOTAL_JSON_SIZE):
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f'parameter directory does not exist or is not a directory: {root}')
    def walk_error(error):
        raise error
    files = []
    for folder, _, names in os.walk(root, onerror=walk_error, followlinks=False):
        files.extend(Path(folder) / name for name in names
                     if name.endswith('.json') and (Path(folder) / name).is_file())
    if not files:
        raise ValueError(f'no *.json files found in parameter directory: {root}')
    merged, origins, total = {}, {}, 0
    for source in sorted(files, key=lambda path: path.relative_to(root).as_posix()):
        try:
            data = read_source(source, max_size)
            total += len(data)
            if total > max_total:
                raise ValueError(f'parameter directory JSON exceeds {max_total} bytes in total')
            value = json.loads(data.decode('utf-8'), object_pairs_hook=unique_pairs)
            validate_import(value)
        except (OSError, ValueError) as error:
            raise ValueError(f'{source}: {error}') from error
        for path, block in value.items():
            if path in origins:
                raise ValueError(f'duplicate parameter path {path}: {origins[path]} and {source}')
            merged[path], origins[path] = block, source
        validate_import(merged)
    return merged


def connection_factory(options):
    """Resolve the endpoint on each connection, including after restart."""
    from ose.parameters.client import ParameterClient
    from ose.transport.secure import contexts
    from ose.codec.runtime import Runtime
    if options.sdl_root:
        from ose.codec.sdl2 import SDL2
        runtime = SDL2(options.sdl_root)
    elif Path(options.plan).is_file():
        from ose.codec.sdl2 import SDL2
        runtime = SDL2(json.loads(Path(options.plan).read_text())['sdl_root'])
    else:
        try:
            runtime = Runtime()
        except ModuleNotFoundError as error:
            if error.name != 'sdl_runtime':
                raise
            raise ValueError('select --sdl-root, a compiled --plan, or install the SDL Python runtime') from error
    async def connect():
        endpoint = json.loads(Path(options.endpoint).read_text()) if options.host is None and Path(options.endpoint).is_file() else {}
        host, port = options.host or endpoint.get('host', '127.0.0.1'), options.port or endpoint.get('port')
        if type(port) is not int or not 0 < port < 65536:
            raise ValueError('select a valid parameter service port or endpoint')
        context = None
        if options.distributed:
            _, context = contexts(options.tls_ca, options.tls_cert, options.tls_key, insecure=options.insecure, hosts=[host])
        elif options.insecure or any((options.tls_ca, options.tls_cert, options.tls_key)):
            raise ValueError('TLS options require --distributed')
        return await ParameterClient.connect(host, port, options.client, token=os.environ.get(options.token_env),
            context=context, distributed=options.distributed, runtime=runtime)
    return connect


async def run(options):
    value = None
    if options.parameter_action in ('set', 'sync') and options.max_json_size <= 0:
        raise ValueError('--max-json-size must be positive')
    if options.parameter_action == 'set':
        if options.max_total_json_size <= 0:
            raise ValueError('--max-total-json-size must be positive')
        if options.dir is not None:
            if options.path is not None:
                raise ValueError('--dir cannot be combined with a positional parameter path')
            value = read_directory(options.dir, options.max_json_size, options.max_total_json_size)
        else:
            value = read_value(options.file, options.max_json_size)
    pattern = None
    if options.parameter_action in ('get', 'sync'):
        if options.exact and options.path is None:
            raise ValueError(f'{options.parameter_action} --exact requires PATH')
        # Keep existing value-only CLI reads literal. Shell shortcuts select
        # export mode, where a positional pattern (or all paths) is the default.
        if not options.exact and (options.parameter_action == 'sync' or options.export or options.path is None):
            try:
                pattern = re.compile(options.path if options.path is not None else '.*')
            except re.error as error:
                raise ValueError(f'invalid parameter path regex: {error}') from error
    if options.parameter_action == 'set' and options.path is None:
        validate_import(value)
    if options.parameter_action == 'watch' and options.count is not None and options.count <= 0:
        raise ValueError('watch count must be positive')
    connect = connection_factory(options)
    if options.parameter_action == 'sync':
        if not math.isfinite(options.debounce) or options.debounce < 0:
            raise ValueError('--debounce must be finite and nonnegative')
        from ose.parameters.sync import DirectorySync
        await DirectorySync(options.dir, connect, pattern=pattern, exact=options.path if options.exact else None,
                            initial=options.initial, debounce=options.debounce, max_json_size=options.max_json_size).run()
        return
    client = await connect()
    async with client:
        if options.parameter_action == 'list':
            print(json.dumps(await client.list()), flush=True)
            return
        if options.parameter_action == 'get':
            paths = [options.path] if pattern is None else [path for path in await client.list() if pattern.search(path)]
            exported = {}
            for path in paths:
                block = await client.block(path)
                exported[path] = await block.get()
            result = exported if options.export or pattern is not None else exported[options.path]
            print(json.dumps(result, allow_nan=False), flush=True)
        elif options.parameter_action == 'set':
            if options.path is not None:
                block = await client.block(options.path)
                await block.set(value)
                print(json.dumps({'updated': options.path}), flush=True)
            else:
                # Validate every block before the first write. Service-side
                # failures may still occur after earlier writes have succeeded.
                blocks = {}
                for path, contents in value.items():
                    blocks[path] = await client.block(path)
                    blocks[path].values.encode(contents)
                updated = []
                for path, block in blocks.items():
                    try:
                        await block.set(value[path])
                    except (ValueError, OSError, asyncio.TimeoutError) as error:
                        raise ValueError(f'parameter import failed at {path}; updated blocks: '
                                         f'{json.dumps(updated)}; {error}') from error
                    updated.append(path)
                print(json.dumps({'updated': updated}), flush=True)
        else:
            await client.subscribe([options.path])
            count = 0
            while options.count is None or count < options.count:
                update = await client.next()
                if update is not None:
                    path, value = update
                    print(json.dumps({'path': path, 'value': value}, allow_nan=False), flush=True)
                    count += 1
