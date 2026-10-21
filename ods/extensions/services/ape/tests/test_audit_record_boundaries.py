"""Exercise tail scanning through the authenticated audit HTTP boundary."""

import json

import pytest


def test_audit_returns_recent_real_verify_decisions(make_client):
    client, app = make_client()
    decisions = []
    for number in range(120):
        response = client.post("/verify", json={
            "tool_name": "read_file", "args": {"path": "fixture.txt"},
            "session": "audit-fixture", "agent": f"fixture-{number}",
        })
        assert response.status_code == 200
        decisions.append(response.json()["decision_id"])
    assert app.AUDIT_LOG.stat().st_size > 8192

    response = client.get("/audit", params={"last_n": 50})
    assert response.status_code == 200
    assert "error" not in response.json()
    assert [entry["id"] for entry in response.json()["entries"]] == decisions[-50:]


@pytest.mark.parametrize("last_n", [1, 50, 1000])
@pytest.mark.parametrize("trailing_newline", [False, True])
@pytest.mark.parametrize("reason", ["policy allowed", "許可された操作 " * 12])
def test_audit_tail_starts_at_complete_record(make_client, last_n, trailing_newline, reason):
    client, app = make_client()
    records = [{"id": n, "reason": reason} for n in range(1400)]
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in records)
    app.AUDIT_LOG.write_text(payload + ("\n" if trailing_newline else ""), encoding="utf-8")

    response = client.get("/audit", params={"last_n": last_n})

    assert response.status_code == 200
    assert "error" not in response.json()
    assert response.json()["entries"] == records[-last_n:]


def test_audit_short_file_keeps_first_record(make_client):
    client, app = make_client()
    records = [{"id": 1}, {"id": 2}]
    app.AUDIT_LOG.write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
    assert client.get("/audit").json()["entries"] == records


def test_audit_exact_chunk_boundary_keeps_complete_leading_record(make_client):
    client, app = make_client()
    row = {"id": 1, "reason": "x" * 40}
    encoded = json.dumps(row).encode() + b"\n"
    # Force the reverse reader to land immediately after a newline.
    tail = encoded + b" " * (8192 - len(encoded) - 2) + b"\n\n"
    app.AUDIT_LOG.write_bytes(b'{"id":0}\n' + tail)
    response = client.get("/audit", params={"last_n": 1})
    assert response.json()["entries"] == [row]


def test_audit_still_refuses_a_malformed_complete_recent_record(make_client):
    client, app = make_client()
    app.AUDIT_LOG.write_bytes(b'{"id":0}\nmalformed-record\n')
    response = client.get("/audit")
    assert response.status_code == 200
    assert response.json() == {"entries": [], "error": "audit log unreadable"}
