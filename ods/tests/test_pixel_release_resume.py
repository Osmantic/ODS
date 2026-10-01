"""Execute the installer recovery helper with explicit service fixtures."""
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case', ['none', 'unfinished', 'apply', 'rollback',
                                  'changed-config', 'verify-failed', 'finish-failed'])
def test_resume_only_proved_release_and_verify_source_before_marker(tmp_path, case):
    helper = tmp_path / 'protected-client'
    helper.touch()
    events = tmp_path / 'events'
    status = {'pending': False}
    if case != 'none':
        status = dict(pending=True, kind='model', transaction_id='a' * 64,
                      release_completion=dict(outcome='rollback' if case == 'rollback' else 'apply',
                                              config_sha256='b' * 64))
        if case == 'unfinished':
            del status['release_completion']
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps(status))
    source = (ROOT / 'installers/lib/pixel-host-install.sh').read_text()
    function = source.split('_ods_pixel_resume_completed_release() {', 1)[1].split(
        '\n_ods_pixel_begin_release_transition()', 1)[0]
    function = '_ods_pixel_resume_completed_release() {' + function
    # Relocate only the fixed client existence check into the isolated fixture.
    function = function.replace('/usr/local/libexec/ods-pixel-access/pixel_model_transition.py', str(helper))
    script = r'''
set -eu
events=$1
status_file=$2
scenario=$3
_ods_pixel_model_transition() {
    if [[ "$1" == status ]]; then cat "$status_file"; return; fi
    echo "$*" >> "$events"
    [[ "$scenario" != finish-failed ]]
}
ods_pixel_run_as_owner() {
    [[ "$3" == sha256sum ]] || return 2
    if [[ "$scenario" == changed-config ]]; then printf 'c%.0s' {1..64}
    else printf 'b%.0s' {1..64}; fi
    echo '  config'
}
_ods_pixel_verify_current_runtime() {
    echo verify >> "$events"
    [[ "$scenario" != verify-failed ]]
}
_ods_pixel_mark_verified_installing() { echo "$*" >> "$events"; }
'''
    script += function + '\n_ods_pixel_resume_completed_release fixture /home/fixture /pixel contract\n'
    result = subprocess.run(['bash', '-c', script, 'fixture', str(events), str(status_file), case],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == (case in ('none', 'apply', 'rollback')), result.stderr
    lines = events.read_text().splitlines() if events.exists() else []
    if case in ('none', 'unfinished', 'changed-config'):
        assert lines == []
    else:
        expected = ['finish fixture /home/fixture ' + 'a' * 64 +
                    (' rolled-back' if case == 'rollback' else ' applied')]
        if case not in ('rollback', 'finish-failed'):
            expected += ['verify']
        if case == 'apply':
            expected += ['fixture /home/fixture contract /pixel']
        assert lines == expected
