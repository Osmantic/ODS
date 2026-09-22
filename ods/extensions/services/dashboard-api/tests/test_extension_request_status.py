import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from extension_requests import create_request, bind_proposal, cancel_request
from extension_recipe_drafts import save_draft
from extension_recipe_package import publish_package
from test_extension_recipe_validation import candidate
from test_extension_recipe_drafts import evidence
from test_extension_recipe_package import upstream
from routers import extensions


def test_status_distinguishes_proposal_preparation_and_observed_runtime(monkeypatch, tmp_path):
    requests = tmp_path / '.extension-requests'; requests.mkdir()
    drafts = tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    library = tmp_path / 'library'; library.mkdir()
    proposal = candidate()
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    runtime = AsyncMock(return_value={'status': 'enabled', 'private': 'do not disclose'})
    monkeypatch.setattr(extensions, 'extension_detail', runtime)

    def read(owner='owner'):
        async def receive():
            return {'type': 'http.request', 'body': json.dumps({'chatId': 'chat', 'requestId': 'turn'}).encode()}
        result = asyncio.run(extensions.extension_github_request_status(
            Request({'type': 'http', 'method': 'POST', 'headers': []}, receive), api_key=owner))
        assert result.headers['cache-control'] == 'no-store'
        assert 'do not disclose' not in result.body.decode()
        return json.loads(result.body)

    create_request(requests, 'owner', 'chat', 'turn', '/extensions ' + proposal['repository'])
    assert read()['proposalAccepted'] is False
    with pytest.raises(extensions.HTTPException) as other:
        read('other')
    assert other.value.status_code == 409
    draft = save_draft(drafts, 'owner', proposal, evidence(proposal))
    bind_proposal(requests, 'owner', 'chat', 'turn', proposal, evidence(proposal), draft)
    result = read()
    assert result['proposalAccepted'] is True
    assert result['prepared'] is False and result['runtimeStatus'] == 'not_observed'
    runtime.assert_not_awaited()
    publish_package(library, proposal, evidence(proposal), upstream(proposal))
    result = read()
    assert result['prepared'] is True and result['runtimeStatus'] == 'enabled'
    runtime.assert_awaited_once_with('apache-answer', api_key='owner')
    runtime.side_effect = TimeoutError()
    assert read()['runtimeStatus'] == 'not_observed'
    runtime.reset_mock()
    (library / 'apache-answer/compose.yaml').write_text('services: {}', encoding='utf-8')
    with pytest.raises(extensions.HTTPException) as changed:
        read()
    assert changed.value.status_code == 409
    runtime.assert_not_awaited()
    cancel_request(requests, 'owner', 'chat', 'turn')
    result = read()
    assert result['requestState'] == 'cancelled'
    assert result['prepared'] is False and result['runtimeStatus'] == 'not_observed'
    runtime.assert_not_awaited()


def test_advance_resolves_only_unchanged_owner_bound_prepared_recipe(monkeypatch, tmp_path):
    from unittest.mock import Mock
    requests=tmp_path / '.extension-requests'; requests.mkdir()
    drafts=tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    library=tmp_path / 'library'; library.mkdir()
    proposal=candidate()
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    identity={'chatId':'chat','requestId':'turn'}
    create_request(requests,'owner','chat','turn','/extensions '+proposal['repository'])
    draft=save_draft(drafts,'owner',proposal,evidence(proposal))
    bind_proposal(requests,'owner','chat','turn',proposal,evidence(proposal),draft)
    advance=Mock(return_value={'state':'pending','activeExtensionId':'apache-answer',
        'operationId':'a'*32,'dispatched':True})
    monkeypatch.setattr(extensions,'_advance_extension_installation',advance)
    def call(owner='owner'):
        async def receive(): return {'type':'http.request','body':json.dumps(identity).encode()}
        return asyncio.run(extensions.extension_github_advance_request(
            Request({'type':'http','method':'POST','headers':[]},receive),api_key=owner))
    with pytest.raises(extensions.HTTPException): call()
    advance.assert_not_called()
    publish_package(library,proposal,evidence(proposal),upstream(proposal))
    with pytest.raises(extensions.HTTPException): call('another-owner')
    advance.assert_not_called()
    result=json.loads(call().body)
    assert result['extensionId']=='apache-answer' and result['state']=='pending'
    assert result['operationId']=='a'*32 and result['dispatched'] is True
    assert advance.call_args.args[3]==identity
    cancel_request(requests,'owner','chat','turn')
    with pytest.raises(extensions.HTTPException): call()
    assert advance.call_count==1

@pytest.mark.parametrize('message', ['sim', 'pode seguir', 'como está indo?'])
def test_followup_context_contains_original_request_and_observed_installation(monkeypatch, tmp_path, message):
    requests = tmp_path / '.extension-requests'; requests.mkdir()
    drafts = tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    library = tmp_path / 'library'; library.mkdir()
    proposal = candidate()
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    create_request(requests, 'owner', 'chat', 'original', '/extensions ' + proposal['repository'])
    draft = save_draft(drafts, 'owner', proposal, evidence(proposal))
    bind_proposal(requests, 'owner', 'chat', 'original', proposal, evidence(proposal), draft)
    publish_package(library, proposal, evidence(proposal), upstream(proposal))
    monkeypatch.setattr(extensions, 'extension_detail', AsyncMock(return_value={'status':'installing','private':'never-show'}))
    result = asyncio.run(extensions.chat_extension_request_context('owner','chat','followup',message))
    assert '"requestId": "original"' in result['content']
    assert '"installationState": "installing"' in result['content']
    assert 'never-show' not in result['content']
