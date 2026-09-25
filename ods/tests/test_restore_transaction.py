"""Exercise the real restore CLI with isolated data and failing filesystem tools."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ODS = Path(__file__).resolve().parents[1]

WRAPPER = r'''#!/usr/bin/env python3
import os, pathlib, signal, subprocess, sys
tool=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
mode=os.environ.get('RESTORE_FAULT','')
source=args[-2] if len(args)>1 else ''
dest=args[-1] if args else ''
root=os.environ['ODS_DIR']
if tool=='docker':
    if mode=='docker_inspect_failure': sys.exit(23)
    if 'ls' in args: print(pathlib.Path(root).name); sys.exit(0)
    sys.exit(23)
if tool=='cp' and mode=='partial_copy' and source.endswith('/config'):
    pathlib.Path(dest).mkdir(exist_ok=True)
    (pathlib.Path(dest)/'settings').write_text('truncated')
    sys.exit(23)
if tool=='rsync' and '--help' not in args and '--dry-run' not in args:
    if mode=='cache_transfer_failure' and source.endswith('/data/models/'):
        (pathlib.Path(dest)/'weights.gguf').write_text('partial cache')
        sys.exit(23)
    if mode=='rsync_failure': sys.exit(23)
    if mode=='rsync_noop': sys.exit(0)
if tool=='mv':
    if source.endswith('/new') and dest==root+'/data/embeddings' and mode=='cache_publish_failure':
        sys.exit(23)
    if source.endswith('/new') and dest==root+'/config' and mode in ('publish_failure','recovery_failure'):
        sys.exit(23)
    if source.endswith('/old') and dest==root+'/.env' and mode=='recovery_failure': sys.exit(23)
result=subprocess.run([os.environ['REAL_'+tool.upper()],*args])
if tool=='mv' and result.returncode==0:
    if mode=='signal_displaced' and source==root+'/.env' and dest.endswith('/old'):
        os.kill(os.getppid(),signal.SIGTERM)
    if mode=='signal_published' and source.endswith('/new') and dest==root+'/.env':
        os.kill(os.getppid(),signal.SIGTERM)
sys.exit(result.returncode)
'''


class RestoreTransaction(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'installation'
        self.backup = self.root / '.backups/snapshot'
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        (self.root / 'data').mkdir(parents=True)
        (self.root / 'lib').mkdir()
        for name in ('rsync.sh', 'backup-paths.sh'):
            shutil.copy2(ODS / 'lib' / name, self.root / 'lib' / name)
        self.write(self.backup, 'manifest.json', json.dumps({
            'manifest_version': '1.0', 'backup_type': 'userdata',
            'backup_date': '2026-01-01', 'description': 'test'}))
        self.env = {**os.environ, 'ODS_DIR': str(self.root),
                    'HOME': self.temp.name, 'PATH': str(self.bin)+os.pathsep+os.environ['PATH']}
        for name in ('cp', 'mv', 'rsync', 'docker'):
            self.env['REAL_'+name.upper()] = shutil.which(name) or '/bin/false'
            path = self.bin / name
            path.write_text(WRAPPER)
            path.chmod(0o755)

    def write(self, root, name, value):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)

    def fixture(self):
        for name in ('data/persona/SOUL.md', 'data/hermes/session', '.env', 'config/settings'):
            self.write(self.root, name, 'current-'+name)
            self.write(self.backup, name, 'backup-'+name)
        self.write(self.root, 'data/persona/newer.txt', 'keep me')
        self.before = self.contents()

    def contents(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()
                and not any(x.startswith('.ods-restore.') or x == '.backups'
                            for x in p.relative_to(self.root).parts)}

    def restore(self, fault='', *options):
        result = subprocess.run(['bash', str(ODS/'ods-restore.sh'), '-f', *options, 'snapshot'],
                                env={**self.env, 'RESTORE_FAULT': fault},
                                text=True, capture_output=True, timeout=30)
        self.output = result.stdout + result.stderr
        return result.returncode

    def test_additive_pixel_only_restore_preserves_permissions_and_links(self):
        self.fixture()
        soul = self.backup/'data/persona/SOUL.md'
        soul.chmod(0o640)
        (self.backup/'data/persona/soul-link').symlink_to('SOUL.md')
        self.assertEqual(self.restore(), 0, self.output)
        self.assertEqual((self.root/'data/persona/SOUL.md').read_text(), 'backup-data/persona/SOUL.md')
        self.assertEqual((self.root/'data/persona/newer.txt').read_text(), 'keep me')
        self.assertEqual((self.root/'config/settings').read_text(), 'backup-config/settings')
        self.assertEqual((self.root/'data/persona/SOUL.md').stat().st_mode & 0o777, 0o640)
        self.assertTrue((self.root/'data/persona/soul-link').is_symlink())
        self.assertFalse((self.root/'data/open-webui').exists())
        self.assertEqual(list(self.root.rglob('.ods-restore.*')), [])

    def test_config_only_requires_no_optional_service(self):
        self.write(self.backup, '.env', 'restored')
        self.assertEqual(self.restore('', '--config-only'), 0, self.output)
        self.assertEqual((self.root/'.env').read_text(), 'restored')

    def cache_fixture(self):
        self.fixture()
        for name in ('models/legacy.gguf', 'data/models/weights.gguf',
                     'data/whisper/model.bin', 'data/embeddings/model.bin'):
            self.write(self.root, name, 'current-'+name)
            self.write(self.backup, name, 'backup-'+name)
        self.write(self.root, 'data/models/newer.gguf', 'keep model')
        self.before = self.contents()

    def test_cache_transfer_and_publication_failures_rollback_all_selected_paths(self):
        for fault in ('cache_transfer_failure', 'cache_publish_failure', 'publish_failure'):
            with self.subTest(fault=fault):
                self.cache_fixture()
                self.assertNotEqual(self.restore(fault), 0, self.output)
                self.assertEqual(self.contents(), self.before, self.output)
                self.assertEqual(list(self.root.rglob('.ods-restore.*')), [])

    def test_cache_restore_is_additive_and_config_only_skips_cache(self):
        self.cache_fixture()
        self.assertEqual(self.restore('', '--config-only'), 0, self.output)
        self.assertEqual((self.root/'data/models/weights.gguf').read_text(), 'current-data/models/weights.gguf')
        self.assertEqual(self.restore('', '--data-only'), 0, self.output)
        for name in ('models/legacy.gguf', 'data/models/weights.gguf',
                     'data/whisper/model.bin', 'data/embeddings/model.bin'):
            self.assertEqual((self.root/name).read_text(), 'backup-'+name)
        self.assertEqual((self.root/'data/models/newer.gguf').read_text(), 'keep model')

    def test_new_data_destination(self):
        self.write(self.backup, 'data/persona/SOUL.md', 'restored')
        self.assertEqual(self.restore('', '--data-only'), 0, self.output)
        self.assertEqual((self.root/'data/persona/SOUL.md').read_text(), 'restored')

    def test_failures_leave_all_originals_intact(self):
        for fault in ('partial_copy', 'rsync_failure', 'rsync_noop', 'publish_failure',
                      'signal_displaced', 'signal_published'):
            with self.subTest(fault=fault):
                self.fixture()
                self.assertNotEqual(self.restore(fault), 0, self.output)
                self.assertEqual(self.contents(), self.before, self.output)
                self.assertNotIn('Restore complete!', self.output)
                self.assertEqual(list(self.root.rglob('.ods-restore.*')), [])

    def test_rollback_removes_newly_created_destination(self):
        self.fixture()
        shutil.rmtree(self.root/'data/persona')
        self.before=self.contents()
        self.assertNotEqual(self.restore('publish_failure'), 0, self.output)
        self.assertEqual(self.contents(), self.before, self.output)
        self.assertFalse((self.root/'data/persona').exists())

    def test_failed_recovery_retains_original_and_reports_location(self):
        self.fixture()
        self.assertNotEqual(self.restore('recovery_failure'), 0, self.output)
        old = list(self.root.glob('.ods-restore.*/old'))
        self.assertEqual(len(old), 1, self.output)
        self.assertEqual(old[0].read_text(), 'current-.env')
        self.assertIn(str(old[0]), self.output)
        self.assertIn('Manual recovery required', self.output)

    def test_top_level_symlink_is_not_replaced(self):
        self.fixture()
        external = Path(self.temp.name)/'external'
        (self.root/'data/persona').rename(external)
        (self.root/'data/persona').symlink_to(external, target_is_directory=True)
        self.assertNotEqual(self.restore(), 0, self.output)
        self.assertEqual((external/'SOUL.md').read_text(), 'current-data/persona/SOUL.md')
        self.assertTrue((self.root/'data/persona').is_symlink())

    def test_requested_stop_failure_leaves_data_untouched(self):
        for fault in ('docker_stop_failure', 'docker_inspect_failure'):
            with self.subTest(fault=fault):
                self.fixture()
                self.assertNotEqual(self.restore(fault, '--stop-containers'), 0, self.output)
                self.assertEqual(self.contents(), self.before, self.output)
                self.assertNotIn('Restore complete!', self.output)


if __name__ == '__main__':
    unittest.main()
