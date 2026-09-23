"""Exercise real bootstrap pip callsites against inert, offline fixture wheels.

Only individual function definitions are loaded. No installer, service, model
download or owner Python environment is run or modified. pip writes to a
temporary --target directory, and network/index access is disabled.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def shell_function(path, name):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^([ \t]*)" + re.escape(name) + r"\(\) \{.*?^\1\}", text)
    if not match:
        raise AssertionError(f"Missing shell function {name} in {path}")
    return match[0]


STUB = r'''
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
stage = pathlib.Path(os.environ['ODS_LOCK_TEST_STAGE'])
if args[:1] == ['-c']:
    if args[1].strip().startswith('import sys'):
        sys.exit(0)
    sys.exit(0 if (stage/'packages/ods_hash_fixture.py').exists() else 1)
if args[:3] == ['-m', 'pip', '--version']:
    sys.exit(0)
if args[:3] == ['-m', 'pip', 'install']:
    with (stage/'pip-args.jsonl').open('a') as handle:
        handle.write(json.dumps(args) + '\n')
    if '--require-hashes' not in args or '--only-binary=:all:' not in args:
        print('missing required integrity flags', file=sys.stderr)
        sys.exit(87)
    args = [item for item in args if item not in ('--user', '--break-system-packages')]
    env = os.environ.copy()
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX='1', PIP_FIND_LINKS=str(stage/'wheels'),
               PIP_DISABLE_PIP_VERSION_CHECK='1')
    command = [sys.executable, *args, '--no-cache-dir', '--target', str(stage/'packages')]
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    with (stage/'pip-boundary.log').open('a') as handle:
        handle.write(result.stdout + result.stderr)
    print(result.stdout, end='')
    print(result.stderr, end='', file=sys.stderr)
    sys.exit(result.returncode)
if args and args[0].endswith(('download-hf-artifact.py', 'download-hf-snapshot.py')):
    (stage/'download-called').write_text('fixture only')
    sys.exit(0)
raise SystemExit('Unexpected bootstrap command: ' + repr(args))
'''


class ExtraBootstrapLocks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ods-extra-lock-boundary-")
        self.addCleanup(temporary.cleanup)
        self.stage = Path(temporary.name)
        (self.stage / "wheels").mkdir()
        (self.stage / "scripts").mkdir()
        locks = self.stage / "installers/python-deps"
        locks.mkdir(parents=True)
        self.wheel = self.stage / "wheels/ods_hash_fixture-0.0.0-py3-none-any.whl"
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr("ods_hash_fixture.py", 'VALUE = "reviewed"\n')
            archive.writestr("ods_hash_fixture-0.0.0.dist-info/METADATA",
                             "Metadata-Version: 2.1\nName: ods-hash-fixture\nVersion: 0.0.0\n")
            archive.writestr("ods_hash_fixture-0.0.0.dist-info/WHEEL",
                             "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr("ods_hash_fixture-0.0.0.dist-info/RECORD", "")
        approved = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        for name in ("host-agent", "zeroconf"):
            (locks / f"{name}.txt").write_text(f"ods-hash-fixture==0.0.0 --hash=sha256:{approved}\n")
        for name in ("download-hf-artifact.py", "download-hf-snapshot.py"):
            (self.stage / "scripts" / name).write_text("# inert fixture; never executed\n")
        self.stub = self.stage / "python-boundary.py"
        self.stub.write_text(STUB)
        self.env = os.environ.copy()
        self.env["ODS_LOCK_TEST_STAGE"] = str(self.stage)

    def substitute_wheel(self):
        with zipfile.ZipFile(self.wheel, "a") as archive:
            archive.writestr("substitution.txt", "unapproved bytes")

    def run_shell_boundary(self, boundary):
        if os.name == "nt":
            self.skipTest("POSIX shell boundary runs on Linux/macOS")
        binary = self.stage / "bin"
        binary.mkdir(exist_ok=True)
        launcher = binary / "python3"
        launcher.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(self.stub))} \"$@\"\n")
        launcher.chmod(0o755)
        # A regression to a PATH-level pip must never install in the host.
        for name in ("pip", "pip3"):
            forbidden = binary / name
            forbidden.write_text("#!/bin/sh\necho 'standalone pip is forbidden in this fixture' >&2\nexit 86\n")
            forbidden.chmod(0o755)
        self.env["PATH"] = str(binary) + os.pathsep + self.env.get("PATH", "")
        preamble = '''set -euo pipefail
INSTALL_DIR="$ODS_LOCK_TEST_STAGE"
SOURCE_ROOT="$INSTALL_DIR"
SCRIPT_DIR="$INSTALL_DIR"
LOG_FILE="$INSTALL_DIR/pip.log"
ODS_LOG_FILE="$LOG_FILE"
PYTHON_CMD=python3
ODS_PYTHON_CMD=python3
ENABLE_EMBEDDINGS=true
EMBEDDING_MODEL=fixture/model
ai() { :; }
ai_bad() { :; }
ai_warn() { :; }
log() { :; }
error() { printf '%s\\n' "$*" >&2; }
ods_ensure_python_pip() { return 0; }
'''
        installer = ROOT / "installers"
        pip_helper = shell_function(installer / "lib/python-runtime.sh", "ods_python_pip_install_user")
        if boundary == "linux-artifact":
            definition = shell_function(installer / "phases/11-services.sh", "_phase11_download_hf_artifact")
            call = '_phase11_download_hf_artifact https://huggingface.co/fixture/model/resolve/main/file "$INSTALL_DIR/result" "$LOG_FILE"'
        elif boundary == "linux-embeddings":
            definition = shell_function(installer / "phases/11-services.sh", "_phase11_prefetch_embeddings_model")
            call = "_phase11_prefetch_embeddings_model"
        elif boundary == "macos-artifact":
            definition = shell_function(installer / "macos/lib/ui.sh", "download_hf_artifact_with_python")
            call = 'download_hf_artifact_with_python https://huggingface.co/fixture/model/resolve/main/file "$INSTALL_DIR/result"'
        elif boundary == "pre-download":
            definition = shell_function(ROOT / "scripts/pre-download.sh", "check_dependencies")
            call = "check_dependencies"
        else:
            definition = shell_function(installer / "phases/07-devtools.sh", "_install_zeroconf_via_pip")
            call = "_install_zeroconf_via_pip"
        # pre-download derives its lock relative to BASH_SOURCE; mirror that layout.
        harness = self.stage / "scripts/test-boundary.sh"
        harness.write_text(preamble + pip_helper + "\n" + definition + "\n" + call + "\n")
        return subprocess.run(["bash", str(harness)], env=self.env, text=True, capture_output=True, timeout=40)

    def assert_integrity_rejection(self, result):
        log = self.stage / "pip.log"
        output = result.stdout + result.stderr + (log.read_text() if log.exists() else "")
        boundary_log = self.stage / "pip-boundary.log"
        if boundary_log.exists():
            output += boundary_log.read_text()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("DO NOT MATCH THE HASHES", output)
        self.assertFalse((self.stage / "packages/ods_hash_fixture.py").exists())
        self.assertFalse((self.stage / "download-called").exists())

    def test_linux_artifact_rejects_substitution(self):
        self.substitute_wheel()
        self.assert_integrity_rejection(self.run_shell_boundary("linux-artifact"))

    def test_linux_embeddings_rejects_substitution(self):
        self.substitute_wheel()
        self.assert_integrity_rejection(self.run_shell_boundary("linux-embeddings"))

    def test_macos_artifact_rejects_substitution(self):
        self.substitute_wheel()
        self.assert_integrity_rejection(self.run_shell_boundary("macos-artifact"))

    def test_pre_download_rejects_substitution(self):
        self.substitute_wheel()
        self.assert_integrity_rejection(self.run_shell_boundary("pre-download"))

    def test_mdns_rejects_substitution(self):
        self.substitute_wheel()
        self.assert_integrity_rejection(self.run_shell_boundary("mdns"))

    def test_reviewed_wheel_still_installs_in_temporary_target(self):
        result = self.run_shell_boundary("pre-download")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.stage / "packages/ods_hash_fixture.py").is_file())

    def test_missing_lock_does_not_fall_back_to_package_name(self):
        (self.stage / "installers/python-deps/host-agent.txt").unlink()
        result = self.run_shell_boundary("macos-artifact")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.stage / "pip-args.jsonl").exists())
        self.assertFalse((self.stage / "download-called").exists())

    @unittest.skipUnless(os.name == "nt", "Real PowerShell boundary runs on Windows")
    def test_windows_artifact_rejects_substitution(self):
        self.substitute_wheel()
        self.env.update(ODS_LOCK_TEST_UI=str(ROOT / "installers/windows/lib/ui.ps1"),
                        ODS_LOCK_TEST_PYTHON=sys.executable)
        harness = self.stage / "boundary.ps1"
        harness.write_text(r'''
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:ODS_LOCK_TEST_UI, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'UI helper parse failed' }
foreach ($name in @('Invoke-ODSHuggingFaceDownloadFallback','Invoke-ODSNativeQuiet','Test-ODSHuggingFaceResolveUrl')) {
    $node = $ast.Find({ param($item) $item -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq $name }, $true)
    Invoke-Expression $node.Extent.Text
}
function Get-ODSHuggingFaceDownloadHelper { return (Join-Path $env:ODS_LOCK_TEST_STAGE 'scripts/download-hf-artifact.py') }
function Get-ODSPythonDownloadCommand { return [pscustomobject]@{ FilePath=$env:ODS_LOCK_TEST_PYTHON; PrefixArgs=@((Join-Path $env:ODS_LOCK_TEST_STAGE 'python-boundary.py')) } }
function Write-AI { param($Message) }
if (Invoke-ODSHuggingFaceDownloadFallback -Url 'https://huggingface.co/fixture/model/resolve/main/file' -Destination (Join-Path $env:ODS_LOCK_TEST_STAGE 'result')) { exit 0 }
exit 1
''')
        result = subprocess.run([shutil.which("pwsh") or "powershell", "-NoProfile", "-File", str(harness)],
                                env=self.env, text=True, capture_output=True, timeout=40)
        self.assert_integrity_rejection(result)
        self.assertTrue((self.stage / "pip-args.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
