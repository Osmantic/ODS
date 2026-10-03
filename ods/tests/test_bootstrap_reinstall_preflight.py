"""get-ods.sh --force checks the host before the candidate removes anything.

Regression for a forced reinstall that ran the candidate uninstaller first and
the installer's disk preflight afterwards: `At least 30 GB free space required`
then left the host with no working ODS and no .env, and the identical retry was
refused because --keep-models could no longer recognize the tree.

These tests run the real get-ods.sh, installers/reinstall-preflight.sh,
install.sh dispatcher and the platform installers' --preflight-only mode from an
isolated candidate repository. PATH stubs supply the host facts (macOS version,
architecture, Docker, free disk, network). The candidate's uninstaller and its
final installer run are recorders, so no test touches a real installation.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / 'get-ods.sh'
UNINSTALL = (ROOT / 'ods-uninstall.sh').read_text()
MODEL = b'GGUF\x00retained-model-bytes\n'
GIT_IDENTITY = ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid']
# install-core.sh reads /etc/os-release and refuses root in its preflight.
LINUX_USER = sys.platform.startswith('linux') and os.geteuid() != 0


def function(source, name):
    return re.search(r'^' + name + r'\(\) \{\n.*?^}', source, re.M | re.S).group()


def real(tool):
    path = shutil.which(tool)
    if not path:
        raise unittest.SkipTest(tool + ' is required')
    return path


def write(path, text, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)


# The candidate's own install.sh routes --preflight-only through the real
# dispatcher (copied as install-real.sh) and records every other invocation.
CANDIDATE_INSTALL = '''#!/bin/bash
set -euo pipefail
if [[ "${1:-}" == --preflight-only ]]; then
    exec bash "$(dirname "${BASH_SOURCE[0]}")/install-real.sh" "$@"
fi
python3 - "$@" <<'PY'
import json, os, pathlib, sys
root = pathlib.Path.cwd()
model = root / 'data/models/llm/model.gguf'
pathlib.Path(os.environ['RESULT']).write_text(json.dumps({
    'args': sys.argv[1:],
    'model': model.read_bytes().hex() if model.exists() else None,
    'modelInode': (root / 'data/models').stat().st_ino if model.exists() else None,
    'oldRuntime': (root / 'old-runtime').exists(),
    'leftover': (root / 'stranded-leftover').exists(),
    'env': (root / '.env').exists(),
}))
PY
'''

CANDIDATE_UNINSTALL = '''#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf '%s\\n' "$@" > "$UNINSTALL_ARGS"
keep=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir) INSTALL_DIR=$2; shift 2 ;;
        --keep-models) keep=true; shift ;;
        --force|--non-interactive) shift ;;
        *) exit 66 ;;
    esac
done
log_info() { :; }
''' + function(UNINSTALL, 'preserve_model_cache') + '''
if $keep; then preserve_model_cache >/dev/null || exit 68; fi
rm -rf "$INSTALL_DIR"
'''


class ReinstallPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        base = Path(cls.fixture.name)
        cls.real = {tool: real(tool) for tool in ['uname', 'df', 'du', 'curl']}
        cls.repo = base / 'repo'
        ods = cls.repo / 'ods'
        ods.mkdir(parents=True)
        for directory in ['installers', 'lib']:
            shutil.copytree(ROOT / directory, ods / directory,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ['install-core.sh', 'ods-cli', 'docker-compose.base.yml']:
            shutil.copy2(ROOT / name, ods / name)
        shutil.copy2(ROOT / 'install.sh', ods / 'install-real.sh')
        write(ods / 'install.sh', CANDIDATE_INSTALL, 0o755)
        write(ods / 'ods-uninstall.sh', CANDIDATE_UNINSTALL, 0o755)
        cls.sha = cls.commit(cls.repo, 'candidate', init=True)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    @staticmethod
    def commit(repo, message, init=False):
        if init:
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
        subprocess.run(['git', '-C', str(repo), *GIT_IDENTITY, 'commit', '-qm', message], check=True)
        return subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.install = self.home / 'ods'
        self.result = self.root / 'result.json'
        self.uninstall_args = self.root / 'uninstall-args'
        self.sudo_calls = self.root / 'sudo-calls'
        self.common_bin = self.root / 'bin'
        self.macos_bin = self.root / 'bin-macos'
        self.write_stubs()
        self.env = dict(os.environ, HOME=str(self.home), ODS_INSTALL_DIR=str(self.install),
            ODS_REPO_URL=str(self.repo), ODS_REF=self.sha, ODS_ALLOW_LEGACY_PARALLEL='1',
            RESULT=str(self.result), UNINSTALL_ARGS=str(self.uninstall_args),
            SUDO_CALLS=str(self.sudo_calls), LOG_FILE=str(self.root / 'ods-install.log'),
            ODS_TEST_FREE_GB='200', ODS_TEST_DOCKER_CONTEXT='desktop-linux', NO_COLOR='1')
        for name in ['INSTALL_DIR', 'ODS_HOME', 'ODS_SCRIPT_HINT', 'ODS_PLATFORM_OVERRIDE']:
            self.env.pop(name, None)

    def write_stubs(self):
        common, macos = self.common_bin, self.macos_bin
        write(common / 'docker', '''#!/bin/bash
case "${1:-}" in
    --version) echo "Docker version 28.0.0, build fixture" ;;
    context)
        case "${2:-}" in
            show) echo "$ODS_TEST_DOCKER_CONTEXT" ;;
            inspect) [[ "$ODS_TEST_DOCKER_CONTEXT" == default ]] \\
                && echo unix:///var/run/docker.sock \\
                || echo unix:///Users/fixture/.docker/run/docker.sock ;;
        esac ;;
    version) [[ "$*" == *--format* ]] && echo 28.0.0 ;;
    info)
        [[ "$*" == *NCPU* ]] && echo 12
        [[ "$*" == *OperatingSystem* ]] && echo '"Ubuntu 24.04 LTS"' ;;
esac
exit 0
''', 0o755)
        write(common / 'df', '''#!/bin/bash
if [[ "${1:-}" == -g ]]; then
    echo "Filesystem 1G-blocks Used Available Capacity iused ifree %iused Mounted on"
    echo "/dev/disk3s5 460 400 ${ODS_TEST_FREE_GB:?} 94% 0 0 0% /System/Volumes/Data"
    exit 0
fi
exec @REAL@ "$@"
'''.replace('@REAL@', self.real['df']), 0o755)
        # Optional per-path sizes (PATH=KB lines) stand in for a large tree.
        write(common / 'du', '''#!/bin/bash
target="${@: -1}"
if [[ -n "${ODS_TEST_DU_TABLE:-}" ]]; then
    while IFS='=' read -r path kb; do
        [[ "$path" == "$target" ]] && { printf '%s\\t%s\\n' "$kb" "$target"; exit 0; }
    done < "$ODS_TEST_DU_TABLE"
fi
exec @REAL@ "$@"
'''.replace('@REAL@', self.real['du']), 0o755)
        write(common / 'curl', '''#!/bin/bash
case "${@: -1}" in
    --version) echo "curl 8.5.0 (fixture)" ;;
    *registry-1.docker.io*)
        [[ "${ODS_TEST_DOCKER_HUB:-up}" == up ]] || { echo "curl: (6) Could not resolve host" >&2; exit 6; }
        printf 401 ;;
    *github.com*) printf 200 ;;
    *) exec @REAL@ "$@" ;;
esac
'''.replace('@REAL@', self.real['curl']), 0o755)
        write(common / 'sudo', '#!/bin/bash\nprintf "%s\\n" "$*" >> "$SUDO_CALLS"\nexit 1\n', 0o755)
        for name in ['lspci', 'nvidia-smi']:
            write(common / name, '#!/bin/bash\nexit 1\n', 0o755)
        # Apple Silicon host facts for the macOS installer. `uname -s` stays
        # real: shared helpers choose GNU or BSD stat from it.
        write(macos / 'uname', '''#!/bin/bash
[[ "${1:-}" == -m ]] && { echo "${ODS_TEST_ARCH:-arm64}"; exit 0; }
exec @REAL@ "$@"
'''.replace('@REAL@', self.real['uname']), 0o755)
        write(macos / 'sw_vers', '''#!/bin/bash
case "${1:-}" in -productVersion) echo 15.3 ;; -buildVersion) echo 24D60 ;; esac
''', 0o755)
        write(macos / 'sysctl', '''#!/bin/bash
case "${2:-}" in
    machdep.cpu.brand_string) echo "Apple M4 Max" ;;
    hw.memsize) echo 137438953472 ;;
    hw.ncpu) echo 16 ;;
    hw.perflevel0.logicalcpu) echo 12 ;;
    hw.perflevel1.logicalcpu) echo 4 ;;
    *) exit 1 ;;
esac
''', 0o755)
        write(macos / 'system_profiler', '#!/bin/bash\nexit 0\n', 0o755)
        write(macos / 'mount', '#!/bin/bash\necho "/dev/disk3s5 on / (apfs, local, journaled)"\n', 0o755)

    def make_installed(self):
        for name in ['.env', 'ods-cli', 'ods-uninstall.sh', 'docker-compose.base.yml']:
            write(self.install / name, 'old fixture\n')
        write(self.install / 'old-runtime', 'replaced by the reinstall\n')
        write(self.install / 'data/models/llm/model.gguf', '')
        (self.install / 'data/models/llm/model.gguf').write_bytes(MODEL)
        return (self.install / 'data/models').stat().st_ino

    def make_stranded(self):
        """The tree the observed failure left: fresh source, models, no .env."""
        inode = self.make_installed()
        (self.install / '.env').unlink()
        (self.install / 'old-runtime').unlink()
        write(self.install / 'stranded-leftover', 'from the interrupted run\n')
        return inode

    def bootstrap(self, *args, platform='macos', keep_models=True):
        env = dict(self.env, ODS_PLATFORM_OVERRIDE=platform)
        path = [str(self.common_bin), env['PATH']]
        if platform == 'macos':
            path.insert(0, str(self.macos_bin))
        env['PATH'] = os.pathsep.join(path)
        flags = ['--non-interactive', '--force'] + (['--keep-models'] if keep_models else [])
        if platform == 'macos':
            flags += ['--recommended', '--pixel', '--no-bootstrap']
        result = subprocess.run(['bash', str(BOOTSTRAP), *flags, *args], env=env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        return result, result.stdout + result.stderr

    def assert_untouched(self, output, env=True):
        self.assertFalse(self.uninstall_args.exists(), 'uninstaller ran:\n' + output)
        self.assertFalse(self.result.exists(), 'installer ran:\n' + output)
        self.assertEqual((self.install / 'data/models/llm/model.gguf').read_bytes(), MODEL, output)
        self.assertEqual((self.install / '.env').exists(), env, output)
        self.assertFalse(Path(str(self.install) + '.models-backup').exists(), output)
        self.assertIn('Existing installation was not replaced', output)

    def test_insufficient_disk_stops_before_uninstall_and_same_command_recovers(self):
        inode = self.make_installed()
        self.env['ODS_TEST_FREE_GB'] = '25'
        result, output = self.bootstrap()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn('At least 30 GB free space required. Found 25 GB.', output)
        self.assert_untouched(output)
        self.assertTrue((self.install / 'old-runtime').exists())

        # Free space and re-run the identical command: the tree is still a
        # recognized install, so --keep-models is accepted and completes.
        self.env['ODS_TEST_FREE_GB'] = '200'
        result, output = self.bootstrap()
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('--keep-models', self.uninstall_args.read_text().split())
        value = json.loads(self.result.read_text())
        self.assertEqual(value['args'], ['--non-interactive', '--force', '--recommended', '--pixel', '--no-bootstrap'])
        self.assertEqual(value['model'], MODEL.hex())
        self.assertEqual(value['modelInode'], inode)
        self.assertFalse(value['oldRuntime'])

    def test_disk_credit_counts_reclaimed_space_but_not_retained_models(self):
        self.make_installed()
        table = self.root / 'du-table'
        gib = 1024 * 1024
        # The extra 64 MiB covers the candidate source that is copied back in.
        table.write_text(f'{self.install}={20 * gib + 65536}\n{self.install}/data/models={18 * gib}\n')
        self.env.update(ODS_TEST_DU_TABLE=str(table), ODS_TEST_FREE_GB='12')

        # Models stay on this filesystem: 12 GB free + 2 GB reclaimed < 30.
        result, output = self.bootstrap()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn('Found 14 GB', output)
        self.assert_untouched(output)

        # Without --keep-models the whole 20 GB tree is reclaimed: 32 >= 30.
        result, output = self.bootstrap(keep_models=False)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('12 GB now, 32 GB once the existing installation is removed', output)
        self.assertNotIn('--keep-models', self.uninstall_args.read_text().split())
        self.assertIsNone(json.loads(self.result.read_text())['model'])

    def test_unsupported_macos_host_stops_before_uninstall(self):
        self.make_installed()
        self.env['ODS_TEST_ARCH'] = 'x86_64'
        result, output = self.bootstrap()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn('Apple Silicon (arm64) is required', output)
        self.assert_untouched(output)

    def test_stopped_container_engine_stops_before_uninstall(self):
        self.make_installed()
        write(self.common_bin / 'docker', '#!/bin/bash\n[[ "${1:-}" == --version ]] && echo "Docker version 28.0.0"\n'
            '[[ "${1:-} ${2:-}" == "context show" ]] && echo desktop-linux\n'
            '[[ "${1:-}" == version ]] && exit 1\nexit 0\n', 0o755)
        result, output = self.bootstrap()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn('Docker daemon is not responding', output)
        self.assert_untouched(output)

    def test_preflight_only_reports_instead_of_changing_the_host(self):
        self.make_installed()
        config = self.home / '.docker/config.json'
        write(config, '{"credsStore": "desktop"}\n')
        self.env['ODS_TEST_DOCKER_CONTEXT'] = 'default'
        result, output = self.bootstrap()
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('The installer will strip credsStore=desktop', output)
        # The recording installer never rewrites it, so only the preflight could have.
        self.assertEqual(config.read_text(), '{"credsStore": "desktop"}\n')
        self.assertFalse(self.sudo_calls.exists(), output)

    def test_stranded_keep_models_tree_is_resumed(self):
        inode = self.make_stranded()
        result, output = self.bootstrap()
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('Resuming that reinstall', output)
        # No configured installation is left, so the uninstaller is not needed.
        self.assertFalse(self.uninstall_args.exists(), output)
        value = json.loads(self.result.read_text())
        self.assertEqual(value['model'], MODEL.hex())
        self.assertEqual(value['modelInode'], inode)
        self.assertFalse(value['leftover'])
        self.assertFalse(Path(str(self.install) + '.models-backup').exists())

    def test_stranded_tree_is_kept_when_preflight_fails(self):
        self.make_stranded()
        self.env['ODS_TEST_FREE_GB'] = '25'
        result, output = self.bootstrap()
        self.assertNotEqual(result.returncode, 0, output)
        self.assert_untouched(output, env=False)
        self.assertTrue((self.install / 'stranded-leftover').exists())

    def test_stranded_recognition_stays_strict(self):
        def broken_fingerprint():
            (self.install / 'ods-cli').unlink()

        def symlinked_models():
            models = self.install / 'data/models'
            models.rename(self.root / 'models-elsewhere')
            models.symlink_to(self.root / 'models-elsewhere', target_is_directory=True)

        def dangling_env():
            (self.install / '.env').symlink_to(self.root / 'missing-env')

        def symlinked_data():
            data = self.install / 'data'
            data.rename(self.root / 'data-elsewhere')
            data.symlink_to(self.root / 'data-elsewhere', target_is_directory=True)

        for mutate in [broken_fingerprint, symlinked_models, dangling_env, symlinked_data]:
            with self.subTest(case=mutate.__name__):
                shutil.rmtree(self.install, ignore_errors=True)
                self.make_stranded()
                mutate()
                result, output = self.bootstrap()
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn('Cannot preserve models', output)
                self.assertFalse(self.uninstall_args.exists(), output)
                self.assertTrue((self.install / 'stranded-leftover').exists(), output)
                self.assertFalse(Path(str(self.install) + '.models-backup').exists(), output)

    def test_stranded_tree_without_keep_models_keeps_incomplete_install_handling(self):
        self.make_stranded()
        result, output = self.bootstrap(keep_models=False)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('Removing incomplete install because --force was provided', output)
        self.assertNotIn('Resuming that reinstall', output)
        self.assertIsNone(json.loads(self.result.read_text())['model'])

    def test_candidate_without_reinstall_preflight_is_refused_before_uninstall(self):
        self.make_installed()
        older = Path(tempfile.mkdtemp(dir=self.root)) / 'repo'
        shutil.copytree(self.repo, older)
        (older / 'ods/installers/reinstall-preflight.sh').unlink()
        self.env.update(ODS_REPO_URL=str(older), ODS_REF=self.commit(older, 'older candidate'))
        result, output = self.bootstrap()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn('predates the forced-reinstall preflight', output)
        self.assert_untouched(output)

    @unittest.skipUnless(LINUX_USER, 'needs a non-root Linux host for install-core.sh')
    def test_linux_preflight_failure_stops_before_uninstall(self):
        self.make_installed()
        self.env['ODS_TEST_DOCKER_HUB'] = 'down'
        result, output = self.bootstrap(platform='linux')
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn('Could not reach Docker Hub', output)
        self.assert_untouched(output)

    @unittest.skipUnless(LINUX_USER, 'needs a non-root Linux host for install-core.sh')
    def test_linux_preflight_pass_proceeds_without_privilege(self):
        inode = self.make_installed()
        result, output = self.bootstrap(platform='linux')
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('Preflight passed; no changes were made.', output)
        self.assertFalse(self.sudo_calls.exists(), output)
        self.assertEqual(json.loads(self.result.read_text())['modelInode'], inode)


if __name__ == '__main__':
    unittest.main()
