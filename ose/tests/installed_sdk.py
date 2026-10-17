"""Verify the installed package, CLI and SDK assets outside the source path.

TEST_LANGUAGES selects build toolchains, as for make check; all are used by default.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SDL = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[2] / "sdl")).resolve()
LANGUAGES = set(os.environ.get('TEST_LANGUAGES', 'python,c,rust,java,ada').split(','))
if not LANGUAGES or LANGUAGES - {'python', 'c', 'rust', 'java', 'ada'}:
    raise ValueError('unknown TEST_LANGUAGES entry')
PROGRAM = r'''
import importlib.metadata, json, os, pathlib, sys
if os.environ.get("OSE_INSTALLED_TARGET"):
    sys.path.insert(0, os.environ["OSE_INSTALLED_TARGET"])
    os.environ["PYTHONPATH"] = os.environ["OSE_INSTALLED_TARGET"]
else:
    os.environ.pop("PYTHONPATH", None)
import ose
from ose.modeling.compiler import build
from ose.modeling.native_containers import sdk_root
distribution = importlib.metadata.distribution('ose')
assert distribution.metadata['Name'] == 'ose'
assert [(entry.name, entry.value) for entry in distribution.entry_points
        if entry.group == 'console_scripts'] == [('ose-ctl', 'ose.cli:main')]
assert not any(str(path).startswith('chain_env/') for path in distribution.files)
assert pathlib.Path(ose.__file__).resolve().is_relative_to(pathlib.Path(distribution.locate_file('')).resolve())
root = pathlib.Path.cwd()
sdk = sdk_root()
assert 'share/ose/sdk' in str(sdk), sdk
for asset in ('java/org/sdlchain/Component.java', 'ada/chain_component.ads',
              'client/java/chain_jni.c', 'client/ada/chain_client-typed.adb'):
    assert (sdk / asset).is_file(), asset
(root / 'probe.sdl').write_text('message Empty {}')
languages = sys.argv[2].split(',')
containers = {
    'python': {'language': 'python', 'handler': 'handler.py'},
    'c': {'language': 'c', 'handler': 'handler.c'},
    'rust': {'language': 'rust', 'handler': 'handler.rs'},
    'java': {'language': 'java', 'handler': 'Handler.java', 'class': 'Handler'},
    'ada': {'language': 'ada', 'handler': 'handler_ada.adb', 'unit': 'Handler_Ada'},
}
model = {'version': 1, 'schemas': 'probe.sdl', 'components': {
    name: {'inputs': {}, 'outputs': {}, 'trigger': {'mode': 'periodic', 'period_ms': 100}}
    for name in languages}, 'subscriptions': []}
deployment = {'version': 1, 'components': {
    name: {'container': containers[name]} for name in languages}}
(root / 'model.json').write_text(json.dumps(model)); (root / 'deployment.json').write_text(json.dumps(deployment))
build(root / 'model.json', root / 'deployment.json', sys.argv[1], root / 'build')
from ose.modeling.client_build import build_java
from ose.modeling.ada_client_build import build_ada
if 'java' in languages:
    build_java(sys.argv[1], root / 'java-client')
if 'ada' in languages:
    build_ada(sys.argv[1], root / 'ada-client')
print('Installed ose package, ose-ctl entry point and SDK builds passed: ' + ', '.join(languages))
'''
with tempfile.TemporaryDirectory() as directory:
    # -I removes PYTHONPATH and the current directory from imports.
    subprocess.run([sys.executable, '-I', '-c', PROGRAM, str(SDL), ','.join(sorted(LANGUAGES))], cwd=directory, check=True)
