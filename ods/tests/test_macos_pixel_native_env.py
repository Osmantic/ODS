import importlib.util
import json
import os
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('native_env',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-env.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('fault', [None, 'missing-key', 'reused-key', 'binding-conflict', 'checkpoint'])
def test_migration_persists_existing_credentials_and_bindings_in_one_write(tmp_path, fault):
    path, backup, previous = tmp_path / '.env', tmp_path / 'backup.env', tmp_path / 'old.json'
    old = b'LLM_MODEL=retained\nDASHBOARD_API_KEY=' + b'a' * 64 + b'\n'
    if fault != 'missing-key': old += b'PIXEL_OPENWEBUI_KEY=' + (b'a' if fault == 'reused-key' else b'b') * 64 + b'\n'
    if fault == 'binding-conflict': old += b'PIXEL_NATIVE_UID=different\n'
    path.write_bytes(old)
    path.chmod(0o600)
    previous.write_text(json.dumps({'models': {'providers': {'ods-gateway': {
        'baseUrl': 'http://127.0.0.1:4006/v1', 'apiKey': 'c' * 64}}}}))
    previous.chmod(0o600)
    bindings = {key: '501' for key in module.KEYS}
    planned = []
    def checkpoint(body):
        assert path.read_bytes() == old
        assert not backup.exists()
        planned.append(body)
        if fault == 'checkpoint': raise OSError('journal-not-durable')
    if fault:
        with pytest.raises((ValueError, OSError)):
            module.persist_migration(path, bindings, previous_config=previous, backup=backup, before_write=checkpoint)
        assert path.read_bytes() == old and not backup.exists()
    else:
        written = module.persist_migration(path, bindings, previous_config=previous, backup=backup,
            before_write=checkpoint)
        assert written == planned[0] == path.read_bytes()
        assert backup.read_bytes() == old
        assert written.startswith(old)
        assert b'PIXEL_MODEL_RELAY_KEY=' + b'c' * 64 in written
        assert module.persist_migration(path, bindings, previous_config=previous, backup=backup) is None
        module.restore(path, backup=backup, expected=written)
        assert path.read_bytes() == old


@pytest.mark.parametrize('fault', [None, 'empty', 'duplicate', 'conflict', 'symlink', 'hardlink',
    'public', 'backup', 'newline', 'extra', 'changed'])
def test_native_bindings_preserve_other_values_and_refuse_conflicts(tmp_path, monkeypatch, fault):
    path, backup = tmp_path / '.env', tmp_path / 'backup.env'
    before = b'# retained\nLLM_MODEL=owner-selected\nSECRET=keep-this-exactly'
    if fault in ('empty', 'duplicate', 'conflict'):
        before += b'\nPIXEL_NATIVE_UID=' + (b'other' if fault == 'conflict' else b'') + b'\n'
        if fault == 'duplicate': before += b'PIXEL_NATIVE_UID=\n'
    path.write_bytes(before)
    path.chmod(0o644 if fault == 'public' else 0o600)
    bindings = {key: '501' for key in module.KEYS}
    bindings['PIXEL_NATIVE_WORKSPACE'] = "/Users/An Owner's $Files/# projects\\demo"
    if fault == 'newline': bindings['PIXEL_NATIVE_UID'] = '501\nINJECT=1'
    if fault == 'extra': bindings['LLM_MODEL'] = 'not-allowed'
    if fault == 'symlink':
        other = tmp_path / 'target'
        path.rename(other)
        path.symlink_to(other)
    if fault == 'hardlink': os.link(path, tmp_path / 'linked')
    if fault == 'backup': backup.write_text('previous backup')
    if fault == 'changed':
        original = module.snapshot
        calls = [0]
        def snapshot(p):
            calls[0] += 1
            if calls[0] == 2: p.write_bytes(before + b'\nOWNER_CHANGE=yes\n')
            return original(p)
        monkeypatch.setattr(module, 'snapshot', snapshot)
    if fault not in (None, 'empty'):
        with pytest.raises((ValueError, OSError)): module.persist(path, bindings, backup=backup)
        assert path.read_bytes() == before + (b'\nOWNER_CHANGE=yes\n' if fault == 'changed' else b'')
    else:
        assert module.persist(path, bindings, backup=backup) is True
        assert backup.read_bytes() == before
        assert path.read_bytes().startswith(b'# retained\nLLM_MODEL=owner-selected\nSECRET=keep-this-exactly\n')
        parsed = {line.split('=', 1)[0]: module.values.parse_env_value(line.split('=', 1)[1])
            for line in path.read_text().splitlines() if '=' in line}
        assert all(parsed[key] == value for key, value in bindings.items())
        assert module.persist(path, bindings, backup=backup) is False
        assert backup.stat().st_mode & 0o777 == 0o600
        assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('.native-env-*'))


@pytest.mark.parametrize('fault', [None, 'empty', 'duplicate', 'invalid', 'reused', 'changed'])
def test_credentials_generated_once_without_rotating_existing_values(tmp_path, monkeypatch, fault):
    path, backup = tmp_path / '.env', tmp_path / 'credentials-backup.env'
    before = b'LLM_MODEL=owner-choice\nDASHBOARD_API_KEY=' + b'a' * 64 + b'\n'
    if fault == 'empty': before += b'PIXEL_OPENWEBUI_KEY=\n'
    if fault == 'duplicate': before += b'DASHBOARD_API_KEY=\n'
    if fault == 'invalid': before += b'PIXEL_OPENWEBUI_KEY=legacy-invalid\n'
    if fault == 'reused': before += b'PIXEL_OPENWEBUI_KEY=' + b'a' * 64 + b'\n'
    path.write_bytes(before)
    path.chmod(0o600)
    if fault == 'changed':
        original = module.snapshot
        calls = []
        def snapshot(p):
            calls.append(p)
            if len(calls) == 2:
                p.write_bytes(before + b'OWNER_EDIT=yes\n')
            return original(p)
        monkeypatch.setattr(module, 'snapshot', snapshot)
    if fault not in (None, 'empty'):
        with pytest.raises(ValueError): module.ensure_credentials(path, backup=backup)
        assert path.read_bytes() == before + (b'OWNER_EDIT=yes\n' if fault == 'changed' else b'')
        assert not backup.exists()
        return
    assert module.ensure_credentials(path, backup=backup)
    parsed = dict(line.split('=', 1) for line in path.read_text().splitlines())
    assert parsed['LLM_MODEL'] == 'owner-choice'
    assert parsed['DASHBOARD_API_KEY'] == 'a' * 64
    assert len({parsed[key] for key in module.CREDENTIALS}) == 3
    assert all(module.re.fullmatch('[a-f0-9]{64}', parsed[key]) for key in module.CREDENTIALS)
    assert backup.read_bytes() == before
    after = path.read_bytes()
    assert module.ensure_credentials(path, backup=backup) is False
    assert path.read_bytes() == after
    assert path.stat().st_mode & 0o777 == backup.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('fault', [None, 'remote', 'port', 'auth', 'query', 'conflict',
    'invalid', 'shape', 'duplicate-port', 'reused', 'source-drift'])
def test_migration_imports_existing_relay_key_without_rotation(tmp_path, monkeypatch, fault):
    path, backup, previous = tmp_path / '.env', tmp_path / 'backup', tmp_path / 'previous.json'
    before = b'LLM_MODEL=keep\nDASHBOARD_API_KEY=' + b'a' * 64 + b'\n'
    if fault == 'conflict': before += b'PIXEL_MODEL_RELAY_KEY=' + b'c' * 64 + b'\n'
    if fault == 'duplicate-port': before += b'PIXEL_MODEL_RELAY_PORT=4006\nPIXEL_MODEL_RELAY_PORT=4006\n'
    path.write_bytes(before)
    path.chmod(0o600)
    provider = {'apiKey': ('a' if fault == 'reused' else 'b') * 64, 'baseUrl': 'http://127.0.0.1:4006/v1'}
    if fault == 'remote': provider['baseUrl'] = 'https://example.com:4006/v1'
    if fault == 'port': provider['baseUrl'] = 'http://localhost:4007/v1'
    if fault == 'auth': provider['baseUrl'] = 'http://user@localhost:4006/v1'
    if fault == 'query': provider['baseUrl'] += '?token=unexpected'
    if fault == 'invalid': provider['apiKey'] = 'invalid'
    previous.write_text(json.dumps({'models': {'providers': {'ods-gateway': None if fault == 'shape' else provider}}}))
    previous.chmod(0o600)
    if fault == 'source-drift':
        original = module.snapshot
        calls = []
        def snapshot(p):
            if Path(p) == previous:
                calls.append(p)
                if len(calls) == 3: previous.write_text('{}')
            return original(p)
        monkeypatch.setattr(module, 'snapshot', snapshot)
    if fault:
        with pytest.raises(ValueError):
            module.ensure_credentials(path, backup=backup, previous_config=previous)
        assert path.read_bytes() == before
    else:
        assert module.ensure_credentials(path, backup=backup, previous_config=previous)
        parsed = dict(line.split('=', 1) for line in path.read_text().splitlines())
        assert parsed['PIXEL_MODEL_RELAY_KEY'] == 'b' * 64
        assert parsed['DASHBOARD_API_KEY'] == 'a' * 64
        assert backup.read_bytes() == before
        assert not module.ensure_credentials(path, backup=backup, previous_config=previous)
    assert not list(tmp_path.glob('.native-env-*'))


@pytest.mark.parametrize('fault', [None, 'owner-edit', 'backup-link', 'drift', 'backup-drift', 'not-bytes'])
def test_migration_environment_rollback_preserves_concurrent_edits(tmp_path, monkeypatch, fault):
    path, backup = tmp_path / '.env', tmp_path / 'backup'
    before = b'# original\nLLM_MODEL=preserved\n'
    path.write_bytes(before)
    path.chmod(0o600)
    module.ensure_credentials(path, backup=backup)
    expected = path.read_bytes()
    if fault == 'owner-edit': path.write_bytes(expected + b'OWNER_EDIT=yes\n')
    if fault == 'backup-link':
        saved = tmp_path / 'saved'
        backup.rename(saved)
        backup.symlink_to(saved)
    if fault in ('drift', 'backup-drift'):
        original = module.snapshot
        target = path if fault == 'drift' else backup
        calls = []
        def snapshot(p):
            if Path(p) == target:
                calls.append(p)
                if len(calls) == 2: target.write_bytes(target.read_bytes() + b'OWNER_EDIT=yes\n')
            return original(p)
        monkeypatch.setattr(module, 'snapshot', snapshot)
    if fault:
        with pytest.raises((ValueError, OSError)):
            module.restore(path, backup=backup, expected='invalid' if fault == 'not-bytes' else expected)
        assert path.read_bytes() == expected + (b'OWNER_EDIT=yes\n' if fault in ('owner-edit', 'drift') else b'')
    else:
        assert module.restore(path, backup=backup, expected=expected)
        assert path.read_bytes() == backup.read_bytes() == before
        assert path.stat().st_mode & 0o777 == 0o600
        assert not module.restore(path, backup=backup, expected=expected)
    assert not list(tmp_path.glob('.native-env-*'))
