import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import talk_conversations as conversations


@pytest.fixture
def durable(monkeypatch, tmp_path):
    db = conversations.Store(tmp_path/'talk.sqlite3')
    monkeypatch.setattr(conversations, 'store', lambda: db)
    monkeypatch.setenv('ODS_TALK_DURABLE', 'true')
    yield db
    db.close()


def test_identity_survives_reopen_without_owner_or_tab_rebinding(tmp_path):
    path = tmp_path/'state.sqlite3'
    first = conversations.Store(path)
    key = first.resolve('owner-digest', 't'*64, 'h'*64, create=True)
    first.created(key, 'native-root', 'runtime-one')
    first.resumed(key, 'compressed-tip', 'runtime-two', running=False)
    first.delivery(key, 'uncertain')
    first.close()
    second = conversations.Store(path)
    try:
        assert second.resolve('owner-digest', 't'*64, 'h'*64) == key
        row = second.get(key)
        assert (row['root'], row['tip'], row['delivery']) == ('native-root', 'compressed-tip', 'uncertain')
        for owner, tab in [('other-owner','t'*64), ('owner-digest','x'*64)]:
            with pytest.raises(conversations.ConversationError):
                second.resolve(owner, tab, 'h'*64, create=True)
        assert 'h'*64 not in json.dumps(row) and 't'*64 not in json.dumps(row)
    finally:
        second.close()


def test_second_worker_refuses_instead_of_sharing_a_runtime(tmp_path):
    first = conversations.Store(tmp_path/'state.sqlite3')
    try:
        with pytest.raises(conversations.ConversationError, match='one dashboard API worker'):
            conversations.Store(tmp_path/'state.sqlite3')
    finally:
        first.close()


@pytest.mark.parametrize('route', ['message','message/stream','audio-message','attachment','approval','cancel'])
@pytest.mark.parametrize('wrong', ['owner','tab'])
def test_every_route_refuses_cross_owner_or_tab_before_work(test_client, monkeypatch, durable, route, wrong):
    import session_signer
    import hermes_bridge
    session_signer._set_secret_for_tests('synthetic-durable-test')
    cookie = session_signer.issue(ttl_seconds=3600)
    owner = hermes_bridge.talk_session_key(cookie)
    durable.resolve(owner if wrong != 'owner' else 'other-owner', 't'*64, 'h'*64, create=True)
    test_client.cookies.set('ods-session',cookie)
    headers = {'X-ODS-Talk-Tab': ('x' if wrong=='tab' else 't')*64, 'X-ODS-Talk-Conversation': 'h'*64}
    if route in {'attachment','audio-message'}:
        response = test_client.post('/api/talk/'+route,headers=headers,files={'file':('fixture.txt',b'hello','text/plain')})
    else:
        response = test_client.post('/api/talk/'+route,headers=headers,json={'text':'hello','choice':'deny'})
    assert response.status_code == 409
    assert 'this tab' in response.json()['detail']


def test_missing_manual_contract_never_creates_a_replacement(monkeypatch, durable):
    import hermes_bridge as bridge
    key = durable.resolve('owner','t'*64,'h'*64,create=True)
    durable.created(key,'original-root','old-runtime')
    rpc = AsyncMock(return_value={'error':{'code':-32601}})
    monkeypatch.setattr(bridge,'_rpc',rpc)
    with pytest.raises(bridge.HermesUnavailable, match='resumed safely'):
        asyncio.run(bridge._resume_conversation(object(),key))
    assert rpc.call_args.args[1:] == ('session.resume.manual', {'session_id':'original-root'})
    assert durable.get(key)['root'] == 'original-root'


def test_enabled_image_upload_refuses_history_bypass(test_client, monkeypatch, durable):
    import hermes_bridge, session_signer
    session_signer._set_secret_for_tests('synthetic-image-test')
    cookie=session_signer.issue(ttl_seconds=3600)
    durable.resolve(hermes_bridge.talk_session_key(cookie),'t'*64,'h'*64,create=True)
    test_client.cookies.set('ods-session',cookie)
    response=test_client.post('/api/talk/attachment',headers={'X-ODS-Talk-Tab':'t'*64,'X-ODS-Talk-Conversation':'h'*64},
                             files={'file':('fixture.png',b'synthetic','image/png')})
    assert response.status_code == 409
    assert 'Image history' in response.json()['detail']


def test_unknown_runtime_status_cannot_advertise_ready(test_client, monkeypatch):
    import session_signer
    monkeypatch.setenv('ODS_TALK_DURABLE','true')
    session_signer._set_secret_for_tests('synthetic-status-test')
    test_client.cookies.set('ods-session',session_signer.issue(ttl_seconds=3600))
    monkeypatch.setattr('routers.talk._service_state',AsyncMock(return_value={'status':'healthy'}))
    monkeypatch.setattr('routers.talk._active_model_app_compatibility',AsyncMock(return_value={}))
    monkeypatch.setattr('hermes_bridge.durable_available',AsyncMock(return_value=False))
    result=test_client.get('/api/talk/status').json()
    assert result['capabilities']['text_chat'] is False
    assert result['capabilities']['audio_message'] is False
    assert result['reasonCode'] == 'continuity_unavailable'


def test_session_projects_native_text_without_exposing_runtime_identity(test_client, monkeypatch, durable):
    import session_signer
    session_signer._set_secret_for_tests('synthetic-history-test')
    test_client.cookies.set('ods-session',session_signer.issue(ttl_seconds=3600))
    monkeypatch.setattr('routers.talk._require_hermes_talk_compatible',AsyncMock(return_value={}))
    monkeypatch.setattr('hermes_bridge.open_conversation',AsyncMock(return_value={
        'messages':[{'role':'user','text':'root history'}, {'role':'assistant','text':'retained reply'},
                    {'role':'tool','text':'internal trace'}], 'running':False, 'session_id':'private-runtime'}))
    response=test_client.post('/api/talk/session',headers={'X-ODS-Talk-Tab':'t'*64,'X-ODS-Talk-Conversation':'h'*64},json={'new':True})
    assert response.status_code==200
    assert response.json()['messages']==[{'role':'user','text':'root history'},{'role':'assistant','text':'retained reply'}]
    assert 'private-runtime' not in response.text


@pytest.mark.parametrize('route', ['session','message'])
def test_disabled_durable_mode_never_downgrades_an_existing_handle(test_client, monkeypatch, route):
    import session_signer
    monkeypatch.delenv('ODS_TALK_DURABLE',raising=False)
    session_signer._set_secret_for_tests('synthetic-rollback-test')
    test_client.cookies.set('ods-session',session_signer.issue(ttl_seconds=3600))
    response=test_client.post('/api/talk/'+route,headers={'X-ODS-Talk-Tab':'t'*64,'X-ODS-Talk-Conversation':'h'*64},json={'text':'hello'})
    assert response.status_code==409
    assert 'not replaced' in response.json()['detail']
