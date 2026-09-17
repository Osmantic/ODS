"""Contract tests for pixel_settings/public.py + pixel_settings/host_api.py.

`host_api` is the owner-facing settings boundary used by `ods-host-agent.py`:
document persistence via the qualified store (`get_settings`/`save_settings`)
and the runtime lifecycle envelopes (`runtime_status`/`runtime_change`) that
talk to the access controller over the owner channel. `public.py` is the strict
nonsecret envelope validator shared by both sides.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_provider.store import StoreError
from pixel_settings import host_api, public
from pixel_settings.contract import SettingsError

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX store custody')

HEX = 'b' * 64
CAPS = {
    'providerContextTokens': 131072, 'providerMaxOutputTokens': 8192,
    'activeContextTokens': 32768, 'activeMaxOutputTokens': 4096,
    'backendContextTokens': 65536, 'capacitySource': 'backend-observed',
    'supportedThinkingLevels': ['off', 'low', 'medium'],
    'samplingSupported': True, 'pixelOnlyRuntime': True,
}


def runtime(status='applied', **changes):
    value = {
        'schemaVersion': 1, 'status': status, 'revision': HEX, 'settingsRevision': 3,
        'appliedRevision': 3, 'capabilities': dict(CAPS), 'pending': False,
        'lastVerifiedAt': '2026-09-17T04:05:06Z', 'reason': None,
    }
    value.update(changes)
    return value


class TestPublicEnvelopes:
    def test_normalize_change(self):
        result = public.normalize_change(
            {'operation': 'apply', 'revision': HEX, 'settingsRevision': 2})
        assert result['operation'] == 'apply'
        for bad in ({'operation': 'deactivate', 'revision': HEX, 'settingsRevision': 2},
                    {'operation': 'apply', 'revision': 'x', 'settingsRevision': 2},
                    {'operation': 'apply', 'revision': HEX, 'settingsRevision': -1},
                    {'operation': 'apply', 'revision': HEX}):
            with pytest.raises(SettingsError, match='invalid-settings-request'):
                public.normalize_change(bad)

    def test_normalize_outcome(self):
        assert public.normalize_outcome(
            {'outcome': 'applied', 'appliedRevision': 4})['appliedRevision'] == 4
        assert public.normalize_outcome(
            {'outcome': 'rolled-back', 'appliedRevision': None})['outcome'] == 'rolled-back'
        for bad in ({'outcome': 'applied', 'appliedRevision': None},
                    {'outcome': 'failed', 'appliedRevision': 1},
                    {'outcome': 'applied', 'appliedRevision': 'x'}):
            with pytest.raises(SettingsError, match='invalid-settings-response'):
                public.normalize_outcome(bad)

    def test_unavailable_shape(self):
        result = public.unavailable('settings-controller-unavailable')
        assert set(result) == public.KEYS and result['status'] == 'unavailable'
        assert all(result[k] is None for k in public.KEYS
                   - {'schemaVersion', 'status', 'reason'})

    @pytest.mark.parametrize('status', ['applied', 'saved-changes', 'restored',
                                        'not-applied', 'pending', 'unavailable'])
    def test_states_accepted(self, status):
        if status == 'pending':
            value = runtime(status='pending', appliedRevision=None, capabilities=None,
                            lastVerifiedAt=None, pending=True)
        elif status == 'unavailable':
            value = public.unavailable('some-reason')
        elif status == 'not-applied':
            value = runtime(status='not-applied', appliedRevision=None, lastVerifiedAt=None)
        elif status == 'restored':
            value = runtime(status='restored', appliedRevision=None)
        elif status == 'saved-changes':
            value = runtime(status='saved-changes', appliedRevision=2)
        else:
            value = runtime()
        assert public.normalize_runtime(value)['status'] == status

    def test_applied_revision_equivalence(self):
        assert public.normalize_runtime(runtime(appliedRevision=3))
        with pytest.raises(SettingsError):
            public.normalize_runtime(runtime(appliedRevision=2))
        assert public.normalize_runtime(runtime(status='saved-changes', appliedRevision=2))
        with pytest.raises(SettingsError):
            public.normalize_runtime(runtime(status='saved-changes', appliedRevision=3))

    def test_unavailable_reason_must_be_slug(self):
        value = public.unavailable('ok-reason')
        value['reason'] = 'Not A Slug!'
        with pytest.raises(SettingsError, match='invalid-settings-response'):
            public.normalize_runtime(value)
        value['reason'] = 'x' * 97
        with pytest.raises(SettingsError):
            public.normalize_runtime(value)

    def test_capabilities_validated(self):
        for bad_caps in (None, {}, dict(CAPS, capacitySource='self-reported'),
                         dict(CAPS, activeMaxOutputTokens=999999),
                         dict(CAPS, supportedThinkingLevels=['low', 'low']),
                         dict(CAPS, backendContextTokens=None)):
            value = runtime(capabilities=bad_caps)
            with pytest.raises(SettingsError, match='invalid-settings-response|invalid-runtime'):
                public.normalize_runtime(value)

    def test_pending_forbids_verification_evidence(self):
        with pytest.raises(SettingsError):
            public.normalize_runtime(runtime(status='pending', pending=True,
                                             capabilities=None, appliedRevision=None,
                                             lastVerifiedAt='2026-09-17T00:00:00Z'))


class TestHostApiDocuments:
    def test_get_settings_default_has_runtime_marker(self, tmp_path):
        result = host_api.get_settings(tmp_path)
        assert result['runtime'] == {'status': 'not-inspected',
                                     'reason': 'runtime-status-separate'}
        assert result['configuration']['revision'] == 0
        assert not (tmp_path / 'pixel-providers').exists()

    def test_save_settings_roundtrip_and_cas(self, tmp_path):
        body = {'expectedRevision': 0, 'changes': {'contextTokens': 65536,
                                                   'thinking': 'low'}}
        saved = host_api.save_settings(tmp_path, body)
        assert saved['configuration']['revision'] == 1
        assert saved['configuration']['preferences']['contextTokens'] == 65536
        with pytest.raises(StoreError, match='stale-revision'):
            host_api.save_settings(tmp_path, body)
        again = host_api.save_settings(
            tmp_path, {'expectedRevision': 1, 'changes': {'thinking': None}})
        assert again['configuration']['revision'] == 2
        assert 'thinking' not in again['configuration']['preferences'] or \
            again['configuration']['preferences']['thinking'] is None

    @pytest.mark.parametrize('body', [
        {}, {'expectedRevision': 0}, {'changes': {}},
        {'expectedRevision': '0', 'changes': {}},
        {'expectedRevision': -1, 'changes': {}},
        {'expectedRevision': 0, 'changes': {}, 'x': 1},
        {'expectedRevision': 0, 'changes': {'not-a-control': 1}},
        {'expectedRevision': 0, 'changes': {'contextTokens': 100}},
        {'expectedRevision': 0, 'changes': {'temperature': 3}},
    ])
    def test_invalid_requests_rejected(self, tmp_path, body):
        with pytest.raises(StoreError, match='invalid-request'):
            host_api.save_settings(tmp_path, body)


class TestHostApiRuntime:
    def test_status_non_linux_unavailable(self, tmp_path, monkeypatch):
        for system, reason in (('Darwin', 'macos-launchd-adapter-missing'),
                               ('Windows', 'native-windows-adapter-missing')):
            monkeypatch.setattr(host_api.platform, 'system', lambda s=system: s)
            result = host_api.runtime_status(tmp_path)
            assert result['status'] == 'unavailable' and result['reason'] == reason

    def test_status_200_wraps_controller_document(self, tmp_path, monkeypatch):
        monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
        document = {k: v for k, v in runtime().items()
                    if k not in ('schemaVersion', 'reason')}
        result = host_api.runtime_status(
            tmp_path, request=lambda *a, **k: (200, dict(document)))
        assert result['status'] == 'applied' and result['schemaVersion'] == 1

    def test_status_controller_error_reason_forwarded_when_slug(self, tmp_path, monkeypatch):
        monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
        result = host_api.runtime_status(
            tmp_path, request=lambda *a, **k: (503, {'error': 'runtime-busy'}))
        assert result == public.unavailable('runtime-busy')

    def test_status_foreign_error_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
        result = host_api.runtime_status(
            tmp_path, request=lambda *a, **k: (500, {'error': 'Sensitive detail!'}))
        assert result == public.unavailable('settings-controller-unavailable')

    def test_status_transport_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
        def boom(*a, **k):
            raise OSError('refused')
        assert host_api.runtime_status(tmp_path, request=boom) == \
            public.unavailable('settings-controller-unavailable')

    def test_change_validates_then_applies(self, tmp_path, monkeypatch):
        monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
        body = {'operation': 'apply', 'revision': HEX, 'settingsRevision': 7}
        calls = []
        def request(operation, payload, **kwargs):
            calls.append((operation, payload))
            return 200, {'outcome': 'applied', 'appliedRevision': 7}
        result = host_api.runtime_change(tmp_path, body, request=request)
        assert result['outcome'] == 'applied'
        assert calls == [('settings-change', body)]

    def test_change_applied_revision_mismatch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
        body = {'operation': 'apply', 'revision': HEX, 'settingsRevision': 7}
        with pytest.raises(SettingsError, match='settings-runtime-revision-mismatch'):
            host_api.runtime_change(
                tmp_path, body,
                request=lambda *a, **k: (200, {'outcome': 'applied', 'appliedRevision': 9}))

    def test_change_controller_error_becomes_store_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
        body = {'operation': 'apply', 'revision': HEX, 'settingsRevision': 7}
        with pytest.raises(StoreError, match='runtime-busy'):
            host_api.runtime_change(tmp_path, body,
                                    request=lambda *a, **k: (409, {'error': 'runtime-busy'}))

    def test_change_non_linux_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(host_api.platform, 'system', lambda: 'Darwin')
        body = {'operation': 'apply', 'revision': HEX, 'settingsRevision': 7}
        with pytest.raises(StoreError, match='settings-platform-unavailable'):
            host_api.runtime_change(tmp_path, body, request=lambda *a, **k: (200, {}))

    def test_change_invalid_body(self, tmp_path, monkeypatch):
        monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
        with pytest.raises(StoreError, match='invalid-request'):
            host_api.runtime_change(tmp_path, {'operation': 'delete', 'revision': HEX,
                                               'settingsRevision': 1},
                                    request=lambda *a, **k: (200, {}))
