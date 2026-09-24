"""Approval identity must survive separators and legacy state reloads."""
import json

import pytest
from test_main import LOW_LIMIT_POLICY, _verify


@pytest.mark.parametrize("legacy", [False, True])
def test_separator_collision_cannot_consume_another_action_grant(make_client, ape_env, legacy):
    client, main = make_client(policy_yaml=LOW_LIMIT_POLICY)
    approved = {"session": "owner|plugin", "tool": "web_fetch", "args": {"url": "https://example.test"}}
    different = {"session": "owner", "tool": "plugin|web_fetch", "args": approved["args"]}
    for action in (approved, different):
        for _ in range(3):
            assert _verify(client, **action).json()["decision"] == "allow"
    token = _verify(client, **approved).json()["approval_token"]
    assert client.post("/approve", json={"approval_token": token}).json()["granted"]

    if legacy:
        saved = json.loads(ape_env.state_file.read_text())
        record = next(iter(saved["grants"].values()))
        old_key = "|".join([approved["session"], approved["tool"], "NetworkFetch", main._args_hash(approved["args"])])
        saved["grants"] = {old_key: record}
        ape_env.state_file.write_text(json.dumps(saved))
    client, _ = make_client()

    assert _verify(client, **different).json()["decision"] == "require_approval"
    assert _verify(client, **approved).json()["decision"] == "allow"
    assert _verify(client, **approved).json()["decision"] != "allow"


def test_explicit_global_session_cannot_consume_unscoped_approval(make_client):
    client, _ = make_client(policy_yaml=LOW_LIMIT_POLICY)
    action = {"session": None, "tool": "web_fetch", "args": {"url": "https://example.test"}}
    for _ in range(3):
        assert _verify(client, **action).json()["decision"] == "allow"
    token = _verify(client, **action).json()["approval_token"]
    assert client.post("/approve", json={"approval_token": token}).json()["granted"]
    assert _verify(client, **{**action, "session": "_global"}).json()["decision"] == "require_approval"
    assert _verify(client, **action).json()["decision"] == "allow"
