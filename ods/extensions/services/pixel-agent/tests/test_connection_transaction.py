"""Disposable POSIX owner transactions for connected apps, NOT installed runtime acceptance."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership and file locks; run under Linux/WSL")

import hashlib
import json
from pathlib import Path
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import connection_transaction as tx  # noqa: E402
import pixel_access_mode as controller  # noqa: E402
from pixel_connections import planner, state  # noqa: E402

READ_ONLY = {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
             "gmail.read": True, "gmail.drafts": False}
RECORD = {"schemaVersion": 1, "kind": "ods-portal-connections", "updatedAt": "2026-10-25T20:00:00Z",
          "ownerTimeZone": "America/Chicago",
          "connectors": {"google": {"status": "connected", "account": "owner@example.com", "calendarId": "primary",
                                    "gmailWindowDays": 180, "capabilities": READ_ONLY, "cloudModels": False,
                                    "connectedAt": "2026-10-25T20:00:00Z"}}}
ENV = ("OPENCLAW_STATE_DIR='/home/owner/.openclaw'\nPIXEL_CALENDAR_DIRECT_ENABLED='0'\n"
       "PIXEL_LIMB_EMAIL_ENABLED='0'\nPIXEL_LIMB_CALENDAR_ENABLED='0'\nPIXEL_LIMB_CALENDAR_WRITE_ENABLED='0'\n"
       "PIXEL_LIMB_WEB_ENABLED='0'\n")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def disconnected_config(install):
    allow = sorted(["read", "write", "exec", "pixel_ods_status", "update_plan"])
    return {"plugins": {"allow": ["pixel-ods", "searxng", "llama-cpp", "pixel-operations-broker"],
                        "entries": {"pixel-ods": {"enabled": True}, "llama-cpp": {"enabled": True}},
                        "load": {"paths": ["/opt/ods/plugin", f"{install}/current/plugin-ops"]}},
            "tools": {"alsoAllow": list(allow), "sandbox": {"tools": {"allow": list(allow)}}},
            "agents": {"defaults": {"bootstrapMaxChars": 32000},
                       "list": [{"id": "pixel", "tools": {"deny": ["discord", "message", *planner.SOURCE_TOOLS]}}]}}


@pytest.fixture
def owner(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    install = tmp_path / "install"
    (install / "current" / "plugin").mkdir(parents=True)
    config = tmp_path / "openclaw.json"
    config.write_text(json.dumps(disconnected_config(install), indent=2) + "\n")
    env = tmp_path / "gateway.env"
    env.write_text(ENV)
    answers = tmp_path / "onboarding.json"
    # The disconnected contract as pixel-onboarding.py writes it: no optional connection keys.
    answers.write_text(json.dumps({"agentId": "pixel", **state.DISCONNECTED_ANSWERS}, indent=2, sort_keys=True) + "\n")
    for path in (config, env, answers):
        path.chmod(0o600)
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    checks = []
    monkeypatch.setattr(tx, "_plugin_ready", lambda openclaw, install_dir, *, enabled: checks.append(enabled) or True)
    return {"config": config, "env": env, "answers": answers, "state": state_dir, "install": install,
            "checks": checks, "before": {path: path.read_bytes() for path in (config, env, answers)}}


def target(capabilities, zone=None):
    return dict(planner.limbs(capabilities), userTimezone=zone, plugins=[])


def apply(owner, gateway, answers, **kwargs):
    options = dict(state_dir=str(owner["state"]), env_path=str(owner["env"]), answers_path=str(owner["answers"]),
                   install_dir=str(owner["install"]), openclaw="/opt/openclaw/bin/openclaw",
                   transaction_id=sha(uuid.uuid4().bytes),
                   expected_config_sha256=sha(owner["config"].read_bytes()),
                   validate_config=lambda staged: True, check_no_active_run=lambda: False,
                   activate=lambda: "verified")
    options.update(kwargs)
    return tx.apply_connection(str(owner["config"]), gateway, answers, **options)


def connected_answers():
    return state.connection_answers(RECORD, google_custody=True)


@pytest.mark.parametrize("catalog", [False, True], ids=["source-broker", "catalog-plugin"])
def test_a_cold_registry_entry_does_not_prove_the_plugin_can_run(tmp_path, monkeypatch, catalog):
    """OpenClaw 2026.6.33 reports cold entries as loaded, even if their module throws."""
    def inspect(argv, **kwargs):
        if "registry" in argv:
            return subprocess.CompletedProcess(argv, 0)
        runtime = "--runtime" in argv
        plugin = {"status": "error" if runtime else "loaded", "imported": runtime,
                  "rootDir": str(tmp_path / "current/plugin")}
        return subprocess.CompletedProcess(argv, 0, json.dumps({"plugin": plugin}).encode())

    monkeypatch.setattr(tx.subprocess, "run", inspect)
    ready = tx._catalog_ready("openclaw", ["memory-wiki"]) if catalog else tx._plugin_ready(
        "openclaw", str(tmp_path), enabled=True)
    assert ready is False, "a module that fails to load must not reach activation"


@pytest.mark.parametrize("catalog", [False, True], ids=["source-broker", "catalog-plugin"])
@pytest.mark.parametrize("imported", [True, False, None, "true"])
def test_plugin_readiness_requires_a_successful_runtime_import(tmp_path, monkeypatch, catalog, imported):
    def inspect(argv, **kwargs):
        if "registry" in argv:
            return subprocess.CompletedProcess(argv, 0)
        plugin = {"status": "loaded", "imported": imported, "rootDir": str(tmp_path / "current/plugin")}
        return subprocess.CompletedProcess(argv, 0, json.dumps({"plugin": plugin}).encode())

    monkeypatch.setattr(tx.subprocess, "run", inspect)
    ready = tx._catalog_ready("openclaw", ["memory-wiki"]) if catalog else tx._plugin_ready(
        "openclaw", str(tmp_path), enabled=True)
    assert ready is (imported is True)


@pytest.mark.parametrize("value", [None, [], "loaded", 1, {"plugin": None}])
def test_source_plugin_inspection_refuses_an_unexpected_json_shape(tmp_path, monkeypatch, value):
    def inspect(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode())

    monkeypatch.setattr(tx.subprocess, "run", inspect)
    assert tx._plugin_ready("openclaw", str(tmp_path), enabled=True) is False


def test_connecting_writes_the_plan_into_all_three_owner_files(owner):
    activations = []
    result = apply(owner, target(READ_ONLY, "America/Chicago"), connected_answers(),
                   activate=lambda: activations.append(1) or "verified")
    config = json.loads(owner["config"].read_text())
    assert result == {"status": "runtime-verified", "configSha256": sha(owner["config"].read_bytes())}
    assert planner.PLUGIN_ID in config["plugins"]["allow"]
    assert config["agents"]["defaults"]["userTimezone"] == "America/Chicago"
    assert planner.gateway_limbs(owner["env"].read_text()) == planner.limbs(READ_ONLY)
    contract = json.loads(owner["answers"].read_text())
    assert contract["googleAccount"] == "owner@example.com" and contract["gmailWindowDays"] == 180
    assert contract["emailLimbEnabled"] is True and contract["calendarWriteEnabled"] is False
    assert contract["agentId"] == "pixel", "keys outside the connection are kept"
    assert activations == [1] and owner["checks"] == [True]
    assert not (owner["state"] / tx.JOURNAL).exists()
    status = tx.connection_status(str(owner["config"]), state_dir=str(owner["state"]), env_path=str(owner["env"]),
                                  answers_path=str(owner["answers"]))
    assert status["live"] == target(READ_ONLY, "America/Chicago")
    assert status["answers"] == connected_answers()
    assert status["completion"]["outcome"] == "applied" and status["pending"] is False


def test_a_rejected_activation_restores_every_file_and_restarts_the_old_setup(owner):
    answers = iter(["rejected", "verified"])
    result = apply(owner, target(READ_ONLY), connected_answers(), activate=lambda: next(answers))
    assert result == {"status": "rolled-back", "configSha256": sha(owner["before"][owner["config"]])}
    for path, before in owner["before"].items():
        assert path.read_bytes() == before, path.name
    assert owner["checks"] == [True, False]
    assert next(answers, None) is None, "root restarted the gateway on the previous setup"


def test_a_plugin_that_does_not_resolve_is_rolled_back_before_any_restart(owner, monkeypatch):
    checks = []
    monkeypatch.setattr(tx, "_plugin_ready",
                        lambda openclaw, install_dir, *, enabled: checks.append(enabled) or not enabled)
    activations = []
    result = apply(owner, target(READ_ONLY), connected_answers(), activate=lambda: activations.append(1) or "verified")
    assert result["status"] == "rolled-back"
    assert activations == [1], "only the restart back onto the previous setup"
    for path, before in owner["before"].items():
        assert path.read_bytes() == before


def test_an_interrupted_activation_stays_journaled_until_recovered(owner):
    with pytest.raises(tx.ConnectionTransactionError, match="connection-activation-unavailable"):
        apply(owner, target(READ_ONLY), connected_answers(), activate=lambda: "unavailable",
              transaction_id="a" * 64)
    journal = owner["state"] / tx.JOURNAL
    assert journal.exists()
    status = tx.connection_status(str(owner["config"]), state_dir=str(owner["state"]), env_path=str(owner["env"]),
                                  answers_path=str(owner["answers"]))
    assert status["pending"] is True and status["completion"] is None
    with pytest.raises(tx.ConnectionTransactionError, match="connection-recovery-required"):
        apply(owner, target(READ_ONLY), connected_answers())
    result = tx.recover_connection(str(owner["config"]), state_dir=str(owner["state"]), install_dir=str(owner["install"]),
                                   openclaw="/opt/openclaw/bin/openclaw", transaction_id="a" * 64,
                                   expected_config_sha256=sha(owner["config"].read_bytes()),
                                   validate_config=lambda staged: True, check_no_active_run=lambda: False,
                                   activate=lambda: "verified")
    assert result["status"] == "rolled-back" and not journal.exists()
    for path, before in owner["before"].items():
        assert path.read_bytes() == before


def test_drift_busy_runs_and_other_transactions_refuse_before_any_write(owner):
    with pytest.raises(tx.ConnectionTransactionError, match="connection-config-changed"):
        apply(owner, target(READ_ONLY), connected_answers(), expected_config_sha256="0" * 64)
    with pytest.raises(controller.AccessModeRejected):
        apply(owner, target(READ_ONLY), connected_answers(), check_no_active_run=lambda: True)
    (owner["state"] / "settings-journal.json").write_text("{}")
    (owner["state"] / "settings-journal.json").chmod(0o600)
    with pytest.raises(tx.ConnectionTransactionError, match="settings-recovery-required"):
        apply(owner, target(READ_ONLY), connected_answers())
    for path, before in owner["before"].items():
        assert path.read_bytes() == before
    assert not (owner["state"] / tx.JOURNAL).exists()


def test_invalid_targets_and_answers_never_reach_the_files(owner):
    with pytest.raises(tx.ConnectionTransactionError, match="invalid-connection-target"):
        apply(owner, dict(target(READ_ONLY), calendarWrite="yes"), connected_answers())
    with pytest.raises(tx.ConnectionTransactionError, match="invalid-connection-answers"):
        apply(owner, target(READ_ONLY), dict(connected_answers(), googleAccount="not an address"))
    for path, before in owner["before"].items():
        assert path.read_bytes() == before


def test_disconnecting_returns_the_limbs_and_contract_to_their_disconnected_values(owner):
    apply(owner, target(READ_ONLY, "America/Chicago"), connected_answers())
    disconnected = {"schemaVersion": 1, "kind": "ods-portal-connections", "updatedAt": "2026-10-25T21:00:00Z",
                    "ownerTimeZone": "America/Chicago", "connectors": {}}
    apply(owner, target(None, "America/Chicago"), state.connection_answers(disconnected, google_custody=False))
    config = json.loads(owner["config"].read_text())
    assert planner.PLUGIN_ID not in config["plugins"]["allow"]
    assert owner["env"].read_text() == ENV
    contract = json.loads(owner["answers"].read_text())
    assert contract["googleAccount"] == "ods@localhost.local" and contract["emailLimbEnabled"] is False
    assert "gmailWindowDays" not in contract and "calendarWriteEnabled" not in contract
    assert contract["timeZone"] == contract["userTimezone"] == "America/Chicago"
    assert owner["checks"] == [True, False]
