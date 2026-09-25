"""Version evidence and network failures must never manufacture an update."""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

import routers.updates as updates


def test_blank_env_falls_back_to_installed_receipt(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('ODS_VERSION=""\n')
    (tmp_path / '.version').write_text(json.dumps({'version': '2.6.0'}))
    monkeypatch.setattr(updates, 'INSTALL_DIR', tmp_path)
    assert updates._read_current_version() == '2.6.0'
    assert not updates._build_version_result(updates._read_current_version(), {'latest': '2.6.0'})['update_available']


def test_manifest_schema_number_is_not_an_installed_release(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('ODS_VERSION=\n')
    (tmp_path / 'manifest.json').write_text(json.dumps({'manifestVersion': '99.0.0'}))
    monkeypatch.setattr(updates, 'INSTALL_DIR', tmp_path)
    assert updates._read_current_version() not in ('', '99.0.0', '0.0.0')


@pytest.mark.parametrize('current,latest,available', [
    ('2.6.0', '2.6.0', False), ('2.7.0', '2.6.0', False),
    ('2.6.0-rc.1', '2.6.0', True), ('2.7.0-beta.1', '2.6.0', False),
    ('2.6.0+38ecf388', '2.6.0', False), ('v2.6.0', 'v2.10.0', True),
    ('', '2.6.0', False), ('0.0.0', '2.6.0', False),
    ('public-beta', '2.6.0', False), ("2.6.0'", '2.6.0', False),
    ('2.6.0', 'invalid', False),
])
def test_semver_comparison(current, latest, available):
    assert updates._build_version_result(current, {'latest': latest})['update_available'] is available


def test_unknown_or_failed_check_has_no_invented_timestamp():
    assert updates._build_version_result('2.6.0', None)['checked_at'] is None
    assert updates._build_version_result('2.6.0', None)['check_status'] == 'unavailable'
    assert updates._build_version_result('2.6.0', None, 'checking')['check_status'] == 'checking'


@pytest.mark.parametrize('status,data', [(429, {'message': 'Rate limit exceeded'}), (200, []),
    (200, {'tag_name': 'v9.0.0', 'draft': True}), (200, {'tag_name': 'v9.0.0-beta.1'})])
def test_invalid_release_does_not_replace_known_cache(monkeypatch, status, data):
    cached = {'latest': '2.6.0', 'checked_at': '2026-09-16T00:00:00+00:00'}
    monkeypatch.setattr(updates, '_version_cache', {'expires_at': 0, 'payload': cached})
    response = httpx.Response(status, json=data, request=httpx.Request('GET', updates._GITHUB_RELEASES_API))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response
    monkeypatch.setattr(updates.httpx, 'AsyncClient', lambda **_: client)
    assert asyncio.run(updates._refresh_release_cache()) is None
    assert updates._version_cache['payload'] == cached


def test_manual_check_bypasses_fresh_cache_and_confirms_release(monkeypatch):
    monkeypatch.setattr(updates, '_version_cache', {'expires_at': time.monotonic() + 100, 'payload': {'latest': '2.6.0'}})
    monkeypatch.setattr(updates, '_version_refresh_task', None)
    monkeypatch.setattr(updates, '_read_current_version', lambda: '2.6.0')
    refresh = AsyncMock(return_value={'latest': '2.7.0', 'checked_at': '2026-09-16T00:00:00+00:00'})
    monkeypatch.setattr(updates, '_refresh_release_cache', refresh)
    result = asyncio.run(updates.get_version(force=True))
    assert result['update_available'] and result['check_status'] == 'checked'
    refresh.assert_awaited_once()


def test_failed_forced_check_does_not_announce_cached_release(monkeypatch):
    monkeypatch.setattr(updates, '_version_cache', {'expires_at': time.monotonic() + 100, 'payload': {'latest': '2.7.0'}})
    monkeypatch.setattr(updates, '_version_refresh_task', None)
    monkeypatch.setattr(updates, '_read_current_version', lambda: '2.6.0')
    monkeypatch.setattr(updates, '_refresh_release_cache', AsyncMock(return_value=None))
    result = asyncio.run(updates.get_version(force=True))
    assert result['latest'] == '2.7.0' and result['check_status'] == 'stale'
    assert not result['update_available']
