"""Preservation regressions independent of the implementation's draft tests."""
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('oc_adversarial', Path(__file__).resolve().parents[1] / 'bin/opencode_config.py')
oc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oc)


class PreservationAdversarial(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.directory = Path(self.scratch.name)
        self.target = self.directory / 'opencode.json'

    def test_recursive_edit_preserves_comments_and_unmanaged_values(self):
        source = '''{
  "provider": { /* owner provider notes */
    "llama-server": {
      "options": {"apiKey": "old", /* retain key comment */ "timeout": 321},
      "models": {"owner": {"name": "Keep me"}}
    },
    "owner": {"options": {"apiKey": "SYNTHETIC_OWNER"}}
  }
}'''
        self.target.write_text(source)
        oc.update_config(self.directory, {'provider': {'llama-server': {'options': {'apiKey': 'new'}}}})
        text = self.target.read_text()
        self.assertIn('/* owner provider notes */', text)
        self.assertIn('/* retain key comment */', text)
        self.assertIn('"timeout": 321', text)
        self.assertIn('"owner": {"name": "Keep me"}', text)
        self.assertIn('SYNTHETIC_OWNER', text)

    def test_comment_positions_and_many_new_fields_are_valid(self):
        cases = ['{ /* empty owner comment */ }', '{"x":1 // owner comment\n}',
                 '{"x":1, /* owner comment */ }', '\ufeff{\r\n"x":1\r\n}']
        for source in cases:
            with self.subTest(source=source):
                self.target.write_bytes(source.encode())
                patch = {'a': 1, 'b': 2, 'c': {'nested': 3}}
                oc.update_config(self.directory, patch)
                text = self.target.read_bytes().decode()
                if 'owner comment' in source:
                    self.assertIn('owner comment', text)
                if source.startswith('\ufeff'):
                    self.assertTrue(text.startswith('\ufeff'))
                    self.assertIn('\r\n"x":1\r\n', text.replace(',', ''))
                effective = oc.read_effective(self.directory)
                for key, value in patch.items():
                    self.assertEqual(effective[key], value)

    def test_explicit_null_is_not_a_missing_object(self):
        self.target.write_text('{"provider":null}')
        before = self.target.read_bytes()
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.directory, {'provider': {'llama-server': {'name': 'ODS'}}})
        self.assertEqual(self.target.read_bytes(), before)

    def test_effective_wrong_shape_in_lower_file_refuses_without_loss(self):
        lower = self.directory / 'config.json'
        lower.write_text('{"provider":[]}')
        self.target.write_text('{"theme":"owner"}')
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.directory, {'provider': {'llama-server': {'name': 'ODS'}}})
        self.assertEqual({p.name: p.read_bytes() for p in self.directory.iterdir()}, before)

    def test_nonfinite_patch_is_refused_before_writing(self):
        for value in [math.nan, math.inf, -math.inf]:
            self.target.write_text('{"owner":true}')
            before = self.target.read_bytes()
            with self.assertRaises((oc.ConfigError, ValueError)):
                oc.update_config(self.directory, {'value': value})
            self.assertEqual(self.target.read_bytes(), before)

    def test_bool_and_number_are_not_the_same_json_value(self):
        self.target.write_text('{"a":true,"b":1}')
        oc.update_config(self.directory, {'a': 1, 'b': True})
        data = json.loads(self.target.read_text())
        self.assertIs(type(data['a']), int)
        self.assertIs(type(data['b']), bool)

    def test_changed_effective_config_refuses_stale_policy(self):
        self.target.write_text('{"model":"owner/changed"}')
        before = self.target.read_bytes()
        with self.assertRaises(oc.ConfigError):
            oc.update_config(self.directory, {'model':'llama-server/new'}, expected_effective={'model':'llama-server/old'})
        self.assertEqual(self.target.read_bytes(), before)

    def test_missing_config_below_symlink_does_not_create_directory(self):
        real = self.directory / 'real'
        real.mkdir()
        link = self.directory / 'linked'
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(oc.ConfigError):
            oc.update_config(link / 'new-config', {'model':'llama-server/new'})
        self.assertFalse((real / 'new-config').exists())


if __name__ == '__main__':
    unittest.main()
