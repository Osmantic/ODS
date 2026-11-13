"""Observed access is separate from consent/grants; cached GET never probes."""
import sys
if sys.platform != 'linux':
    from unittest import SkipTest
    raise SkipTest('Ordinary Linux/WSL Root/owner transaction fixtures')

from datetime import timedelta
import asyncio
import importlib.util
import json
import shutil
import subprocess

import pytest

import test_native_public as fixture
from pixel_access_bridge import AccessError
from pixel_connections import coordinator as c, native_access as access, native_public

world, owner, public = fixture.world, fixture.owner, fixture.public


def request(owner, operation, **fields):
    return native_public.control(owner.bridge, 'connection-app-' + operation, {'app': 'notion', **fields})


def connect(owner):
    flow, address = fixture.begin(owner)
    request(owner, 'complete', flowId=flow, address=address)
    return flow, request(owner, 'finish', flowId=flow)


def status(owner):
    return native_public.control(owner.bridge, 'connection-app-status', {})['apps']['notion']


def test_activation_records_the_same_observation_without_an_extra_provider_probe(public):
    _flow, result = connect(public)
    assert result['phase'] == 'ready' and result['portalAccessVerified']
    assert public.native_calls == ['begin', 'complete', 'probe']
    value = access.validate(json.loads((public.bridge.state / access.NAME).read_bytes()))
    assert value['proof']['permitted'] and value['proof']['scope'] == 'pixel-owner-default'
    assert (public.bridge.state / access.NAME).stat().st_mode & 0o777 == 0o600
    assert not any(word in json.dumps(value) for word in ('PRIVATE', public.auth_state, 'authorizationCode', 'authUrl'))
    before = (public.bridge.state / access.NAME).read_bytes()
    assert status(public)['portalAccessVerified'] is True
    assert (public.bridge.state / access.NAME).read_bytes() == before
    assert public.native_calls == ['begin', 'complete', 'probe']


def test_expired_enrolled_proof_uses_explicit_readonly_check_not_finish_or_new_consent(public):
    flow, _ready = connect(public)
    controller = public.controller
    journal, consent = controller.path.read_bytes(), controller.consent.path.read_bytes()
    config, record = public.bridge.config_path.read_bytes(), public.record.read_bytes()
    old = controller.now()
    controller.now = lambda: old + timedelta(seconds=301)
    assert status(public)['phase'] == 'configured'
    calls = public.native_calls[:]
    checked = request(public, 'check')
    assert checked['phase'] == 'ready' and checked['flowId'] == flow
    assert public.native_calls == calls + ['probe']
    assert (controller.path.read_bytes(), controller.consent.path.read_bytes(),
            public.bridge.config_path.read_bytes(), public.record.read_bytes()) == (journal, consent, config, record)


@pytest.mark.parametrize('drift', ['record', 'model', 'provider', 'source', 'mode', 'runtime-pid', 'runtime-proof',
    'runtime-boot', 'runtime-start', 'policy-sdk', 'native-binding', 'owner-gid', 'clock-future'])
def test_cached_ready_invalidates_current_bindings_without_provider_work(public, monkeypatch, drift):
    connect(public)
    if drift == 'record':
        c._write_record(dict(c._record(), plugins={'memory-wiki': False}))
    elif drift in ('model', 'provider', 'source'):
        fixture.lifecycle._accepted_config_update(public, drift)
    elif drift == 'mode':
        public.bridge.mode = 'full-access'
    elif drift == 'runtime-pid':
        public.bridge.gateway_service.restart(timeout=5)
    elif drift == 'runtime-proof':
        public.bridge.proof = dict(public.bridge.proof, nonce='changed-proof')
    elif drift == 'runtime-boot':
        monkeypatch.setattr(public.bridge.gateway_service, 'boot_identity', lambda: '22222222-2222-3333-4444-555555555555')
    elif drift == 'runtime-start':
        public.bridge.started += 1
    elif drift == 'policy-sdk':
        public.policy_stamp = 'changed-sdk-fixture'
    elif drift == 'native-binding':
        original = fixture.lifecycle.native.bridge_binding
        monkeypatch.setattr(fixture.lifecycle.native, 'bridge_binding',
            lambda *args: dict(original(*args), nodeSha256='e' * 64))
    elif drift == 'owner-gid':
        discover = public.bridge.discover
        def changed(**kwargs):
            discover(**kwargs)
            public.bridge.owner.pw_gid += 1
        monkeypatch.setattr(public.bridge, 'discover', changed)
    else:
        old = public.controller.now()
        public.controller.now = lambda: old - timedelta(seconds=1)
    calls = public.native_calls[:]
    assert status(public)['phase'] == 'configured'
    assert status(public)['portalAccessVerified'] is False
    assert public.native_calls == calls


def test_real_edge_chat_epoch_does_not_invalidate_the_same_proved_runtime(public, tmp_path, monkeypatch):
    connect(public)
    path = fixture.lifecycle.harness.ROOT / 'extensions/services/pixel-edge/transition_gate.py'
    spec = importlib.util.spec_from_file_location('native_cache_real_edge_gate', path)
    edge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edge)
    directory = tmp_path / 'edge-gate'
    directory.mkdir(mode=0o700)
    edge.initialize(str(directory))
    gate = edge.TransitionGate(str(directory), set())
    try:
        observed = dict(asyncio.run(gate.status()))
        original = public.bridge.edge
        def edge_snapshot(operation=None, *args, **kwargs):
            return dict(observed) if operation is None else original(operation, *args, **kwargs)
        monkeypatch.setattr(public.bridge, 'edge', edge_snapshot)
        request(public, 'check')
        old_revision = public.bridge.inspect()['revision']
        calls = public.native_calls[:]
        async def turn():
            await gate.admit('fixture-chat')
            observed.update(await gate.status())
            assert observed['phase'] == 'busy' and observed['streams'] == 1
            assert public.bridge.inspect()['busy'] and status(public)['phase'] == 'ready'
            assert public.native_calls == calls
            await gate.finish('fixture-chat')
            observed.update(await gate.status())
        asyncio.run(turn())
        assert public.bridge.inspect()['revision'] != old_revision
        assert status(public)['phase'] == 'ready' and public.native_calls == calls
        asyncio.run(gate.shutdown())
        observed.update(asyncio.run(gate.status()))
        assert observed['phase'] == 'idle' and observed['admission_blocked'] is True
        assert status(public)['phase'] == 'configured' and public.native_calls == calls
    finally:
        gate.close()


def test_real_native_direct_and_cron_completion_epochs_preserve_observed_access(public, tmp_path):
    if not shutil.which('node'):
        pytest.skip('Node is needed for the actual native admission gate')
    module = fixture.lifecycle.harness.ROOT / 'extensions/services/pixel-agent/plugin/access-runtime.mjs'
    script = r'''
import {pathToFileURL} from 'node:url';
const {createAccessRuntime}=await import(pathToFileURL(process.argv[1]).href);
const runtime=createAccessRuntime({directory:process.argv[2],runtimeVersion:'2026.6.33',hooksAllowed:true});
if(!runtime.status().available)throw new Error('controlled native gate unavailable');
const traces=[];
const record=()=>{const {revision,phase,active}=runtime.status();traces.push({revision,phase,active});};
record();
for(const context of [{runId:'native',agentId:'pixel'},{runId:'cron',agentId:'pixel',trigger:'cron'}]){
  if(runtime.admit({},context).outcome!=='pass')throw new Error('controlled admission refused');
  record();runtime.finish({},context);record();
}
const event={toolCallId:'direct-read',toolName:'fixture-read'};
if(runtime.beforeTool(event,{}))throw new Error('controlled direct admission refused');
record();runtime.afterTool(event,{});record();
process.stdout.write(JSON.stringify({traces,phase:runtime.status().phase,active:runtime.status().active}));
'''
    child = subprocess.run(['node', '--input-type=module', '-e', script, str(module), str(tmp_path / 'native-gate')],
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result['phase'] == 'idle' and result['active'] == 0 and len(result['traces']) == 7
    connect(public)
    public.bridge.nrev = result['traces'][0]['revision']
    request(public, 'check')
    calls = public.native_calls[:]
    for trace in result['traces'][1:]:
        previous = public.bridge.inspect()['revision']
        public.bridge.nrev = trace['revision']
        public.bridge.native_phase, public.bridge.active = trace['phase'], trace['active']
        assert public.bridge.inspect()['revision'] != previous
        assert public.bridge.inspect()['busy'] is (trace['phase'] == 'busy')
        assert status(public)['phase'] == 'ready' and public.native_calls == calls


@pytest.mark.parametrize('gate,phase', [('native', 'held'), ('native', 'interrupted'), ('native', 'unavailable'),
    ('edge', 'held'), ('edge', 'interrupted'), ('edge', 'unavailable')])
def test_unowned_retained_admission_is_not_a_ready_cache_hit(public, gate, phase):
    connect(public)
    setattr(public.bridge, gate + '_phase', phase)
    assert public.bridge.pending() is None and public.bridge.inspect()['runtime_verified']
    calls = public.native_calls[:]
    assert status(public)['phase'] == 'configured' and not status(public)['portalAccessVerified']
    with pytest.raises(AccessError, match='native-app-runtime-unverified'):
        request(public, 'check')
    assert public.native_calls == calls


def test_stopped_native_process_cannot_reuse_the_observed_cache(public, monkeypatch):
    connect(public)
    original = public.bridge.native
    monkeypatch.setattr(public.bridge, 'native', lambda *args, **kwargs: dict(original(*args, **kwargs), stopped=True))
    calls = public.native_calls[:]
    assert status(public)['phase'] == 'configured'
    assert public.native_calls == calls


@pytest.mark.parametrize('change', [
    lambda frame: frame.pop('admission_blocked'), lambda frame: frame.update(admission_blocked=None),
    lambda frame: frame.update(admission_blocked='false'), lambda frame: frame.update(admission_blocked=0),
    lambda frame: frame.update(streams=True), lambda frame: frame.update(streams=-1),
    lambda frame: frame.update(revision=None),
])
def test_incomplete_or_untyped_edge_admission_is_not_an_open_gate(public, monkeypatch, change):
    connect(public)
    original = public.bridge.edge
    def malformed(*args, **kwargs):
        result = original(*args, **kwargs)
        change(result)
        return result
    monkeypatch.setattr(public.bridge, 'edge', malformed)
    calls = public.native_calls[:]
    assert status(public)['phase'] == 'configured'
    with pytest.raises(AccessError, match='native-app-runtime-unverified'):
        request(public, 'check')
    assert public.native_calls == calls


@pytest.mark.parametrize('change', [lambda frame: frame.update(stopped=None), lambda frame: frame.update(stopped=0),
    lambda frame: frame.update(active=True), lambda frame: frame.update(active=-1),
    lambda frame: frame.update(revision=None)])
def test_incomplete_or_untyped_native_admission_is_not_a_ready_observation(public, monkeypatch, change):
    connect(public)
    original = public.bridge.native
    def malformed(*args, **kwargs):
        result = original(*args, **kwargs)
        change(result)
        return result
    monkeypatch.setattr(public.bridge, 'native', malformed)
    calls = public.native_calls[:]
    assert status(public)['phase'] == 'configured'
    assert public.native_calls == calls


def test_negative_check_is_not_ready_and_cannot_modify_grants_or_saved_authorization(public):
    connect(public)
    public.readiness = 'policy-blocked'
    paths = (public.controller.path, public.controller.consent.path, public.record, public.bridge.config_path,
             public.bridge.env_path, public.bridge.answers_path)
    before = {path: path.read_bytes() for path in paths}
    result = request(public, 'check')
    assert result['phase'] == 'configured' and not result['portalAccessVerified']
    assert result['error'] == 'native-app-policy-blocked'
    assert {path: path.read_bytes() for path in paths} == before
    assert public.native_calls[-1] == 'probe' and public.native_calls.count('begin') == 1
    assert status(public)['phase'] == 'configured'


@pytest.mark.parametrize('fault', ['lost-reply', 'timeout', 'crash'])
def test_unknown_new_check_does_not_reuse_an_earlier_ready_observation(public, monkeypatch, fault):
    connect(public)
    paths = (public.controller.path, public.controller.consent.path, public.record, public.bridge.config_path,
             public.bridge.env_path, public.bridge.answers_path)
    before = {path: path.read_bytes() for path in paths}
    def unknown(*args, **kwargs):
        assert not (public.bridge.state / access.NAME).exists()
        if fault == 'crash':
            raise fixture.lifecycle.harness.Crash()
        raise fixture.lifecycle.native.NativeError('runtime-operation-timeout' if fault == 'timeout' else 'native-oauth-exchange-unconfirmed')
    monkeypatch.setattr(fixture.lifecycle.native, '_check_access_locked', unknown)
    with pytest.raises(fixture.lifecycle.harness.Crash if fault == 'crash' else AccessError):
        request(public, 'check')
    assert status(public)['phase'] == 'configured' and not status(public)['portalAccessVerified']
    assert {path: path.read_bytes() for path in paths} == before


def test_check_invalidation_must_be_durable_before_the_provider_probe(public, monkeypatch):
    connect(public)
    calls = public.native_calls[:]
    monkeypatch.setattr(access.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('synthetic private disk failure')))
    with pytest.raises(AccessError, match='native-app-observation-unavailable'):
        request(public, 'check')
    assert public.native_calls == calls and not (public.bridge.state / access.NAME).exists()
    assert status(public)['phase'] == 'configured'


@pytest.mark.parametrize('problem', ['symlink', 'hardlink', 'mode', 'size', 'malformed'])
def test_unsafe_or_invalid_cache_cannot_produce_ready_and_is_not_silently_reset(public, tmp_path, problem):
    connect(public)
    path = public.bridge.state / access.NAME
    if problem == 'symlink':
        content = path.read_bytes()
        path.unlink()
        target = tmp_path / 'outside'
        target.write_bytes(content)
        path.symlink_to(target)
    elif problem == 'hardlink':
        import os
        os.link(path, tmp_path / 'linked')
    elif problem == 'mode':
        path.chmod(0o644)
    elif problem == 'size':
        path.write_bytes(b'x' * (access.MAX_BYTES + 1))
    else:
        path.write_text('{broken')
    calls = public.native_calls[:]
    assert status(public)['portalAccessVerified'] is False
    assert public.native_calls == calls
    if problem == 'malformed':
        assert request(public, 'check')['portalAccessVerified'] is True
        access.validate(json.loads(path.read_bytes()))
    else:
        with pytest.raises((AccessError, fixture.lifecycle.native.NativeError)):
            request(public, 'check')
        assert public.native_calls == calls


def test_pending_enrollment_has_precedence_over_any_cached_proof(public):
    flow, _ = connect(public)
    journal = public.controller._read()
    public.controller._put(journal, phase='verifying')
    calls = public.native_calls[:]
    result = status(public)
    assert result['phase'] == 'verifying' and result['flowId'] == flow and not result['portalAccessVerified']
    with pytest.raises(AccessError, match='native-activation-settlement-required'):
        request(public, 'check')
    assert public.native_calls == calls


def test_existing_configured_grant_without_enrollment_can_be_checked_and_disconnected(public):
    # This fixture models an already accepted managed choice. The separate
    # SourceOverlay test below proves its supported producer without deleting a
    # previously created enrollment journal.
    public.bridge.state.mkdir(mode=0o700, exist_ok=True)
    c._write_record({'schemaVersion': 1, 'kind': 'ods-portal-connections', 'updatedAt': '2026-10-28T12:00:00Z',
        'ownerTimeZone': None, 'connectors': {}, 'nativeApps': {'notion': True}})
    fixture.lifecycle._accepted_config_update(public, 'regrant')
    public.authorized = True
    assert not public.controller.path.exists() and not public.controller.consent.path.exists()
    assert status(public)['phase'] == 'configured' and status(public)['flowId'] is None
    paths = (public.record, public.bridge.config_path, public.bridge.env_path, public.bridge.answers_path)
    before = {path: path.read_bytes() for path in paths}
    result = request(public, 'check')
    assert result['phase'] == 'ready' and result['flowId'] is None and result['portalAccessVerified']
    assert {path: path.read_bytes() for path in paths} == before
    assert not public.controller.path.exists() and not public.controller.consent.path.exists()
    assert public.native_calls == ['probe']
    assert status(public)['phase'] == 'ready' and public.native_calls == ['probe']
    disconnected = request(public, 'disconnect')
    assert disconnected['phase'] == 'disconnected' and disconnected['providerRevoked'] is False
    assert fixture.lifecycle.choice(public) == {'notion': False}
    assert public.native_calls == ['probe', 'forget']


def test_terminal_withdrawal_does_not_hide_a_current_independently_accepted_grant(public):
    connect(public)
    request(public, 'disconnect')
    c._write_record(dict(c._record(), nativeApps={'notion': True}))
    fixture.lifecycle._accepted_config_update(public, 'regrant')
    public.authorized = True
    result = status(public)
    assert result['phase'] == 'configured' and result['flowId'] is None
    checked = request(public, 'check')
    assert checked['phase'] == 'ready' and checked['flowId'] is None
    assert request(public, 'disconnect')['phase'] == 'disconnected'


def test_observation_failure_preserves_grant_and_pending_recovery_instead_of_ready(public, monkeypatch):
    flow, address = fixture.begin(public)
    request(public, 'complete', flowId=flow, address=address)
    write = access.atomic_json
    monkeypatch.setattr(access, 'atomic_json', lambda *args: (_ for _ in ()).throw(OSError('synthetic private disk failure')))
    with pytest.raises(AccessError, match='native-app-observation-unavailable'):
        request(public, 'finish', flowId=flow)
    assert fixture.lifecycle.choice(public) == {'notion': True}
    assert status(public)['phase'] == 'verifying' and not status(public)['portalAccessVerified']
    monkeypatch.setattr(access, 'atomic_json', write)
    assert request(public, 'finish', flowId=flow)['portalAccessVerified']
    assert public.native_calls.count('complete') == 1
