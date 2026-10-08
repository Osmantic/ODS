"""Workspace-guidance migration tests.

Pixel 4.3.28 and earlier shipped a retired section in the workspace template's
AGENTS.md and a matching MEMORY.md entry. Those exact files are read from Git
history by blob id, so this file never reproduces their text; a shallow checkout
lacks the blobs and skips those cases. The in-process cases also run against a
synthetic retired block, with the module's recognition constants substituted,
so the migration machinery is exercised in every checkout.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('workspace_guidance', ROOT / 'installers/lib/pixel-workspace-guidance.py')
guidance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guidance)
TEMPLATE = ROOT / 'vendor/pixel/workspace-template'
CURRENT_AGENTS = (TEMPLATE / 'AGENTS.md').read_bytes()
CURRENT_MEMORY = (TEMPLATE / 'MEMORY.md').read_bytes()
# workspace-template blobs as shipped through Pixel 4.3.28.
SHIPPED_BLOBS = {'AGENTS.md': 'e3b876a937f371834e7d827c3641ef05d9b1809e',
                 'MEMORY.md': '358d3020fd5a6912457bebb17bf2b66da38c8548'}
SYNTHETIC_MARKER = b'Retired Example Operating Contract'
SYNTHETIC_HEADING = b'## ' + SYNTHETIC_MARKER + b' (canonical)\n'
SYNTHETIC_SECTION = SYNTHETIC_HEADING + b'\nRoute 90% of every task to the example fleet.\n\n'
SYNTHETIC_ENTRY = b'- 2026-01-01: ' + SYNTHETIC_MARKER + b' is canonical; keep a 90% example-fleet share.\n'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(object_id):
    if subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-e', object_id], capture_output=True).returncode:
        pytest.skip('the Pixel 4.3.28 template blobs need Git history; this checkout is shallow')
    return subprocess.run(['git', '-C', str(ROOT), 'cat-file', 'blob', object_id],
                          capture_output=True, check=True).stdout


def retired_defaults(kind, agents, memory):
    start = agents.index(guidance.LEGACY_HEADING)
    end = agents.index(b'\n## ', start) + 1
    line = next(line for line in memory.splitlines(keepends=True) if guidance.MARKER in line)
    return SimpleNamespace(kind=kind, agents=agents, memory=memory, start=start, end=end,
                           section=agents[start:end], memory_line=line)


@pytest.fixture
def shipped():
    """The exact defaults Pixel 4.3.28 and earlier shipped."""
    return retired_defaults('shipped', git_blob(SHIPPED_BLOBS['AGENTS.md']), git_blob(SHIPPED_BLOBS['MEMORY.md']))


@pytest.fixture(params=['shipped', 'synthetic'])
def legacy(request, monkeypatch):
    """The shipped retired defaults, or a synthetic block the module is taught to recognize."""
    if request.param == 'shipped':
        return request.getfixturevalue('shipped')
    start = CURRENT_AGENTS.index(guidance.GUIDANCE)
    agents = CURRENT_AGENTS[:start] + SYNTHETIC_SECTION + CURRENT_AGENTS[start + len(guidance.GUIDANCE):]
    monkeypatch.setattr(guidance, 'MARKER', SYNTHETIC_MARKER)
    monkeypatch.setattr(guidance, 'LEGACY_HEADING', SYNTHETIC_HEADING)
    monkeypatch.setattr(guidance, 'LEGACY_SECTION_SHA256', sha256(SYNTHETIC_SECTION))
    monkeypatch.setattr(guidance, 'LEGACY_MEMORY_SHA256', sha256(SYNTHETIC_ENTRY))
    return retired_defaults('synthetic', agents, CURRENT_MEMORY + SYNTHETIC_ENTRY)


def test_shipped_template_needs_no_migration():
    assert guidance.transform('AGENTS.md', CURRENT_AGENTS) == (CURRENT_AGENTS, 'current')
    assert guidance.transform('MEMORY.md', CURRENT_MEMORY) == (CURRENT_MEMORY, 'unchanged')
    start = CURRENT_AGENTS.index(guidance.GUIDANCE)
    assert CURRENT_AGENTS[start:CURRENT_AGENTS.index(b'\n## ', start) + 1] == guidance.GUIDANCE


def test_migrated_retired_defaults_equal_the_shipped_template(legacy):
    # A new workspace starts exactly where a migrated older workspace ends.
    assert guidance.transform('AGENTS.md', legacy.agents) == (CURRENT_AGENTS, 'migrated')
    assert guidance.transform('MEMORY.md', legacy.memory) == (CURRENT_MEMORY, 'migrated')


def test_exact_shipped_defaults_are_neutralized_without_changing_other_text(legacy):
    updated, result = guidance.transform('AGENTS.md', legacy.agents)
    assert result == 'migrated'
    assert updated == legacy.agents[:legacy.start] + guidance.GUIDANCE + legacy.agents[legacy.end:]
    updated_memory, result = guidance.transform('MEMORY.md', legacy.memory)
    assert result == 'migrated'
    assert updated_memory == legacy.memory.replace(legacy.memory_line, b'')
    assert b'Tower' not in updated and b'90%' not in updated
    assert b'cloud provider' in updated and b'no remote model' in updated


@pytest.mark.parametrize('name', ['AGENTS.md', 'MEMORY.md'])
def test_owner_prefix_suffix_and_crlf_are_preserved_exactly(legacy, name):
    original = legacy.agents if name == 'AGENTS.md' else legacy.memory
    owner = b'# Owner instructions\nKeep my spelling: caf\xc3\xa9.\n\n' + original + b'\nPersonal notes stay here.\n'
    for source in (owner, owner.replace(b'\n', b'\r\n')):
        result, status = guidance.transform(name, source)
        assert status == 'migrated'
        old = legacy.section if name == 'AGENTS.md' else legacy.memory_line
        new = guidance.GUIDANCE if name == 'AGENTS.md' else b''
        if b'\r\n' in source:
            old, new = old.replace(b'\n', b'\r\n'), new.replace(b'\n', b'\r\n')
        assert result == source.replace(old, new, 1)
        assert guidance.transform(name, result)[0] == result


EDITS = {
    'agents-edited': ('AGENTS.md', lambda d: d.agents.replace(b'90%', b'70%')),
    'agents-duplicated': ('AGENTS.md', lambda d: d.agents + d.section),
    'agents-renamed': ('AGENTS.md', lambda d: d.agents.replace(guidance.LEGACY_HEADING, b'## My fleet policy\n')),
    'agents-mixed-line-endings': ('AGENTS.md', lambda d: d.agents.replace(b'\n', b'\r\n', 1)),
    'memory-edited': ('MEMORY.md', lambda d: d.memory.replace(b'90%', b'70%')),
    'memory-duplicated': ('MEMORY.md', lambda d: d.memory + d.memory_line),
}


@pytest.mark.parametrize('edit', sorted(EDITS))
def test_edited_or_ambiguous_owner_policy_is_never_removed(legacy, edit):
    name, change = EDITS[edit]
    body = change(legacy)
    assert body != (legacy.agents if name == 'AGENTS.md' else legacy.memory)
    updated, status = guidance.transform(name, body)
    assert updated == body
    # A renamed block is no longer the shipped heading; do not reinterpret it.
    assert status in ('manual-review-required', 'unchanged')


@pytest.mark.parametrize('body', [b'# Entirely custom\n', b'', b'# My Tower2 notes\n'])
def test_custom_profiles_without_shipped_policy_are_unchanged(body):
    assert guidance.transform('AGENTS.md', body) == (body, 'unchanged')


def owner_workspace(tmp_path, agents, memory):
    if os.name != 'posix' or os.geteuid() == 0:
        pytest.skip('migration runs as the non-root POSIX workspace owner')
    root = tmp_path.resolve() / 'workspace'
    root.mkdir(mode=0o700)
    for name, data in [('AGENTS.md', agents), ('MEMORY.md', memory)]:
        (root / name).write_bytes(data)
        (root / name).chmod(0o600)
    return root


@pytest.fixture
def workspace(tmp_path, legacy):
    return owner_workspace(tmp_path, legacy.agents, legacy.memory)


@pytest.fixture
def shipped_workspace(tmp_path, shipped):
    """For another process or module instance, which recognizes only the shipped block."""
    return owner_workspace(tmp_path, shipped.agents, shipped.memory)


def backup_runs(workspace):
    root = workspace.parent / guidance.BACKUP_DIRECTORY
    return sorted(root.iterdir()) if root.exists() else []


def test_real_migration_and_noop_rerun_preserve_file_identity_and_modes(workspace):
    assert guidance.migrate_workspace(workspace) == {'AGENTS.md': 'migrated', 'MEMORY.md': 'migrated'}
    identities = {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in workspace.iterdir()}
    assert guidance.migrate_workspace(workspace) == {'AGENTS.md': 'current', 'MEMORY.md': 'unchanged'}
    assert {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in workspace.iterdir()} == identities
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in workspace.iterdir())


def test_owner_workspace_keeps_replaced_bytes_outside_the_workspace(workspace, legacy):
    guidance.migrate_workspace(workspace)
    guidance.migrate_workspace(workspace)  # A no-op rerun writes no further backup.
    runs = backup_runs(workspace)
    assert len(runs) == 1 and runs[0].name.startswith('workspace-')
    assert {p.name: p.read_bytes() for p in runs[0].iterdir()} == {'AGENTS.md': legacy.agents, 'MEMORY.md': legacy.memory}
    assert stat.S_IMODE(runs[0].parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(runs[0].stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in runs[0].iterdir())
    assert not list(workspace.glob('*' + guidance.BACKUP_DIRECTORY + '*'))


def test_group_writable_workspace_parent_still_gets_private_backups(workspace, legacy):
    workspace.parent.chmod(0o775)  # Common for directories created under umask 002.
    assert guidance.migrate_workspace(workspace) == {'AGENTS.md': 'migrated', 'MEMORY.md': 'migrated'}
    run, = backup_runs(workspace)
    assert stat.S_IMODE(run.parent.stat().st_mode) == 0o700
    assert (run / 'AGENTS.md').read_bytes() == legacy.agents


@pytest.mark.parametrize('fault', ['symlink', 'shared'])
def test_unsafe_backup_directory_prevents_all_mutation(workspace, legacy, fault):
    root = workspace.parent / guidance.BACKUP_DIRECTORY
    outside = workspace.parent / 'outside-backups'
    outside.mkdir(mode=0o700)
    if fault == 'symlink':
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        root.chmod(0o750)
    with pytest.raises((OSError, ValueError)):
        guidance.migrate_workspace(workspace)
    assert (workspace / 'AGENTS.md').read_bytes() == legacy.agents
    assert (workspace / 'MEMORY.md').read_bytes() == legacy.memory
    assert not list(outside.iterdir())


def test_failed_backup_prevents_all_mutation(workspace, legacy, monkeypatch):
    original = guidance._write_backup
    def fail_second(directory, name, body):
        if name == 'MEMORY.md':
            raise OSError('backup device full')
        original(directory, name, body)
    monkeypatch.setattr(guidance, '_write_backup', fail_second)
    with pytest.raises(OSError, match='backup device full'):
        guidance.migrate_workspace(workspace)
    assert (workspace / 'AGENTS.md').read_bytes() == legacy.agents
    assert (workspace / 'MEMORY.md').read_bytes() == legacy.memory


def test_missing_profile_is_reported_without_creating_it(workspace):
    (workspace / 'MEMORY.md').unlink()
    assert guidance.migrate_workspace(workspace) == {'AGENTS.md': 'migrated', 'MEMORY.md': 'absent'}
    assert not (workspace / 'MEMORY.md').exists()
    assert [p.name for p in backup_runs(workspace)[0].iterdir()] == ['AGENTS.md']


@pytest.mark.parametrize('fault', ['symlink', 'hardlink', 'oversized', 'writable', 'invalid-utf8', 'fifo'])
def test_unsafe_second_file_prevents_all_mutation(workspace, legacy, fault):
    target = workspace / 'MEMORY.md'
    outside = workspace.parent / 'outside'
    outside.write_bytes(legacy.memory)
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
    assert (workspace / 'AGENTS.md').read_bytes() == legacy.agents
    assert outside.read_bytes() == legacy.memory
    assert backup_runs(workspace) == []


def test_symlink_workspace_and_foreign_owner_are_refused(workspace, legacy, monkeypatch):
    link = workspace.parent / 'alias'
    link.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(OSError):
        guidance.migrate_workspace(link)
    with monkeypatch.context() as patch:
        patch.setattr(guidance.os, 'getuid', lambda: 98765)
        with pytest.raises(ValueError, match='owner workspace'):
            guidance.migrate_workspace(workspace)
    assert (workspace / 'AGENTS.md').read_bytes() == legacy.agents


def test_concurrent_owner_edit_is_not_overwritten(workspace, legacy, monkeypatch):
    original = guidance._revalidate
    edited = legacy.agents + b'\nNew owner text.\n'
    def race(directory, path, name, body, info):
        (workspace / name).write_bytes(edited)
        original(directory, path, name, body, info)
    monkeypatch.setattr(guidance, '_revalidate', race)
    with pytest.raises(ValueError, match='changed before replacement'):
        guidance.migrate_workspace(workspace)
    assert (workspace / 'AGENTS.md').read_bytes() == edited
    assert (workspace / 'MEMORY.md').read_bytes() == legacy.memory
    assert not list(workspace.glob('.ods-guidance-*'))


def test_concurrent_workspace_replacement_is_not_followed(workspace, legacy, monkeypatch):
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
    assert (saved / 'AGENTS.md').read_bytes() == legacy.agents
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


def test_native_helper_uses_same_owner_preserving_migration(shipped_workspace, shipped):
    spec = importlib.util.spec_from_file_location('native_guidance_adapter', ROOT / 'installers/macos/lib/pixel-native-config.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    (shipped_workspace / 'AGENTS.md').write_bytes(shipped.agents + b'\nMy preserved preference.\n')
    result = native.migrate_workspace_guidance(shipped_workspace)
    assert result == {'AGENTS.md': 'migrated', 'MEMORY.md': 'migrated'}
    assert (shipped_workspace / 'AGENTS.md').read_bytes().endswith(b'\nMy preserved preference.\n')
    assert guidance.MARKER not in (shipped_workspace / 'AGENTS.md').read_bytes()


def test_linux_installer_helper_executes_as_owner_without_modifying_configuration(shipped_workspace, shipped):
    (shipped_workspace / 'openclaw.json').write_bytes(b'{"owner":"unchanged"}')
    script = '''set -euo pipefail
source "$1/installers/lib/pixel-host-install.sh"
INSTALL_DIR="$1"
ods_pixel_run_as_owner() { shift 2; "$@"; }
_ods_pixel_reconcile_workspace_guidance owner "$HOME" "$2"
'''
    result = subprocess.run(['bash', '-c', script, 'guidance-test', str(ROOT), str(shipped_workspace)],
                            text=True, capture_output=True, check=True)
    assert '"AGENTS.md": "migrated"' in result.stdout
    assert 'ODS kept the replaced workspace guidance in ' + str(shipped_workspace.parent / guidance.BACKUP_DIRECTORY) in result.stderr
    assert (shipped_workspace / 'openclaw.json').read_bytes() == b'{"owner":"unchanged"}'
    assert guidance.MARKER not in (shipped_workspace / 'AGENTS.md').read_bytes()
    assert (backup_runs(shipped_workspace)[0] / 'AGENTS.md').read_bytes() == shipped.agents


@pytest.mark.parametrize('fault', ['symlink', 'writable', 'invalid-utf8', 'edited-policy'])
def test_live_installer_warns_and_continues_without_replacing_unsafe_or_custom_text(shipped_workspace, shipped, fault):
    home = shipped_workspace.parent / 'home'
    destination = home / '.openclaw/workspace-pixel'
    destination.parent.mkdir(parents=True, mode=0o700)
    shipped_workspace.rename(destination)
    target = destination / 'AGENTS.md'
    if fault == 'symlink':
        target.unlink()
        target.symlink_to(destination / 'MEMORY.md')
    elif fault == 'writable':
        target.chmod(0o666)
    elif fault == 'invalid-utf8':
        target.write_bytes(b'\xff')
    else:
        target.write_bytes(shipped.agents.replace(b'90%', b'70%'))
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
    # A generated workspace is a fresh template copy, not owner data to keep.
    assert not (generated / guidance.BACKUP_DIRECTORY).exists()


def test_generated_permission_repair_refuses_public_parent(workspace, legacy):
    generated = workspace.parent / '.generated'
    generated.mkdir(mode=0o755)
    destination = generated / 'workspace'
    workspace.rename(destination)
    destination.chmod(0o775)
    with pytest.raises(ValueError, match='private generated workspace parent'):
        guidance.migrate_workspace(destination, generated=True)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o775
    assert (destination / 'AGENTS.md').read_bytes() == legacy.agents


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
def test_generated_file_permission_repair_never_chmods_link_targets(workspace, legacy, link_kind):
    generated = workspace.parent / '.generated'
    generated.mkdir(mode=0o700)
    destination = generated / 'workspace'
    workspace.rename(destination)
    outside = workspace.parent / 'external-owner-notes'
    outside.write_bytes(legacy.agents)
    outside.chmod(0o664)
    path = destination / 'AGENTS.md'
    path.unlink()
    if link_kind == 'symlink':
        path.symlink_to(outside)
    else:
        os.link(outside, path)
    with pytest.raises((ValueError, OSError)):
        guidance.migrate_workspace(destination, generated=True)
    assert outside.read_bytes() == legacy.agents
    assert stat.S_IMODE(outside.stat().st_mode) == 0o664
