import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider.sharing import (
    PUBLIC_MODEL,
    SharingStore,
    StoreError,
)
from pixel_provider.store import LOCK_NAME

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX storage')

@pytest.fixture
def store(tmp_path):
    os.chmod(tmp_path, 0o700)
    return SharingStore(tmp_path)


@pytest.fixture
def valid_settings():
    return {
        'label': 'Test Device',
        'catalogId': 'cat-1',
        'runtimeModelId': 'model-1',
        'ttlSeconds': 3600,
        'maxConcurrent': 1,
        'maxOutputTokens': 1024,
        'deadlineSeconds': 60,
        'requestsPerMinute': 10
    }


def test_issue_returns_credential_and_config(store, valid_settings):
    result = store.issue(valid_settings, expected_revision=0)

    assert 'credential' in result
    assert 'configuration' in result
    assert 'model' in result
    assert result['model'] == PUBLIC_MODEL

    cred = result['credential']
    assert 'id' in cred
    assert 'key' in cred
    assert cred['id'].startswith('device-')
    assert len(cred['key']) == 74  # 'ods_infer_' + 64 hex chars

    config = result['configuration']
    assert config['revision'] == 1
    assert config['enabled'] is False
    assert len(config['devices']) == 1
    assert config['devices'][0]['id'] == cred['id']
    assert 'tokenHash' not in config['devices'][0]


def test_set_enabled_increments_revision(store, valid_settings):
    store.issue(valid_settings, expected_revision=0)

    result = store.set_enabled(True, expected_revision=1)

    assert result['revision'] == 2
    assert result['enabled'] is True


def test_authenticate_valid_token(store, valid_settings):
    now = int(time.time())
    result = store.issue(valid_settings, expected_revision=0, now=now)
    key = result['credential']['key']

    # Enable sharing
    store.set_enabled(True, expected_revision=1)

    # Authenticate
    device = store.authenticate(key, now=now + 1)

    assert device['id'] == result['credential']['id']
    assert device['label'] == valid_settings['label']
    assert 'tokenHash' not in device


def test_authenticate_expired_token(store, valid_settings):
    now = int(time.time())
    result = store.issue(valid_settings, expected_revision=0, now=now)
    key = result['credential']['key']

    store.set_enabled(True, expected_revision=1)

    # Token expires after ttlSeconds
    with pytest.raises(StoreError, match='invalid-credential'):
        store.authenticate(key, now=now + valid_settings['ttlSeconds'] + 1)


def test_authenticate_revoked_token(store, valid_settings):
    now = int(time.time())
    result = store.issue(valid_settings, expected_revision=0, now=now)
    key = result['credential']['key']
    device_id = result['credential']['id']

    store.set_enabled(True, expected_revision=1)
    store.revoke(device_id, expected_revision=2)

    with pytest.raises(StoreError, match='invalid-credential'):
        store.authenticate(key, now=now + 1)


def test_authenticate_wrong_key(store, valid_settings):
    now = int(time.time())
    store.issue(valid_settings, expected_revision=0, now=now)
    store.set_enabled(True, expected_revision=1)

    wrong_key = 'ods_infer_' + 'a' * 64
    with pytest.raises(StoreError, match='invalid-credential'):
        store.authenticate(wrong_key, now=now + 1)


def test_storage_plaintext_absence(store, valid_settings, tmp_path):
    now = int(time.time())
    result = store.issue(valid_settings, expected_revision=0, now=now)
    key = result['credential']['key']

    # Read raw file content
    config_path = tmp_path / 'inference-sharing.json'
    content = config_path.read_text()

    # Key should not be in plaintext
    assert key not in content
    # Token hash should be present
    assert result['configuration']['devices'][0]['id'] in content


def test_public_hash_absence(store, valid_settings):
    result = store.issue(valid_settings, expected_revision=0)

    config = result['configuration']
    for device in config['devices']:
        assert 'tokenHash' not in device


def test_cas_conflict(store, valid_settings):
    now = int(time.time())
    store.issue(valid_settings, expected_revision=0, now=now)

    # Try to issue with stale revision
    with pytest.raises(StoreError, match='stale-revision'):
        store.issue(valid_settings, expected_revision=0, now=now + 1)


def test_read_snapshot_read_only_semantics(store, valid_settings, tmp_path):
    now = int(time.time())
    result = store.issue(valid_settings, expected_revision=0, now=now)

    # Unlink lock file to simulate read-only mount scenario
    lock_path = tmp_path / LOCK_NAME
    if lock_path.exists():
        lock_path.unlink()

    # read_snapshot should work without lock
    snapshot = store.read_snapshot()
    assert snapshot['revision'] == 1
    assert len(snapshot['devices']) == 1

    # Authenticate should work with snapshot (no lock needed)
    key = result['credential']['key']
    store.set_enabled(True, expected_revision=1)

    # Re-unlink lock after set_enabled
    if lock_path.exists():
        lock_path.unlink()

    device = store.authenticate(key, now=now + 1)
    assert device['id'] == result['credential']['id']
    assert not lock_path.exists()


@pytest.mark.parametrize("settings,expected_error", [
    (
        {
            'label': 'Test',
            'catalogId': 'cat-1',
            'runtimeModelId': 'model-1',
            'ttlSeconds': 30,  # Too low
            'maxConcurrent': 1,
            'maxOutputTokens': 1024,
            'deadlineSeconds': 60,
            'requestsPerMinute': 10
        },
        'invalid-request'
    ),
    (
        {
            'label': 'Test',
            'catalogId': 'cat-1',
            'runtimeModelId': 'model-1',
            'ttlSeconds': 3600,
            'maxConcurrent': 10,  # Too high
            'maxOutputTokens': 1024,
            'deadlineSeconds': 60,
            'requestsPerMinute': 10
        },
        'invalid-config'
    ),
    (
        {
            'label': 'Test',
            'catalogId': 'cat-1',
            'runtimeModelId': 'model-1',
            'ttlSeconds': 3600,
            'maxConcurrent': 1,
            'maxOutputTokens': 200000,  # Too high
            'deadlineSeconds': 60,
            'requestsPerMinute': 10
        },
        'invalid-config'
    ),
    (
        {
            'label': 'Test',
            'catalogId': 'cat-1',
            'runtimeModelId': 'model-1',
            'ttlSeconds': 3600,
            'maxConcurrent': 1,
            'maxOutputTokens': 1024,
            'deadlineSeconds': 5000,  # Too high
            'requestsPerMinute': 10
        },
        'invalid-config'
    ),
    (
        {
            'label': 'Test',
            'catalogId': 'cat-1',
            'runtimeModelId': 'model-1',
            'ttlSeconds': 3600,
            'maxConcurrent': 1,
            'maxOutputTokens': 1024,
            'deadlineSeconds': 60,
            'requestsPerMinute': 1000  # Too high
        },
        'invalid-config'
    ),
    (
        {
            'label': 'Test',
            'catalogId': 'cat-1',
            'runtimeModelId': 'model-1',
            'ttlSeconds': 3600,
            'maxConcurrent': 1,
            'maxOutputTokens': 1024,
            'deadlineSeconds': 60,
            # Missing requestsPerMinute
        },
        'invalid-request'
    ),
])
def test_invalid_settings(store, settings, expected_error):
    with pytest.raises(StoreError, match=expected_error):
        store.issue(settings, expected_revision=0)


def test_revoke_nonexistent_device(store):
    with pytest.raises(StoreError, match='invalid-request'):
        store.revoke('device-0000000000000000', expected_revision=0)


def test_set_enabled_invalid_type(store):
    with pytest.raises(StoreError, match='invalid-request'):
        store.set_enabled('true', expected_revision=0)


def test_issue_invalid_revision(store, valid_settings):
    with pytest.raises(StoreError, match='invalid-request'):
        store.issue(valid_settings, expected_revision=-1)


def test_issue_invalid_timestamp(store, valid_settings):
    # Timestamp too large
    with pytest.raises(StoreError, match='invalid-request'):
        store.issue(valid_settings, expected_revision=0, now=2**53)
