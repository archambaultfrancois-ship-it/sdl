#!/usr/bin/env python3
"""Build the optional Rust engine and selected C ABI gateways."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

def run(command):
    print('+', shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, check=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jni', action='store_true')
    parser.add_argument('--mex', action='store_true', help='build a C MEX with mkoctfile')
    args = parser.parse_args()
    if sys.platform not in ('linux', 'android', 'darwin'):
        parser.error('automatic gateway builds currently support Linux/Android and macOS')
    out = ROOT / 'build/recording'
    release = out / 'release'
    run([os.environ.get('CARGO', 'cargo'), 'build', '--locked', '--release', '--manifest-path',
         ROOT / 'runtime/recording/Cargo.toml', '--target-dir', out])
    include = ROOT / 'runtime/recording/include'
    cc = shlex.split(os.environ.get('CC', 'cc'))
    linker = ['-L' + str(release), '-lsdl_recording', '-Wl,-rpath,$ORIGIN']
    if sys.platform == 'darwin':
        linker[-1] = '-Wl,-rpath,@loader_path'
    if args.jni:
        javac = shutil.which(os.environ.get('JAVAC', 'javac'))
        if not javac:
            parser.error('javac not found; set JAVA_HOME and JAVAC for your JDK')
        home = Path(os.environ.get('JAVA_HOME', str(Path(javac).resolve().parents[1])))
        platform = 'darwin' if sys.platform == 'darwin' else 'linux'
        suffix = 'dylib' if sys.platform == 'darwin' else 'so'
        run(cc + ['-std=c99', '-Wall', '-Wextra', '-Werror', '-fPIC', '-shared',
                  '-I' + str(include), '-I' + str(home / 'include'), '-I' + str(home / 'include' / platform),
                  ROOT / 'runtime/recording/java/sdl_recording_jni.c', *linker,
                  '-o', release / ('libsdl_recording_jni.' + suffix)])
        classes = out / 'java'
        classes.mkdir(parents=True, exist_ok=True)
        java_release = os.environ.get('SDL_JAVA_RELEASE', '8')
        run([javac, '-encoding', 'UTF-8', '-source', java_release, '-target', java_release, '-Xlint:-options',
             '-d', classes, ROOT / 'runtime/recording/java/SdlRecording.java'])
    if args.mex:
        mex = out / 'mex'
        mex.mkdir(parents=True, exist_ok=True)
        rpath = r'\$ORIGIN/../release' if sys.platform != 'darwin' else '@loader_path/../release'
        run([os.environ.get('MKOCTFILE', 'mkoctfile'), '--mex', '-std=c99', '-Wall', '-Wextra',
             '-I' + str(include), ROOT / 'runtime/recording/matlab/sdl_recording_mex.c',
             '-L' + str(release), '-lsdl_recording', '-Wl,-rpath,' + rpath,
             '-o', mex / 'sdl_recording_mex'])

if __name__ == '__main__':
    main()
