"""Exercise production completion receipt code without installing any services."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

def bash_command():
    configured = os.environ.get('ODS_TEST_BASH')
    if configured:
        return configured
    if os.name == 'nt':
        git_bash = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
        return str(git_bash) if git_bash.exists() else None
    return shutil.which('bash')

class PortalReceipt(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell completion protocol')
    def test_windows_verified_receipt_without_browser_auto_open(self):
        # Execute the real top-level forwarding block, excluding all host setup.
        script = r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:WINDOWS_SCRIPT,[ref]$tokens,[ref]$errors)
if($errors.Count){throw $errors[0]}
$block=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if ($installerExitCode -eq 0) {') -and $node.Extent.Text.Contains('ODS_PORTAL_URL=')},$true))
if($block.Count -ne 1){throw 'Expected one production receipt block'}
$OpenPortal=$false
$verifyOutput=@('ODS_PORTAL_URL='+$env:PORTAL_URL)
$installerExitCode=[int]$env:VERIFY_CODE
Invoke-Expression $block[0].Extent.Text
'''
        for url, code, expected in [
            ('http://localhost:4321/pixel', 0, 'ODS_PORTAL_URL=http://localhost:4321/pixel'),
            ('http://localhost:4321/pixel', 7, ''),
            ('http://localhost:0/pixel', 0, ''),
            ('http://localhost:65536/pixel', 0, ''),
            ('http://example.com:4321/pixel', 0, ''),
        ]:
            with self.subTest(url=url, code=code):
                env=dict(os.environ, WINDOWS_SCRIPT=str(ROOT/'installers/windows.ps1'), PORTAL_URL=url, VERIFY_CODE=str(code))
                result=subprocess.run(['powershell.exe','-NoProfile','-Command',script],env=env,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),expected)

    def test_unix_completion_receipt_pixel_and_real_install_only(self):
        bash=bash_command()
        if not bash:
            self.skipTest('Bash unavailable')
        helper=(ROOT/'installers/lib/progress.sh').as_posix()
        for enabled, dry, gui, port, expected in [
            ('true','false','1','4321','ODS_PORTAL_URL=http://localhost:4321/pixel'),
            ('true','false','1','3001','ODS_PORTAL_URL=http://localhost:3001/pixel'),
            ('false','false','1','4321',''),('true','true','1','4321',''),
            ('true','false','0','4321',''),('true','false','1','0',''),
            ('true','false','1','65536',''),('true','false','1','bad',''),
        ]:
            with self.subTest(enabled=enabled,dry=dry,gui=gui,port=port):
                result=subprocess.run([bash,'-c','source "$1"; ods_portal_receipt "$2" "$3" "$4"','test',helper,enabled,port,dry],env=dict(os.environ,ODS_INSTALLER_GUI=gui),capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),expected)

    def test_linux_completion_caller_uses_resolved_service_port(self):
        bash = bash_command()
        if not bash:
            self.skipTest('Bash unavailable')
        phase = ROOT / 'installers/phases/13-summary.sh'
        caller = phase.read_text(encoding='utf-8').split(
            '# Windows emits its own receipt after its additional WSL readiness gate.\n', 1
        )[1]
        command = 'source "$1"; declare -A SERVICE_PORTS=([dashboard]=4321); ENABLE_PIXEL_RUNTIME=true; DRY_RUN=false; ' + caller
        result = subprocess.run(
            [bash, '-c', command, 'test', (ROOT / 'installers/lib/progress.sh').as_posix()],
            env=dict(os.environ, ODS_INSTALLER_GUI='1'), capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ODS_PORTAL_URL=http://localhost:4321/pixel')

    def test_macos_completion_caller_reads_installed_port_and_real_helper(self):
        bash = bash_command()
        if not bash:
            self.skipTest('Bash unavailable')
        import tempfile
        script = ROOT / 'installers/macos/install-macos.sh'
        # Execute the actual terminal caller and production dotenv reader only.
        caller = script.read_text(encoding='utf-8').split(
            '# The macOS compose stack publishes DASHBOARD_PORT from the installed .env.\n', 1
        )[1]
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory)
            (installed / '.env').write_text('DASHBOARD_PORT=4321\n')
            command = 'SCRIPT_DIR="$1"; INSTALL_DIR="$2"; source "$3"; ENABLE_PIXEL=true; DRY_RUN=false; ' + caller
            result = subprocess.run(
                [bash, '-c', command, 'test', script.parent.as_posix(), installed.as_posix(),
                 (ROOT / 'installers/macos/lib/env-generator.sh').as_posix()],
                env=dict(os.environ, ODS_INSTALLER_GUI='1'), capture_output=True, text=True, timeout=10
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'ODS_PORTAL_URL=http://localhost:4321/pixel')

if __name__ == '__main__':
    unittest.main()
