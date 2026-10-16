#!/usr/bin/env python3
"""Build the common filter library and selected language gateways."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build/filter'

def run(command):
    print('+', shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, check=True,
        env=dict(os.environ, CCACHE_DISABLE='1'))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', action='store_true')
    parser.add_argument('--jni', action='store_true')
    parser.add_argument('--mex', action='store_true')
    args = parser.parse_args()
    run([os.environ.get('CARGO','cargo'), 'build', '--locked', '--release', '--offline',
        '--manifest-path', ROOT/'runtime/filter/Cargo.toml', '--target-dir', OUT])
    cc = shlex.split(os.environ.get('CC','cc'))
    include = ['-I'+str(ROOT/'runtime/filter/include')]
    relative = '@loader_path' if sys.platform == 'darwin' else '$ORIGIN'
    link = ['-L'+str(OUT/'release'), '-lsdl_filter', '-Wl,-rpath,'+relative]
    if args.python:
        # Android extensions must explicitly link the interpreter's shared library.
        python_link = ([str(Path(sysconfig.get_config_var('LIBDIR')) /
            sysconfig.get_config_var('LDLIBRARY'))] if sys.platform == 'android' else [])
        run(cc + ['-std=c99','-Wall','-Wextra','-Werror','-O3','-shared','-fPIC'] + include +
            ['-I'+sysconfig.get_path('include'), ROOT/'runtime/filter/python/sdl_filter_native.c',
             *python_link,*link,'-o', OUT/'release'/('_sdl_filter_native'+sysconfig.get_config_var('EXT_SUFFIX'))])
    if args.jni:
        javac = shutil.which(shlex.split(os.environ.get('JAVAC','javac'))[0])
        if not javac:
            parser.error('javac not found')
        home = Path(os.environ.get('JAVA_HOME',str(Path(javac).resolve().parents[1])))
        platform = 'darwin' if sys.platform=='darwin' else 'linux'
        run(cc + ['-std=c99','-Wall','-Wextra','-Werror','-O3','-shared','-fPIC','-pthread'] + include +
            ['-I'+str(home/'include'),'-I'+str(home/'include'/platform),ROOT/'runtime/filter/java/sdl_filter_jni.c',
             *link,'-o',OUT/'release'/('libsdl_filter_jni.'+('dylib' if sys.platform=='darwin' else 'so'))])
        classes=OUT/'java';classes.mkdir(exist_ok=True)
        run([javac,'-encoding','UTF-8','-source','7','-target','7','-Xlint:-options','-d',classes,ROOT/'runtime/filter/java/SdlFilter.java'])
        jar=home/'bin/jar'
        run([jar,'cf',OUT/'sdl-filter.jar','-C',classes,'sdl/filter'])
    if args.mex:
        destination=OUT/'mex';destination.mkdir(exist_ok=True)
        run([os.environ.get('MKOCTFILE','mkoctfile'),'--mex','-std=c99','-O3',*include,
            ROOT/'runtime/filter/matlab/sdl_filter_mex.c','-L'+str(OUT/'release'),'-lsdl_filter',
            '-Wl,-rpath,'+('\\'+relative if sys.platform!='darwin' else relative)+'/../release','-o',destination/'sdl_filter_mex'])

if __name__ == '__main__':
    main()
