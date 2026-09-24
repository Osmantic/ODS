"""Exercise tail scanning through the authenticated audit HTTP boundary."""

import json

import pytest


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
