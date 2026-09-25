"""Exercise the real bootstrap against an isolated local candidate repository."""
import json
import errno
import importlib.util
import shutil
from unittest import mock
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / 'get-ods.sh'
UNINSTALL = (ROOT / 'ods-uninstall.sh').read_text()
HELPER = ROOT / 'lib/model-cache-custody.py'
spec = importlib.util.spec_from_file_location('model_cache_custody', HELPER)
custody = importlib.util.module_from_spec(spec)
spec.loader.exec_module(custody)

def function(source, name):
    return re.search(r'^' + name + r'\(\) \{\n.*?^}', source, re.M | re.S).group()

class KeepModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.install = self.home / 'ods'
        self.install.mkdir()
        for name in ['.env', 'ods-cli', 'ods-uninstall.sh', 'docker-compose.base.yml']:
            (self.install / name).write_text('old fixture\n')
        (self.install / 'old-runtime').write_text('must not survive')
        model = self.install / 'data/models/llm/model.gguf'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'GGUF\x00fixture-exact-bytes\n')
        (self.install / 'data/models/.cache-state').write_bytes(b'hidden-cache\n')
        self.repo = self.root / 'repo'
        ods = self.repo / 'ods'
        ods.mkdir(parents=True)
        (ods / 'lib').mkdir()
        shutil.copyfile(HELPER, ods / 'lib/model-cache-custody.py')
        installer = '''#!/bin/bash
set -euo pipefail
python3 - "$@" <<'PY'
import json,os,pathlib,sys
root=pathlib.Path.cwd();model=root/'data/models/llm/model.gguf'
pathlib.Path(os.environ['RESULT']).write_text(json.dumps({'args':sys.argv[1:],'model':model.read_bytes().hex() if model.exists() else None,'oldRuntime':(root/'old-runtime').exists(),'oldEnv':(root/'.env').exists(),'hidden':(root/'data/models/.cache-state').exists(),'candidate':(root/'candidate-only').read_text()}))
PY
'''
        (ods / 'install.sh').write_text(installer)
        (ods / 'candidate-only').write_text('fresh source')
        uninstaller = '''#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf '%s\\n' "$@" > "$UNINSTALL_ARGS"
keep=false
while [[ $# -gt 0 ]]; do
 case "$1" in --install-dir) INSTALL_DIR=$2; shift 2;; --keep-models) keep=true; shift;; --force|--non-interactive) shift;; *) exit 66;; esac
done
[[ "$INSTALL_DIR" == "$ODS_INSTALL_DIR" && -f "$INSTALL_DIR/old-runtime" ]] || exit 67
log_info() { :; }
''' + function(UNINSTALL, 'preserve_model_cache') + '''
if $keep; then preserve_model_cache || exit 68; fi
rm -rf "$INSTALL_DIR"
'''
        (ods / 'ods-uninstall.sh').write_text(uninstaller)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        self.sha = subprocess.check_output(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], text=True).strip()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ['docker', 'nvidia-smi', 'lspci']:
            f = self.bin / name
            f.write_text('#!/bin/bash\nexit 1\n')
            f.chmod(0o755)
        self.result = self.root / 'result.json'
        self.args = self.root / 'uninstall-args'
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin) + ':' + os.environ['PATH'],
            ODS_INSTALL_DIR=str(self.install), ODS_REPO_URL=str(self.repo), ODS_REF=self.sha,
            ODS_ALLOW_LEGACY_PARALLEL='1', RESULT=str(self.result), UNINSTALL_ARGS=str(self.args))

    def bootstrap(self, *args, mac=False):
        command = ['bash', str(BOOTSTRAP), *args]
        if mac:
            command = ['bash', '-c', 'OSTYPE=darwin; source "$1" "${@:2}"', 'fixture', str(BOOTSTRAP), *args]
        return subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=30)

    def assert_cache_restored(self, mac):
        result = self.bootstrap('--non-interactive', '--force', '--keep-models', '--pixel', '--summary-json', 'summary.json', mac=mac)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(self.result.read_text())
        self.assertEqual(value['args'], ['--non-interactive', '--force', '--pixel', '--summary-json', 'summary.json'])
        self.assertEqual(value['model'], b'GGUF\x00fixture-exact-bytes\n'.hex())
        self.assertTrue(value['hidden'])
        self.assertFalse(value['oldRuntime'])
        self.assertFalse(value['oldEnv'])
        self.assertEqual(value['candidate'], 'fresh source')
        self.assertEqual(self.args.read_text().splitlines().count('--keep-models'), 1)
        self.assertFalse((self.home / '.ods-models-backup').exists())
        self.assertFalse(Path(str(self.install) + '.models-backup').exists())

    def test_keep_models_is_consumed_and_restores_only_cache_on_linux(self):
        self.assert_cache_restored(False)

    def test_keep_models_is_consumed_and_restores_only_cache_on_macos(self):
        self.assert_cache_restored(True)

    def test_default_force_still_purges_models(self):
        result = self.bootstrap('--force', '--non-interactive')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNone(json.loads(self.result.read_text())['model'])
        self.assertNotIn('--keep-models', self.args.read_text())

    def test_keep_models_requires_force_before_candidate_actions(self):
        result = self.bootstrap('--keep-models', '--non-interactive')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.args.exists())
        self.assertTrue((self.install / 'old-runtime').exists())

    def test_new_or_incomplete_install_cannot_claim_preservation(self):
        (self.install / 'ods-cli').unlink()
        result = self.bootstrap('--force', '--keep-models')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.args.exists())
        self.assertTrue((self.install / 'data/models/llm/model.gguf').exists())

    def test_existing_or_symlink_backup_is_never_overwritten(self):
        for symlink in [False, True]:
            backup = self.home / '.ods-models-backup'
            if symlink:
                backup.rmdir()
                backup.symlink_to(self.root / 'missing-target', target_is_directory=True)
            else:
                backup.mkdir()
            result = self.bootstrap('--force', '--keep-models')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.args.exists())
            self.assertTrue((self.install / 'old-runtime').exists())
            self.assertEqual(backup.is_symlink(), symlink)

    def test_symlinked_model_directory_is_rejected(self):
        source = self.install / 'data/models'
        source.rename(self.root / 'external-models')
        source.symlink_to(self.root / 'external-models', target_is_directory=True)
        self.assertNotEqual(self.bootstrap('--force', '--keep-models').returncode, 0)
        self.assertTrue((self.root / 'external-models/llm/model.gguf').exists())
        self.assertFalse(self.args.exists())

    def test_help_is_read_only_and_documents_scope(self):
        result = self.bootstrap('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--force [--keep-models]', result.stdout)
        self.assertIn('ordinary installer validation', result.stdout)
        self.assertFalse(self.args.exists())
        self.assertFalse(self.result.exists())

    def helper_env(self):
        return mock.patch.dict(os.environ, {'HOME': str(self.home)})

    def test_failed_preservation_stops_production_deletion(self):
        guard = re.search(r'if \$KEEP_MODELS && ! preserve_model_cache; then\n.*?\nfi', UNINSTALL, re.S).group()
        shell = 'set -eu\nKEEP_MODELS=true\nlog_info() { :; }\nlog_error() { :; }\npython3() { return 23; }\n'
        shell += function(UNINSTALL, 'preserve_model_cache') + '\n' + guard + '\nrm -rf "$INSTALL_DIR"\n'
        result = subprocess.run(['bash', '-c', shell], env=dict(self.env, INSTALL_DIR=str(self.install), SCRIPT_DIR=str(ROOT)), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.install / 'old-runtime').exists())
        self.assertTrue((self.install / 'data/models/llm/model.gguf').exists())

    def test_cache_rename_preserves_inode_hidden_files_and_receipt(self):
        source = self.install / 'data/models'
        before = source.stat()
        with self.helper_env():
            custody.preserve(self.install)
            backup = Path(str(self.install) + '.models-backup')
            self.assertEqual((backup / 'models').stat().st_ino, before.st_ino)
            self.assertEqual((backup / 'models').stat().st_dev, before.st_dev)
            self.assertFalse(source.exists())
            custody.restore(self.install)
        self.assertEqual(source.stat().st_ino, before.st_ino)
        self.assertEqual((source / '.cache-state').read_bytes(), b'hidden-cache\n')
        self.assertFalse(backup.exists())

    def test_cross_device_rename_never_copies_or_deletes_source(self):
        source = self.install / 'data/models'
        with self.helper_env(), mock.patch.object(custody.os, 'rename', side_effect=OSError(errno.EXDEV, 'cross-device')):
            with self.assertRaises(OSError):
                custody.preserve(self.install)
        self.assertEqual((source / 'llm/model.gguf').read_bytes(), b'GGUF\x00fixture-exact-bytes\n')
        self.assertTrue((self.install / 'old-runtime').exists())
        self.assertFalse((Path(str(self.install) + '.models-backup') / 'models').exists())

    def test_separate_model_mount_rejected_before_backup_creation(self):
        source = self.install / 'data/models'
        original = custody.directory
        def separate_device(path):
            result = original(path)
            if path == source:
                fields = list(result)
                fields[2] += 1
                return os.stat_result(fields)
            return result
        with self.helper_env(), mock.patch.object(custody, 'directory', side_effect=separate_device):
            with self.assertRaisesRegex(ValueError, 'separate mount'):
                custody.preflight(self.install)
        self.assertFalse(Path(str(self.install) + '.models-backup').exists())

    def test_install_on_different_filesystem_from_home_retains_inode(self):
        if not Path('/dev/shm').is_dir() or Path('/dev/shm').stat().st_dev == self.home.stat().st_dev:
            self.skipTest('needs separate writable /dev/shm filesystem')
        with tempfile.TemporaryDirectory(dir='/dev/shm') as other:
            moved = Path(other) / 'ods'
            shutil.move(str(self.install), moved)
            self.install = moved
            self.env['ODS_INSTALL_DIR'] = str(moved)
            identity = (moved / 'data/models').stat().st_ino
            self.assert_cache_restored(False)
            self.assertEqual((moved / 'data/models').stat().st_ino, identity)

    def test_restore_conflict_or_changed_custody_keeps_backup(self):
        with self.helper_env():
            custody.preserve(self.install)
            backup = Path(str(self.install) + '.models-backup')
            (self.install / 'data/models').mkdir()
            with self.assertRaisesRegex(ValueError, 'destination already exists'):
                custody.restore(self.install)
            (self.install / 'data/models').rmdir()
            marker = backup / 'custody.json'
            receipt = json.loads(marker.read_text())
            receipt['installRoot'] = str(self.root / 'unrelated')
            marker.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'custody changed'):
                custody.restore(self.install)
            self.assertTrue((backup / 'models/llm/model.gguf').is_file())

    def test_failed_restore_rename_keeps_backup(self):
        with self.helper_env():
            custody.preserve(self.install)
            with mock.patch.object(custody.os, 'rename', side_effect=OSError(errno.EXDEV, 'cross-device')):
                with self.assertRaises(OSError):
                    custody.restore(self.install)
        self.assertTrue((Path(str(self.install) + '.models-backup') / 'models/llm/model.gguf').is_file())
        self.assertFalse((self.install / 'data/models').exists())

    def test_adjacent_existing_or_symlink_backup_is_never_overwritten(self):
        backup = Path(str(self.install) + '.models-backup')
        for symlink in [False, True]:
            if symlink:
                backup.rmdir()
                backup.symlink_to(self.root / 'missing', target_is_directory=True)
            else:
                backup.mkdir()
            result = self.bootstrap('--force', '--keep-models')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.args.exists())
            self.assertTrue((self.install / 'old-runtime').exists())

    def test_older_candidate_without_custody_helper_fails_before_uninstall(self):
        (self.repo / 'ods/lib/model-cache-custody.py').unlink()
        subprocess.run(['git', '-C', str(self.repo), 'add', '-u'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'older candidate'], check=True)
        self.env['ODS_REF'] = subprocess.check_output(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], text=True).strip()
        result = self.bootstrap('--force', '--keep-models')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('predates same-filesystem', result.stdout + result.stderr)
        self.assertFalse(self.args.exists())
        self.assertTrue((self.install / 'data/models/llm/model.gguf').exists())

    @unittest.skipIf(os.getuid() == 0, 'root bypasses ordinary directory permissions')
    def test_unwritable_source_or_backup_parent_fails_in_preflight(self):
        for parent in (self.install / 'data', self.install.parent):
            with self.subTest(parent=parent), self.helper_env():
                parent.chmod(0o555)
                try:
                    with self.assertRaisesRegex(ValueError, 'not writable/searchable'):
                        custody.preflight(self.install)
                    self.assertFalse(Path(str(self.install) + '.models-backup').exists())
                    self.assertTrue((self.install / 'data/models/llm/model.gguf').exists())
                finally:
                    parent.chmod(0o755)

    def test_boolean_schema_does_not_equal_integer_custody_version(self):
        with self.helper_env():
            custody.preserve(self.install)
            backup = Path(str(self.install) + '.models-backup')
            marker = backup / 'custody.json'
            receipt = json.loads(marker.read_text())
            receipt['schemaVersion'] = True
            marker.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'invalid model custody schema'):
                custody.restore(self.install)
            self.assertTrue((backup / 'models/llm/model.gguf').is_file())

if __name__ == '__main__':
    unittest.main()
