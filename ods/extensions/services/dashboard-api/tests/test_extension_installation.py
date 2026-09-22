import asyncio
import contextlib
import json
import os
import stat
from unittest.mock import Mock

import pytest

from extension_installation import verify_failed_attempt, retire_failed_attempt


@pytest.mark.parametrize('state', ['accepted', 'running', 'uncertain', 'succeeded', None])
def test_revision_never_retires_nonfailed_attempt(tmp_path, state):
    journal = InstallationJournal(tmp_path / 'journal.json')
    saved = {'action':'install', 'state':'accepted', 'operationId':'a'*32}
    journal.records['app'] = saved.copy(); journal.save()
    observe = Mock(return_value={'service_id':'app', 'operation_id':'a'*32, 'state':state})
    with pytest.raises(ValueError): retire_failed_attempt(journal, 'app', saved, observe)
    assert InstallationJournal(journal.path).records['app'] == saved


def test_revision_retires_only_matching_failed_attempt_and_preserves_peers(tmp_path):
    journal = InstallationJournal(tmp_path / 'journal.json')
    saved = {'action':'install', 'state':'accepted', 'operationId':'a'*32}
    peer = {'action':'install', 'state':'uncertain', 'operationId':'b'*32}
    journal.records = {'app': saved.copy(), 'peer': peer.copy()}; journal.save()
    observe = Mock(return_value={'service_id':'app', 'operation_id':'a'*32, 'state':'failed'})
    assert verify_failed_attempt(journal, 'app', observe) == saved
    for invalid in [None, {}, {'service_id':'peer', 'operation_id':'a'*32, 'state':'failed'},
                    {'service_id':'app', 'operation_id':'b'*32, 'state':'failed'}]:
        with pytest.raises(ValueError): retire_failed_attempt(journal, 'app', saved, lambda *args: invalid)
    with pytest.raises(ValueError): retire_failed_attempt(journal, 'app', peer, observe)
    retire_failed_attempt(journal, 'app', saved, observe)
    assert InstallationJournal(journal.path).records == {'peer':peer}
    with pytest.raises(ValueError): retire_failed_attempt(journal, 'app', saved, observe)


def test_revision_observation_failure_does_not_clear_attempt(tmp_path):
    journal = InstallationJournal(tmp_path / 'journal.json')
    saved = {'action':'install', 'state':'uncertain', 'operationId':'a'*32}
    journal.records['app'] = saved.copy(); journal.save()
    with pytest.raises(TimeoutError):
        retire_failed_attempt(journal, 'app', saved, Mock(side_effect=TimeoutError()))
    assert InstallationJournal(journal.path).records == {'app':saved}

from extension_installation import InstallationJournal, advance_installation
import extension_installation as installation_module
from extension_install_plan import build_install_plan
from routers import extensions


def make_plan(states, configured=True):
    definitions = {'app': {'id': 'app', 'depends_on': ['db']}, 'db': {'id': 'db'}}
    definitions['app']['env_vars'] = [{'key': 'APP_SECRET', 'required': True, 'secret': True}]
    return build_install_plan('app', [dict(id=k, status=v, installable=True) for k, v in states.items()],
                              definitions.__getitem__, lambda key: configured)


def test_dependencies_wait_for_readiness_and_repeated_requests_do_not_replay(tmp_path):
    path = tmp_path / 'journal.json'
    states = {'app': 'not_installed', 'db': 'not_installed'}
    calls = []

    def advance():
        return advance_installation(lambda: make_plan(states), InstallationJournal(path),
                                    lambda key: contextlib.nullcontext(), lambda *args: calls.append(args))

    first = advance()
    assert first['state'] == 'pending'
    assert first['dispatched'] is True
    assert calls == [('db', 'install')]
    # A stale catalog after acceptance and a process restart cannot replay it.
    retry = advance()
    assert retry['state'] == 'reconciliation_required'
    assert retry['dispatched'] is False
    states['db'] = 'installing'
    waiting = advance()
    assert waiting['state'] == 'pending'
    assert waiting['dispatched'] is False
    assert calls == [('db', 'install')]
    states['db'] = 'enabled'
    assert advance()['activeExtensionId'] == 'app'
    assert calls == [('db', 'install'), ('app', 'install')]
    states['app'] = 'enabled'
    assert advance()['state'] == 'succeeded'
    assert advance()['state'] == 'succeeded'
    assert len(calls) == 2
    assert InstallationJournal(path).records == {}


def test_missing_configuration_blocks_before_installing_any_dependency(tmp_path):
    dispatch = Mock()
    result = advance_installation(
        lambda: make_plan({'app': 'not_installed', 'db': 'not_installed'}, configured=False),
        InstallationJournal(tmp_path / 'journal.json'), lambda key: contextlib.nullcontext(), dispatch)
    assert result['state'] == 'configuration_required'
    dispatch.assert_not_called()


def test_shared_dependency_is_not_submitted_again_by_another_target(tmp_path):
    path = tmp_path / 'journal.json'
    graph = {'first': {'id': 'first', 'depends_on': ['db']},
             'second': {'id': 'second', 'depends_on': ['db']}, 'db': {'id': 'db'}}
    entries = [dict(id=key, status='not_installed', installable=True) for key in graph]
    dispatch = Mock()
    for target, expected in [('first', 'pending'), ('second', 'reconciliation_required')]:
        result = advance_installation(
            lambda: build_install_plan(target, entries, graph.__getitem__, lambda key: True),
            InstallationJournal(path), lambda key: contextlib.nullcontext(), dispatch)
        assert result['state'] == expected
        assert result['activeExtensionId'] == 'db'
    dispatch.assert_called_once_with('db', 'install')


def test_timeout_after_host_acceptance_is_durable_and_does_not_leak_errors(tmp_path):
    path = tmp_path / 'journal.json'
    read = lambda: make_plan({'app': 'not_installed', 'db': 'enabled'})
    dispatch = Mock(side_effect=TimeoutError('secret host detail'))
    result = advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), dispatch)
    assert result['state'] == 'reconciliation_required'
    assert 'secret host detail' not in json.dumps(result)
    retry = Mock()
    advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), retry)
    retry.assert_not_called()


def test_journal_precedes_external_effect_and_survives_process_interruption(tmp_path):
    path = tmp_path / 'journal.json'
    read = lambda: make_plan({'app': 'not_installed', 'db': 'enabled'})

    def crash(key, action):
        assert InstallationJournal(path).records[key]['state'] == 'dispatching'
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), crash)
    retry = Mock()
    assert advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), retry)['state'] == 'reconciliation_required'
    retry.assert_not_called()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX directory durability barrier')
def test_journal_directory_sync_failure_never_dispatches(tmp_path, monkeypatch):
    path = tmp_path / 'journal.json'
    real_fsync = os.fsync
    directory_attempts = []

    def fail_directory_sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_attempts.append(fd)
            raise OSError('directory sync failed')
        return real_fsync(fd)

    monkeypatch.setattr(installation_module.os, 'fsync', fail_directory_sync)
    read = lambda: make_plan({'app': 'not_installed', 'db': 'enabled'})
    dispatch = Mock()
    with pytest.raises(OSError, match='directory sync failed'):
        advance_installation(read, InstallationJournal(path),
                             lambda key: contextlib.nullcontext(), dispatch)
    assert directory_attempts
    dispatch.assert_not_called()
    monkeypatch.setattr(installation_module.os, 'fsync', real_fsync)
    retry = advance_installation(read, InstallationJournal(path),
                                 lambda key: contextlib.nullcontext(), dispatch)
    assert retry['state'] == 'reconciliation_required'
    dispatch.assert_not_called()


def test_manual_lifecycle_change_while_waiting_for_lock_is_observed(tmp_path):
    states = {'app': 'not_installed', 'db': 'enabled'}

    @contextlib.contextmanager
    def lock(key):
        states[key] = 'installing'
        yield

    dispatch = Mock()
    assert advance_installation(lambda: make_plan(states), InstallationJournal(tmp_path / 'journal.json'),
                                lock, dispatch)['state'] == 'pending'
    dispatch.assert_not_called()


def test_failed_or_unhealthy_dependency_never_starts_dependent(tmp_path):
    dispatch = Mock()
    result = advance_installation(lambda: make_plan({'app': 'not_installed', 'db': 'unhealthy'}),
                                  InstallationJournal(tmp_path / 'journal.json'),
                                  lambda key: contextlib.nullcontext(), dispatch)
    assert result['state'] == 'blocked'
    dispatch.assert_not_called()


def test_journal_write_failure_prevents_dispatch(tmp_path, monkeypatch):
    journal = InstallationJournal(tmp_path / 'journal.json')
    monkeypatch.setattr(journal, 'save', Mock(side_effect=OSError('disk full')))
    dispatch = Mock()
    with pytest.raises(OSError):
        advance_installation(lambda: make_plan({'app': 'not_installed', 'db': 'enabled'}),
                             journal, lambda key: contextlib.nullcontext(), dispatch)
    dispatch.assert_not_called()


@pytest.mark.parametrize('text', ['{}', 'not json', '{"schemaVersion":1,"records":{"../escape":{}}}'])
def test_corrupt_journal_cannot_be_reset_into_a_replay(tmp_path, text):
    path = tmp_path / 'journal.json'
    path.write_text(text)
    with pytest.raises(ValueError):
        InstallationJournal(path)


def test_api_uses_existing_installer_on_worker_and_health_plan_on_api_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / 'lock')
    held = []

    @contextlib.contextmanager
    def lock(key):
        held.append(key)
        yield
        held.pop()

    monkeypatch.setattr(extensions, '_extension_operation_lock', lock)
    calls = []

    def install(key, api_key, operation_id):
        assert held == [key]
        assert api_key == 'test'
        calls.append(key)
        assert len(operation_id) == 32
        return {'restart_required': False}

    monkeypatch.setattr(extensions, '_install_extension', install)
    monkeypatch.setattr(extensions, 'request_agent_json', Mock(return_value={}))

    async def run():
        api_loop = asyncio.get_running_loop()

        async def read(key, api_key):
            assert asyncio.get_running_loop() is api_loop
            return make_plan({'app': 'not_installed', 'db': 'enabled'})

        monkeypatch.setattr(extensions, 'extension_install_plan', read)
        assert (await extensions.extension_install_next('app', api_key='test'))['state'] == 'pending'
        assert (await extensions.extension_install_next('app', api_key='test'))['state'] == 'reconciliation_required'

    asyncio.run(run())
    assert calls == ['app']


def test_enable_uses_existing_endpoint_without_implicit_dependency_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / 'lock')
    monkeypatch.setattr(extensions, '_extension_operation_lock', lambda key: contextlib.nullcontext())
    enable = Mock()
    monkeypatch.setattr(extensions, 'enable_extension', Mock(__wrapped__=enable))

    async def read(key, api_key):
        return make_plan({'app': 'disabled', 'db': 'enabled'})

    monkeypatch.setattr(extensions, 'extension_install_plan', read)
    asyncio.run(extensions.extension_install_next('app', api_key='test'))
    enable.assert_called_once_with('app', auto_enable_deps=False, api_key='test')

@pytest.mark.parametrize('host_state,expected', [('accepted','pending'), ('running','pending'),
    ('failed','failed'), ('uncertain','reconciliation_required'), ('succeeded','reconciliation_required')])
def test_managed_attempt_is_observed_after_restart_without_replay(tmp_path, host_state, expected):
    path = tmp_path / 'journal.json'
    read = lambda: make_plan({'app': 'not_installed', 'db': 'enabled'})
    dispatch = Mock()
    observe = Mock()
    first = advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), dispatch, observe=observe)
    operation_id = first['operationId']
    assert len(operation_id) == 32
    dispatch.assert_called_once_with('app', 'install', operation_id=operation_id)
    observe.return_value = {'service_id':'app', 'operation_id':operation_id, 'state':host_state}
    result = advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), dispatch, observe=observe)
    assert result['state'] == expected
    assert result['dispatched'] is False
    assert dispatch.call_count == 1
    observe.assert_called_once_with('app', operation_id)


def test_old_healthy_container_does_not_erase_running_host_attempt(tmp_path):
    path = tmp_path / 'journal.json'
    states = {'app':'not_installed', 'db':'enabled'}
    read = lambda: make_plan(states)
    dispatch = Mock()
    observe = Mock()
    first = advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), dispatch, observe=observe)
    states['app'] = 'enabled'
    receipt = {'service_id':'app','operation_id':first['operationId'],'state':'running'}
    observe.return_value = receipt
    assert advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), dispatch, observe=observe)['state'] == 'pending'
    assert InstallationJournal(path).records
    receipt['state'] = 'succeeded'
    assert advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), dispatch, observe=observe)['state'] == 'succeeded'
    assert not InstallationJournal(path).records


def test_other_attempt_receipt_cannot_reconcile_installation(tmp_path):
    path = tmp_path / 'journal.json'
    read = lambda: make_plan({'app':'not_installed','db':'enabled'})
    observe = Mock(return_value={'service_id':'app','operation_id':'f'*32,'state':'failed'})
    dispatch = Mock()
    advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), dispatch, observe=observe)
    result = advance_installation(read, InstallationJournal(path), lambda key: contextlib.nullcontext(), dispatch, observe=observe)
    assert result['state'] == 'reconciliation_required'
    assert dispatch.call_count == 1

@pytest.mark.parametrize('reply,accepted', [
    ({'status':'accepted','service_id':'app','operation_id':'a'*32}, True),
    ({'status':'accepted','service_id':'other','operation_id':'a'*32}, False),
    ({'status':'accepted'}, False), ({'operation':None}, False),
    ({'operation':{'service_id':'app','operation_id':'a'*32,'state':'failed'}},False),
    ({'operation':{'service_id':'app','operation_id':'a'*32,'state':'running'}},True),
])
def test_host_acceptance_must_match_operation_and_service(monkeypatch, reply, accepted):
    request = Mock(return_value=reply)
    monkeypatch.setattr(extensions, 'request_agent_json', request)
    assert extensions._call_agent_install('app', operation_id='a'*32) is accepted
    assert request.call_args.kwargs['payload']['operation_id'] == 'a'*32
