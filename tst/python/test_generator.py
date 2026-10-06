import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / 'gen'))

from generator import MsgParser


class GeneratorValidationTests(unittest.TestCase):
   def test_anonymous_nested_structs_get_stable_logical_names(self):
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

   def test_fixed_array_rejects_variable_wire_type_with_field_name(self):
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

   def test_fixed_array_dimensions_must_be_positive(self):
      parser = MsgParser()
      with self.assertRaisesRegex(ValueError,
            'fixed array dimensions must be positive uint32 values in Container.items'):
         parser.parse_text('''
message Container {
   1: required int32[0] items;
}
''')

   def test_fixed_array_wire_size_must_fit_field_length(self):
      parser = MsgParser()
      with self.assertRaisesRegex(ValueError,
            'fixed array wire size exceeds uint32 in Container.items'):
         parser.parse_text('''
message Container {
   1: required int64[2147483648] items;
}
''')


if __name__ == '__main__':
   unittest.main()
