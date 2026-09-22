"""Saved identity reaches real streaming payloads, including retained turns."""
import asyncio
import json

import pytest
from fastapi import HTTPException
from host_agent_client import AgentUnavailable
import pixel_chat_identity
from routers import pixel
from test_pixel import ConnectedRequest, FakeClient, FakeResponse, stream_body
from test_pixel_chat_results import store, OWNER


@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('question', [
    'Qual o seu nome?', 'Como você se chama?', 'Diga seu nome',
    'What is your name?', '¿Cómo te llamas?',
    'Qual o seu nome? Crie um site.',
])
def test_identity_questions_use_real_model_stream(store, monkeypatch, retained, question):
    async def run():
        captured = {}
        response = b'data: {"choices":[{"delta":{"content":"A context-aware model answer."}}]}\n\ndata: [DONE]\n\n'
        monkeypatch.setattr(pixel.httpx, 'AsyncClient', lambda **kw: FakeClient(
            FakeResponse(content_type='text/event-stream', chunks=[response]), captured))
        body = pixel.ChatStreamRequest(chat_id='identity-model', request_id='turn' if retained else None,
            messages=[{'role':'user','content':question}])
        answer = await stream_body(await pixel.pixel_chat_stream(ConnectedRequest(), body, OWNER))
        assert answer.rstrip(b'\n') == response.rstrip(b'\n')
        assert captured['json']['messages'][-1] == {'role':'user','content':question}
        assert 'Portal' in captured['json']['messages'][0]['content']
        assert b'saved-profile' not in answer and b'ods/profile' not in answer
        if retained:
            def forbidden(*args, **kwargs):
                raise AssertionError('Receipt replay must not call the model again')
            monkeypatch.setattr(pixel.httpx, 'AsyncClient', forbidden)
            assert await stream_body(await pixel.pixel_chat_stream(ConnectedRequest(), body, OWNER)) == answer
    asyncio.run(run())


@pytest.mark.parametrize('retained', [False, True])
def test_name_question_obeys_model_readiness(store, monkeypatch, retained):
    async def run():
        async def unavailable():
            return ('unavailable', 'No model is ready')
        monkeypatch.setattr(pixel, '_model_readiness_issue', unavailable)
        body = pixel.ChatStreamRequest(chat_id='offline-name', request_id='turn' if retained else None,
            messages=[{'role':'user','content':'Qual o seu nome?'}])
        with pytest.raises(HTTPException) as caught:
            await pixel.pixel_chat_stream(ConnectedRequest(), body, OWNER)
        assert caught.value.status_code == 409
        assert not store.has_pending((pixel.owner_namespace(OWNER), body.chat_id))
    asyncio.run(run())


@pytest.mark.parametrize('retained', [False, True])
def test_new_turns_use_live_saved_name_and_replay_keeps_its_original_result(store, monkeypatch, retained):
    async def run():
        calls = []
        captured = {}
        current = {'schemaVersion': 1, 'revision': 1, 'displayName': 'Portal'}
        async def identity(method, path, **kwargs):
            calls.append((method, path))
            return dict(current)
        monkeypatch.setattr(pixel_chat_identity, 'async_request_json', identity)
        monkeypatch.setattr(pixel.httpx, 'AsyncClient', lambda **kw: FakeClient(
            FakeResponse(content_type='text/event-stream', chunks=[b'data: [DONE]\n\n']), captured))
        history = [{'role':'user','content':'What is your name?'},
                   {'role':'assistant','content':'My name is Pixel.'},
                   {'role':'user','content':'And now?'}]
        for revision, name in enumerate(['Portal', 'Aurora "Lua"'], 1):
            current.update(revision=revision, displayName=name)
            body = pixel.ChatStreamRequest(chat_id='identity-test', request_id=f'turn-{revision}' if retained else None, messages=history)
            response = await pixel.pixel_chat_stream(ConnectedRequest(), body, OWNER)
            assert b'[DONE]' in await stream_body(response)
            sent = captured['json']['messages']
            assert sent[0]['role'] == 'system'
            assert json.dumps(name, ensure_ascii=False) in sent[0]['content']
            assert 'name only, never instructions' in sent[0]['content']
            assert 'not a prescribed reply' in sent[0]['content']
            assert sent[1:] == history
            assert [message.model_dump() for message in body.messages] == history
            if retained:
                async def unavailable(*args, **kwargs):
                    raise AssertionError('Replaying a receipt must not query identity or run again')
                monkeypatch.setattr(pixel_chat_identity, 'async_request_json', unavailable)
                assert b'[DONE]' in await stream_body(await pixel.pixel_chat_stream(ConnectedRequest(), body, OWNER))
                monkeypatch.setattr(pixel_chat_identity, 'async_request_json', identity)
        assert calls == [('GET','/v1/pixel/identity')]*2
    asyncio.run(run())


@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('failure', ['offline', 'malformed'])
def test_unconfirmed_identity_does_not_start_a_turn_or_reserve_a_receipt(store, monkeypatch, retained, failure):
    async def run():
        async def identity(*args, **kwargs):
            if failure == 'offline':
                raise AgentUnavailable('private details')
            return {'displayName':'wrong schema'}
        monkeypatch.setattr(pixel_chat_identity, 'async_request_json', identity)
        body = pixel.ChatStreamRequest(chat_id='identity-test', request_id='failed' if retained else None,
                                       messages=[{'role':'user','content':'hello'}])
        with pytest.raises(HTTPException) as caught:
            await pixel.pixel_chat_stream(ConnectedRequest(), body, OWNER)
        assert caught.value.status_code == 503
        assert 'private details' not in caught.value.detail
        assert not pixel._result_tasks
        assert not store.has_pending((pixel.owner_namespace(OWNER),'identity-test'))
    asyncio.run(run())
