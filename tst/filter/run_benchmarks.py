#!/usr/bin/env python3
"""Identical warm filter cases across backends; timings do not gate correctness."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build/filter'
BACKENDS = ('c', 'rust', 'python', 'java', 'ada', 'octave', 'matlab')


def run(command, output=None, env=None):
    environment = dict(os.environ, CCACHE_DISABLE='1')
    if env:
        environment.update(env)
    result = subprocess.run(list(map(str, command)), cwd=ROOT, env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if output:
        output.write_text(result.stdout)
    if result.returncode:
        print(result.stdout, file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, command)
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backends', nargs='+', choices=BACKENDS, default=list(BACKENDS[:-1]))
    parser.add_argument('--no-build', action='store_true')
    parser.add_argument('--gnatmake', default=os.environ.get('GNATMAKE', '/home/t0005140/opt/gnat-21.1/bin/gnatmake'))
    parser.add_argument('--matlab', default=os.environ.get('MATLAB', 'matlab'))
    parser.add_argument('--report', type=Path, default=OUT / 'benchmarks.json')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.no_build:
        options = [sys.executable, 'tools/filter/build_native.py']
        if 'python' in args.backends: options.append('--python')
        if 'java' in args.backends: options.append('--jni')
        if 'octave' in args.backends: options.append('--mex')
        run(options, OUT / 'bench-build.log')
    run([sys.executable, 'gen/generator.py', '-c', '-rust', '-python', '-java', '-ada', '-matlab',
         'tst/fixtures/filter', OUT / 'generated'], OUT / 'bench-generation.log')
    cc = shlex.split(os.environ.get('CC', 'cc'))
    link = ['-L' + str(OUT / 'release'), '-lsdl_filter', '-Wl,-rpath,' + str(OUT / 'release')]
    c_sources = sorted((ROOT / 'runtime/c').glob('*.c'))
    commands = {}
    if 'c' in args.backends:
        run(cc + ['-std=c99', '-Wall', '-Wextra', '-Werror', '-O3', '-Iruntime/c', '-Iruntime/filter/include', '-Iruntime/filter/c',
                  '-I' + str(OUT / 'generated/c'), 'runtime/filter/c/sdl_filter_native.c', *c_sources,
                  *sorted((OUT / 'generated/c').rglob('*.c')), 'tst/filter/c/bench.c', *link, '-lm', '-o', OUT / 'c-bench'], OUT / 'bench-c-build.log')
        commands['c'] = [OUT / 'c-bench']
    commands['rust'] = [os.environ.get('CARGO', 'cargo'), 'run', '--offline', '--release', '--manifest-path',
                        'tst/filter/rust/Cargo.toml', '--bin', 'filter-bench', '--target-dir', OUT / 'rust-check']
    commands['python'] = [sys.executable, 'tst/filter/python/bench.py']
    if 'java' in args.backends:
        classes = OUT / 'java'; classes.mkdir(exist_ok=True)
        javac = shlex.split(os.environ.get('JAVAC', 'javac'))[0]
        run([javac, '-encoding', 'UTF-8', '-source', '7', '-target', '7', '-Xlint:-options', '-d', classes,
             *sorted((OUT / 'generated/java').rglob('*.java')), 'runtime/filter/java/SdlFilter.java', 'tst/filter/java/FilterBench.java'], OUT / 'bench-java-build.log')
        commands['java'] = shlex.split(os.environ.get('JAVA', 'java')) + ['-Djava.library.path=' + str(OUT / 'release'), '-cp', classes, 'FilterBench']
    if 'ada' in args.backends:
        ada = OUT / 'ada'; ada.mkdir(exist_ok=True); objects = []
        for source in c_sources + [ROOT / 'runtime/ada/sdl_ada_bridge.c']:
            obj = ada / (source.stem + '.o')
            run(cc + ['-std=c99', '-O3', '-Iruntime/c', '-c', source, '-o', obj]); objects.append(obj)
        run([args.gnatmake, '--RTS=sjlj', '-gnat2012', '-gnata', '-gnatn', '-O3', '-Iruntime/ada', '-Iruntime/filter/ada',
             '-I' + str(OUT / 'generated/ada'), '-D', ada, '-o', ada / 'filter-bench', 'tst/filter/ada/filter_bench.adb',
             '-largs', *objects, *link, '-lm'], OUT / 'bench-ada-build.log')
        commands['ada'] = [ada / 'filter-bench']
    setup = "addpath('build/filter/generated/matlab');addpath('runtime/filter/matlab');addpath('tst/filter/matlab');"
    commands['octave'] = [os.environ.get('OCTAVE', 'octave'), '--quiet', '--no-gui', '--eval', setup + "addpath('build/filter/mex');filter_bench;"]
    commands['matlab'] = [args.matlab, '-nodisplay', '-nosplash', '-nodesktop', '-nojvm', '-r',
                          "try," + setup + "addpath('build/filter/matlab-mex');filter_bench;catch err,disp(getReport(err,'extended'));exit(1);end;exit(0)"]
    records = []
    print('Backend  Case           Encode ns   Decode ns   Object ns    Wire ns   Obj/enc Wire/dec', flush=True)
    for backend in args.backends:
        env = {'PYTHONPATH': ':'.join(map(str, [ROOT / 'runtime/filter/python', OUT / 'release', ROOT / 'runtime/python', OUT / 'generated/python']))}
        output = run(commands[backend], OUT / ('bench-' + backend + '.log'), env)
        for line in output.splitlines():
            if not line.startswith('BENCH '): continue
            _, language, case, *numbers = line.split(); enc, dec, obj, wire = map(float, numbers)
            record = dict(backend=language, case=case, encode_ns=enc, decode_ns=dec, object_ns=obj, wire_ns=wire,
                          object_ratio=obj / enc, wire_ratio=wire / dec)
            records.append(record)
            print('%-8s %-14s %10.1f %10.1f %10.1f %10.1f %8.2f %8.2f' %
                  (language, case, enc, dec, obj, wire, obj / enc, wire / dec), flush=True)
    report = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), platform=platform.platform(),
                  method='Median of five warmed trials; prepare excluded; codecs allocate and release outputs; filter alone.', results=records)
    args.report.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
