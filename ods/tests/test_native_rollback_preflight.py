"""Real native identity/prospective-state checks precede both rollback mutations."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import pytest
from test_source_update_preflight import guard, linux_identity as linux_identity, save_marker

ROOT = Path(__file__).resolve().parents[1]
DISCORD = 'https://discord.gg/4ntNp9MAwC'


def test_native_rollback_refusal_names_steps_that_work(tmp_path, monkeypatch, capsys):
    # #7969: the refusal ended in "use deployment-specific recovery", a step
    # documented nowhere, so a native Pixel owner with a failed update had no
    # way forward. `ods update` never touches native Pixel, and these steps do.
    install, snapshot = tmp_path / 'install', tmp_path / 'snapshot'
    install.mkdir()
    snapshot.mkdir()
    monkeypatch.setattr(guard, 'managed_pixel_identity', lambda path: 'linux')
    monkeypatch.setattr(sys, 'argv', ['source-update-preflight.py', 'rollback',
                                      '--install-dir', str(install), '--snapshot', str(snapshot)])
    assert guard.main() == 1
    refusal = capsys.readouterr().err
    assert 'deployment-specific' not in refusal, refusal
    for step in ("'ods status'", "'ods logs <service>'", "'ods start'", DISCORD):
        assert step in refusal, refusal


def shell_function(text, name):
    match = re.search(rf'^{name}\(\) \{{\n(.*?)^\}}$', text, re.S | re.M)
    assert match, f'{name} not found in ods-cli'
    return match.group(1)


def test_update_failure_guidance_works_on_portal_installs():
    # `ods rollback` refuses every Portal (native Pixel) install, the Linux
    # default, so `ods update` must not send owners there without saying so,
    # and its failures must name the steps that work everywhere.
    cli = (ROOT / 'ods-cli').read_text(encoding='utf-8')
    update = shell_function(cli, 'cmd_update')
    for name, body in (('cmd_update', update), ('_check_version_compat', shell_function(cli, '_check_version_compat'))):
        for line in body.splitlines():
            if re.match(r'\s*(log|log_error|error|warn|echo)\b', line) and 'ods rollback' in line:
                assert 'without Portal (Pixel)' in line, f'{name}: {line.strip()}'
    lines = update.splitlines()
    for marker in ('Failed to recreate containers', 'services are not running'):
        start = next(index for index, line in enumerate(lines) if marker in line)
        following = '\n'.join(lines[start:start + 3])
        assert "'ods start'" in following, following
    assert DISCORD in update

@pytest.mark.parametrize('state', ['ready', 'installing'])
def test_current_managed_rollback_refused(linux_identity, tmp_path, state):
    install, marker, record = linux_identity
    record['state'] = state
    save_marker(marker, record)
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    with pytest.raises((ValueError, RuntimeError)):
        guard.check_rollback(install, snapshot)

@pytest.mark.parametrize('mode', ['manual', 'automatic'])
@pytest.mark.parametrize('selection', ['ordinary', 'current-native', 'snapshot-native', 'snapshot-quoted-native', 'snapshot-unsafe-env'])
def test_actual_rollback_guards_before_mutation(tmp_path, mode, selection):
    install = tmp_path / 'install'
    scripts = install / 'scripts'
    scripts.mkdir(parents=True)
    (scripts / 'source-update-preflight.py').symlink_to(ROOT / 'scripts/source-update-preflight.py')
    snapshot = tmp_path / 'backups/point'
    snapshot.mkdir(parents=True)
    (snapshot / 'snapshot.json').write_text(json.dumps({'version': '2.6.0'}))
    before = 'VALUE=before\n' + ('PIXEL_AGENT_MODE=pixel\n' if selection == 'current-native' else '')
    (install / '.env').write_text(before)
    value = 'VALUE=after\n'
    if selection == 'snapshot-native':
        value += 'PIXEL_AGENT_MODE=pixel\n'
    if selection == 'snapshot-quoted-native':
        value += 'PIXEL_AGENT_MODE="pixel" # retained selection\n'
    (snapshot / '.env').write_text(value)
    if selection == 'snapshot-unsafe-env':
        (snapshot / '.env').rename(snapshot / 'saved-env')
        (snapshot / '.env').symlink_to(snapshot / 'saved-env')
    effects = tmp_path / 'effects'
    program = r'''
set -euo pipefail
source <(sed '$d' "$SOURCE")
INSTALL_DIR="$FIXTURE";ROLLBACK_DIR="$SNAPSHOT_ROOT";BACKUP_DIR="$SNAPSHOT_ROOT"
log_info(){ :; };log_ok(){ :; };log_warn(){ :; };log_error(){ :; }
resolve_compose_flags(){ :; }
_restore_snapshot(){ echo restore >> "$EFFECTS";cp "$1/.env" "$INSTALL_DIR/.env"; }
docker(){ echo docker >> "$EFFECTS"; }
docker-compose(){ echo legacy >> "$EFFECTS"; }
wait_for_healthy(){ return 0; }
if [[ $MODE == manual ]];then cmd_rollback point;else _update_rollback synthetic "$SNAPSHOT_ROOT/point";fi
'''
    env = {**os.environ, 'SOURCE': os.environ.get('ODS_ROLLBACK_SCRIPT_UNDER_TEST', str(ROOT / 'ods-update.sh')),
           'FIXTURE': str(install), 'SNAPSHOT_ROOT': str(snapshot.parent), 'MODE': mode, 'EFFECTS': str(effects)}
    result = subprocess.run(['bash', '-c', program], env=env, text=True, capture_output=True)
    if selection == 'ordinary':
        assert result.returncode == 0, result.stdout + result.stderr
        assert (install / '.env').read_text() == value
        assert 'restore' in effects.read_text()
    else:
        assert result.returncode != 0, 'native rollback unexpectedly succeeded'
        assert (install / '.env').read_text() == before
        assert not effects.exists(), effects.read_text() if effects.exists() else ''
