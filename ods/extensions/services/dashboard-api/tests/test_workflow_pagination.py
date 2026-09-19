"""Exercise paginated n8n inventory through the authenticated catalog endpoint."""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.parametrize('second_status', [200, 503])
def test_workflow_catalog_reads_complete_inventory(test_client, monkeypatch, second_status):
    from routers import workflows

    catalog = {'workflows': [
        {'id': name, 'name': name, 'description': name} for name in ('Alpha', 'Beta')
    ]}
    monkeypatch.setattr(workflows, 'load_workflow_catalog', lambda: catalog)
    monkeypatch.setattr(workflows, 'check_n8n_available', AsyncMock(return_value=True))
    pages = [
        {'data': [{'id': 'first', 'name': 'Alpha', 'active': True}], 'nextCursor': 'opaque+/='},
        {'data': [{'id': 'second', 'name': 'Beta', 'active': True}], 'nextCursor': None},
    ]
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    calls = []

    def get(url, **kwargs):
        index = len(calls)
        calls.append((url, kwargs))
        assert index < 2
        response = MagicMock(status=200 if index == 0 else second_status)
        response.json = AsyncMock(return_value=pages[index])
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        return context

    session.get.side_effect = get
    monkeypatch.setattr(workflows.aiohttp, 'ClientSession', lambda **kwargs: session)
    response = test_client.get('/api/workflows', headers=test_client.auth_headers)
    assert response.status_code == 200
    installed = [item['n8nId'] for item in response.json()['workflows']]
    assert installed == (['first', 'second'] if second_status == 200 else [None, None])
    assert len(calls) == 2
    assert calls[1][1]['params'] == {'cursor': 'opaque+/='}
