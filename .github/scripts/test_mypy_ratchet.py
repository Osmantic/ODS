"""Self-test for the mypy ratchet: it must fail on new errors and tolerate moved ones."""

import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("mypy_ratchet", Path(__file__).with_name("mypy-ratchet.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

OUTPUT = (
    'app.py:10: error: Incompatible types in assignment (expression has type "int", '
    'target has type "str")  [assignment]\n'
    'app.py:20: note: See https://mypy.readthedocs.io\n'
    'lib/util.py:5: error: Name "x" already defined on line 2  [no-redef]\n'
    'Found 2 errors in 2 files (checked 3 source files)\n'
)


class MypyRatchet(unittest.TestCase):
    def run_ratchet(self, baseline_path, text, *extra):
        argv = ['mypy-ratchet.py', '--target', 'svc', '--baseline', str(baseline_path), *extra]
        stdout = io.StringIO()
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(sys, 'stdin', io.StringIO(text)), \
                mock.patch.object(sys, 'stdout', stdout):
            status = MODULE.main()
        return status, stdout.getvalue()

    def test_update_then_unchanged_output_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            self.assertEqual(self.run_ratchet(baseline, OUTPUT, '--update')[0], 0)
            recorded = json.loads(baseline.read_text(encoding='utf-8'))['svc']
            self.assertIn('lib/util.py [no-redef] Name "x" already defined on line N', recorded)
            self.assertEqual(self.run_ratchet(baseline, OUTPUT)[0], 0)

    def test_moved_errors_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            self.run_ratchet(baseline, OUTPUT, '--update')
            moved = OUTPUT.replace('app.py:10:', 'app.py:14:').replace('on line 2', 'on line 3')
            self.assertEqual(self.run_ratchet(baseline, moved)[0], 0)

    def test_new_error_fails_with_annotation(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            self.run_ratchet(baseline, OUTPUT, '--update')
            added = OUTPUT.replace(
                'Found 2 errors in 2 files',
                'app.py:30: error: Missing return statement  [return]\nFound 3 errors in 2 files')
            status, output = self.run_ratchet(baseline, added, '--prefix', 'ods/svc')
            self.assertEqual(status, 1)
            self.assertIn('::error file=ods/svc/app.py,line=30::Missing return statement', output)

    def test_repeated_error_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            self.run_ratchet(baseline, OUTPUT, '--update')
            repeated = OUTPUT.replace(
                'Found 2 errors in 2 files',
                'app.py:40: error: Incompatible types in assignment (expression has type "int", '
                'target has type "str")  [assignment]\nFound 3 errors in 2 files')
            self.assertEqual(self.run_ratchet(baseline, repeated)[0], 1)

    def test_fixed_errors_pass_and_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            self.run_ratchet(baseline, OUTPUT, '--update')
            status, output = self.run_ratchet(baseline, 'Success: no issues found in 3 source files\n')
            self.assertEqual(status, 0)
            self.assertIn('2 fixed', output)

    def test_parser_must_account_for_every_reported_error(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            self.run_ratchet(baseline, OUTPUT, '--update')
            unparsed = OUTPUT.replace('Found 2 errors', 'Found 5 errors')
            with self.assertRaises(SystemExit):
                self.run_ratchet(baseline, unparsed)

    def test_missing_target_baseline_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            baseline.write_text('{}\n', encoding='utf-8')
            with self.assertRaises(SystemExit):
                self.run_ratchet(baseline, OUTPUT)


if __name__ == '__main__':
    unittest.main()
