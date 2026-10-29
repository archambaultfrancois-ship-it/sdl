"""Source-format round trips, codegen parity and independent XSD validation."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'sdlc'), str(ROOT / 'runtime/python')]
from generator import parse_schemas, canonical_type_descriptor
from schema_formats import generate_schema_formats, schema_document, XS, SDL, xml_name

HAS_YAML = importlib.util.find_spec('yaml') is not None
HAS_XMLSCHEMA = importlib.util.find_spec('xmlschema') is not None
FORMATS = ('json', 'xsd') + (('yaml',) if HAS_YAML else ())

PROBE = '''/** Probe catalogue. */
package example;
/** Operating mode. */
enum Mode { OFF = -1; /** Inactive. */ ON = 7; /** Active. */ }
const AXES = 1 + 1; /** Axis count. */
alias Sample = int16; /** Measurement type. */
message Reading {
   uint64 sequence wire(encoding=varint);
   Sample[AXES] coordinates;
   optional string label;
   packed fl32 samples;
   Mode mode;
   c64 phasor;
   optional struct { int32 code; } detail; /** Additional details. */
}
'''


class SchemaFormatTests(unittest.TestCase):
   @classmethod
   def setUpClass(cls):
      cls.temporary = tempfile.TemporaryDirectory()
      cls.addClassCleanup(cls.temporary.cleanup)
      cls.root = Path(cls.temporary.name)
      cls.probe = cls.root / 'probe.sdl'
      cls.probe.write_text(PROBE, encoding='utf-8')
      cls.sources = {'core': ROOT / 'schemas', 'packages': ROOT / 'tests/fixtures/packages',
         'representation': ROOT / 'tests/fixtures/representation', 'probe': cls.probe}
      for name, source in cls.sources.items():
         generate_schema_formats(source, cls.root / name, FORMATS)

   def signature(self, source):
      return [(base, identifier, canonical_type_descriptor(schema), schema_document(schema), schema.dependencies())
         for base, identifier, schema in parse_schemas(source)]

   def test_round_trip_json_and_xsd_preserve_catalogues_and_metadata(self):
      for name, source in self.sources.items():
         for kind in ('json', 'xsd'):
            with self.subTest(fixture=name, format=kind):
               self.assertEqual(self.signature(source), self.signature(self.root / name / kind))

   @unittest.skipUnless(HAS_YAML, 'optional PyYAML is not installed')
   def test_round_trip_yaml_preserves_catalogues_and_metadata(self):
      for name, source in self.sources.items():
         with self.subTest(fixture=name):
            self.assertEqual(self.signature(source), self.signature(self.root / name / 'yaml'))

   def test_textual_sdl_import_round_trips_every_source_format(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         for name, source in self.sources.items():
            inputs = (source,) + tuple(self.root / name / kind for kind in FORMATS)
            for index, input_path in enumerate(inputs):
               with self.subTest(fixture=name, source=str(input_path)):
                  output = directory / name / str(index)
                  generate_schema_formats(input_path, output, ('sdl',))
                  self.assertEqual(self.signature(source), self.signature(output / 'sdl'))

   def test_textual_sdl_rejects_metadata_loss_before_replacing_outputs(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         generate_schema_formats(self.probe, directory / 'out', ('sdl',))
         expected = {path: path.read_bytes() for path in (directory / 'out').rglob('*.sdl')}
         for document, error in (
               (dict(format='sdl-schema', messages={'Root': dict(doc='Literal */ delimiter', fields=[])}),
                  'closing comment delimiter'),
               (dict(format='sdl-schema', messages={
                  'Root': {'fields': [dict(name='child', type='.Root$2')]}, 'Root$2': {'fields': []}}),
                  'losslessly')):
            with self.subTest(error=error):
               source = self.write_document(document, directory)
               with self.assertRaisesRegex(ValueError, error):
                  generate_schema_formats(source, directory / 'out', ('sdl',))
               self.assertEqual({path: path.read_bytes() for path in (directory / 'out').rglob('*.sdl')}, expected)

   def test_textual_sdl_preserves_references_to_nested_packages(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         source = directory / 'source'
         source.mkdir()
         (source / 'outer.sdl').write_text('package geo; message Outer { geo.detail.Point value; }')
         (source / 'point.sdl').write_text('package geo.detail; message Point { int32 x; }')
         generate_schema_formats(source, directory / 'out', ('sdl',))
         self.assertEqual(self.signature(source), self.signature(directory / 'out/sdl'))

   def test_version_is_implicit_and_explicit_versions_are_validated(self):
      expected = self.signature(self.probe)
      with tempfile.TemporaryDirectory() as temporary:
         for kind in FORMATS:
            original = self.root / 'probe' / kind / 'example' / ('probe.sdl.' + kind)
            if kind == 'xsd':
               tree = ET.parse(original)
               unit = tree.getroot().find('.//{' + SDL + '}unit')
               document = json.loads(unit.text)
            elif kind == 'json':
               document = json.loads(original.read_text())
            else:
               import yaml
               document = yaml.safe_load(original.read_text())
            self.assertNotIn('version', document)
            path = Path(temporary) / ('probe.sdl.' + kind)
            for present, version in ((False, None), (True, 1), (True, 2),
                  (True, 0), (True, True), (True, 1.0), (True, '1'), (True, None)):
               with self.subTest(format=kind, present=present, version=repr(version)):
                  value = dict(document)
                  if present:
                     value['version'] = version
                  if kind == 'xsd':
                     unit.text = json.dumps(value)
                     tree.write(path, encoding='unicode')
                  elif kind == 'json':
                     path.write_text(json.dumps(value))
                  else:
                     path.write_text(yaml.safe_dump(value, sort_keys=False))
                  if not present or type(version) is int and version == 1:
                     self.assertEqual(self.signature(path), expected)
                  else:
                     with self.assertRaisesRegex(ValueError, 'version'):
                        parse_schemas(path)

   def test_exports_omit_defaults_and_preserve_nondefault_ids_and_zero_values(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         source = directory / 'compact.sdl'
         source.write_text('''const AXES=1;
alias Count=uint32;
enum State { OFF=0; ON=1; /** Active. */ }
message Empty {}
/** Measurement. */
message Reading {
 3: Count sequence wire(align=1,pad_after=0); /** Sequence number. */
 uint8[AXES] axes;
 9: optional string note;
 2: repeated uint16 counts;
 12: packed fl32 values wire(bits=12,q=0.25);
 required int8 tail;
}
''', encoding='utf-8')
         generate_schema_formats(source, directory / 'export', FORMATS)
         expected_fields = [
            dict(id=3, name='sequence', type='uint32', wire={'align': 1, 'pad_after': 0}, doc='Sequence number.'),
            dict(name='axes', type='uint8', dimensions=[1]),
            dict(id=9, name='note', type='string', modifier='optional'),
            dict(id=2, name='counts', type='uint16', modifier='repeated'),
            dict(id=12, name='values', type='fl32', modifier='packed', wire={'bits': 12, 'q': '0.25'}),
            dict(name='tail', type='int8'),
         ]
         for kind in FORMATS:
            with self.subTest(format=kind):
               self.assertEqual(self.signature(source), self.signature(directory / 'export' / kind))
               path = directory / 'export' / kind / ('compact.sdl.' + kind)
               if kind == 'xsd':
                  root = ET.parse(path).getroot()
                  fields = [json.loads(element.text) for element in root.iter('{' + SDL + '}field')]
                  self.assertEqual(fields, [{key: value for key, value in field.items() if key != 'doc'}
                     for field in expected_fields])
                  for element in root.findall('.//{' + XS + '}sequence/{' + XS + '}element'):
                     self.assertNotEqual(element.get('minOccurs'), '1')
                     self.assertNotEqual(element.get('maxOccurs'), '1')
                  continue
               if kind == 'json':
                  document = json.loads(path.read_text())
               else:
                  import yaml
                  document = yaml.safe_load(path.read_text())
               self.assertNotIn('package', document)
               self.assertNotIn('doc', document)
               self.assertEqual(document['constants'], {'AXES': {'value': 1}})
               self.assertEqual(document['aliases'], {'Count': {'target': 'uint32'}})
               self.assertEqual(document['messages']['Empty'], {'fields': []})
               self.assertEqual(document['messages']['Reading'], dict(doc='Measurement.', fields=expected_fields))
               self.assertEqual(document['enums']['State']['values'], [
                  dict(name='OFF', value=0), dict(name='ON', value=1, doc='Active.')])
         # Empty top-level declaration tables are optional, unlike message fields.
         simple = json.loads((self.root / 'core/json/empty_message.sdl.json').read_text())
         for key in ('constants', 'aliases', 'enums'):
            self.assertNotIn(key, simple)

   def test_xsd_still_accepts_explicit_default_metadata_and_cardinalities(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         shutil.copytree(self.root / 'packages/xsd', directory / 'legacy')
         for path in (directory / 'legacy').rglob('*.sdl.xsd'):
            tree = ET.parse(path)
            for element in tree.getroot().iter('{' + SDL + '}unit'):
               metadata = json.loads(element.text)
               metadata.setdefault('version', 1)
               metadata.setdefault('package', '')
               for key in ('constants', 'aliases'):
                  metadata.setdefault(key, {})
                  for value in metadata[key].values():
                     value.setdefault('doc', '')
               element.text = json.dumps(metadata)
            for message in tree.getroot().findall('{' + XS + '}complexType'):
               previous_id = 0
               for element in message.iter('{' + SDL + '}field'):
                  metadata = json.loads(element.text)
                  metadata.setdefault('id', previous_id + 1)
                  previous_id = metadata['id']
                  metadata.setdefault('modifier', 'required')
                  metadata.setdefault('dimensions', [])
                  metadata.setdefault('wire', {})
                  element.text = json.dumps(metadata)
            for element in tree.getroot().findall('.//{' + XS + '}sequence/{' + XS + '}element'):
               element.set('minOccurs', element.get('minOccurs', '1'))
               element.set('maxOccurs', element.get('maxOccurs', '1'))
            tree.write(path, encoding='unicode')
         self.assertEqual(self.signature(self.sources['packages']), self.signature(directory / 'legacy'))

   def test_all_six_backends_generate_identical_files(self):
      from c_backend import generate_c
      from rust_backend import generate_rust
      from python_backend import generate_python
      from matlab_backend import generate_matlab
      from java_backend import generate_java
      from ada_backend import generate_ada
      with tempfile.TemporaryDirectory() as temporary:
         output = Path(temporary)
         for name in ('probe', 'packages', 'representation'):
            for backend in (generate_c, generate_rust, generate_python, generate_matlab, generate_java, generate_ada):
               with self.subTest(fixture=name, backend=backend.__name__):
                  def generated(source):
                     backend(source, output)
                     return {str(path.relative_to(output)): path.read_bytes()
                        for path in output.rglob('*') if path.is_file()}
                  expected = generated(self.sources[name])
                  for kind in FORMATS:
                     self.assertEqual(expected, generated(self.root / name / kind), kind)

   def test_binary_wire_is_identical_for_every_format(self):
      from python_backend import generate_python
      from sdl_runtime import prepare, description, encode, decode
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         wires = []
         for source in (self.probe,) + tuple(self.root / 'probe' / kind for kind in FORMATS):
            generate_python(source, directory)
            module = types.ModuleType('_schema_formats_probe')
            sys.modules[module.__name__] = module
            try:
               path = directory / 'example/probe.py'
               exec(compile(path.read_text(), str(path), 'exec'), module.__dict__)
               value = module.Reading(sequence=2**63 + 3, coordinates=[-32768, 32767],
                  label='échantillon', samples=[0.25, -1.5], mode=module.Mode.ON,
                  phasor=complex(3, -4))
               context = prepare(description(module.Reading), [module.Reading])
               wire = encode(context, value)
               self.assertEqual(encode(context, decode(context, wire)), wire)
               wires.append(wire)
            finally:
               del sys.modules[module.__name__]
         self.assertTrue(wires[0])
         self.assertTrue(all(wire == wires[0] for wire in wires))

   def test_mixed_sources_and_only_dedicated_suffixes(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         sources = sorted((self.root / 'packages/json').rglob('*.sdl.json'))
         shutil.copy(sources[0], directory / 'shapes.sdl.json')
         # Remaining units may have the same basename when their packages differ.
         for index, source in enumerate(sources[1:]):
            target = directory / str(index)
            target.mkdir()
            relative = source.relative_to(self.root / 'packages/json')
            other = self.root / 'packages/xsd' / relative.with_suffix('.xsd')
            shutil.copy(other, target / 'shapes.sdl.xsd')
         for suffix in ('json', 'yaml', 'xsd'):
            (directory / ('deployment.' + suffix)).write_text('not a schema')
         self.assertEqual(self.signature(self.sources['packages']), self.signature(directory))
         shutil.copy(sources[0], directory / 'duplicate.sdl.json')
         with self.assertRaisesRegex(ValueError, 'duplicate'):
            parse_schemas(directory)

   def write_document(self, document, directory, name='input.sdl.json'):
      path = Path(directory) / name
      path.write_text(json.dumps(document), encoding='utf-8')
      return path

   def test_native_defaults_and_shared_validation(self):
      document = dict(format='sdl-schema', version=1, messages={'Point': {'fields': [
         {'type': 'int32', 'name': 'x'}, {'id': 5, 'type': 'int32', 'name': 'y'},
         {'type': 'int32', 'name': 'z'}]}})
      with tempfile.TemporaryDirectory() as directory:
         path = self.write_document(document, directory)
         self.assertEqual(canonical_type_descriptor(parse_schemas(path)[0][2]),
            b'SDL2|Point{1:int32:x;5:int32:y;6:int32:z;}')
         failures = (
            ('field modifier', lambda d: d['messages']['Point']['fields'][0].update(modifier='unknown')),
            ('duplicate field', lambda d: d['messages']['Point']['fields'][1].update(id=1)),
            ('unknown type', lambda d: d['messages']['Point']['fields'][0].update(type='Missing')),
            ('integer', lambda d: d['messages']['Point']['fields'][0].update(id=True)),
            ('invalid wire', lambda d: d['messages']['Point']['fields'][0].update(wire={'q': '0.5,align=4'})),
            ('wire bits', lambda d: d['messages']['Point']['fields'][0].update(wire={'bits': 33})),
            ('unknown keys', lambda d: d.update(typo=True)),
            ('version', lambda d: d.update(version=True)),
            ('declaration name', lambda d: d['messages'].update({'bad-name': {'fields': []}})),
            ('declaration name', lambda d: d.update(constants={'123': {'value': 2}})),
            ('declaration name', lambda d: d.update(aliases={'123': {'target': 'int32'}})),
         )
         for message, change in failures:
            with self.subTest(error=message):
               modified = copy.deepcopy(document)
               change(modified)
               with self.assertRaisesRegex(ValueError, message):
                  parse_schemas(self.write_document(modified, directory))

   def test_constants_aliases_and_cross_format_resolution(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         (directory / 'defs.sdl').write_text('package p; const N = 2; alias Count = uint32;')
         document = dict(format='sdl-schema', version=1, package='p', messages={'Point': {'fields': [
            dict(type='Count', name='values', dimensions=['N'])]}})
         self.write_document(document, directory)
         schema = next(schema for base, _, schema in parse_schemas(directory) if base == 'input')
         self.assertEqual(canonical_type_descriptor(schema), b'SDL2|p.Point{1:uint32[2]:values;}')

   def test_duplicate_keys_and_nonfinite_json_are_rejected(self):
      with tempfile.TemporaryDirectory() as directory:
         path = Path(directory) / 'bad.sdl.json'
         for text, error in (
               ('{"format":"sdl-schema","version":1,"version":1}', 'duplicate mapping key'),
               ('{"format":"sdl-schema","version":1,"doc":NaN}', 'non-finite'),
               ('{"format":"sdl-schema","version":1,"doc":1e999}', 'finite')):
            with self.subTest(text=text):
               path.write_text(text)
               with self.assertRaisesRegex(ValueError, error):
                  parse_schemas(path)

   @unittest.skipUnless(HAS_YAML, 'optional PyYAML is not installed')
   def test_yaml_safe_loading_and_json_scalar_rules(self):
      with tempfile.TemporaryDirectory() as directory:
         path = Path(directory) / 'probe.sdl.yaml'
         path.write_text('''format: sdl-schema
version: 1
enums:
  Mode:
    values:
      - {name: ON, value: 1}
      - {name: OFF, value: 0}
messages:
  Reading:
    fields:
      - {type: Mode, name: enabled}
''')
         schema = parse_schemas(path)[0][2]
         self.assertEqual(schema.enums['Mode'].pairs, [('ON', '1'), ('OFF', '0')])
         for text, error in (
               ('format: sdl-schema\nversion: 1\nversion: 1', 'duplicate mapping key'),
               ('!!python/object/apply:os.system [echo unsafe]', 'invalid YAML'),
               ('&self [*self]', 'recursive|unconstructable'),
               ('format: sdl-schema\nversion: 1\ndoc: !!float .nan', 'finite'),
               ('format: sdl-schema\nversion: 1\ndoc: 2026-10-28', None)):
            with self.subTest(text=text):
               path.write_text(text)
               if error:
                  with self.assertRaisesRegex(ValueError, error):
                     parse_schemas(path)
               else:
                  self.assertEqual(parse_schemas(path)[0][2].doc, '2026-10-28')

   def test_json_and_xsd_use_only_the_standard_library(self):
      with tempfile.TemporaryDirectory() as directory:
         cli = [sys.executable, '-S', str(ROOT / 'sdlc/generator.py')]
         environment = dict(os.environ, PYTHONPATH='')
         result = subprocess.run(cli + ['-sdl', '-json', '-xsd', str(self.probe), directory],
            env=environment, capture_output=True, text=True)
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertEqual(self.signature(self.probe), self.signature(Path(directory) / 'sdl'))
         result = subprocess.run(cli + ['-python', str(Path(directory) / 'xsd'),
            str(Path(directory) / 'bindings')], env=environment, capture_output=True, text=True)
         self.assertEqual(result.returncode, 0, result.stderr)
         result = subprocess.run(cli + ['-yaml', str(self.probe), str(Path(directory) / 'missing')],
            env=environment, capture_output=True, text=True)
         self.assertNotEqual(result.returncode, 0)
         self.assertIn('requires PyYAML', result.stderr)
         self.assertNotIn('Traceback', result.stderr)
         self.assertFalse((Path(directory) / 'missing').exists())

   def test_cli_combines_exports_and_language_backends(self):
      with tempfile.TemporaryDirectory() as directory:
         result = subprocess.run([sys.executable, str(ROOT / 'sdlc/generator.py'),
            '-json', '-xsd', '-python', str(self.probe), directory], capture_output=True, text=True)
         self.assertEqual(result.returncode, 0, result.stderr)
         for name in ('json/example/probe.sdl.json', 'xsd/example/probe.sdl.xsd', 'python/example/probe.py'):
            self.assertTrue((Path(directory) / name).is_file(), name)

   def test_stale_exports_are_removed_and_unrelated_files_preserved(self):
      with tempfile.TemporaryDirectory() as directory:
         output = Path(directory)
         formats = FORMATS + ('sdl',)
         generate_schema_formats(self.probe, output, formats)
         for kind in formats:
            suffix = '.sdl' if kind == 'sdl' else '.sdl.' + kind
            stale = output / kind / ('old' + suffix)
            shutil.copy(output / kind / 'example' / ('probe' + suffix), stale)
            (output / kind / ('user' + suffix)).write_text('unrelated user content')
         generate_schema_formats(self.probe, output, formats)
         for kind in formats:
            suffix = '.sdl' if kind == 'sdl' else '.sdl.' + kind
            self.assertFalse((output / kind / ('old' + suffix)).exists())
            self.assertEqual((output / kind / ('user' + suffix)).read_text(), 'unrelated user content')
         existing = {path: path.read_bytes() for path in output.rglob('*') if path.is_file()}
         bad = output / 'bad.sdl.json'
         bad.write_text('{}')
         with self.assertRaises(ValueError):
            generate_schema_formats(bad, output, formats)
         self.assertTrue(all(path.read_bytes() == text for path, text in existing.items()))

   def test_xsd_rejects_unsupported_or_inconsistent_structures(self):
      path = self.root / 'probe/xsd/example/probe.sdl.xsd'
      original = path.read_text()
      with tempfile.TemporaryDirectory() as directory:
         target = Path(directory) / 'probe.sdl.xsd'
         cases = [original.replace('type="xs:unsignedLong"', 'type="xs:string"', 1),
            original.replace('minOccurs="0" maxOccurs="unbounded"', 'minOccurs="1" maxOccurs="unbounded"', 1),
            original.replace('<xs:sequence>', '<xs:choice>', 1).replace('</xs:sequence>', '</xs:choice>', 1),
            original.replace('<xs:element name="example.Reading"', '<xs:element minOccurs="1" name="example.Reading"'),
            original.replace('xmlns:xs="' + XS + '"', 'xmlns:other="' + XS + '" xmlns:xs="urn:wrong"').replace('<xs:', '<other:').replace('</xs:', '</other:'),
            '<!DOCTYPE x [<!ENTITY x "invalid">]>\n' + original,
            '<xs:schema xmlns:xs="' + XS + '"/>']
         for index, text in enumerate(cases):
            with self.subTest(case=index):
               target.write_text(text)
               with self.assertRaises(ValueError):
                  parse_schemas(target)

   def test_xsd_referenced_copies_must_match_the_input_graph(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         shutil.copytree(self.root / 'packages/xsd', directory / 'sources')
         target = directory / 'sources/radio/shapes.sdl.xsd'
         tree = ET.parse(target)
         # Change both the visible structure and metadata: valid as an individual
         # profile, but stale relative to dependent units' embedded copies.
         for element in tree.getroot().iter('{' + SDL + '}field'):
            data = json.loads(element.text)
            if data['type'] == 'int32':
               data['type'] = 'int16'
               element.text = json.dumps(data)
         for element in tree.getroot().iter('{' + XS + '}element'):
            if element.get('type') == 'xs:int':
               element.set('type', 'xs:short')
         tree.write(target, encoding='unicode')
         with self.assertRaisesRegex(ValueError, 'dependency copies disagree'):
            parse_schemas(directory / 'sources')

   def test_xml_names_are_reversible_without_collisions(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         source = directory / 'names.sdl'
         source.write_text('message État { int32 1; struct { int32 x; } inner; }\n'
            'message _sdl_c389746174 { État value; }', encoding='utf-8')
         generate_schema_formats(source, directory / 'out', ('json', 'xsd'))
         self.assertEqual(self.signature(source), self.signature(directory / 'out/xsd'))
         names = ['État', '_sdl_c389746174', '1', 'État$1', 'p.Point', '_sdl_builtin_c32']
         self.assertEqual(len({xml_name(name) for name in names}), len(names))

   def test_xsd_dimensions_and_xml_documentation_boundaries(self):
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         document = dict(format='sdl-schema', version=1, doc='Line one\rLine two',
            messages={'Grid': {'fields': [dict(name='values', type='uint8', dimensions=[1] * 255)]}})
         source = self.write_document(document, directory)
         generate_schema_formats(source, directory / 'out', ('xsd',))
         self.assertEqual(self.signature(source), self.signature(directory / 'out/xsd'))
         document['doc'] = 'Forbidden\x00character'
         source = self.write_document(document, directory)
         with self.assertRaisesRegex(ValueError, 'forbidden by XML'):
            generate_schema_formats(source, directory / 'bad', ('xsd',))

   def test_xsd_metadata_must_agree_with_resolved_types(self):
      original = (self.root / 'probe/xsd/example/probe.sdl.xsd').read_text()
      with tempfile.TemporaryDirectory() as temporary:
         target = Path(temporary) / 'probe.sdl.xsd'
         # An alias exists in SDL metadata, but has no XSD type declaration.
         text = original.replace('"type": "int16"', '"type": "example.Sample"')
         target.write_text(text.replace('type="xs:short"', 'type="example.Sample"'))
         with self.assertRaisesRegex(ValueError, 'structure disagrees'):
            parse_schemas(target)

   @unittest.skipUnless(HAS_XMLSCHEMA, 'optional xmlschema validator is not installed')
   def test_all_exported_xsds_are_valid_xml_schemas(self):
      import xmlschema
      for name in self.sources:
         for path in (self.root / name / 'xsd').rglob('*.sdl.xsd'):
            with self.subTest(file=str(path.relative_to(self.root))):
               xmlschema.XMLSchema(str(path))

   @unittest.skipUnless(HAS_XMLSCHEMA, 'optional xmlschema validator is not installed')
   def test_xml_values_obey_presence_bounds_arrays_and_complex_mapping(self):
      import xmlschema
      with tempfile.TemporaryDirectory() as temporary:
         directory = Path(temporary)
         source = directory / 'value.sdl'
         source.write_text('enum Mode { OFF=0; ON=7; }\nmessage Value { '
            'uint8 counter; optional string label; int16[2][2] grid; '
            'repeated string tags; packed bool flags; c32 z; Mode mode; }')
         generate_schema_formats(source, directory / 'out', ('xsd',))
         validator = xmlschema.XMLSchema(str(directory / 'out/xsd/value.sdl.xsd'))
         value = ('<Value><counter>255</counter><grid>'
            '<item><item>-32768</item><item>2</item></item>'
            '<item><item>3</item><item>32767</item></item></grid>'
            '<tags/><flags><item>true</item><item>false</item></flags>'
            '<z><real>1.25</real><imag>-2</imag></z><mode>ON</mode></Value>')
         self.assertTrue(validator.is_valid(value))
         self.assertTrue(validator.is_valid(value.replace('</counter>', '</counter><label/>')))
         for invalid in (value.replace('255', '256'), value.replace('<tags/>', ''),
               value.replace('<counter>255</counter>', ''), value.replace('<item>3</item>', ''),
               value.replace('<mode>ON</mode>', '<mode>7</mode>'),
               value.replace('<imag>-2</imag>', ''), value.replace('32767', '32768')):
            with self.subTest(xml=invalid):
               self.assertFalse(validator.is_valid(invalid))


if __name__ == '__main__':
   unittest.main(verbosity=2)
