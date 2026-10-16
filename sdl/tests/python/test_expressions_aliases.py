import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'gen'), str(ROOT / 'runtime/python')]
from generator import MsgParser, canonical_type_descriptor, parse_schemas
from c_backend import generate_c
from rust_backend import generate_rust
from python_backend import generate_python
from java_backend import generate_java
from matlab_backend import generate_matlab
from ada_backend import generate_ada
from sdl_runtime import catalogue_id


class ExpressionsAliasesTests(unittest.TestCase):
   def parse(self, text):
      schema = MsgParser()
      schema.parse_text(text)
      return schema

   def test_expression_precedence_associativity_and_unary_signs(self):
      cases = {'1+2*3': 7, '(1+2)*3': 9, '10-3-2': 5, '8/2/2': 2,
         '2*-(-3)': 6, '--2': 2, '+2': 2, '5+-2': 3, '5--2': 7,
         '0002*(3+1)': 8, '(2-5)*(-2)': 6, '(-8)/(-2)': 4,
         '2 /* note */ +\n3 * ( 4 - 1 )': 11}
      for expression, expected in cases.items():
         with self.subTest(expression=expression):
            schema = self.parse('const N=' + expression + ';message X{int8[N]x;}')
            self.assertEqual(schema.constants['N'], expected)
            self.assertEqual(schema.messages['X'].fields[0].array_dimensions, [expected])

   def test_forward_references_shared_dependencies_and_unused_constants(self):
      schema = self.parse('const TOTAL=LEFT+RIGHT;const LEFT=BASE*2;'
         'const RIGHT=BASE+1;const COPY=TOTAL;const BASE=3;')
      self.assertEqual(schema.constants, {'TOTAL': 10, 'LEFT': 6, 'RIGHT': 4, 'COPY': 10, 'BASE': 3})

   def test_arithmetic_errors_are_rejected_even_when_unused(self):
      cases = {'2/0': 'division by zero', '3/2': 'non-integer division',
         '(-3)/2': 'non-integer division', '0': 'positive uint32',
         '1-2': 'positive uint32', '4294967296': 'exceeds uint32 magnitude',
         '4294967295+1-1': 'exceeds uint32 magnitude',
         '65536*65536': 'exceeds uint32 magnitude', 'UNKNOWN+1': 'unknown dimension constant UNKNOWN'}
      for expression, error in cases.items():
         with self.subTest(expression=expression), self.assertRaisesRegex(ValueError, error):
            self.parse('const N=' + expression + ';')
      schema = self.parse('const N=4294967295-0;message X{uint8[N]x;}')
      self.assertEqual(schema.constants['N'], 0xFFFFFFFF)

   def test_malformed_and_unsupported_expressions_are_rejected(self):
      for expression in ('', '()', '(2', '2)', '1+', '*2', '2 3', '2(3)',
            '2**3', '5%2', '1.5', '1e2', '0x10', '1_000', '2+/** misplaced */3'):
         with self.subTest(expression=expression), self.assertRaises(ValueError):
            self.parse('const N=' + expression + ';')
      # Expressions belong to const declarations, not inline dimensions or wire options.
      for source in ('message X{int8[1+1]x;}', 'message X{int8 x wire(bits=1+1);}'):
         with self.subTest(source=source), self.assertRaises(ValueError):
            self.parse(source)

   def test_constant_cycles_and_missing_references(self):
      for source in ('const A=A;', 'const A=B+1;const B=C;const C=A*2;'):
         with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'cyclic dimension constant'):
            self.parse(source)
      with self.assertRaisesRegex(ValueError, 'unknown dimension constant Missing'):
         self.parse('const A=Missing;')

   def test_long_dependency_chains_and_expression_nesting(self):
      constants = ''.join('const N%d=N%d;' % (i, i+1) for i in range(1500)) + 'const N1500=2;'
      self.assertEqual(self.parse(constants).constants['N0'], 2)
      aliases = ''.join('alias T%d=T%d;' % (i, i+1) for i in range(1500)) + 'alias T1500=int16;'
      self.assertEqual(self.parse(aliases + 'message X{T0 x;}').messages['X'].fields[0].type_name, 'int16')
      self.assertEqual(self.parse('const N=' + '(' * 64 + '2' + ')' * 64 + ';').constants['N'], 2)
      with self.assertRaisesRegex(ValueError, 'constant expression nesting exceeds 64'):
         self.parse('const N=' + '(' * 65 + '2' + ')' * 65 + ';')
      # Flat expressions are evaluated iteratively as well.
      self.assertEqual(self.parse('const N=' + '+'.join(['1'] * 1500) + ';').constants['N'], 1500)

   def test_aliases_resolve_builtins_messages_enums_and_chains(self):
      schema = self.parse('alias Stamp=uint64;alias Place=Position;alias Position=Point;'
         'alias Status=State;alias Text=string;message Point{int16 x;}'
         'enum State{OFF=0;ON=1;}message Root{Stamp time;Place position;'
         'optional Status state;repeated Text labels;struct{Position[2]points;}nested;}')
      self.assertEqual([f.type_name for f in schema.messages['Root'].fields],
         ['uint64', 'Point', 'State', 'string', 'Root$1'])
      self.assertEqual(schema.messages['Root$1'].fields[0].type_name, 'Point')
      self.assertNotIn(b'Place', canonical_type_descriptor(schema))

   def test_alias_target_and_syntax_errors(self):
      for source, error in (
            ('alias A=A;', 'cyclic type alias'),
            ('alias A=B;alias B=A;', 'cyclic type alias'),
            ('alias A=Missing;', 'unknown type Missing in alias A'),
            ('const N=2;alias A=N;', 'unknown type N in alias A'),
            ('alias A=int16[2];', 'invalid type alias declaration'),
            ('alias A=struct{int16 x;};', 'invalid type alias declaration'),
            ('alias A=int16;package nav;', 'misplaced package'),
            ('alias int16=uint16;', 'built-in type')):
         with self.subTest(source=source), self.assertRaisesRegex(ValueError, error):
            self.parse(source)

   def test_aliases_share_namespace_with_types_and_constants(self):
      for source in ('alias A=int8;alias A=int16;', 'alias A=int8;const A=2;',
            'const A=2;alias A=int8;', 'alias A=int8;message A{}',
            'message A{}alias A=int8;', 'alias A=int8;enum A{ZERO=0;}',
            'enum A{ZERO=0;}alias A=int8;'):
         with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'duplicate declaration name'):
            self.parse(source)

   def test_alias_expansion_keeps_wire_and_array_validation(self):
      cases = [('alias Text=string;message X{Text[2]x;}', 'variable wire size'),
         ('alias Text=string;message X{packed Text x;}', 'fixed wire size'),
         ('alias Number=uint16;message X{Number x wire(bits=17);}', 'wire bits must fit'),
         ('alias Self=X;message X{Self x;}', 'recursive message dependency'),
         ('alias Number=int16;message X{optional Number[2]x;}', 'required modifier')]
      for source, error in cases:
         with self.subTest(source=source), self.assertRaisesRegex(ValueError, error):
            self.parse(source)
      self.parse('alias Status=State;enum State{ZERO=0;}message X{Status x wire(bits=2);}')

   def test_package_and_cross_file_resolution_and_dependencies(self):
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         (root / 'a.sdl').write_text('package nav;alias Position=helpers.Place;'
            'const N=helpers.ROWS*.AXES;message Root{Position[N]points;}')
         (root / 'b.sdl').write_text('package helpers;alias Place=radio.Point;const ROWS=.AXES+1;')
         (root / 'c.sdl').write_text('package radio;const COUNT=2;'
            'alias Coordinate=int16;message Point{Coordinate[COUNT]x;}')
         (root / 'globals.sdl').write_text('const AXES=2;alias GlobalPoint=radio.Point;')
         schemas = parse_schemas(str(root)); schema = schemas[0][2]
         self.assertEqual(schema.constants['nav.N'], 6)
         self.assertEqual(schema.messages['nav.Root'].fields[0].type_name, 'radio.Point')
         self.assertEqual(schema.fixed_wire_size('nav.Root'), 24)
         self.assertEqual(schema.dependencies(), [('radio', 'c')])
         (root / 'a.sdl').write_text('package nav;message Root{.GlobalPoint[2]points;}')
         self.assertEqual(parse_schemas(str(root))[0][2].fixed_wire_size('nav.Root'), 8)

   def test_unqualified_references_never_fall_back_to_global(self):
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         (root / 'global.sdl').write_text('const N=2;alias Scalar=int16;')
         local = root / 'local.sdl'
         for source, error in (
               ('package nav;const TOTAL=N*2;', 'unknown dimension constant nav.N'),
               ('package nav;alias Value=Scalar;', 'unknown type nav.Scalar'),
               ('package nav;message X{Scalar x;}', 'unknown type nav.Scalar')):
            local.write_text(source)
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, error):
               parse_schemas(str(root))
         local.write_text('package nav;const TOTAL=.N*2;alias Value=.Scalar;message X{Value[TOTAL]x;}')
         self.assertEqual(parse_schemas(str(root))[1][2].fixed_wire_size('nav.X'), 8)

   def test_cross_file_cycles_and_duplicate_declarations(self):
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         a, b = root / 'a.sdl', root / 'b.sdl'
         for left, right, error in (
               ('package a;const N=b.N;', 'package b;const N=a.N;', 'cyclic dimension constant'),
               ('package a;alias T=b.T;', 'package b;alias T=a.T;', 'cyclic type alias'),
               ('package a;alias T=int16;', 'package a;alias T=int32;', 'duplicate declaration name'),
               ('package a;alias T=int16;', 'package a;message T{}', 'duplicate declaration name'),
               ('package a;message T{}', 'package a;alias T=int16;', 'duplicate declaration name'),
               ('package a;alias T=int16;', 'package a;const T=2;', 'duplicate declaration name'),
               ('package a;const T=2;', 'package a;alias T=int16;', 'duplicate declaration name')):
            a.write_text(left); b.write_text(right)
            with self.subTest(left=left, right=right), self.assertRaisesRegex(ValueError, error):
               parse_schemas(str(root))

   def test_alias_and_expression_documentation_is_retained(self):
      schema = self.parse('/** Axis count. */const N=(1+1)*2;\n'
         '/** Signed coordinate. */alias Scalar=int16;\n'
         'alias Position=Point;/** Position synonym. */\nmessage Point{Scalar[N]x;}')
      self.assertEqual(schema.constant_docs['N'], 'Axis count.')
      self.assertEqual(schema.aliases['Scalar'].doc, 'Signed coordinate.')
      self.assertEqual(schema.aliases['Position'].doc, 'Position synonym.')
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary); path = root / 'schema.sdl'
         path.write_text('const N=2*2;/** Axis count. */\n'
            'alias Scalar=int16;/** Signed coordinate. */\nmessage Point{Scalar[N]x;}')
         for backend in (generate_c, generate_rust, generate_python, generate_java, generate_matlab, generate_ada):
            with self.subTest(backend=backend.__name__):
               output = root / backend.__name__; backend(str(path), str(output))
               code = '\n'.join(p.read_text() for p in output.rglob('*') if p.is_file())
               self.assertIn('Signed coordinate.', code)
               self.assertIn('N = 4', code)

   def test_all_six_backends_and_hash_match_expanded_schema(self):
      symbolic = {
         'globals.sdl': 'const AXES=1+1;alias Scalar=int16;message Global{Scalar[AXES]coords;}',
         'foreign.sdl': 'package radio;const AXES=.AXES+1;alias Number=.Scalar;'
            'message Point{Number[AXES]coords;}enum State{OFF=0;ON=1;}',
         'local.sdl': 'package nav;const ROWS=(radio.AXES+1)/2;alias Place=radio.Point;'
            'alias State=radio.State;alias Byte=uint8;message Root{Place[ROWS]points;'
            'Byte[.AXES][radio.AXES]grid;State state;struct{Place point;}metadata;}',
      }
      literal = {
         'globals.sdl': 'message Global{int16[2]coords;}',
         'foreign.sdl': 'package radio;message Point{int16[3]coords;}enum State{OFF=0;ON=1;}',
         'local.sdl': 'package nav;message Root{radio.Point[2]points;uint8[2][3]grid;'
            'radio.State state;struct{radio.Point point;}metadata;}',
      }
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary); inputs = root / 'source'; inputs.mkdir()
         for backend in (generate_c, generate_rust, generate_python, generate_java, generate_matlab, generate_ada):
            with self.subTest(backend=backend.__name__):
               for filename, source in literal.items():
                  (inputs / filename).write_text(source)
               backend(str(inputs), str(root / 'literal' / backend.__name__))
               expected = [canonical_type_descriptor(schema) for _, _, schema in parse_schemas(str(inputs))]
               for filename, source in symbolic.items():
                  (inputs / filename).write_text(source)
               backend(str(inputs), str(root / 'symbolic' / backend.__name__))
               actual = [canonical_type_descriptor(schema) for _, _, schema in parse_schemas(str(inputs))]
               self.assertEqual(actual, expected)
               self.assertEqual(list(map(catalogue_id, actual)), list(map(catalogue_id, expected)))
               def files(output):
                  return {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
               self.assertEqual(files(root / 'literal' / backend.__name__), files(root / 'symbolic' / backend.__name__))


if __name__ == '__main__':
   unittest.main()
