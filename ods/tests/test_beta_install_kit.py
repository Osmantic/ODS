"""Pinned beta source launchers must refuse changed bytes before executing code."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/build-beta-install-kit.py'
PROJECT = SCRIPT.parents[2]
spec = importlib.util.spec_from_file_location('beta_kit', SCRIPT)
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


class InstallKitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ods-beta-kit-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.out = Path(self.temp.name) / 'release'
        self.git('init', '--quiet')
        (self.root / 'ods').mkdir()
        (self.root / 'ods/fixture.txt').write_text('fixture', encoding='utf-8')
        # Use real wrappers and attributes: a flat echo fixture missed archive
        # CRLF conversion and the executable bit needed by the shell delegate.
        for name in ('.gitattributes', 'install.sh', 'install.ps1'):
            shutil.copyfile(PROJECT / name, self.root / name)
        (self.root / 'ods/install.sh').write_text(
            '#!/bin/bash\necho INSTALLER_EXECUTED\n'
            'printf "INSTALLER_UMASK=%s\\n" "$(umask)"\n'
            'printf "config" > fixture-created.conf\n'
            'mkdir fixture-created-dir\n', encoding='utf-8', newline='\n')
        windows = self.root / 'ods/installers/windows'
        windows.mkdir(parents=True)
        (windows / 'install-windows.ps1').write_text("Write-Output 'INSTALLER_EXECUTED'\n", encoding='utf-8')
        self.git('add', '.')
        self.git('update-index', '--chmod=+x', 'install.sh', 'ods/install.sh')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.receipt = kit.build(self.root, self.commit,
                                 'https://github.com/Osmantic/ODS/releases/download/test-beta', self.out)
        self.archive = next(self.out.glob('*.zip'))

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True)

    def test_archive_receipt_binds_exact_commit_and_installer(self):
        self.assertEqual(self.receipt['resolvedCommit'], self.commit)
        self.assertEqual(self.receipt['tree'], self.git('rev-parse', 'HEAD^{tree}').strip())
        self.assertEqual(self.receipt['archiveSha256'], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        with zipfile.ZipFile(self.archive) as archive:
            for name in ('install.sh', 'install.ps1'):
                body = archive.read('ods-beta/' + name)
                self.assertEqual(self.receipt['installerSha256'][name], hashlib.sha256(body).hexdigest())
            self.assertIn(b'\r\n', archive.read('ods-beta/install.ps1'))
        raw_blob = subprocess.check_output(['git', '-C', str(self.root), 'show', self.commit + ':install.ps1'])
        self.assertNotEqual(self.receipt['installerSha256']['install.ps1'], hashlib.sha256(raw_blob).hexdigest())
        self.assertEqual(len(self.receipt['launcherSha256']), 2)

    def test_rejects_mutable_ref_and_unsafe_release_url(self):
        with self.assertRaises(ValueError):
            kit.build(self.root, 'public-beta', 'https://github.com/Osmantic/ODS/releases/download/test', self.out)
        with self.assertRaises(ValueError):
            kit.build(self.root, self.commit, 'https://example.com/release', self.out)

    def test_existing_artifacts_are_not_overwritten(self):
        with self.assertRaisesRegex(ValueError, 'already exist'):
            kit.build(self.root, self.commit, 'https://github.com/Osmantic/ODS/releases/download/test', self.out)

    def launch(self, verify=True, python_only=False, caller_umask=None):
        env = dict(os.environ)
        env['ODS_TEST_ARCHIVE'] = str(self.archive)
        env['TMPDIR'] = env['TEMP'] = self.temp.name
        if os.name == 'nt':
            exe = shutil.which('pwsh') or shutil.which('powershell')
            command = ("function Invoke-WebRequest { param($Uri,$OutFile,[switch]$UseBasicParsing); "
                       "Copy-Item -LiteralPath $env:ODS_TEST_ARCHIVE -Destination $OutFile }; & '"
                       + str(self.out / 'install-beta.ps1').replace("'", "''") + "'"
                       + (' -VerifyOnly' if verify else ''))
            args = [exe, '-NoProfile', '-NonInteractive', '-Command', command]
        else:
            bindir = Path(self.temp.name) / 'bin'
            bindir.mkdir(exist_ok=True)
            curl = bindir / 'curl'
            curl.write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n'
                            'if [ "$1" = --output ]; then shift; cp "$ODS_TEST_ARCHIVE" "$1"; exit; fi\n'
                            'shift\ndone\nexit 1\n', encoding='utf-8')
            curl.chmod(0o700)
            if python_only:
                # Omit unzip without modifying the generated launcher.
                for tool in ('bash', 'python3', 'cp', 'mktemp', 'mkdir', 'awk', 'cat', 'dirname', 'sha256sum', 'shasum'):
                    executable = shutil.which(tool)
                    if executable:
                        (bindir / tool).symlink_to(executable)
                env['PATH'] = str(bindir)
            else:
                env['PATH'] = str(bindir) + os.pathsep + env['PATH']
            args = ['bash', str(self.out / 'install-beta.sh')]
            if verify:
                args.append('--verify-only')
            if caller_umask is not None:
                args = ['bash', '-c', 'umask "$1"; shift; exec bash "$@"',
                        'launcher-test', caller_umask, *args[1:]]
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)

    def test_platform_launcher_verifies_before_execution(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.commit, result.stdout)
        self.assertNotIn('INSTALLER_EXECUTED', result.stdout)
        receipts = list(Path(self.temp.name).glob('ods-beta*/ods-beta/ods/beta-install-receipt.json'))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(json.loads(receipts[0].read_text())['resolvedCommit'], self.commit)
        installed = self.launch(verify=False)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn('INSTALLER_EXECUTED', installed.stdout)

    def test_altered_download_never_executes_installer(self):
        self.archive.write_bytes(b'untrusted changed response')
        result = self.launch(verify=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('INSTALLER_EXECUTED', result.stdout)
        self.assertIn('checksum mismatch', result.stdout + result.stderr)

    def test_verify_only_is_self_contained_after_build_checkout_is_unavailable(self):
        # Simulate a release consumer who has only downloaded kit artifacts.
        temp_root = Path(self.temp.name).resolve()
        unavailable = temp_root / 'unavailable-source'
        self.assertEqual(self.root.resolve().parent, temp_root)
        self.assertEqual(unavailable.parent, temp_root)
        self.root.rename(unavailable)
        result = self.launch(verify=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('INSTALLER_EXECUTED', result.stdout)
        self.assertIn(self.commit, result.stdout)

    @unittest.skipIf(os.name == 'nt', 'Unix extraction permissions')
    def test_python_fallback_preserves_archive_modes_and_caller_umask(self):
        result = self.launch(verify=False, python_only=True, caller_umask='022')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('INSTALLER_EXECUTED', result.stdout)
        sources = list(Path(self.temp.name).glob('ods-beta*/ods-beta'))
        self.assertEqual(len(sources), 1)
        delegate_mode = stat.S_IMODE((sources[0] / 'ods/install.sh').stat().st_mode)
        data_mode = stat.S_IMODE((sources[0] / 'ods/fixture.txt').stat().st_mode)
        self.assertEqual(delegate_mode, 0o755)
        self.assertEqual(data_mode, 0o644)
        self.assertIn('INSTALLER_UMASK=0022', result.stdout)
        self.assertEqual(stat.S_IMODE((sources[0] / 'ods/fixture-created.conf').stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((sources[0] / 'ods/fixture-created-dir').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(sources[0].parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((sources[0].parent / 'source.zip').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((sources[0] / 'ods/beta-install-receipt.json').stat().st_mode), 0o600)

    @unittest.skipIf(os.name == 'nt', 'Unix umask propagation')
    def test_launcher_keeps_an_intentionally_restrictive_caller_umask(self):
        result = self.launch(verify=False, python_only=True, caller_umask='077')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('INSTALLER_UMASK=0077', result.stdout)
        sources = list(Path(self.temp.name).glob('ods-beta*/ods-beta'))
        self.assertEqual(stat.S_IMODE((sources[0] / 'ods/fixture-created.conf').stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
