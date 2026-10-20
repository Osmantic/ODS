#!/usr/bin/env python3
"""Exercise archive/restore failure recovery using only private model fixtures."""
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(os.environ.get('ODS_COLD_STORAGE_SCRIPT', Path(__file__).resolve().parents[1] / 'scripts/llm-cold-storage.sh'))
NAME = 'models--ODSFixture--idle-model'
PAYLOAD = b'fixture weights\x00\xff\n'


class ColdStorageRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ods-cold-recovery-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / 'cache'
        self.cold = self.root / 'cold pool'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.cache.mkdir()
        self.hot = self.cache / NAME
        self.archived = self.cold / NAME
        self.env = dict(os.environ, HF_CACHE=str(self.cache), COLD_DIR=str(self.cold),
                        LOG_FILE=str(self.root / 'logs/cold.log'), PATH=f'{self.bin}:{os.environ["PATH"]}')
        self.stub('pgrep', 'exit 1')

    def stub(self, command, body):
        path = self.bin / command
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(0o755)

    def seed(self, path):
        path.mkdir(parents=True)
        (path / 'weights.bin').write_bytes(PAYLOAD)
        os.utime(path / 'weights.bin', (1_000_000_000, 1_000_000_000))

    def invoke(self, *args):
        return subprocess.run(['bash', str(SCRIPT), *args], env=self.env, cwd=self.root,
                              text=True, capture_output=True, timeout=15)

    def intact(self, path):
        self.assertEqual((path / 'weights.bin').read_bytes(), PAYLOAD)
        self.assertFalse((path / NAME).exists(), 'model was incorrectly nested')

    def failed(self, result, claim):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(claim + ':', result.stdout)

    def cold_model(self, linked=True):
        self.seed(self.archived)
        if linked:
            self.hot.symlink_to(self.archived, target_is_directory=True)

    def test_missing_directory_round_trip(self):
        self.seed(self.hot)
        self.assertEqual(self.invoke('--execute').returncode, 0)
        self.assertEqual(os.readlink(self.hot), str(self.archived))
        self.intact(self.hot)
        self.assertEqual(self.invoke('--restore', NAME).returncode, 0)
        self.assertFalse(self.hot.is_symlink())
        self.assertFalse(self.archived.exists())
        self.intact(self.hot)

    def test_archive_link_failure_rolls_back_without_false_success(self):
        self.seed(self.hot)
        self.stub('ln', 'exit 1')
        self.failed(self.invoke('--execute'), 'ARCHIVED')
        self.intact(self.hot)
        self.assertFalse(self.archived.exists())

    def test_archive_rollback_failure_preserves_cold_payload(self):
        self.seed(self.hot)
        self.stub('ln', 'exit 1')
        real = shlex.quote(shutil.which('mv'))
        self.stub('mv', f'case "$1" in "$COLD_DIR"/*) exit 1;; esac\nexec {real} "$@"')
        self.failed(self.invoke('--execute'), 'ARCHIVED')
        self.intact(self.archived)
        self.assertFalse(self.hot.exists())

    def test_reappearing_cache_path_is_preserved_on_link_failure(self):
        self.seed(self.hot)
        self.stub('ln', 'mkdir "$3"\nprintf other > "$3/other"\nexit 1')
        self.failed(self.invoke('--execute'), 'ARCHIVED')
        self.assertEqual((self.hot / 'other').read_text(), 'other')
        self.intact(self.archived)
        self.assertFalse((self.hot / NAME).exists())

    def test_successful_but_wrong_link_is_not_accepted(self):
        self.seed(self.hot)
        real = shlex.quote(shutil.which('ln'))
        self.stub('ln', f'exec {real} -s /not-the-model "$3"')
        self.failed(self.invoke('--execute'), 'ARCHIVED')
        self.assertEqual(os.readlink(self.hot), '/not-the-model')
        self.intact(self.archived)

    def test_existing_cold_destination_is_not_nested_or_overwritten(self):
        self.seed(self.hot)
        self.seed(self.archived)
        (self.archived / 'other').write_text('keep')
        self.failed(self.invoke('--execute'), 'ARCHIVED')
        self.intact(self.hot)
        self.intact(self.archived)
        self.assertEqual((self.archived / 'other').read_text(), 'keep')

    def test_mkdir_and_move_errors_propagate(self):
        for command in ('mkdir', 'mv'):
            with self.subTest(command=command):
                if not self.hot.exists():
                    self.seed(self.hot)
                os.utime(self.hot / 'weights.bin', (1_000_000_000, 1_000_000_000))
                self.stub(command, 'exit 1')
                self.failed(self.invoke('--execute'), 'ARCHIVED')
                self.intact(self.hot)
                (self.bin / command).unlink()

    def test_restore_refuses_real_hot_path(self):
        self.cold_model(linked=False)
        self.seed(self.hot)
        self.failed(self.invoke('--restore', NAME), 'RESTORED')
        self.intact(self.hot)
        self.intact(self.archived)

    def test_restore_refuses_foreign_link(self):
        self.cold_model(linked=False)
        self.hot.symlink_to('/foreign-dangling-link')
        self.failed(self.invoke('--restore', NAME), 'RESTORED')
        self.assertEqual(os.readlink(self.hot), '/foreign-dangling-link')
        self.intact(self.archived)

    def test_restore_unlink_failure_preserves_link(self):
        self.cold_model()
        self.stub('rm', 'exit 1')
        self.failed(self.invoke('--restore', NAME), 'RESTORED')
        self.assertEqual(os.readlink(self.hot), str(self.archived))
        self.intact(self.archived)

    def test_restore_move_failure_reinstates_owned_link(self):
        self.cold_model()
        self.stub('mv', 'exit 1')
        self.failed(self.invoke('--restore', NAME), 'RESTORED')
        self.assertEqual(os.readlink(self.hot), str(self.archived))
        self.intact(self.archived)

    def test_restore_move_failure_does_not_invent_prior_link(self):
        self.cold_model(linked=False)
        self.stub('mv', 'exit 1')
        self.failed(self.invoke('--restore', NAME), 'RESTORED')
        self.assertFalse(self.hot.is_symlink())
        self.intact(self.archived)

    def test_partial_restore_failure_preserves_both_paths(self):
        self.cold_model()
        self.stub('mv', 'mkdir "$2"\nprintf partial > "$2/partial"\nexit 1')
        self.failed(self.invoke('--restore', NAME), 'RESTORED')
        self.intact(self.archived)
        self.assertFalse(self.hot.is_symlink())
        self.assertEqual((self.hot / 'partial').read_bytes(), b'partial')

    def test_restore_all_reports_partial_failure(self):
        self.cold_model()
        other = 'models--ODSFixture--blocked'
        self.seed(self.cold / other)
        self.seed(self.cache / other)
        result = self.invoke('--restore-all')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('All models restored', result.stdout)
        self.intact(self.hot)
        self.intact(self.cold / other)
        self.intact(self.cache / other)


if __name__ == '__main__':
    unittest.main()
