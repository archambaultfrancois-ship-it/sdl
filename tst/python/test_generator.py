import pathlib
import sys
import tempfile
import unittest
import warnings

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / 'gen'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / 'runtime/python'))

from c_backend import CBackend, generate_c
from generator import MsgParser, canonical_type_descriptor
from rust_backend import RustBackend, generate_rust
from python_backend import PythonBackend, generate_python
from java_backend import JavaBackend, generate_java
from ada_backend import AdaBackend, generate_ada


class GeneratorValidationTests(unittest.TestCase):
   def test_01_schema_packed_encodings_warn_without_rejecting_schema(self):
      for type_name, encoding in (('uint64', 'varint'), ('int32', 'varint'),
            ('int64', 'zigzag'), ('State', 'varint'), ('State', 'zigzag')):
         with self.subTest(type=type_name, encoding=encoding):
            source = 'enum State {\n ZERO = 0;\n}\nmessage Batch {\n packed ' + \
               type_name + ' values wire(encoding=' + encoding + ');\n}\n'
            with warnings.catch_warnings(record=True) as caught:
               warnings.simplefilter('always')
               parser = MsgParser()
               parser.parse_text(source)
            self.assertEqual(len(caught), 1)
            message = str(caught[0].message)
            self.assertIn('packed field Batch.values', message)
            self.assertIn('wire(encoding=' + encoding + ') at Batch.values', message)
            self.assertIn('may reduce runtime optimizations', message)
            self.assertIn(b'@' + (b'v' if encoding == 'varint' else b'z'),
               canonical_type_descriptor(parser))

   def test_01_schema_packed_nested_encoding_warning_reports_field_path(self):
      source = '''message Batch {
 packed Record records;
}
message Record {
 Leaf[2] points;
}
message Leaf {
 struct {
   int64 value wire(encoding=zigzag);
 } inner;
}
'''
      with warnings.catch_warnings(record=True) as caught:
         warnings.simplefilter('always')
         MsgParser().parse_text(source)
      self.assertEqual(len(caught), 1)
      self.assertIn('packed field Batch.records', str(caught[0].message))
      self.assertIn('wire(encoding=zigzag) at Batch.records.points.inner.value',
         str(caught[0].message))

   def test_01_schema_packed_encoding_warning_excludes_unaffected_fields(self):
      source = '''message Item {
 int32 value wire(encoding=zigzag);
}
message Batch {
 uint64 counter wire(encoding=varint);
 repeated Item items;
 packed uint32 ordinary;
 packed int16 narrow wire(bits=12,align=4,pad_after=1);
 packed fl32 quantized wire(bits=12,q=0.25);
}
'''
      with warnings.catch_warnings(record=True) as caught:
         warnings.simplefilter('always')
         MsgParser().parse_text(source)
      self.assertEqual(caught, [])
      invalid = 'message Item {\n optional int32 value wire(encoding=zigzag);\n}\n' + \
         'message Batch {\n packed Item items;\n}\n'
      with warnings.catch_warnings(record=True) as caught:
         warnings.simplefilter('always')
         with self.assertRaisesRegex(ValueError, 'packed field type must have a fixed wire size'):
            MsgParser().parse_text(invalid)
      self.assertEqual(caught, [])

   def test_02_backend_packed_encoding_warning_once_for_multiple_backends(self):
      import subprocess
      repo = pathlib.Path(__file__).resolve().parents[2]
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         source = root / 'warning.sdl'
         source.write_text('message Batch {\n packed uint64 values wire(encoding=varint);\n}\n')
         result = subprocess.run([sys.executable, str(repo / 'gen/generator.py'),
            '-c', '-rust', '-python', '-java', '-ada', '-matlab', str(source),
            str(root / 'generated')], capture_output=True, text=True)
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertEqual(result.stderr.count('UserWarning: packed field Batch.values'), 1,
            result.stderr)
         for backend in ('c', 'rust', 'python', 'java', 'ada', 'matlab'):
            self.assertTrue(any((root / 'generated' / backend).iterdir()), backend)

   def test_02_backend_automatic_fixed_codecs_without_packed(self):
      from ada_direct import DirectCodecs
      from matlab_backend import MatlabBackend
      parser = MsgParser()
      parser.parse_text((pathlib.Path(__file__).resolve().parents[2] / 'sdl/auto_fixed.sdl').read_text())
      direct = DirectCodecs(AdaBackend(parser, 'auto_fixed'))
      self.assertTrue(all(direct.direct.values()))
      self.assertEqual(direct.fixed['AutoRecord'], 31)
      self.assertTrue(direct.bulk['AutoBatch'])
      matlab = MatlabBackend(parser)
      fixed = matlab.generate_message('AutoRecord')
      self.assertIn("case 'encode_fixed'", fixed)
      self.assertIn("case 'decode_fixed'", fixed)
      self.assertIn("b=zeros(31,1,'uint8')", fixed)
      self.assertNotIn("case 'encode_fixed'", matlab.generate_message('AutoBatch'))
      collisions = MsgParser()
      collisions.parse_text('message sdl_fixed_encode {\n int16 value;\n}\n'
         'message sdl_fixed_decode {\n sdl_fixed_encode child;\n}\n')
      generated = MatlabBackend(collisions).generate_message('sdl_fixed_decode')
      self.assertIn('function b=sdl_fixed_encode_(ctx,v)', generated)
      self.assertIn('function v=sdl_fixed_decode_(ctx,b)', generated)
      self.assertIn("value.child=sdl_fixed_encode('new')", generated)

   def test_02_backend_enum_leading_zeros_are_decimal_in_every_backend(self):
      import os
      import shlex
      import shutil
      import struct
      import subprocess
      import types
      from unittest.mock import patch
      from matlab_backend import generate_matlab
      from sdl_runtime import prepare, encode, decode
      source = '''enum DecimalState {
 ZERO = -000;
 EIGHT = 008;
 TEN = 010;
 NEGATIVE = -010;
 MINIMUM = -02147483648;
 MAXIMUM = 02147483647;
}
message DecimalPacket {
 DecimalState state;
}
'''
      canonical = source.replace('-000', '0').replace('008', '8').replace('010', '10').replace('021474836', '21474836')
      parser = MsgParser()
      parser.parse_text(source)
      self.assertEqual(parser.enums['DecimalState'].pairs, [
         ('ZERO', '0'), ('EIGHT', '8'), ('TEN', '10'), ('NEGATIVE', '-10'),
         ('MINIMUM', '-2147483648'), ('MAXIMUM', '2147483647')])
      module = types.ModuleType('_sdl_decimal_fixture')
      exec(PythonBackend(parser).generate(), module.__dict__)
      with patch.dict(sys.modules, {module.__name__: module}):
         context = prepare(canonical_type_descriptor(parser), module.DecimalPacket)
         for value in (0, 8, 10, -10, -2147483648, 2147483647):
            packet = module.DecimalPacket()
            packet.state = module.DecimalState(value)
            wire = encode(context, packet)
            self.assertEqual(wire, b'\x01' + struct.pack('>i', value))
            self.assertEqual(decode(context, wire).state, packet.state)
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         schema = root / 'decimal.sdl'
         for generate in (generate_c, generate_rust, generate_python, generate_java, generate_ada, generate_matlab):
            with self.subTest(backend=generate.__name__):
               schema.write_text(canonical)
               generate(str(schema), str(root / 'canonical'))
               schema.write_text(source)
               generate(str(schema), str(root / 'padded'))
               outputs = lambda directory: {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
               self.assertEqual(outputs(root / 'canonical'), outputs(root / 'padded'))
         compiler = shlex.split(os.environ.get('CC', 'cc'))
         if compiler and shutil.which(compiler[0]):
            repo = pathlib.Path(__file__).resolve().parents[2]
            executable = root / 'decimal-check'
            check = '#include "decimal.h"\n#include <assert.h>\nint main(void) {\n' + ''.join(
               'assert(DECIMALSTATE_%s == %s);\n' % pair for pair in parser.enums['DecimalState'].pairs) + 'return 0;\n}\n'
            subprocess.run(compiler + ['-std=c99', '-I' + str(root / 'padded'),
               '-I' + str(repo / 'runtime/c'), '-x', 'c', '-', '-o', str(executable)],
               input=check, check=True, capture_output=True, text=True)
            subprocess.run([str(executable)], check=True)

   def test_01_schema_enum_rejects_trailing_tokens(self):
      for suffix, error in (('unexpected tokens', 'invalid enum entry'),
            ('}', 'unexpected closing brace')):
         with self.subTest(suffix=suffix):
            with self.assertRaisesRegex(ValueError, error):
               MsgParser().parse_text('enum State {\n READY = 1; ' + suffix + '\n}\n')
      compact = MsgParser()
      compact.parse_text('enum State { READY = 1; SECOND = 2; }')
      self.assertEqual(compact.enums['State'].pairs, [('READY', '1'), ('SECOND', '2')])
      parser = MsgParser()
      parser.parse_text('enum State {\n READY = 1; // comment\n}\nmessage Packet {\n State state;\n}\n')
      self.assertEqual(parser.enums['State'].pairs, [('READY', '1')])

   def test_03_limits_message_depth_and_shared_subtypes(self):
      from sdl_runtime import prepare

      def chain(count, leaf_first=False):
         declarations = ['message M%04d {\n %s\n}\n' % (index,
            'M%04d child;' % (index + 1) if index + 1 < count else 'int32 value;')
            for index in range(count)]
         return ''.join(reversed(declarations) if leaf_first else declarations)

      for leaf_first in (False, True):
         parser = MsgParser()
         parser.parse_text(chain(65, leaf_first))
         prepare(canonical_type_descriptor(parser))
         for count in (66, 1500):
            with self.subTest(count=count, leaf_first=leaf_first):
               with self.assertRaisesRegex(ValueError, 'message dependency nesting exceeds 64 levels'):
                  MsgParser().parse_text(chain(count, leaf_first))
      # The child was already visited at depth zero before this new root.
      with self.assertRaisesRegex(ValueError, 'message dependency nesting exceeds 64 levels'):
         MsgParser().parse_text(chain(65, True) + 'message Root {\n M0000 left;\n M0000 right;\n}\n')

   def test_02_backend_c_registration_growth_and_conflicts(self):
      import os
      import shlex
      import shutil
      import subprocess
      compiler = shlex.split(os.environ.get('CC', 'cc'))
      if not compiler or not shutil.which(compiler[0]):
         self.skipTest('C compiler is unavailable')
      repo = pathlib.Path(__file__).resolve().parents[2]
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         schema = root / 'registry_test.sdl'
         schema.write_text(''.join('message M%03d {\n}\n' % i for i in range(257)))
         output = root / 'generated'
         generate_c(str(schema), str(output))
         check = root / 'check.c'
         check.write_text('''#include "sdl_registry.h"
#include "type_registry.h"
#include "type_private.h"
#include <assert.h>
#include <string.h>
int main(int argc, char **argv) {
   static SdlTypeDesc conflict;
   const SdlTypeDesc *last;
   (void)argv;
   if (argc > 1) {
      conflict.kind = SDL_TYPE_STRUCT;
      conflict.name = "M256";
      assert(sdl_register_type(&conflict));
      assert(!register_all_types());
      assert(sdl_lookup_type("M000"));
      assert(sdl_lookup_type("M256") == &conflict);
      assert(!register_all_types());
   } else {
      assert(register_all_types());
      last = sdl_lookup_type("M256");
      assert(last && !strcmp(last->name, "M256"));
      assert(register_all_types());
      assert(sdl_lookup_type("M256") == last);
   }
   return 0;
}
''')
         executable = root / 'registry-check'
         command = compiler + ['-std=c99', '-I' + str(repo / 'runtime/c'),
            '-I' + str(output), str(repo / 'runtime/c/type_registry.c'),
            str(output / 'registry_test.c'), str(output / 'sdl_registry.c'),
            str(check), '-o', str(executable)]
         subprocess.run(command, check=True, capture_output=True, text=True)
         subprocess.run([str(executable)], check=True)
         subprocess.run([str(executable), 'conflict'], check=True)

   def test_02_backend_c_private_readable_descriptors(self):
      import os
      import shlex
      import shutil
      import subprocess
      parser = MsgParser()
      parser.parse_text('message Point {\n fl32 x;\n fl32 y;\n}\n')
      header, source = CBackend(parser).generate_files('point')
      self.assertNotIn('SdlTypeDesc', header)
      self.assertNotIn('SCHEMA_DESCRIPTOR', header)
      self.assertNotIn('type_descriptors.h', header)
      self.assertIn('static const uint8_t point_schema_descriptor[] =\n'
         '   "SDL2|Point{1:fl32:x;2:fl32:y;}";', source)
      self.assertIn('static const SdlTypeDesc point_desc =', source)
      self.assertIn('sizeof(point_schema_descriptor) - 1', source)
      compiler = shlex.split(os.environ.get('CC', 'cc'))
      if not compiler or not shutil.which(compiler[0]):
         self.skipTest('C compiler is unavailable')
      repo = pathlib.Path(__file__).resolve().parents[2]
      # Private names must not collide with SDL types or array field names.
      schema_text = '''enum State {
 READY = 1;
}
enum sdl_enum_state_desc {
 READY = 2;
}
message Point {
 fl32[2] x;
 fl32[2] X;
 State status;
 optional sdl_enum_state_desc other;
}
message point_fields {
 Point child;
}
message point_desc {
 point_fields child;
}
'''
      parser = MsgParser()
      parser.parse_text(schema_text)
      expected = canonical_type_descriptor(parser).decode('ascii')
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         schema = root / 'private_test.sdl'
         schema.write_text(schema_text)
         output = root / 'generated'
         generate_c(str(schema), str(output))
         check = root / 'check.c'
         check.write_text('''#include "private_test.h"
#include "type_private.h"
#include <assert.h>
#include <string.h>
int main(void) {
   const SdlTypeDesc *descriptor;
   assert(register_private_test_types());
   descriptor = sdl_lookup_type("Point");
   assert(descriptor && descriptor->schema_descriptor_size == strlen("%s"));
   assert(!memcmp(descriptor->schema_descriptor, "%s", descriptor->schema_descriptor_size));
   assert(sdl_lookup_type("point_fields") && sdl_lookup_type("point_desc"));
   return 0;
}
''' % (expected, expected))
         executable = root / 'private-check'
         command = compiler + ['-std=c99', '-Wall', '-Wextra', '-Werror', '-pedantic',
            '-I' + str(repo / 'runtime/c'), '-I' + str(output)] + [
            str(path) for path in sorted((repo / 'runtime/c').glob('*.c'))] + [
            str(output / 'private_test.c'), str(check), '-lm', '-o', str(executable)]
         result = subprocess.run(command, capture_output=True, text=True)
         self.assertEqual(result.returncode, 0, result.stderr)
         subprocess.run([str(executable)], check=True)

   def test_01_schema_compact_catalogue_and_nested_hash(self):
      import hashlib
      source = '''struct Position {
 int32 x;
 int32 y wire(bits=12);
}
struct Vehicule {
 uint32 id;
 Position position;
 optional int16 code;
 repeated Position history;
 packed uint8 flags wire(bits=3,align=4,pad_after=1);
}
enum State {
 STOPPED = 0;
 MOVING = 1;
}
'''
      parser = MsgParser(); parser.parse_text(source)
      expected = (b'SDL2|Position{1:int32:x;2:int32:y@b12;}'
         b'|Vehicule{1:uint32:id;2:Position:position;3?:int16:code;'
         b'4*:Position:history;5+:uint8:flags@b3,a4,p1;}'
         b'|#State{STOPPED=0;MOVING=1;}')
      self.assertEqual(canonical_type_descriptor(parser, 'Vehicule'), expected)
      self.assertFalse(any(chr(c).isspace() for c in expected))
      changed = MsgParser(); changed.parse_text(source.replace('bits=12', 'bits=11'))
      self.assertNotEqual(hashlib.sha256(expected).digest(),
         hashlib.sha256(canonical_type_descriptor(changed, 'Vehicule')).digest())

   def test_01_schema_message_keyword_aliases(self):
      source = """message Child {
 int32 value;
}
message Packet {
 Child child;
 optional struct {
   bool active;
 } details;
}
"""
      canonical = MsgParser()
      canonical.parse_text(source)
      for keyword in ('message', 'structure', 'struct'):
         variant = source.replace('message ', keyword + ' ').replace('optional struct {', 'optional ' + keyword + ' {')
         with self.subTest(keyword=keyword):
            parser = MsgParser()
            parser.parse_text(variant)
            self.assertEqual(canonical_type_descriptor(parser), canonical_type_descriptor(canonical))
            self.assertEqual(parser.message_order, canonical.message_order)
            for malformed, error in (
               (keyword + ' Packet\n', 'unexpected SDL statement'),
               (keyword + ' Packet {\n', 'unterminated message'),
               ('message Packet {\n}\n' + keyword + ' Packet {\n}\n', 'duplicate type name'),
               ('enum State {\n' + keyword + ' Packet {\n', 'missing closing brace'),
            ):
               with self.assertRaisesRegex(ValueError, error):
                  MsgParser().parse_text(malformed)

   def test_02_backend_message_aliases_produce_identical_outputs(self):
      from matlab_backend import generate_matlab
      source = """message Child {
 int32 value;
}
message Packet {
 Child child;
 optional struct {
   bool active;
 } details;
}
"""
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         schema = root / 'packet.sdl'
         for generate in (generate_c, generate_rust, generate_python, generate_java, generate_ada, generate_matlab):
            with self.subTest(backend=generate.__name__):
               schema.write_text(source)
               generate(str(schema), str(root / 'canonical'))
               outputs = lambda directory: {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
               for keyword in ('message', 'structure', 'struct'):
                  schema.write_text(source.replace('message ', keyword + ' ').replace('optional struct {', 'optional ' + keyword + ' {'))
                  generate(str(schema), str(root / 'alias'))
                  self.assertEqual(outputs(root / 'canonical'), outputs(root / 'alias'))

   def test_01_schema_required_is_default(self):
      parser = MsgParser()
      parser.parse_text("""
enum State {
 READY = 1;
}
message Child {
 bool active;
}
message Packet {
 1: int32 value;
 State state;
 Child child;
 int16[2] samples;
 optional string label;
 repeated Child children;
 packed int16 readings;
 struct {
   int32 inner;
   5: struct {
      bool active;
   } nested;
 } details;
}
""")
      fields = parser.messages['Packet'].fields
      self.assertEqual([f.index for f in fields], list(range(1, 9)))
      self.assertEqual([f.modifier for f in fields],
         ['required'] * 4 + ['optional', 'repeated', 'packed', 'required'])
      self.assertEqual(parser.messages['Packet$1'].fields[0].modifier, 'required')
      self.assertEqual(parser.messages['Packet$1'].fields[1].modifier, 'required')
      self.assertEqual(parser.messages['Packet$2'].fields[0].modifier, 'required')
      self.assertIn('1:int32:value;', canonical_type_descriptor(parser).decode())
      with self.assertRaisesRegex(ValueError, 'invalid field declaration'):
         MsgParser().parse_text('message Packet {\noptional required bool value;\n}\n')

   def test_01_schema_implicit_field_numbers(self):
      parser = MsgParser()
      parser.parse_text("""
message Packet {
 required int32 first;
 optional string second;
 10: repeated int16 third;
 packed int16 fourth;
 5: required bool fifth;
 required int32[2] sixth;
}
message Other {
 required bool first;
}
""")
      self.assertEqual([f.index for f in parser.messages['Packet'].fields], [1, 2, 10, 11, 5, 6])
      self.assertEqual(parser.messages['Other'].fields[0].index, 1)
      self.assertIn('11+:int16:fourth;', canonical_type_descriptor(parser).decode())

   def test_01_schema_implicit_nested_numbers(self):
      parser = MsgParser()
      parser.parse_text("""
message Envelope {
 required bool flag;
 optional struct {
   required int32 value;
   required struct {
      required bool flag;
   } inner;
   required int32 tail;
 } data;
 required string label;
}
""")
      self.assertEqual([f.index for f in parser.messages['Envelope'].fields], [1, 2, 3])
      self.assertEqual([f.index for f in parser.messages['Envelope$1'].fields], [1, 2, 3])
      self.assertEqual(parser.messages['Envelope$2'].fields[0].index, 1)

   def test_01_schema_implicit_number_conflicts_and_overflow(self):
      for fields, error in (
         ('required bool a;\n1: required bool b;', 'duplicate field ID'),
         ('2: required bool a;\n1: required bool b;\nrequired bool c;', 'duplicate field ID'),
         ('4294967295: required bool a;\nrequired bool b;', 'nonzero uint32'),
      ):
         with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, error):
            MsgParser().parse_text('message Packet {\n' + fields + '\n}\n')

   def test_02_backend_implicit_numbers_produce_identical_outputs(self):
      from matlab_backend import generate_matlab
      explicit = """message Packet {
 1: required int32 value;
 2: optional struct {
   1: required bool flag;
 } details;
 3: packed int16 samples;
}
"""
      variants = (explicit.replace('required ', ''),
         explicit.replace('1: ', '').replace('2: ', '').replace('3: ', ''),
         explicit.replace('required ', '').replace('1: ', '').replace('2: ', '').replace('3: ', ''))
      with tempfile.TemporaryDirectory() as temporary:
         root = pathlib.Path(temporary)
         for generate in (generate_c, generate_rust, generate_python, generate_java, generate_ada, generate_matlab):
            with self.subTest(backend=generate.__name__):
               schema = root / 'packet.sdl'
               schema.write_text(explicit)
               generate(str(schema), str(root / 'explicit'))
               outputs = lambda directory: {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
               for implicit in variants:
                  schema.write_text(implicit)
                  generate(str(schema), str(root / 'implicit'))
                  self.assertEqual(outputs(root / 'explicit'), outputs(root / 'implicit'))

   def test_01_schema_01_message_and_enum_braces_are_required_and_balanced(self):
      malformed_schemas = (
         ('message Packet\n   1: required int32 value;\n}\n',
            'unexpected SDL statement'),
         ('enum State\n   READY = 1;\n}\n',
            'unexpected SDL statement'),
         ('message Packet {\n   1: required int32 value;\n',
            'unterminated message declaration: Packet'),
         ('enum State {\n   READY = 1;\n',
            'unterminated enum declaration: State'),
         ('message Packet {\n}\n}\n', 'unexpected closing brace'),
         ('message Packet {\n   1: required int32 value;\n'
            'message Other {\n}\n',
            'missing closing brace before declaration: message Other {'),
         ('message Packet {\n   1: required struct {\n'
            '      1: required int32 value;\n',
            'unterminated anonymous struct in Packet'),
         ('message Packet {\n   1: required struct {\n'
            '      1: required int32 value;\n}\n}\n',
            'anonymous struct must close with its field name'),
      )
      for source, error in malformed_schemas:
         with self.subTest(error=error):
            with self.assertRaisesRegex(ValueError, error):
               MsgParser().parse_text(source)

   def test_01_schema_02_s04_duplicate_field_ids_and_unknown_types_are_rejected(self):
      with self.assertRaisesRegex(ValueError, 'duplicate field ID or name in Packet'):
         MsgParser().parse_text('''
message Packet {
   1: required int32 first;
   1: required int32 second;
}
''')
      with self.assertRaisesRegex(ValueError, 'unknown type Missing in Packet'):
         MsgParser().parse_text('''
message Packet {
   1: required Missing item;
}
''')

   def test_01_schema_03_numeric_schema_tokens_use_ascii_decimal_digits(self):
      with self.assertRaisesRegex(ValueError, 'invalid field declaration'):
         MsgParser().parse_text("""
message Packet {
   ١: required int32 value;
}
""")
      with self.assertRaisesRegex(ValueError, 'invalid field declaration'):
         MsgParser().parse_text("""
message Packet {
   1: required int32[٢] values;
}
""")
      with self.assertRaisesRegex(ValueError, 'invalid enum entry'):
         MsgParser().parse_text("""
enum State {
   READY = ١;
}
""")

   def test_01_schema_04_declared_types_cannot_reuse_builtin_names(self):
      cases = (
         ("""
message int32 {
   1: required string text;
}
""", 'type name conflicts with built-in type: int32'),
         ("""
enum string {
   TEXT = 1;
}
""", 'type name conflicts with built-in type: string'),
         ("""
message bool {
   1: required int32 value;
}
""", 'type name conflicts with built-in type: bool'))
      for source, error in cases:
         with self.assertRaisesRegex(ValueError, error):
            MsgParser().parse_text(source)

   def test_03_limits_05_schema_descriptor_limits_are_rejected_during_parse(self):
      with self.assertRaisesRegex(ValueError, 'enum value must fit signed int32 in State'):
         MsgParser().parse_text("""
enum State {
   TOO_LARGE = 2147483648;
}
""")

      with self.assertRaisesRegex(ValueError, 'enum value must fit signed int32 in State'):
         MsgParser().parse_text("""
enum State {
   TOO_SMALL = -2147483649;
}
""")

      dimensions = '[1]' * 256
      with self.assertRaisesRegex(ValueError, 'too many array dimensions in Packet.values'):
         MsgParser().parse_text('message Packet {\n   1: required int32' +
            dimensions + ' values;\n}\n')

      long_name = 'é' * 32768
      with self.assertRaisesRegex(ValueError, 'type name exceeds 65535 UTF-8 bytes in schema catalogue'):
         MsgParser().parse_text('message ' + long_name + ' {\n}\n')

   def test_03_limits_06_field_name_length_accepts_max_bytes(self):
      name = 'x' * 0xFFFF
      parser = MsgParser()
      parser.parse_text('message Packet {\n   1: required int32 ' + name + ';\n}\n')
      self.assertEqual(parser.messages['Packet'].fields[0].name, name)

      with self.assertRaisesRegex(ValueError,
            'field name in Packet exceeds 65535 UTF-8 bytes in schema catalogue'):
         MsgParser().parse_text('message Packet {\n   1: required int32 ' +
            name + 'x;\n}\n')

   def test_03_limits_07_schema_catalogue_counts_are_validated(self):
      enum_values = ''.join('   ITEM_' + str(index) + ' = ' + str(index) + ';\n'
         for index in range(0x10000))
      with self.assertRaisesRegex(ValueError,
            'too many values in enum State for schema descriptor'):
         MsgParser().parse_text('enum State {\n' + enum_values + '}\n')

      fields = ''.join('   ' + str(index + 1) + ': required int32 field_' +
         str(index) + ';\n' for index in range(0x10000))
      with self.assertRaisesRegex(ValueError,
            'too many fields in Packet for schema descriptor'):
         MsgParser().parse_text('message Packet {\n' + fields + '}\n')

   def test_03_limits_08_total_wire_descriptor_size_limit_is_inclusive(self):
      field_lengths = [65534] * 15 + [65402]

      def source_with_extra_byte(extra_byte):
         fields = []
         for index, length in enumerate(field_lengths):
            suffix = str(index)
            if index == len(field_lengths) - 1:
               length += extra_byte
            name = 'x' * (length - len(suffix)) + suffix
            fields.append('   ' + str(index + 1) + ': required int32 ' + name + ';\n')
         return 'message Packet {\n' + ''.join(fields) + '}\n'

      parser = MsgParser()
      parser.parse_text(source_with_extra_byte(0))
      self.assertEqual(len(canonical_type_descriptor(parser, 'Packet')), 1 << 20)

      with self.assertRaisesRegex(ValueError,
            'wire descriptor exceeds 1 MiB'):
         MsgParser().parse_text(source_with_extra_byte(1))

   def test_01_schema_05_empty_enums_are_rejected_for_backend_portability(self):
      with self.assertRaisesRegex(ValueError,
            'enum must declare at least one value: State'):
         MsgParser().parse_text('''
enum State {
}
''')

   def test_01_schema_06_s05_recursive_message_dependencies_are_rejected(self):
      with self.assertRaisesRegex(ValueError,
            'recursive message dependency involving Packet'):
         MsgParser().parse_text('''
message Packet {
   1: required Packet child;
}
''')

   def test_01_schema_07_anonymous_nested_structs_get_stable_logical_names(self):
      parser = MsgParser()
      parser.parse_text('''
message Packet {
   1: required struct {
      1: required int32 code;
      2: required struct {
         1: required string label;
      } detail;
   } metadata;
}
''')
      self.assertIn('Packet$1', parser.messages)
      self.assertIn('Packet$2', parser.messages)
      self.assertEqual(parser.messages['Packet$1'].fields[1].type_name, 'Packet$2')

   def test_02_backend_02_c_backend_avoids_auxiliary_member_name_collisions(self):
      parser = MsgParser()
      parser.parse_text('''
message Packet {
   1: optional int32 value;
   2: required int32 has_value;
   3: repeated int32 items;
   4: required int32 items_count;
   5: optional string label;
   6: required int32 sdl_string_length_5;
}
''')
      header, source = CBackend(parser).generate_files('packet')
      self.assertIn('bool has_value_2;', header)
      self.assertIn('uint32_t items_count_2;', header)
      self.assertIn('uint32_t sdl_string_length_5_2;', header)
      self.assertIn('offsetof(Packet, has_value_2)', source)
      self.assertIn('offsetof(Packet, items_count_2)', source)
      self.assertIn('offsetof(Packet, sdl_string_length_5_2)', source)

   def test_02_backend_03_c_backend_rejects_c_keyword_type_and_field_names(self):
      cases = (
         ('''
message struct {
   1: required int32 value;
}
''',
            'C backend cannot use C keyword as type name: struct'),
         ('''
message Packet {
   1: required int32 int;
}
''',
            'C backend cannot use C keyword as field name: Packet.int'),
         ('''
enum struct {
   VALUE = 1;
}
message Packet {
   1: required struct value;
}
''',
            'C backend cannot use C keyword as enum name: struct'))
      for source, error in cases:
         parser = MsgParser()
         parser.parse_text(source)
         with self.assertRaisesRegex(ValueError, error):
            CBackend(parser).generate_files('schema')

   def test_02_backend_04_c_backend_rejects_nonportable_identifiers(self):
      cases = (
         ("""
message Packet {
   1: required int32 1value;
}
""", 'C backend cannot emit portable field name: Packet.1value'),
         ("""
enum 2State {
   READY = 1;
}
""", 'C backend cannot emit portable enum type name: 2State'),
         ("""
message SdlTypeDesc {
   1: required int32 value;
}
""", 'C backend cannot reuse a C or runtime name: SdlTypeDesc'),
         ("""
message SdlDynamicKind {
   1: required int32 value;
}
""", 'C backend cannot reuse a C or runtime name: SdlDynamicKind'),
         ("""
enum type_encode {
   OK = 1;
}
""", 'C backend cannot reuse a C or runtime name: type_encode'),
         ("""
message sdl_register_type {
   1: required int32 value;
}
""", 'C backend cannot reuse a C or runtime name: sdl_register_type'),
         ("""
message Packet {
   1: required int32 true;
}
""", 'C backend cannot use C macro as field name: Packet.true'),
         ("""
message Packet {
   1: required int32 ²field;
}
""", 'C backend cannot emit portable field name: Packet.²field'))
      for source, error in cases:
         parser = MsgParser()
         parser.parse_text(source)
         with self.assertRaisesRegex(ValueError, error):
            CBackend(parser).generate_files('schema')

   def test_02_backend_05_backend_generated_module_filenames_are_reserved(self):
      cases = (
         ('sdl_registry', generate_c,
          'C schema filename sdl_registry collides with generated registry files'),
         ('sdl_type_descriptors', generate_c,
          'C schema filename sdl_type_descriptors collides with generated/runtime include guard SDL_TYPE_DESCRIPTORS_H'),
         ('mod', generate_rust,
          'Rust schema filename mod collides with generated mod.rs'),
         ('__init__', generate_python,
          'Python schema filename __init__ collides with generated package initializer'),
         ('class', generate_python,
          'invalid Python module name from SDL filename: class'))
      for base_name, generate, error in cases:
         with tempfile.TemporaryDirectory() as directory:
            schema_path = pathlib.Path(directory) / (base_name + '.sdl')
            output_path = pathlib.Path(directory) / 'out'
            schema_path.write_text('message Packet {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
            with self.assertRaisesRegex(ValueError, error):
               generate(str(schema_path), str(output_path))
            self.assertFalse(output_path.exists())

   def test_02_backend_01_backends_refresh_outputs_safely(self):
      cases = (
         (generate_c, 'obsolete.c', 'custom.c'),
         (generate_rust, 'obsolete.rs', 'custom.rs'),
         (generate_python, 'obsolete.py', 'custom.py'),
         (generate_java, 'obsolete.java', 'custom.java'),
      )
      for generate, obsolete_file, custom_file in cases:
         with self.subTest(backend=obsolete_file), tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / 'schemas'
            output_path = pathlib.Path(directory) / 'out'
            input_path.mkdir()
            (input_path / 'active.sdl').write_text(
               'message Active {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
            obsolete_schema = input_path / 'obsolete.sdl'
            obsolete_schema.write_text(
               'message Obsolete {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
            generate(str(input_path), str(output_path))
            self.assertTrue((output_path / obsolete_file).is_file())
            custom_output = output_path / custom_file
            custom_output.write_text('user source', encoding='utf-8')

            obsolete_schema.unlink()
            generate(str(input_path), str(output_path))

            self.assertFalse((output_path / obsolete_file).exists())
            self.assertEqual(custom_output.read_text(encoding='utf-8'),
               'user source')
            self.assertTrue(any(path.name.startswith('active')
               for path in output_path.iterdir()))

            before_invalid_schema = {path.name: path.read_bytes()
               for path in output_path.iterdir() if path.is_file()}
            (input_path / 'invalid.sdl').write_text(
               'message Invalid {\n   1: required Missing value;\n}\n',
               encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'unknown type Missing'):
               generate(str(input_path), str(output_path))
            self.assertEqual(before_invalid_schema, {path.name: path.read_bytes()
               for path in output_path.iterdir() if path.is_file()})

            (input_path / 'invalid.sdl').unlink()
            (input_path / 'active.sdl').unlink()
            (input_path / 'README.txt').write_text('no SDL files', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, r'no \.sdl files found'):
               generate(str(input_path), str(output_path))
            self.assertEqual(before_invalid_schema, {path.name: path.read_bytes()
               for path in output_path.iterdir() if path.is_file()})

            (input_path / 'README.txt').unlink()
            (input_path / 'active.sdl').write_text(
               'message Active {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
            (input_path / 'radio-capture.sdl').write_text(
               'message RadioCapture {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
            (input_path / 'radio_capture.sdl').write_text(
               'message RadioCaptureOther {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
            with self.assertRaisesRegex(ValueError,
                  'SDL filenames map to the same identifier: radio_capture'):
               generate(str(input_path), str(output_path))
            self.assertEqual(before_invalid_schema, {path.name: path.read_bytes()
               for path in output_path.iterdir() if path.is_file()})

            (input_path / 'radio-capture.sdl').unlink()
            (input_path / 'radio_capture.sdl').unlink()
            active_output = output_path / obsolete_file.replace('obsolete', 'active')
            active_before_update = active_output.read_bytes()
            (input_path / 'active.sdl').write_text(
               'message Active {\n   1: required int32 value;\n' +
               '   2: required int16 revision;\n}\n', encoding='utf-8')
            generate(str(input_path), str(output_path))
            active_after_update = active_output.read_bytes()
            self.assertNotEqual(active_after_update, active_before_update)
            self.assertIn(b'revision', active_after_update)
            self.assertEqual(custom_output.read_text(encoding='utf-8'),
               'user source')

            before_type_collision = {path.name: path.read_bytes()
               for path in output_path.iterdir() if path.is_file()}
            (input_path / 'conflict.sdl').write_text(
               'message active {\n   1: required int32 other;\n}\n',
               encoding='utf-8')
            with self.assertRaisesRegex(ValueError,
                  'duplicate type name across SDL files: active'):
               generate(str(input_path), str(output_path))
            self.assertEqual(before_type_collision,
               {path.name: path.read_bytes() for path in output_path.iterdir()
                  if path.is_file()})

   def test_02_backend_18_java_backend_emits_java7_style_codec(self):
      schema = MsgParser()
      schema.parse_text('''
enum State {
   UNKNOWN = 0;
   READY = 1;
}
message Packet {
   1: optional string text;
   2: required int32[2] values;
   3: packed State states;
   4: packed c32 points;
}
''')
      source = JavaBackend(schema, 'fixture').generate()
      self.assertIn('public enum State implements SdlCodec.EnumValue', source)
      self.assertIn('public static final class Packet implements SdlCodec.Message', source)
      self.assertIn('new int[2]', source)
      self.assertIn('SdlCodec.Complex32Array points;', source)
      self.assertNotIn('->', source)
      self.assertNotIn('<>', source)

   def test_02_backend_06_rust_and_python_backends_reject_case_colliding_modules_safely(self):
      cases = (
         (generate_rust, 'obsolete.rs', 'SDL filenames map to the same Rust module: foo'),
         (generate_python, 'obsolete.py', 'SDL filenames map to the same Python module: foo'),
      )
      for generate, obsolete_file, error in cases:
         with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / 'schemas'
            output_path = pathlib.Path(directory) / 'out'
            input_path.mkdir()
            (input_path / 'obsolete.sdl').write_text(
               'message Obsolete {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
            generate(str(input_path), str(output_path))
            before_collision = {path.name: path.read_bytes()
               for path in output_path.iterdir() if path.is_file()}
            self.assertIn(obsolete_file, before_collision)

            (input_path / 'Foo.sdl').write_text(
               'message UpperFile {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
            (input_path / 'foo.sdl').write_text(
               'message LowerFile {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
            with self.assertRaisesRegex(ValueError, error):
               generate(str(input_path), str(output_path))
            self.assertEqual(before_collision, {path.name: path.read_bytes()
               for path in output_path.iterdir() if path.is_file()})

   def test_02_backend_07_c_backend_rejects_case_colliding_header_guards(self):
      with tempfile.TemporaryDirectory() as directory:
         input_path = pathlib.Path(directory) / 'schemas'
         output_path = pathlib.Path(directory) / 'out'
         input_path.mkdir()
         for filename, type_name in (('Packet.sdl', 'FirstPacket'),
                                     ('packet.sdl', 'SecondPacket')):
            (input_path / filename).write_text(
               'message ' + type_name + ' {\n   1: required int32 value;\n}\n',
               encoding='utf-8')
         with self.assertRaisesRegex(ValueError,
               'C SDL filenames map to the same header guard: PACKET_H'):
            generate_c(str(input_path), str(output_path))
         self.assertFalse(output_path.exists())

   def test_02_backend_08_python_backend_rejects_imported_type_name_collisions(self):
      parser = MsgParser()
      parser.parse_text("""
enum SdlMessage {
   FIRST = 1;
}
message Packet {
   1: required int32 value;
}
""")
      with self.assertRaisesRegex(ValueError,
            'Python type name conflicts with an imported runtime type: SdlMessage'):
         PythonBackend(parser).generate()

   def test_02_backend_09_python_backend_rejects_keyword_and_non_normalized_identifiers(self):
      cases = (
         ("""
message Packet {
   1: required int32 class;
}
""", 'Python backend cannot emit invalid field identifier: Packet.class'),
         ("""
enum State {
   class = 1;
}
""", 'Python backend cannot emit invalid or reserved enum item: State.class'),
         ("""
message Packet {
   1: required int32 ²field;
}
""", 'Python backend cannot emit invalid field identifier: Packet.²field'))
      for source, error in cases:
         parser = MsgParser()
         parser.parse_text(source)
         with self.assertRaisesRegex(ValueError, error):
            PythonBackend(parser).generate()

   def test_02_backend_10_python_backend_rejects_runtime_member_and_enum_reserved_names(self):
      cases = (
         ("""
message Packet {
   1: required int32 _SDL_NAME;
}
""", 'Python field name conflicts with runtime metadata or methods: Packet._SDL_NAME'),
         ("""
message Packet {
   1: required int32 _SDL_FIELDS;
}
""", 'Python field name conflicts with runtime metadata or methods: Packet._SDL_FIELDS'),
         ("""
message Packet {
   1: required int32 __class__;
}
""", 'Python field name conflicts with runtime metadata or methods: Packet.__class__'),
         ("""
enum State {
   mro = 1;
}
""", 'Python backend cannot emit invalid or reserved enum item: State.mro'),
         ("""
enum State {
   _ignore_ = 1;
}
""", 'Python backend cannot emit invalid or reserved enum item: State._ignore_'))
      for source, error in cases:
         parser = MsgParser()
         parser.parse_text(source)
         with self.assertRaisesRegex(ValueError, error):
            PythonBackend(parser).generate()

   def test_02_backend_11_rust_backend_rejects_enum_variant_name_collisions(self):
      parser = MsgParser()
      parser.parse_text("""
enum State {
   HTTP_OK = 1;
   Http_Ok = 2;
}
""")
      with self.assertRaisesRegex(ValueError,
            'Rust enum variant collision after conversion in State: Http_Ok and HTTP_OK'):
         RustBackend(parser).generate()

   def test_02_backend_12_rust_backend_rejects_type_name_collisions_from_anonymous_types(self):
      parser = MsgParser()
      parser.parse_text("""
message Packet {
   1: required struct {
      1: required int32 code;
   } item;
}
message Packet1 {
   1: required int32 code;
}
""")
      with self.assertRaisesRegex(ValueError,
            r'Rust type name collision after conversion: Packet1 and Packet\$1'):
         RustBackend(parser).generate()

   def test_02_backend_13_rust_backend_rejects_imported_type_name_collisions(self):
      parser = MsgParser()
      parser.parse_text('''\nmessage String {\n   1: required int32 value;\n}\n''')
      with self.assertRaisesRegex(ValueError,
            'Rust type name conflicts with a runtime or standard type: String'):
         RustBackend(parser).generate()

   def test_02_backend_14_rust_backend_rejects_enum_variants_starting_with_digits(self):
      parser = MsgParser()
      parser.parse_text("""
enum State {
   2FAST = 1;
}
""")
      with self.assertRaisesRegex(ValueError,
            'Rust backend cannot emit invalid enum variant: State.2FAST'):
         RustBackend(parser).generate()

   def test_02_backend_15_rust_backend_rejects_primitive_type_name_collisions(self):
      parser = MsgParser()
      parser.parse_text("""
message i32 {
   1: required int64 value;
}
""")
      with self.assertRaisesRegex(ValueError,
            'Rust type name conflicts with a runtime or standard type: i32'):
         RustBackend(parser).generate()

   def test_02_backend_16_rust_backend_rejects_non_xid_unicode_identifiers(self):
      parser = MsgParser()
      parser.parse_text("""
message Packet {
   1: required int32 ²field;
}
""")
      with self.assertRaisesRegex(ValueError,
            'Rust backend cannot emit invalid field identifier: Packet.²field'):
         RustBackend(parser).generate()

   def test_02_backend_17_rust_backend_rejects_reserved_field_names(self):
      parser = MsgParser()
      parser.parse_text("""
message Packet {
   1: required int32 type;
}
""")
      with self.assertRaisesRegex(ValueError,
            'Rust backend cannot use reserved word as field name: Packet.type'):
         RustBackend(parser).generate()

   def test_03_limits_01_fixed_array_rejects_variable_wire_type_with_field_name(self):
      parser = MsgParser()
      with self.assertRaisesRegex(ValueError,
            'fixed array element has variable wire size in Container.items'):
         parser.parse_text('''
message DynamicItem {
   1: optional string name;
}
message Container {
   1: required DynamicItem[2] items;
}
''')

   def test_03_limits_02_fixed_array_dimensions_must_be_positive(self):
      parser = MsgParser()
      with self.assertRaisesRegex(ValueError,
            'fixed array dimensions must be positive uint32 values in Container.items'):
         parser.parse_text('''
message Container {
   1: required int32[0] items;
}
''')

   def test_03_limits_03_fixed_array_wire_size_accepts_uint32_max(self):
      parser = MsgParser()
      parser.parse_text('''
message Unit {
   1: required int8 value;
}
message Container {
   1: required Unit[4294967295] items;
}
''')
      self.assertEqual(parser.fixed_wire_size('Unit'), 1)
      self.assertEqual(parser.fixed_wire_size('Container'), 0xFFFFFFFF)

   def test_03_limits_04_fixed_array_wire_size_must_fit_field_length(self):
      parser = MsgParser()
      with self.assertRaisesRegex(ValueError,
            'fixed array wire size exceeds uint32 in Container.items'):
         parser.parse_text('''
message Container {
   1: required int64[2147483648] items;
}
''')
      parser = MsgParser()
      with self.assertRaisesRegex(ValueError,
            'fixed array wire size exceeds uint32 in Container.items'):
         parser.parse_text('''
message Unit {
   1: required int8 value;
}
message Container {
   1: required Unit[4294967295][2] items;
}
''')

   def test_02_backend_ada_case_insensitive_names_and_composite_names(self):
      sources = (
         'message Foo {\n}\nmessage foo {\n}\n',
         'message X {\n 1: required int32 item;\n 2: required int32 Item;\n}\n',
         'enum X {\n A = 0;\n a = 1;\n}\n',
         'message A_B {\n 1: repeated int32 C;\n}\nmessage A {\n 1: repeated int32 B_C;\n}\n',
         'enum A_B {\n C = 0;\n}\nenum A {\n B_C = 1;\n}\n',
      )
      for source in sources:
         with self.subTest(source=source):
            with self.assertRaisesRegex(ValueError, 'collision'):
               schema = MsgParser()
               schema.parse_text(source)
               AdaBackend(schema, 'SDL_Test').validate()

   def test_02_backend_ada_regeneration_and_validation_preserve_files(self):
      with tempfile.TemporaryDirectory() as directory:
         root = pathlib.Path(directory)
         source = root / 'input'
         output = root / 'output'
         source.mkdir()
         (source / 'one.sdl').write_text('message Foo {\n}\n')
         generate_ada(str(source), str(output))
         self.assertTrue((output / 'sdl_one.ads').exists())
         custom = output / 'manual.ads'
         custom.write_text('-- hand written\n')
         (source / 'one.sdl').rename(source / 'two.sdl')
         generate_ada(str(source), str(output))
         self.assertFalse((output / 'sdl_one.ads').exists())
         self.assertTrue(custom.exists())
         (source / 'TWO.sdl').write_text('message Other {\n}\n')
         with self.assertRaisesRegex(ValueError, 'Ada package name collision'):
            generate_ada(str(source), str(output))
         self.assertTrue((output / 'sdl_two.ads').exists())
         self.assertTrue(custom.exists())


if __name__ == '__main__':
   unittest.main()
