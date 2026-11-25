"""Run the pre-checkout Mint rewrite against temporary source lists."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/matrix-smoke.yml'
SHELL = os.environ.get('ODS_TEST_BASH') or shutil.which('bash')


@unittest.skipUnless(SHELL and os.name == 'posix', 'POSIX filesystem and bash are required')
class MintTransportTests(unittest.TestCase):
    def rewrite(self, text):
        source = WORKFLOW.read_text(encoding='utf8')
        start = source.index('          configure_mint_ci_repository_transport() {')
        end = source.index('          install_apt_checkout_prerequisites() {', start)
        function = textwrap.dedent(source[start:end])
        with tempfile.TemporaryDirectory(prefix='ods mint transport ') as tmp:
            repo = Path(tmp) / 'official-package-repositories.list'
            source_repo = Path(tmp) / 'official-source-repositories.list'
            sentinel = Path(tmp) / 'unrelated.list'
            sentinel.write_text(text)
            repo.write_text(text)
            source_repo.write_text(text)
            result = subprocess.run([SHELL, '-e', '-c', function + '\nconfigure_mint_ci_repository_transport "$1"',
                                     'fixture', tmp], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sentinel.read_text(), text)
            self.assertEqual(source_repo.read_text(), repo.read_text())
            return repo.read_text()

    def test_image_repository_and_signed_options_are_preserved(self):
        source = ('deb http://packages.linuxmint.com virginia main upstream import backport\n'
                  '  deb-src [arch=amd64 signed-by=/keys/mint.gpg] https://packages.linuxmint.com/ virginia main\n'
                  'deb http://archive.ubuntu.com/ubuntu jammy main restricted universe multiverse\n'
                  'deb http://security.ubuntu.com/ubuntu/ jammy-security main\n')
        expected = source.replace('http://packages.linuxmint.com ', 'https://mirrors.kernel.org/linuxmint-packages/ ').replace(
            'https://packages.linuxmint.com/ ', 'https://mirrors.kernel.org/linuxmint-packages/ ')
        self.assertEqual(self.rewrite(source), expected)
        self.assertEqual(self.rewrite(expected), expected)

    def test_lookalikes_credentials_ports_comments_and_other_releases_are_unchanged(self):
        source = '\n'.join([
            '# deb http://packages.linuxmint.com virginia main',
            'deb http://packages.linuxmint.com.evil.example virginia main',
            'deb http://user@packages.linuxmint.com virginia main',
            'deb http://packages.linuxmint.com:8080 virginia main',
            'deb http://packages.linuxmint.com/subdirectory virginia main',
            'deb http://packages.linuxmint.com wilma main',
            'deb http://example.test/path/http://packages.linuxmint.com virginia main',
        ]) + '\n'
        self.assertEqual(self.rewrite(source), source)


if __name__ == '__main__':
    unittest.main()
