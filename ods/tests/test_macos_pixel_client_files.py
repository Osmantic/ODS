"""Exercise owner-code preservation and interrupted rename recovery on files."""
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


SPEC = importlib.util.spec_from_file_location('native_client_files',
    Path(__file__).resolve().parents[1] / 'installers/macos/owner/pixel-native-client-files.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def write(root, relative, content, mode=0o644):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)
    return path


def record(path):
    info = path.stat()
    return path.read_bytes(), info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)


@pytest.fixture
def layout(tmp_path):
    root = tmp_path.resolve()
    source, installed = root / 'source', root / 'installed'
    source.mkdir()
    installed.mkdir()
    write(source, 'bin/agent.py', b'new agent\n', 0o755)
    write(source, 'scripts/new/worker.py', b'new worker\n')
    write(installed, 'bin/agent.py', b'previous agent\n', 0o750)
    return source, installed, root / 'transaction'


def test_publication_preserves_private_state_local_extensions_and_original_inodes(layout):
    source, installed, transaction = layout
    preserved = {}
    for name in ('.env', 'config/owner.json', 'data/notes/test.md', 'data/history.db',
            'models/model.gguf', 'extensions/services/local-extension/compose.yaml', 'bin/local-helper.py'):
        path = write(installed, name, b'owner bytes\n', 0o600)
        preserved[name] = record(path)
    for name in ('.env', 'config/owner.json', 'data/history.db', 'models/model.gguf',
            'extensions/services/local-extension/compose.yaml'):
        write(source, name, b'must not copy\n')
    write(source, 'bin/tests/test_agent.py', b'not deployed\n')
    write(source, 'bin/__pycache__/agent.pyc', b'not deployed\n')
    write(source, 'extensions/services/pixel-agent/host/new.py', b'host integration\n')
    write(source, 'extensions/services/pixel-agent/compose.yaml', b'not selected\n')
    disabled = write(installed, 'extensions/services/pixel-agent/compose.yaml.disabled', b'owner selection\n')
    disabled_before = record(disabled)
    write(source, 'vendor/pixel.bundle', b'public bundle\n')
    original = record(installed / 'bin/agent.py')
    manifest = module.stage(source, installed, transaction)
    binding = module.identity(transaction)
    assert module.verify_staged(transaction)
    assert record(installed / 'bin/agent.py') == original
    assert module.publish(transaction)['status'] == 'published'
    assert module.publish(transaction)['status'] == 'published'
    assert module.verify(transaction)
    assert record(transaction / 'previous/bin/agent.py') == original
    assert (installed / 'vendor/pixel.bundle').read_bytes() == b'public bundle\n'
    assert not (installed / 'bin/tests').exists()
    assert not (installed / 'extensions/services/pixel-agent/compose.yaml').exists()
    assert record(disabled) == disabled_before
    assert all(record(installed / name) == before for name, before in preserved.items())
    assert module.identity(transaction) == binding
    assert len(manifest['files']) == 4
    assert module.restore(transaction)['status'] == 'restored'
    assert module.restore(transaction)['status'] == 'restored'
    assert module.verify(transaction, selected='previous')
    assert module.verify_staged(transaction)
    assert record(installed / 'bin/agent.py') == original
    assert not (installed / 'scripts').exists()
    assert not (installed / 'vendor').exists()
    assert all(record(installed / name) == before for name, before in preserved.items())


def test_mac_cli_aliases_take_precedence_and_keep_executable_mode(layout):
    source, installed, transaction = layout
    write(source, 'lib/constants.sh', b'generic\n')
    write(source, 'installers/macos/lib/constants.sh', b'macos\n')
    write(source, 'installers/macos/ods-macos.sh', b'#!/bin/bash\n', 0o755)
    module.stage(source, installed, transaction)
    module.publish(transaction)
    assert (installed / 'lib/constants.sh').read_bytes() == b'macos\n'
    assert (installed / 'ods-macos.sh').read_bytes() == b'#!/bin/bash\n'
    assert stat.S_IMODE((installed / 'ods-macos.sh').stat().st_mode) == 0o755


@pytest.mark.parametrize('mutation', ['source-link', 'source-hardlink', 'target-link', 'target-hardlink',
    'target-directory-link', 'target-world-writable', 'source-world-writable', 'fifo'])
def test_stage_refuses_unsafe_objects_without_modifying_original(layout, mutation):
    source, installed, transaction = layout
    target = installed / 'bin/agent.py'
    original = record(target)
    if mutation == 'source-link':
        (source / 'bin/alias.py').symlink_to(source / 'bin/agent.py')
    elif mutation == 'source-hardlink':
        os.link(source / 'bin/agent.py', source / 'bin/alias.py')
    elif mutation == 'target-link':
        target.rename(installed / 'preserved.py')
        target.symlink_to(installed / 'preserved.py')
    elif mutation == 'target-hardlink':
        os.link(target, installed / 'preserved.py')
    elif mutation == 'target-directory-link':
        (installed / 'bin').rename(installed / 'preserved-bin')
        (installed / 'bin').symlink_to(installed / 'preserved-bin', target_is_directory=True)
    elif mutation == 'target-world-writable':
        (installed / 'bin').chmod(0o777)
    elif mutation == 'source-world-writable':
        (source / 'bin').chmod(0o777)
    else:
        os.mkfifo(source / 'bin/pipe')
    with pytest.raises(ValueError, match='native-client-files-unqualified'):
        module.stage(source, installed, transaction)
    assert record(target) == original


@pytest.mark.parametrize('location,mutation', [('target', 'bytes'), ('target', 'inode'),
    ('candidate', 'bytes'), ('candidate', 'inode'), ('candidate', 'unknown')])
def test_candidate_and_original_drift_are_refused_before_publication(layout, location, mutation):
    source, installed, transaction = layout
    module.stage(source, installed, transaction)
    path = installed / 'bin/agent.py' if location == 'target' else transaction / 'candidate/bin/agent.py'
    if mutation == 'bytes':
        path.write_bytes(b'changed\n')
    elif mutation == 'inode':
        replacement = path.with_name('replacement')
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(replacement, path)
    else:
        write(transaction / 'candidate', 'bin/unrecorded.py', b'unknown\n')
    before = record(installed / 'bin/agent.py')
    with pytest.raises(ValueError, match='native-client-files-unqualified'):
        module.verify_staged(transaction)
    if mutation != 'unknown':
        with pytest.raises(ValueError, match='native-client-files-unqualified'):
            module.publish(transaction)
    assert record(installed / 'bin/agent.py') == before
    assert not (installed / 'scripts').exists()


def interrupt_after_rename(monkeypatch, count):
    original = module._rename
    calls = []
    def rename(source, destination):
        original(source, destination)
        calls.append((source, destination))
        if len(calls) == count:
            raise RuntimeError('lost acknowledgement')
    monkeypatch.setattr(module, '_rename', rename)
    return original


@pytest.mark.parametrize('boundary', range(1, 6))
@pytest.mark.parametrize('recovery', ['publish', 'restore'])
def test_lost_publish_acknowledgement_recovers_exact_objects(layout, monkeypatch, boundary, recovery):
    source, installed, transaction = layout
    original = record(installed / 'bin/agent.py')
    module.stage(source, installed, transaction)
    rename = interrupt_after_rename(monkeypatch, boundary)
    with pytest.raises(RuntimeError, match='lost acknowledgement'):
        module.publish(transaction)
    monkeypatch.setattr(module, '_rename', rename)
    getattr(module, recovery)(transaction)
    assert module.verify(transaction, selected='candidate' if recovery == 'publish' else 'previous')
    preserved = transaction / 'previous/bin/agent.py' if recovery == 'publish' else installed / 'bin/agent.py'
    assert record(preserved) == original


@pytest.mark.parametrize('boundary', range(1, 6))
def test_lost_restore_acknowledgement_returns_original_inode(layout, monkeypatch, boundary):
    source, installed, transaction = layout
    original = record(installed / 'bin/agent.py')
    module.stage(source, installed, transaction)
    module.publish(transaction)
    rename = interrupt_after_rename(monkeypatch, boundary)
    with pytest.raises(RuntimeError, match='lost acknowledgement'):
        module.restore(transaction)
    monkeypatch.setattr(module, '_rename', rename)
    module.restore(transaction)
    assert module.verify_staged(transaction)
    assert record(installed / 'bin/agent.py') == original


def test_restore_preserves_new_owner_files_in_transaction_created_directories(layout):
    source, installed, transaction = layout
    module.stage(source, installed, transaction)
    module.publish(transaction)
    unknown = write(installed, 'scripts/new/owner-created.txt', b'keep me\n')
    before = record(unknown)
    module.restore(transaction)
    assert record(unknown) == before
    assert module.verify(transaction, selected='previous')


def test_restore_refuses_changed_candidate_instead_of_overwriting_it(layout):
    source, installed, transaction = layout
    before = record(installed / 'bin/agent.py')
    module.stage(source, installed, transaction)
    module.publish(transaction)
    (installed / 'bin/agent.py').write_bytes(b'owner changed after publication\n')
    with pytest.raises(ValueError, match='native-client-files-unqualified'):
        module.restore(transaction)
    assert (installed / 'bin/agent.py').read_bytes() == b'owner changed after publication\n'
    assert record(transaction / 'previous/bin/agent.py') == before


def test_bounds_and_manifest_tampering_do_not_admit_a_publication(layout, monkeypatch):
    source, installed, transaction = layout
    monkeypatch.setattr(module, 'MAX_FILES', 1)
    with pytest.raises(ValueError, match='native-client-files-unqualified'):
        module.stage(source, installed, transaction)
    assert (installed / 'bin/agent.py').read_bytes() == b'previous agent\n'
    monkeypatch.setattr(module, 'MAX_FILES', 12000)
    other = transaction.with_name('other-transaction')
    module.stage(source, installed, other)
    manifest = other / 'manifest.json'
    manifest.write_bytes(manifest.read_bytes().replace(b'new/worker.py', b'new/unsafe.py'))
    with pytest.raises(ValueError, match='native-client-files-unqualified'):
        module.publish(other)
    assert not (installed / 'scripts').exists()


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin ACL enforcement')
def test_owner_code_with_extended_acl_is_not_replaced(layout):
    source, installed, transaction = layout
    target = installed / 'bin/agent.py'
    before = record(target)
    subprocess.run(['/bin/chmod', '+a', 'everyone allow write', str(target)], check=True)
    with pytest.raises(ValueError, match='native-client-files-unqualified'):
        module.stage(source, installed, transaction)
    assert record(target) == before
