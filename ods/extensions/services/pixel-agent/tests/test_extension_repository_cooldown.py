import json
from unittest.mock import Mock

import pytest
from test_extension_manager import manager


def test_scoped_pin_preserves_rate_limit_without_dispatching_install(monkeypatch, tmp_path):
    identity = {'chatId': 'chat', 'requestId': 'turn'}
    current = {'schemaVersion': 1, **identity, 'repository': 'https://github.com/o/r',
               'state': 'pending', 'authorizationMode': 'install', 'installationStarted': False}
    responses = Mock(side_effect=[(200, current),
                                  (429, {'detail': {'code': 'github-rate-limited', 'retryAfter': 120}})])
    monkeypatch.setattr(manager, '_read_env', lambda path: {'DASHBOARD_API_KEY': 'a' * 64})
    monkeypatch.setattr(manager, '_request_json', responses)
    mutation = Mock(side_effect=AssertionError('pin cannot install'))
    monkeypatch.setattr(manager, '_mutate', mutation)
    receipt = manager._pin_request_repository(tmp_path / '.env', 3002,
        json.dumps({'schemaVersion': 1, 'action': 'github-request-pin', **identity}).encode())
    assert receipt == {'schemaVersion': 1, 'kind': 'ods-extension-request-repository-unavailable',
                       **identity, 'repository': current['repository'], 'reason': 'github-rate-limited',
                       'retryAfter': 120, 'installationStarted': False}
    mutation.assert_not_called()


@pytest.mark.parametrize('retry_after', [True, 0, 3601, '120'])
def test_malformed_cooldown_is_not_exposed_as_a_verified_receipt(monkeypatch, tmp_path, retry_after):
    monkeypatch.setattr(manager, '_read_env', lambda path: {'DASHBOARD_API_KEY': 'a' * 64})
    monkeypatch.setattr(manager, '_request_json', lambda **kwargs: (
        429, {'detail': {'code': 'github-rate-limited', 'retryAfter': retry_after}}))
    with pytest.raises(manager.ManagerError) as failure:
        manager._inspect_repository(tmp_path / '.env', 3002, 'https://github.com/o/r')
    assert not isinstance(failure.value, manager.RepositoryRateLimitError)
