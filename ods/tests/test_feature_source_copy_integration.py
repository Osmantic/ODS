"""Cross-phase feature selection with the real copy and source journal.

Only the privileged coordinator boundary is represented by the existing test
hold verifier. No installed host paths or services are used by these fixtures.
"""
import json
import os
import pwd
import subprocess
from pathlib import Path

import pytest

from test_feature_source_hold import feature_functions
from test_pixel_source_upgrade import trees as trees, held, upgrade


ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / 'installers/lib/pixel-host-install.sh'


def host_function(name):
    source = HOST.read_text()
    start = source.index(name + '() {')
    return source[start:source.index('\n}\n', start) + 3]


def shell_fixture(tmp_path, home, old, new):
    # Isolate only the four fixed deployment-presence paths. Keep the actual
    # marker custody/shape checks and owner execution wrapper unchanged.
    required = (host_function('_ods_pixel_initial_source_copy_allowed') + '\n'
                + host_function('_ods_pixel_source_transition_required'))
    for number, path in enumerate(('/var/lib/ods-pixel-access', '/etc/ods/pixel-access.json',
                                   '/usr/local/libexec/ods-pixel-access',
                                   '/etc/systemd/system/openclaw-gateway.service')):
        required = required.replace(path, str(tmp_path / f'protected-{number}'))
    source = ('set -euo pipefail\n'
              'HOME="$1"; INSTALL_DIR="$2"; SCRIPT_DIR="$3"; INSTALL_USER="$4"\n'
              'log(){ :; }; error(){ printf "%s\\n" "$*" >&2; return 1; }\n')
    source += '\n'.join(host_function(name) for name in (
        'ods_pixel_install_owner', 'ods_pixel_run_as_owner',
        '_ods_pixel_source_transition_state', '_ods_pixel_initial_unconfigured_marker',
        '_ods_pixel_check_source_transaction'))
    source += '\n' + required + '\n' + feature_functions() + '\n'
    args = ['/bin/bash', '-c', source, 'feature-source-flow', str(home), str(old), str(new), pwd.getpwuid(os.getuid()).pw_name]
    return source, args


def feature_copy_flow(trees, tmp_path, runtime, previous, enabled, after_copy='', deferred_ref=None, topology=False):
    manager, old, new, identity = trees
    identity['afterRef'] = identity['beforeRef']
    # No unrelated source change should force a same-release held transition.
    for root in (old, new):
        for path in (root / 'bin').iterdir():
            path.unlink()
        (root / 'bin/same.py').write_text('same source\n')
        (root / 'installers/lib').mkdir()
        (root / 'installers/lib/pixel-host-install.sh').write_text('same integration\n')
        for relative in ('extensions/services/pixel-agent/host', 'extensions/services/pixel-agent/plugin',
                         'extensions/services/whisper'):
            (root / relative).mkdir(parents=True)
    home = tmp_path / 'home'
    marker = home / '.config/ods/pixel-managed.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps(dict(schema_version=2, manager='ods', state=runtime,
        initial_active_state='absent', install_dir=str(old), pixel_source_ref=identity['beforeRef'])))
    marker.chmod(0o600)
    compose = 'extensions/services/whisper/compose.yaml'
    selected = compose if enabled else compose + '.disabled'
    opposite = compose + '.disabled' if enabled else compose
    # A fresh candidate ships active compose; Phase03 makes the selection.
    (new / compose).write_text('selected candidate\n')
    for relative in ([selected] if previous == 'same' else [opposite] if previous == 'opposite' else [selected, opposite]):
        (old / relative).write_text('selected candidate\n')
    source, args = shell_fixture(tmp_path, home, old, new)
    if topology:
        (old / 'config').mkdir()
        (old / 'config/gpu-topology.json').write_text('{"retained":"old topology"}\n')
        phase = (ROOT / 'installers/phases/03-features.sh').read_text()
        tail = '# Keep generated topology outside' + phase.split('# Keep generated topology outside', 1)[1]
        # Both shell invocations carry the state produced by the exact Phase03
        # deferral code. No fabricated hold or direct assignment of its state.
        source += ('\nDRY_RUN=false\nTOPOLOGY_FILE="$HOME/generated-topology.json"\n'
                   'printf \'{"gpu_count":2,"fixture":true}\\n\' > "$TOPOLOGY_FILE"\n'
                   + tail + '\n')
    selection = f'_sync_extension_compose {str(enabled).lower()} whisper fixture fixture\n'
    args[2] = source + selection + (
        'decision=0\n_ods_pixel_source_transition_required "$INSTALL_USER" "$HOME" '
        + identity['beforeRef'] + ' "$SCRIPT_DIR" || decision=$?\n'
        'printf "%s\\n" "$decision"\n')
    chosen = subprocess.run(args, capture_output=True, text=True, check=True)
    decision = int(chosen.stdout.strip())
    expected = 0 if runtime == 'ready' and (previous != 'same' or topology) else 1
    assert decision == expected
    assert (new / selected).exists() and not (new / opposite).exists()
    # The managed installed pair must stay untouched until Phase06's choice.
    assert (old / opposite).exists() == (previous != 'same')
    if decision == 0:
        verify, events = held(manager, new, identity)
        manager.publish(verify)
        assert events and upgrade.inventory(old, os.getuid()) == manager.journal()['after']
        status = dict(pending=True, transaction=manager.journal()['hold'],
                      phase=manager.journal()['phase'], mode='full-access')
        (home / 'status.json').write_text(json.dumps(status))
    # Exercise the exact generic copy and deferred verifier after publication;
    # the status boundary returns the actual SourceUpgrade journal identity.
    args[2] = source + f'_ODS_DEFERRED_FEATURE_SELECTION=(whisper {str(enabled).lower()})\n'
    if decision == 0:
        args[2] += ('ODS_PIXEL_SOURCE_TRANSACTION="' + 'd' * 64 + '"\n'
                    '_ods_pixel_source_upgrade(){ [[ "$1" == status && "$2" == "$INSTALL_USER" ]]; cat "$HOME/status.json"; }\n')
    args[2] += ('source "' + str(ROOT / 'installers/lib/source-copy.sh') + '"\n'
                'ods_copy_install_source "$SCRIPT_DIR" "$INSTALL_DIR" "$HOME/copy.log"\n'
                + after_copy + '\n_ods_apply_deferred_feature_state '
                + (identity['beforeRef'] if deferred_ref is None else deferred_ref) + '\n')
    result = subprocess.run(args, capture_output=True, text=True)
    return result, manager, old, new, selected, opposite, decision


@pytest.mark.parametrize('runtime', ['ready', 'installing'])
@pytest.mark.parametrize('previous', ['same', 'opposite', 'both'])
@pytest.mark.parametrize('enabled', [True, False])
def test_same_release_feature_selection_reconciles_after_real_copy(trees, tmp_path, runtime, previous, enabled):
    result, manager, old, _, selected, opposite, decision = feature_copy_flow(
        trees, tmp_path, runtime, previous, enabled)
    assert result.returncode == 0, result.stderr
    assert (old / selected).read_text() == 'selected candidate\n'
    assert not (old / opposite).exists()
    if decision == 0:
        assert upgrade.inventory(old, os.getuid()) == manager.journal()['after']
    else:
        assert manager.journal() is None


@pytest.mark.parametrize('change', ['native', 'ready', 'configured', 'selected-bytes', 'opposite-link', 'candidate-both', 'wrong-ref', 'empty-ref'])
def test_initial_counterpart_removal_rechecks_inert_state_and_copied_bytes(trees, tmp_path, change):
    after_copy = {
        'native': 'touch "${HOME%/home}/protected-0"',
        'ready': 'sed -i \'s/"installing"/"ready"/\' "$HOME/.config/ods/pixel-managed.json"',
        'configured': 'mkdir -p "$HOME/.local/share/pixel"; touch "$HOME/.local/share/pixel/runtime-attestation.json"',
        'selected-bytes': 'printf edited > "$INSTALL_DIR/extensions/services/whisper/compose.yaml"',
        'opposite-link': 'printf retained > "$HOME/owner-content"; rm "$INSTALL_DIR/extensions/services/whisper/compose.yaml.disabled"; ln -s "$HOME/owner-content" "$INSTALL_DIR/extensions/services/whisper/compose.yaml.disabled"',
        'candidate-both': 'printf ambiguous > "$SCRIPT_DIR/extensions/services/whisper/compose.yaml.disabled"',
        'wrong-ref': '',
        'empty-ref': '',
    }[change]
    result, manager, old, _, _, opposite, _ = feature_copy_flow(
        trees, tmp_path, 'installing', 'opposite', True, after_copy,
        deferred_ref='b' * 40 if change == 'wrong-ref' else '' if change == 'empty-ref' else None)
    assert result.returncode != 0
    assert 'Feature source was not reconciled' in result.stderr
    assert (old / opposite).exists()
    assert manager.journal() is None


@pytest.mark.parametrize('runtime', ['ready', 'installing'])
@pytest.mark.parametrize('enabled', [True, False])
def test_deferred_topology_and_feature_selection_share_the_verified_copy_path(trees, tmp_path, runtime, enabled):
    result, manager, old, _, selected, opposite, decision = feature_copy_flow(
        trees, tmp_path, runtime, 'opposite', enabled, topology=True)
    assert result.returncode == 0, result.stderr
    assert (old / selected).read_text() == 'selected candidate\n'
    assert not (old / opposite).exists()
    target = old / 'config/gpu-topology.json'
    assert json.loads(target.read_text()) == {'gpu_count': 2, 'fixture': True}
    assert target.stat().st_mode & 0o777 == 0o644
    if decision == 0:
        assert upgrade.inventory(old, os.getuid()) == manager.journal()['after']
    else:
        assert manager.journal() is None


@pytest.mark.parametrize('change', ['native', 'ready', 'configured', 'wrong-ref', 'empty-ref', 'config-link', 'topology-link', 'revoked-hold'])
def test_deferred_topology_keeps_prior_bytes_when_authority_or_path_is_invalid(trees, tmp_path, change):
    after_copy = {
        'native': 'touch "${HOME%/home}/protected-0"',
        'ready': 'sed -i \'s/"installing"/"ready"/\' "$HOME/.config/ods/pixel-managed.json"',
        'configured': 'mkdir -p "$HOME/.local/share/pixel"; touch "$HOME/.local/share/pixel/current"',
        'wrong-ref': '',
        'empty-ref': '',
        'config-link': 'mv "$INSTALL_DIR/config" "$HOME/owner-config"; ln -s "$HOME/owner-config" "$INSTALL_DIR/config"',
        'topology-link': 'mv "$INSTALL_DIR/config/gpu-topology.json" "$HOME/owner-topology"; ln -s "$HOME/owner-topology" "$INSTALL_DIR/config/gpu-topology.json"',
        'revoked-hold': 'sed -i \'s/"applied"/"held"/\' "$HOME/status.json"',
    }[change]
    result, _, old, _, _, _, _ = feature_copy_flow(
        trees, tmp_path, 'ready' if change == 'revoked-hold' else 'installing', 'same', True,
        after_copy, deferred_ref='b' * 40 if change == 'wrong-ref' else '' if change == 'empty-ref' else None,
        topology=True)
    assert result.returncode != 0
    assert (old / 'config/gpu-topology.json').read_text() == '{"retained":"old topology"}\n'
