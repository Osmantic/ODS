"""Tests for the persistent per-session revocation store."""

import json
import os

import pytest

from session_revocations import RevocationStore


def test_revoke_prunes_expired_entries_and_persists_owner_only(tmp_path):
    path = tmp_path / "auth" / "revocations.json"
    old_id = "expired_session_identifier"
    active_id = "active_session_identifier"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "revoked": {old_id: 99, active_id: 300},
        }),
        encoding="utf-8",
    )
    store = RevocationStore(path)

    new_id = "new_session_identifier"
    store.revoke(new_id, 400, now=100)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["revoked"] == {active_id: 300, new_id: 400}
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_revoke_is_idempotent_and_never_shortens_expiry(tmp_path):
    path = tmp_path / "revocations.json"
    store = RevocationStore(path)
    session_id = "idempotent_session_identifier"

    store.revoke(session_id, 400, now=100)
    store.revoke(session_id, 300, now=100)

    assert RevocationStore(path).is_revoked(session_id, now=350) is True


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema_version": 2, "revoked": {}},
        {"schema_version": 1, "revoked": []},
        {"schema_version": 1, "revoked": {"bad id": 200}},
        {"schema_version": 1, "revoked": {"valid_session_identifier": "200"}},
    ],
)
def test_invalid_persisted_shape_is_rejected(tmp_path, payload):
    path = tmp_path / "revocations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        RevocationStore(path).is_revoked("valid_session_identifier", now=100)
