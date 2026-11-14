import importlib.util
import json
import hashlib
import pathlib
import sys
import types

import pytest

HELPER = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "doctor-remote-provider.py"


def _load():
    spec = importlib.util.spec_from_file_location("doctor_remote_provider", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drp = _load()


def _fingerprint(provider):
    identity = {k: provider.get(k) for k in
                ("transport", "baseUrl", "model", "contextLength", "maxTokens", "reasoning")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _provider():
    return {"transport": "direct", "baseUrl": "https://api.example.com/v1",
            "model": "gpt-4o", "contextLength": 128000, "maxTokens": 4096, "reasoning": False}


def _snapshot():
    provider = _provider()
    fp = _fingerprint(provider)
    return {
        "status": "ready",
        "routeState": {"mode": "cloud", "exists": True, "valid": True, "enabled": True, "provider": provider},
        "activation": {"valid": True, "active": True, "proven": True, "reason": "active_and_proven",
                       "gateway": "litellm-cloud", "publicModel": "ods/current",
                       "routeFingerprint": fp, "model": "gpt-4o", "contextLength": 128000, "maxTokens": 4096, "reasoning": False},
        "egress": {"reachable": True, "valid": True, "ready": True},
        "capabilities": {"inference": True},
    }


def test_classify_ready():
    result = drp.classify(_snapshot())
    assert result["state"] == "ready" and result["ready"] is True
    assert result["routeFingerprint"] == _fingerprint(_provider())


@pytest.mark.parametrize("path,value", [
    (("routeState", "exists"), False),
    (("routeState", "valid"), False),
    (("routeState", "enabled"), False),
    (("activation", "valid"), False),
    (("activation", "active"), False),
    (("activation", "proven"), False),
    (("egress", "reachable"), False),
    (("egress", "valid"), False),
    (("egress", "ready"), False),
    (("capabilities", "inference"), False),
    (("routeState", "exists"), "true"),
    (("activation", "proven"), 1),
    (("egress", "ready"), None),
])
def test_classify_rejects_false_or_nonbool(path, value):
    snap = _snapshot()
    snap[path[0]][path[1]] = value
    assert drp.classify(snap)["state"] == "unproven"


def test_classify_rejects_stale_fingerprint_and_wrong_model():
    snap = _snapshot()
    snap["activation"]["routeFingerprint"] = "0" * 64
    assert drp.classify(snap)["state"] == "unproven"
    snap = _snapshot()
    snap["activation"]["model"] = "other-model"
    assert drp.classify(snap)["state"] == "unproven"


@pytest.mark.parametrize("status", ["paused", "degraded", "error"])
def test_classify_rejects_paused_degraded(status):
    snap = _snapshot()
    snap["status"] = status
    assert drp.classify(snap)["state"] == "unproven"


@pytest.mark.parametrize("url", [
    "http://example.com:3002", "https://127.0.0.1:3002", "http://10.0.0.1:3002",
    "http://user:pass@127.0.0.1:3002", "http://127.0.0.1:3002/path",
    "http://127.0.0.1:3002?x=1", "http://127.0.0.1:3002#frag",
])
def test_local_api_url_rejects_nonlocal(url):
    with pytest.raises(ValueError):
        drp.local_api_url({"ODS_DASHBOARD_API_URL": url})


def test_local_api_url_accepts_localhost():
    assert drp.local_api_url({"ODS_DASHBOARD_API_URL": "http://localhost:3002"}) == \
        "http://127.0.0.1:3002/api/remote-provider/status"


def test_no_redirect_handler_refuses():
    handler = drp.NoRedirect()
    with pytest.raises(drp.error.HTTPError):
        handler.redirect_request(drp.request.Request("http://127.0.0.1:3002/api/remote-provider/status"), None, 302, "Found", {}, "http://evil")


def _write_env(root, values):
    (root / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")


def _write_env_module(root):
    p = root / "extensions/services/dashboard-api/env_values.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("def parse_env_value(v):\n    return v.strip().strip('\"').strip(\"'\")\n", encoding="utf-8")


def test_probe_absent(tmp_path):
    assert drp.probe(tmp_path)["state"] == "absent"


def test_probe_no_key(tmp_path):
    (tmp_path / "data/remote-provider").mkdir(parents=True)
    (tmp_path / "data/remote-provider/routing-state.json").write_text("{}")
    _write_env_module(tmp_path)
    _write_env(tmp_path, {"ODS_MODE": "cloud"})
    result = drp.probe(tmp_path)
    assert result["state"] == "unavailable"


def test_probe_unavailable_on_network_error(tmp_path, monkeypatch):
    (tmp_path / "data/remote-provider").mkdir(parents=True)
    (tmp_path / "data/remote-provider/routing-state.json").write_text("{}")
    _write_env_module(tmp_path)
    _write_env(tmp_path, {"ODS_MODE": "cloud", "DASHBOARD_API_KEY": "k"})

    class Boom:
        def open(self, *a, **k):
            raise OSError("no")
    monkeypatch.setattr(drp.request, "build_opener", lambda *a, **k: Boom())
    assert drp.probe(tmp_path)["state"] == "unavailable"


def test_probe_classifies_response(tmp_path, monkeypatch):
    (tmp_path / "data/remote-provider").mkdir(parents=True)
    (tmp_path / "data/remote-provider/routing-state.json").write_text("{}")
    _write_env_module(tmp_path)
    _write_env(tmp_path, {"ODS_MODE": "cloud", "DASHBOARD_API_KEY": "k"})
    body = json.dumps(_snapshot()).encode()

    class Resp:
        status = 200
        def read(self, n):
            return body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class Opener:
        def open(self, *a, **k):
            return Resp()
    monkeypatch.setattr(drp.request, "build_opener", lambda *a, **k: Opener())
    monkeypatch.setattr(drp, "hermes_disabled", lambda root: True)
    result = drp.probe(tmp_path)
    assert result["state"] == "ready" and result["hermesDisabled"] is True


def test_hermes_disabled_marker_and_no_running(tmp_path, monkeypatch):
    hermes = tmp_path / "extensions/services/hermes"
    hermes.mkdir(parents=True)
    (hermes / "compose.yaml.disabled").write_text("")
    monkeypatch.setattr(drp.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="other\n"))
    assert drp.hermes_disabled(tmp_path) is True


@pytest.mark.parametrize('row', ['ods-hermes', 'ods-hermes-1', 'custom-name\thermes'])
def test_hermes_disabled_running_container(tmp_path, monkeypatch, row):
    hermes = tmp_path / "extensions/services/hermes"
    hermes.mkdir(parents=True)
    (hermes / "compose.yaml.disabled").write_text("")
    monkeypatch.setattr(drp.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=row + "\n"))
    assert drp.hermes_disabled(tmp_path) is False


def test_hermes_disabled_active_fragment(tmp_path, monkeypatch):
    hermes = tmp_path / "extensions/services/hermes"
    hermes.mkdir(parents=True)
    (hermes / "compose.yaml").write_text("")
    (hermes / "compose.yaml.disabled").write_text("")
    monkeypatch.setattr(drp.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=""))
    assert drp.hermes_disabled(tmp_path) is False


def test_hermes_disabled_missing_marker(tmp_path, monkeypatch):
    hermes = tmp_path / "extensions/services/hermes"
    hermes.mkdir(parents=True)
    monkeypatch.setattr(drp.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=""))
    assert drp.hermes_disabled(tmp_path) is False


@pytest.mark.parametrize('ready,disabled,expected', [
    (True, True, []),
    (True, False, ['ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE']),
    (False, True, ['ODS-RUNTIME-REMOTE-PROVIDER-NOT-READY', 'ODS-RUNTIME-CLOUD-OVERLAY-MISSING']),
])
def test_remote_contract_keeps_real_failures(tmp_path, monkeypatch, ready, disabled, expected):
    from test_doctor_vllm_route import _contract
    for key in ('EXTERNAL_LLM_URL', 'NATIVE_LLM_BASE_URL', 'LEMONADE_EXTERNAL', 'AMD_INFERENCE_RUNTIME'):
        monkeypatch.delenv(key, raising=False)
    _write_env(tmp_path, {'ODS_MODE': 'cloud', 'LLM_BACKEND': 'llama-server',
                        'LLM_API_URL': 'http://litellm:4000',
                        'HERMES_LLM_BASE_URL': 'http://model-router:9099/v1'})
    (tmp_path / '.compose-flags').write_text('-f docker-compose.base.yml -f docker-compose.nvidia.yml\n')
    report = _contract(tmp_path)({'state': 'ready' if ready else 'unproven',
                                'ready': ready, 'hermesDisabled': disabled})
    assert [i['id'] for i in report['issues']] == expected


@pytest.mark.parametrize('field,value', [('contextLength', True), ('maxTokens', '4096'),
                                       ('reasoning', 0), ('baseUrl', {}), ('transport', 'unknown')])
def test_invalid_provider_is_not_ready_even_with_matching_digest(field, value):
    snap = _snapshot()
    snap['routeState']['provider'][field] = value
    snap['activation']['routeFingerprint'] = _fingerprint(snap['routeState']['provider'])
    assert drp.classify(snap)['ready'] is False
