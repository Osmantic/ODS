"""The host-side model lifecycle lock also covers hybrid access changes."""
import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'bin'))
spec = importlib.util.spec_from_file_location('access_transport_host_agent', ROOT / 'bin/ods-host-agent.py')
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
import pixel_access_relay


def handler_setup(monkeypatch):
    monkeypatch.setattr(agent, 'check_auth', lambda _:True)
    monkeypatch.setattr(agent, 'load_env', lambda _: {'PIXEL_OPENWEBUI_KEY':'c'*64})
    monkeypatch.setattr(agent, 'read_json_body', lambda _: {'mode':'sandboxed','confirmed':False,'revision':'a'*64})
    responses = []
    monkeypatch.setattr(agent, 'json_response', lambda _, status, body:responses.append((status,body)))
    return responses


def test_busy_model_operation_prevents_hybrid_access_change(monkeypatch):
    responses = handler_setup(monkeypatch)
    upstream = Mock()
    monkeypatch.setattr(pixel_access_relay, 'request_runtime_access', upstream)
    monkeypatch.setattr(agent, '_begin_model_lifecycle', lambda _: (False,'model_activation'))
    agent.AgentHandler._handle_pixel_access_mode(object(), True)
    assert responses == [(409, {'error':'model-lifecycle-busy'})]
    upstream.assert_not_called()


def test_hybrid_change_retains_lock_and_does_not_replay_after_transport_failure(monkeypatch):
    responses = handler_setup(monkeypatch)
    events = []
    monkeypatch.setattr(agent, '_begin_model_lifecycle', lambda name:(events.append(('begin',name)) or (True,None)))
    monkeypatch.setattr(agent, '_end_model_lifecycle', lambda name:events.append(('end',name)))
    upstream = Mock(side_effect=pixel_access_relay.AccessRelayError('agent-access-runtime-unavailable'))
    monkeypatch.setattr(pixel_access_relay, 'request_runtime_access', upstream)
    agent.AgentHandler._handle_pixel_access_mode(object(), True)
    assert upstream.call_count == 1
    assert responses == [(503, {'error':'access-transition-unavailable'})]
    assert events == [('begin','pixel_access_mode'),('end','pixel_access_mode')]


def test_windows_get_reports_agent_runtime_surface(monkeypatch):
    responses = handler_setup(monkeypatch)
    monkeypatch.setattr(agent.platform, 'system', lambda:'Windows')
    monkeypatch.setattr(pixel_access_relay, 'request_runtime_access', Mock(return_value=(200, {'surface':'wsl-systemd'})))
    agent.AgentHandler._handle_pixel_access_mode(object(), False)
    assert responses == [(200, {'surface':'wsl-systemd'})]
