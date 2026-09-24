"""Authenticated portable exports use exact n8n identity and never mutate it."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.parametrize('mode,expected', [('ok', 200), ('missing', 404), ('denied', 502), ('malformed', 502), ('timeout', 504)])
def test_export_boundary(test_client, monkeypatch, mode, expected):
    from routers import workflows
    source = {'id': 'wf-42', 'name': 'Saved workflow', 'nodes': [{'id': 'a', 'parameters': {'text': 'Hello'}}], 'connections': {}, 'settings': {'executionOrder': 'v1'}, 'active': True, 'staticData': {'cursor': 'private-runtime-state'}, 'shared': [{'userId': 'owner'}]}
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    response = MagicMock(status={'missing': 404, 'denied': 401}.get(mode, 200))
    response.json = AsyncMock(return_value=[] if mode == 'malformed' else source)
    session.get.return_value.__aenter__ = AsyncMock(return_value=response)
    session.get.return_value.__aexit__ = AsyncMock(return_value=False)
    if mode == 'timeout':
        session.get.return_value.__aenter__.side_effect = asyncio.TimeoutError
    monkeypatch.setattr(workflows.aiohttp, 'ClientSession', lambda **kwargs: session)
    monkeypatch.setattr(workflows, 'N8N_API_KEY', 'server-only-key')
    result = test_client.get('/api/workflows/n8n/wf-42/export', headers=test_client.auth_headers)
    assert result.status_code == expected
    session.get.assert_called_once_with(f'{workflows.N8N_URL}/api/v1/workflows/wf-42', headers={'X-N8N-API-KEY': 'server-only-key'}, params={'excludePinnedData': 'true'}, allow_redirects=False)
    session.post.assert_not_called()
    session.patch.assert_not_called()
    assert 'server-only-key' not in result.text
    if expected == 200:
        assert result.json() == {key: source[key] for key in ('name', 'nodes', 'connections', 'settings')}
        assert source['active'] is True
        assert result.headers['cache-control'] == 'no-store'
        assert result.headers['content-disposition'] == 'attachment; filename="ods-workflow-wf-42.json"'


def test_export_rejects_unauthenticated_and_invalid_identity(test_client):
    assert test_client.get('/api/workflows/n8n/wf-42/export').status_code == 401
    assert test_client.get('/api/workflows/n8n/bad%20id/export', headers=test_client.auth_headers).status_code == 400
