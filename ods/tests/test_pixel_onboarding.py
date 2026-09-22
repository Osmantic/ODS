"""Shared onboarding contract used by Linux and native macOS provisioning."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


LIB = Path(__file__).resolve().parents[1] / "installers" / "lib"


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
                      "SEARXNG_PORT": "8888"}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert answers.read_bytes() == expected
    assert not list(answers.parent.glob(".pixel-gateway-key.*"))
