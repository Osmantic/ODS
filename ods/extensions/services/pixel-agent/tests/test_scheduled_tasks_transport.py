"""Managed-install binding and protected schedule protocol boundaries."""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('The root coordinator uses POSIX custody and owner pipes')

import contextlib
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import socketserver
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'bin'))
import pixel_access_bridge as bridge  # noqa: E402
import pixel_access_protocol as protocol  # noqa: E402
import pixel_schedules as contract  # noqa: E402
import pixel_access_relay as relay  # noqa: E402


def test_runtime_mirrors_are_exact_and_installed_image_includes_the_contract():
    canonical = (ROOT / 'bin/pixel_schedules.py').read_bytes()
    for path in ('extensions/services/dashboard-api/pixel_schedules_public.py', 'extensions/services/pixel-edge/schedule_contract.py'):
        assert (ROOT / path).read_bytes() == canonical
    assert 'COPY schedule_contract.py ./' in (ROOT / 'extensions/services/pixel-edge/Dockerfile').read_text()


def test_only_fixed_source_proofs_are_accepted():
    base = {'operation': 'schedules', 'request': {'action': 'list', 'offset': 0},
            'source': {'kind': 'owner-key', 'identity': 'a' * 64}}
    assert protocol.control_request(base) == base
    for source in ({'kind': 'path', 'identity': 'a' * 64}, {'kind': 'owner-key', 'identity': 'token'},
                   {'kind': 'owner-key', 'identity': 'a' * 64, 'port': 1234}):
        with pytest.raises(protocol.ProtocolError):
            protocol.control_request(dict(base, source=source))
    with pytest.raises(protocol.ProtocolError):
        protocol.control_request(dict(base, request={'action': 'list', 'offset': 0, 'agentId': 'other'}))


@pytest.fixture
def bound_adapter(tmp_path):
    adapter = bridge.SystemdAccessBridge(tmp_path, 'o' * 64)
    adapter.native_port = 18789
    snapshot = {'configSha256': 'c' * 64, 'pending': False}
    adapter.worker = Mock(side_effect=lambda operation, **_kwargs: snapshot if operation == 'settings-status' else {'kind': 'change', 'outcome': 'unconfirmed', 'task': None})
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(adapter, 'bounded', return_value=contextlib.nullcontext()))
        stack.enter_context(patch.object(adapter, 'locked', return_value=contextlib.nullcontext()))
        discover = stack.enter_context(patch.object(adapter, 'discover'))
        pending = stack.enter_context(patch.object(adapter, 'pending', return_value=None))
        source = stack.enter_context(patch.object(adapter, 'settings_source', return_value='d' * 64))
        yield adapter, snapshot, discover, pending, source


def test_hybrid_ingress_proof_is_bound_to_the_existing_owner_key(bound_adapter):
    adapter, _snapshot, discover, _pending, source = bound_adapter
    request = {'action': 'list', 'offset': 0}
    proof = {'kind': 'owner-key', 'identity': hashlib.sha256(('o' * 64).encode()).hexdigest()}
    adapter.schedules(request, source=proof)
    discover.assert_called_once()
    source.assert_not_called()  # Windows and WSL data paths need not be equal.
    adapter.worker.assert_called_with('schedules', config_hash='c' * 64, schedule_request=request, gateway_port=18789)
    adapter.worker.reset_mock()
    with pytest.raises(bridge.AccessError, match='schedule-installation-mismatch'):
        adapter.schedules(request, source=dict(proof, identity='a' * 64))
    adapter.worker.assert_not_called()


def test_direct_host_must_match_the_managed_data_directory(bound_adapter):
    adapter, _snapshot, _discover, _pending, source = bound_adapter
    adapter.schedules({'action': 'list', 'offset': 0}, source={'kind': 'data-directory', 'identity': 'd' * 64})
    source.assert_called_once()
    adapter.worker.reset_mock()
    with pytest.raises(bridge.AccessError, match='schedule-installation-mismatch'):
        adapter.schedules({'action': 'list', 'offset': 0}, source={'kind': 'data-directory', 'identity': 'a' * 64})
    adapter.worker.assert_not_called()


def test_pending_changes_block_start_but_leave_pause_available(bound_adapter):
    adapter, snapshot, _discover, pending, _source = bound_adapter
    proof = {'kind': 'data-directory', 'identity': 'd' * 64}
    task = {'action': 'set-enabled', 'id': 'fixture-task', 'revision': 'a' * 64, 'enabled': True}
    pending.return_value = {'phase': 'held'}
    with pytest.raises(bridge.AccessError, match='runtime-change-pending'):
        adapter.schedules(task, source=proof)
    adapter.worker.assert_not_called()
    adapter.schedules(dict(task, enabled=False), source=proof)
    pending.return_value = None
    snapshot['pending'] = True
    adapter.worker.reset_mock()
    with pytest.raises(bridge.AccessError, match='runtime-change-pending'):
        adapter.schedules(task, source=proof)
    assert [call.args[0] for call in adapter.worker.call_args_list] == ['settings-status']


def test_larger_run_results_do_not_expand_other_owner_frames():
    result = {'kind': 'runs', 'taskId': 'fixture-task', 'items': [{'timestamp': 1, 'status': 'ok',
              'summary': 'A' * 30000, 'summaryTruncated': False, 'durationMs': 1}], 'offset': 0, 'hasMore': False, 'nextOffset': None}
    frame = json.dumps({'result': result}) + '\n'
    assert protocol.MAX_REPLY == 8192
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_frame(frame, protocol.MAX_REPLY)
    decoded = protocol.decode_frame(frame, protocol.SCHEDULE_REPLY)
    assert protocol.result('schedules', decoded['result']) == result
    with pytest.raises(contract.ScheduleError):
        contract.validate_reply({'action': 'runs', 'id': 'another-task', 'offset': 0}, result)


@pytest.mark.parametrize('system', ['Darwin', 'Windows', 'Linux'])
def test_hybrid_native_schedule_reuses_fixed_authenticated_edge_transport(monkeypatch, system):
    request = {'action': 'list', 'offset': 0}
    result = {'kind': 'tasks', 'items': [], 'pendingCreates': [], 'pendingCount': 0,
              'pendingRemovals': [], 'offset': 0, 'hasMore': False, 'nextOffset': None}
    # Use the real shared reply validator; only the HTTP service is simulated.
    contract.validate_reply(request, result)
    monkeypatch.setattr(relay.platform, 'system', lambda: system)
    transport = Mock(return_value=(200, result))
    monkeypatch.setattr(relay, '_request_runtime_controller', transport)
    config = {'PIXEL_OPENWEBUI_KEY': 'fixed-edge-key'}
    assert relay.request_runtime_schedules(request, config=config, data_dir='/owned/data') == (200, result)
    transport.assert_called_once_with(request, config=config, path='/v1/schedules', timeout=190)


def test_native_schedule_does_not_guess_a_transport_or_enable_connections(monkeypatch):
    monkeypatch.setattr(relay.platform, 'system', lambda: 'Darwin')
    transport = Mock(side_effect=AssertionError('unconfigured native transport'))
    monkeypatch.setattr(relay, '_request_runtime_controller', transport)
    assert relay.request_runtime_schedules({'action': 'list', 'offset': 0}, config={}, data_dir='/owned/data')[0] == 503
    assert relay.request_runtime_connection_action('connection-sync', {}, config={'PIXEL_OPENWEBUI_KEY': 'edge'})[0] == 409
    transport.assert_not_called()


@pytest.mark.parametrize('system,status', [('Darwin', 200), ('Linux', 200), ('Windows', 503)])
def test_actual_root_handler_routes_only_supported_native_schedules(system, status):
    source = ROOT / 'extensions/services/pixel-agent/host/access_mode_server.py'
    tree = ast.parse(source.read_text())
    handler = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == 'Handler')
    action = Mock(return_value={'kind': 'tasks'})
    scope = {'socketserver': socketserver, 'owner': SimpleNamespace(pw_uid=os.getuid()),
             'peer_ids': lambda _socket: (os.getuid(), os.getgid()),
             'control_request': protocol.control_request, 'decode_frame': protocol.decode_frame,
             'platform': SimpleNamespace(system=lambda: system), 'AccessError': bridge.AccessError,
             'make_adapter': lambda: SimpleNamespace(schedules=action), 'json': json}
    # Execute the actual nested handler, not a mirror of its dispatch. Root
    # publication/UID custody is separately qualified; peer identity is explicit
    # here and no installed daemon/socket is selected.
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), 'exec'), scope)
    request = {'operation': 'schedules', 'request': {'action': 'list', 'offset': 0},
               'source': {'kind': 'owner-key', 'identity': 'a' * 64}}

    def send(value):
        instance = object.__new__(scope['Handler'])
        instance.connection = SimpleNamespace(settimeout=lambda _seconds: None)
        instance.rfile, instance.wfile = io.BytesIO(json.dumps(value).encode() + b'\n'), io.BytesIO()
        instance.handle()
        return json.loads(instance.wfile.getvalue())

    assert send(request)['status'] == status
    if status == 200:
        action.assert_called_once_with(request['request'], source=request['source'])
    else:
        action.assert_not_called()
    action.reset_mock()
    scope['peer_ids'] = lambda _socket: (os.getuid() + 1, os.getgid())
    assert send(request)['status'] == 403
    action.assert_not_called()
    scope['peer_ids'] = lambda _socket: (os.getuid(), os.getgid())
    assert send(dict(request, source={'kind': 'owner-key', 'identity': 'wrong'}))['status'] == 400
    action.assert_not_called()
