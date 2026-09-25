#!/usr/bin/env python3
"""Run backup/restore with large POSIX disk observations and tiny real files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIB_KIB = 1024 * 1024
BACKUP_ID = '20260101-000000'


class SpaceArithmeticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ods backup space ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ods = self.root / 'ods'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.ods / 'lib').mkdir(parents=True)
        for name in ('rsync.sh', 'backup-paths.sh'):
            shutil.copyfile(ROOT / 'lib' / name, self.ods / 'lib' / name)
        (self.ods / '.version').write_text('test\n')
        self.env = dict(os.environ, ODS_DIR=str(self.ods), TMPDIR=str(self.root),
                        PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                        REAL_DU=shutil.which('du'), REAL_TAR=shutil.which('tar'),
                        LARGE_PATH='', FREE_KIB=str(4 * GIB_KIB))
        if os.environ.get('ODS_TEST_AWK'):
            self.env['SPACE_TEST_AWK'] = shutil.which(os.environ['ODS_TEST_AWK'])
            self.assertIsNotNone(self.env['SPACE_TEST_AWK'])
            self.stub('awk', 'exec "$SPACE_TEST_AWK" "$@"\n')
        self.stub('du', '''
if [[ "$1" == -sk && "$2" == "$LARGE_PATH" ]]; then
    printf '3145728\\t%s\\n' "$2"
else
    exec "$REAL_DU" "$@"
fi
''')
        self.stub('df', '''
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'
printf '/dev/fixture 8388608 0 %s 0%% /\\n' "$FREE_KIB"
''')
        self.stub('tar', '''
if [[ "$1" == -tvzf ]]; then
    printf '%s\\n' '-rw-r--r-- coder/coder 3221225472 2026-01-01 00:00 snapshot/data'
else
    exec "$REAL_TAR" "$@"
fi
''')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/bash\nset -euo pipefail\n' + body)
        path.chmod(0o755)

    def command(self, script, *args):
        return subprocess.run(['bash', str(ROOT / script), *args], env=self.env,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=30)

    def test_each_large_backup_source_checks_space_before_copying(self):
        paths = [('user-data', 'data/open-webui'), ('config', 'config'),
                 ('full', 'models'), ('full', 'data/models'),
                 ('full', 'data/whisper'), ('full', 'data/embeddings')]
        for index, (kind, relative) in enumerate(paths):
            source = self.ods / relative
            source.mkdir(parents=True, exist_ok=True)
            (source / 'receipt.txt').write_text('preserve this fixture\n')
            self.env['LARGE_PATH'] = str(source)
            for free in (4 * GIB_KIB, 5 * GIB_KIB // 2, 0):
                with self.subTest(source=relative, free_kib=free):
                    output = self.root / f'backup-{index}-{free}'
                    self.env['FREE_KIB'] = str(free)
                    result = self.command('ods-backup.sh', '--type', kind, '--output', str(output))
                    if free == 4 * GIB_KIB:
                        self.assertEqual(result.returncode, 0, result.stdout)
                        self.assertNotIn('syntax error', result.stdout)
                        self.assertEqual(len(list(output.glob('*/checksums.sha256'))), 1, result.stdout)
                    else:
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn('Not enough disk space', result.stdout)
                        self.assertEqual(list(output.iterdir()), [], result.stdout)
                    self.assertEqual((source / 'receipt.txt').read_text(), 'preserve this fixture\n')

    def test_large_directory_and_archive_restore_checks_before_writing(self):
        snapshot = self.ods / '.backups' / BACKUP_ID
        data = snapshot / 'data/open-webui'
        data.mkdir(parents=True)
        (data / 'receipt.txt').write_text('restored fixture\n')
        (snapshot / 'manifest.json').write_text(json.dumps({
            'manifest_version': '1.0', 'backup_type': 'user-data',
            'backup_date': '2026-01-01', 'description': 'space fixture',
        }, indent=2))
        archive = snapshot.with_suffix('.tar.gz')
        subprocess.run([self.env['REAL_TAR'], 'czf', str(archive), '-C', str(snapshot.parent), BACKUP_ID], check=True)
        target = self.ods / 'data/open-webui/receipt.txt'
        target.parent.mkdir(parents=True)
        self.env['LARGE_PATH'] = str(snapshot)
        for compressed in (False, True):
            if compressed:
                shutil.rmtree(snapshot)
            for free in (4 * GIB_KIB, 5 * GIB_KIB // 2, 0):
                with self.subTest(compressed=compressed, free_kib=free):
                    if compressed and snapshot.exists():
                        shutil.rmtree(snapshot)
                    target.write_text('previous live fixture\n')
                    self.env['FREE_KIB'] = str(free)
                    result = self.command('ods-restore.sh', '--force', '--skip-verify', '--data-only', BACKUP_ID)
                    if free == 4 * GIB_KIB:
                        self.assertEqual(result.returncode, 0, result.stdout)
                        self.assertNotIn('syntax error', result.stdout)
                        self.assertEqual(target.read_text(), 'restored fixture\n')
                    else:
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn('Not enough disk space', result.stdout)
                        self.assertEqual(target.read_text(), 'previous live fixture\n')


if __name__ == '__main__':
    unittest.main()
