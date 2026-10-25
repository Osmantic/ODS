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


def _bridge_fixture(monkeypatch, durable):
    import hermes_bridge as bridge
    key = durable.resolve('owner', 't'*64, 'h'*64, create=True)
    durable.created(key, 'native-root', 'runtime-one')
    snapshot = {'session_id': 'runtime-one', 'running': False, 'pending_approval': None}
    sockets = []
    def http(**kwargs):
        return SimpleNamespace(close=AsyncMock())
    async def connect(_http):
        ws = SimpleNamespace(closed=False, send_str=AsyncMock(), close=AsyncMock())
        sockets.append(ws)
        return ws
    monkeypatch.setattr(bridge.aiohttp, 'ClientSession', http)
    monkeypatch.setattr(bridge, '_connect_ws', connect)
    monkeypatch.setattr(bridge, '_resume_conversation', AsyncMock(side_effect=lambda *args: dict(snapshot)))
    return bridge, key, snapshot, sockets


def test_refresh_cannot_replace_between_prompt_lookup_and_lock_claim(monkeypatch, durable):
    bridge, key, _, _ = _bridge_fixture(monkeypatch, durable)
    async def run():
        queued = asyncio.Event()
        class ObservedLock(asyncio.Lock):
            async def acquire(self):
                if self.locked():
                    queued.set()
                return await super().acquire()
        await bridge.open_conversation(key)
        original = bridge._CONNECTION_POOL[key]
        original.lock = ObservedLock()
        await original.lock.acquire()
        used = []
        async def submit(conn, *args):
            assert not conn.closed, 'queued prompt used the connection closed by refresh'
            used.append(conn)
            yield {'type': 'complete', 'text': 'once'}
        monkeypatch.setattr(bridge, '_submit_on_connection', submit)
        prompt = asyncio.create_task(bridge.submit_prompt(key, 'explicit'))
        await queued.wait()
        try:
            # Real asyncio.Lock.release makes locked() false before the queued
            # acquirer gets its turn. Run refresh in precisely that interval.
            # The queued prompt must recheck pool identity after it wakes.
            original.lock.release()
            await bridge.open_conversation(key)
            assert original.closed
            assert (await prompt).text == 'once'
            assert used == [bridge._CONNECTION_POOL[key]]
            assert used[0] is not original
        finally:
            await asyncio.gather(prompt, return_exceptions=True)
            await bridge.shutdown_pool()
    asyncio.run(run())


def test_warm_lookup_waits_for_refresh_replacement(monkeypatch, durable):
    bridge, key, _, _ = _bridge_fixture(monkeypatch, durable)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        await bridge.open_conversation(key)
        original = bridge._CONNECTION_POOL[key]
        drop = bridge._drop_connection
        async def paused_drop(*args):
            entered.set()
            await release.wait()
            await drop(*args)
        monkeypatch.setattr(bridge, '_drop_connection', paused_drop)
        refresh = asyncio.create_task(bridge.open_conversation(key))
        await entered.wait()
        lookup = asyncio.create_task(bridge._get_connection(key))
        try:
            await asyncio.sleep(0)
            assert not lookup.done(), 'warm lookup returned the connection being replaced'
            release.set()
            await refresh
            assert await lookup is bridge._CONNECTION_POOL[key]
            assert await lookup is not original and original.closed
        finally:
            release.set()
            await asyncio.gather(refresh, lookup, return_exceptions=True)
            await bridge.shutdown_pool()
    asyncio.run(run())


def test_cold_reconnect_retains_approval_and_cancel_does_not_wait_for_prompt(monkeypatch, durable):
    bridge, key, snapshot, _ = _bridge_fixture(monkeypatch, durable)
    snapshot['pending_approval'] = {'command': 'synthetic command', 'description': 'approval fixture'}
    interrupt = AsyncMock()
    monkeypatch.setattr(bridge, '_interrupt_session_with_ack', interrupt)
    async def run():
        try:
            conn = await bridge._get_connection(key)
            async with conn.lock:
                assert await asyncio.wait_for(bridge.respond_approval(key, 'once'), .5)
                assert not await bridge.respond_approval(key, 'deny')
                sent = json.loads(conn.ws.send_str.call_args.args[0])
                assert sent['params'] == {'session_id': 'runtime-one', 'choice': 'once', 'all': False}
                assert await asyncio.wait_for(bridge.interrupt_active_prompt(key), .5)
            assert conn.closed and key not in bridge._CONNECTION_POOL
            interrupt.assert_awaited_once_with('runtime-one')
        finally:
            await bridge.shutdown_pool()
    asyncio.run(run())


def test_cancel_finishes_before_refresh_can_replace_connection(monkeypatch, durable):
    bridge, key, _, _ = _bridge_fixture(monkeypatch, durable)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def interrupt(_runtime):
            entered.set()
            await release.wait()
        monkeypatch.setattr(bridge, '_interrupt_session_with_ack', interrupt)
        await bridge.open_conversation(key)
        original = bridge._CONNECTION_POOL[key]
        cancel = asyncio.create_task(bridge.interrupt_active_prompt(key))
        await entered.wait()
        refresh = asyncio.create_task(bridge.open_conversation(key))
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not refresh.done(), 'refresh replaced the connection during cancellation'
            release.set()
            assert await cancel
            await refresh
            assert original.closed
            assert not bridge._CONNECTION_POOL[key].closed
        finally:
            release.set()
            await asyncio.gather(cancel, refresh, return_exceptions=True)
            await bridge.shutdown_pool()
    asyncio.run(run())


def test_cancel_before_first_send_prevents_late_admitted_submit(monkeypatch, durable):
    bridge, key, _, _ = _bridge_fixture(monkeypatch, durable)
    monkeypatch.setattr(bridge, '_interrupt_session_with_ack', AsyncMock())
    async def run():
        stream = bridge.stream_prompt(key, 'must not be sent after cancel')
        try:
            assert (await stream.__anext__())['type'] == 'session'
            conn = bridge._CONNECTION_POOL[key]
            assert await bridge.interrupt_active_prompt(key)
            with pytest.raises(bridge.HermesUnavailable, match='closed before submission'):
                await stream.__anext__()
            conn.ws.send_str.assert_not_awaited()
        finally:
            await stream.aclose()
            await bridge.shutdown_pool()
    asyncio.run(run())
