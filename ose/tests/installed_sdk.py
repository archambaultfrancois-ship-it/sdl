"""Run after pip installation, outside the checkout import path."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SDL = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[2] / "sdl")).resolve()
PROGRAM = r'''
import json, os, pathlib, sys
if os.environ.get("CHAIN_INSTALLED_TARGET"):
    sys.path.insert(0, os.environ["CHAIN_INSTALLED_TARGET"])
from chain_env.modeling.compiler import build
from chain_env.modeling.native_containers import sdk_root
root = pathlib.Path.cwd()
sdk = sdk_root()
assert 'share/sdl-chain/sdk' in str(sdk), sdk
for asset in ('java/org/sdlchain/Component.java', 'ada/chain_component.ads',
              'client/java/chain_jni.c', 'client/ada/chain_client-typed.adb'):
    assert (sdk / asset).is_file(), asset
(root / 'types.sdl').write_text('message Empty {}')
model = {'version': 1, 'schemas': 'types.sdl', 'components': {
    name: {'inputs': {}, 'outputs': {}, 'trigger': {'mode': 'periodic', 'period_ms': 100}}
    for name in ('jvm', 'native')}, 'subscriptions': []}
deployment = {'version': 1, 'components': {
    'jvm': {'container': {'language': 'java', 'handler': 'Handler.java', 'class': 'Handler'}},
    'native': {'container': {'language': 'ada', 'handler': 'handler_ada.adb', 'unit': 'Handler_Ada'}}}}
(root / 'model.json').write_text(json.dumps(model)); (root / 'deployment.json').write_text(json.dumps(deployment))
build(root / 'model.json', root / 'deployment.json', sys.argv[1], root / 'build')
from chain_env.modeling.client_build import build_java
from chain_env.modeling.ada_client_build import build_ada
build_java(sys.argv[1], root / 'java-client')
build_ada(sys.argv[1], root / 'ada-client')
'''
with tempfile.TemporaryDirectory() as directory:
    # -I removes PYTHONPATH and the current directory from imports.
    subprocess.run([sys.executable, '-I', '-c', PROGRAM, str(SDL)], cwd=directory, check=True)
