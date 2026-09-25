"""Dashboard probes use the container listener when its published port changes."""

from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest
import yaml


def test_container_listener_matches_manifest_and_published_target():
    extension = Path(__file__).resolve().parents[2] / 'privacy-shield'
    service = yaml.safe_load((extension / 'manifest.yaml').read_text())['service']
    compose = yaml.safe_load((extension / 'compose.yaml').read_text())['services']['privacy-shield']
    environment = dict(item.split('=', 1) for item in compose['environment'])
    assert environment['SHIELD_PORT'] == str(service['port'])
    assert compose['ports'][0].endswith(f":{service['port']}")
    assert '${SHIELD_PORT:-8085}' in compose['ports'][0]


@pytest.mark.parametrize('endpoint', ['status', 'stats'])
def test_published_port_does_not_redirect_internal_shield_requests(test_client, monkeypatch, endpoint):
    monkeypatch.setenv('SHIELD_PORT', '18085')
    monkeypatch.setattr('routers.privacy.SERVICES', {
        'privacy-shield': {'host': 'privacy-shield', 'port': 8085, 'external_port': 18085},
    })
    response = AsyncMock()
    response.status = 200
    response.json.return_value = {'requests': 42}
    request_context = AsyncMock()
    request_context.__aenter__.return_value = response
    session = MagicMock()
    session.get.return_value = request_context
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session

    with patch('routers.privacy.aiohttp.ClientSession', return_value=session_context):
        result = test_client.get(f'/api/privacy-shield/{endpoint}', headers=test_client.auth_headers)

    assert result.status_code == 200
    path = 'health' if endpoint == 'status' else 'stats'
    assert session.get.call_args.args[0] == f'http://privacy-shield:8085/{path}'
    if endpoint == 'status':
        assert result.json()['port'] == 18085
        assert result.json()['enabled'] is True
    else:
        assert result.json() == {'requests': 42}
        assert session.get.call_args.kwargs['headers']['Authorization'] == 'Bearer test-shield-key-fixture'
