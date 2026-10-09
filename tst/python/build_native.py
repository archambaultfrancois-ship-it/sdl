"""Build the optional extension for the invoking CPython, without setuptools."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig

root = Path(__file__).resolve().parents[2]
out = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build' / 'generated' / 'python'
out.mkdir(parents=True, exist_ok=True)
command = shlex.split(os.environ.get('CC', sysconfig.get_config_var('CC')))
command += ['-shared', '-fPIC', '-std=c99', '-Wall', '-Wextra', '-O3', '-flto']
command += ['-I' + sysconfig.get_path('include'), '-I' + str(root / 'runtime/c')]
command += [str(root / 'runtime/python/sdl_native.c'), str(root / 'runtime/c/sdl_wire.c')]
command += ['-o', str(out / ('_sdl_native' + sysconfig.get_config_var('EXT_SUFFIX')))]
subprocess.run(command, check=True)
