"""Owner review of actual pending payloads, never agent-visible or executable."""
import json
import pytest

import test_integrations_preparation as preparation
from test_integrations_preparation import call, policy
from integrations_common import IntegrationError, MAX_RESPONSE_BYTES, digest, now
from integrations_service import handle_line

world = preparation.world


def detail(service, approval_id):
    return service.handle("owner", {"schemaVersion": 1, "action": "approval-detail", "approvalId": approval_id})["approval"]


@pytest.mark.parametrize("action", ["notes-write", "notes-prepare"])
def test_exact_note_proposal_is_lazy_owner_only_and_approval_still_needs_exact_retry(world, action):
    service, store, vault, endpoint, notifications, mutations = world
    if action == "notes-prepare":
        policy(world, "allow")
    text = "Linha solicitada.\n" + "\n".join(f"{i}. Texto adicional proposto" for i in range(1, 21)) + "\n<script>não executar</script>"
    fields = {"path": "Owned.md", "text": text, "mode": "append"}
    reply = call(service, action, **fields)
    approval_id = reply["approvalId"]
    assert text not in json.dumps(reply, ensure_ascii=False)
    listed = service.handle("owner", {"schemaVersion": 1, "action": "get"})
    assert "reviewPayload" not in json.dumps(listed)
    assert text not in json.dumps(listed, ensure_ascii=False)
    denied = handle_line(service, "agent", json.dumps({"schemaVersion": 1, "action": "approval-detail", "approvalId": approval_id}))
    assert denied["ok"] is False and "request" not in denied and text not in json.dumps(denied, ensure_ascii=False)
    before = store.approvals()
    reviewed = detail(service, approval_id)
    assert reviewed["available"] is True and reviewed["redacted"] is False
    assert reviewed["request"] == {"action": "notes-write", **fields}
    assert store.approvals() == before
    assert (vault / "Owned.md").read_text() == "Invented original.\n"
    assert not endpoint.requests and not mutations
    # The new data is a review snapshot; it does not enter the existing digest.
    service.decide(approval_id, "approve")
    assert not mutations and store.approvals()[0]["digest"] == before[0]["digest"]
    call(service, "notes-write", **fields)
    assert len(mutations) == 1
    if action == "notes-write":
        assert store.approvals()[0]["state"] == "used"


def test_mcp_arguments_are_detached_exact_types_without_provider_calls_or_agent_leak(world):
    service, store, _, endpoint, *_ = world
    arguments = {"query": "A\nB\nC", "date": "2026-08-21", "limit": 0, "flag": False, "nested": [None, {"x": "é"}]}
    original = json.loads(json.dumps(arguments))
    reply = call(service, "mcp-prepare", server="invented", tool="change", arguments=arguments)
    arguments["nested"][1]["x"] = "changed after admission"
    result = detail(service, reply["approvalId"])
    assert result["request"] == {"action": "mcp-call", "server": "invented", "tool": "change", "arguments": original}
    assert not endpoint.requests and not endpoint.mutations
    assert "reviewPayload" not in json.dumps(reply)
    assert "arguments" not in service.view()["approvals"][0]
    assert store.approvals()[0]["state"] == "pending"


@pytest.mark.parametrize("tool", ["add", "remove"])
def test_watch_detail_preserves_effective_target_and_does_not_repeat_metadata_read(world, tool):
    service, _, _, endpoint, *_ = world
    endpoint.watches = {"watch-1": {"url": "https://example.test/item", "title": "Invented item"}}
    arguments = {"url": "https://example.test/other", "title": "New item", "below": 17.5} if tool == "add" else {"id": "watch-1"}
    reply = call(service, "mcp-prepare", server="app:watch", tool=tool, arguments=arguments)
    before = list(endpoint.requests)
    assert len(before) == 1 and before[0][0] == "GET"
    assert detail(service, reply["approvalId"])["request"] == {"action": "mcp-call", "server": "app:watch", "tool": tool, "arguments": arguments}
    assert endpoint.requests == before and not endpoint.mutations


def test_full_unicode_note_is_not_truncated_and_detail_remains_bounded(world):
    service, _, _, endpoint, *_ = world
    text = "字" * 20000
    reply = call(service, "notes-prepare", path="Owned.md", text=text, mode="append")
    result = detail(service, reply["approvalId"])
    assert result["request"]["text"] == text
    assert len(json.dumps({"approval": result}).encode()) < MAX_RESPONSE_BYTES
    assert not endpoint.requests


def test_legacy_missing_expired_and_declined_details_do_not_invent_a_payload(world):
    service, store, *_ = world
    row = {"id": "legacy", "kind": "note", "summary": "Append to Owned.md", "digest": "opaque", "state": "pending",
           "createdAt": now(), "expiresAt": now() + 60}
    store.save_approvals([row])
    # Upgrading the filesystem binding revokes weaker pending note grants.
    with pytest.raises(IntegrationError, match="no longer waiting"):
        detail(service, "legacy")
    assert store.approvals() == []
    # Other legacy request kinds keep the unavailable-preview fallback.
    row = {**row, "kind": "mcp", "summary": "Legacy custom tool"}
    store.save_approvals([row])
    result = detail(service, "legacy")
    assert result["available"] is False and "request" not in result
    assert store.approvals() == [row]
    with pytest.raises(IntegrationError, match="no longer waiting"):
        detail(service, "unknown")
    service.decide("legacy", "decline")
    with pytest.raises(IntegrationError, match="no longer waiting"):
        detail(service, "legacy")
    row["expiresAt"] = now() - 1
    store.save_approvals([row])
    with pytest.raises(IntegrationError, match="no longer waiting"):
        detail(service, "legacy")


def test_configured_secrets_are_masked_in_owner_detail_without_altering_saved_action(world):
    service, store, *_ = world
    arguments = {"value": 'quoted "invented-key"', "nested": {"invented-key": "invented-watch-key"}}
    reply = call(service, "mcp-prepare", server="invented", tool="change", arguments=arguments)
    before = store.approvals()
    result = detail(service, reply["approvalId"])
    assert result["redacted"] is True
    assert "invented-key" not in json.dumps(result) and "invented-watch-key" not in json.dumps(result)
    assert store.approvals() == before and before[0]["reviewPayload"]["arguments"] == arguments


@pytest.mark.parametrize("change", ["payload", "digest", "review-digest", "unicode-review-digest"])
def test_detail_rejects_a_snapshot_that_is_no_longer_bound_to_its_exact_approval(world, change):
    service, store, _, endpoint, *_ = world
    reply = call(service, "notes-prepare", path="Owned.md", text="Original proposal.", mode="append")
    rows = store.approvals()
    if change == "payload":
        rows[0]["reviewPayload"]["text"] = "Different proposal."
    elif change == "digest":
        rows[0]["digest"] = "different admitted action"
    else:
        rows[0]["reviewPayloadDigest"] = "字" if change == "unicode-review-digest" else "invalid"
    store.save_approvals(rows)
    if change == "digest":
        with pytest.raises(IntegrationError, match="no longer waiting"):
            detail(service, reply["approvalId"])
        assert not endpoint.requests and store.approvals() == []
        return
    result = detail(service, reply["approvalId"])
    assert result["available"] is False and "request" not in result
    assert not endpoint.requests and store.approvals() == rows


@pytest.mark.parametrize("payload", [
    {"action": "notes-write", "path": "Owned.md", "mode": "bad", "text": "Text"},
    {"action": "notes-write", "path": "Owned.md", "mode": "append", "text": {}},
    {"action": "mcp-call", "server": "invented", "tool": "change", "arguments": []},
])
def test_invalid_saved_snapshot_is_unavailable_instead_of_rendered_as_an_exact_action(world, payload):
    service, store, *_ = world
    reply = call(service, "notes-prepare", path="Owned.md", text="Original proposal.", mode="append")
    rows = store.approvals()
    rows[0].update(reviewPayload=payload, reviewPayloadDigest=digest("approval-review-v1", rows[0]["digest"], payload))
    store.save_approvals(rows)
    assert detail(service, reply["approvalId"])["available"] is False


def test_redaction_expansion_cannot_overflow_the_owner_reply(world):
    service, store, *_ = world
    credentials = store.secrets()
    credentials["ntfyPassword"] = "a"
    credentials["mcpEnv"] = {"invented": {"X": "d", "Y": "e"}}
    store.save_secrets(credentials)
    reply = call(service, "notes-prepare", path="Owned.md", text="a" * 20000, mode="append")
    result = detail(service, reply["approvalId"])
    assert result["available"] is False and "request" not in result
    assert len(json.dumps(result).encode()) < MAX_RESPONSE_BYTES


def test_multiple_large_pending_proposals_remain_readable_without_expanding_the_owner_list(world):
    service, store, *_ = world
    requests = []
    for index in range(8):
        text = str(index) + "字" * 19999
        reply = call(service, "notes-prepare", path="Owned.md", text=text, mode="append")
        requests.append((reply["approvalId"], text))
    assert len(store.approvals()) == 8
    assert (store.root / "approvals.json").stat().st_size < 8 * (128 * 1024 + 1024)
    listed = service.view()
    assert len(listed["approvals"]) == 8
    assert len(json.dumps(listed).encode()) < MAX_RESPONSE_BYTES
    assert "reviewPayload" not in json.dumps(listed)
    for approval_id, text in requests:
        assert detail(service, approval_id)["request"]["text"] == text


@pytest.mark.parametrize("mode,path,initial,separator", [
    ("append", "Owned.md", b"Original without LF", b"\n\n"),
    ("append", "Owned.md", b"Original with LF\n", b"\n"),
    ("append", "Owned.md", b"Original blank line\n\n", b""),
    ("append", "Owned.md", b"Original CRLF\r\n", b"\n"),
    ("create", "New.md", None, b""),
])
@pytest.mark.parametrize("text,body", [
    ("Trailing Markdown spaces  ", b"Trailing Markdown spaces  \n"),
    ("Multiple blank lines\n\n", b"Multiple blank lines\n\n"),
    ("Primeira linha  \r\nSegunda linha\t \r\n\r\n", b"Primeira linha  \r\nSegunda linha\t \r\n\r\n"),
])
def test_approved_note_body_is_written_verbatim_only_on_explicit_write(world, mode, path, initial, separator, text, body):
    service, store, vault, endpoint, _, mutations = world
    target = vault / path
    untouched = vault / "Untouched.md"
    untouched.write_bytes(b"Existing data\r\n\x00Not the target.\n")
    if initial is not None:
        target.write_bytes(initial)
    request = {"path": path, "mode": mode, "text": text}
    pending = call(service, "notes-prepare", **request)
    assert detail(service, pending["approvalId"])["request"] == {"action": "notes-write", **request}
    assert (target.read_bytes() if target.exists() else None) == initial
    assert not mutations and not endpoint.requests
    service.handle("owner", {"schemaVersion": 1, "action": "decide", "approvalId": pending["approvalId"], "decision": "approve"})
    assert (target.read_bytes() if target.exists() else None) == initial
    assert not mutations and store.approvals()[0]["state"] == "approved"
    result = call(service, "notes-write", **request)
    assert result["written"] == path
    assert target.read_bytes() == (initial or b"") + separator + body
    assert untouched.read_bytes() == b"Existing data\r\n\x00Not the target.\n"
    assert len(mutations) == 1 and store.approvals()[0]["state"] == "used"
    assert not endpoint.requests
