"""Run the real bootstrap the way the README does: `curl ... | bash`, no options.

macOS ships Bash 3.2 as /bin/bash. There, expanding an empty array under
`set -u` is an "unbound variable" error, so a bootstrap that only works when
options are passed dies before cloning for anyone using the one-line install.
"""
import json
import os
from pathlib import Path
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


if __name__ == '__main__':
    unittest.main()
