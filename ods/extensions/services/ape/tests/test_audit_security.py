import pytest
from fastapi.testclient import TestClient
from ods.extensions.services.ape.main import app

client = TestClient(app)


def test_audit_log_does_not_leak_token():
    # 1. Trigger a require_approval decision
    # We use a tool and args that we know will trigger a windowed limit
    # or policy that requires approval.
    # For this test, we can mock the policy or just send many requests.

    # First, let's verify the /verify response contains the token
    # (which is necessary for the agent to use it)
    # Note: In the real app, we'd need a valid API key.

    # We'll simulate a request that requires approval.
    # Since we can't easily modify the in-memory state for a simple test,
    # we'll assume the current logic for a specific tool triggers it
    # or we just check that the /audit endpoint doesn't return tokens.

    # Trigger /verify (assuming default policy requires approval for 'ExecuteCommand'
    # if limits are hit, but let's just check the audit log entries generally)

    # We trigger a few verify calls
    for i in range(5):
        client.post(
            "/verify",
            json={"tool_name": "ExecuteCommand", "args": {"command": "ls"}},
            headers={"X-API-Key": "test-key"},
        )

    # 2. Check the audit log
    response = client.get("/audit", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    data = response.json()

    for entry in data.get("entries", []):
        # The approval_token should NEVER be a key in the audit entry
        assert "approval_token" not in entry, (
            f"Audit log leaked approval_token in entry {entry.get('id')}"
        )
        # Also check that the token isn't hidden in the reason
        if "reason" in entry:
            assert "appr_" not in entry["reason"], (
                f"Audit log leaked token in reason: {entry['reason']}"
            )


if __name__ == "__main__":
    pytest.main([__file__])
