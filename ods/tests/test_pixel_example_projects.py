"""Elf of Doom ships as an example Portal project in every Portal workspace.

The installers add it to Playground/elf-of-doom as the workspace owner whenever
nothing of that name exists and the workspace has never received it, with the
exact bytes Portal wrote and https://odsspark.com/game/ serves. A deleted or
renamed example is never added again. Portal's sidebar then lists it like any
other project; no installer prints a hint about it. These tests pin those
bytes, the create-only copy, the record of received examples (kept in Portal's
state folder, outside the workspace the agent's sandbox mounts), and where
each installer runs it.

The copy and installer-step cases need POSIX descriptors and a non-root owner,
and run in CI on Linux and macOS. On Windows only the shipped-file and source
checks run: there `bash` would start WSL, so no shell case runs.
"""
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('example_projects', ROOT / 'installers/lib/pixel-example-projects.py')
examples = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(examples)
SOURCE = ROOT / 'extensions/services/pixel-agent/example-projects'
PHASE = ROOT / 'installers/phases/11-services.sh'
MACOS_INSTALLER = ROOT / 'installers/macos/install-macos.sh'
# The game files as Portal wrote them and https://odsspark.com/game/ serves
# them. This second pin means changing the game takes two deliberate edits.
PUBLISHED = {
    'chars.js': '161b08c8a331fef5f09ac45e6ade2cff62e03316ba7c6ac197adf8147af629db',
    'dungeon.js': '80dfc2e0517cb89bce4f445d75e6a85a4a9c2317893a6762d87666b5b136004c',
    'engine.js': '2f573a818c04b23554a95426bbe7a8a367780c9c583e03488fbe5e66e19dbedf',
    'index.html': 'af1c8b2b0a9f4e3ec4f5c6389078cf0b3a187a53badd451006ce24b737fd9906',
    'legacy.js': '0d33ded8670f4113f47baba1325ed52247305cbb9a0c92271aa7c8ef0c498165',
    'style.css': 'e063614db72fbc5c607bd4c79d4e2e967a0e5deceb3962f0a50f045e55197ee9',
    'ui.js': '5d0138ba4517d812359d4dd474f6febccf4dc703870412234a092cf8a04ef7f1',
    'world.js': 'b45864ccd2bed3d9413e571a167683f8b44cba29559bbee856a9f4d56ff8aff5',
}
WARNING = 'WARN: Portal is ready, but ODS could not add its Elf of Doom example project: '
# Not a Discord invite: tests/test-help-links.sh rejects any other invite.
HELP_URL = 'https://help.invalid/ods'
POSIX_OWNER = pytest.mark.skipif(os.name != 'posix' or os.geteuid() == 0,
                                 reason='the copy runs as a non-root POSIX workspace owner')


def shell_function(path, name):
    text = path.read_text(encoding='utf-8')
    start = text.index('\n' + name + '() {\n') + 1
    return text[start:text.index('\n}\n', start) + 3]


def snapshot(path):
    """lstat identity and content of path and of everything below it."""
    entries = {}

    def record(item):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode):
            content = os.readlink(item)
        elif stat.S_ISREG(info.st_mode):
            content = item.read_bytes()
        else:
            content = None
        entries[item.relative_to(path.parent).as_posix()] = (
            info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_mtime_ns, content)

    record(path)
    if path.is_dir() and not path.is_symlink():
        for current, directories, names in os.walk(path):
            for name in directories + names:
                record(Path(current) / name)
    return entries


def source_copy(tmp_path):
    source = tmp_path.resolve() / 'source'
    shutil.copytree(SOURCE, source)
    return source


def staging_folder(playground, files, age=2 * examples.STALE_STAGING_SECONDS):
    """A staging folder as an interrupted earlier copy leaves it, age seconds ago."""
    folder = playground / (examples.STAGING_PREFIX + 'a1b2c3d4e5f60718')
    folder.mkdir(mode=0o700)
    for name, body in files.items():
        (folder / name).write_bytes(body)
    then = time.time() - age
    os.utime(folder, (then, then))
    return folder


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path.resolve() / 'home/.openclaw/workspace-pixel'
    root.mkdir(parents=True)
    root.chmod(0o700)
    root.parent.chmod(0o700)
    return root


def state_of(workspace):
    """Portal's state folder: the workspace's parent in every ODS layout."""
    return workspace.parent


def add(workspace, **kwargs):
    return examples.add_projects(workspace, state_of(workspace), **kwargs)


def record_path(workspace):
    return state_of(workspace) / examples.RECORD_DIRECTORY / examples.record_name(workspace)


def record_bytes(workspace, received, added):
    return ('{"schemaVersion":1,"workspace":' + json.dumps(str(workspace)) + ',"received":'
            + json.dumps(received, separators=(',', ':')) + ',"added":'
            + json.dumps(added, separators=(',', ':')) + '}\n').encode()


def write_record(workspace, body):
    folder = state_of(workspace) / examples.RECORD_DIRECTORY
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / examples.record_name(workspace)
    path.write_bytes(body)
    path.chmod(0o600)
    return path


# ------------------------------------------------------------ shipped files --

def test_shipped_files_are_exactly_the_pinned_bytes():
    assert sorted(path.name for path in SOURCE.iterdir()) == sorted(examples.EXAMPLES)
    for name, expected in examples.EXAMPLES.items():
        directory = SOURCE / name
        assert sorted(path.name for path in directory.iterdir()) == sorted(expected)
        for filename, digest in expected.items():
            body = (directory / filename).read_bytes()
            assert hashlib.sha256(body).hexdigest() == digest, filename
            assert b'\r' not in body, filename
            assert len(body) <= examples.MAX_BYTES, filename
    game = {name: digest for name, digest in examples.EXAMPLES['elf-of-doom'].items() if name != 'README.md'}
    assert game == PUBLISHED
    assert examples.PINNED_NAMES == frozenset(examples.EXAMPLES['elf-of-doom'])


def test_readme_says_where_it_is_how_to_play_and_how_it_was_made():
    body = (SOURCE / 'elf-of-doom/README.md').read_bytes()
    text = body.decode('utf-8')
    prose = ' '.join(text.split())
    assert body.endswith(b'\n') and not body.endswith(b'\n\n')
    assert "In Portal's sidebar, open Projects, then Playground, then elf-of-doom." in prose
    assert '~/.openclaw/workspace-pixel/Playground/elf-of-doom' in text
    assert 'data/pixel-native/home/.openclaw/workspace-pixel/Playground/elf-of-doom' in text
    # The Windows installer accepts more than one WSL distribution.
    assert 'Windows: the same path inside the WSL Linux environment ODS runs in' in prose
    assert 'Ubuntu' not in text and 'Debian' not in text
    assert ('ODS updates never overwrite or delete this folder. To delete or rename it, ask Portal the same way, '
            'for example "In elf-of-doom, delete this game"; ODS then does not add it again.') in prose
    assert 'ODS never overwrites' not in prose and 'fresh copy' not in prose
    # The rule the plugin's guard enforces (plugin/example-guard.mjs), in plain words.
    assert ('Play it, or ask Portal to change it: start your message with "In elf-of-doom,", for example '
            '"In elf-of-doom, make the arrows give 7". Portal changes Elf of Doom only then. Any other change to '
            'this folder, even one you make by hand, is put back when Portal next works on something else.') in prose
    assert 'later requests in that chat' not in prose and 'best-effort' not in prose
    assert 'Qwen3.8-Flash-Next' in text
    assert 'See how it was made: https://odsspark.com\n' in text
    # The folder is read inside a workspace, where a relative link resolves to nothing.
    assert all(re.match(r'[a-z]+://', target) for target in re.findall(r'\]\(([^)]+)\)', text))


def test_example_names_are_portal_project_names():
    for name in examples.EXAMPLES:
        assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', name)
        assert not name.startswith(examples.STAGING_PREFIX)


def test_staging_is_hidden_from_portal_preview_and_routing():
    # Portal's preview and project routing skip names that start with '.'.
    assert examples.STAGING_PREFIX.startswith('.')
    assert examples.STAGING_NAME.fullmatch(examples.STAGING_PREFIX + 'a1b2c3d4e5f60718')
    assert not examples.STAGING_NAME.fullmatch(examples.STAGING_PREFIX + 'mine')


def test_the_privacy_guard_covers_the_shipped_examples():
    spec = importlib.util.spec_from_file_location('example_privacy_guard', ROOT / 'tests/test-seeded-content-privacy.py')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    assert 'extensions/services/pixel-agent/example-projects' in guard.SEEDED
    for path in sorted(SOURCE.rglob('*')):
        if path.is_file():
            assert list(guard.findings(path.read_text(encoding='utf-8'))) == [], path


def test_git_keeps_the_shipped_bytes():
    rules = (ROOT.parent / '.gitattributes').read_text(encoding='utf-8').splitlines()
    assert 'ods/extensions/services/pixel-agent/example-projects/** -text -whitespace' in rules


def test_no_installer_or_summary_advertises_the_game():
    # The game is simply there, in Portal's sidebar, like any other project.
    for path in (PHASE, ROOT / 'installers/phases/13-summary.sh', MACOS_INSTALLER,
                 ROOT / 'installers/macos/lib/pixel-native-update.py'):
        text = path.read_text(encoding='utf-8')
        assert 'Portal has a game to try' not in text, path
        assert 'ODS_EXAMPLE_PROJECT_ADDED' not in text, path
        assert 'Open the existing Playground/elf-of-doom' not in text, path
    summary = (ROOT / 'installers/phases/13-summary.sh').read_text(encoding='utf-8')
    assert 'elf-of-doom' not in summary.lower() and 'elf of doom' not in summary.lower()
    for body in (shell_function(PHASE, '_phase11_add_example_projects'),
                 shell_function(MACOS_INSTALLER, '_macos_add_example_projects')):
        assert 'ai_ok' not in body and 'echo' not in body
        # log() also prints in the plain display; the outcome goes to the file only.
        assert not re.search(r'^\s*(?:\d\) )?log ', body, re.M) and 'tee' not in body


def test_docstrings_say_when_the_example_is_added():
    for path in (ROOT / 'installers/lib/pixel-example-projects.py', ROOT / 'installers/macos/lib/pixel-native-update.py',
                 PHASE, MACOS_INSTALLER):
        prose = ' '.join(path.read_text(encoding='utf-8').replace('#', ' ').split())
        assert 'whenever Playground/elf-of-doom is absent and this workspace has never received it' in prose, path
        assert 'copied once' not in prose and 'workspace once' not in prose, path
        assert 'tries again' not in prose and 'adds a fresh copy' not in prose, path


def test_the_record_lives_outside_the_workspace_where_the_plugin_reads_it():
    # Hidden, never a staging name, and in Portal's state folder: the agent's
    # sandbox mounts only the workspace, so no model tool can rewrite it.
    assert examples.RECORD_DIRECTORY.startswith('.') and not examples.STAGING_NAME.fullmatch(examples.RECORD_DIRECTORY)
    plugin = (ROOT / 'extensions/services/pixel-agent/plugin/example-projects.mjs').read_text(encoding='utf-8')
    assert f"export const EXAMPLE_RECORD_DIRECTORY = '{examples.RECORD_DIRECTORY}';" in plugin
    assert "process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), '.openclaw')" in plugin
    assert "createHash('sha256').update(root).digest('hex')}.json" in plugin
    assert examples.record_name('/home/owner/.openclaw/workspace-pixel') == (
        hashlib.sha256(b'/home/owner/.openclaw/workspace-pixel').hexdigest() + '.json')


def test_each_cause_names_what_stopped_the_copy_and_how_to_recover():
    # The installers print this sentence as the warning's cause.
    assert examples._cause(examples.LayoutRefused('the Portal workspace /w is not a private folder')) == (
        'ODS does not write into this workspace layout: the Portal workspace /w is not a private folder.')
    for error in (ValueError('example source README.md could not be read (No such file or directory)'),
                  ValueError('example source ui.js must be a regular file'),
                  ValueError('example source index.html does not match the shipped bytes')):
        assert examples._cause(error) == ("ODS's own copy of the game files is damaged or incomplete. "
                                          'Download ODS again, then rerun the installer.')
    assert examples._cause(FileNotFoundError(errno.ENOENT, 'No such file or directory', '/w')) == (
        'the Portal folder /w was not found. Rerun the installer so it sets Portal up again.')
    assert examples._cause(OSError(errno.ENOSPC, 'No space left on device')) == (
        'a file could not be written in the Portal workspace (No space left on device). '
        "Free some disk space or fix that folder's permissions, then rerun the installer.")
    assert examples._cause(ValueError('add example projects as the non-root workspace owner')) == (
        'the copy stopped (add example projects as the non-root workspace owner).')


# ------------------------------------------------------- installer wiring --

def test_linux_step_runs_after_portal_is_verified_and_before_the_stack_starts():
    lines = PHASE.read_text(encoding='utf-8').splitlines()
    call = lines.index('    _phase11_add_example_projects')
    dry_run = lines.index('if $DRY_RUN; then')
    assert lines.index('_phase11_add_example_projects() {') < dry_run < lines.index('else', dry_run) < call
    assert lines.index('    if ! ods_pixel_install_default_agent; then') < call
    # Outside the gateway-refresh boundary that test-phase11-litellm-reload.sh executes.
    assert lines[call - 1] == '    _phase11_write_compose_launch_record'
    assert any('up -d --remove-orphans' in line for line in lines[call + 1:call + 4])
    body = shell_function(PHASE, '_phase11_add_example_projects')
    assert body.splitlines()[1] == '    [[ "${ENABLE_PIXEL_RUNTIME:-false}" == true ]] || return 0'
    assert 'ods_pixel_run_as_owner "$owner" "$home" /usr/bin/python3 -B' in body
    assert '--workspace "$home/.openclaw/workspace-pixel" --state-dir "$home/.openclaw"' in body
    assert 'ai_warn "Portal is ready' in body and '${ODS_HELP_DISCORD_URL}' in body
    assert '|| true' not in body and body.endswith('    return 0\n}\n')


def test_macos_step_runs_after_native_activation():
    script = MACOS_INSTALLER.read_text(encoding='utf-8')
    save = '    echo "${COMPOSE_FLAGS[*]}" > "${INSTALL_DIR}/.compose-flags"\n'
    guarded = save + '    if $ENABLE_PIXEL; then\n        _macos_add_example_projects\n    fi\n'
    assert script.count('_macos_add_example_projects\n') == 1
    assert script.index(guarded) == script.rindex(save)
    assert script.index('ai_ok "Native Pixel activated for Dashboard/Portal"') < script.index(guarded)
    body = shell_function(MACOS_INSTALLER, '_macos_add_example_projects')
    assert 'install_root="$(cd "$INSTALL_DIR" && pwd -P)"' in body
    assert '--workspace "$install_root/data/pixel-native/home/.openclaw/workspace-pixel"' in body
    assert '--state-dir "$install_root/data/pixel-native/home/.openclaw"' in body
    assert 'ai_warn "Portal is ready' in body and '${ODS_HELP_DISCORD_URL}' in body
    assert '|| true' not in body and body.endswith('    return 0\n}\n')


def test_macos_update_adds_examples_after_the_proved_update():
    source = (ROOT / 'installers/macos/lib/pixel-native-update.py').read_text(encoding='utf-8')
    finalize = source.index("helper('pixel-native-finalize').finalize_update(preparation)")
    identity = source.index("outcome['portalIdentityMigration'] = migrate_public_identity(")
    # Logged privately, never part of the outcome the update prints, and the
    # helper's own notes are captured instead of reaching the terminal.
    added = source.index("log_example_projects(work, add_example_projects(preparation=preparation,\n"
                         "            state_dir=install_dir / 'data/pixel-native/home/.openclaw'))")
    assert finalize < identity < added
    assert "outcome['exampleProjects']" not in source
    assert 'contextlib.redirect_stdout(notes), contextlib.redirect_stderr(notes)' in source


# ------------------------------------------------------- create-only copy --

@POSIX_OWNER
def test_fresh_workspace_gets_a_private_exact_copy(workspace):
    assert add(workspace) == {'elf-of-doom': 'added'}
    playground = workspace / 'Playground'
    project = playground / 'elf-of-doom'
    for directory in (playground, project, state_of(workspace) / examples.RECORD_DIRECTORY):
        info = directory.lstat()
        assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
        assert info.st_uid == os.getuid()
    assert sorted(path.name for path in project.iterdir()) == sorted(examples.EXAMPLES['elf-of-doom'])
    for name in examples.EXAMPLES['elf-of-doom']:
        info = (project / name).lstat()
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_nlink == 1 and info.st_uid == os.getuid()
        assert (project / name).read_bytes() == (SOURCE / 'elf-of-doom' / name).read_bytes()
    assert [path.name for path in playground.iterdir()] == ['elf-of-doom']
    record = record_path(workspace)
    info = record.lstat()
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid()
    assert record.read_bytes() == record_bytes(workspace, ['elf-of-doom'], ['elf-of-doom'])
    # Nothing but the project in the workspace: the record is outside it.
    assert [path.name for path in workspace.iterdir()] == ['Playground']
    assert [path.name for path in record.parent.iterdir()] == [record.name]
    before = snapshot(project)
    assert add(workspace) == {'elf-of-doom': 'previously-received'}
    assert snapshot(project) == before
    assert [path.name for path in playground.iterdir()] == ['elf-of-doom']


@POSIX_OWNER
@pytest.mark.parametrize('change', ['deleted', 'renamed'])
def test_a_deleted_or_renamed_example_is_never_added_again(workspace, change):
    assert add(workspace) == {'elf-of-doom': 'added'}
    playground = workspace / 'Playground'
    if change == 'deleted':
        shutil.rmtree(playground / 'elf-of-doom')
    else:
        (playground / 'elf-of-doom').rename(playground / 'my-dungeon')
    before = snapshot(state_of(workspace))
    assert add(workspace) == {'elf-of-doom': 'previously-received'}
    assert snapshot(state_of(workspace)) == before
    assert not (playground / 'elf-of-doom').exists()


@POSIX_OWNER
def test_each_workspace_has_its_own_record(workspace, tmp_path):
    other = state_of(workspace) / 'workspace-other'
    other.mkdir(mode=0o700)
    assert add(workspace) == {'elf-of-doom': 'added'}
    shutil.rmtree(workspace / 'Playground/elf-of-doom')
    assert examples.add_projects(other, state_of(workspace)) == {'elf-of-doom': 'added'}
    assert add(workspace) == {'elf-of-doom': 'previously-received'}
    assert sorted(path.name for path in (state_of(workspace) / examples.RECORD_DIRECTORY).iterdir()) == sorted(
        [examples.record_name(workspace), examples.record_name(other)])


@POSIX_OWNER
def test_an_owner_entry_of_that_name_is_recorded_so_it_is_never_replaced_later(workspace):
    (workspace / 'Playground').mkdir(mode=0o700)
    (workspace / 'Playground/elf-of-doom').mkdir()
    assert add(workspace) == {'elf-of-doom': 'present'}
    # Received, but never added: Portal keeps treating it as the owner's own.
    assert record_path(workspace).read_bytes() == record_bytes(workspace, ['elf-of-doom'], [])
    (workspace / 'Playground/elf-of-doom').rmdir()
    assert add(workspace) == {'elf-of-doom': 'previously-received'}
    assert list((workspace / 'Playground').iterdir()) == []


@POSIX_OWNER
def test_other_received_names_are_kept_in_the_record(workspace):
    write_record(workspace, record_bytes(workspace, ['future-example', 'owner-example'], ['future-example']))
    assert add(workspace) == {'elf-of-doom': 'added'}
    assert record_path(workspace).read_bytes() == record_bytes(
        workspace, ['elf-of-doom', 'future-example', 'owner-example'], ['elf-of-doom', 'future-example'])


@POSIX_OWNER
@pytest.mark.parametrize('kind', ['not-json', 'wrong-shape', 'boolean-version', 'no-added', 'added-not-received',
                                  'extra-key', 'other-workspace', 'too-large', 'folder', 'link', 'hard-link',
                                  'linked-records', 'shared-records', 'file-for-records'])
def test_an_unusable_record_adds_nothing_and_is_left_alone(workspace, tmp_path, kind):
    outside = tmp_path.resolve() / 'outside.json'
    outside.write_bytes(record_bytes(workspace, [], []))
    folder = state_of(workspace) / examples.RECORD_DIRECTORY
    bodies = {
        'not-json': b'not json\n',
        'wrong-shape': record_bytes(workspace, [], []).replace(b'"received":[]', b'"received":"elf-of-doom"'),
        'boolean-version': record_bytes(workspace, [], []).replace(b'"schemaVersion":1', b'"schemaVersion":true'),
        'no-added': record_bytes(workspace, [], []).replace(b',"added":[]', b''),
        'added-not-received': record_bytes(workspace, [], ['elf-of-doom']),
        # A small record with one key too many is not the helper's own.
        'extra-key': record_bytes(workspace, [], []).replace(b'}\n', b',"note":1}\n'),
        'other-workspace': record_bytes(Path('/somewhere/else'), [], []),
        'too-large': record_bytes(workspace, ['x'] * 2000, []),
    }
    if kind in bodies:
        write_record(workspace, bodies[kind])
    elif kind == 'folder':
        # os.fdopen() refuses a folder descriptor outright, so the type is
        # checked on the raw descriptor first.
        folder.mkdir(mode=0o700)
        (folder / examples.record_name(workspace)).mkdir()
    elif kind == 'link':
        folder.mkdir(mode=0o700)
        (folder / examples.record_name(workspace)).symlink_to(outside)
    elif kind == 'hard-link':
        folder.mkdir(mode=0o700)
        os.link(outside, folder / examples.record_name(workspace))
    elif kind == 'linked-records':
        elsewhere = tmp_path.resolve() / 'elsewhere'
        elsewhere.mkdir(mode=0o700)
        folder.symlink_to(elsewhere, target_is_directory=True)
    elif kind == 'shared-records':
        folder.mkdir()
        folder.chmod(0o755)
    else:
        folder.write_bytes(b'not a folder\n')
    before, outside_before = snapshot(tmp_path.resolve() / 'home'), snapshot(outside)
    assert add(workspace) == {'elf-of-doom': 'record-unusable'}
    assert snapshot(tmp_path.resolve() / 'home') == before
    assert snapshot(outside) == outside_before


@POSIX_OWNER
@pytest.mark.parametrize('unreadable', ['records', 'record'])
def test_a_record_that_cannot_be_read_adds_nothing_and_is_only_logged(workspace, capsys, unreadable):
    # An EACCES (or EIO) on the record folder or file in Portal's state folder
    # is the record being unusable, never a workspace write failure.
    path = write_record(workspace, record_bytes(workspace, [], []))
    (path.parent if unreadable == 'records' else path).chmod(0)
    try:
        assert add(workspace) == {'elf-of-doom': 'record-unusable'}
    finally:
        path.parent.chmod(0o700)
        path.chmod(0o600)
    assert not (workspace / 'Playground').exists()
    assert 'example record in .ods-example-projects of the Portal state folder could not be read' in capsys.readouterr().err
    assert path.read_bytes() == record_bytes(workspace, [], [])


@POSIX_OWNER
def test_a_failed_record_write_keeps_the_added_example_and_the_next_run_records_it(workspace, monkeypatch, capsys):
    real = examples._write_record

    def unwritable(*_args):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(examples, '_write_record', unwritable)
    assert add(workspace) == {'elf-of-doom': 'added'}
    assert (workspace / 'Playground/elf-of-doom/index.html').is_file()
    assert 'could not record the added example projects: OSError' in capsys.readouterr().err
    assert not record_path(workspace).exists()
    # The unchanged copy is recognized as ODS's own and recorded as added.
    monkeypatch.setattr(examples, '_write_record', real)
    assert add(workspace) == {'elf-of-doom': 'added'}
    assert record_path(workspace).read_bytes() == record_bytes(workspace, ['elf-of-doom'], ['elf-of-doom'])


@POSIX_OWNER
def test_a_run_stopped_between_the_rename_and_the_record_is_recovered(workspace, monkeypatch):
    class Killed(BaseException):
        """The installer killed right after the folder was renamed into place."""

    def killed(*_args):
        raise Killed()

    monkeypatch.setattr(examples, '_write_record', killed)
    with pytest.raises(Killed):
        add(workspace)
    monkeypatch.undo()
    assert (workspace / 'Playground/elf-of-doom/index.html').is_file() and not record_path(workspace).exists()
    before = snapshot(workspace / 'Playground')
    assert add(workspace) == {'elf-of-doom': 'added'}
    assert snapshot(workspace / 'Playground') == before
    assert record_path(workspace).read_bytes() == record_bytes(workspace, ['elf-of-doom'], ['elf-of-doom'])


@POSIX_OWNER
@pytest.mark.parametrize('kind', ['edited', 'empty', 'partial', 'extra-file', 'one-byte', 'appended-byte', 'linked-file',
                                  'hard-link', 'file', 'link', 'dangling-link'])
def test_an_existing_entry_of_that_name_is_never_touched(workspace, tmp_path, kind):
    playground = workspace / 'Playground'
    playground.mkdir(mode=0o700)
    target = playground / 'elf-of-doom'
    outside = tmp_path.resolve() / 'outside'
    outside.mkdir()
    if kind == 'edited':
        target.mkdir()
        (target / 'index.html').write_bytes(b'<!doctype html><title>My own version</title>\n')
        (target / 'notes.txt').write_bytes(b'mine\n')
    elif kind == 'empty':
        target.mkdir()
    elif kind == 'partial':
        target.mkdir()
        shutil.copyfile(SOURCE / 'elf-of-doom/index.html', target / 'index.html')
    elif kind in ('extra-file', 'one-byte', 'appended-byte', 'linked-file', 'hard-link'):
        # The pinned files with one difference: an owner's own folder.
        shutil.copytree(SOURCE / 'elf-of-doom', target)
        if kind == 'extra-file':
            (target / 'notes.txt').write_bytes(b'mine\n')
        elif kind == 'one-byte':
            body = (target / 'ui.js').read_bytes()
            (target / 'ui.js').write_bytes(body[:-1] + bytes([body[-1] ^ 1]))
        elif kind == 'appended-byte':
            # The pinned bytes plus one more: only a read past their length tells them apart.
            (target / 'ui.js').write_bytes((target / 'ui.js').read_bytes() + b'x')
        elif kind == 'hard-link':
            # The pinned bytes, but the same file as one outside the folder.
            shutil.copyfile(target / 'ui.js', outside / 'ui.js')
            (target / 'ui.js').unlink()
            os.link(outside / 'ui.js', target / 'ui.js')
        else:
            shutil.copyfile(target / 'ui.js', outside / 'ui.js')
            (target / 'ui.js').unlink()
            (target / 'ui.js').symlink_to(outside / 'ui.js')
    elif kind == 'file':
        target.write_bytes(b'not a folder\n')
    elif kind == 'link':
        target.symlink_to(outside, target_is_directory=True)
    else:
        target.symlink_to(tmp_path.resolve() / 'missing')
    before, outside_before = snapshot(playground), snapshot(outside)
    assert add(workspace) == {'elf-of-doom': 'present'}
    assert snapshot(playground) == before
    assert snapshot(outside) == outside_before
    assert record_path(workspace).read_bytes() == record_bytes(workspace, ['elf-of-doom'], [])


@POSIX_OWNER
@pytest.mark.parametrize('fault', ['linked-workspace', 'shared-workspace', 'linked-playground', 'shared-playground',
                                   'linked-state', 'shared-state', 'unreadable-state'])
def test_an_unsafe_workspace_or_playground_is_refused_without_creating_anything(workspace, tmp_path, fault):
    outside = tmp_path.resolve() / 'outside'
    outside.mkdir(mode=0o700)
    path, state = workspace, state_of(workspace)
    if fault == 'linked-workspace':
        path = workspace.parent / 'linked-workspace'
        path.symlink_to(workspace, target_is_directory=True)
    elif fault == 'shared-workspace':
        workspace.chmod(0o770)
    elif fault == 'linked-playground':
        (workspace / 'Playground').symlink_to(outside, target_is_directory=True)
    elif fault == 'shared-playground':
        (workspace / 'Playground').mkdir()
        (workspace / 'Playground').chmod(0o775)
    elif fault == 'linked-state':
        state = tmp_path.resolve() / 'linked-state'
        state.symlink_to(state_of(workspace), target_is_directory=True)
    elif fault == 'shared-state':
        state.chmod(0o770)
    else:
        # A folder this owner cannot open (EACCES): a layout, not a disk fault.
        state = tmp_path.resolve() / 'locked/.openclaw'
        state.mkdir(parents=True, mode=0o700)
        state.parent.chmod(0)
    try:
        before = snapshot(tmp_path.resolve())
        with pytest.raises(examples.LayoutRefused):
            examples.add_projects(path, state)
        assert snapshot(tmp_path.resolve()) == before
    finally:
        if fault == 'unreadable-state':
            state.parent.chmod(0o700)


@POSIX_OWNER
def test_root_is_refused_before_anything_is_created(workspace, monkeypatch):
    before = snapshot(state_of(workspace))
    monkeypatch.setattr(examples.os, 'geteuid', lambda: 0)
    with pytest.raises(ValueError, match='non-root'):
        add(workspace)
    assert snapshot(state_of(workspace)) == before


@POSIX_OWNER
def test_an_entry_created_during_the_copy_is_kept_and_staging_removed(workspace, monkeypatch):
    real, last = examples._write, sorted(examples.EXAMPLES['elf-of-doom'])[-1]

    def write(directory, name, body):
        real(directory, name, body)
        if name == last:
            (workspace / 'Playground/elf-of-doom').mkdir()

    monkeypatch.setattr(examples, '_write', write)
    assert add(workspace) == {'elf-of-doom': 'present'}
    playground = workspace / 'Playground'
    assert [path.name for path in playground.iterdir()] == ['elf-of-doom']
    assert list((playground / 'elf-of-doom').iterdir()) == []


@POSIX_OWNER
def test_an_empty_folder_made_after_the_last_check_is_never_replaced(workspace, monkeypatch):
    # Portal's own project reservation creates an empty Playground/<name>.
    # Exclusive rename refuses it even inside the check-then-rename window,
    # and that answer came from the exclusive rename itself, not the plain
    # rename fallback's own later check.
    real, calls = examples._exists, []
    real_exclusive, exclusive_results = examples._exclusive_rename, []

    def exists(directory, name):
        calls.append(name)
        if len(calls) == 2:
            os.mkdir(name, 0o700, dir_fd=directory)
            return False
        return real(directory, name)

    def exclusive(*args):
        exclusive_results.append(real_exclusive(*args))
        return exclusive_results[-1]

    monkeypatch.setattr(examples, '_exists', exists)
    monkeypatch.setattr(examples, '_exclusive_rename', exclusive)
    assert add(workspace) == {'elf-of-doom': 'present'}
    assert exclusive_results == [False]
    assert len(calls) == 2
    playground = workspace / 'Playground'
    assert [path.name for path in playground.iterdir()] == ['elf-of-doom']
    assert list((playground / 'elf-of-doom').iterdir()) == []


@POSIX_OWNER
def test_exclusive_rename_works_on_this_system(tmp_path):
    # Linux CI must have renameat2 and macOS CI renameatx_np: without them
    # every run would fall back to the plain rename.
    folder = tmp_path.resolve() / 'playground'
    folder.mkdir()
    (folder / 'staging').mkdir()
    (folder / 'taken').mkdir()
    (folder / 'staging/index.html').write_bytes(b'x')
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert examples._exclusive_rename(directory, 'staging', 'taken') is False
        assert list((folder / 'taken').iterdir()) == []
        assert (folder / 'staging/index.html').read_bytes() == b'x'
        assert examples._exclusive_rename(directory, 'staging', 'placed') is True
    finally:
        os.close(directory)
    assert sorted(path.name for path in folder.iterdir()) == ['placed', 'taken']
    assert (folder / 'placed/index.html').read_bytes() == b'x'


@POSIX_OWNER
def test_without_exclusive_rename_the_plain_rename_still_adds_the_example(workspace, monkeypatch):
    def unavailable(*_args):
        raise OSError(errno.ENOSYS, 'exclusive rename is unavailable')

    monkeypatch.setattr(examples, '_exclusive_rename', unavailable)
    assert add(workspace) == {'elf-of-doom': 'added'}
    assert [path.name for path in (workspace / 'Playground').iterdir()] == ['elf-of-doom']


@POSIX_OWNER
def test_an_unexpected_rename_failure_propagates_and_leaves_no_staging(workspace, monkeypatch):
    def failed(*_args):
        raise OSError(errno.EIO, 'I/O error')

    monkeypatch.setattr(examples, '_exclusive_rename', failed)
    with pytest.raises(OSError, match='I/O error'):
        add(workspace)
    assert list((workspace / 'Playground').iterdir()) == []


@POSIX_OWNER
def test_a_folder_swapped_in_after_the_rename_is_reported_not_claimed(workspace, monkeypatch):
    real = examples._rename_into_place

    def swapped(playground, staging, name):
        assert real(playground, staging, name)
        os.rename(name, 'moved-away', src_dir_fd=playground, dst_dir_fd=playground)
        os.mkdir(name, 0o700, dir_fd=playground)
        return True

    monkeypatch.setattr(examples, '_rename_into_place', swapped)
    with pytest.raises(OSError, match='changed while it was being added'):
        add(workspace)
    assert list((workspace / 'Playground/elf-of-doom').iterdir()) == []


@POSIX_OWNER
def test_a_failed_write_leaves_no_partial_project_and_a_rerun_completes(workspace, monkeypatch):
    real, calls = examples._write, []

    def write(directory, name, body):
        calls.append(name)
        if len(calls) == 3:
            real(directory, name, body[:10])
            raise OSError(28, 'No space left on device')
        real(directory, name, body)

    monkeypatch.setattr(examples, '_write', write)
    with pytest.raises(OSError, match='No space left'):
        add(workspace)
    assert list((workspace / 'Playground').iterdir()) == []
    monkeypatch.setattr(examples, '_write', real)
    assert add(workspace) == {'elf-of-doom': 'added'}


@POSIX_OWNER
def test_an_interrupted_copy_leaves_nothing_after_the_next_run(workspace):
    playground = workspace / 'Playground'
    playground.mkdir(mode=0o700)
    staging_folder(playground, {'index.html': b'<!doctype html>', 'ui.js': b'partial'})
    assert add(workspace) == {'elf-of-doom': 'added'}
    assert [path.name for path in playground.iterdir()] == ['elf-of-doom']


@POSIX_OWNER
def test_a_leftover_that_cannot_be_inspected_is_logged_and_the_copy_still_runs(workspace, monkeypatch, capsys):
    playground = workspace / 'Playground'
    playground.mkdir(mode=0o700)
    staging_folder(playground, {'index.html': b'x'})

    def unreadable(*_args):
        raise PermissionError(13, 'Permission denied')

    monkeypatch.setattr(examples, '_remove_one_staging', unreadable)
    assert add(workspace) == {'elf-of-doom': 'added'}
    assert 'left interrupted staging folder .ods-example-a1b2c3d4e5f60718' in capsys.readouterr().err


@POSIX_OWNER
@pytest.mark.parametrize('kind', ['still-copying', 'owner-file', 'nested-folder', 'other-name', 'link'])
def test_anything_else_that_looks_like_staging_is_left_alone(workspace, tmp_path, kind):
    playground = workspace / 'Playground'
    playground.mkdir(mode=0o700)
    if kind == 'still-copying':
        # Another copy may be writing this one right now.
        staging_folder(playground, {'index.html': b'x'}, age=0)
    elif kind == 'owner-file':
        staging_folder(playground, {'index.html': b'x', 'notes.txt': b'mine\n'})
    elif kind == 'nested-folder':
        folder = staging_folder(playground, {'index.html': b'x'})
        (folder / 'assets').mkdir()
        then = time.time() - 2 * examples.STALE_STAGING_SECONDS
        os.utime(folder, (then, then))
    elif kind == 'other-name':
        (playground / (examples.STAGING_PREFIX + 'mine')).mkdir()
        (playground / (examples.STAGING_PREFIX + 'mine') / 'index.html').write_bytes(b'x')
    else:
        outside = tmp_path.resolve() / 'outside'
        outside.mkdir()
        (outside / 'index.html').write_bytes(b'x')
        (playground / (examples.STAGING_PREFIX + 'a1b2c3d4e5f60718')).symlink_to(outside, target_is_directory=True)
    def kept(entries):
        # Adding elf-of-doom changes Playground's own mtime; compare its entries.
        return {key: value for key, value in entries.items()
                if key.startswith('Playground/') and not key.startswith('Playground/elf-of-doom')}

    before = kept(snapshot(playground))
    assert before
    assert add(workspace) == {'elf-of-doom': 'added'}
    assert kept(snapshot(playground)) == before


@POSIX_OWNER
@pytest.mark.parametrize('change', ['crlf', 'one-byte'])
def test_a_changed_source_copies_nothing(workspace, tmp_path, change):
    source = source_copy(tmp_path)
    index = source / 'elf-of-doom/index.html'
    body = index.read_bytes()
    index.write_bytes(body.replace(b'\n', b'\r\n') if change == 'crlf' else body[:-1] + bytes([body[-1] ^ 1]))
    with pytest.raises(ValueError, match='shipped bytes'):
        add(workspace, source=source)
    assert list(workspace.iterdir()) == []


@POSIX_OWNER
@pytest.mark.parametrize('fault', ['missing', 'folder', 'link', 'unreadable', 'no-example-folder'])
def test_an_incomplete_ods_copy_is_named_as_such(workspace, tmp_path, fault, monkeypatch, capsys):
    # ODS's own files, never the owner's workspace, are the cause: the
    # installer's warning must say so on every rerun.
    source = source_copy(tmp_path)
    readme = source / 'elf-of-doom/README.md'
    if fault == 'missing':
        readme.unlink()
    elif fault == 'folder':
        readme.unlink()
        readme.mkdir()
    elif fault == 'link':
        shutil.copyfile(readme, tmp_path.resolve() / 'README.md')
        readme.unlink()
        readme.symlink_to(tmp_path.resolve() / 'README.md')
    elif fault == 'unreadable':
        readme.chmod(0)
    else:
        shutil.rmtree(source / 'elf-of-doom')
    with pytest.raises(ValueError, match='example source README.md') as raised:
        add(workspace, source=source)
    assert examples._cause(raised.value) == ("ODS's own copy of the game files is damaged or incomplete. "
                                             'Download ODS again, then rerun the installer.')
    assert list(workspace.iterdir()) == []
    monkeypatch.setattr(examples, 'SOURCE', source)
    monkeypatch.setattr(sys, 'argv', ['pixel-example-projects.py', '--workspace', str(workspace),
                                      '--state-dir', str(state_of(workspace))])
    assert examples.main() == examples.NOT_ADDED
    assert capsys.readouterr().out == ("ODS's own copy of the game files is damaged or incomplete. "
                                       'Download ODS again, then rerun the installer.\n')


@POSIX_OWNER
def test_the_command_names_the_cause_and_marks_a_layout_it_never_writes_into(workspace, monkeypatch, capsys):
    arguments = ['pixel-example-projects.py', '--workspace', str(workspace), '--state-dir', str(state_of(workspace))]
    monkeypatch.setattr(sys, 'argv', arguments)
    real = examples._write

    def full(*_args):
        raise OSError(errno.ENOSPC, 'No space left on device')

    monkeypatch.setattr(examples, '_write', full)
    assert examples.main() == examples.NOT_ADDED
    out, err = capsys.readouterr()
    assert out == ('a file could not be written in the Portal workspace (No space left on device). '
                   "Free some disk space or fix that folder's permissions, then rerun the installer.\n")
    assert 'example projects not added: OSError' in err
    monkeypatch.setattr(examples, '_write', real)
    monkeypatch.setitem(examples.EXAMPLES['elf-of-doom'], 'index.html', '0' * 64)
    assert examples.main() == examples.NOT_ADDED
    assert capsys.readouterr().out == ("ODS's own copy of the game files is damaged or incomplete. "
                                       'Download ODS again, then rerun the installer.\n')
    monkeypatch.undo()
    monkeypatch.setattr(sys, 'argv', arguments)
    assert examples.main() == 0
    assert capsys.readouterr().out == '{"elf-of-doom":"added"}\n'
    # A layout only the owner can change: rerunning cannot help, so none is suggested.
    workspace.chmod(0o770)
    assert examples.main() == examples.LAYOUT_REFUSED
    out, err = capsys.readouterr()
    assert out.startswith('ODS does not write into this workspace layout: the Portal workspace ') and out.count('\n') == 1
    assert 'private owner workspace required' in out and 'rerun' not in out.lower()
    assert 'example projects not added: LayoutRefused' in err
    missing = workspace.parent / 'missing-workspace'
    monkeypatch.setattr(sys, 'argv', ['pixel-example-projects.py', '--workspace', str(missing),
                                      '--state-dir', str(state_of(workspace))])
    assert examples.main() == examples.NOT_ADDED
    assert capsys.readouterr().out == (f'the Portal folder {missing} was not found. '
                                       'Rerun the installer so it sets Portal up again.\n')


@POSIX_OWNER
def test_only_the_pinned_files_are_copied(workspace, tmp_path):
    source = source_copy(tmp_path)
    (source / 'elf-of-doom/notes.txt').write_bytes(b'not part of the example\n')
    assert add(workspace, source=source) == {'elf-of-doom': 'added'}
    project = workspace / 'Playground/elf-of-doom'
    assert sorted(path.name for path in project.iterdir()) == sorted(examples.EXAMPLES['elf-of-doom'])


@POSIX_OWNER
def test_permissive_source_modes_and_umask_never_reach_the_workspace(workspace, tmp_path):
    # A Windows-mounted (DrvFS) checkout can report every file as 0777.
    source = source_copy(tmp_path)
    for path in [source, *source.rglob('*')]:
        path.chmod(0o777)
    previous = os.umask(0o002)
    try:
        assert add(workspace, source=source) == {'elf-of-doom': 'added'}
    finally:
        os.umask(previous)
    project = workspace / 'Playground/elf-of-doom'
    assert stat.S_IMODE((workspace / 'Playground').stat().st_mode) == 0o700
    assert stat.S_IMODE(project.stat().st_mode) == 0o700
    assert {stat.S_IMODE(path.lstat().st_mode) for path in project.iterdir()} == {0o600}
    assert stat.S_IMODE((state_of(workspace) / examples.RECORD_DIRECTORY).stat().st_mode) == 0o700
    assert stat.S_IMODE(record_path(workspace).stat().st_mode) == 0o600


@POSIX_OWNER
def test_portal_preview_accepts_the_copied_project(workspace):
    assert add(workspace) == {'elf-of-doom': 'added'}
    spec = importlib.util.spec_from_file_location(
        'example_projects_preview', ROOT / 'extensions/services/pixel-agent/host/workspace_preview.py')
    preview = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preview)
    files = preview._source_files(workspace, 'Playground/elf-of-doom', os.getuid())
    assert [relative for relative, _path, _info in files] == sorted(examples.EXAMPLES['elf-of-doom'])


# ------------------------------------------------- installer steps (bash) --

# The installer's own display: the real logging.sh and ui.sh, whose log()
# prints to the console whenever the display is not cinematic.
DISPLAY = '''RED='' GRN='' BGRN='' DGRN='' MAG='' BMAG='' AMB='' WHT='' DIM='' NC=''
source "$INSTALL_DIR/installers/lib/ui.sh"
source "$INSTALL_DIR/installers/lib/logging.sh"
'''
DISPLAY_MODES = [{'ODS_UI_MODE': 'plain'}, {'ODS_UI_MODE': 'auto', 'CI': '1'},
                 {'ODS_UI_MODE': 'cinematic', 'INTERACTIVE': 'true'}]


def run_linux_step(home, log, enable='true', python=sys.executable, display=None):
    function = shell_function(PHASE, '_phase11_add_example_projects').replace(
        '/usr/bin/python3', shlex.quote(python))
    script = '''set -euo pipefail
INSTALL_DIR="$1"; LOG_FILE="$2"; HOME_FIXTURE="$3"; ODS_HELP_DISCORD_URL="$4"
''' + DISPLAY + '''ods_pixel_install_owner() { printf '%s\\n' owner; }
ods_pixel_owner_home() { printf '%s\\n' "$HOME_FIXTURE"; }
ods_pixel_run_as_owner() { printf 'run as %s\\n' "$1" >>"$LOG_FILE"; shift 2; "$@"; }
''' + function + '''_phase11_add_example_projects
printf 'installer continues\\n'
'''
    env = {key: value for key, value in os.environ.items()
           if key not in ('PIXEL_SERVICE_USER', 'CI', 'NO_COLOR', 'ODS_INSTALLER_GUI', 'INTERACTIVE', 'ODS_UI_MODE')}
    env.update(display or DISPLAY_MODES[0], ENABLE_PIXEL_RUNTIME=enable)
    return subprocess.run(['bash', '-c', script, 'example-test', str(ROOT), str(log), str(home), HELP_URL],
                          text=True, capture_output=True, env=env)


def exiting_helper(tmp_path, code, out='the cause.'):
    """A stand-in for python3 that prints one line and exits with code."""
    stub = tmp_path.resolve() / f'python-exit-{code}'
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" {shlex.quote(out)}\nexit {code}\n', encoding='utf-8')
    stub.chmod(0o700)
    return str(stub)


@POSIX_OWNER
@pytest.mark.parametrize('display', DISPLAY_MODES)
def test_linux_step_adds_the_game_quietly_then_reports_it_present(workspace, tmp_path, display):
    log = tmp_path.resolve() / 'install.log'
    first = run_linux_step(workspace.parents[1], log, display=display)
    assert first.returncode == 0, first.stderr
    # Nothing on the console in any display, though log() prints in some.
    assert first.stdout == 'installer continues\n'
    assert 'run as owner\nPortal example projects: {"elf-of-doom":"added"}\n' in log.read_text(encoding='utf-8')
    assert (workspace / 'Playground/elf-of-doom/index.html').is_file()
    assert record_path(workspace).is_file()
    second = run_linux_step(workspace.parents[1], log, display=display)
    assert second.returncode == 0, second.stderr
    assert second.stdout == 'installer continues\n'
    assert log.read_text(encoding='utf-8').endswith('Portal example projects: {"elf-of-doom":"previously-received"}\n')


@POSIX_OWNER
def test_log_prints_in_the_plain_display_so_the_step_never_uses_it(tmp_path):
    # The reason the step writes to the install log itself.
    log = tmp_path.resolve() / 'install.log'
    script = 'set -euo pipefail\nINSTALL_DIR="$1"; LOG_FILE="$2"\n' + DISPLAY + 'log "visible"\n'
    env = {**os.environ, 'ODS_UI_MODE': 'plain'}
    result = subprocess.run(['bash', '-c', script, 'log-test', str(ROOT), str(log)], text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '[INFO] visible\n'


@POSIX_OWNER
def test_linux_step_warns_with_the_cause_and_continues_when_the_copy_fails(tmp_path):
    home = tmp_path.resolve() / 'home-without-portal'
    home.mkdir()
    log = tmp_path.resolve() / 'install.log'
    result = run_linux_step(home, log)
    assert result.returncode == 0, result.stderr
    warning, after = result.stdout.splitlines()
    # The installer's own ai_warn() line: an indented warning sign, then the text.
    assert warning.startswith('  ') and (WARNING.replace('WARN: ', '') + f'the Portal folder {home}/.openclaw/'
                                         'workspace-pixel was not found. Rerun the installer so it sets Portal up '
                                         'again. Nothing in your Portal workspace was replaced.') in warning
    assert HELP_URL in warning and str(log) in warning
    assert after == 'installer continues'
    assert 'example projects not added: FileNotFoundError' in log.read_text(encoding='utf-8')
    assert list(home.iterdir()) == []


@POSIX_OWNER
@pytest.mark.parametrize('display', DISPLAY_MODES)
def test_linux_step_only_logs_a_workspace_layout_it_never_writes_into(workspace, tmp_path, display):
    # The owner's own permissions persist across reruns: no console line, ever.
    workspace.chmod(0o770)
    log = tmp_path.resolve() / 'install.log'
    for _attempt in range(2):
        result = run_linux_step(workspace.parents[1], log, display=display)
        assert result.returncode == 0, result.stderr
        assert result.stdout == 'installer continues\n'
    text = log.read_text(encoding='utf-8')
    assert text.count('Portal example projects not added: ODS does not write into this workspace layout: ') == 2
    assert 'private owner workspace required' in text
    assert list(workspace.iterdir()) == []


@POSIX_OWNER
def test_linux_step_warns_about_an_unexpected_exit_and_a_missing_owner(workspace, tmp_path):
    log = tmp_path.resolve() / 'install.log'
    result = run_linux_step(workspace.parents[1], log, python=exiting_helper(tmp_path, 1))
    assert result.returncode == 0, result.stderr
    warning, after = result.stdout.splitlines()
    assert 'its copy step stopped unexpectedly (exit 1)' in warning and HELP_URL in warning and str(log) in warning
    assert after == 'installer continues'
    script = shell_function(PHASE, '_phase11_add_example_projects')
    lookup = '''set -euo pipefail
INSTALL_DIR="$1"; LOG_FILE="$2"; ODS_HELP_DISCORD_URL="$3"
''' + DISPLAY + '''ods_pixel_install_owner() { return 1; }
''' + script + '''_phase11_add_example_projects
printf 'installer continues\\n'
'''
    env = {key: value for key, value in os.environ.items() if key != 'PIXEL_SERVICE_USER'}
    env.update(ENABLE_PIXEL_RUNTIME='true', ODS_UI_MODE='plain')
    missing = subprocess.run(['bash', '-c', lookup, 'example-test', str(ROOT), str(log), HELP_URL],
                             text=True, capture_output=True, env=env)
    assert missing.returncode == 0, missing.stderr
    warning, after = missing.stdout.splitlines()
    assert "it could not find the Portal owner's account or home folder" in warning and HELP_URL in warning
    assert after == 'installer continues'
    assert list(workspace.iterdir()) == []


@POSIX_OWNER
def test_linux_step_does_nothing_without_portal(workspace, tmp_path):
    log = tmp_path.resolve() / 'install.log'
    result = run_linux_step(workspace.parents[1], log, enable='false')
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'installer continues\n'
    assert not log.exists()
    assert list(workspace.iterdir()) == []


def macos_install(tmp_path):
    """An install reached through a symlink, as a moved or linked folder is."""
    real = tmp_path.resolve() / 'real install'
    for relative in ('installers/lib/pixel-example-projects.py', 'installers/lib/pixel-workspace-guidance.py'):
        (real / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, real / relative)
    shutil.copytree(SOURCE, real / 'extensions/services/pixel-agent/example-projects')
    link = tmp_path.resolve() / 'ODS'
    link.symlink_to(real, target_is_directory=True)
    return link, real / 'data/pixel-native/home/.openclaw/workspace-pixel'


def run_macos_step(install, log):
    function = shell_function(MACOS_INSTALLER, '_macos_add_example_projects').replace(
        '/usr/bin/python3', shlex.quote(sys.executable))
    script = '''set -euo pipefail
INSTALL_DIR="$1"; ODS_LOG_FILE="$2"; ODS_HELP_DISCORD_URL="$3"
ai_ok() { printf 'OK: %s\\n' "$1"; }
ai_warn() { printf 'WARN: %s\\n' "$1"; }
''' + function + '''_macos_add_example_projects
printf 'installer continues\\n'
'''
    return subprocess.run(['bash', '-c', script, 'example-test', str(install), str(log), HELP_URL],
                          text=True, capture_output=True)


@POSIX_OWNER
def test_macos_step_adds_the_game_quietly_through_the_resolved_install(tmp_path):
    install, workspace = macos_install(tmp_path)
    workspace.mkdir(parents=True)
    workspace.chmod(0o700)
    workspace.parent.chmod(0o700)
    log = tmp_path.resolve() / 'install.log'
    first = run_macos_step(install, log)
    assert first.returncode == 0, first.stderr
    assert first.stdout == 'installer continues\n'
    assert log.read_text(encoding='utf-8') == 'Portal example projects: {"elf-of-doom":"added"}\n'
    assert (workspace / 'Playground/elf-of-doom/index.html').is_file()
    assert record_path(workspace).read_bytes() == record_bytes(workspace, ['elf-of-doom'], ['elf-of-doom'])
    second = run_macos_step(install, log)
    assert second.returncode == 0, second.stderr
    assert second.stdout == 'installer continues\n'
    assert log.read_text(encoding='utf-8').endswith('Portal example projects: {"elf-of-doom":"previously-received"}\n')


@POSIX_OWNER
def test_macos_step_warns_with_the_cause_and_continues_when_the_copy_fails(tmp_path):
    install, workspace = macos_install(tmp_path)
    log = tmp_path.resolve() / 'install.log'
    result = run_macos_step(install, log)
    assert result.returncode == 0, result.stderr
    warning, after = result.stdout.splitlines()
    assert warning.startswith(WARNING + f'the Portal folder {workspace} was not found. '
                              'Rerun the installer so it sets Portal up again.')
    assert HELP_URL in warning and str(log) in warning
    assert after == 'installer continues'
    assert 'FileNotFoundError' in log.read_text(encoding='utf-8')
    assert not (install / 'data').exists()


@POSIX_OWNER
def test_macos_step_only_logs_a_workspace_layout_it_never_writes_into(tmp_path):
    install, workspace = macos_install(tmp_path)
    workspace.mkdir(parents=True)
    workspace.chmod(0o770)
    log = tmp_path.resolve() / 'install.log'
    result = run_macos_step(install, log)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'installer continues\n'
    text = log.read_text(encoding='utf-8')
    assert 'Portal example projects not added: ODS does not write into this workspace layout: ' in text
    assert list(workspace.iterdir()) == []
