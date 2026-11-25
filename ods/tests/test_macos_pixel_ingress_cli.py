"""Run the shipped CLI with controlled process boundaries, including failure ordering."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(os.environ.get('ODS_INGRESS_CLI_SOURCE_ROOT', Path(__file__).resolve().parents[1]))
# The CLI requires Bash 4+. Sourcing under 3.2 would re-exec its caller ($0),
# so use the same explicit modern Bash as the real lifecycle-child fixtures.
BASH = os.environ.get('ODS_LIFECYCLE_TEST_BASH', '/bin/bash')


@pytest.mark.parametrize('command', ['start', 'restart', 'update'])
@pytest.mark.parametrize('case', ['network', 'proxy', 'loopback', 'already-auth', 'write-failure'])
def test_native_cli_auth_precedes_frozen_selection(tmp_path, command, case):
    install = tmp_path / 'install'
    install.mkdir()
    env_file = install / '.env'
    bind = '127.0.0.1' if case in ('proxy', 'loopback') else '0.0.0.0'
    initial_auth = 'true' if case == 'already-auth' else 'false'
    original = f'SHIELD_API_KEY=fixture\nBIND_ADDRESS={bind}\nWEBUI_AUTH={initial_auth}\n'
    env_file.write_text(original)
    inode = env_file.stat().st_ino
    if case == 'proxy':
        proxy = install / 'extensions/services/ods-proxy/compose.yaml'
        proxy.parent.mkdir(parents=True)
        proxy.write_text('services: {}\n')
    log = tmp_path / 'commands'
    python = tmp_path / 'private-python'
    python.write_text('''#!/bin/bash
set -euo pipefail
auth=$(sed -n 's/^WEBUI_AUTH=//p' "$ODS_INSTALL_DIR/.env")
printf 'refresh:%s:%s\\n' "$auth" "$WEBUI_AUTH" >> "$EVENT_LOG"
''')
    python.chmod(0o700)
    script = '''
source "$CLI_SOURCE" help >/dev/null
test_install() { :; }
ensure_llama_cpu_budget() { :; }
get_compose_flags() { printf '%s\\n' '-f installers/macos/pixel-native.compose.yaml.disabled'; }
macos_maybe_resume_bootstrap_upgrade() { :; }
docker() { printf 'unexpected-docker\\n' >> "$EVENT_LOG"; return 29; }
if [[ "$AUTH_CASE" == write-failure ]]; then
    upsert_env_value() { printf 'write-refused\\n' >> "$EVENT_LOG"; return 17; }
fi
'''
    result = subprocess.run([BASH, '-c', script + '\ncmd_' + command],
        env={**os.environ, 'ODS_INSTALL_DIR': str(install), 'ODS_PYTHON_CMD': str(python),
            'EVENT_LOG': str(log), 'CLI_SOURCE': str(ROOT / 'installers/macos/ods-macos.sh'),
            'AUTH_CASE': case, 'WEBUI_AUTH': 'false'},
        text=True, capture_output=True, timeout=15)
    events = log.read_text().splitlines() if log.exists() else []
    if case == 'write-failure':
        assert result.returncode != 0, result.stdout
        assert events == ['write-refused'], 'Auth publication failure must precede any lifecycle work'
        assert env_file.read_text() == original
    else:
        assert result.returncode == 0, result.stderr[-500:]
        expected = 'false' if case == 'loopback' else 'true'
        assert events == [f'refresh:{expected}:{expected}'], 'Capture must see persisted and exported auth'
        assert env_file.read_text() == original.replace('WEBUI_AUTH=false', f'WEBUI_AUTH={expected}')
    assert env_file.stat().st_ino == inode, 'Auth preparation must preserve the live env inode'


@pytest.mark.parametrize('command', ['start pixel-native-ingress', 'restart pixel-native-ingress', 'start', 'restart', 'update'])
@pytest.mark.parametrize('failure', [False, True])
def test_native_cli_parent_delegates_whole_body_and_never_recreates_after_release(tmp_path, command, failure):
    install = tmp_path / 'install'
    install.mkdir()
    (install / '.env').write_text('SHIELD_API_KEY=fixture\n')
    log = tmp_path / 'commands'
    python = tmp_path / 'private-python'
    python.write_text('#!/bin/bash\nprintf "refresh\\n" >> "$EVENT_LOG"\nexit ' + ('19' if failure else '0') + '\n')
    python.chmod(0o700)
    script = '''
source "$CLI_SOURCE" help >/dev/null
test_install() { :; }
ensure_llama_cpu_budget() { :; }
get_compose_flags() { printf '%s\n' '-f installers/macos/pixel-native.compose.yaml.disabled'; }
network_access_is_enabled() { return 1; }
macos_wait_for_bootstrap_compose_safe() { :; }
macos_maybe_resume_bootstrap_upgrade() { :; }
start_native_llama() { :; }
compose_pull_with_retry() { :; }
cmd_status() { :; }
sleep() { :; }
docker() { printf 'docker:%s\n' "$*" >> "$EVENT_LOG"; }
'''
    result = subprocess.run([BASH, '-c', script + '\ncmd_' + command],
        env={**os.environ, 'ODS_INSTALL_DIR': str(install), 'ODS_PYTHON_CMD': str(python),
            'EVENT_LOG': str(log), 'CLI_SOURCE': str(ROOT / 'installers/macos/ods-macos.sh')},
        text=True, capture_output=True, timeout=15)
    events = log.read_text().splitlines() if log.exists() else []
    assert events and events[0] == 'refresh', (result.returncode, events, result.stderr[-500:])
    if failure:
        assert result.returncode != 0 and events == ['refresh']
    else:
        assert result.returncode == 0, result.stderr[-500:]
        assert events == ['refresh'], 'The parent must not restart native or Compose after its transaction returns'


def test_unrelated_service_needs_no_native_refresh(tmp_path):
    install = tmp_path / 'install'
    install.mkdir()
    log = tmp_path / 'commands'
    # The public entry point is a script, not a sourced Bash library. Exercise
    # the real system-Bash handoff too (3.2 -> Homebrew Bash on macOS).
    public_entry = subprocess.run(['/bin/bash', str(ROOT / 'installers/macos/ods-macos.sh'), 'help'],
        env={**os.environ, 'ODS_INSTALL_DIR': str(install)},
        text=True, capture_output=True, timeout=15)
    assert public_entry.returncode == 0, public_entry.stderr[-500:]
    assert 'start' in public_entry.stdout and 'restart' in public_entry.stdout
    script = '''source "$CLI_SOURCE" help >/dev/null
test_install() { :; }; ensure_llama_cpu_budget() { :; }
get_compose_flags() { printf '%s\n' '-f installers/macos/pixel-native.compose.yaml.disabled'; }
network_access_is_enabled() { return 1; }
docker() { printf 'docker:%s\n' "$*" >> "$EVENT_LOG"; }
# This control isolates native dispatch; the private-store integration suite
# separately exercises the real storage dispatcher and exact Compose argv.
dashboard_compose_up() { printf 'private:%s:%s\n' "$2" "$3" >> "$EVENT_LOG"; }
cmd_start dashboard-api
'''
    result = subprocess.run([BASH, '-c', script], env={**os.environ,
        'ODS_INSTALL_DIR': str(install), 'ODS_PYTHON_CMD': '/deliberately-absent-python',
        'EVENT_LOG': str(log), 'CLI_SOURCE': str(ROOT / 'installers/macos/ods-macos.sh')},
        text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr[-500:]
    assert log.read_text().splitlines() == ['private:start:dashboard-api']
