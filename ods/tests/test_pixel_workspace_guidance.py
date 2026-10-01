"""Product migration tests using the actual formerly shipped default bytes."""
import importlib.util
import os
from pathlib import Path
import stat
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('workspace_guidance', ROOT / 'installers/lib/pixel-workspace-guidance.py')
guidance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guidance)
TEMPLATE = ROOT / 'vendor/pixel/workspace-template'
AGENTS = (TEMPLATE / 'AGENTS.md').read_bytes()
MEMORY = (TEMPLATE / 'MEMORY.md').read_bytes()
START = AGENTS.index(guidance.LEGACY_HEADING)
END = AGENTS.index(b'\n## ', START) + 1
LEGACY = AGENTS[START:END]
MEMORY_LINE = next(line for line in MEMORY.splitlines(keepends=True) if guidance.MARKER in line)


def test_exact_shipped_defaults_are_neutralized_without_changing_other_text():
    updated, result = guidance.transform('AGENTS.md', AGENTS)
    assert result == 'migrated'
    assert updated == AGENTS[:START] + guidance.GUIDANCE + AGENTS[END:]
    updated_memory, result = guidance.transform('MEMORY.md', MEMORY)
    assert result == 'migrated'
    assert updated_memory == MEMORY.replace(MEMORY_LINE, b'')
    assert b'Tower' not in updated and b'90%' not in updated
    assert b'cloud provider' in updated and b'no remote model' in updated


@pytest.mark.parametrize('name,original', [('AGENTS.md', AGENTS), ('MEMORY.md', MEMORY)])
def test_owner_prefix_suffix_and_crlf_are_preserved_exactly(name, original):
    owner = b'# Owner instructions\nKeep my spelling: caf\xc3\xa9.\n\n' + original + b'\nPersonal notes stay here.\n'
    for source in (owner, owner.replace(b'\n', b'\r\n')):
        result, status = guidance.transform(name, source)
        assert status == 'migrated'
        old = LEGACY if name == 'AGENTS.md' else MEMORY_LINE
        new = guidance.GUIDANCE if name == 'AGENTS.md' else b''
        if b'\r\n' in source:
            old, new = old.replace(b'\n', b'\r\n'), new.replace(b'\n', b'\r\n')
        assert result == source.replace(old, new, 1)
        assert guidance.transform(name, result)[0] == result


@pytest.mark.parametrize('name,body', [
    ('AGENTS.md', AGENTS.replace(b'90%', b'70%')),
    ('AGENTS.md', AGENTS + LEGACY),
    ('AGENTS.md', AGENTS.replace(guidance.LEGACY_HEADING, b'## My fleet policy\n')),
    ('AGENTS.md', AGENTS.replace(b'\n', b'\r\n', 1)),
    ('MEMORY.md', MEMORY.replace(b'90%', b'70%')),
    ('MEMORY.md', MEMORY + MEMORY_LINE),
])
def test_edited_or_ambiguous_owner_policy_is_never_removed(name, body):
    updated, status = guidance.transform(name, body)
    assert updated == body
    # A renamed block is no longer the shipped heading; do not reinterpret it.
    assert status in ('manual-review-required', 'unchanged')


@pytest.mark.parametrize('body', [b'# Entirely custom\n', b'', b'# My Tower2 notes\n'])
def test_custom_profiles_without_shipped_policy_are_unchanged(body):
    assert guidance.transform('AGENTS.md', body) == (body, 'unchanged')


@pytest.fixture
def workspace(tmp_path):
    if os.name != 'posix' or os.geteuid() == 0:
        pytest.skip('migration runs as the non-root POSIX workspace owner')
    root = tmp_path.resolve() / 'workspace'
    root.mkdir(mode=0o700)
    for name, data in [('AGENTS.md', AGENTS), ('MEMORY.md', MEMORY)]:
        (root / name).write_bytes(data)
        (root / name).chmod(0o600)
    return root


def test_real_migration_and_noop_rerun_preserve_file_identity_and_modes(workspace):
    assert guidance.migrate_workspace(workspace) == {'AGENTS.md': 'migrated', 'MEMORY.md': 'migrated'}
    identities = {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in workspace.iterdir()}
    assert guidance.migrate_workspace(workspace) == {'AGENTS.md': 'current', 'MEMORY.md': 'unchanged'}
    assert {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in workspace.iterdir()} == identities
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in workspace.iterdir())


def test_missing_profile_is_reported_without_creating_it(workspace):
    (workspace / 'MEMORY.md').unlink()
    assert guidance.migrate_workspace(workspace) == {'AGENTS.md': 'migrated', 'MEMORY.md': 'absent'}
    assert not (workspace / 'MEMORY.md').exists()


@pytest.mark.parametrize('fault', ['symlink', 'hardlink', 'oversized', 'writable', 'invalid-utf8', 'fifo'])
def test_unsafe_second_file_prevents_all_mutation(workspace, fault):
    target = workspace / 'MEMORY.md'
    outside = workspace.parent / 'outside'
    outside.write_bytes(MEMORY)
    if fault in ('symlink', 'hardlink', 'fifo'):
        target.unlink()
    if fault == 'symlink':
        target.symlink_to(outside)
    elif fault == 'hardlink':
        os.link(outside, target)
    elif fault == 'oversized':
        target.write_bytes(b'x' * (guidance.MAX_BYTES + 1))
    elif fault == 'writable':
        target.chmod(0o666)
    elif fault == 'invalid-utf8':
        target.write_bytes(b'\xff')
    else:
        os.mkfifo(target)
    with pytest.raises((OSError, ValueError)):
        guidance.migrate_workspace(workspace)
    assert (workspace / 'AGENTS.md').read_bytes() == AGENTS
    assert outside.read_bytes() == MEMORY


def test_symlink_workspace_and_foreign_owner_are_refused(workspace, monkeypatch):
    link = workspace.parent / 'alias'
    link.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(OSError):
        guidance.migrate_workspace(link)
    with monkeypatch.context() as patch:
        patch.setattr(guidance.os, 'getuid', lambda: 98765)
        with pytest.raises(ValueError, match='owner workspace'):
            guidance.migrate_workspace(workspace)
    assert (workspace / 'AGENTS.md').read_bytes() == AGENTS


def test_concurrent_owner_edit_is_not_overwritten(workspace, monkeypatch):
    original = guidance._revalidate
    edited = AGENTS + b'\nNew owner text.\n'
    def race(directory, path, name, body, info):
        (workspace / name).write_bytes(edited)
        original(directory, path, name, body, info)
    monkeypatch.setattr(guidance, '_revalidate', race)
    with pytest.raises(ValueError, match='changed before replacement'):
        guidance.migrate_workspace(workspace)
    assert (workspace / 'AGENTS.md').read_bytes() == edited
    assert (workspace / 'MEMORY.md').read_bytes() == MEMORY
    assert not list(workspace.glob('.ods-guidance-*'))


def test_concurrent_workspace_replacement_is_not_followed(workspace, monkeypatch):
    original = guidance._revalidate
    saved = workspace.with_name('retired')
    def race(directory, path, name, body, info):
        workspace.rename(saved)
        workspace.mkdir(mode=0o700)
        (workspace / 'AGENTS.md').write_bytes(b'new owner workspace')
        original(directory, path, name, body, info)
    monkeypatch.setattr(guidance, '_revalidate', race)
    with pytest.raises(ValueError, match='directory changed'):
        guidance.migrate_workspace(workspace)
    assert (workspace / 'AGENTS.md').read_bytes() == b'new owner workspace'
    assert (saved / 'AGENTS.md').read_bytes() == AGENTS
    assert not list(saved.glob('.ods-guidance-*'))


def test_linux_configure_migration_precedes_plan_and_live_path_is_shared():
    source = (ROOT / 'installers/lib/pixel-host-install.sh').read_text()
    for segment in source.split('"$pixel_root/pixel" configure --answers')[1:]:
        # Every configure/plan path (ordinary install and model reconciliation)
        # hashes the guidance actually deployed, not the obsolete fleet policy.
        before_plan = segment.split('"$pixel_root/pixel" plan', 1)[0]
        assert '_ods_pixel_reconcile_workspace_guidance' in before_plan
        assert '"$pixel_root/.generated/workspace"' in before_plan
    live = source.index('_ods_pixel_migrate_live_workspace_guidance "$owner" "$home" "$pixel_log"')
    assert source.index('The exact ODS-managed Pixel contract is already active') < live
    assert source.index('# Record the verified Pixel release') > live
    assert source.index('_ods_pixel_restart_gateway_and_verify', live) > live


def test_native_generated_migration_precedes_candidate_copy():
    source = (ROOT / 'installers/macos/lib/pixel-native-config.py').read_text()
    assert source.index('migrate_workspace_guidance(checkout /') < source.index("shutil.copytree(checkout / '.generated/workspace'")


def test_native_helper_uses_same_owner_preserving_migration(workspace):
    spec = importlib.util.spec_from_file_location('native_guidance_adapter', ROOT / 'installers/macos/lib/pixel-native-config.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    (workspace / 'AGENTS.md').write_bytes(AGENTS + b'\nMy preserved preference.\n')
    result = native.migrate_workspace_guidance(workspace)
    assert result == {'AGENTS.md': 'migrated', 'MEMORY.md': 'migrated'}
    assert (workspace / 'AGENTS.md').read_bytes().endswith(b'\nMy preserved preference.\n')
    assert guidance.MARKER not in (workspace / 'AGENTS.md').read_bytes()


def test_linux_installer_helper_executes_as_owner_without_modifying_configuration(workspace):
    (workspace / 'openclaw.json').write_bytes(b'{"owner":"unchanged"}')
    script = '''set -euo pipefail
source "$1/installers/lib/pixel-host-install.sh"
INSTALL_DIR="$1"
ods_pixel_run_as_owner() { shift 2; "$@"; }
_ods_pixel_reconcile_workspace_guidance owner "$HOME" "$2"
'''
    result = subprocess.run(['bash', '-c', script, 'guidance-test', str(ROOT), str(workspace)],
                            text=True, capture_output=True, check=True)
    assert '"AGENTS.md": "migrated"' in result.stdout
    assert (workspace / 'openclaw.json').read_bytes() == b'{"owner":"unchanged"}'
    assert guidance.MARKER not in (workspace / 'AGENTS.md').read_bytes()


@pytest.mark.parametrize('fault', ['symlink', 'writable', 'invalid-utf8', 'edited-policy'])
def test_live_installer_warns_and_continues_without_replacing_unsafe_or_custom_text(workspace, fault):
    home = workspace.parent / 'home'
    destination = home / '.openclaw/workspace-pixel'
    destination.parent.mkdir(parents=True, mode=0o700)
    workspace.rename(destination)
    target = destination / 'AGENTS.md'
    if fault == 'symlink':
        target.unlink()
        target.symlink_to(destination / 'MEMORY.md')
    elif fault == 'writable':
        target.chmod(0o666)
    elif fault == 'invalid-utf8':
        target.write_bytes(b'\xff')
    else:
        target.write_bytes(AGENTS.replace(b'90%', b'70%'))
    before = target.read_bytes()
    logfile = home / 'install.log'
    script = '''set -euo pipefail
source "$1/installers/lib/pixel-host-install.sh"
INSTALL_DIR="$1"
ods_pixel_run_as_owner() { shift 2; "$@"; }
ai_warn() { printf 'WARNING: %s\n' "$1"; }
_ods_pixel_migrate_live_workspace_guidance owner "$2" "$3"
printf 'runtime reconciliation continues\n'
'''
    result = subprocess.run(['bash', '-c', script, 'guidance-test', str(ROOT), str(home), str(logfile)],
                            text=True, capture_output=True, check=True)
    assert 'WARNING: Portal preserved' in result.stdout
    assert 'runtime reconciliation continues' in result.stdout
    assert 'manual-review-required' in logfile.read_text()
    assert target.read_bytes() == before
    if fault == 'symlink':
        assert target.is_symlink()


def test_generated_copy_with_group_write_is_tightened_only_below_private_generated_parent(workspace):
    generated = workspace.parent / '.generated'
    generated.mkdir(mode=0o700)
    destination = generated / 'workspace'
    workspace.rename(destination)
    destination.chmod(0o775)  # Recursive Node cp preserves template permissions.
    with pytest.raises(ValueError, match='private owner workspace'):
        guidance.migrate_workspace(destination)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o775
    assert guidance.migrate_workspace(destination, generated=True)['AGENTS.md'] == 'migrated'
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700


def test_generated_permission_repair_refuses_public_parent(workspace):
    generated = workspace.parent / '.generated'
    generated.mkdir(mode=0o755)
    destination = generated / 'workspace'
    workspace.rename(destination)
    destination.chmod(0o775)
    with pytest.raises(ValueError, match='private generated workspace parent'):
        guidance.migrate_workspace(destination, generated=True)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o775
    assert (destination / 'AGENTS.md').read_bytes() == AGENTS


@pytest.mark.parametrize('workspace_mode', [0o700, 0o775])
def test_generated_clone_file_modes_are_tightened_through_checked_descriptors(workspace, workspace_mode):
    generated = workspace.parent / '.generated'
    generated.mkdir(mode=0o700)
    destination = generated / 'workspace'
    workspace.rename(destination)
    destination.chmod(workspace_mode)
    for name in ('AGENTS.md', 'MEMORY.md'):
        (destination / name).chmod(0o664)
    unrelated = destination / 'owner-notes.md'
    unrelated.write_bytes(b'unrelated generated content')
    unrelated.chmod(0o664)
    result = guidance.migrate_workspace(destination, generated=True)
    assert result == {'AGENTS.md': 'migrated', 'MEMORY.md': 'migrated'}
    assert all(stat.S_IMODE((destination / name).stat().st_mode) == 0o600
               for name in ('AGENTS.md', 'MEMORY.md'))
    assert stat.S_IMODE(unrelated.stat().st_mode) == 0o664


@pytest.mark.parametrize('link_kind', ['symlink', 'hardlink'])
def test_generated_file_permission_repair_never_chmods_link_targets(workspace, link_kind):
    generated = workspace.parent / '.generated'
    generated.mkdir(mode=0o700)
    destination = generated / 'workspace'
    workspace.rename(destination)
    outside = workspace.parent / 'external-owner-notes'
    outside.write_bytes(AGENTS)
    outside.chmod(0o664)
    path = destination / 'AGENTS.md'
    path.unlink()
    if link_kind == 'symlink':
        path.symlink_to(outside)
    else:
        os.link(outside, path)
    with pytest.raises((ValueError, OSError)):
        guidance.migrate_workspace(destination, generated=True)
    assert outside.read_bytes() == AGENTS
    assert stat.S_IMODE(outside.stat().st_mode) == 0o664
