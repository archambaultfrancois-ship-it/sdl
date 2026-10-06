import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / 'gen'))

from c_backend import CBackend, generate_c
from generator import MsgParser, canonical_type_descriptor
from rust_backend import RustBackend, generate_rust
from python_backend import PythonBackend, generate_python


class GeneratorValidationTests(unittest.TestCase):
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
      with self.assertRaisesRegex(ValueError, 'type name exceeds uint16 in schema descriptor'):
         MsgParser().parse_text('message ' + long_name + ' {\n}\n')

   def test_03_limits_06_field_name_length_accepts_uint16_max_bytes(self):
      name = 'x' * 0xFFFF
      parser = MsgParser()
      parser.parse_text('message Packet {\n   1: required int32 ' + name + ';\n}\n')
      self.assertEqual(parser.messages['Packet'].fields[0].name, name)

      with self.assertRaisesRegex(ValueError,
            'field name in Packet exceeds uint16 in schema descriptor'):
         MsgParser().parse_text('message Packet {\n   1: required int32 ' +
            name + 'x;\n}\n')

   def test_03_limits_07_schema_descriptor_u16_counts_are_validated(self):
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
      field_lengths = [65520] * 15 + [65510]

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
            'wire descriptor exceeds 1 MiB for Packet'):
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
   1: required int32 encode_payload;
}
""", 'Python field name conflicts with runtime metadata or methods: Packet.encode_payload'),
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


if __name__ == '__main__':
   unittest.main()
