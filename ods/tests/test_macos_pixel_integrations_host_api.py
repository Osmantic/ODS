"""Exercise the actual host-agent functions with disposable authenticated-channel adapters."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'bin/ods-host-agent.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))


def function(name):
    for item in TREE.body:
        if isinstance(item, ast.FunctionDef) and item.name == name:
            return item
        if isinstance(item, ast.ClassDef) and item.name == 'AgentHandler':
            for method in item.body:
                if isinstance(method, ast.FunctionDef) and method.name == name:
                    return method
    raise AssertionError(name)


def execute(name, namespace):
    code = ast.Module(body=[function(name)], type_ignores=[])
    exec(compile(ast.fix_missing_locations(code), str(SOURCE), 'exec'), namespace)
    return namespace[name]


def test_darwin_host_client_uses_only_fixed_root_published_authenticated_transport(monkeypatch):
    selected = []
    body = b'def native_owner_request(path, payload):\n return {"ok": True, "path": path, "request": payload}\n'
    def protected(path, **kwargs):
        selected.append((path, kwargs))
        return body
    monkeypatch.setitem(sys.modules, 'pixel_macos_custody', SimpleNamespace(protected_bytes=protected))
    namespace = {'sys': SimpleNamespace(platform='darwin'), 'json': json,
                 'PIXEL_INTEGRATIONS_SOCKET': '/Users/fixture/.config/ods/pixel-integrations/owner.sock',
                 'socket': SimpleNamespace(socket=lambda *args: pytest.fail('native failure fell back to bare Unix transport'))}
    request = execute('_pixel_integrations_request', namespace)
    result = request({'action': 'get'})
    assert result == {'ok': True, 'path': namespace['PIXEL_INTEGRATIONS_SOCKET'], 'request': {'schemaVersion': 1, 'action': 'get'}}
    assert selected == [('/usr/local/libexec/ods-pixel-services/integrations/integrations_common.py', {'limit': 1024 * 1024})]


@pytest.mark.parametrize('failure', [OSError('missing'), ValueError('changed custody')])
def test_native_custody_failure_has_no_legacy_fallback(monkeypatch, failure):
    def refuse(*args, **kwargs):
        raise failure
    monkeypatch.setitem(sys.modules, 'pixel_macos_custody', SimpleNamespace(protected_bytes=refuse))
    namespace = {'sys': SimpleNamespace(platform='darwin'), 'json': json,
                 'socket': SimpleNamespace(socket=lambda *args: pytest.fail('custody refusal bypassed owner authentication'))}
    request = execute('_pixel_integrations_request', namespace)
    with pytest.raises(type(failure)):
        request({'action': 'save'})


@pytest.mark.parametrize('authorized', [False, True])
def test_native_owner_http_authentication_precedes_channel_and_body_reads(authorized):
    requests, responses = [], []
    namespace = {'check_auth': lambda _handler: authorized,
        'json_response': lambda handler, status, body, **kwargs: responses.append((status, body, kwargs)),
        'read_json_body': lambda _handler: {'expectedRevision': 7, 'settings': {}, 'action': 'decide'},
        '_pixel_integrations_request': lambda payload: requests.append(payload) or {'ok': True, 'settings': {'revision': 8}}}
    handle = execute('_handle_pixel_integrations', namespace)
    handle(object(), 'save')
    if authorized:
        assert requests == [{'expectedRevision': 7, 'settings': {}, 'action': 'save'}]
        assert responses == [(200, {'settings': {'revision': 8}}, {'no_store': True})]
    else:
        assert requests == [] and responses == []


@pytest.mark.parametrize('reply,status', [({'ok': False, 'error': 'stale-revision', 'message': 'Reload.'}, 409),
    ({'ok': False, 'error': 'forbidden', 'message': 'Owner authentication failed.'}, 400), (None, 503)])
def test_native_refusals_and_absent_service_remain_visible_without_fake_success(reply, status):
    responses = []
    def request(_payload):
        if reply is None:
            raise OSError('no authenticated endpoint')
        return reply
    handle = execute('_handle_pixel_integrations', {'check_auth': lambda _handler: True,
        'json_response': lambda _handler, code, body, **kwargs: responses.append((code, body)),
        '_pixel_integrations_request': request})
    handle(object())
    assert responses[0][0] == status
    assert 'error' in responses[0][1]
