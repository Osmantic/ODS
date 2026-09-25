"""Real native identity/prospective-state checks precede both rollback mutations."""
import json
import os
from pathlib import Path
import subprocess
import pytest
from test_source_update_preflight import guard, linux_identity as linux_identity, save_marker

ROOT = Path(__file__).resolve().parents[1]

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
