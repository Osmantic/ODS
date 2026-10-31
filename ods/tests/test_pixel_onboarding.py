"""Shared onboarding contract used by Linux and native macOS provisioning."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


LIB = Path(__file__).resolve().parents[1] / "installers" / "lib"


@pytest.fixture(autouse=True)
def installer_umask():
    # The renderer refuses group-writable onboarding directories. Create every
    # fixture with the installer's modes, whatever the host's umask (Ubuntu's
    # default 0002 would otherwise make them unsafe).
    original_umask = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(original_umask)


@pytest.fixture
def contract(tmp_path):
    home = tmp_path / "Owner Home"
    home.mkdir()
    key = home / "relay-key"
    key.write_text("test-only-relay-key")
    key.chmod(0o600)
    answers = home / "config" / "onboarding.json"
    args = [str(answers), "/opt/pixel/bin/openclaw", str(home), "Qwen3.5-9B",
            "16384", "4096", "false", "ods/current", "Current", "4006",
            "18789", str(key), "8888", "/opt/pixel/plugin", "a" * 64,
            "searxng", "", ""]
    return answers, args


def render(args):
    return subprocess.run([sys.executable, "-I", str(LIB / "pixel-onboarding.py"),
                           *args], cwd="/", capture_output=True, text=True)


def test_shared_contract_and_private_output(contract):
    answers, args = contract
    result = render(args)
    assert result.returncode == 0, result.stderr
    value = json.loads(answers.read_text())
    assert value["modelId"] == "ods/current"
    assert value["modelBaseUrl"] == "http://127.0.0.1:4006/v1"
    assert value["modelContextWindow"] == 16384
    assert value["modelMaxTokens"] == 4096
    assert value["workspace"] == str(Path(args[2]) / ".openclaw/workspace-pixel")
    assert "pixel_ods_workspace_preview" in value["gatewayExtensions"][0]["tools"]
    assert "pixel_ods_workspace_bundle" in value["gatewayExtensions"][0]["tools"]
    assert "pixel_ods_workspace_artifact" in value["gatewayExtensions"][0]["tools"]
    assert "pixel_ods_image_read" in value["gatewayExtensions"][0]["tools"]
    assert "pixel_ods_connections_status" in value["gatewayExtensions"][0]["tools"]
    assert {'pixel_ods_python_library_proposal', 'pixel_ods_extension_request_status',
            'pixel_ods_extension_request_prepare', 'pixel_ods_extension_request_advance'}.issubset(value['gatewayExtensions'][0]['tools'])
    assert value["operationsLimbEnabled"] is True
    assert answers.stat().st_mode & 0o777 == 0o600
    assert not result.stdout


def test_update_preserves_budget_only_for_same_route(contract):
    answers, args = contract
    assert render(args).returncode == 0
    value = json.loads(answers.read_text())
    value.update(modelMaxTokens=2300, modelRouteFingerprint="b" * 64)
    answers.write_text(json.dumps(value))
    assert render(args).returncode == 0
    assert json.loads(answers.read_text())["modelMaxTokens"] == 2300
    assert json.loads(answers.read_text())["modelRouteFingerprint"] == "b" * 64
    args[3] = "different-model"
    assert render(args).returncode == 0
    assert json.loads(answers.read_text())["modelMaxTokens"] == 4096
    assert "modelRouteFingerprint" not in json.loads(answers.read_text())


@pytest.mark.parametrize("policy", ["unsupported", "unknown"])
def test_image_policy_is_preserved_only_for_same_unambiguous_route(contract, policy):
    answers, args = contract
    assert render(args).returncode == 0
    value = json.loads(answers.read_text())
    assert value["modelImageInput"] == "unknown"
    value["modelImageInput"] = policy
    answers.write_text(json.dumps(value))
    assert render(args).returncode == 0
    assert json.loads(answers.read_text())["modelImageInput"] == policy
    args[3] = "another-model"
    assert render(args).returncode == 0
    assert json.loads(answers.read_text())["modelImageInput"] == "unknown"


def test_a_vision_claim_is_never_carried_through_a_render(contract):
    # "supported" needs a loaded projector, which the renderer cannot see.
    # The rest of the same route's contract is still preserved.
    answers, args = contract
    assert render(args).returncode == 0
    value = json.loads(answers.read_text())
    answers.write_text(json.dumps(dict(value, modelImageInput="supported", modelMaxTokens=2300,
                                       modelThinkingControl="always")))
    assert render(args).returncode == 0
    rendered = json.loads(answers.read_text())
    assert rendered["modelMaxTokens"] == 2300
    assert rendered["modelThinkingControl"] == "always"
    assert rendered["modelImageInput"] == "unknown"


def test_installer_rerun_after_a_vision_switch_waits_for_the_next_readback(contract):
    # A Models-page switch declared "supported" from llama-server's /props
    # readback. Rerunning the installer (how ODS updates) keeps the model, and
    # keeps LLAMA_ARG_MMPROJ only while its projector file still checks out,
    # so the model may now run without its projector. The rerun must not keep
    # the claim; the next switch reads the projector back.
    answers, args = contract
    assert render(args).returncode == 0
    switch = '''
source "$1/pixel-host-install.sh"
ods_pixel_run_as_owner() { shift 2; "$@"; }
_ods_pixel_update_onboarding_model fixture "$2" "$3" Qwen3.5-9B 16384 2300 false "" supported always
'''
    installer = '''
source "$1/pixel-host-install.sh"
ods_pixel_run_as_owner() { shift 2; "$@"; }
_ods_pixel_gateway_model_alias() { printf 'ods/current'; }
_ods_pixel_runtime_model_identity() { printf 'Qwen3.5-9B'; }
ai_bad() { printf '%s\\n' "$*" >&2; }
_ods_pixel_write_onboarding fixture "$2" "$3" /opt/pixel/bin/openclaw /opt/pixel/plugin "$4"
'''
    environment = {**os.environ, "MAX_CONTEXT": "16384", "LLAMA_REASONING": "off",
                   "PIXEL_MODEL_RELAY_KEY": "test-only-relay-key",
                   "PIXEL_MODEL_RELAY_PORT": "4006", "PIXEL_GATEWAY_PORT": "18789",
                   "SEARXNG_PORT": "8888"}

    def run(script):
        result = subprocess.run(["bash", "-eu", "-c", script, "onboarding-test",
                                 str(LIB), args[2], str(answers), "a" * 64],
                                cwd="/", env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(answers.read_text())

    assert run(switch)["modelImageInput"] == "supported"
    rerun = run(installer)
    # The same route: the rerun keeps the switch's budget and control.
    assert rerun["modelName"] == "ODS Current (Qwen3.5-9B)"
    assert rerun["modelMaxTokens"] == 2300
    assert rerun["modelThinkingControl"] == "always"
    assert rerun["modelImageInput"] == "unknown"
    assert run(switch)["modelImageInput"] == "supported"


def test_remote_route_without_fresh_fingerprint_cannot_preserve_vision_claim(contract):
    answers, args = contract
    assert render(args).returncode == 0
    value = json.loads(answers.read_text())
    value.update(modelImageInput="supported", modelRouteFingerprint="a" * 64)
    answers.write_text(json.dumps(value))
    assert render(args).returncode == 0
    assert json.loads(answers.read_text())["modelImageInput"] == "unknown"


@pytest.mark.parametrize("control", ["enable_thinking", "always", "none"])
def test_thinking_control_is_kept_only_for_the_same_unambiguous_route(contract, control):
    answers, args = contract
    assert render(args).returncode == 0
    value = json.loads(answers.read_text())
    assert "modelThinkingControl" not in value
    answers.write_text(json.dumps(dict(value, modelThinkingControl=control)))
    assert render(args).returncode == 0
    assert json.loads(answers.read_text())["modelThinkingControl"] == control
    # A remote alias without a fresh fingerprint proof cannot keep it either.
    answers.write_text(json.dumps(dict(value, modelThinkingControl=control, modelRouteFingerprint="a" * 64)))
    assert render(args).returncode == 0
    assert "modelThinkingControl" not in json.loads(answers.read_text())
    answers.write_text(json.dumps(dict(value, modelThinkingControl=control)))
    args[3] = "another-model"
    assert render(args).returncode == 0
    assert "modelThinkingControl" not in json.loads(answers.read_text())


def test_thinking_control_invalid_existing_value_is_not_rewritten(contract):
    answers, args = contract
    assert render(args).returncode == 0
    value = json.loads(answers.read_text())
    answers.write_text(json.dumps(dict(value, modelThinkingControl="sometimes")))
    before = answers.read_bytes()
    result = render(args)
    assert result.returncode != 0 and "thinking-control" in result.stderr
    assert answers.read_bytes() == before


def test_image_policy_invalid_existing_value_is_not_rewritten(contract):
    answers, args = contract
    assert render(args).returncode == 0
    value = json.loads(answers.read_text())
    value["modelImageInput"] = True
    answers.write_text(json.dumps(value))
    before = answers.read_bytes()
    assert render(args).returncode != 0
    assert answers.read_bytes() == before


def test_shell_model_switch_updates_and_clears_image_policy(contract):
    answers, args = contract
    assert render(args).returncode == 0
    script = '''
source "$1/pixel-host-install.sh"
ods_pixel_run_as_owner() { shift 2; "$@"; }
_ods_pixel_update_onboarding_model fixture "$2" "$3" "$4" 16384 4096 false "" "${5:-unknown}"
'''
    for model, policy in [("vision-model", "supported"), ("text-model", "unsupported"), ("legacy-model", None)]:
        command = ["bash", "-eu", "-c", script, "onboarding-test", str(LIB), args[2], str(answers), model]
        if policy is not None:
            command.append(policy)
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        value = json.loads(answers.read_text())
        assert value["modelName"] == f"ODS Current ({model})"
        assert value["modelImageInput"] == (policy or "unknown")
    before = answers.read_bytes()
    result = subprocess.run([*command, "true"], capture_output=True, text=True)
    assert result.returncode != 0
    assert answers.read_bytes() == before


def test_shell_model_switch_sets_and_clears_thinking_control(contract):
    answers, args = contract
    assert render(args).returncode == 0
    script = '''
source "$1/pixel-host-install.sh"
ods_pixel_run_as_owner() { shift 2; "$@"; }
_ods_pixel_update_onboarding_model fixture "$2" "$3" "$4" 16384 4096 true "" unknown "${5:-}"
'''
    # Each promoted model brings its own measured control or none at all.
    for model, control in [("thinking-model", "always"), ("switch-model", "enable_thinking"), ("plain-model", None)]:
        command = ["bash", "-eu", "-c", script, "onboarding-test", str(LIB), args[2], str(answers), model]
        if control is not None:
            command.append(control)
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        value = json.loads(answers.read_text())
        assert value["modelName"] == f"ODS Current ({model})"
        assert value.get("modelThinkingControl") == control
    before = answers.read_bytes()
    result = subprocess.run([*command, "sometimes"], capture_output=True, text=True)
    assert result.returncode != 0
    assert answers.read_bytes() == before


@pytest.mark.parametrize("index,value", [(4, "2048"), (4, "10000001"),
    (5, "0"), (5, "20000"), (6, "yes"), (12, "0"), (12, "65536"),
    (9, "65536"), (10, "0"), (15, "unknown")])
def test_invalid_inputs_leave_existing_contract_unchanged(contract, index, value):
    answers, args = contract
    assert render(args).returncode == 0
    before = answers.read_bytes()
    args[index] = value
    assert render(args).returncode != 0
    assert answers.read_bytes() == before


def test_symlink_credential_and_output_are_rejected(contract):
    answers, args = contract
    key = Path(args[11])
    target = key.with_name("real-key")
    key.rename(target)
    key.symlink_to(target)
    assert render(args).returncode != 0
    assert not answers.exists()
    key.unlink()
    target.rename(key)
    answers.parent.mkdir()
    answers.symlink_to(key)
    assert render(args).returncode != 0
    assert key.read_text() == "test-only-relay-key"


def test_shell_wrapper_matches_direct_renderer(contract):
    answers, args = contract
    assert render(args).returncode == 0
    expected = answers.read_bytes()
    answers.unlink()
    script = '''
source "$1/pixel-host-install.sh"
ods_pixel_run_as_owner() { shift 2; "$@"; }
_ods_pixel_gateway_model_alias() { printf 'ods/current'; }
_ods_pixel_runtime_model_identity() { printf 'Qwen3.5-9B'; }
ai_bad() { printf '%s\\n' "$*" >&2; }
_ods_pixel_write_onboarding fixture "$2" "$3" /opt/pixel/bin/openclaw /opt/pixel/plugin "$4"
'''
    result = subprocess.run(["bash", "-eu", "-c", script, "onboarding-test",
                             str(LIB), args[2], str(answers), "a" * 64],
        cwd="/", env={**os.environ, "MAX_CONTEXT": "16384", "LLAMA_REASONING": "off",
                      "PIXEL_MODEL_RELAY_KEY": "test-only-relay-key",
                      "PIXEL_MODEL_RELAY_PORT": "4006", "PIXEL_GATEWAY_PORT": "18789",
                      "SEARXNG_PORT": "8888",
                      "ODS_PORTAL_CONNECTIONS_FILE": str(answers.parent / "absent-connections.json")},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert answers.read_bytes() == expected
    assert not list(answers.parent.glob(".pixel-gateway-key.*"))


# Connected apps: /etc/ods/portal-connections.json and whether the broker holds the sign-in.

CONNECTED_GOOGLE = {
    "schemaVersion": 1, "kind": "ods-portal-connections", "updatedAt": "2026-10-25T20:00:00Z",
    "ownerTimeZone": "America/Chicago",
    "connectors": {"google": {
        "status": "connected", "account": "owner@example.com", "calendarId": "primary",
        "gmailWindowDays": 180,
        "capabilities": {"calendar.read": True, "calendar.write": True, "calendar.direct": False,
                         "gmail.read": True, "gmail.drafts": False},
        "cloudModels": True, "connectedAt": "2026-10-25T20:00:00Z",
    }},
}


def write_record(tmp_path, content, mode=0o640):
    directory = tmp_path / "etc-ods"
    directory.mkdir(mode=0o755, exist_ok=True)
    path = directory / "portal-connections.json"
    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
    path.chmod(mode)
    return path


def baseline_contract(answers, args):
    assert render(args).returncode == 0
    expected = answers.read_bytes()
    answers.unlink()
    return expected


def test_without_a_record_the_contract_is_unchanged(contract, tmp_path):
    answers, args = contract
    expected = baseline_contract(answers, args)
    for custody in ("present", "absent"):
        result = render([*args, str(tmp_path / "missing" / "portal-connections.json"), custody])
        assert result.returncode == 0, result.stderr
        assert answers.read_bytes() == expected
        assert not result.stderr
        answers.unlink()


def test_a_connected_google_account_turns_on_its_sources_across_updates(contract, tmp_path):
    answers, args = contract
    path = write_record(tmp_path, CONNECTED_GOOGLE)
    result = render([*args, str(path), "present"])
    assert result.returncode == 0, result.stderr
    value = json.loads(answers.read_text())
    assert value["emailLimbEnabled"] is True
    assert value["calendarLimbEnabled"] is True
    assert value["calendarWriteEnabled"] is True
    assert value["calendarDirectEnabled"] is False
    assert value["googleAccount"] == "owner@example.com"
    assert value["calendarId"] == "primary"
    assert value["gmailWindowDays"] == 180
    assert value["timeZone"] == value["userTimezone"] == "America/Chicago"
    assert value["socialLimbEnabled"] is False and value["webLimbEnabled"] is False
    assert not result.stderr
    # An update renders again from the same record and keeps the connection.
    first = answers.read_bytes()
    assert render([*args, str(path), "present"]).returncode == 0
    assert answers.read_bytes() == first


def test_a_connection_without_its_sign_in_keeps_sources_off(contract, tmp_path):
    answers, args = contract
    path = write_record(tmp_path, CONNECTED_GOOGLE)
    result = render([*args, str(path), "absent"])
    assert result.returncode == 0, result.stderr
    value = json.loads(answers.read_text())
    assert value["emailLimbEnabled"] is False and value["calendarLimbEnabled"] is False
    assert "calendarWriteEnabled" not in value
    assert value["googleAccount"] == "ods@localhost.local"
    assert value["timeZone"] == "America/Chicago"
    assert "Reconnect Google in Settings" in result.stderr


@pytest.mark.parametrize("content,mode", [
    (b"{not json", 0o640),
    (json.dumps({**CONNECTED_GOOGLE, "unexpected": True}).encode(), 0o640),
    (json.dumps(CONNECTED_GOOGLE).encode(), 0o666),
])
def test_an_unusable_record_never_fails_an_install(contract, tmp_path, content, mode):
    answers, args = contract
    expected = baseline_contract(answers, args)
    path = write_record(tmp_path, content, mode)
    result = render([*args, str(path), "present"])
    assert result.returncode == 0, result.stderr
    assert answers.read_bytes() == expected
    assert "connected apps stay off" in result.stderr
    assert "https://discord.gg/4ntNp9MAwC" in result.stderr


@pytest.mark.parametrize("record,custody", [
    (None, "absent"), (CONNECTED_GOOGLE, "present"), (CONNECTED_GOOGLE, "absent"),
    ({**CONNECTED_GOOGLE, "connectors": {}}, "absent"), ({**CONNECTED_GOOGLE, "ownerTimeZone": None}, "present"),
    ({**CONNECTED_GOOGLE, "connectors": {}, "nativeApps": {"notion": False}}, "absent"),
    ({**CONNECTED_GOOGLE, "connectors": {}, "nativeApps": {"notion": True}}, "absent"),
])
def test_the_custodian_writes_the_connection_answers_a_render_would(contract, tmp_path, record, custody):
    # Root's coordinator updates these keys in place when the owner connects or
    # disconnects; a later render (an update, a model change) must agree.
    sys.path.insert(0, str(LIB.parents[1] / "bin"))
    from pixel_connections import state
    answers, args = contract
    path = write_record(tmp_path, record) if record else tmp_path / "missing" / "portal-connections.json"
    assert render([*args, str(path), custody]).returncode == 0
    value = json.loads(answers.read_text())
    assert ({key: value.get(key) for key in state.ANSWER_KEYS}
            == state.connection_answers(record, google_custody=custody == "present"))


def test_connection_arguments_are_validated(contract, tmp_path):
    answers, args = contract
    path = write_record(tmp_path, CONNECTED_GOOGLE)
    assert render([*args, str(path), "maybe"]).returncode != 0
    assert render([*args, str(path)]).returncode != 0
    assert not answers.exists()


@pytest.mark.parametrize("token_present", [True, False])
def test_shell_wrapper_reads_the_record_and_asks_root_about_the_sign_in(contract, tmp_path, token_present):
    answers, args = contract
    path = write_record(tmp_path, CONNECTED_GOOGLE)
    token = tmp_path / "broker-private" / "google-token.json"
    if token_present:
        token.parent.mkdir()
        token.write_text("{}")
    script = '''
source "$1/pixel-host-install.sh"
ods_pixel_run_as_owner() { shift 2; "$@"; }
ods_sudo() { "$@"; }
_ods_pixel_gateway_model_alias() { printf 'ods/current'; }
_ods_pixel_runtime_model_identity() { printf 'Qwen3.5-9B'; }
ai_bad() { printf '%s\\n' "$*" >&2; }
_ods_pixel_write_onboarding fixture "$2" "$3" /opt/pixel/bin/openclaw /opt/pixel/plugin "$4"
'''
    result = subprocess.run(["bash", "-eu", "-c", script, "onboarding-test",
                             str(LIB), args[2], str(answers), "a" * 64],
        cwd="/", env={**os.environ, "MAX_CONTEXT": "16384", "LLAMA_REASONING": "off",
                      "PIXEL_MODEL_RELAY_KEY": "test-only-relay-key",
                      "PIXEL_MODEL_RELAY_PORT": "4006", "PIXEL_GATEWAY_PORT": "18789",
                      "SEARXNG_PORT": "8888", "ODS_PORTAL_CONNECTIONS_FILE": str(path),
                      "ODS_PIXEL_SOURCE_TOKEN_PATH": str(token)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    value = json.loads(answers.read_text())
    assert value["emailLimbEnabled"] is token_present
    assert value["calendarLimbEnabled"] is token_present
    assert value["timeZone"] == "America/Chicago"
