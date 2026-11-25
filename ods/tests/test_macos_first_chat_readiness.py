"""Focused contracts for the macOS installer first-chat readiness probe.

Drives ``run_probe`` with injected HTTP and clock so delayed proof, terminal
errors, wrong identities, auth/HTTP failures, route sequence races, bootstrap
current-model selection, Pixel/cloud gates, credential non-leakage, and
timeout are all deterministic and Docker-free.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/dashboard-api"))

PATH = Path(__file__).resolve().parents[1] / "installers/macos/lib/first-chat-readiness.py"
spec = importlib.util.spec_from_file_location("first_chat_readiness", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.now += seconds


class FakeHttp:
    """Scripted HTTP responder keyed by URL suffix."""

    def __init__(self, clock: Clock, script: dict) -> None:
        self.clock = clock
        self.script = script
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, headers: dict, timeout: float):
        self.calls.append((url, dict(headers), timeout))
        self.clock.now += 0.1
        for suffix, responder in self.script.items():
            if url.endswith(suffix):
                if callable(responder):
                    return responder(self.clock, headers)
                return responder
        raise mod.ProbeError(f"unexpected url {url}")


def _result(status: int, body: dict | bytes | None = None) -> mod._HttpResult:
    if body is None:
        raw = b""
    elif isinstance(body, bytes):
        raw = body
    else:
        raw = json.dumps(body).encode("utf-8")
    return mod._HttpResult(status, raw)


RUNTIME = "/Users/michaelbradley/ods/data/models/Qwen3.5-9B-Q4_K_M.gguf"
GGUF = "Qwen3.5-9B-Q4_K_M.gguf"
CATALOG = "qwen3.5-9b-q4"


def _verified_state(runtime: str = RUNTIME, seq: int = 2) -> dict:
    return {
        "valid": True,
        "seq": seq,
        "routeSeq": seq,
        "active": {
            "routeSeq": seq,
            "runtimeModelId": runtime,
            "catalogId": CATALOG,
            "backend": {"kind": "llama-server"},
            "contextLength": 65536,
            "verifiedAt": "2025-01-01T00:00:00Z",
            "reconstructed": False,
            "proof": {"completion": True, "identity": runtime},
        },
    }


def _unverified_state() -> dict:
    state = _verified_state(seq=1)
    state["active"]["verifiedAt"] = None
    state["active"]["proof"] = {"completion": False, "identity": None}
    return state


def _router_health(state: dict, **overrides) -> dict:
    body = {
        "status": "ok",
        "endpointCount": 1,
        "instanceId": "router-test-instance",
        "hasRoute": True,
        "seq": state["seq"],
        "routeSeq": state["routeSeq"],
        "modelSwapGateActive": False,
    }
    body.update(overrides)
    return body


def _router_models(state: dict, **overrides) -> dict:
    active = state["active"]
    ods = {
        "routedModel": active["runtimeModelId"],
        "catalogId": active["catalogId"],
        "backend": "llama-server",
        "routeSeq": state["routeSeq"],
        "contextLength": active["contextLength"],
    }
    ods.update(overrides)
    return {"object": "list", "data": [], "ods": ods}


def _expected() -> dict:
    return {
        "llmModel": CATALOG,
        "ggufFile": GGUF,
        "catalogId": None,
        "contextSize": 65536,
    }


def _run(clock: Clock, script: dict, *, timeout: float = 30.0, interval: float = 1.0):
    http = FakeHttp(clock, script)
    mod.run_probe(
        agent_url="http://agent",
        dashboard_url="http://dash",
        router_url="http://router",
        agent_key="agent-secret",
        dashboard_key="dash-secret",
        expected=_expected(),
        timeout=timeout,
        interval=interval,
        http_get=http,
        sleep=clock.sleep,
        clock=clock.monotonic,
    )
    return http


def _happy_script(state: dict) -> dict:
    return {
        "/v1/model/status": _result(200, {"status": "idle", "modelTransactionPending": False}),
        "/api/models/state": _result(200, state),
        "/health": _result(200, _router_health(state)),
        "/v1/models": _result(200, _router_models(state)),
    }


def test_delayed_proof_succeeds_after_seq2():
    clock = Clock()
    state_calls = {"n": 0}
    verified = _verified_state()

    def state(clock, headers):
        state_calls["n"] += 1
        if state_calls["n"] < 3:
            return _result(200, _unverified_state())
        return _result(200, verified)

    script = _happy_script(verified)
    script["/api/models/state"] = state
    http = _run(clock, script)
    assert state_calls["n"] >= 3
    assert any(call[0].endswith("/v1/model/status") for call in http.calls)


def test_initial_verified_seq1_is_accepted():
    clock = Clock()
    state = _verified_state(seq=1)
    _run(clock, _happy_script(state))


def test_agent_auth_failure_is_terminal():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/v1/model/status"] = _result(401)
    with pytest.raises(mod.ProbeError, match="host agent rejected authentication"):
        _run(clock, script)


def test_dashboard_auth_failure_is_terminal():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/api/models/state"] = _result(403)
    with pytest.raises(mod.ProbeError, match="dashboard API rejected authentication"):
        _run(clock, script)


def test_wrong_identity_never_succeeds():
    clock = Clock()
    wrong = _verified_state(runtime="some-other-model.gguf")
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, _happy_script(wrong), timeout=3.0, interval=1.0)


def test_substring_identity_is_rejected():
    clock = Clock()
    # A runtime id that merely contains the expected basename must not match.
    wrong = _verified_state(runtime="prefix-Qwen3.5-9B-Q4_K_M.gguf-suffix")
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, _happy_script(wrong), timeout=2.0, interval=1.0)


def test_reconstructed_route_is_rejected():
    clock = Clock()
    state = _verified_state()
    state["active"]["reconstructed"] = True
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, _happy_script(state), timeout=2.0, interval=1.0)


def test_router_hasroute_false_blocks_success():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/health"] = _result(200, _router_health(state, hasRoute=False))
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_router_degraded_blocks_success():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/health"] = _result(
        200, _router_health(state, status="degraded", endpointCount=0)
    )
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_router_swap_gate_blocks_success():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/health"] = _result(200, _router_health(state, modelSwapGateActive=True))
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_router_seq_mismatch_blocks_success():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/health"] = _result(200, _router_health(state, routeSeq=state["routeSeq"] + 1))
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_router_models_mismatch_blocks_success():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/v1/models"] = _result(
        200, _router_models(state, routedModel="some-other-model.gguf")
    )
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_router_http_failure_blocks_success():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/health"] = _result(503)
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_router_http_500_blocks_success():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/health"] = _result(500)
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_router_http_404_blocks_success():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/health"] = _result(404)
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_malformed_json_blocks_success():
    clock = Clock()
    state = _verified_state()
    script = _happy_script(state)
    script["/health"] = _result(200, b"not-json")
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_route_sequence_race_requires_verified_identity():
    clock = Clock()
    calls = {"n": 0}
    verified = _verified_state()

    def state(clock, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            return _result(200, verified)
        raced = _verified_state(seq=3)
        raced["active"]["verifiedAt"] = None
        raced["active"]["proof"] = {"completion": False, "identity": None}
        return _result(200, raced)

    script = _happy_script(verified)
    script["/api/models/state"] = state
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_state_change_during_observation_blocks_success():
    clock = Clock()
    verified = _verified_state()
    calls = {"n": 0}

    def state(clock, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            return _result(200, verified)
        # Second read (recheck) shows a newer seq: must not accept.
        return _result(200, _verified_state(seq=3))

    script = _happy_script(verified)
    script["/api/models/state"] = state
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2.0, interval=1.0)


def test_bootstrap_current_model_is_accepted():
    clock = Clock()
    state = _verified_state()
    _run(clock, _happy_script(state))


def test_timeout_is_bounded():
    clock = Clock()
    state = _unverified_state()
    script = _happy_script(state)
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=5.0, interval=1.0)
    assert clock.now <= 5.0 + 1.0


def test_slow_request_does_not_overrun_deadline():
    clock = Clock()
    state = _unverified_state()
    script = _happy_script(state)

    def slow(clock, headers):
        clock.now += 4.0
        return _result(200, state)

    script["/api/models/state"] = slow
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=5.0, interval=1.0)
    assert clock.now <= 5.0 + 1.0


def test_invalid_timeout_rejected():
    clock = Clock()
    with pytest.raises(mod.ProbeError, match="timeout must be"):
        _run(clock, {}, timeout=0.0, interval=1.0)
    with pytest.raises(mod.ProbeError, match="timeout must be"):
        _run(clock, {}, timeout=float("inf"), interval=1.0)


def test_invalid_interval_rejected():
    clock = Clock()
    with pytest.raises(mod.ProbeError, match="interval must be"):
        _run(clock, {}, timeout=1.0, interval=0.0)
    with pytest.raises(mod.ProbeError, match="interval must be"):
        _run(clock, {}, timeout=1.0, interval=float("nan"))


def test_missing_keys_fail_closed():
    clock = Clock()
    with pytest.raises(mod.ProbeError, match="missing authentication key"):
        mod.run_probe(
            agent_url="http://agent",
            dashboard_url="http://dash",
            router_url="http://router",
            agent_key="",
            dashboard_key="dash-secret",
            expected=_expected(),
            timeout=1.0,
            interval=0.1,
            http_get=FakeHttp(clock, {}),
            sleep=clock.sleep,
            clock=clock.monotonic,
        )


def test_missing_identity_fails_closed():
    clock = Clock()
    with pytest.raises(mod.ProbeError, match="no configured model identity"):
        mod.run_probe(
            agent_url="http://agent",
            dashboard_url="http://dash",
            router_url="http://router",
            agent_key="agent-secret",
            dashboard_key="dash-secret",
            expected={"llmModel": None, "ggufFile": None, "catalogId": None},
            timeout=1.0,
            interval=0.1,
            http_get=FakeHttp(clock, {}),
            sleep=clock.sleep,
            clock=clock.monotonic,
        )


def test_credentials_never_logged(capsys):
    clock = Clock()
    state = _unverified_state()
    with pytest.raises(mod.ProbeError):
        _run(clock, _happy_script(state), timeout=1.0, interval=0.5)
    captured = capsys.readouterr()
    assert "agent-secret" not in captured.out
    assert "agent-secret" not in captured.err
    assert "dash-secret" not in captured.out
    assert "dash-secret" not in captured.err


def test_main_returns_nonzero_on_failure(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(f"LLM_MODEL={CATALOG}\nGGUF_FILE={GGUF}\n")
    monkeypatch.setenv("ODS_AGENT_KEY", "agent-secret")
    monkeypatch.setenv("DASHBOARD_API_KEY", "dash-secret")

    def fake_get(url, headers, timeout):
        if url.endswith("/v1/model/status"):
            return _result(200, {"status": "idle", "modelTransactionPending": False})
        if url.endswith("/api/models/state"):
            return _result(200, _unverified_state())
        return _result(200, _router_health(_unverified_state()))

    monkeypatch.setattr(mod, "_http_get", fake_get)
    rc = mod.main([
        "--agent-url", "http://agent",
        "--dashboard-url", "http://dash",
        "--router-url", "http://router",
        "--env-file", str(env_file),
        "--timeout", "1",
        "--interval", "0.2",
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert "agent-secret" not in captured.err
    assert "dash-secret" not in captured.err


@pytest.mark.parametrize("response", [
    _result(500), _result(404), _result(200, b"not-json"),
    _result(200, {"status": "idle", "modelTransactionPending": True}),
    _result(200, {"status": "idle", "modelTransactionPending": False, "lifecycleActive": True}),
    _result(200, {"status": "idle"}),
])
def test_agent_failure_cannot_fall_through_to_valid_route(response):
    clock = Clock()
    script = _happy_script(_verified_state())
    script["/v1/model/status"] = response
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=2, interval=.2)


def test_invalid_state_recheck_is_not_accepted():
    clock = Clock()
    good = _verified_state()
    calls = {"n": 0}

    def state(clock, headers):
        calls["n"] += 1
        return _result(200, {**good, "valid": calls["n"] % 2 == 1})

    script = _happy_script(good)
    script["/api/models/state"] = state
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=3, interval=.2)


def test_live_expected_identity_changes_during_observation():
    clock = Clock()
    reads = {"n": 0}

    def expected():
        reads["n"] += 1
        return _expected() if reads["n"] == 1 else {"ggufFile": "other.gguf"}

    with pytest.raises(mod.ProbeError, match="timed out"):
        mod.run_probe(agent_url="http://agent", dashboard_url="http://dash",
                      router_url="http://router", agent_key="agent-secret",
                      dashboard_key="dash-secret", expected=expected,
                      timeout=3, interval=.2, clock=clock.monotonic, sleep=clock.sleep,
                      http_get=FakeHttp(clock, _happy_script(_verified_state())))
    assert reads["n"] >= 2


def test_bootstrap_desired_target_does_not_replace_serving_identity():
    clock = Clock()
    state = _verified_state()
    state["desired"] = {"catalogId": "still-downloading-larger-model"}
    _run(clock, _happy_script(state))


def test_successful_last_response_after_deadline_is_rejected():
    clock = Clock()
    base = FakeHttp(clock, _happy_script(_verified_state()))

    def slow_last(url, headers, timeout):
        result = base(url, headers, timeout)
        if len(base.calls) == 7:
            clock.now += timeout + .1
        return result

    with pytest.raises(mod.ProbeError, match="timed out"):
        mod.run_probe(agent_url="http://agent", dashboard_url="http://dash",
                      router_url="http://router", agent_key="agent-secret",
                      dashboard_key="dash-secret", expected=_expected(),
                      timeout=2, interval=.2, clock=clock.monotonic, sleep=clock.sleep,
                      http_get=slow_last)
    assert len(base.calls) == 7


def test_router_restart_between_observations_is_rejected():
    clock = Clock()
    good = _verified_state()
    count = {"n": 0}

    def health(clock, headers):
        count["n"] += 1
        return _result(200, _router_health(good, instanceId=str(count["n"])))

    script = _happy_script(good)
    script["/health"] = health
    with pytest.raises(mod.ProbeError, match="timed out"):
        _run(clock, script, timeout=3, interval=.2)


def test_live_env_parser_preserves_literals_and_last_assignment(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GGUF_FILE=old.gguf\nGGUF_FILE='model #1.gguf'\nLLM_MODEL=owner # comment\n")
    assert mod._load_env(str(env)) == {"GGUF_FILE": "model #1.gguf", "LLM_MODEL": "owner"}
    with pytest.raises(mod.ProbeError, match="unreadable"):
        mod._load_env(str(tmp_path / "missing"))


def test_http_redirect_does_not_forward_credentials():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/other")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = mod._http_get(f"http://127.0.0.1:{server.server_port}/start",
                                 {"Authorization": "Bearer TEST_ONLY"}, 2)
        assert response.status == 302
        assert seen == [("/start", "Bearer TEST_ONLY")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.skipif(sys.platform == "win32", reason="Executable installer block requires POSIX bash")
@pytest.mark.parametrize("cloud,backend,switchboard,pixel,helper,probe_rc,expected_calls,expected_rc", [
    (False, "llama-server", "enabled", True, True, 0, 1, 0),
    (False, "llama-server", "enabled", False, True, 0, 1, 0),
    (False, "llama-server", "enabled", True, True, 1, 1, 1),
    (False, "llama-server", "enabled", True, False, 0, 0, 1),
    (True, "llama-server", "enabled", True, False, 0, 0, 0),
    (False, "external", "enabled", True, False, 0, 0, 0),
    (False, "llama-server", "disabled", True, False, 0, 0, 0),
])
def test_actual_installer_gate(tmp_path, cloud, backend, switchboard, pixel,
                               helper, probe_rc, expected_calls, expected_rc):
    import shlex
    import subprocess
    installer = (PATH.parents[1] / "install-macos.sh").read_text()
    start = installer.index("# ── Verified local route before reporting installation success")
    end = installer.index("# ── Success card", start)
    assert start > installer.rindex("ods_macos_install_host_agent", 0, start)
    assert start > installer.index('ai_ok "Native Pixel activated for Dashboard/Portal"')
    if helper:
        (tmp_path / "first-chat-readiness.py").write_text("fixture helper source")
    q = shlex.quote
    setup = f'''set -eu
CLOUD_MODE={str(cloud).lower()}
DRY_RUN=false
ENABLE_PIXEL={str(pixel).lower()}
LIB_DIR={q(str(tmp_path))}
INSTALL_DIR={q(str(tmp_path))}
ODS_LOG_FILE={q(str(tmp_path / 'install.log'))}
ai() {{ :; }}
ai_ok() {{ :; }}
ai_err() {{ :; }}
read_env_value() {{
  case "$2" in
    LLM_BACKEND) printf '%s' {q(backend)} ;;
    ODS_MODEL_SWITCHBOARD) printf '%s' {q(switchboard)} ;;
    ODS_AGENT_HOST) printf '%s' host.docker.internal ;;
    ODS_AGENT_PORT) printf '%s' 7710 ;;
  esac
}}
docker() {{
  printf '%s\\n' "$@" >> {q(str(tmp_path / 'calls'))}
  cat > {q(str(tmp_path / 'stdin'))}
  return {probe_rc}
}}
'''
    result = subprocess.run(["bash"], input=setup + installer[start:end], text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == expected_rc, result.stderr
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    assert calls.splitlines().count("ods-dashboard-api") == expected_calls
    if expected_calls:
        assert (tmp_path / "stdin").read_text() == "fixture helper source"
        assert "--agent-url\nhttp://host.docker.internal:7710" in calls


def test_main_returns_zero_on_success(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(f"LLM_MODEL={CATALOG}\nGGUF_FILE={GGUF}\n")
    monkeypatch.setenv("ODS_AGENT_KEY", "agent-secret")
    monkeypatch.setenv("DASHBOARD_API_KEY", "dash-secret")
    state = _verified_state()

    def fake_get(url, headers, timeout):
        if url.endswith("/v1/model/status"):
            return _result(200, {"status": "idle", "modelTransactionPending": False})
        if url.endswith("/api/models/state"):
            return _result(200, state)
        if url.endswith("/health"):
            return _result(200, _router_health(state))
        return _result(200, _router_models(state))

    monkeypatch.setattr(mod, "_http_get", fake_get)
    rc = mod.main([
        "--agent-url", "http://agent",
        "--dashboard-url", "http://dash",
        "--router-url", "http://router",
        "--env-file", str(env_file),
        "--timeout", "5",
        "--interval", "0.2",
    ])
    assert rc == 0
