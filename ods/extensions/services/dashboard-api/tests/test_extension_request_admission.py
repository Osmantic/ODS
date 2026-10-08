"""Admission rollback must preserve audit, tool visibility and newer intent."""
import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import Request

import extension_requests as routing
from routers import extensions, pixel
from test_pixel import FakeClient, FakeResponse, stream_body
from test_pixel_chat_results import FINAL, IDENTITY, OWNER, body
import test_pixel_chat_results as result_tests


store = result_tests.store
COMMAND = '/extensions inspect https://github.com/owner/repo'
REJECTION = {'error': 'pixel_transition_in_progress'}


@pytest.fixture
def directory(tmp_path, monkeypatch):
    requests = tmp_path / '.extension-requests'
    requests.mkdir()
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    monkeypatch.setattr('extension_github.inspect_repository', AsyncMock(side_effect=ValueError('unavailable')))
    return requests


def saved(directory, request='attempt-one'):
    return routing.read_request(directory, OWNER, 'chat-test', request)


def snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.glob('*.json')}


async def submit(store, monkeypatch, directory, response, *, command=COMMAND, during=None):
    calls = []

    class Client(FakeClient):
        def stream(self, *args, **kwargs):
            # This is before response headers. Tools resolving the session can
            # already see its correct scope, including on successful admission.
            current = routing.active_session_request(directory, OWNER, hashlib.sha256(b'chat-test').hexdigest())
            calls.append((current, kwargs))
            if during:
                with extensions._extensions_lock():
                    during()
            return super().stream(*args, **kwargs)

    monkeypatch.setattr(pixel.httpx, 'AsyncClient', lambda **kw: Client(response))
    reply = await pixel.pixel_chat_stream(Request({'type': 'http'}), body(text=command), OWNER)
    await asyncio.gather(*list(pixel._result_tasks.values()))
    data = await stream_body(reply)
    before_replay = snapshot(directory)
    replay = await pixel.pixel_chat_stream(Request({'type': 'http'}), body(text=command), OWNER)
    assert await stream_body(replay) == data
    assert snapshot(directory) == before_replay
    assert len(calls) == 1
    return data, calls[0]


def refusal(payload=REJECTION):
    return FakeResponse(status=409, chunks=[json.dumps(payload).encode()])


@pytest.mark.parametrize('predecessor', [False, True])
def test_trusted_rejection_removes_actionable_routing_but_preserves_audit(store, monkeypatch, directory, predecessor):
    if predecessor:
        routing.create_request(directory, OWNER, 'chat-test', 'previous', COMMAND)
        routing.bind_integration(directory, OWNER, 'chat-test', 'previous',
                                 {'extensionId': 'widget', 'definitionDigest': 'c' * 64})
        before = saved(directory, 'previous')
    data, (visible, _) = asyncio.run(submit(store, monkeypatch, directory, refusal()))
    assert visible['requestId'] == 'attempt-one' and visible['state'] == 'pending'
    assert b'"code": "transition_in_progress"' in data
    assert store.get(IDENTITY)['state'] == 'rejected' and not store.has_pending(IDENTITY[:2])
    rejected = saved(directory)
    assert rejected['state'] == 'cancelled' and rejected['repository'] == 'https://github.com/owner/repo'
    assert len(snapshot(directory)) == (2 if predecessor else 1)
    if predecessor:
        assert saved(directory, 'previous') == before
    else:
        assert routing.active_chat_request(directory, OWNER, 'chat-test') is None


def test_successful_admission_keeps_registration_visible_and_context_bound(store, monkeypatch, directory):
    routing.create_request(directory, OWNER, 'chat-test', 'previous', COMMAND)
    data, (visible, arguments) = asyncio.run(submit(store, monkeypatch, directory,
        FakeResponse(content_type='text/event-stream', chunks=[FINAL])))
    assert data == FINAL and store.get(IDENTITY)['state'] == 'complete'
    assert visible == saved(directory) and visible['requestId'] == 'attempt-one'
    assert saved(directory)['state'] == 'pending' and saved(directory, 'previous')['state'] == 'cancelled'
    messages = arguments['json']['messages']
    assert any('"requestId": "attempt-one"' in message['content'] for message in messages if message['role'] == 'system')


@pytest.mark.parametrize('response', [
    refusal({'error': 'other_conflict'}),
    refusal({'error': 'pixel_transition_in_progress', 'private': 'secret'}),
    FakeResponse(content_type='text/event-stream', chunks=[
        b'data: {"error":{"type":"pixel_dashboard_error","code":"transition_in_progress"}}\n\ndata: [DONE]\n\n']),
])
def test_untrusted_rejections_never_rollback_routing(store, monkeypatch, directory, response):
    routing.create_request(directory, OWNER, 'chat-test', 'previous', COMMAND)
    data, _ = asyncio.run(submit(store, monkeypatch, directory, response))
    assert store.get(IDENTITY)['state'] == 'interrupted'
    assert b'transition_in_progress' not in data and b'secret' not in data
    assert saved(directory)['state'] == 'pending' and saved(directory, 'previous')['state'] == 'cancelled'


@pytest.mark.parametrize('accepted', [False, True])
def test_slash_cancellation_restores_predecessor_only_after_trusted_refusal(store, monkeypatch, directory, accepted):
    routing.create_request(directory, OWNER, 'chat-test', 'previous', COMMAND)
    response = FakeResponse(content_type='text/event-stream', chunks=[FINAL]) if accepted else refusal()
    _, (visible, _) = asyncio.run(submit(store, monkeypatch, directory, response, command='/help'))
    assert visible is None
    assert saved(directory, 'previous')['state'] == ('cancelled' if accepted else 'pending')
    assert len(snapshot(directory)) == 1


@pytest.mark.parametrize('command,request_id', [(COMMAND, 'attempt-one'), ('please continue', 'previous')])
def test_rejection_does_not_cancel_preexisting_identity_or_ordinary_followup(store, monkeypatch, directory, command, request_id):
    routing.create_request(directory, OWNER, 'chat-test', request_id, COMMAND)
    before = snapshot(directory)
    asyncio.run(submit(store, monkeypatch, directory, refusal(), command=command))
    assert store.get(IDENTITY)['state'] == 'rejected'
    assert snapshot(directory) == before


@pytest.mark.parametrize('binding', ['proposal', 'integration'])
def test_rejection_preserves_concurrent_binding_as_cancelled_audit(store, monkeypatch, directory, binding):
    def bind():
        if binding == 'integration':
            routing.bind_integration(directory, OWNER, 'chat-test', 'attempt-one',
                                     {'extensionId': 'widget', 'definitionDigest': 'c' * 64})
        else:
            candidate = {'repository': 'https://github.com/owner/repo', 'manifest': {'service': {'id': 'widget'}}}
            digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            routing.bind_proposal(directory, OWNER, 'chat-test', 'attempt-one', candidate,
                {'valid': True, 'recipeDigest': digest, 'errors': [], 'existingExtensionIds': []},
                {'recipeDigest': digest, 'state': 'draft', 'installationStarted': False,
                 'registered': False, 'draftId': 'd' * 64})
        bound.update(saved(directory)[binding])

    bound: dict[str, str] = {}
    asyncio.run(submit(store, monkeypatch, directory, refusal(), during=bind))
    assert saved(directory)['state'] == 'cancelled' and saved(directory)[binding] == bound
    assert store.get(IDENTITY)['state'] == 'rejected'


@pytest.mark.parametrize('change', ['cancel-previous', 'cancel-current', 'successor', 'cancelled-successor', 'revision'])
@pytest.mark.parametrize('command', [COMMAND, '/help'])
def test_rollback_never_resurrects_routing_over_newer_intent(store, monkeypatch, directory, change, command):
    previous = routing.create_request(directory, OWNER, 'chat-test', 'previous', COMMAND)

    def mutate():
        if change == 'cancel-previous':
            routing.cancel_request(directory, OWNER, 'chat-test', 'previous')
        elif change == 'cancel-current':
            routing.cancel_request(directory, OWNER, 'chat-test', 'attempt-one')
        elif change in {'successor', 'cancelled-successor'}:
            routing.create_request(directory, OWNER, 'chat-test', 'later', COMMAND)
            if change == 'cancelled-successor':
                routing.cancel_request(directory, OWNER, 'chat-test', 'later')
        else:
            journals = directory.parent / '.extension-installations'
            journals.mkdir()
            (journals / (previous['id'] + '.revision.json')).write_text('retained coordination')
        concurrent.update(snapshot(directory))

    concurrent: dict[str, bytes] = {}
    asyncio.run(submit(store, monkeypatch, directory, refusal(), command=command, during=mutate))
    assert saved(directory, 'previous')['state'] == 'cancelled'
    if change in {'successor', 'cancelled-successor'}:
        assert saved(directory, 'later')['state'] == ('pending' if change == 'successor' else 'cancelled')
        filename = saved(directory, 'later')['id'] + '.json'
        assert snapshot(directory)[filename] == concurrent[filename]
    if change == 'revision':
        assert (directory.parent / '.extension-installations' / (previous['id'] + '.revision.json')).read_text() == 'retained coordination'
    assert store.get(IDENTITY)['state'] == 'rejected'


def test_unrelated_owner_and_chat_mutations_do_not_lose_predecessor(store, monkeypatch, directory):
    previous = routing.create_request(directory, OWNER, 'chat-test', 'previous', COMMAND)

    def unrelated():
        routing.create_request(directory, 'other-owner', 'chat-test', 'later', COMMAND)
        routing.create_request(directory, OWNER, 'other-chat', 'later', COMMAND)

    asyncio.run(submit(store, monkeypatch, directory, refusal(), during=unrelated))
    assert saved(directory, 'previous') == previous
    assert routing.read_request(directory, 'other-owner', 'chat-test', 'later')['state'] == 'pending'
    assert routing.read_request(directory, OWNER, 'other-chat', 'later')['state'] == 'pending'


def test_failed_rollback_never_publishes_safe_to_resend_receipt(store, monkeypatch, directory):
    monkeypatch.setattr(extensions, 'rollback_chat_extension_request', AsyncMock(side_effect=OSError('private path')))
    data, _ = asyncio.run(submit(store, monkeypatch, directory, refusal()))
    assert store.get(IDENTITY)['state'] == 'unresolved' and store.has_pending(IDENTITY[:2])
    assert b'transition_in_progress' not in data and b'private path' not in data
    assert saved(directory)['state'] == 'pending'


def test_legacy_record_revision_and_expiration_are_preserved(directory):
    initial = routing.create_request(directory, OWNER, 'chat-test', 'previous', COMMAND, now=10)
    path = directory / (initial['id'] + '.json')
    legacy = json.loads(path.read_text())
    legacy.pop('mutationRevision')
    path.write_text(json.dumps(legacy))
    assert saved_legacy(directory, 11) == initial
    preparation: dict = {}
    routing.create_request(directory, OWNER, 'chat-test', 'attempt-one', COMMAND, now=11, preparation=preparation)
    routing.rollback_prepared_request(directory, OWNER, 'chat-test', 'attempt-one', preparation, now=10 + routing.TTL_SECONDS)
    assert saved_legacy(directory, 10 + routing.TTL_SECONDS)['state'] == 'cancelled'
    assert json.loads(path.read_text())['expiresAt'] == initial['expiresAt']


def saved_legacy(directory, now):
    return routing.read_request(directory, OWNER, 'chat-test', 'previous', now=now)


@pytest.mark.parametrize('revision', [True, -1, '1', None])
def test_invalid_mutation_revision_is_refused(directory, revision):
    initial = routing.create_request(directory, OWNER, 'chat-test', 'previous', COMMAND)
    path = directory / (initial['id'] + '.json')
    record = json.loads(path.read_text())
    record['mutationRevision'] = revision
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        saved(directory, 'previous')
