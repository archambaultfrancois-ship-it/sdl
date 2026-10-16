import pathlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'gen'), str(ROOT / 'runtime/python')]
from generator import MsgParser, canonical_type_descriptor, parse_schemas, c_identifier
from python_backend import generate_python
from c_backend import generate_c, CBackend
from rust_backend import generate_rust
from java_backend import generate_java
from ada_backend import generate_ada
from matlab_backend import generate_matlab, MatlabBackend
from sdl_runtime import prepare, CodecError


class PackageTests(unittest.TestCase):
   def test_package_qualifies_messages_enums_and_anonymous_types(self):
      schema = MsgParser()
      schema.parse_text('package nav.geometry;\nenum State {\n ZERO = 0;\n}\nmessage Point {\n State state;\n struct {\n int32 x;\n } detail;\n}\n')
      self.assertEqual(schema.package, 'nav.geometry')
      self.assertEqual(set(schema.messages), {'nav.geometry.Point', 'nav.geometry.Point$1'})
      self.assertEqual(schema.messages['nav.geometry.Point'].fields[0].type_name, 'nav.geometry.State')
      self.assertIn(b'nav.geometry.Point$1:detail;', canonical_type_descriptor(schema))

   def test_invalid_package_declarations(self):
      for source in ('package a..b;', 'package .a;', 'package a.;', 'package 1a;',
            'package a', 'package a;\npackage a;',
            'message X {\n int32 x;\n}\npackage a;', 'message X {\n package a;\n}'):
         with self.subTest(source=source), self.assertRaises(ValueError):
            MsgParser().parse_text(source)

   def test_no_implicit_global_fallback(self):
      with tempfile.TemporaryDirectory() as directory:
         root = pathlib.Path(directory)
         (root / 'global.sdl').write_text('message Point {\n int32 x;\n}\n')
         (root / 'local.sdl').write_text('package nav;\nmessage Sample {\n Point value;\n}\n')
         with self.assertRaisesRegex(ValueError, 'unknown type nav.Point'):
            parse_schemas(directory)
         (root / 'local.sdl').write_text('package nav;\nmessage Sample {\n .Point value;\n}\n')
         schemas = parse_schemas(directory)
         nav = next(schema for _, _, schema in schemas if schema.package == 'nav')
         self.assertEqual(nav.messages['nav.Sample'].fields[0].type_name, 'Point')
         self.assertIn(b'Point{', canonical_type_descriptor(nav))

   def test_duplicate_types_rejected_in_same_package(self):
      with tempfile.TemporaryDirectory() as directory:
         root = pathlib.Path(directory)
         source = 'package nav;\nmessage Point {\n int32 x;\n}\n'
         (root / 'a.sdl').write_text(source)
         (root / 'b.sdl').write_text(source)
         with self.assertRaisesRegex(ValueError, 'duplicate type name'):
            parse_schemas(directory)
         (root / 'b.sdl').write_text(source.replace('package nav;', 'package radio;'))
         self.assertEqual(len(parse_schemas(directory)), 2)

   def test_same_package_cross_file_and_explicit_local_reference(self):
      with tempfile.TemporaryDirectory() as directory:
         root = pathlib.Path(directory)
         (root / 'a.sdl').write_text('package nav;\nmessage Point {\n int32 x;\n}\n')
         (root / 'b.sdl').write_text('package nav;\nmessage Sample {\n Point short_name;\n nav.Point full_name;\n}\n')
         schema = parse_schemas(directory)[1][2]
         self.assertEqual([f.type_name for f in schema.messages['nav.Sample'].fields], ['nav.Point', 'nav.Point'])

   def test_cross_file_cycles_and_unknown_references_rejected(self):
      with tempfile.TemporaryDirectory() as directory:
         root = pathlib.Path(directory)
         (root / 'a.sdl').write_text('package a;\nmessage X {\n b.Y y;\n}\n')
         (root / 'b.sdl').write_text('package b;\nmessage Y {\n a.X x;\n}\n')
         with self.assertRaisesRegex(ValueError, 'recursive message dependency'):
            parse_schemas(directory)
         (root / 'b.sdl').write_text('package b;\nmessage Z {\n int32 x;\n}\n')
         with self.assertRaisesRegex(ValueError, 'unknown type b.Y'):
            parse_schemas(directory)

   def test_c_name_encoding_keeps_package_boundaries(self):
      self.assertNotEqual(c_identifier('a.b_c.Point'), c_identifier('a_b.c.Point'))
      self.assertNotEqual(c_identifier('a.Point'), c_identifier('A.Point'))

   def test_unused_c_enums_compile_without_warnings_and_remain_available(self):
      compiler = shlex.split(os.environ.get('CC', 'cc'))
      if not compiler or not shutil.which(compiler[0]):
         self.skipTest('C compiler is unavailable')
      environment = dict(os.environ, CCACHE_DISABLE='1')
      for package in ('', 'nav.geometry'):
         with self.subTest(package=package):
            schema = MsgParser()
            schema.parse_text(('package ' + package + ';\n' if package else '') +
               'enum State {\n ZERO = 0;\n}\nmessage Point {\n int32 x;\n}\n')
            name = package + '.State' if package else 'State'
            self.assertIn('#' + name + '{ZERO=0;}', canonical_type_descriptor(schema).decode())
            point = package + '.Point' if package else 'Point'
            with tempfile.TemporaryDirectory() as directory:
               root = pathlib.Path(directory)
               # Using the public enum must compile even when no message uses it.
               (root / 'client.c').write_text('#include "shapes.h"\nint enum_available(void) { ' +
                  c_identifier(name) + ' value = ' + c_identifier(name).upper() +
                  '_ZERO; return (int)value; }\n')
               for referenced in (False, True):
                  with self.subTest(referenced=referenced):
                     if referenced:
                        # Fixed arrays also require a usable enum descriptor.
                        schema.messages[point].fields[0].type_name = name
                        schema.messages[point].fields[0].array_dimensions = [2]
                        schema.validate()
                     header, source = CBackend(schema).generate_files('shapes')
                     (root / 'shapes.h').write_text(header)
                     (root / 'shapes.c').write_text(source)
                     for unit in ('shapes', 'client'):
                        subprocess.run(compiler + ['-std=c99', '-Wall', '-Wextra', '-Werror',
                           '-I' + str(ROOT / 'runtime/c'), '-I' + str(root), '-c',
                           str(root / (unit + '.c')), '-o', str(root / (unit + '.o'))],
                           check=True, capture_output=True, text=True, env=environment)

   def test_packaged_matlab_helper_does_not_shadow_constructor(self):
      schema = MsgParser()
      schema.parse_text('package nav;\nmessage sdl_fixed_encode {\n int32 x;\n}\n')
      source = MatlabBackend(schema).generate_message('nav.sdl_fixed_encode')
      self.assertIn('function b=sdl_fixed_encode_(ctx,v)', source)

   def test_native_unit_cycles_are_rejected_before_emission(self):
      with tempfile.TemporaryDirectory() as directory:
         root = pathlib.Path(directory)
         (root / 'a.sdl').write_text('package a;\nmessage Leaf {\n int32 x;\n}\nmessage Root {\n b.Leaf leaf;\n}\n')
         (root / 'b.sdl').write_text('package b;\nmessage Leaf {\n int32 x;\n}\nmessage Root {\n a.Leaf leaf;\n}\n')
         self.assertEqual(len(parse_schemas(directory)), 2)
         for backend in (generate_c, generate_ada):
            with self.subTest(backend=backend.__name__), self.assertRaisesRegex(ValueError, 'circular schema file dependency'):
               backend(directory, str(root / 'output'))

   def test_nested_regeneration_preserves_unrelated_files(self):
      with tempfile.TemporaryDirectory() as directory:
         root = pathlib.Path(directory); schema = root / 'shape.sdl'; output = root / 'output'
         schema.write_text('package nav.geometry;\nmessage Point {\n int32 x;\n}\n')
         generate_python(str(schema), str(output))
         helper = output / 'nav/geometry/helper.py'; helper.write_text('answer = 42\n')
         schema.write_text('package radio;\nmessage Point {\n int32 x;\n}\n')
         generate_python(str(schema), str(output))
         self.assertFalse((output / 'nav/geometry/shape.py').exists())
         self.assertTrue((output / 'radio/shape.py').exists())
         self.assertEqual(helper.read_text(), 'answer = 42\n')

   def test_runtime_qualified_name_grammar(self):
      for name in ('nav.Point', 'nav.geometry.Point$1'):
         self.assertEqual(prepare('SDL2|' + name + '{1:int32:x;}').names, (name,))
      for name in ('.Point', 'nav..Point', 'nav.Point.'):
         with self.subTest(name=name), self.assertRaises(CodecError):
            prepare('SDL2|' + name + '{1:int32:x;}')
      for row in ('1:int32:nav.x;', '1:int32:x;', '1:nav..Point:x;'):
         if row == '1:int32:x;':
            continue
         with self.subTest(row=row), self.assertRaises(CodecError):
            prepare('SDL2|nav.Point{' + row + '}')

   def test_native_package_keywords_and_module_collisions(self):
      with tempfile.TemporaryDirectory() as directory:
         root = pathlib.Path(directory); schema = root / 'input'; schema.mkdir()
         for backend, keyword in ((generate_python, 'class'), (generate_rust, 'mod'),
               (generate_java, 'class'), (generate_ada, 'begin'), (generate_matlab, 'function')):
            (schema / 'shapes.sdl').write_text('package ' + keyword + ';\nmessage Point {\n int32 x;\n}\n')
            with self.subTest(backend=backend.__name__), self.assertRaises(ValueError):
               backend(str(schema), str(root / 'output'))
         (schema / 'shapes.sdl').write_text('message Point {\n int32 x;\n}\n')
         (schema / 'another.sdl').write_text('package shapes;\nmessage Other {\n int32 x;\n}\n')
         for backend in (generate_python, generate_rust, generate_java):
            with self.subTest(backend=backend.__name__), self.assertRaisesRegex(ValueError, 'package conflicts'):
               backend(str(schema), str(root / 'output'))


if __name__ == '__main__':
   unittest.main()
