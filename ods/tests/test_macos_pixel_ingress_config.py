"""Real private-file reader controls around the bounded Docker staging calls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from native_config_volume_fixture import StorageEngine, mapping

SPEC = importlib.util.spec_from_file_location('native_ingress_config',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-ingress-config.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
pytestmark = pytest.mark.skipif(not hasattr(os, 'getuid') or os.getuid() == 0,
    reason='requires an unprivileged POSIX owner')


@pytest.mark.parametrize('fault', [None, 'init', 'write', 'digest', 'oversized-output', 'changed-before', 'changed-after'])
def test_staging_checks_real_private_input_and_readback_before_returning_selection(tmp_path, fault):
    source = tmp_path / 'config.json'
    raw = b'{"gateway":{"auth":{"token":"synthetic-private-token"}}}'
    source.write_bytes(raw)
    source.chmod(0o600)
    expected = hashlib.sha256(raw).hexdigest()
    storage = StorageEngine(tmp_path)
    document = mapping({'services': {}})
    calls = []
    def run(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs['timeout'] == 30
        assert 'synthetic-private-token' not in str(args)
        if args[-1] == 'pixel-native-config-init':
            if fault == 'changed-before': source.write_bytes(raw + b'\n')
            return SimpleNamespace(returncode=int(fault == 'init'), stdout='')
        assert args[-2:] == ('pixel-native-config-write', expected)
        assert kwargs['input'].encode() == raw
        if fault == 'changed-after': source.write_bytes(raw + b'\n')
        return SimpleNamespace(returncode=int(fault == 'write'), stdout='x' * 1025 if fault == 'oversized-output'
            else json.dumps({'sha256': 'a' * 64 if fault == 'digest' else expected, 'reused': False}))
    if fault:
        with pytest.raises(ValueError): module.stage(run, source, user=f'{os.getuid()}:{os.getgid()}',
            docker_run=storage, install_dir=tmp_path, document=document)
        if fault in ('init', 'changed-before'): assert len(calls) == 1
    else:
        assert module.stage(run, source, user=f'{os.getuid()}:{os.getgid()}',
            docker_run=storage, install_dir=tmp_path, document=document) == {'sha256': expected, 'reused': False}
        assert len(calls) == 2


@pytest.mark.parametrize('fault', ['mode', 'symlink', 'directory', 'hardlink', 'user', 'invalid-json'])
def test_unsafe_source_never_starts_storage_or_writer(tmp_path, fault):
    source = tmp_path / 'config.json'
    source.write_text('{"gateway":{"auth":{"token":"synthetic-token"}}}')
    source.chmod(0o600)
    user = f'{os.getuid()}:{os.getgid()}'
    if fault == 'mode': source.chmod(0o640)
    if fault == 'symlink':
        original = tmp_path / 'original'
        source.rename(original)
        source.symlink_to(original)
    if fault == 'directory':
        source.unlink()
        source.mkdir()
    if fault == 'hardlink': os.link(source, tmp_path / 'linked')
    if fault == 'user': user = '0:0'
    if fault == 'invalid-json': source.write_text('{')
    def forbidden(*args, **kwargs): pytest.fail('unsafe input reached Docker')
    with pytest.raises((ValueError, OSError)):
        module.stage(forbidden, source, user=user, docker_run=forbidden, install_dir=tmp_path)


@pytest.mark.parametrize('existing', [False, True])
def test_prepare_publishes_only_after_staging_and_restores_exact_previous_env(tmp_path, existing):
    source, env = tmp_path / 'config.json', tmp_path / '.env'
    source.write_text('{"gateway":{"auth":{"token":"synthetic-token"}}}')
    source.chmod(0o600)
    before = b'LLM_MODEL=operator-choice\n' + (b'PIXEL_NATIVE_CONFIG_SHA256=' + b'a' * 64 + b'\n' if existing else b'')
    env.write_bytes(before)
    env.chmod(0o600)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    user, image = f'{os.getuid()}:{os.getgid()}', 'sha256:' + 'b' * 64
    calls, receipts = [], []
    storage = StorageEngine(tmp_path)
    def run(*args, **kwargs):
        calls.append(args)
        assert env.read_bytes() == before
        if args[0] == '--profile':
            return SimpleNamespace(returncode=0, stdout=json.dumps(mapping({'services': {
                'pixel-native-config-write': {'user': user, 'image': image, 'network_mode': 'none',
                    'read_only': True, 'cap_drop': ['ALL'], 'entrypoint': ['node', '/source/native_ingress_config.mjs'],
                    'labels': {'org.osmantic.ods.native-config-source': str(source)}},
                'pixel-native-ingress': {'user': user, 'image': image}}})))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'sha256': digest, 'reused': False}))
    def checkpoint(value):
        assert env.read_bytes() == before
        assert Path(value['environmentAfter']).read_bytes() != before
        receipts.append(value)
    receipt = module.prepare(run, docker_run=storage, environment_path=env, backup=tmp_path / 'before', checkpoint=checkpoint)
    assert receipts == [receipt]
    assert len(calls) == 3
    after = env.read_bytes()
    assert after.count(b'PIXEL_NATIVE_CONFIG_SHA256=') == 1 and digest.encode() in after
    assert after.startswith(b'LLM_MODEL=operator-choice\n')
    assert module.restore_environment(env, receipt) is True
    assert env.read_bytes() == before
    assert module.restore_environment(env, receipt) is False
    # Concurrent owner edits must not be overwritten by recovery.
    env.write_bytes(after + b'OWNER_EDIT=preserve\n')
    with pytest.raises(ValueError, match='environment-changed-before-native-rollback'):
        module.restore_environment(env, receipt)
    assert env.read_bytes().endswith(b'OWNER_EDIT=preserve\n')


def test_digest_replacement_cannot_replace_any_other_setting(tmp_path):
    env = tmp_path / '.env'
    env.write_text('LLM_MODEL=operator-choice\n')
    env.chmod(0o600)
    with pytest.raises(ValueError, match='native-ingress-digest-binding-required'):
        module._environment()._persist(env, {'LLM_MODEL': 'a' * 64}, backup=tmp_path / 'before',
            replace_ingress_digest=True)
    assert env.read_text() == 'LLM_MODEL=operator-choice\n'
