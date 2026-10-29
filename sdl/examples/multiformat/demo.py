#!/usr/bin/env python3
"""Symmetric schema conversions: SDL -> mixed -> SDL and mixed -> SDL -> mixed."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


EXAMPLE = Path(__file__).resolve().parent
SDL_ROOT = EXAMPLE.parents[1]
LANGUAGES = ('-c', '-rust', '-python', '-java', '-matlab', '-ada')
STAGES = ('source', 'converted', 'restored')
MIXED_UNITS = {'common': 'sdl', 'position': 'xsd', 'sensor': 'json', 'reading': 'yaml'}


def compile_schemas(options, source, output):
   subprocess.run([sys.executable, str(SDL_ROOT / 'sdlc/generator.py'),
      *options, str(source), str(output)], check=True)


def to_sdl(source, output):
   compile_schemas(('-sdl',), source, output)
   return output / 'sdl'


def to_mixed(source, output):
   # Use the same unit-to-format mapping in both directions.
   formats = output / 'formats'
   compile_schemas(('-sdl', '-json', '-yaml', '-xsd'), source, formats)
   mixed = output / 'mixed'
   for unit, kind in MIXED_UNITS.items():
      suffix = '.sdl' if kind == 'sdl' else '.sdl.' + kind
      original = formats / kind / 'multiformat' / (unit + suffix)
      target = mixed / 'multiformat' / original.name
      target.parent.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(original, target)
   return mixed


def generated_files(directory):
   files = {str(path.relative_to(directory)): path.read_bytes()
      for path in directory.rglob('*') if path.is_file() and '__pycache__' not in path.parts}
   if not files:
      raise ValueError('Missing bindings in ' + str(directory))
   return files


def run_stage(direction, stage, output):
   generated = output / 'generated' / stage / 'python'
   sys.path[:0] = [str(SDL_ROOT / 'runtime/python'), str(generated)]
   from sdl_runtime import catalogue_id, decode, description, encode, prepare
   from multiformat import common, position, reading, sensor

   value = reading.Reading(
      sensor=sensor.Sensor(id=17, location=position.Position(x=1.5, y=-2.0),
         label='vibration'),
      state=common.State.READY,
      acceleration=[0.25, -9.75, 1.5],
      samples=[0.25, -1.5, 3.75],
      note='Same SDL2 bytes from every source format.')
   local_types = [reading.Reading, sensor.Sensor, position.Position]
   catalogue = description(local_types)
   context = prepare(catalogue, local_types)
   payload = encode(context, value)
   restored = decode(context, payload)
   if restored != value or encode(context, restored) != payload:
      raise ValueError(direction + '/' + stage + ': binary round trip changed the message')

   result = output / 'results' / stage
   result.mkdir(parents=True, exist_ok=True)
   (result / 'catalogue.sdl2').write_bytes(catalogue.encode('utf-8'))
   (result / 'reading.bin').write_bytes(payload)
   (result / 'reading.hex').write_text(payload.hex() + '\n', encoding='ascii')
   print('  {}/{}: catalogue={}, payload={} bytes, encode/decode OK'.format(
      direction, stage, catalogue_id(catalogue), len(payload)), flush=True)


def run_workflow(direction, build):
   output = build / direction
   source_kind = 'sdl' if direction == 'export' else 'mixed'
   source = EXAMPLE / 'schemas' / source_kind
   first, second = (to_mixed, to_sdl) if direction == 'export' else (to_sdl, to_mixed)
   route = 'SDL -> mixed -> SDL' if direction == 'export' else 'mixed -> SDL -> mixed'
   print(direction + ': ' + route, flush=True)
   converted = first(source, output)
   restored = second(converted, output)

   expected = None
   for stage, schemas in zip(STAGES, (source, converted, restored)):
      generated = output / 'generated' / stage
      compile_schemas(LANGUAGES, schemas, generated)
      actual = generated_files(generated)
      if expected is None:
         expected = actual
      elif actual != expected:
         changed = sorted(name for name in set(expected) | set(actual)
            if expected.get(name) != actual.get(name))
         raise ValueError(direction + '/' + stage + ': generated bindings differ: ' + ', '.join(changed))
      # One interpreter per stage avoids caching another stage's module names.
      subprocess.run([sys.executable, str(Path(__file__).resolve()), direction,
         '--build-dir', str(build), '--stage', stage], check=True)
   for stage in STAGES[1:]:
      for name in ('catalogue.sdl2', 'reading.bin'):
         expected = (output / 'results/source' / name).read_bytes()
         actual = (output / 'results' / stage / name).read_bytes()
         if actual != expected:
            raise ValueError(direction + '/' + stage + ': ' + name + ' differs from source')
   print(direction + ': PASS (3 stages; identical bindings in 6 languages, catalogues and binary payloads)', flush=True)


def main():
   parser = argparse.ArgumentParser(description=__doc__)
   parser.add_argument('direction', choices=('export', 'import'))
   parser.add_argument('--build-dir', type=Path, default=EXAMPLE / 'build')
   parser.add_argument('--stage', choices=STAGES, help=argparse.SUPPRESS)
   args = parser.parse_args()
   build = args.build_dir.resolve()
   try:
      if args.stage:
         run_stage(args.direction, args.stage, build / args.direction)
      else:
         run_workflow(args.direction, build)
   except (ValueError, OSError, subprocess.CalledProcessError) as error:
      parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
   main()
