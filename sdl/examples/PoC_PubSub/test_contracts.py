"""Contract manifests and offline validation share the broker's policy."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sdl_runtime import description
from contracts import DEFAULT_CONTRACT, TopicContract, load_contract
from resolution import IntegrationError
from temperature_v1 import Temperature as V1
from temperature_v2 import Temperature as V2
from temperature_bad import Temperature as Bad


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.document = json.loads(DEFAULT_CONTRACT.read_text())
        self.manifest = self.folder / 'topic.json'
        (self.folder / self.document['baseline_catalogue']).write_text(description([V1]))
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.document))

    def assert_config_invalid(self):
        with self.assertRaises(IntegrationError) as caught:
            load_contract(self.manifest)
        self.assertEqual(caught.exception.code, 'CONFIG_INVALID')

    def test_example_baseline_matches_canonical_emission(self):
        contract = load_contract()
        self.assertEqual(contract.baseline, description([V1]))
        self.assertEqual(contract.metadata()['baseline_catalogue_id'], '88330348')
        self.assertEqual(contract.root_type, 'Temperature')

    def test_relative_path_independent_of_current_directory(self):
        self.document['topic'] = 'custom/measurements'
        self.write_manifest()
        contract = load_contract(self.manifest)
        self.assertEqual(contract.topic, 'custom/measurements')
        self.assertEqual(contract.baseline, description([V1]))

    def test_compatible_rename_and_addition_and_diagnostic(self):
        contract = load_contract(self.manifest)
        contract.prepare_candidate(description([V2]))
        with self.assertRaises(IntegrationError) as caught:
            contract.prepare_candidate(description([Bad]))
        self.assertEqual(caught.exception.code, 'INCOMPATIBLE_SCHEMA')
        self.assertEqual(caught.exception.diagnostic['code'], 'FIELD_TYPE_MISMATCH')
        self.assertEqual(caught.exception.diagnostic['path'], 'Temperature.celsius')

    def test_required_policy(self):
        text = description([V1]).replace('2:fl64:celsius;', '')
        load_contract(self.manifest).prepare_candidate(text)
        self.document['require_required'] = True
        self.write_manifest()
        with self.assertRaises(IntegrationError) as caught:
            load_contract(self.manifest).prepare_candidate(text)
        self.assertEqual(caught.exception.code, 'TOPIC_CONTRACT')
        self.assertEqual(caught.exception.diagnostic['code'], 'REQUIRED_FIELD_MISSING')

    def test_single_root_rule_and_invalid_candidate(self):
        contract = load_contract()
        for text in ("SDL2|Other{}", description([V1]).replace('SDL2|', 'SDL2|Other{}|', 1)):
            with self.assertRaises(IntegrationError) as caught:
                contract.prepare_candidate(text)
            self.assertEqual(caught.exception.code, 'TOPIC_CONTRACT')
        with self.assertRaises(IntegrationError) as caught:
            contract.prepare_candidate('invalid')
        self.assertEqual(caught.exception.diagnostic['code'], 'INVALID_PUBLISHER_CATALOGUE')

    def test_invalid_manifest_fields(self):
        original = dict(self.document)
        for key, value in (('version', 2), ('version', True), ('topic', ''),
                           ('root_type', 'Other'), ('require_required', 'false'),
                           ('compatibility', 'anything'), ('baseline_catalogue', 1)):
            with self.subTest(key=key, value=value):
                self.document = dict(original, **{key: value})
                self.write_manifest()
                self.assert_config_invalid()
        self.document = dict(original, unexpected=True)
        self.write_manifest(); self.assert_config_invalid()
        del self.document['unexpected']; del self.document['root_type']
        self.write_manifest(); self.assert_config_invalid()

    def test_bad_json_duplicate_keys_missing_or_invalid_baseline(self):
        for text in ('[', '[]', '{"version":1,"version":1}'):
            self.manifest.write_text(text)
            self.assert_config_invalid()
        self.write_manifest()
        path = self.folder / self.document['baseline_catalogue']
        path.write_text('invalid'); self.assert_config_invalid()
        path.unlink(); self.assert_config_invalid()

    def test_cli_exit_status_and_machine_readable_output(self):
        script = Path(__file__).resolve().parent / 'contracts.py'
        for message_type, expected in ((V1, 0), (V2, 0), (Bad, 1)):
            candidate = self.folder / 'candidate.sdl2'
            candidate.write_text(description([message_type]))
            process = subprocess.run([sys.executable, str(script), 'validate',
                                      '--contract', str(self.manifest), '--catalogue', str(candidate)],
                                     cwd=self.folder, capture_output=True, text=True)
            self.assertEqual(process.returncode, expected, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result['ok'], expected == 0)
            if expected:
                self.assertEqual(result['diagnostic']['code'], 'FIELD_TYPE_MISMATCH')
        candidate.unlink()
        process = subprocess.run([sys.executable, str(script), 'validate', '--catalogue', str(candidate)],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)['code'], 'CONFIG_INVALID')


if __name__ == '__main__':
    unittest.main()
