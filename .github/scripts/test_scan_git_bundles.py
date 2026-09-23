"""Exercise the archive boundary using a real disposable Git bundle and Gitleaks."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('scan-git-bundles.py')
spec = importlib.util.spec_from_file_location('scan_bundles', SCRIPT)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def git(directory, *args):
    return subprocess.run(['git', '-C', str(directory), *args], check=True,
                          capture_output=True)


class BundleScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ods-bundle-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'root'
        self.root.mkdir()
        git(self.root, 'init', '--quiet')
        shutil.copyfile(SCRIPT.parents[2] / '.gitleaks.toml', self.root / '.gitleaks.toml')
        (self.root / '.gitleaksignore').write_text('', encoding='utf-8')
        self.report = Path(self.temp.name) / 'report.json'

    def scan(self):
        with patch.object(scanner, 'ROOT', self.root), patch.dict(os.environ, {
            'GITLEAKS_BUNDLE_REPORT': str(self.report),
        }), contextlib.redirect_stdout(io.StringIO()):
            return scanner.main()

    def test_no_bundle_is_an_empty_success(self):
        self.assertEqual(self.scan(), 0)
        self.assertEqual(json.loads(self.report.read_text()), [])

    def test_bundle_secret_fails_and_is_redacted(self):
        nested = Path(self.temp.name) / 'nested'
        nested.mkdir()
        git(nested, 'init', '--quiet')
        token = 'gh' + 'p_' + 'aB3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0iL3oR6'
        (nested / 'sample.txt').write_text('github_token="' + token + '"\n', encoding='utf-8')
        git(nested, 'add', 'sample.txt')
        git(nested, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'fixture')
        git(nested, 'bundle', 'create', str(self.root / 'nested.bundle'), '--all')
        git(self.root, 'add', 'nested.bundle')
        self.assertEqual(self.scan(), 1)
        raw = self.report.read_text(encoding='utf-8')
        self.assertNotIn(token, raw)
        findings = json.loads(raw)
        self.assertTrue(any(row['RuleID'] == 'github-pat' for row in findings))
        self.assertTrue(all(row['Archive'] == 'nested.bundle' for row in findings))

    def test_invalid_bundle_fails_closed(self):
        (self.root / 'broken.bundle').write_text('not a Git bundle', encoding='utf-8')
        git(self.root, 'add', 'broken.bundle')
        with self.assertRaisesRegex(SystemExit, 'Unable to extract'):
            self.scan()


if __name__ == '__main__':
    unittest.main()
