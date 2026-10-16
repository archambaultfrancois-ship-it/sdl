import ast
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'sdlc'), str(ROOT / 'runtime/python')]
from generator import MsgParser, canonical_type_descriptor
from c_backend import generate_c
from rust_backend import generate_rust
from python_backend import generate_python, PythonBackend
from java_backend import generate_java
from matlab_backend import generate_matlab
from ada_backend import generate_ada
from sdl_runtime import description, catalogue_id, prepare, encode, decode


SOURCE = '''/** Geometry catalogue. */
package nav;
/** Number of dimensions. */
const AXES = 2;
/** Device state. */
enum State {
   /** Inactive state. */ ZERO = 0;
   ONE = 1; /** Active state. */
}
/** Point in metres. */
message Point {
   /** Signed coordinates. */ int16[AXES] coords;
   State state; /** Current state. */
   optional struct {
      int32 code; /** Diagnostic code. */
   } detail; /** Optional diagnostic. */
}
'''


class DocumentationCommentTests(unittest.TestCase):
   def parse(self, text):
      schema = MsgParser()
      schema.parse_text(text)
      return schema

   def test_ordinary_blocks_are_ignored_between_tokens(self):
      tokens = ('package', 'nav', '.', 'geometry', ';', 'const', 'AXES', '=', '2', ';',
         'enum', 'State', '{', 'OFF', '=', '-1', ';', 'ON', '=', '1', ';', '}',
         'message', 'Point', '{', 'int16', '[', 'AXES', ']', 'coords',
         'wire', '(', 'bits', '=', '12', ')', ';', 'State', 'state', ';', '}')
      expected = self.parse(' '.join(tokens))
      actual = self.parse('/* ordinary\n * } ; /** doc marker ignored\n */'.join(tokens))
      self.assertEqual(canonical_type_descriptor(actual), canonical_type_descriptor(expected))
      self.assertEqual(actual.messages['nav.geometry.Point'].doc, '')
      # Comment delimiters inside a line comment are ordinary text.
      self.parse('// /* never opens a block\nmessage Empty{}')

   def test_leading_and_trailing_documentation_on_all_declarations(self):
      schema = self.parse(SOURCE)
      self.assertEqual(schema.doc, 'Geometry catalogue.')
      self.assertEqual(schema.constant_docs['nav.AXES'], 'Number of dimensions.')
      self.assertEqual(schema.enums['nav.State'].doc, 'Device state.')
      self.assertEqual(schema.enums['nav.State'].value_docs,
         {'ZERO': 'Inactive state.', 'ONE': 'Active state.'})
      point = schema.messages['nav.Point']
      self.assertEqual(point.doc, 'Point in metres.')
      self.assertEqual([field.doc for field in point.fields],
         ['Signed coordinates.', 'Current state.', 'Optional diagnostic.'])
      self.assertEqual(schema.messages['nav.Point$1'].doc, 'Optional diagnostic.')
      self.assertEqual(schema.messages['nav.Point$1'].fields[0].doc, 'Diagnostic code.')

   def test_trailing_documentation_prefers_previous_completed_declaration(self):
      schema = self.parse('const N=2;/** count */message Point{'
         'int32 x;/** horizontal */int32 y;/** vertical */}/** point */')
      self.assertEqual(schema.constant_docs['N'], 'count')
      self.assertEqual(schema.messages['Point'].doc, 'point')
      self.assertEqual([f.doc for f in schema.messages['Point'].fields], ['horizontal', 'vertical'])

   def test_documentation_on_new_line_targets_next_declaration(self):
      schema = self.parse('message Point { int32 x;\n/** vertical */\nint32 y; }')
      self.assertEqual([f.doc for f in schema.messages['Point'].fields], ['', 'vertical'])

   def test_multiline_trailing_block_uses_its_opening_line(self):
      schema = self.parse('message Point { int32 x; /** First line.\n * Second line.\n */\nint32 y; }')
      self.assertEqual(schema.messages['Point'].fields[0].doc, 'First line.\nSecond line.')
      self.assertEqual(schema.messages['Point'].fields[1].doc, '')

   def test_optional_stars_indentation_and_paragraphs_are_normalized(self):
      for block in ('/**\n * Title.\n *\n *   Indented example.\n */',
            '/**\n Title.\n\n   Indented example.\n */'):
         with self.subTest(block=block):
            schema = self.parse(block + '\nmessage Point{}')
            self.assertEqual(schema.messages['Point'].doc, 'Title.\n\n  Indented example.')

   def test_multiple_blocks_are_combined_and_empty_blocks_are_valid(self):
      schema = self.parse('/** First. */\n/* ignored */\n/** Second. */\n'
         'message Point { /**/ int32 x; /** Last. */ }')
      self.assertEqual(schema.messages['Point'].doc, 'First.\n\nSecond.')
      self.assertEqual(schema.messages['Point'].fields[0].doc, 'Last.')

   def test_unterminated_or_orphaned_documentation_is_rejected(self):
      for source, message in (
            ('message Point{}\n/* missing end', 'unterminated block comment at line 2, column 1'),
            ('/** missing end', 'unterminated block comment'),
            ('/** orphan */', 'documentation comment has no following declaration'),
            ('message Point{}\n/** orphan */', 'documentation comment has no following declaration'),
            ('message Point{\n/** orphan */\n}', 'documentation comment has no following declaration'),
            ('enum State{ZERO=0;\n/** orphan */\n}', 'documentation comment has no following declaration'),
            ('message Point{int32 /** misplaced */ x;}', 'documentation comment must precede'),
            ('message Point{int32 x wire(bits=/** misplaced */12);}', 'documentation comment must precede')):
         with self.subTest(source=source), self.assertRaisesRegex(ValueError, message):
            self.parse(source)

   def test_all_comments_leave_catalogue_and_hash_unchanged(self):
      plain = 'package nav;const AXES=2;enum State{ZERO=0;ONE=1;}'
      plain += 'message Point{int16[AXES]coords;State state;optional struct{int32 code;}detail;}'
      documented = self.parse(SOURCE)
      expected = self.parse(plain)
      self.assertEqual(canonical_type_descriptor(documented), canonical_type_descriptor(expected))
      self.assertEqual(catalogue_id(canonical_type_descriptor(documented)),
         catalogue_id(canonical_type_descriptor(expected)))

   def test_python_docstrings_are_accessible_and_codecs_are_unchanged(self):
      schema = self.parse(SOURCE)
      module = types.ModuleType('_sdl_documentation_test')
      code = PythonBackend(schema).generate()
      tree = ast.parse(code)
      self.assertIn('Geometry catalogue.', ast.get_docstring(tree))
      exec(code, module.__dict__)
      self.assertIn('Point in metres.', module.Point.__doc__)
      self.assertIn('Signed coordinates.', module.Point.__doc__)
      self.assertEqual(module.State.ONE.__doc__, 'Active state.')
      with patch.dict(sys.modules, {module.__name__: module}):
         text = description(module.Point)
         context = prepare(text, module.Point)
         value = module.Point(coords=[11, 22], state=module.State.ONE)
         self.assertEqual(decode(context, encode(context, value)), value)

   def test_ordinary_blocks_produce_identical_files_in_six_backends(self):
      source = 'message Point{int16 x;int16 y;}'
      commented = '/* ordinary */message/* ordinary */Point{/* ordinary */'
      commented += 'int16 x;/* ordinary */int16 y;}/* ordinary */'
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary); path = root / 'schema.sdl'
         for backend in (generate_c, generate_rust, generate_python, generate_java, generate_matlab, generate_ada):
            with self.subTest(backend=backend.__name__):
               path.write_text(source); backend(str(path), str(root / 'plain' / backend.__name__))
               path.write_text(commented); backend(str(path), str(root / 'commented' / backend.__name__))
               def files(output):
                  return {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
               self.assertEqual(files(root / 'plain' / backend.__name__), files(root / 'commented' / backend.__name__))

   def test_documentation_is_embedded_in_all_six_generated_languages(self):
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary); path = root / 'schema.sdl'; path.write_text(SOURCE)
         for backend in (generate_c, generate_rust, generate_python, generate_java, generate_matlab, generate_ada):
            with self.subTest(backend=backend.__name__):
               output = root / backend.__name__; backend(str(path), str(output))
               text = '\n'.join(p.read_text() for p in output.rglob('*') if p.is_file())
               for phrase in ('Geometry catalogue.', 'Number of dimensions.', 'Device state.',
                     'Inactive state.', 'Active state.', 'Point in metres.', 'Signed coordinates.',
                     'Current state.', 'Diagnostic code.', 'Optional diagnostic.'):
                  self.assertIn(phrase, text)

   def test_matlab_retains_documentation_for_constants_only_schema(self):
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary); path = root / 'limits.sdl'
         path.write_text('package nav;\nconst N=2;/** Array size. */')
         generate_matlab(str(path), str(root / 'generated'))
         doc = root / 'generated/+nav/sdl_schema_doc_limits.m'
         self.assertIn('% Array size.', doc.read_text())

   def test_c_and_ada_docs_attach_to_values_after_auxiliary_members(self):
      source = '''message Point {
         optional int32 code; /** Diagnostic code. */
         repeated int32 samples; /** Recorded samples. */
      }'''
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary); path = root / 'schema.sdl'; path.write_text(source)
         generate_c(str(path), str(root / 'c'))
         header = (root / 'c/schema.h').read_text()
         self.assertRegex(header, r'bool \w+;\n   /\*\*\n    \* Diagnostic code\.\n    \*/\n   int32_t code;')
         self.assertRegex(header, r'uint32_t \w+;\n   /\*\*\n    \* Recorded samples\.\n    \*/\n   int32_t \*samples;')
         generate_ada(str(path), str(root / 'ada'))
         spec = next((root / 'ada').rglob('*schema.ads')).read_text()
         self.assertRegex(spec, r'H_code : Boolean := False;\n      -- Diagnostic code\.\n      F_code :')


if __name__ == '__main__':
   unittest.main()
