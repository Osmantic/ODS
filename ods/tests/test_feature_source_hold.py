from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
PHASE = ROOT / 'installers/phases/03-features.sh'


def feature_functions():
    source = PHASE.read_text()
    start = source.index('_sync_extension_compose_at() {')
    end = source.index('\nif ! $DRY_RUN;', start)
    return source[start:end]


def run_selection(tmp_path, *, managed=True, same=False, enable=True, both=False, apply=False, token=False, disable=False):
    home, source, installed = (tmp_path / name for name in ('home', 'source', 'installed'))
    for path in (home, source, installed):
        path.mkdir()
    if managed:
        marker = home / '.config/ods/pixel-managed.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('{}')  # Mere presence defers; Phase06 authenticates it.
    for root in (source, installed):
        service = root / 'extensions/services/whisper'
        service.mkdir(parents=True)
        (service / 'compose.yaml.disabled').write_text('original')
        if both:
            (service / 'compose.yaml').write_text('active')
    if same:
        source = installed
    script = ('set -euo pipefail\nHOME="$1"; SCRIPT_DIR="$2"; INSTALL_DIR="$3"\n'
              'log(){ :; }; error(){ printf "%s\\n" "$*" >&2; return 1; }\n'
              'ENABLE_PIXEL_RUNTIME=true\nods_pixel_install_owner(){ printf fixture-owner; }\n' + feature_functions() + '\n'
              '_sync_extension_compose "$4" whisper fixture fixture\n')
    if apply:
        # Simulate only the already-authenticated copy result or completed uninstall.
        if token:
            script += ('ODS_PIXEL_SOURCE_TRANSACTION="' + 'a' * 64 + '"\n'
                       '_ods_pixel_check_source_transaction(){ [[ "$#" == 1 && "$1" == fixture-owner ]]; }\n'
                       'rm -f "$INSTALL_DIR/extensions/services/whisper/compose.yaml.disabled"\n'
                       'cp "$SCRIPT_DIR/extensions/services/whisper/compose.yaml" "$INSTALL_DIR/extensions/services/whisper/compose.yaml"\n')
        if disable:
            script += 'rm "$HOME/.config/ods/pixel-managed.json"\nENABLE_PIXEL_RUNTIME=false\n'
        script += '_ods_apply_deferred_feature_state\n'
    result = subprocess.run(['/bin/bash', '-c', script, 'feature-test', str(home), str(source), str(installed), str(enable).lower()], capture_output=True, text=True)
    return result, source, installed


@pytest.mark.parametrize('both', [False, True])
def test_managed_selection_changes_candidate_only_before_hold(tmp_path, both):
    result, source, installed = run_selection(tmp_path, both=both)
    assert result.returncode == 0, result.stderr
    assert (source/'extensions/services/whisper/compose.yaml').exists()
    assert not (source/'extensions/services/whisper/compose.yaml.disabled').exists()
    assert (installed/'extensions/services/whisper/compose.yaml.disabled').read_text() == 'original'
    assert (installed/'extensions/services/whisper/compose.yaml').exists() == both


def test_fresh_install_retains_existing_selection_behavior(tmp_path):
    result, _, installed = run_selection(tmp_path, managed=False)
    assert result.returncode == 0, result.stderr
    assert (installed/'extensions/services/whisper/compose.yaml').exists()


def test_in_place_change_is_rejected_without_changing_either_name(tmp_path):
    result, _, installed = run_selection(tmp_path, same=True)
    assert result.returncode != 0
    assert (installed/'extensions/services/whisper/compose.yaml.disabled').read_text() == 'original'
    assert not (installed/'extensions/services/whisper/compose.yaml').exists()


def test_in_place_identical_selection_does_not_require_new_source(tmp_path):
    result, _, installed = run_selection(tmp_path, same=True, enable=False)
    assert result.returncode == 0, result.stderr
    assert (installed/'extensions/services/whisper/compose.yaml.disabled').exists()


def test_held_copy_is_already_reconciled_before_deferred_apply(tmp_path):
    result, _, installed = run_selection(tmp_path, apply=True, token=True)
    assert result.returncode == 0, result.stderr
    assert (installed/'extensions/services/whisper/compose.yaml').exists()


def test_deferred_apply_refuses_unheld_managed_selection_change(tmp_path):
    result, _, installed = run_selection(tmp_path, apply=True)
    assert result.returncode != 0
    assert (installed/'extensions/services/whisper/compose.yaml.disabled').exists()


def test_explicit_deactivation_applies_selection_after_marker_retirement(tmp_path):
    result, _, installed = run_selection(tmp_path, apply=True, disable=True)
    assert result.returncode == 0, result.stderr
    assert (installed/'extensions/services/whisper/compose.yaml').exists()


def test_same_ref_feature_change_requires_authenticated_transition(tmp_path):
    host = (ROOT / 'installers/lib/pixel-host-install.sh').read_text()
    start = host.index('_ods_pixel_source_transition_required() {')
    end = host.index('\n}\n', start) + 3
    function = host[start:end]
    script = ('set -euo pipefail\n'
              '_ods_pixel_source_transition_state(){ printf "ready|%s\\n" "$3"; }\n'
              '_ODS_PIXEL_FEATURE_SOURCE_CHANGED=true\n' + function + '\n'
              '_ods_pixel_source_transition_required owner /tmp/home ' + 'a'*40 + ' /tmp/unused-candidate\n')
    result = subprocess.run(['/bin/bash', '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    # The selection flag requests a transaction; it does not bypass bad custody.
    script = script.replace('printf "ready|%s\\n" "$3"', 'return 2')
    result = subprocess.run(['/bin/bash', '-c', script], capture_output=True, text=True)
    assert result.returncode == 2


@pytest.mark.parametrize('artifact', [None, 'state', 'config', 'program', 'unit', 'broken-link', 'configured'])
def test_initial_bootstrap_source_refresh_requires_absent_host_deployment(tmp_path, artifact):
    host = (ROOT / 'installers/lib/pixel-host-install.sh').read_text()
    start = host.index('_ods_pixel_source_transition_required() {')
    function = host[start:host.index('\n}\n', start) + 3]
    paths = dict(state='/var/lib/ods-pixel-access', config='/etc/ods/pixel-access.json',
                 program='/usr/local/libexec/ods-pixel-access', unit='/etc/systemd/system/openclaw-gateway.service')
    for label, path in paths.items():
        function = function.replace(path, str(tmp_path / label))
    if artifact in paths:
        (tmp_path / artifact).touch()
    if artifact == 'broken-link':
        (tmp_path / 'state').symlink_to(tmp_path / 'missing')
    script = ('set -eu\nINSTALL_DIR=$1\n'
              '_ods_pixel_source_transition_state(){ printf "installing|%s\\n" "$3"; }\n'
              '_ods_pixel_initial_unconfigured_marker(){ test "${PIXEL_SOURCE_REF:?}" = "' + 'a'*40 + '" || exit 9; return ' + ('1' if artifact == 'configured' else '0') + '; }\n'
              '_ODS_PIXEL_FEATURE_SOURCE_CHANGED=true\n' + function + '\n'
              '_ods_pixel_source_transition_required owner "$1" ' + 'a'*40 + ' /candidate\n')
    result = subprocess.run(['bash', '-c', script, 'fixture', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == (1 if artifact is None else 0), result.stderr


def test_topology_is_deferred_until_held_downstream(tmp_path):
    home, source, installed = (tmp_path / name for name in ('home', 'source', 'installed'))
    for item in (home, source, installed):
        item.mkdir()
    marker = home / '.config/ods/pixel-managed.json'
    marker.parent.mkdir(parents=True)
    marker.write_text('{}')
    config = installed / 'config'
    config.mkdir()
    target = config / 'gpu-topology.json'
    target.write_text('old topology')
    generated = tmp_path / 'generated.json'
    generated.write_text('{"gpu_assignment":{}}\n')
    tail = PHASE.read_text().split('# Keep generated topology outside', 1)[1]
    tail = '# Keep generated topology outside' + tail
    script = ('set -euo pipefail\nHOME="$1"; SCRIPT_DIR="$2"; INSTALL_DIR="$3"; TOPOLOGY_FILE="$4"\n'
              'DRY_RUN=false; _ODS_DEFERRED_FEATURE_SELECTION=()\nods_pixel_install_owner(){ printf fixture-owner; }\n'
              'error(){ printf "%s\\n" "$*" >&2; return 1; }; log(){ :; }\n'
              + feature_functions() + '\n' + tail + '\n'
              '[[ "$(cat "$INSTALL_DIR/config/gpu-topology.json")" == "old topology" ]]\n'
              '[[ "$_ODS_PIXEL_FEATURE_SOURCE_CHANGED" == true ]]\n'
              'if _ods_apply_deferred_feature_state; then exit 90; fi\n'
              '[[ "$(cat "$INSTALL_DIR/config/gpu-topology.json")" == "old topology" ]]\n'
              'ODS_PIXEL_SOURCE_TRANSACTION="held"\n'
              '_ods_pixel_check_source_transaction(){ return 1; }\n'
              'if _ods_apply_deferred_feature_state; then exit 91; fi\n'
              '_ods_pixel_check_source_transaction(){ [[ "$#" == 1 && "$1" == fixture-owner ]]; }\n'
              '_ods_apply_deferred_feature_state\n')
    result = subprocess.run(['/bin/bash', '-c', script, 'topology-test', str(home), str(source), str(installed), str(generated)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert target.read_text() == '{"gpu_assignment":{}}\n'
    assert not generated.exists()


def test_cloud_phase_returns_before_topology_generation_or_write(tmp_path):
    text = PHASE.read_text()
    start = text.index('if [[ "${GPU_COUNT:-0}"')
    tail = text[start:]
    wrapper = tmp_path / 'cloud-tail.sh'
    wrapper.write_text(tail)
    result = subprocess.run(['/bin/bash', '-c', 'set -euo pipefail; log(){ :; }; GPU_COUNT=0; PATH=/nonexistent; source "$1"', 'cloud-test', str(wrapper)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_candidate_symlink_cannot_rename_installed_feature_before_hold(tmp_path):
    home, source, installed = (tmp_path / name for name in ('home', 'source', 'installed'))
    for item in (home, source, installed):
        item.mkdir()
    marker = home / '.config/ods/pixel-managed.json'
    marker.parent.mkdir(parents=True)
    marker.write_text('{}')
    service = installed / 'extensions/services/whisper'
    service.mkdir(parents=True)
    (service / 'compose.yaml.disabled').write_text('retained')
    (source / 'extensions/services').mkdir(parents=True)
    (source / 'extensions/services/whisper').symlink_to(service, target_is_directory=True)
    script = ('set -euo pipefail\nHOME="$1"; SCRIPT_DIR="$2"; INSTALL_DIR="$3"\n'
              'log(){ :; }; error(){ return 1; }\n' + feature_functions() + '\n'
              'if _sync_extension_compose true whisper fixture fixture; then exit 0; else exit 23; fi\n')
    result = subprocess.run(['/bin/bash', '-c', script, 'symlink-test', str(home), str(source), str(installed)], capture_output=True, text=True)
    assert result.returncode == 23
    assert (service / 'compose.yaml.disabled').read_text() == 'retained'
    assert not (service / 'compose.yaml').exists()


def test_deferred_apply_without_any_selection_is_safe_under_nounset(tmp_path):
    script = ('set -euo pipefail\nHOME="$1"; INSTALL_DIR="$1"; SCRIPT_DIR="$1"\n'
              'log(){ :; }; error(){ return 1; }\n' + feature_functions() + '\n'
              '_ods_apply_deferred_feature_state\n')
    result = subprocess.run(['/bin/bash', '-c', script, 'empty-selection', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_phase06_applies_deferred_selection_after_authenticated_copy_and_downstream():
    phase = (ROOT / 'installers/phases/06-directories.sh').read_text()
    # Actual called shell functions are exercised above; this contract also keeps
    # their callsite behind the protected exchange rather than before its hold.
    stage = phase.index('_ods_pixel_source_upgrade stage')
    hold = phase.index('_ods_pixel_source_upgrade hold', stage)
    copy = phase.index('_ods_pixel_source_upgrade copy', hold)
    downstream = phase.index('_ods_pixel_source_upgrade downstream', copy)
    generic_copy = phase.index('_phase06_step "copy-source"', downstream)
    reconcile = phase.index('_ods_apply_deferred_feature_state ||', generic_copy)
    assert stage < hold < copy < downstream < generic_copy < reconcile
    assert phase.index('ods_pixel_uninstall_managed', downstream) < reconcile


@pytest.mark.parametrize('status,expected', [
    ({'pending': True, 'transaction': 'a'*64, 'phase': 'applied', 'mode': 'full-access'}, 0),
    ({'pending': True, 'transaction': 'b'*64, 'phase': 'applied', 'mode': 'full-access'}, 1),
    ({'pending': True, 'transaction': 'a'*64, 'phase': 'held', 'mode': 'full-access'}, 1),
    ({'pending': False, 'transaction': 'a'*64, 'phase': 'applied', 'mode': 'full-access'}, 1),
    ({'pending': True, 'transaction': 'a'*64, 'phase': 'applied', 'mode': 'unknown'}, 1),
])
def test_deferred_apply_uses_real_owner_resolver_and_checker_signature(tmp_path, status, expected):
    import json
    host = (ROOT / 'installers/lib/pixel-host-install.sh').read_text()
    def function(name):
        start = host.index(name + '() {')
        return host[start:host.index('\n}\n', start)+3]
    owner = subprocess.check_output(['id', '-un'], text=True).strip()
    script = ('set -euo pipefail\nHOME="$1"; INSTALL_DIR="$1"; SCRIPT_DIR="$1"; INSTALL_USER="$2"\n'
              'ODS_PIXEL_SOURCE_TRANSACTION="' + 'a'*64 + '"\n'
              + function('ods_pixel_install_owner') + '\n'
              + function('_ods_pixel_check_source_transaction') + '\n'
              + '_ods_pixel_source_upgrade(){ [[ "$#" == 2 && "$1" == status && "$2" == "$INSTALL_USER" ]] || return 79; cat "$HOME/status.json"; }\n'
              + feature_functions() + '\n_ods_apply_deferred_feature_state\n')
    (tmp_path/'status.json').write_text(json.dumps(status))
    result = subprocess.run(['/bin/bash', '-c', script, 'real-signature', str(tmp_path), owner], capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    # A malformed/root owner is rejected by the existing real resolver.
    refused = subprocess.run(['/bin/bash', '-c', script, 'real-signature', str(tmp_path), 'root'], capture_output=True, text=True)
    assert refused.returncode != 0
