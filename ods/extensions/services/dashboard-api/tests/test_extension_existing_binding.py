import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, Mock
from pathlib import Path
import shutil

import pytest
import yaml
from starlette.requests import Request

from extension_requests import create_request, read_request, cancel_request
from extension_existing_binding import integration_identity
from extension_install_plan import build_install_plan
from routers import extensions
from test_extension_recipe_validation import candidate


def request(payload):
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(payload).encode()}
    return Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)


@pytest.fixture
def existing(monkeypatch, tmp_path):
    roots = [tmp_path / name for name in ('user', 'builtin', 'library')]
    for root in roots:
        root.mkdir()
    schema = tmp_path / 'schema'; schema.mkdir()
    shutil.copyfile(Path(extensions.__file__).resolve().parents[3] / 'schema/service-manifest.v1.json',
                    schema / 'service-manifest.v1.json')
    manifest = candidate()['manifest']
    manifest['service']['id'] = 'example'
    package = roots[2] / 'example'; package.mkdir()
    for name, content in {'manifest.yaml': yaml.safe_dump(manifest), 'compose.yaml': 'services: {}',
                          'upstream.json': json.dumps({'repository': 'https://github.com/owner/repo', 'origin':'github-proposal'})}.items():
        (package / name).write_text(content, encoding='utf-8')
    directory = tmp_path / '.extension-requests'; directory.mkdir()
    create_request(directory, 'owner', 'chat', 'original', '/extensions https://github.com/owner/repo')
    for key, root in zip(('USER_EXTENSIONS_DIR', 'EXTENSIONS_DIR', 'EXTENSIONS_LIBRARY_DIR'), roots):
        monkeypatch.setattr(extensions, key, root)
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'extension_detail', AsyncMock(return_value={'status': 'not_installed'}))
    return roots, package, directory, {'chatId': 'chat', 'requestId': 'original'}


def test_existing_binding_persists_scope_without_new_recipe_or_install(monkeypatch, existing):
    roots, package, directory, identity = existing
    bind = lambda: asyncio.run(extensions.extension_github_prepare_request(
        request({**identity, 'extensionId': 'example'}), api_key='owner'))
    first = json.loads(bind().body)
    assert first['kind'] == 'ods-extension-request-binding'
    assert first['installationStarted'] is False and first['runtimeVerified'] is False
    assert json.loads(bind().body) == first
    recovered = asyncio.run(extensions.extension_github_prepare_request(request(identity), api_key='owner'))
    assert json.loads(recovered.body) == first
    saved = read_request(directory, 'owner', **{'chat_id':'chat','request_id':'original'})
    assert saved['integration']['extensionId'] == 'example' and 'proposal' not in saved
    assert extensions._bound_prepared_request('owner', identity) == 'example'
    status = asyncio.run(extensions._observe_extension_request(identity, 'owner'))
    assert status['integrationBound'] and status['prepared'] and not status['proposalAccepted']
    assert status['runtimeStatus'] == 'not_installed'
    context = asyncio.run(extensions.chat_extension_request_context('owner', 'chat', 'next', 'sim'))
    assert '"requestId": "original"' in context['content'] and '"integrationBound": true' in context['content']
    # Lifecycle filename changes do not select a different definition.
    (package / 'compose.yaml').rename(package / 'compose.yaml.disabled')
    assert extensions._bound_prepared_request('owner', identity) == 'example'
    # Installation copies the same definition to the higher-priority user root.
    shutil.copytree(package, roots[0] / 'example')
    extensions._write_library_receipt(roots[0] / 'example', source_digest='a'*64, installed_digest='b'*64)
    assert extensions._bound_prepared_request('owner', identity) == 'example'
    (roots[0] / 'example/compose.yaml.disabled').write_text('services: {changed: {}}')
    with pytest.raises(ValueError): extensions._bound_prepared_request('owner', identity)
    with pytest.raises(extensions.HTTPException): bind()


def test_binding_rejects_wrong_owner_repository_cancel_and_ambiguous_files(existing):
    roots, package, directory, identity = existing
    payload = {**identity, 'extensionId': 'example'}
    with pytest.raises(extensions.HTTPException):
        asyncio.run(extensions.extension_github_prepare_request(request(payload), api_key='other'))
    (package / 'compose.yaml.disabled').write_text('services: {}')
    with pytest.raises(ValueError): integration_identity('https://github.com/owner/repo', 'example', roots)
    (package / 'compose.yaml.disabled').unlink()
    with pytest.raises(ValueError): integration_identity('https://github.com/another/repo', 'example', roots)
    with pytest.raises(ValueError): integration_identity('https://github.com/owner/repo', '../example', roots)
    cancel_request(directory, 'owner', 'chat', 'original')
    with pytest.raises(extensions.HTTPException):
        asyncio.run(extensions.extension_github_prepare_request(request(payload), api_key='owner'))


def test_reused_integration_uses_durable_journal_and_never_replays_unknown_attempt(monkeypatch, existing):
    roots, package, directory, identity = existing
    asyncio.run(extensions.extension_github_prepare_request(request({**identity, 'extensionId':'example'}), api_key='owner'))
    state = {'status': 'not_installed'}
    async def plan(*args, **kwargs):
        return build_install_plan('example', [{'id':'example', 'status':state['status'], 'installable':True}],
                                  lambda key: {'id':key}, lambda key: True)
    monkeypatch.setattr(extensions, 'extension_install_plan', plan)
    monkeypatch.setattr(extensions, '_extension_operation_lock', lambda key: contextlib.nullcontext())
    install = Mock(return_value={})
    monkeypatch.setattr(extensions, '_install_extension', install)
    monkeypatch.setattr(extensions, 'request_agent_json', Mock(return_value={'operation':None}))
    async def advance():
        return json.loads((await extensions.extension_github_advance_request(request(identity), api_key='owner')).body)
    first = asyncio.run(advance())
    assert first['dispatched'] and first['state'] == 'pending'
    again = asyncio.run(advance())
    assert not again['dispatched'] and again['state'] == 'reconciliation_required'
    assert install.call_count == 1
    state['status'] = 'installing'
    assert asyncio.run(advance())['state'] == 'reconciliation_required'
    assert install.call_count == 1
    cancel_request(directory, 'owner', 'chat', 'original')
    with pytest.raises(extensions.HTTPException): asyncio.run(advance())
    assert install.call_count == 1


def test_definition_digest_preserves_installer_build_path_rewrite(existing):
    roots, package, directory, identity = existing
    (package / 'Dockerfile').write_text('FROM scratch')
    (package / 'compose.yaml').write_text('services:\n  example:\n    build: .\n')
    original = integration_identity('https://github.com/owner/repo', 'example', roots)
    target = roots[0] / 'example'
    shutil.copytree(package, target)
    extensions._rewrite_build_context(target / 'compose.yaml', target)
    assert integration_identity('https://github.com/owner/repo', 'example', roots) == original
    (target / 'Dockerfile').write_text('FROM different')
    assert integration_identity('https://github.com/owner/repo', 'example', roots) != original


def test_empty_prepare_resolves_only_one_existing_repository_match(existing):
    roots, package, directory, identity = existing
    duplicate = roots[2] / 'second'
    shutil.copytree(package, duplicate)
    with pytest.raises(extensions.HTTPException) as error:
        asyncio.run(extensions.extension_github_prepare_request(request(identity), api_key='owner'))
    assert error.value.detail['reason'] == 'integration_selection_required'
    assert 'integration' not in read_request(directory, 'owner', 'chat', 'original')
    # Remove the competing repository association, retaining the other definition.
    (duplicate / 'upstream.json').write_text(json.dumps({'repository':'https://github.com/other/repo'}))
    result = asyncio.run(extensions.extension_github_prepare_request(request(identity), api_key='owner'))
    assert json.loads(result.body)['extensionId'] == 'example'
    assert json.loads(result.body)['installationStarted'] is False


def test_missing_proposal_is_a_scoped_rejection_without_state_mutation(existing):
    roots, package, directory, identity = existing
    (package / 'upstream.json').write_text(json.dumps({'repository':'https://github.com/other/repo'}))
    before = read_request(directory, 'owner', 'chat', 'original')
    with pytest.raises(extensions.HTTPException) as error:
        asyncio.run(extensions.extension_github_prepare_request(request(identity), api_key='owner'))
    assert error.value.status_code == 409
    assert error.value.detail == {'schemaVersion':1, 'kind':'ods-extension-request-preparation-rejected',
                                 **identity, 'reason':'proposal_required', 'installationStarted':False}
    assert read_request(directory, 'owner', 'chat', 'original') == before


def test_status_preserves_bounded_failure_evidence_only_for_failed_runtime(existing, monkeypatch):
    roots, package, directory, identity = existing
    asyncio.run(extensions.extension_github_prepare_request(request(identity), api_key='owner'))
    before = read_request(directory, 'owner', 'chat', 'original')
    detail = AsyncMock(return_value={'status': 'error', 'error_message': 'missing pyproject.toml'})
    monkeypatch.setattr(extensions, 'extension_detail', detail)
    observed = asyncio.run(extensions._observe_extension_request(identity, 'owner'))
    assert observed['runtimeError'] == 'missing pyproject.toml'
    assert observed['runtimeStatus'] == 'error'
    detail.return_value = {'status': 'error', 'error_message': 'x' * 9000}
    assert len(asyncio.run(extensions._observe_extension_request(identity, 'owner'))['runtimeError']) == 8192
    for status, error in [('enabled', 'old failure'), ('error', None), ('error', 42), ('error', ' ')]:
        detail.return_value = {'status': status, 'error_message': error}
        assert 'runtimeError' not in asyncio.run(extensions._observe_extension_request(identity, 'owner'))
    assert read_request(directory, 'owner', 'chat', 'original') == before
