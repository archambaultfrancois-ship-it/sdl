"""CLI for complete parameter blocks; all access goes through the service."""
import json
import os
from pathlib import Path
from ose.modeling.model import unique_pairs


def parser(commands):
    command = commands.add_parser('parameters', help='read, write or watch shared SDL parameter blocks')
    actions = command.add_subparsers(dest='parameter_action', required=True)
    for name in ('list', 'get', 'set', 'watch'):
        action = actions.add_parser(name)
        if name != 'list':
            action.add_argument('path')
        if name == 'set':
            action.add_argument('--file', required=True)
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


async def run(options):
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
        block = await client.block(options.path)
        if options.parameter_action == 'get':
            print(json.dumps(await block.get(), allow_nan=False), flush=True)
        elif options.parameter_action == 'set':
            source = Path(options.file)
            if source.stat().st_size > 1024 * 1024:
                raise ValueError('parameter JSON exceeds 1 MiB')
            value = json.loads(source.read_text(), object_pairs_hook=unique_pairs)
            await block.set(value)
            print(json.dumps({'updated': options.path}), flush=True)
        else:
            await client.subscribe([options.path])
            count = 0
            while options.count is None or count < options.count:
                update = await client.next()
                if update is not None:
                    path, value = update
                    print(json.dumps({'path': path, 'value': value}, allow_nan=False), flush=True)
                    count += 1
