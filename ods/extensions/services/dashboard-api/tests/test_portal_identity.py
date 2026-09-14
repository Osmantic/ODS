"""Display identity contract and real owner HTTP/storage, not installed acceptance."""
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from portal_identity_contract import (
    default_document,
    normalize_document,
    normalize_edit,
    normalize_name,
)
from routers import portal_identity
from test_pixel_settings_host_integration import actual_stack  # noqa: F401


@pytest.fixture
def identity_stack(actual_stack):  # noqa: F811
    client, agent, handler = actual_stack
    client.app.include_router(portal_identity.router)
    return client, agent, handler


@pytest.mark.parametrize('raw,expected', [
    ('', 'Assistant'), ('   ', 'Assistant'), ('\u00a0', 'Assistant'), (' Nova ', 'Nova'),
    ('Cafe\u0301', 'Café'), ('👩\u200d💻', '👩\u200d💻'), ('  ' + 'A' * 60 + '  ', 'A' * 60),
    ('<img src=x onerror=alert(1)>', '<img src=x onerror=alert(1)>'),
])
def test_normalizes_display_text_only(raw, expected):
    assert normalize_name(raw) == expected


def test_historical_portal_name_remains_valid_owner_data():
    assert normalize_document({'schemaVersion': 1, 'revision': 4, 'displayName': 'Portal'})['displayName'] == 'Portal'


@pytest.mark.parametrize('raw', [None, True, 1, [], {}, 'x' * 61, ' ' * 241,
    '\nNova', 'Nova\t', '\u202eNova', '\ufeffNova', '\ud800', 'A\u2028B', 'A\u2029B'])
def test_rejects_invalid_or_ambiguous_names(raw):
    with pytest.raises(ValueError):
        normalize_name(raw)


@pytest.mark.parametrize('change', [{'schemaVersion': True}, {'revision': True}, {'revision': -1},
    {'revision': 2**53}, {'displayName': ' Nova '}, {'displayName': ''}, {'extra': 'private-sentinel'}])
def test_stored_identity_is_strict_and_never_silently_migrated(change):
    with pytest.raises(ValueError):
        normalize_document({**default_document(), **change})


def test_public_and_host_contract_copies_are_identical():
    api = Path(portal_identity.__file__).resolve().parent.parent
    host = api.parents[2] / 'bin/portal_identity_contract.py'
    assert (api / 'portal_identity_contract.py').read_bytes() == host.read_bytes()
    with pytest.raises(ValueError):
        normalize_edit({'expectedRevision': True, 'displayName': 'Nova'})


def test_pristine_get_and_invalid_input_do_not_write(identity_stack, tmp_path):
    client, _agent, handler = identity_stack
    response = client.get('/api/pixel/identity')
    assert response.status_code == 200 and response.json() == default_document()
    assert response.headers['cache-control'] == 'no-store'
    assert list(tmp_path.iterdir()) == []
    assert client.post('/api/pixel/identity/save', json={'expectedRevision': 0, 'displayName': '\u202eprivate'}).status_code == 400
    assert client.post('/api/pixel/identity/save', content=b'x' * 2049).status_code == 413
    assert client.post('/api/pixel/identity/save', content=b'{"expectedRevision":0,"displayName":"A","displayName":"B"}').status_code == 400
    assert client.get('/api/pixel/identity', headers={'Authorization': ''}).status_code == 401
    assert handler.posts == 0 and list(tmp_path.iterdir()) == []


def test_actual_save_fresh_read_reset_conflict_and_separate_storage(identity_stack, tmp_path):
    client, _agent, handler = identity_stack
    saved = client.post('/api/pixel/identity/save', json={'expectedRevision': 0, 'displayName': ' Cafe\u0301 '})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {'schemaVersion': 1, 'revision': 1, 'displayName': 'Café'}
    assert client.get('/api/pixel/identity').json() == saved.json()
    stale = client.post('/api/pixel/identity/save', json={'expectedRevision': 0, 'displayName': 'Other'})
    assert stale.status_code == 409
    assert client.get('/api/pixel/identity').json() == saved.json()
    reset = client.post('/api/pixel/identity/save', json={'expectedRevision': 1, 'displayName': ''})
    assert reset.status_code == 200 and reset.json()['displayName'] == 'Assistant'
    assert reset.json()['revision'] == 2
    assert handler.posts == 3
    assert json.loads((tmp_path / 'pixel-providers/portal-identity.json').read_bytes()) == reset.json()
    assert not (tmp_path / 'pixel-providers/pixel-settings.json').exists()
    assert not (tmp_path / 'pixel-providers/provider-config.json').exists()


def test_identity_does_not_rewrite_existing_runtime_settings(identity_stack, tmp_path):
    client, _agent, _handler = identity_stack
    settings = client.post('/api/pixel/settings/save', json={'expectedRevision': 0, 'changes': {'contextTokens': 65536}})
    assert settings.status_code == 200, settings.text
    path = tmp_path / 'pixel-providers/pixel-settings.json'
    before = path.read_bytes()
    assert client.post('/api/pixel/identity/save', json={'expectedRevision': 0, 'displayName': 'Nova'}).status_code == 200
    assert path.read_bytes() == before
    assert client.get('/api/pixel/settings').json() == settings.json()


def test_main_registers_owner_authenticated_identity_route(test_client):
    assert test_client.get('/api/pixel/identity').status_code == 401
    assert test_client.post('/api/pixel/identity/save', json={'expectedRevision': 0, 'displayName': 'Nova'}).status_code == 401


def test_lost_committed_response_does_not_replay_post(identity_stack, monkeypatch):
    client, agent, handler = identity_stack
    original = agent.json_response

    def drop_response(request, status, value, **options):
        if request.command == 'POST' and status == 200:
            request.close_connection = True
            request.connection.shutdown(socket.SHUT_RDWR)
            request.connection.close()
            return
        return original(request, status, value, **options)

    monkeypatch.setattr(agent, 'json_response', drop_response)
    result = client.post('/api/pixel/identity/save', json={'expectedRevision': 0, 'displayName': 'Nova'})
    assert result.status_code == 503 and handler.posts == 1
    assert client.get('/api/pixel/identity').json() == {'schemaVersion': 1, 'revision': 1, 'displayName': 'Nova'}


def test_concurrent_editors_cannot_overwrite_the_same_revision(identity_stack):
    client, _agent, _handler = identity_stack
    # Prepare the owner directory before exercising concurrent CAS, not setup races.
    assert client.post('/api/pixel/identity/save', json={'expectedRevision': 0, 'displayName': 'Initial'}).status_code == 200
    def save(name):
        return client.post('/api/pixel/identity/save', json={'expectedRevision': 1, 'displayName': name})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, ['A', 'B']))
    assert sorted(result.status_code for result in responses) == [200, 409]
    winner = next(result.json() for result in responses if result.status_code == 200)
    assert winner['revision'] == 2 and client.get('/api/pixel/identity').json() == winner


@pytest.mark.parametrize('raw', [b'not-json-private-sentinel', b'{"schemaVersion":1,"revision":1,"displayName":" Nova "}', b'\xff'])
def test_corrupt_store_is_not_silently_replaced_or_exposed(identity_stack, tmp_path, raw):
    client, _agent, _handler = identity_stack
    assert client.post('/api/pixel/identity/save', json={'expectedRevision': 0, 'displayName': 'Nova'}).status_code == 200
    path = tmp_path / 'pixel-providers/portal-identity.json'
    path.write_bytes(raw)
    for result in (client.get('/api/pixel/identity'), client.post('/api/pixel/identity/save', json={'expectedRevision': 0, 'displayName': 'Other'})):
        assert result.status_code == 503
        assert 'private-sentinel' not in result.text
    assert path.read_bytes() == raw
