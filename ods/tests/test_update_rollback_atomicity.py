"""Exercise the real rollback CLI with isolated data and injected I/O failures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TARGET = Path(os.environ.get('ODS_UPDATE_UNDER_TEST', ROOT / 'ods-update.sh'))

WRAPPER = r'''
import os, pathlib, signal, subprocess, sys
tool = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
src, dst = args[-2:]
mode = os.environ.get('FAILURE_MODE', '')
config = dst.endswith('/config/litellm')
if tool == 'cp' and mode == 'partial-copy' and src.endswith('/.env'):
    partial = pathlib.Path(dst)
    if partial.is_dir():
        partial = partial / pathlib.Path(src).name
    partial.write_text('TRUNCATED')
    sys.exit(1)
if tool == 'mv':
    if mode == 'displace' and src.endswith('/config/litellm') and dst.endswith('/old'):
        sys.exit(1)
    if mode in ('publish', 'recovery') and src.endswith('/new') and config:
        sys.exit(1)
    if mode == 'recovery' and src.endswith('/old') and config:
        sys.exit(1)
result = subprocess.run(['/bin/' + tool, *args])
if result.returncode == 0 and tool == 'mv':
    if ((mode == 'signal-displace' and src.endswith('/config/litellm') and dst.endswith('/old'))
        or (mode == 'signal-publish' and src.endswith('/new') and config)):
        os.kill(os.getppid(), signal.SIGTERM)
sys.exit(result.returncode)
'''


class RollbackAtomicity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ods-rollback-atomic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'install with spaces'
        self.root.mkdir()
        shutil.copy2(TARGET, self.root / 'ods-update.sh')
        shutil.copytree(ROOT / 'lib', self.root / 'lib')
        self.config = self.root / 'config/litellm'
        self.config.mkdir(parents=True)
        (self.config / 'config.yaml').write_text('LIVE')
        (self.config / 'stale.yaml').write_text('LIVE-ONLY')
        (self.root / '.env').write_text('ODS_VERSION=2.0.0\n')
        (self.root / '.env').chmod(0o600)
        self.snap = self.root / 'data/backups/pre-update-20260101-000000'
        self.snap.mkdir(parents=True)
        (self.snap / '.env').write_text('ODS_VERSION=1.9.0\n')
        (self.snap / '.env').chmod(0o600)
        (self.snap / 'snapshot.json').write_text(json.dumps({'version': '1.9.0'}))
        (self.snap / 'config-litellm').mkdir()
        (self.snap / 'config-litellm/config.yaml').write_text('SNAPSHOT')
        (self.snap / 'config-litellm/link.yaml').symlink_to('config.yaml')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for tool in ('cp', 'mv'):
            path = self.bin / tool
            path.write_text('#!' + sys.executable + '\n' + WRAPPER)
            path.chmod(0o755)
        docker = self.bin / 'docker'
        docker.write_text('''#!/bin/sh
printf "%s\\n" "$*" >> "$DOCKER_LOG"
case "$*" in
  *'ps --services'*) echo fixture ;;
  *'ps --format json'*) echo '{"State":"running"}' ;;
esac
exit 0
''')
        docker.chmod(0o755)
        curl = self.bin / 'curl'
        curl.write_text('#!/bin/sh\nexit 0\n')
        curl.chmod(0o755)

    def run_restore(self, mode=''):
        env = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH'],
               'HOME': str(self.root / 'home'), 'FAILURE_MODE': mode,
               'DOCKER_LOG': str(self.root / 'docker.log')}
        result = subprocess.run(['bash', './ods-update.sh', 'rollback'], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=20)
        self.output = result.stdout + result.stderr
        return result

    def assert_originals(self):
        self.assertEqual((self.root / '.env').read_text(), 'ODS_VERSION=2.0.0\n')
        self.assertEqual((self.config / 'config.yaml').read_text(), 'LIVE')
        self.assertEqual((self.config / 'stale.yaml').read_text(), 'LIVE-ONLY')
        self.assertEqual(list(self.root.rglob('.ods-restore.*')), [])

    def assert_failure(self, result):
        self.assertNotEqual(result.returncode, 0, self.output)
        self.assertNotIn('Snapshot restored.', self.output)
        self.assertNotIn('up -d', (self.root / 'docker.log').read_text())

    def test_partial_flat_copy_preserves_all_live_data(self):
        self.assert_failure(self.run_restore('partial-copy'))
        self.assert_originals()

    def test_displacement_failure_undoes_already_restored_files(self):
        self.assert_failure(self.run_restore('displace'))
        self.assert_originals()

    def test_publish_failure_restores_originals(self):
        self.assert_failure(self.run_restore('publish'))
        self.assert_originals()

    def test_failed_recovery_retains_original_and_reports_path(self):
        self.assert_failure(self.run_restore('recovery'))
        originals = list(self.root.glob('config/.ods-restore.*/old/config.yaml'))
        self.assertEqual(len(originals), 1)
        self.assertEqual(originals[0].read_text(), 'LIVE')
        self.assertIn(str(originals[0].parent), self.output)
        self.assertIn('Manual recovery required', self.output)
        self.assertEqual((self.root / '.env').read_text(), 'ODS_VERSION=2.0.0\n')

    def test_signal_immediately_after_displacement_restores_originals(self):
        self.assert_failure(self.run_restore('signal-displace'))
        self.assert_originals()

    def test_signal_immediately_after_publication_restores_originals(self):
        self.assert_failure(self.run_restore('signal-publish'))
        self.assert_originals()

    def test_failure_removes_new_flat_file_without_prior_original(self):
        (self.snap / 'extra.yaml').write_text('NEW')
        self.assert_failure(self.run_restore('publish'))
        self.assert_originals()
        self.assertFalse((self.root / 'extra.yaml').exists())

    def test_success_preserves_private_mode_links_and_replaces_tree(self):
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual((self.root / '.env').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.config / 'config.yaml').read_text(), 'SNAPSHOT')
        self.assertEqual(os.readlink(self.config / 'link.yaml'), 'config.yaml')
        self.assertFalse((self.config / 'stale.yaml').exists())
        self.assertEqual(list(self.root.rglob('.ods-restore.*')), [])
        self.assertIn('Snapshot restored.', self.output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
