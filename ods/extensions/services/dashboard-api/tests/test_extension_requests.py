import asyncio
import json

import pytest
from starlette.requests import Request

from extension_requests import create_request, read_request, cancel_request, bind_proposal, TTL_SECONDS


COMMAND = '/extensions https://github.com/Owner/Repo.git configure for my project'


def test_chat_context_uses_current_turn_without_modifying_saved_history():
    from routers import pixel
    history = [{'role': 'system', 'content': 'Identity'}, {'role': 'user', 'content': COMMAND}]
    body = pixel.ChatStreamRequest(chat_id='current-chat', request_id='current-turn', messages=history)
    result = pixel._edge_chat_body(body, history)
    assert len(history) == 2 and len(result['messages']) == 3
    assert result['messages'][-1] == history[-1]
    context = result['messages'][1]
    assert context['role'] == 'system'
    assert 'current-chat' in context['content'] and 'current-turn' in context['content']
    assert 'https://github.com/owner/repo' in context['content']
    assert 'does not grant execution authority' in context['content']


def test_old_extension_command_does_not_create_current_context():
    from routers import pixel
    history = [{'role': 'user', 'content': COMMAND}, {'role': 'assistant', 'content': 'Previous response'},
               {'role': 'user', 'content': 'What did we do?'}]
    body = pixel.ChatStreamRequest(chat_id='chat', request_id='next', messages=history)
    assert pixel._edge_chat_body(body, history)['messages'] == history


@pytest.mark.parametrize('existing', [[], ['registered-integration']])
def test_request_evidence_resolves_commit_without_forwarding_unbounded_documents(monkeypatch, tmp_path, existing):
    from routers import extensions
    from unittest.mock import AsyncMock
    inspect = AsyncMock(return_value={'repository': 'https://github.com/owner/repo', 'commit': 'a' * 40,
        'archived': False, 'existingExtensionIds': existing, 'licenseIdentifier': 'MIT',
        'contentTrust': 'untrusted-upstream-evidence', 'evidenceScope': 'repository-documents-at-commit',
        'readme': 'UNTRUSTED_DOCUMENT' * 20000})
    monkeypatch.setattr('extension_github.inspect_repository', inspect)
    monkeypatch.setattr('extension_github.inspect_installation_layout', AsyncMock(return_value={'documents': []}))
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    command = COMMAND + ' research only; do not install'
    result = asyncio.run(extensions.chat_extension_request_context('owner', 'chat', 'turn', command,
        include_evidence=True))
    assert 'a' * 40 in result['content']
    assert 'UNTRUSTED_DOCUMENT' not in result['content']
    assert 'pixel_ods_skill' in result['content']
    assert json.dumps(existing) in result['content']
    assert 'research-only scope' in result['content']
    assert 'do not select an implementation or authorize installation' in result['content']
    assert 'submit the researched recipe' not in result['content']
    assert 'pixel_ods_web_extract' not in result['content']
    saved = read_request(tmp_path / '.extension-requests', 'owner', 'chat', 'turn')
    assert saved['installationStarted'] is False and 'proposal' not in saved
    inspect.assert_awaited_once()
    inspect.reset_mock()
    assert asyncio.run(extensions.chat_extension_request_context('owner', 'different', 'turn', 'hello',
        include_evidence=True)) is None
    inspect.assert_not_awaited()


def test_unavailable_repository_evidence_preserves_request_but_does_not_fabricate_revision(monkeypatch, tmp_path):
    from routers import extensions
    from unittest.mock import AsyncMock
    monkeypatch.setattr('extension_github.inspect_repository', AsyncMock(side_effect=ValueError('upstream failure')))
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    result = asyncio.run(extensions.chat_extension_request_context('owner', 'chat', 'turn', COMMAND,
        include_evidence=True))
    assert 'revision could not be verified' in result['content']
    assert 'upstream failure' not in result['content']


def test_missing_request_id_does_not_invent_an_execution_scope():
    from extension_requests import model_request_context
    assert model_request_context(COMMAND, 'chat', None) is None


def test_request_is_repo_and_turn_bound_and_never_an_installation(tmp_path):
    record = create_request(tmp_path, 'owner-secret', 'chat', 'turn', COMMAND, now=10)
    assert record['repository'] == 'https://github.com/owner/repo'
    assert record['state'] == 'pending' and record['installationStarted'] is False
    assert 'owner-secret' not in json.dumps(record)
    assert create_request(tmp_path, 'owner-secret', 'chat', 'turn', COMMAND, now=100) == record
    with pytest.raises(ValueError):
        create_request(tmp_path, 'owner-secret', 'chat', 'turn', '/extensions https://github.com/other/repo', now=100)
    with pytest.raises((ValueError, OSError)):
        read_request(tmp_path, 'other-owner', 'chat', 'turn', now=100)


def test_expired_and_cancelled_requests_cannot_be_revived(tmp_path):
    create_request(tmp_path, 'owner', 'chat', 'turn', COMMAND, now=10)
    assert create_request(tmp_path, 'owner', 'chat', 'turn', COMMAND, now=10 + TTL_SECONDS)['state'] == 'expired'
    cancel_request(tmp_path, 'owner', 'chat', 'turn', now=20)
    assert create_request(tmp_path, 'owner', 'chat', 'turn', COMMAND, now=21)['state'] == 'cancelled'


def test_cancellation_before_creation_prevents_delayed_authorization(tmp_path):
    cancelled = cancel_request(tmp_path, 'owner', 'chat', 'turn', now=10)
    assert cancelled['state'] == 'cancelled' and cancelled['repository'] is None
    assert create_request(tmp_path, 'owner', 'chat', 'turn', COMMAND, now=11) == cancelled
    assert read_request(tmp_path, 'owner', 'chat', 'turn', now=12)['state'] == 'cancelled'


def test_new_turn_cancels_only_its_owner_and_conversation(tmp_path):
    for owner, chat in [('owner', 'chat'), ('owner', 'other-chat'), ('other-owner', 'chat')]:
        create_request(tmp_path, owner, chat, 'turn', COMMAND, now=10)
    create_request(tmp_path, 'owner', 'chat', 'next', COMMAND, now=11)
    assert read_request(tmp_path, 'owner', 'chat', 'turn', now=12)['state'] == 'cancelled'
    for owner, chat in [('owner', 'other-chat'), ('other-owner', 'chat')]:
        assert read_request(tmp_path, owner, chat, 'turn', now=12)['state'] == 'pending'


@pytest.mark.parametrize('command', ['please /extensions https://github.com/o/r', '/extensions @repo',
    '/extensions http://github.com/o/r', '/extensions https://github.com.evil/o/r',
    '/extensions https://github.com/o/r?token=secret', '/extensions https://github.com/o/r/tree/main'])
def test_non_commands_and_unsafe_repository_targets_do_not_create_requests(tmp_path, command):
    with pytest.raises(ValueError):
        create_request(tmp_path, 'owner', 'chat', 'turn', command)
    assert not list(tmp_path.iterdir())


def test_revision_binding_requires_exact_previous_binding_and_preserves_identity(tmp_path):
    import copy
    from test_extension_recipe_validation import candidate
    from test_extension_recipe_drafts import evidence
    from extension_recipe_drafts import save_draft
    initial = candidate()
    create_request(tmp_path, 'owner', 'chat', 'turn', '/extensions ' + initial['repository'], now=10)
    drafts = tmp_path / 'drafts'; drafts.mkdir()
    def bind(value, **kwargs):
        validation = evidence(value)
        draft = save_draft(drafts, 'owner', value, validation)
        return bind_proposal(tmp_path, 'owner', 'chat', 'turn', value, validation, draft, now=11, **kwargs)
    first = bind(initial)
    revised = copy.deepcopy(initial); revised['commit'] = 'b' * 40
    for expected in [{}, {**first['proposal'], 'recipeDigest': 'c' * 64}]:
        with pytest.raises(ValueError): bind(revised, expected_proposal=expected)
        assert read_request(tmp_path, 'owner', 'chat', 'turn', now=11) == first
    renamed = copy.deepcopy(revised); renamed['manifest']['service']['id'] = 'other'
    with pytest.raises(ValueError): bind(renamed, expected_proposal=first['proposal'])
    second = bind(revised, expected_proposal=first['proposal'])
    assert second['proposal'] != first['proposal']
    assert second['proposal']['extensionId'] == first['proposal']['extensionId']
    assert second['expiresAt'] == first['expiresAt']
    assert second['installationStarted'] is False
    assert (drafts / (first['proposal']['draftId'] + '.json')).exists()
    with pytest.raises(ValueError): bind(initial, expected_proposal=first['proposal'])
    assert read_request(tmp_path, 'owner', 'chat', 'turn', now=11) == second
    cancel_request(tmp_path, 'owner', 'chat', 'turn', now=12)
    with pytest.raises(ValueError): bind(initial, expected_proposal=second['proposal'])


def test_api_request_lifecycle_never_calls_installation(monkeypatch, tmp_path):
    from routers import extensions
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    def forbidden(*args, **kwargs):
        pytest.fail('Request registration attempted installation')
    monkeypatch.setattr(extensions, '_call_agent_install', forbidden)
    def request(action):
        payload = {'action': action, 'chatId': 'chat', 'requestId': 'turn'}
        if action == 'create':
            payload['command'] = COMMAND
        async def receive():
            return {'type': 'http.request', 'body': json.dumps(payload).encode(), 'more_body': False}
        return Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)
    for action, state in [('create', 'pending'), ('read', 'pending'), ('cancel', 'cancelled')]:
        result = asyncio.run(extensions.extension_github_request(request(action), api_key='owner'))
        assert result.headers['cache-control'] == 'no-store'
        assert json.loads(result.body)['state'] == state


def test_proposal_is_bound_to_repo_and_cannot_change_after_acceptance(tmp_path):
    from test_extension_recipe_validation import candidate
    from test_extension_recipe_drafts import evidence
    from extension_recipe_drafts import save_draft
    import copy
    proposal = candidate()
    create_request(tmp_path, 'owner', 'chat', 'turn', '/extensions ' + proposal['repository'], now=10)
    drafts = tmp_path / 'drafts'; drafts.mkdir()
    validation = evidence(proposal)
    draft = save_draft(drafts, 'owner', proposal, validation)
    result = bind_proposal(tmp_path, 'owner', 'chat', 'turn', proposal, validation, draft, now=11)
    assert result['proposal']['extensionId'] == 'apache-answer'
    assert result['proposal']['draftId'] == draft['draftId']
    assert bind_proposal(tmp_path, 'owner', 'chat', 'turn', proposal, validation, draft, now=12) == result
    changed = copy.deepcopy(proposal); changed['commit'] = 'b' * 40
    changed_validation = evidence(changed)
    changed_draft = save_draft(drafts, 'owner', changed, changed_validation)
    with pytest.raises(ValueError):
        bind_proposal(tmp_path, 'owner', 'chat', 'turn', changed, changed_validation, changed_draft, now=12)
    cancel_request(tmp_path, 'owner', 'chat', 'turn', now=13)
    with pytest.raises(ValueError):
        bind_proposal(tmp_path, 'owner', 'chat', 'turn', proposal, validation, draft, now=14)


def test_proposal_route_saves_only_for_active_matching_owner_request(monkeypatch, tmp_path):
    from routers import extensions
    from test_extension_recipe_validation import candidate, ODS
    from unittest.mock import AsyncMock
    proposal = candidate()
    requests = tmp_path / '.extension-requests'; requests.mkdir()
    create_request(requests, 'owner', 'chat', 'turn', '/extensions ' + proposal['repository'])
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', tmp_path / 'user')
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': []}))
    def request():
        async def receive():
            return {'type': 'http.request', 'body': json.dumps({'chatId': 'chat', 'requestId': 'turn',
                    'candidate': proposal}).encode(), 'more_body': False}
        return Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)
    with pytest.raises(extensions.HTTPException) as failure:
        asyncio.run(extensions.extension_github_request_proposal(request(), api_key='other-owner'))
    assert failure.value.status_code == 409
    assert not (tmp_path / '.extension-recipe-drafts').exists()
    response = asyncio.run(extensions.extension_github_request_proposal(request(), api_key='owner'))
    result = json.loads(response.body)
    assert result['proposal']['extensionId'] == 'apache-answer'
    assert result['installationStarted'] is False
    assert response.headers['cache-control'] == 'no-store'
    assert not (tmp_path / 'library').exists() and not (tmp_path / 'user').exists()

    # The coordinator may already have published the extension when a lost
    # proposal response is retried. Recover the same receipt without treating
    # its catalog ID as a conflicting new install or repeating remote work.
    with monkeypatch.context() as retry_patch:
        validation = AsyncMock(side_effect=AssertionError('Retry must not validate a new installation'))
        retry_patch.setattr(extensions, '_validated_github_recipe', validation)
        retry = asyncio.run(extensions.extension_github_request_proposal(request(), api_key='owner'))
        assert json.loads(retry.body) == result
        validation.assert_not_awaited()
        draft_path = tmp_path / '.extension-recipe-drafts' / (result['proposal']['draftId'] + '.json')
        saved = draft_path.read_text(encoding='utf-8')
        draft_path.write_text('{}', encoding='utf-8')
        with pytest.raises(extensions.HTTPException) as corrupted:
            asyncio.run(extensions.extension_github_request_proposal(request(), api_key='owner'))
        assert corrupted.value.status_code == 409
        draft_path.write_text(saved, encoding='utf-8')

    # An invalid recipe must yield actionable schema paths, not a misleading
    # ownership conflict or an echo of untrusted rejected values.
    proposal['manifest'] = {'private': 'sensitive-value'}
    with pytest.raises(extensions.HTTPException) as failure:
        asyncio.run(extensions.extension_github_request_proposal(request(), api_key='owner'))
    assert failure.value.status_code == 422
    assert failure.value.detail['code'] == 'recipe-validation-failed'
    assert any(row['code'] == 'manifest-schema' for row in failure.value.detail['errors'])
    assert 'sensitive-value' not in json.dumps(failure.value.detail)
    monkeypatch.setattr(extensions, '_validated_github_recipe', AsyncMock(return_value={
        'valid': False, 'errors': [{'code': 'repository-already-exists', 'path': 'repository'}],
        'existingExtensionIds': ['existing-answer']}))
    with pytest.raises(extensions.HTTPException) as conflict:
        asyncio.run(extensions.extension_github_request_proposal(request(), api_key='owner'))
    assert conflict.value.status_code == 422
    assert conflict.value.detail['existingExtensionIds'] == ['existing-answer']


def test_active_routing_is_owner_chat_and_expiry_scoped(tmp_path):
    from extension_requests import active_chat_request
    create_request(tmp_path, 'owner', 'chat', 'original', COMMAND, now=100)
    assert active_chat_request(tmp_path, 'owner', 'chat', now=101)['requestId'] == 'original'
    assert active_chat_request(tmp_path, 'another-owner', 'chat', now=101) is None
    assert active_chat_request(tmp_path, 'owner', 'another-chat', now=101) is None
    assert active_chat_request(tmp_path, 'owner', 'chat', now=100 + TTL_SECONDS) is None
    cancel_request(tmp_path, 'owner', 'chat', 'original', now=102)
    assert active_chat_request(tmp_path, 'owner', 'chat', now=103) is None


def test_verified_followup_routing_preserves_original_scope_and_user_message():
    from extension_requests import model_request_context
    from routers import pixel
    history = [{'role': 'user', 'content': 'sim'}]
    body = pixel.ChatStreamRequest(chat_id='chat', request_id='followup', messages=history)
    context = model_request_context(COMMAND, 'chat', 'original')
    result = pixel._edge_chat_body(body, history, extension_context=context)
    assert result['messages'][0] == context
    assert result['messages'][1] == history[0]
    assert 'original' in result['messages'][0]['content']
    assert 'does not grant execution authority' in result['messages'][0]['content']
    assert len(context['content']) < 750
    assert 'not installation status or a plan' in context['content']
    assert 'pixel_ods_skill' in context['content']
    # The trusted routing hint must not reinterpret the owner's research-only
    # request as a mandatory proposal/prepare/install sequence.
    for operation in ('pixel_ods_python_library_proposal', 'pixel_ods_extension_request_prepare',
                      'pixel_ods_extension_request_advance'):
        assert operation not in context['content']


def test_goal_wrapped_extension_uses_the_same_request_route():
    from extension_requests import command_repository, model_request_context
    command = '/goal /extensions https://github.com/NandhaKishorM/laya pode instalar'
    assert command_repository(command) == 'https://github.com/nandhakishorm/laya'
    assert model_request_context(command, 'chat', 'request') is not None
    with pytest.raises(ValueError):
        command_repository('explain /goal /extensions https://github.com/owner/repo')


def test_followup_recovers_bound_proposal_without_network_research(monkeypatch, tmp_path):
    from routers import extensions
    from extension_recipe_drafts import save_draft
    from test_extension_recipe_validation import candidate
    from test_extension_recipe_drafts import evidence
    from unittest.mock import AsyncMock
    proposal = candidate()
    requests = tmp_path / '.extension-requests'; requests.mkdir()
    drafts = tmp_path / '.extension-recipe-drafts'; drafts.mkdir()
    draft = save_draft(drafts, 'owner', proposal, evidence(proposal))
    create_request(requests, 'owner', 'chat', 'original', '/extensions ' + proposal['repository'])
    bind_proposal(requests, 'owner', 'chat', 'original', proposal, evidence(proposal), draft)
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    inspect = AsyncMock(return_value={'repository': proposal['repository'], 'commit': proposal['commit'],
        'archived': False, 'existingExtensionIds': [], 'licenseIdentifier': 'MIT',
        'contentTrust': 'untrusted-upstream-evidence', 'evidenceScope': 'repository-documents-at-commit'})
    monkeypatch.setattr('extension_github.inspect_repository', inspect)
    monkeypatch.setattr('extension_github.inspect_installation_layout', AsyncMock(return_value={'documents': []}))
    result = asyncio.run(extensions.chat_extension_request_context('owner', 'chat', 'followup',
        'sim, pode continuar', include_evidence=True))
    assert draft['draftId'] in result['content']
    assert '"requestId": "original"' in result['content']
    assert '"proposalAccepted": true' in result['content']
    assert '"installationState": "not_observed"' in result['content']
    assert proposal['commit'] in result['content']
    inspect.assert_not_awaited()
    inspect.reset_mock()
    assert asyncio.run(extensions.chat_extension_request_context('other', 'chat', 'followup',
        'sim', include_evidence=True)) is None
    inspect.assert_not_awaited()
    (drafts / (draft['draftId'] + '.json')).write_text('{}', encoding='utf-8')
    result = asyncio.run(extensions.chat_extension_request_context('owner', 'chat', 'next',
        'continue', include_evidence=True))
    assert 'could not recover' in result['content']
    inspect.assert_not_awaited()

def test_session_scope_is_owner_bound_and_does_not_revive_requests(tmp_path):
    import hashlib
    from extension_requests import active_session_request, cancel_request
    session_hash = hashlib.sha256(b'chat').hexdigest()
    create_request(tmp_path, 'owner', 'chat', 'original', COMMAND, now=100)
    create_request(tmp_path, 'another-owner', 'chat', 'foreign', COMMAND, now=100)
    assert active_session_request(tmp_path, 'owner', session_hash, now=101)['requestId'] == 'original'
    assert active_session_request(tmp_path, 'stranger', session_hash, now=101) is None
    assert active_session_request(tmp_path, 'owner', '0'*64, now=101) is None
    assert active_session_request(tmp_path, 'owner', session_hash, now=100000) is None
    cancel_request(tmp_path, 'owner', 'chat', 'original', now=101)
    assert active_session_request(tmp_path, 'owner', session_hash, now=102) is None
    with pytest.raises(ValueError):
        active_session_request(tmp_path, 'owner', '../chat', now=102)


def test_session_scope_endpoint_uses_authenticated_owner(monkeypatch, tmp_path):
    import hashlib
    import json
    from starlette.requests import Request
    from routers import extensions
    directory = tmp_path / '.extension-requests'; directory.mkdir()
    create_request(directory, 'owner', 'chat', 'original', COMMAND)
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    async def call(owner, payload):
        async def receive(): return {'type':'http.request','body':json.dumps(payload).encode()}
        return await extensions.extension_github_request_resolve(
            Request({'type':'http','method':'POST','headers':[]},receive),api_key=owner)
    payload={'sessionHash':hashlib.sha256(b'chat').hexdigest()}
    result=asyncio.run(call('owner',payload))
    assert result.headers['cache-control']=='no-store'
    assert json.loads(result.body)['request']=={'chatId':'chat','requestId':'original'}
    assert json.loads(asyncio.run(call('stranger',payload)).body)['request'] is None
    with pytest.raises(extensions.HTTPException):
        asyncio.run(call('owner',{'sessionHash':'bad'}))


def test_status_discovers_existing_repository_without_binding_or_installing(monkeypatch, tmp_path):
    from routers import extensions
    from unittest.mock import AsyncMock
    requests = tmp_path / '.extension-requests'; requests.mkdir()
    roots = [tmp_path / name for name in ('user', 'built-in', 'library')]
    for root in roots: root.mkdir()
    create_request(requests, 'owner', 'chat', 'turn', COMMAND)
    for identifier in ('existing-a', 'existing-b'):
        target = roots[2] / identifier; target.mkdir()
        (target / 'upstream.json').write_text(json.dumps({'repository': 'https://github.com/owner/repo'}))
    # Higher-priority definitions shadow a lower-priority repository match.
    (roots[0] / 'existing-b').mkdir()
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    for name, root in zip(('USER_EXTENSIONS_DIR', 'EXTENSIONS_DIR', 'EXTENSIONS_LIBRARY_DIR'), roots):
        monkeypatch.setattr(extensions, name, root)
    runtime = AsyncMock()
    monkeypatch.setattr(extensions, 'extension_detail', runtime)
    result = asyncio.run(extensions._observe_extension_request({'chatId': 'chat', 'requestId': 'turn'}, 'owner'))
    assert result['existingExtensionIds'] == ['existing-a']
    assert result['proposalAccepted'] is False and result['prepared'] is False
    assert result['extensionId'] is None and result['runtimeStatus'] == 'not_observed'
    assert 'proposal' not in read_request(requests, 'owner', 'chat', 'turn')
    runtime.assert_not_awaited()
    cancel_request(requests, 'owner', 'chat', 'turn')
    result = asyncio.run(extensions._observe_extension_request({'chatId': 'chat', 'requestId': 'turn'}, 'owner'))
    assert result['existingExtensionIds'] == []
