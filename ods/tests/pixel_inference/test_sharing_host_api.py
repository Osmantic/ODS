"""Contract tests for pixel_provider.sharing_host_api owner control plane.

`ods-host-agent.py` serves these functions to the owner dashboard: read the
inference-sharing configuration, issue a new device grant, toggle sharing, and
revoke a device. The wrapper pins the public envelope shape and binds every
mutation to the currently active route so a settings write can never outlive
the model it was approved for.
"""
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider import sharing_host_api as api
from pixel_provider.sharing_host_api import change_sharing, get_sharing
from pixel_provider.store import StoreError

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX private store')

ROUTE = {'catalogId': 'catalog-alpha', 'runtimeModelId': 'model-alpha', 'port': 4005}


def settings(**changes):
    base = dict(label='Living room tablet', catalogId='catalog-alpha',
                runtimeModelId='model-alpha', ttlSeconds=86400, maxConcurrent=2,
                maxOutputTokens=2048, deadlineSeconds=60, requestsPerMinute=30)
    base.update(changes)
    return base


def issued_device_id(data_dir):
    return get_sharing(data_dir, ROUTE)['configuration']['devices'][0]['id']


def test_missing_directory_returns_public_defaults(tmp_path):
    result = get_sharing(tmp_path, ROUTE)
    assert result['configuration'] == {'schemaVersion': 1, 'revision': 0,
                                       'enabled': False, 'devices': []}
    assert result['activeRoute'] == ROUTE
    assert result['transport'] == {'mode': 'loopback-only', 'defaultPort': 4005, 'port': 4005}
    assert result['runtime'] == {'status': 'not-probed'}


def test_envelope_never_leaks_token_hashes(tmp_path):
    change_sharing(tmp_path, 'issue', {'expectedRevision': 0, 'settings': settings()}, ROUTE)
    result = get_sharing(tmp_path, ROUTE)
    device = result['configuration']['devices'][0]
    assert 'tokenHash' not in device and 'tokenHash' not in result['configuration']


def test_issue_returns_bound_credential_and_shared_model(tmp_path):
    result = change_sharing(tmp_path, 'issue',
                            {'expectedRevision': 0, 'settings': settings()}, ROUTE)
    credential = result['credential']
    assert credential['id'].startswith('device-') and len(credential['id']) == 23
    assert credential['key'].startswith('ods_infer_') and len(credential['key']) == 74
    assert result['model'] == 'ods/shared'
    device = result['configuration']['devices'][0]
    assert device['id'] == credential['id'] and device['revoked'] is False
    assert device['expiresAt'] - device['createdAt'] == 86400
    assert result['configuration']['revision'] == 1


def test_issue_requires_the_active_route(tmp_path):
    with pytest.raises(StoreError, match='active-route-changed'):
        change_sharing(tmp_path, 'issue',
                       {'expectedRevision': 0, 'settings': settings()}, None)
    other = dict(ROUTE, runtimeModelId='model-beta')
    with pytest.raises(StoreError, match='active-route-changed'):
        change_sharing(tmp_path, 'issue',
                       {'expectedRevision': 0, 'settings': settings()}, other)
    assert get_sharing(tmp_path, ROUTE)['configuration']['revision'] == 0


@pytest.mark.parametrize('body', [
    {'expectedRevision': 0},
    {'expectedRevision': 0, 'settings': settings(), 'extra': 1},
    {'expectedRevision': '0', 'settings': settings()},
    {'expectedRevision': -1, 'settings': settings()},
    {'expectedRevision': 2**53 - 1, 'settings': settings()},
    {'expectedRevision': 0, 'settings': 'not-a-dict'},
])
def test_change_rejects_malformed_requests(tmp_path, body):
    for action in ('issue', 'enable', 'revoke'):
        with pytest.raises(StoreError, match='invalid-request'):
            change_sharing(tmp_path, action, body, ROUTE)


@pytest.mark.parametrize('patch', [
    {'ttlSeconds': 59}, {'ttlSeconds': 365 * 86400 + 1}, {'maxConcurrent': 0},
    {'maxConcurrent': 9}, {'maxOutputTokens': 0}, {'deadlineSeconds': 3601},
    {'requestsPerMinute': 0}, {'label': ''}, {'label': '  padded  '},
])
def test_issue_validates_settings_bounds(tmp_path, patch):
    with pytest.raises(StoreError, match='invalid-request|invalid-config'):
        change_sharing(tmp_path, 'issue',
                       {'expectedRevision': 0, 'settings': settings(**patch)}, ROUTE)


def test_enable_disable_and_revoke_flow(tmp_path):
    change_sharing(tmp_path, 'issue', {'expectedRevision': 0, 'settings': settings()}, ROUTE)
    enabled = change_sharing(tmp_path, 'enable', {'expectedRevision': 1, 'enabled': True}, ROUTE)
    assert enabled['configuration']['enabled'] is True
    assert enabled['configuration']['revision'] == 2
    device_id = issued_device_id(tmp_path)
    revoked = change_sharing(tmp_path, 'revoke',
                           {'expectedRevision': 2, 'deviceId': device_id}, ROUTE)
    device = revoked['configuration']['devices'][0]
    assert device['revoked'] is True and revoked['configuration']['revision'] == 3
    disabled = change_sharing(tmp_path, 'enable', {'expectedRevision': 3, 'enabled': False}, None)
    assert disabled['configuration']['enabled'] is False


def test_enable_requires_route_only_when_turning_on(tmp_path):
    change_sharing(tmp_path, 'issue', {'expectedRevision': 0, 'settings': settings()}, ROUTE)
    with pytest.raises(StoreError, match='active-route-changed'):
        change_sharing(tmp_path, 'enable', {'expectedRevision': 1, 'enabled': True}, None)


def test_revoke_requires_a_real_device_id(tmp_path):
    change_sharing(tmp_path, 'issue', {'expectedRevision': 0, 'settings': settings()}, ROUTE)
    for bad in ('device-XYZ', 'device-' + '0' * 15, 'other-' + '0' * 16, 42):
        with pytest.raises(StoreError, match='invalid-request'):
            change_sharing(tmp_path, 'revoke', {'expectedRevision': 1, 'deviceId': bad}, ROUTE)
    with pytest.raises(StoreError, match='invalid-request'):
        change_sharing(tmp_path, 'revoke',
                       {'expectedRevision': 1, 'deviceId': 'device-' + 'f' * 16}, ROUTE)


def test_stale_revision_is_rejected_for_every_action(tmp_path):
    change_sharing(tmp_path, 'issue', {'expectedRevision': 0, 'settings': settings()}, ROUTE)
    device_id = issued_device_id(tmp_path)
    for action, body in (('issue', {'expectedRevision': 0, 'settings': settings()}),
                         ('enable', {'expectedRevision': 0, 'enabled': False}),
                         ('revoke', {'expectedRevision': 0, 'deviceId': device_id})):
        with pytest.raises(StoreError, match='stale-revision'):
            change_sharing(tmp_path, action, body, ROUTE)


def test_state_directory_is_owner_private(tmp_path):
    change_sharing(tmp_path, 'issue', {'expectedRevision': 0, 'settings': settings()}, ROUTE)
    directory = tmp_path / 'pixel-inference'
    assert directory.stat().st_mode & 0o777 == 0o700


def test_control_plane_rejects_non_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(api.os, 'name', 'nt')
    with pytest.raises(StoreError, match='unsupported-platform'):
        get_sharing(tmp_path, ROUTE)
    with pytest.raises(StoreError, match='unsupported-platform'):
        change_sharing(tmp_path, 'enable', {'expectedRevision': 0, 'enabled': False}, ROUTE)
