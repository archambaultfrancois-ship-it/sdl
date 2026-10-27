"""CLI for complete parameter blocks; all access goes through the service."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from ose.modeling.model import unique_pairs


def parser(commands):
    command = commands.add_parser('parameters', help='read, write or watch shared SDL parameter blocks')
    actions = command.add_subparsers(dest='parameter_action', required=True)
    for name in ('list', 'get', 'set', 'watch'):
        action = actions.add_parser(name)
        if name in ('get', 'set'):
            action.add_argument('path', nargs='?')
        elif name == 'watch':
            action.add_argument('path')
        if name == 'get':
            action.add_argument('--exact', action='store_true', help='read one literal path instead of matching a pattern')
            action.add_argument('--export', action='store_true', help='export {path: block}; PATH is a regex by default, omitted selects all')
        if name == 'set':
            action.add_argument('--file', required=True, help='block JSON with PATH, or {path: block} export without PATH; - for stdin')
        if name == 'watch':
            action.add_argument('--count', type=int)
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


def read_value(source):
    limit = 1024 * 1024
    if source == '-':
        data = sys.stdin.buffer.read(limit + 1)
    else:
        with Path(source).open('rb') as stream:
            data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('parameter JSON exceeds 1 MiB')
    return json.loads(data.decode('utf-8'), object_pairs_hook=unique_pairs)


async def run(options):
    from ose.parameters.client import ParameterClient
    from ose.transport.secure import contexts
    from ose.codec.runtime import Runtime
    value = read_value(options.file) if options.parameter_action == 'set' else None
    pattern = None
    if options.parameter_action == 'get':
        if options.exact and options.path is None:
            raise ValueError('get --exact requires PATH')
        # Keep existing value-only CLI reads literal. Shell shortcuts select
        # export mode, where a positional pattern (or all paths) is the default.
        if not options.exact and (options.export or options.path is None):
            try:
                pattern = re.compile(options.path if options.path is not None else '.*')
            except re.error as error:
                raise ValueError(f'invalid parameter path regex: {error}') from error
    if options.parameter_action == 'set' and options.path is None:
        if (not isinstance(value, dict) or len(value) > 4096 or
                any(not path or not isinstance(block, dict) for path, block in value.items())):
            raise ValueError('parameter import requires a JSON object {path: complete block}, at most 4096 blocks')
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
    endpoint = json.loads(Path(options.endpoint).read_text()) if options.host is None and Path(options.endpoint).is_file() else {}
    host, port = options.host or endpoint.get('host', '127.0.0.1'), options.port or endpoint.get('port')
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError('select a valid parameter service port or endpoint')
    if options.parameter_action == 'watch' and options.count is not None and options.count <= 0:
        raise ValueError('watch count must be positive')
    context = None
    if options.distributed:
        _, context = contexts(options.tls_ca, options.tls_cert, options.tls_key, insecure=options.insecure, hosts=[host])
    elif options.insecure or any((options.tls_ca, options.tls_cert, options.tls_key)):
        raise ValueError('TLS options require --distributed')
    client = await ParameterClient.connect(host, port, options.client, token=os.environ.get(options.token_env),
        context=context, distributed=options.distributed, runtime=runtime)
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
