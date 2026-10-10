"""Run the real bootstrap the way the README does: `curl ... | bash`, no options.

macOS ships Bash 3.2 as /bin/bash. There, expanding an empty array under
`set -u` is an "unbound variable" error, so a bootstrap that only works when
options are passed dies before cloning for anyone using the one-line install.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / 'get-ods.sh'


def interpreters():
    """The default bash, plus the system Bash 3.2 where one exists (macOS)."""
    found = {'bash': 'bash'}
    system = Path('/bin/bash')
    if system.is_file():
        major = subprocess.run([str(system), '-c', 'echo "${BASH_VERSINFO[0]}"'],
            capture_output=True, text=True, check=True).stdout.strip()
        if int(major) < 4:
            found['system bash ' + major] = str(system)
    return found


class SystemBashBootstrapTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.home = root / 'home'
        self.home.mkdir()
        repo = root / 'repo'
        (repo / 'ods').mkdir(parents=True)
        (repo / 'ods/install.sh').write_text(
            '#!/bin/bash\n'
            'python3 -c \'import json,os,sys; open(os.environ["RESULT"],"w").write(json.dumps(sys.argv[1:]))\' "$@"\n')
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
            '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        stubs = root / 'bin'
        stubs.mkdir()
        for name in ['docker', 'nvidia-smi', 'lspci']:
            (stubs / name).write_text('#!/bin/bash\nexit 1\n')
            (stubs / name).chmod(0o755)
        self.result = root / 'result.json'
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(stubs) + ':' + os.environ['PATH'],
            ODS_REPO_URL=str(repo), ODS_ALLOW_LEGACY_PARALLEL='1', RESULT=str(self.result))
        for name in ['ODS_INSTALL_DIR', 'ODS_REF', 'ODS_BOOTSTRAP_REF', 'ODS_HOME']:
            self.env.pop(name, None)

    def bootstrap(self, shell, *args):
        return subprocess.run([shell, str(BOOTSTRAP), *args], env=self.env, cwd=self.home,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def test_bootstrap_reaches_the_installer_without_options(self):
        for label, shell in interpreters().items():
            with self.subTest(label):
                if self.result.exists():
                    self.result.unlink()
                subprocess.run(['rm', '-rf', str(self.home / 'ods')], check=True)
                result = self.bootstrap(shell)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(self.result.read_text()), [])

    def test_bootstrap_passes_installer_options_unchanged(self):
        for label, shell in interpreters().items():
            with self.subTest(label):
                if self.result.exists():
                    self.result.unlink()
                subprocess.run(['rm', '-rf', str(self.home / 'ods')], check=True)
                result = self.bootstrap(shell, '--non-interactive', '--tier', '1', '--summary-json', 'a b.json')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(self.result.read_text()),
                    ['--non-interactive', '--tier', '1', '--summary-json', 'a b.json'])


class BootstrapGpuNoticeTests(unittest.TestCase):
    """Run the production preliminary check without invoking the installer."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.drm = self.root / 'drm'
        self.drm.mkdir()
        self.stubs = self.root / 'bin'
        self.stubs.mkdir()
        self.calls = self.root / 'smi-calls'
        self.stub('nvidia-smi', 'exit 1\n')
        self.stub('lspci', 'exit 1\n')

    def stub(self, name, body):
        path = self.stubs / name
        path.write_text('#!/bin/bash\n' + body)
        path.chmod(0o755)

    def vendor(self, value):
        parent = self.drm / 'card0/device'
        parent.mkdir(parents=True)
        (parent / 'vendor').write_text(value + '\n')

    def query(self, body):
        self.stub('nvidia-smi',
            'echo called >> "$SMI_CALLS"\n'
            '[[ "$*" == "--query-gpu=name,memory.total --format=csv,noheader" ]] || exit 99\n'
            + body)

    def notice(self, os_name='wsl'):
        source = BOOTSTRAP.read_text()
        block = source[source.index('# GPU check (early info'):source.index('\n# git\n')]
        # Substitute only the external sysfs path; all detection logic is verbatim.
        block = block.replace('/sys/class/drm', shlex.quote(str(self.drm)))
        script = ('set -euo pipefail\nOS=' + shlex.quote(os_name) + '\n'
                  'success() { printf "OK: %s\\n" "$1"; }\n'
                  'warn() { printf "WARN: %s\\n" "$1"; }\n' + block)
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
            timeout=30, env=dict(os.environ, PATH=str(self.stubs) + ':' + os.environ['PATH'],
                                SMI_CALLS=str(self.calls)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_wsl_empty_drm_uses_successful_query(self):
        self.query('printf "RTX 5090, 32607 MiB\\n"\n')
        self.assertIn('NVIDIA GPU detected: RTX 5090', self.notice())
        self.assertEqual(self.calls.read_text().splitlines(), ['called'])

    def test_wsl_microsoft_virtual_vendor_uses_query(self):
        self.vendor('0x1414')
        self.query('printf "RTX 5090, 32607 MiB\\n"\n')
        self.assertIn('NVIDIA GPU detected: RTX 5090', self.notice())

    def test_failed_and_empty_queries_do_not_prove_gpu(self):
        for body in ['printf "RTX 5090, 32607 MiB\\n"; exit 1\n', 'exit 0\n']:
            with self.subTest(body=body):
                self.query(body)
                output = self.notice()
                self.assertNotIn('NVIDIA GPU detected', output)
                self.assertIn('installer will check again', output)

    def test_wsl_multi_gpu_output_is_fully_consumed_once(self):
        self.query('for ((i=0; i<10000; i++)); do printf "GPU %s, 24564 MiB\\n" "$i"; done\n')
        output = self.notice()
        self.assertIn('NVIDIA GPU detected: GPU 0, 24564 MiB', output)
        self.assertNotIn('GPU 1,', output)
        self.assertEqual(self.calls.read_text().splitlines(), ['called'])

    def test_baremetal_amd_does_not_trust_installed_nvidia_tool(self):
        self.vendor('0x1002')
        self.query('printf "NVIDIA stub, 24564 MiB\\n"\n')
        self.assertIn('AMD GPU detected', self.notice('linux'))
        self.assertFalse(self.calls.exists())

    def test_baremetal_nvidia_still_requires_vendor_witness(self):
        self.query('printf "RTX 5090, 32607 MiB\\n"\n')
        self.assertNotIn('NVIDIA GPU detected', self.notice('linux'))
        self.assertFalse(self.calls.exists())
        self.vendor('0x10de')
        self.assertIn('NVIDIA GPU detected: RTX 5090', self.notice('linux'))

    def test_intel_arc_path_is_preserved(self):
        self.vendor('0x8086')
        self.stub('lspci', 'printf "VGA Intel Arc Graphics\\n"\n')
        self.assertIn('Intel Arc GPU detected', self.notice('linux'))

    def test_macos_has_no_cpu_fallback_warning(self):
        self.assertNotIn('No GPU detected', self.notice('macos'))


if __name__ == '__main__':
    unittest.main()
