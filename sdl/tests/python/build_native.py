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
if sys.platform == 'android' or hasattr(sys, 'getandroidapilevel'):
    # Android's loader requires an explicit dependency on libpython (PEP 738).
    libdir = sysconfig.get_config_var('LIBDIR')
    library = sysconfig.get_config_var('LDLIBRARY')
    if not libdir or not library or not (Path(libdir) / library).is_file():
        raise RuntimeError('Android native build requires the invoking CPython shared library')
    command += [str(Path(libdir) / library), '-Wl,-rpath,' + libdir,
                '-Wl,--no-undefined']
command += ['-o', str(out / ('_sdl_native' + sysconfig.get_config_var('EXT_SUFFIX')))]
subprocess.run(command, check=True)

# A successful link must also produce an extension this interpreter can load.
environment = dict(os.environ)
environment['PYTHONPATH'] = str(out.resolve()) + os.pathsep + environment.get('PYTHONPATH', '')
subprocess.run([sys.executable, '-c',
               'import _sdl_native; print("SDL native extension loaded (API {})".format(_sdl_native.API_VERSION))'],
               env=environment, check=True)
