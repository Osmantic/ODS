import os
from pathlib import Path
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider.config import default_config
from pixel_provider.scopes import ScopeStore, native_key, handle
from pixel_provider.store import ProviderStore, StoreError

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='private POSIX scope store')


@pytest.fixture
def store(tmp_path):
    root = tmp_path / 'pixel-providers'; root.mkdir(mode=0o700)
    config = default_config(); config['enabled'] = True
    config['providers'] = [dict(id=name, label=name, kind='local', baseUrl='http://127.0.0.1:10001/v1',
        model=name, contextTokens=32768, maxOutputTokens=4096, supportsTools=True,
        supportsVision=False, reasoning=False, credentialRef=None, enabled=True) for name in ('leader', 'stronger')]
    config['roles'].update(leader='leader', handoff='stronger')
    ProviderStore(root).save(config, expected_revision=0)
    return ScopeStore(root)


def change(store, action, chat='Chat_A', **extra):
    status = store.status(chat)
    return store.change(action, dict(chatId=chat, expectedRevision=status['revision'],
        taskId=status['taskId'], **extra))


def begin(store, chat='Chat_A'):
    status = store.status(chat)
    return store.change('begin', dict(chatId=chat, taskId=str(uuid.uuid4()), expectedRevision=status['revision']))


def select(store, scope, chat='Chat_A', **extra):
    return change(store, 'select', chat, scope=scope, providerId='stronger', providerRevision=1,
        allowCloud=extra.get('allowCloud', False), acceptUnknownCost=extra.get('acceptUnknownCost', False))


def test_pristine_read_creates_nothing(tmp_path):
    result = handle(tmp_path, 'status', {'chatId': 'untouched'})
    assert result['revision'] == 0 and result['effectiveSelection'] is None
    assert list(tmp_path.iterdir()) == []


def test_explicit_task_lives_across_runs_until_owner_end(store):
    initial = begin(store)
    selected = select(store, 'task')
    assert selected['taskId'] == initial['taskId']
    first = store.resolve(native_key('Chat_A'), 1)
    assert first['scope'] == 'task' and first['providerId'] == 'stronger'
    assert store.resolve(native_key('Chat_A'), 1) == first
    assert store.status('Chat_A')['revision'] == selected['revision']  # reads/runs do not create tasks
    ended = change(store, 'end')
    assert ended['taskId'] is None and store.resolve(native_key('Chat_A'), 1) is None
    with pytest.raises(StoreError, match='replayed'):
        store.change('begin', dict(chatId='Chat_A', taskId=initial['taskId'], expectedRevision=ended['revision']))


def test_conversation_survives_task_end_and_explicit_return(store):
    begin(store); select(store, 'conversation'); select(store, 'task')
    assert change(store, 'return', scope='task')['effectiveScope'] == 'conversation'
    assert change(store, 'end')['effectiveScope'] == 'conversation'
    assert begin(store)['effectiveScope'] == 'conversation'
    assert change(store, 'return', scope='conversation')['effectiveSelection'] is None


def test_task_uuid_replay_is_global_and_survives_new_store_instance(store):
    first = begin(store)
    change(store, 'end')
    reopened = ScopeStore(store.directory)
    with pytest.raises(StoreError, match='replayed'):
        reopened.change('begin', dict(chatId='Chat_B', taskId=first['taskId'],
            expectedRevision=reopened.status('Chat_B')['revision']))


def test_conversation_override_beats_new_task_default_after_end(store):
    begin(store); select(store, 'conversation'); select(store, 'default')
    change(store, 'end')
    state = begin(ScopeStore(store.directory))
    assert state['defaultSnapshot'] is not None and state['effectiveScope'] == 'conversation'
    assert change(store, 'return', scope='conversation')['effectiveScope'] == 'default'


def test_default_applies_only_to_new_explicit_tasks(store):
    begin(store)
    assert select(store, 'default')['effectiveSelection'] is None
    assert store.resolve(native_key('unknown'), 1) is None
    assert begin(store, 'Chat_B')['effectiveScope'] == 'default'
    assert change(store, 'return', scope='default')['defaultSelection'] is None
    assert store.status('Chat_B')['effectiveScope'] == 'default'
    change(store, 'end', 'Chat_B')
    assert begin(store, 'Chat_B')['effectiveSelection'] is None


def test_ingress_case_sensitive_hash_binding_and_other_sessions(store):
    import hashlib
    begin(store); select(store, 'task')
    assert native_key('Chat_A') == 'agent:pixel:openai-user:ods-' + hashlib.sha256(b'Chat_A').hexdigest()
    assert store.resolve(native_key('chat_a'), 1) is None
    for key in ('Chat_A', 'agent:pixel:openai-user:Chat_A', 'agent:other:openai-user:ods-'+'a'*64, None):
        with pytest.raises(StoreError, match='session-unavailable'): store.resolve(key, 1)


def test_stale_scope_and_provider_revisions_fail_closed(store):
    begin(store); select(store, 'task')
    with pytest.raises(StoreError, match='stale-revision'):
        store.change('return', dict(chatId='Chat_A', taskId=store.status('Chat_A')['taskId'], scope='task', expectedRevision=0))
    config = ProviderStore(store.directory).load()
    ProviderStore(store.directory).save(config, expected_revision=1)
    for revision in (1, 2):
        with pytest.raises(StoreError, match='stale-provider-revision'): store.resolve(native_key('Chat_A'), revision)


@pytest.mark.parametrize('cloud,cost', [(False, False), (True, False), (False, True), (True, True)])
def test_cloud_needs_both_explicit_consents(store, cloud, cost):
    config = ProviderStore(store.directory).load()
    config['providers'][1].update(kind='cloud', baseUrl='https://api.example/v1', credentialRef='synthetic-test-ref')
    config['policy']['allowCloud'] = True
    ProviderStore(store.directory).save(config, expected_revision=1)
    state = begin(store)
    body = dict(chatId='Chat_A', taskId=state['taskId'], expectedRevision=state['revision'], scope='task',
        providerId='stronger', providerRevision=2, allowCloud=cloud, acceptUnknownCost=cost)
    if cloud and cost:
        assert store.change('select', body)['checkpointApproval'] == 'required-each-handoff-run'
        assert store.resolve(native_key('Chat_A'), 2)['allowCloud'] is True
    else:
        with pytest.raises(StoreError, match='cloud-transfer'): store.change('select', body)


def test_concurrent_writers_have_one_winner_and_unchanged_provider_config(store):
    before = (store.directory / 'provider-config.json').read_bytes()
    initial = begin(store)
    body = dict(chatId='Chat_A', taskId=initial['taskId'], expectedRevision=initial['revision'], scope='conversation')
    def attempt():
        try: store.change('return', body); return 'saved'
        except StoreError as error: return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ['saved', 'stale-revision']
    assert (store.directory / 'provider-config.json').read_bytes() == before


def test_private_storage_and_corruption_are_not_silently_reset(store):
    begin(store)
    path = store.directory / 'provider-scopes.json'
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(StoreError, match='unsafe-file'): store.status('Chat_A')
    path.chmod(0o600); path.write_text('{"revision":1,"revision":0}')
    with pytest.raises(StoreError, match='malformed-json'): store.status('Chat_A')


@pytest.mark.parametrize('field,value', [('expectedRevision', True), ('taskId', '../bad'), ('chatId', 'bad\n'), ('extra', True)])
def test_strict_request_fields(store, field, value):
    body = dict(chatId='Chat_A', taskId=str(uuid.uuid4()), expectedRevision=0)
    body[field] = value
    with pytest.raises(StoreError): store.change('begin', body)


def test_wrong_task_cannot_end_or_change_selection(store):
    first = begin(store)
    with pytest.raises(StoreError, match='already-active'): begin(store)
    for action in ('end', 'return'):
        body = dict(chatId='Chat_A', taskId=str(uuid.uuid4()), expectedRevision=first['revision'])
        if action == 'return': body['scope'] = 'conversation'
        with pytest.raises(StoreError, match='task-mismatch'): store.change(action, body)


def test_native_worker_freezes_selection_without_granting_checkpoint_approval(store):
    from pixel_provider.route_worker import scoped_request, validate_request
    begin(store); select(store, 'task')
    request = dict(schemaVersion=1, runId=str(uuid.uuid4()), sessionId=str(uuid.uuid4()),
        expectedRevision=1, confirmed=True, allowCloud=False, timeoutSeconds=60, scopeSessionKey=native_key('Chat_A'))
    frozen = scoped_request(store.directory, validate_request(request))
    assert frozen['handoffProviderId'] == 'stronger'
    assert 'approved' not in frozen and 'checkpointDigest' not in frozen
    change(store, 'return', scope='task')
    assert frozen['handoffProviderId'] == 'stronger'  # already acquired run is immutable
    assert 'handoffProviderId' not in scoped_request(store.directory, request)
    with pytest.raises(StoreError): validate_request(dict(request, handoffProviderId='stronger'))


@pytest.mark.parametrize('activation,scope', [(False, False), (False, True), (True, False), (True, True)])
def test_scope_cannot_widen_activation_cloud_permission(store, monkeypatch, activation, scope):
    from pixel_provider.route_worker import scoped_request
    monkeypatch.setattr(ScopeStore, 'resolve', lambda *_: dict(providerId='stronger', allowCloud=scope, scope='task'))
    request = dict(schemaVersion=1, runId=str(uuid.uuid4()), sessionId=str(uuid.uuid4()),
        expectedRevision=1, confirmed=True, allowCloud=activation, timeoutSeconds=60, scopeSessionKey=native_key('Chat_A'))
    frozen = scoped_request(store.directory, request)
    assert frozen['allowCloud'] is (activation and scope)
    assert request['allowCloud'] is activation


def test_real_saved_cloud_scope_respects_local_only_activation(store):
    from pixel_provider.route_worker import scoped_request
    config = ProviderStore(store.directory).load()
    config['providers'][1].update(kind='cloud', baseUrl='https://api.example/v1', credentialRef='synthetic-test-ref')
    config['policy']['allowCloud'] = True
    ProviderStore(store.directory).save(config, expected_revision=1)
    state = begin(store)
    store.change('select', dict(chatId='Chat_A', taskId=state['taskId'], expectedRevision=state['revision'], scope='task',
        providerId='stronger', providerRevision=2, allowCloud=True, acceptUnknownCost=True))
    request = dict(schemaVersion=1, runId=str(uuid.uuid4()), sessionId=str(uuid.uuid4()), expectedRevision=2,
        confirmed=True, allowCloud=False, timeoutSeconds=60, scopeSessionKey=native_key('Chat_A'))
    frozen = scoped_request(store.directory, request)
    assert frozen['handoffProviderId'] == 'stronger' and frozen['allowCloud'] is False
    assert store.resolve(native_key('Chat_A'), 2)['allowCloud'] is True  # owner preference preserved
