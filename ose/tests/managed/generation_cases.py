"""Exercise managed build workflows through their public model/compiler boundary."""
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from ose.execution.ipc import frame
from ose.modeling.compiler import build, compile_plan


def check_generation(case, root, sdl, language):
    root = Path(root) / 'project with spaces'
    root.mkdir()
    (root / 'sample.sdl').write_text('package demo; message Sample {1: uint64 sequence;}')
    (root / 'details.sdl').write_text('package other; message Details {1: string label;}')
    model = {'version': 1, 'schemas': 'sample.sdl', 'components': {
        'source': {'inputs': {}, 'outputs': {
            'sample': {'root': 'demo.Sample'},
            'details': {'root': 'other.Details', 'schema': 'details.sdl'}},
            'trigger': {'mode': 'periodic', 'period_ms': 10}}}, 'subscriptions': []}
    container = ({'language': 'java', 'handler': 'handlers/NewHandler.java', 'class': 'app.components.NewHandler'}
                 if language == 'java' else
                 {'language': 'ada', 'handler': 'handlers/app-handlers-main_handler.adb', 'unit': 'App.Handlers.Main_Handler'})
    deployment = {'version': 1, 'components': {'source': {'container': container}}}
    model_path, deployment_path = root / 'model.json', root / 'deployment.json'
    model_path.write_text(json.dumps(model))
    deployment_path.write_text(json.dumps(deployment))
    destination = root / 'generated build'
    handler = root / container['handler']
    # Validation must not invoke a native toolchain or scaffold application code.
    unavailable = {tool: '/unavailable/chain-test-' + tool.lower()
                   for tool in ('JAVA', 'JAVAC', 'GNATMAKE', 'CC', 'CARGO')}
    with patch.dict(os.environ, unavailable):
        plan = compile_plan(model_path, deployment_path, sdl, destination)
    case.assertFalse(handler.exists())
    case.assertEqual(set(plan['components']['source']['container']['types']), {'demo.Sample', 'other.Details'})

    path = build(model_path, deployment_path, sdl, destination)
    files = [handler] if language == 'java' else [handler, handler.with_suffix('.ads')]
    original = {p: p.read_bytes() for p in files}
    if language == 'ada':
        for parent in ('app.ads', 'app-handlers.ads'):
            case.assertTrue((handler.parent / parent).is_file())
    build(model_path, deployment_path, sdl, destination)
    case.assertEqual({p: p.read_bytes() for p in files}, original)
    command = json.loads(path.read_text())['components']['source']['command']
    result = subprocess.run(command, input=frame('I', b'{}') + frame('T') + frame('X'),
                            capture_output=True, timeout=15)
    case.assertEqual(result.returncode, 0, result.stderr.decode())
    case.assertEqual(result.stdout, frame('R', b'\1') + frame('F'))

    if language == 'ada':
        handler.rename(handler.with_suffix('.saved'))
        with case.assertRaisesRegex(ValueError, 'partial pair'):
            build(model_path, deployment_path, sdl, destination)
        case.assertFalse(handler.exists())
        case.assertEqual(handler.with_suffix('.ads').read_bytes(), original[handler.with_suffix('.ads')])
