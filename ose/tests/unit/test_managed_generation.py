"""Managed backend validation independent of Java/GNAT toolchains."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ose.modeling.managed_containers import tool, validate_names
from ose.modeling.native_client_build import writable_manifest


class ManagedNamesTests(unittest.TestCase):
    def test_language_specific_keywords(self):
        node = {'inputs': {'input': {}}, 'outputs': {}}
        validate_names('java', 'source', node, {'class': 'app.Handler'})
        validate_names('ada', 'source', node, {'unit': 'App.Handler'})
        for language, identity in [('java', {'class': 'app.class'}), ('ada', {'unit': 'App.BEGIN'}), ('ada', {'unit': 'App.Bad__Name'}), ('ada', {'unit': 'Main'}), ('ada', {'unit': 'GNAT.Handler'}), ('java', {'class': 'java.Handler'})]:
            with self.assertRaises(ValueError): validate_names(language, 'source', node, identity)
        with self.assertRaises(ValueError): validate_names('ada', 'source', {'inputs': {'Input': {}, 'input': {}}, 'outputs': {}}, {'unit': 'Handler'})
        with self.assertRaises(ValueError): validate_names('java', 'source', node, {'class': 'org.sdlose.Handler'})


class ManagedBuildTests(unittest.TestCase):
    def test_java_home_selects_all_jdk_tools_with_spaces(self):
        with patch.dict(os.environ, {'JAVA_HOME': '/opt/jdk 17'}, clear=True):
            for variable, executable in (('JAVA', 'java'), ('JAVAC', 'javac'), ('JAR', 'jar')):
                self.assertEqual(tool(variable, executable), ['/opt/jdk 17/bin/' + executable])
            self.assertEqual(tool('CC', 'cc'), ['cc'])

    def test_explicit_tools_override_java_home(self):
        with patch.dict(os.environ, {'JAVA_HOME': '/opt/jdk 17', 'JAVAC': '"/opt/custom jdk/bin/javac" -J-Xmx128m'}, clear=True):
            self.assertEqual(tool('JAVAC', 'javac'), ['/opt/custom jdk/bin/javac', '-J-Xmx128m'])
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tool('JAVA', 'java'), ['java'])

    def test_native_sdk_staging_preserves_read_only_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'sdk/client/rust'
            (source / 'src').mkdir(parents=True)
            manifest = source / 'Cargo.toml'
            manifest.write_text('[package]\nname = "probe"\n')
            rust = source / 'src/lib.rs'
            rust.write_text('pub fn probe() {}\n')
            manifest.chmod(0o444)
            rust.chmod(0o444)
            staged = writable_manifest(root / 'sdk', root / 'application build')
            self.assertEqual(staged.read_bytes(), manifest.read_bytes())
            self.assertEqual((staged.parent / 'src/lib.rs').read_bytes(), rust.read_bytes())
            self.assertEqual(sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()), ['Cargo.toml', 'src/lib.rs'])
            rust.chmod(0o644)
            rust.write_text('pub fn probe() -> bool { true }\n')
            writable_manifest(root / 'sdk', root / 'application build')
            self.assertEqual((staged.parent / 'src/lib.rs').read_bytes(), rust.read_bytes())
