"""An existing APE process must enforce edits to its mounted policy file."""

import os

import pytest
import yaml


def policy(version, mode):
    return yaml.safe_dump({
        "version": version,
        "intents": {"ReadFile": {"mode": mode}, "NetworkFetch": {"mode": "allow"}},
        "rate_limit": {"requests_per_minute": 10000},
        "windowed_limits": {"enabled": False},
        "circuit_breaker": {"enabled": False},
    })


def replace_policy(path, content):
    # Change the actual filesystem mtime, without a sleep or mocking the loader.
    previous = path.stat().st_mtime
    path.write_text(content, encoding="utf-8")
    os.utime(path, (previous + 2, previous + 2))


def verify(client, tool="read_file"):
    return client.post("/verify", json={
        "tool_name": tool, "args": {"path": "/tmp/fixture"},
        "session_id": "policy-edit-fixture",
    })


@pytest.mark.parametrize("strict", [False, True], ids=["advisory", "strict"])
def test_same_process_applies_policy_edits_and_preserves_other_intents(make_client, ape_env, strict):
    client, app = make_client(env={"APE_STRICT_MODE": str(strict).lower()},
                              policy_yaml=policy(1, "allow"))
    assert verify(client).json()["allowed"] is True
    replace_policy(ape_env.policy_file, policy(2, "deny"))

    denied = verify(client)
    assert denied.status_code == (403 if strict else 200)
    if not strict:
        assert denied.json()["allowed"] is False
        assert denied.json()["decision"] == "deny"
    assert client.get("/policy").json()["version"] == 2
    assert verify(client, "web_fetch").json()["allowed"] is True

    replace_policy(ape_env.policy_file, policy(3, "allow"))
    recovered = verify(client)
    assert recovered.status_code == 200
    assert recovered.json()["allowed"] is True
    assert client.get("/policy").json()["version"] == 3
    audit = client.get("/audit").json()["entries"]
    assert [entry["allowed"] for entry in audit] == [True, False, True, True]
    assert app.POLICY_FILE == ape_env.policy_file


@pytest.mark.parametrize("invalid", ["intents: [", "- not-a-policy"],
                         ids=["malformed-yaml", "wrong-root-type"])
def test_invalid_edit_keeps_last_policy_and_repaired_file_is_loaded(make_client, ape_env, invalid):
    client, _ = make_client(policy_yaml=policy(10, "deny"))
    assert verify(client).json()["allowed"] is False

    replace_policy(ape_env.policy_file, invalid)
    assert verify(client).json()["allowed"] is False
    assert client.get("/policy").json()["version"] == 10

    replace_policy(ape_env.policy_file, policy(11, "allow"))
    assert verify(client).json()["allowed"] is True
    assert client.get("/policy").json()["version"] == 11
