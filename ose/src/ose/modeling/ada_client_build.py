"""Compile Ada client units and native libraries for standalone applications."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
from ose.modeling.native_containers import sdk_root
from ose.modeling.native_client_build import writable_manifest
from ose.modeling.managed_containers import tool


def build_ada(sdl_root, destination, main=None, include_dirs=()):
    destination = Path(destination).resolve(); destination.mkdir(parents=True, exist_ok=True)
    sdk = sdk_root(); runtime = Path(sdl_root).resolve() / 'runtime'
    target = destination / 'rust'; objects = destination / 'obj'; objects.mkdir(exist_ok=True)
    subprocess.run(tool('CARGO', 'cargo') + ['build', '--offline', '--manifest-path', str(writable_manifest(sdk, destination))], env=dict(os.environ, CARGO_TARGET_DIR=str(target)), check=True)
    sources = [runtime / 'c' / (name + '.c') for name in ('type_engine', 'type_registry', 'type_codec', 'sdl_wire', 'sdl_dynamic', 'type_display')]
    sources += [runtime / 'ada/sdl_ada_bridge.c', sdk / 'client/ada/ose_ada.c']
    linked = []
    for i, source in enumerate(sources):
        obj = objects / f'bridge_{i}.o'; linked.append(str(obj))
        subprocess.run(tool('CC', 'cc') + ['-std=c11', '-O2', '-Wall', '-Wextra', '-I' + str(runtime / 'c'), '-I' + str(sdk / 'client/c'), '-c', str(source), '-o', str(obj)], check=True)
    includes = [sdk / 'client/ada', sdk / 'ada', runtime / 'ada', *map(Path, include_dirs)]
    flags = shlex.split(os.environ.get('ADA_FLAGS', '-gnat2012 -gnata -O2'))
    if os.environ.get('ADA_RTS'): flags.append('--RTS=' + os.environ['ADA_RTS'])
    if main is None:
        main = destination / 'client_probe.adb'
        main.write_text('with Ose_Client;\nprocedure Client_Probe is\n   C : Ose_Client.Client;\nbegin null; end Client_Probe;\n')
    main = Path(main).resolve(); binary = destination / main.stem
    subprocess.run(tool('GNATMAKE', 'gnatmake') + flags + ['-D', str(objects), *['-I' + str(p) for p in includes], '-I' + str(main.parent), str(main), '-o', str(binary), '-largs', *linked, str(target / 'debug/libsdl_ose_client.a'), '-ldl', '-lpthread', '-lm'], check=True)
    return binary


if __name__ == '__main__':
    build_ada(*sys.argv[1:])
