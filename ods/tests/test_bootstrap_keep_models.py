"""Exercise the real bootstrap against an isolated local candidate repository."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / 'get-ods.sh'
UNINSTALL = (ROOT / 'ods-uninstall.sh').read_text()

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
printf '%s\\n' "$@" > "$UNINSTALL_ARGS"
keep=false
while [[ $# -gt 0 ]]; do
 case "$1" in --install-dir) INSTALL_DIR=$2; shift 2;; --keep-models) keep=true; shift;; --force|--non-interactive) shift;; *) exit 66;; esac
done
[[ "$INSTALL_DIR" == "$HOME/ods" && -f "$INSTALL_DIR/old-runtime" ]] || exit 67
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

    def test_failed_move_stops_production_deletion_and_retains_model_bytes(self):
        guard = re.search(r'if \$KEEP_MODELS && ! preserve_model_cache; then\n.*?\nfi', UNINSTALL, re.S).group()
        shell = 'set -eu\nKEEP_MODELS=true\nlog_info() { :; }\nlog_error() { :; }\nmv() { return 23; }\n'
        shell += function(UNINSTALL, 'preserve_model_cache') + '\n' + guard + '\nrm -rf "$INSTALL_DIR"\n'
        result = subprocess.run(['bash', '-c', shell], env=dict(self.env, INSTALL_DIR=str(self.install)), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.install / 'data/models/llm/model.gguf').read_bytes(), b'GGUF\x00fixture-exact-bytes\n')
        self.assertTrue((self.install / 'old-runtime').exists())
        self.assertTrue((self.home / '.ods-models-backup').is_dir())

    def test_partial_move_failure_retains_every_file_and_original_install(self):
        guard = re.search(r'if \$KEEP_MODELS && ! preserve_model_cache; then\n.*?\nfi', UNINSTALL, re.S).group()
        shell = 'set -eu\nKEEP_MODELS=true\nlog_info() { :; }\nlog_error() { :; }\nmv() { if [[ "$1" == */.cache-state ]]; then return 23; fi; command mv "$@"; }\n'
        shell += function(UNINSTALL, 'preserve_model_cache') + '\n' + guard + '\nrm -rf "$INSTALL_DIR"\n'
        result = subprocess.run(['bash', '-c', shell], env=dict(self.env, INSTALL_DIR=str(self.install)), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.home / '.ods-models-backup/llm/model.gguf').read_bytes(), b'GGUF\x00fixture-exact-bytes\n')
        self.assertEqual((self.install / 'data/models/.cache-state').read_bytes(), b'hidden-cache\n')
        self.assertTrue((self.install / 'old-runtime').exists())

    def test_restore_conflict_leaves_backup_available(self):
        source = BOOTSTRAP.read_text()
        restore = function(source, 'restore_bootstrap_models')
        backup = self.home / '.ods-models-backup'
        backup.mkdir()
        (backup / 'model.gguf').write_bytes(b'retained')
        shell = 'set -eu\nBOOTSTRAP_KEEP_MODELS=true\nsuccess() { :; }\n' + restore + '\nrestore_bootstrap_models\n'
        result = subprocess.run(['bash', '-c', shell], env=dict(self.env, INSTALL_DIR=str(self.install)), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((backup / 'model.gguf').read_bytes(), b'retained')
        self.assertTrue((self.install / 'data/models/llm/model.gguf').exists())

    def test_failed_restore_move_retains_backup(self):
        restore = function(BOOTSTRAP.read_text(), 'restore_bootstrap_models')
        backup = self.home / '.ods-models-backup'
        (self.install / 'data/models').rename(backup)
        shell = 'set -eu\nBOOTSTRAP_KEEP_MODELS=true\nsuccess() { :; }\nmv() { return 23; }\n' + restore + '\nrestore_bootstrap_models\n'
        result = subprocess.run(['bash', '-c', shell], env=dict(self.env, INSTALL_DIR=str(self.install)), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((backup / 'llm/model.gguf').read_bytes(), b'GGUF\x00fixture-exact-bytes\n')
        self.assertFalse((self.install / 'data/models').exists())

if __name__ == '__main__':
    unittest.main()
