"""Source upgrade guards use actual native identity validators and preserve no-mutation ordering."""
import importlib.util
import json
from pathlib import Path
import subprocess
import types
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_update_preflight_test', ROOT / 'scripts/source-update-preflight.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

@pytest.fixture
def linux_identity(tmp_path, monkeypatch):
    import pwd
    install = tmp_path / 'install'
    install.mkdir()
    home = tmp_path / 'owner'
    marker = home / '.config/ods/pixel-managed.json'
    marker.parent.mkdir(parents=True)
    real_load = guard.load_helper
    agent = real_load(ROOT / 'bin/ods-host-agent.py', 'source_guard_real_agent')
    def trusted_load(path, name):
        assert path.is_relative_to(ROOT)
        if path == ROOT / 'bin/ods-host-agent.py':
            return agent
        return real_load(path, name)
    monkeypatch.setattr(guard, 'load_helper', trusted_load)
    monkeypatch.setattr(guard.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(pwd, 'getpwuid', lambda uid: types.SimpleNamespace(pw_name='fixture-owner', pw_dir=str(home)))
    record = {'manager': 'ods', 'schema_version': 2, 'state': 'ready', 'install_dir': str(install)}
    return install, marker, record


def save_marker(marker, record):
    marker.write_text(json.dumps(record))
    marker.chmod(0o600)

@pytest.mark.parametrize('state', ['ready', 'installing'])
def test_same_install_native_refused(linux_identity, state):
    install, marker, record = linux_identity
    record['state'] = state
    save_marker(marker, record)
    with pytest.raises((ValueError, RuntimeError)):
        guard.check_native(install)

@pytest.mark.parametrize('damage', ['symlink', 'public', 'malformed', 'hardlink'])
def test_unsafe_native_marker_refused(linux_identity, damage):
    install, marker, record = linux_identity
    save_marker(marker, record)
    if damage == 'symlink':
        target = marker.with_name('target')
        marker.rename(target)
        marker.symlink_to(target)
    elif damage == 'public':
        marker.chmod(0o644)
    elif damage == 'malformed':
        marker.write_text('{')
    else:
        marker.with_name('second').hardlink_to(marker)
    with pytest.raises(RuntimeError):
        guard.check_native(install)


def test_foreign_or_absent_native_not_adopted(linux_identity):
    install, marker, record = linux_identity
    guard.check_native(install)
    record['install_dir'] = str(install.parent / 'foreign')
    save_marker(marker, record)
    guard.check_native(install)


def test_root_ordinary_install_supported(tmp_path, monkeypatch):
    import pwd
    monkeypatch.setattr(guard.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(guard.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(pwd, 'getpwall', lambda: [types.SimpleNamespace(pw_uid=0, pw_dir=str(tmp_path / 'root'))])
    guard.check_native(tmp_path)


@pytest.mark.parametrize('state', ['ready', 'installing', 'foreign', 'absent', 'unsafe', 'symlink', 'hardlink'])
def test_root_inspects_all_owner_receipts(linux_identity, monkeypatch, state):
    import os
    import pwd
    install, marker, record = linux_identity
    uid = os.geteuid()
    monkeypatch.setattr(guard.os, 'geteuid', lambda: 0)
    # Neither HOME nor SUDO_USER is consulted; the receipt may belong to another
    # account from the caller and installation-directory owner.
    accounts = [types.SimpleNamespace(pw_uid=0, pw_dir=str(install.parent / 'root')),
                types.SimpleNamespace(pw_uid=uid, pw_dir=str(marker.parents[2]))]
    monkeypatch.setattr(pwd, 'getpwall', lambda: accounts)
    if state == 'foreign':
        record['install_dir'] = str(install.parent / 'foreign')
    elif state == 'installing':
        record['state'] = state
    if state != 'absent':
        save_marker(marker, record)
    if state == 'unsafe':
        marker.chmod(0o644)
    elif state == 'symlink':
        target = marker.with_name('real')
        marker.rename(target)
        marker.symlink_to(target)
    elif state == 'hardlink':
        marker.with_name('second').hardlink_to(marker)
    if state in {'foreign', 'absent'}:
        guard.check_native(install)
    else:
        with pytest.raises((ValueError, OSError), match='Pixel|symbolic'):
            guard.check_native(install)


def test_root_cannot_infer_absence_from_failed_account_enumeration(tmp_path, monkeypatch):
    import pwd
    monkeypatch.setattr(guard.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(guard.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(pwd, 'getpwall', lambda: [])
    with pytest.raises(ValueError, match='enumerate'):
        guard.check_native(tmp_path)


def test_root_system_accounts_without_pixel_home_do_not_block(tmp_path, monkeypatch):
    import pwd
    monkeypatch.setattr(guard.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(guard.os, 'geteuid', lambda: 0)
    home_file = tmp_path / 'home-file'
    home_file.write_text('system account')
    monkeypatch.setattr(pwd, 'getpwall', lambda: [types.SimpleNamespace(pw_uid=1, pw_dir=p)
                        for p in ('/', '/nonexistent', str(home_file))])
    guard.check_native(tmp_path)

@pytest.mark.parametrize('build', ['.', {'context': '.', 'dockerfile': 'Dockerfile'}, {}])
def test_source_built_compose_refused(build):
    with pytest.raises(ValueError, match='source-built'):
        guard.check_compose({'services': {'app': {'image': 'example/app:v1', 'build': build}}})

@pytest.mark.parametrize('document', [None, {}, {'services': {}}, {'services': {'app': None}}])
def test_unknown_compose_refused(document):
    with pytest.raises(ValueError):
        guard.check_compose(document)


def test_image_only_compose_supported():
    guard.check_compose({'services': {'app': {'image': 'example/app@sha256:' + 'a' * 64}}})

@pytest.mark.parametrize('mode', ['native-denied', 'build-denied', 'compose-v1'])
def test_actual_updater_refuses_before_snapshot_or_git(tmp_path, mode):
    # Invoke the real updater function with synthetic command boundaries. No git,
    # Docker, native service, owner credentials or installed state is touched.
    install = tmp_path / 'install'
    install.mkdir()
    (install / 'scripts').mkdir()
    (install / 'scripts/source-update-preflight.py').write_text('')
    log = tmp_path / 'effects'
    program = r'''
set -euo pipefail
source <(sed '$d' "$SOURCE")
INSTALL_DIR="$FIXTURE"; VERSION_FILE="$FIXTURE/.version"
log_info(){ :; };log_ok(){ :; };log_warn(){ :; };log_error(){ :; }
get_current_version(){ echo 2.6.0; };ensure_source_checkout_for_update(){ return 0; }
resolve_compose_flags(){ echo '-f compose.yaml'; }
snapshot_pre_update(){ echo snapshot >> "$EFFECTS";echo /unused; }
git(){ echo git >> "$EFFECTS"; }
python3(){
 if [[ $2 == native ]];then [[ $MODE != native-denied ]];return;fi
 command python3 "$GUARD" compose
}
docker(){
 [[ $MODE != compose-v1 ]] || return 1
 printf '%s\n' '{"services":{"app":{"build":".","image":"app:v1"}}}'
}
if cmd_update;then exit 91;fi
[[ ! -e "$EFFECTS" ]]
'''
    import os
    env = {**os.environ, 'SOURCE': str(ROOT / 'ods-update.sh'), 'FIXTURE': str(install), 'MODE': mode,
           'EFFECTS': str(log), 'GUARD': str(ROOT / 'scripts/source-update-preflight.py')}
    result = subprocess.run(['bash', '-c', program], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not log.exists()


def test_image_only_cli_does_not_call_source_guard():
    # Existing runtime CLI path is independently tested by its functional suite.
    text = (ROOT / 'ods-cli').read_text()
    body = text.split('cmd_update() {', 1)[1].split('\n}\n', 1)[0]
    assert 'source-update-preflight' not in body
    assert '_ods_cli_pull_external_images' in body

@pytest.mark.parametrize('state', ['ready', 'incomplete', 'partial', 'symlink'])
def test_actual_macos_selection_guard(tmp_path, monkeypatch, state):
    monkeypatch.setattr(guard.platform, 'system', lambda: 'Darwin')
    root = tmp_path / 'install'
    root.mkdir()
    stack = guard.load_helper(ROOT / 'installers/macos/lib/pixel-native-stack.py', 'guard_actual_mac_stack')
    p = root / 'data/pixel-native/preparation'
    p.mkdir(parents=True)
    prepared = {'status': 'prepared', 'phase': 'awaiting-protected-activation',
                'home': str(root / 'data/pixel-native/home'), 'runtimeDigest': 'a' * 64, 'serviceDigest': 'b' * 64}
    (p / 'preparation.json').write_text(json.dumps(prepared))
    if state != 'partial':
        activation = {'status': 'ready' if state != 'incomplete' else 'pending', 'phase': 'services-ready',
                      'runtimeDigest': 'a' * 64, 'serviceDigest': 'b' * 64}
        (p / 'activation.json').write_text(json.dumps(activation))
    if state == 'symlink':
        (p / 'activation.json').rename(p / 'saved.json')
        (p / 'activation.json').symlink_to(p / 'saved.json')
    for fragment in stack.installer.FRAGMENTS:
        f = root / fragment
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('services: {}\n')
    if state in ('ready', 'partial'):
        assert guard.managed_pixel_identity(root) == 'macos'
    with pytest.raises((ValueError, OSError), match='native|Source-only|symbolic'):
        guard.check_native(root)


def test_incomplete_macos_native_directory_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(guard.platform, 'system', lambda: 'Darwin')
    (tmp_path / 'data/pixel-native').mkdir(parents=True)
    with pytest.raises(ValueError, match='without a complete selection'):
        guard.managed_pixel_identity(tmp_path)


def test_other_directory_owner_refused(linux_identity, monkeypatch):
    install, _, _ = linux_identity
    original = Path.stat
    monkeypatch.setattr(Path, 'stat', lambda self, *a, **k: types.SimpleNamespace(st_uid=guard.os.geteuid()+1)
                        if self == install else original(self, *a, **k))
    with pytest.raises(ValueError, match='directory owner'):
        guard.managed_pixel_identity(install)


def test_preflight_uses_target_compose_directory(tmp_path):
    install = tmp_path / 'install'
    install.mkdir()
    (install / 'scripts').mkdir()
    (install / 'scripts/source-update-preflight.py').write_text('')
    caller = tmp_path / 'caller'
    caller.mkdir()
    (install / 'compose.yaml').write_text('target')
    (caller / 'compose.yaml').write_text('unrelated')
    program = r'''
set -euo pipefail
source <(sed '$d' "$SOURCE")
INSTALL_DIR="$FIXTURE";VERSION_FILE="$FIXTURE/.version"
log_info(){ :; };log_ok(){ :; };log_warn(){ :; };log_error(){ :; }
get_current_version(){ echo 2.6.0; };ensure_source_checkout_for_update(){ return 0; }
resolve_compose_flags(){ echo '-f compose.yaml'; }
snapshot_pre_update(){ touch "$REACHED";echo /unused; }
python3(){ if [[ $2 == native ]];then return 0;fi;command python3 "$GUARD" compose; }
docker(){
 if [[ $* == *'config --format json'* ]];then
   [[ $PWD == "$FIXTURE" && $(cat compose.yaml) == target ]] || return 81
   echo '{"services":{"app":{"image":"app:v1"}}}'
 fi
}
git(){ case "$1" in branch) echo main;;describe) echo v2.6.0;;esac; }
wait_for_healthy(){ return 0; }
cmd_update
[[ -f "$REACHED" ]]
'''
    import os
    env = {**os.environ, 'SOURCE': str(ROOT / 'ods-update.sh'), 'FIXTURE': str(install),
           'REACHED': str(tmp_path / 'snapshot'), 'GUARD': str(ROOT / 'scripts/source-update-preflight.py')}
    result = subprocess.run(['bash', '-c', program], cwd=caller, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('selection', ['PIXEL_AGENT_MODE=pixel', 'PIXEL_NATIVE_UID=1000',
                                       'PIXEL_NATIVE_CONFIG_PATH=/owned/config', 'PIXEL_NATIVE_WORKSPACE=/owned/workspace'])
def test_selected_native_without_marker_refused(linux_identity, selection):
    install, _, _ = linux_identity
    (install / '.env').write_text(selection + '\n')
    with pytest.raises(ValueError, match='selected'):
        guard.managed_pixel_identity(install)


@pytest.mark.parametrize('shape', ['nonempty', 'dangling', 'file'])
def test_remaining_linux_native_footprint_refused(linux_identity, shape):
    install, _, _ = linux_identity
    path = install / 'data/pixel'
    path.parent.mkdir()
    if shape == 'nonempty':
        path.mkdir()
        (path / 'managed-receipt.json').write_text('{}')
    elif shape == 'dangling':
        path.symlink_to(install / 'missing')
    else:
        path.write_text('incomplete')
    with pytest.raises(ValueError, match='files remain'):
        guard.managed_pixel_identity(install)


def test_empty_disabled_linux_footprint_unselected(linux_identity):
    install, _, _ = linux_identity
    (install / 'data/pixel').mkdir(parents=True)
    (install / '.env').write_text('ENABLE_PIXEL=false\nPIXEL_AGENT_MODE=hermes\n')
    assert guard.managed_pixel_identity(install) is None


def test_untrusted_target_validator_never_imported(linux_identity):
    install, _, _ = linux_identity
    (install / 'bin').mkdir()
    (install / 'bin/ods-host-agent.py').write_text('raise AssertionError("target code executed")')
    assert guard.managed_pixel_identity(install) is None


@pytest.mark.parametrize('value', ['pixel # selected', '"pixel" # selected', "'pixel' # selected"])
def test_native_env_comments_use_actual_parser(linux_identity, value):
    install, _, _ = linux_identity
    (install / '.env').write_text('PIXEL_AGENT_MODE=' + value + '\n')
    with pytest.raises(ValueError, match='selected'):
        guard.managed_pixel_identity(install)


@pytest.mark.parametrize('value', ['hermes # disabled', '"hermes" # disabled', "'hermes' # disabled", 'false', ''])
def test_disabled_env_comments_are_not_native(linux_identity, value):
    install, _, _ = linux_identity
    (install / '.env').write_text('PIXEL_AGENT_MODE=' + value + '\nPIXEL_NATIVE_WORKSPACE="" # absent\n')
    assert guard.managed_pixel_identity(install) is None


def test_export_native_assignment_is_ambiguous_not_absent(linux_identity):
    install, _, _ = linux_identity
    (install / '.env').write_text('export PIXEL_AGENT_MODE=pixel\n')
    with pytest.raises(ValueError, match='Unsupported export syntax'):
        guard.managed_pixel_identity(install)
