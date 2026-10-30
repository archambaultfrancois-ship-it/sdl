"""Build standalone external language SDKs without downloading dependencies."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from ose.modeling.native_containers import sdk_root
from ose.modeling.native_client_build import writable_manifest
from ose.modeling.managed_containers import tool


def build_java(sdl_root, destination, extra_sources=()):
    sdk = sdk_root(); destination = Path(destination).resolve(); destination.mkdir(parents=True, exist_ok=True)
    sdl_root = Path(sdl_root).resolve()
    target = destination / 'rust'
    env = dict(os.environ, CARGO_TARGET_DIR=str(target))
    subprocess.run(tool('CARGO', 'cargo') + ['build', '--offline', '--manifest-path', str(writable_manifest(sdk, destination))], env=env, check=True)
    javac = tool('JAVAC', 'javac')[0]
    home = Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else Path(shutil.which(javac) or javac).resolve().parents[1]
    if not (home / 'include/jni.h').is_file():
        raise ValueError('JAVA_HOME must name a JDK containing include/jni.h')
    platform = 'darwin' if sys.platform == 'darwin' else 'linux'
    library = destination / ('libsdl_ose_jni.dylib' if platform == 'darwin' else 'libsdl_ose_jni.so')
    subprocess.run(tool('CC', 'cc') + ['-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(home / 'include'),
        '-I' + str(home / 'include' / platform), '-I' + str(sdk / 'client/c'), str(sdk / 'client/java/ose_jni.c'),
        str(target / 'debug/libsdl_ose_client.a'), '-lpthread', '-ldl', '-lm', '-o', str(library)], check=True)
    sources = [sdl_root / 'runtime/java/SdlCodec.java', *sorted((sdk / 'java').rglob('*.java')),
               *sorted((sdk / 'client/java').rglob('*.java')), *map(Path, extra_sources)]
    classes = destination / 'classes'
    subprocess.run([sys.executable, '-m', 'ose.modeling.java_build', str(classes), *map(str, sources)], check=True)
    subprocess.run(tool('JAR', 'jar') + ['cf', str(destination / 'sdl-ose-client.jar'), '-C', str(classes), '.'], check=True)
    return classes, library


if __name__ == '__main__':
    build_java(*sys.argv[1:3], extra_sources=sys.argv[3:])
