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
    assert read()['installationVerified'] is False
    assert read()['authorizationMode'] == 'research'
    with pytest.raises(extensions.HTTPException) as other:
        read('other')
    assert other.value.status_code == 409
    draft = save_draft(drafts, 'owner', proposal, evidence(proposal))
    bind_proposal(requests, 'owner', 'chat', 'turn', proposal, evidence(proposal), draft)
    result = read()
    assert result['proposalAccepted'] is True
    assert result['authorizationMode'] == 'research'
    assert result['prepared'] is False and result['runtimeStatus'] == 'not_observed'
    assert result['installationVerified'] is False
    runtime.assert_not_awaited()
    publish_package(library, proposal, evidence(proposal), upstream(proposal))
    result = read()
    assert result['prepared'] is True and result['runtimeStatus'] == 'enabled'
    assert result['requestState'] == 'pending' and result['installationVerified'] is True
    runtime.assert_awaited_once_with('apache-answer', api_key='owner')
    runtime.return_value = {'status':'cli_installed'}
    assert read()['installationVerified'] is True
    runtime.return_value = {'status':'disabled'}
    assert read()['installationVerified'] is False
    runtime.side_effect = TimeoutError()
    unknown = read()
    assert unknown['runtimeStatus'] == 'not_observed' and unknown['installationVerified'] is False
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
    assert result['installationVerified'] is False
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
    create_request(requests,'owner','chat','turn','/extensions install '+proposal['repository'])
    assert asyncio.run(extensions._observe_extension_request(identity, 'owner'))['authorizationMode'] == 'install'
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


def test_retry_uses_only_owner_bound_request_identity(monkeypatch, tmp_path):
    from unittest.mock import Mock
    from extension_requests import _identity
    requests = tmp_path / '.extension-requests'; requests.mkdir()
    drafts = tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    library = tmp_path / 'library'; library.mkdir()
    proposal = candidate()
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    identity = {'chatId': 'chat', 'requestId': 'turn'}
    create_request(requests, 'owner', 'chat', 'turn', '/extensions install ' + proposal['repository'])
    draft = save_draft(drafts, 'owner', proposal, evidence(proposal))
    bind_proposal(requests, 'owner', 'chat', 'turn', proposal, evidence(proposal), draft)
    publish_package(library, proposal, evidence(proposal), upstream(proposal))
    retry = Mock(return_value={'state': 'pending', 'activeExtensionId': 'apache-answer',
        'operationId': 'a' * 32, 'dispatched': True})
    monkeypatch.setattr(extensions, '_advance_extension_installation', retry)
    monkeypatch.setattr(extensions, '_is_installable', lambda service_id: service_id == 'apache-answer')
    monkeypatch.setattr(extensions, '_has_error_progress', lambda service_id: service_id == 'apache-answer')

    def call(owner='owner'):
        async def receive():
            return {'type': 'http.request', 'body': json.dumps(identity).encode()}
        return asyncio.run(extensions.extension_github_retry_request(
            Request({'type': 'http', 'method': 'POST', 'headers': []}, receive), api_key=owner))

    with pytest.raises(extensions.HTTPException):
        call('another-owner')
    retry.assert_not_called()
    result = json.loads(call().body)
    assert result['extensionId'] == 'apache-answer' and result['state'] == 'pending'
    assert retry.call_args.args[3] == identity
    assert retry.call_args.args[4] == _identity('owner', 'chat', 'turn')[1]
    monkeypatch.setattr(extensions, '_has_error_progress', lambda _service_id: False)
    with pytest.raises(extensions.HTTPException) as rejected:
        call()
    assert rejected.value.status_code == 409
    assert retry.call_count == 1


def test_research_only_request_cannot_advance_or_retry_prepared_recipe(monkeypatch, tmp_path):
    from unittest.mock import Mock
    requests = tmp_path / '.extension-requests'; requests.mkdir()
    drafts = tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    library = tmp_path / 'library'; library.mkdir()
    proposal = candidate()
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    create_request(requests, 'owner', 'chat', 'turn', '/extensions ' + proposal['repository'])
    draft = save_draft(drafts, 'owner', proposal, evidence(proposal))
    bind_proposal(requests, 'owner', 'chat', 'turn', proposal, evidence(proposal), draft)
    publish_package(library, proposal, evidence(proposal), upstream(proposal))
    advance = Mock()
    monkeypatch.setattr(extensions, '_advance_extension_installation', advance)

    async def receive():
        return {'type': 'http.request', 'body': b'{"chatId":"chat","requestId":"turn"}'}
    for endpoint in (extensions.extension_github_advance_request,
                     extensions.extension_github_retry_request):
        with pytest.raises(extensions.HTTPException) as denied:
            asyncio.run(endpoint(Request({'type':'http','method':'POST','headers':[]}, receive), api_key='owner'))
        assert denied.value.status_code == 409
    advance.assert_not_called()

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
