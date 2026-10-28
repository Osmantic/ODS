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


def _github(monkeypatch, response=None, error=None):
    """Answer every GitHub request with one response or error, and count them."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    if error is not None:
        client.get.side_effect = error
    else:
        client.get.return_value = response
    monkeypatch.setattr(updates.httpx, 'AsyncClient', lambda **_: client)
    return client.get


def _hold_seconds() -> float:
    until = updates._version_hold['until']
    assert isinstance(until, float)
    return until - time.monotonic()


def _response(status, headers=None, data=None):
    return httpx.Response(status, json=data if data is not None else {'message': 'x'}, headers=headers or {},
                          request=httpx.Request('GET', updates._GITHUB_RELEASES_API))


@pytest.mark.parametrize('status,headers,expected', [
    (403, {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': 'RESET+600'}, 600),
    (403, {'retry-after': '30'}, 30), (429, {'retry-after': '120'}, 120),
    (429, {}, 0), (403, {}, None), (404, {}, None), (500, {'retry-after': '30'}, None),
])
def test_rate_limit_seconds_reads_github_headers(status, headers, expected):
    now = int(time.time())
    headers = {k: str(now + 600) if v == 'RESET+600' else v for k, v in headers.items()}
    seconds = updates._rate_limit_seconds(_response(status, headers))
    if expected is None:
        assert seconds is None
    else:
        assert seconds is not None and abs(seconds - expected) <= 2


def test_rate_limited_check_holds_every_request_until_github_resets(monkeypatch):
    # Workstation06 (2026-10-27): the site's shared address had spent GitHub's
    # 60 unauthenticated requests, and each dashboard poll asked again.
    monkeypatch.setattr(updates, '_read_current_version', lambda: '3.0.0')
    reset = str(int(time.time()) + 1200)
    get = _github(monkeypatch, _response(403, {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': reset}))

    assert asyncio.run(updates.get_version())['check_status'] == 'unavailable'
    assert get.await_count == 1
    assert updates._version_hold['rate_limited'] is True
    assert 1100 < _hold_seconds() <= 1200

    for force in (False, True, False):
        assert asyncio.run(updates.get_version(force=force))['check_status'] == 'unavailable'
    assert get.await_count == 1


def test_failed_check_holds_polls_but_a_manual_check_retries(monkeypatch):
    monkeypatch.setattr(updates, '_read_current_version', lambda: '3.0.0')
    get = _github(monkeypatch, error=httpx.ConnectError('offline'))

    asyncio.run(updates.get_version())
    asyncio.run(updates.get_version())
    assert get.await_count == 1
    assert updates._version_hold['rate_limited'] is False

    asyncio.run(updates.get_version(force=True))
    assert get.await_count == 2


def test_hold_is_capped_at_an_hour_and_cleared_by_a_good_check(monkeypatch):
    monkeypatch.setattr(updates, '_read_current_version', lambda: '3.0.0')
    reset = str(int(time.time()) + 7200)
    _github(monkeypatch, _response(403, {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': reset}))
    asyncio.run(updates.get_version())
    assert _hold_seconds() <= 3600

    monkeypatch.setattr(updates, '_version_hold', {'until': 0.0, 'rate_limited': True})
    _github(monkeypatch, _response(200, data={'tag_name': 'v3.0.0', 'html_url': 'https://github.com/x'}))
    assert asyncio.run(updates.get_version())['check_status'] == 'checked'
    assert updates._version_hold == {'until': 0.0, 'rate_limited': False}


def _owner_only_env(monkeypatch, tmp_path, text):
    """The install owner is not UID 1000, so this container cannot read the 0600 .env."""
    (tmp_path / '.env').write_text(text)
    monkeypatch.setattr(updates, 'INSTALL_DIR', str(tmp_path))
    real_read = updates._read_utf8

    def read(path):
        if path.name == '.env':
            raise PermissionError(13, 'Permission denied', str(path))
        return real_read(path)

    monkeypatch.setattr(updates, '_read_utf8', read)


def test_offline_switch_holds_when_the_env_file_is_owner_only(monkeypatch, tmp_path):
    _owner_only_env(monkeypatch, tmp_path, 'DISABLE_UPDATE_CHECK=true\n')
    monkeypatch.setenv('DISABLE_UPDATE_CHECK', 'true')
    assert updates._update_check_disabled() is True
    monkeypatch.setenv('DISABLE_UPDATE_CHECK', 'false')
    assert updates._update_check_disabled() is False


def test_dry_run_reports_an_owner_only_env_instead_of_failing(test_client, monkeypatch, tmp_path):
    # Workstation06 (2026-10-27): this read raised PermissionError and the dry run answered 500.
    _owner_only_env(monkeypatch, tmp_path, 'ODS_VERSION=3.0.0\nTIER=4\n')
    monkeypatch.delenv('DISABLE_UPDATE_CHECK', raising=False)
    _github(monkeypatch, _response(200, data={'tag_name': 'v3.0.0', 'html_url': 'https://github.com/x'}))

    resp = test_client.get('/api/update/dry-run', headers=test_client.auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data['env_keys'] is None
    assert data['env_keys_error'] == "The install's .env is readable only by its owner"
    assert data['latest_version'] == '3.0.0'


def test_dry_run_honors_the_offline_switch(test_client, monkeypatch, tmp_path):
    (tmp_path / '.env').write_text('ODS_VERSION=3.0.0\nDISABLE_UPDATE_CHECK=true\n')
    monkeypatch.setattr(updates, 'INSTALL_DIR', str(tmp_path))
    get = _github(monkeypatch, error=AssertionError('GitHub must not be contacted when update checks are off'))

    data = test_client.get('/api/update/dry-run', headers=test_client.auth_headers).json()

    assert get.await_count == 0
    assert data['latest_version'] is None
    assert data['version_check_error'] == 'Update checks are off (DISABLE_UPDATE_CHECK)'
    assert data['env_keys'] == {'ODS_VERSION': '3.0.0'}


def test_dry_run_shares_the_dashboard_cache_and_rate_limit_hold(test_client, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, 'INSTALL_DIR', str(tmp_path))
    get = _github(monkeypatch, error=AssertionError('the cached release answers the dry run'))
    monkeypatch.setattr(updates, '_version_cache', {'expires_at': time.monotonic() + 100,
                                                    'payload': {'latest': '9.0.0', 'changelog_url': None}})
    assert test_client.get('/api/update/dry-run', headers=test_client.auth_headers).json()['latest_version'] == '9.0.0'

    monkeypatch.setattr(updates, '_version_cache', {'expires_at': 0.0, 'payload': None})
    monkeypatch.setattr(updates, '_version_hold', {'until': time.monotonic() + 600, 'rate_limited': True})
    data = test_client.get('/api/update/dry-run', headers=test_client.auth_headers).json()
    assert get.await_count == 0
    assert data['latest_version'] is None
    assert data['version_check_error'] == "GitHub's request limit for this network is used up; try again in 10 min"


def test_a_held_check_says_why_it_has_no_release(test_client, monkeypatch):
    # Settings told the owner to "try again", which the hold refuses for up to an hour.
    monkeypatch.setattr(updates, '_read_current_version', lambda: '3.0.0')
    reset = str(int(time.time()) + 600)
    _github(monkeypatch, _response(403, {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': reset}))

    data = test_client.get('/api/version?force=true', headers=test_client.auth_headers).json()

    assert data['check_status'] == 'unavailable'
    assert data['check_error'] == "GitHub's request limit for this network is used up; try again in 10 min"

    monkeypatch.setattr(updates, '_version_hold', {'until': 0.0, 'rate_limited': False})
    _github(monkeypatch, _response(200, data={'tag_name': 'v3.0.0', 'html_url': 'https://github.com/x'}))
    data = test_client.get('/api/version?force=true', headers=test_client.auth_headers).json()
    assert data['check_status'] == 'checked'
    assert data['check_error'] is None
