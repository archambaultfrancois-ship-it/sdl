import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'gen'))
from generator import MsgParser, canonical_type_descriptor, parse_schemas
from c_backend import generate_c
from rust_backend import generate_rust
from python_backend import generate_python
from java_backend import generate_java
from matlab_backend import generate_matlab
from ada_backend import generate_ada


class SourceSyntaxTests(unittest.TestCase):
   def parse(self, text):
      schema = MsgParser()
      schema.parse_text(text)
      return schema

   def test_whitespace_and_newlines_do_not_change_catalogues(self):
      tokens = ('package', 'nav', '.', 'geometry', ';',
         'const', 'AXES', '=', '3', ';',
         'enum', 'State', '{', 'OFF', '=', '-1', ';', 'ON', '=', '1', ';', '}',
         'message', 'Root', '{', '1', ':', 'required', 'fl64', '[', 'AXES', ']', 'coords',
         'wire', '(', 'bits', '=', '12', ',', 'q', '=', '+1e-1', ',', 'align', '=', '4',
         ',', 'pad_after', '=', '2', ')', ';', 'optional', 'State', 'state', ';',
         'struct', '{', 'int32', 'code', ';', '}', 'metadata', ';', '}')
      expected = self.parse('''package nav.geometry;
enum State { OFF = -1; ON = 1; }
message Root {
 1: required fl64[3] coords wire(bits=12,q=0.1,align=4,pad_after=2);
 optional State state;
 struct { int32 code; } metadata;
}
''')
      for separator in (' ', '\n', '\r\n', '\t', ' // between tokens\n'):
         with self.subTest(separator=repr(separator)):
            actual = self.parse(separator.join(tokens))
            self.assertEqual(canonical_type_descriptor(actual), canonical_type_descriptor(expected))
            self.assertEqual(actual.message_order, expected.message_order)

   def test_compact_declarations_fields_and_anonymous_messages(self):
      schema = self.parse('package nav;const N=2;enum State{ZERO=0;ONE=1;}'
         'message Point{int32 x;}message Pair{1:Point[N]points;'
         '2:optional struct{State state;struct{int16 code;}detail;}metadata;}')
      self.assertEqual(schema.messages['nav.Pair'].fields[0].array_dimensions, [2])
      self.assertEqual(set(schema.messages), {'nav.Point', 'nav.Pair', 'nav.Pair$1', 'nav.Pair$2'})
      self.assertEqual(schema.messages['nav.Pair$1'].fields[1].type_name, 'nav.Pair$2')

   def test_punctuation_can_be_separated_by_whitespace(self):
      schema = self.parse('message\nPoint\n{\n1\n:\nint16\n[\n2\n]\n[\n3\n]\ngrid\n;\n}')
      self.assertEqual(schema.messages['Point'].fields[0].array_dimensions, [2, 3])

   def test_forward_constants_and_decimal_literals(self):
      schema = self.parse('message Matrix { fl32[ROWS][COLS] values; }'
         'const ROWS=002; const COLS=03;')
      self.assertEqual(schema.constants, {'ROWS': 2, 'COLS': 3})
      self.assertEqual(canonical_type_descriptor(schema), b'SDL2|Matrix{1:fl32[2][3]:values;}')

   def test_constant_names_and_comments_do_not_affect_wire_identity(self):
      literal = self.parse('message Point { int16[3] coords; }')
      first = self.parse('const AXES = 3; message Point { int16[AXES] coords; }')
      second = self.parse('// geometry\nconst DIMENSION=3; // axis count\n'
         'message Point { int16 [ DIMENSION ] coords; } // no final newline')
      self.assertEqual(canonical_type_descriptor(literal), canonical_type_descriptor(first))
      self.assertEqual(canonical_type_descriptor(first), canonical_type_descriptor(second))

   def test_invalid_constant_declarations(self):
      for source in ('const N=0;', 'const N=-1;', 'const N=+1;', 'const N=1.5;',
            'const N=1e2;', 'const N=4294967296;', 'const N=٢;', 'const 2N=2;',
            'const N=OTHER;', 'const N=1+1;', 'const N=2', 'const N 2;',
            'const int32=2;', 'message X { const N=2; }', 'const N=2; package nav;'):
         with self.subTest(source=source), self.assertRaises(ValueError):
            self.parse(source)

   def test_duplicate_constant_and_type_names(self):
      for source in ('const N=2;const N=2;', 'const Point=2;message Point{}',
            'message Point{}const Point=2;', 'enum State{ZERO=0;}const State=2;'):
         with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'duplicate declaration name'):
            self.parse(source)

   def test_constants_keep_array_validation_rules(self):
      maximum = self.parse('const N=4294967295;message Bytes{uint8[N]values;}')
      self.assertEqual(maximum.messages['Bytes'].fields[0].array_dimensions, [0xFFFFFFFF])
      for source, error in (
            ('const N=4294967295;message X{uint8[N][2]values;}', 'fixed array wire size exceeds uint32'),
            ('const N=2;message X{string[N]values;}', 'fixed array element has variable wire size'),
            ('const N=2;message X{optional int16[N]values;}', 'fixed arrays require the required modifier'),
            ('message X{int16[MISSING]values;}', 'unknown dimension constant MISSING in X.values')):
         with self.subTest(source=source), self.assertRaisesRegex(ValueError, error):
            self.parse(source)

   def test_package_constant_resolution_has_no_global_fallback(self):
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         (root / 'global.sdl').write_text('const AXES=2;')
         current = root / 'local.sdl'
         current.write_text('package nav;message Point{int16[AXES]coords;}')
         with self.assertRaisesRegex(ValueError, 'unknown dimension constant nav.AXES'):
            parse_schemas(str(root))
         current.write_text('package nav;message Point{int16[.AXES]coords;}')
         schema = parse_schemas(str(root))[1][2]
         self.assertEqual(schema.messages['nav.Point'].fields[0].array_dimensions, [2])

   def test_external_message_dimensions_are_resolved_before_size_checks(self):
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         (root / 'a.sdl').write_text('package a;const COUNT=2;message Root{b.Point[COUNT]points;}')
         (root / 'z.sdl').write_text('package b;const AXES=3;message Point{int16[AXES]coords;}')
         schemas = parse_schemas(str(root))
         self.assertEqual(schemas[0][2].fixed_wire_size('a.Root'), 12)
         self.assertIn(b'b.Point{1:int16[3]:coords;}', canonical_type_descriptor(schemas[0][2]))

   def test_duplicate_constant_names_across_files_and_packages(self):
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         (root / 'a.sdl').write_text('package nav;const AXES=2;')
         other = root / 'b.sdl'
         other.write_text('package nav;const AXES=2;')
         with self.assertRaisesRegex(ValueError, 'duplicate declaration name across SDL files: nav.AXES'):
            parse_schemas(str(root))
         other.write_text('package nav;message AXES{}')
         with self.assertRaisesRegex(ValueError, 'duplicate declaration name across SDL files: nav.AXES'):
            parse_schemas(str(root))
         other.write_text('package radio;const AXES=2;')
         self.assertEqual(len(parse_schemas(str(root))), 2)

   def test_all_six_backends_emit_identical_files_for_literals_and_constants(self):
      symbolic = {
         'globals.sdl': 'const AXES=2;message Global{int16[AXES]coords;}',
         'foreign.sdl': 'package radio;const AXES=3;message Point{int16[AXES]coords;}',
         'local.sdl': 'package nav;const ROWS=2;message Root{radio.Point[ROWS]points;'
            'int8[.AXES][radio.AXES]grid;struct{uint8[ROWS]bytes;}metadata;}',
      }
      literal = {
         'globals.sdl': 'message Global {\n int16[2] coords;\n}\n',
         'foreign.sdl': 'package radio;\nmessage Point {\n int16[3] coords;\n}\n',
         'local.sdl': 'package nav;\nmessage Root {\n radio.Point[2] points;\n'
            ' int8[2][3] grid;\n struct {\n uint8[2] bytes;\n } metadata;\n}\n',
      }
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         inputs = root / 'source'; inputs.mkdir()
         for backend in (generate_c, generate_rust, generate_python, generate_java, generate_matlab, generate_ada):
            with self.subTest(backend=backend.__name__):
               literal_output = root / 'literal' / backend.__name__
               symbolic_output = root / 'symbolic' / backend.__name__
               for filename, text in literal.items():
                  (inputs / filename).write_text(text)
               backend(str(inputs), str(literal_output))
               for filename, text in symbolic.items():
                  (inputs / filename).write_text(text)
               backend(str(inputs), str(symbolic_output))
               def outputs(path):
                  return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}
               self.assertEqual(outputs(literal_output), outputs(symbolic_output))

   def test_missing_delimiters_and_malformed_wire_clauses(self):
      for source in ('message X{int32 a int32 b;}',
            'message X{int32 a wire(bits=1\n2);}', 'message X{int32 a wire(bits=);}',
            'message X{int32 a wire();}', 'message X{int32 a wire(bits=1,);}',
            'enum X{A=1 B=2;}', 'message X{int32[2 coords;}'):
         with self.subTest(source=source), self.assertRaises(ValueError):
            self.parse(source)
      with self.assertRaisesRegex(ValueError, 'line 3, column 1'):
         self.parse('message X {\n int32 value\n}')
      with self.assertRaisesRegex(ValueError, 'line 2, column 14'):
         self.parse('message X {\n int32 value wire ( unknown = 2 );\n}')


if __name__ == '__main__':
   unittest.main()
