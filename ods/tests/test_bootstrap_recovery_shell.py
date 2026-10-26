"""Run the shell owner-recovery and shared-lock controls in Python CI too."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('name', ['test-linux-installer-model-lifecycle-lock.sh', 'test-model-recover-cli.sh', 'test-pixel-uninstall.sh'])
def test_shell_contract(name):
    if name == 'test-model-recover-cli.sh' and shutil.which('jq') is None:
        pytest.skip('jq is required for the CLI protocol control')
    result = subprocess.run(['bash', str(ROOT / 'tests' / name)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr

@pytest.mark.parametrize('name', ['ods-cli', 'scripts/bootstrap-upgrade.sh', 'installers/lib/model-lifecycle-lock.sh',
                                  'installers/lib/pixel-host-install.sh'])
def test_shell_syntax(name):
    subprocess.run(['bash', '-n', str(ROOT / name)], check=True, timeout=10)
