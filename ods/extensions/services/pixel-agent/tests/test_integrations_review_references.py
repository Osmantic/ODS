"""Private typed lookup identity, never rendered-text authority or permission."""
import copy
import json
import os
from pathlib import Path
import sys

import pytest

if sys.platform == "win32":
    pytest.skip("Descriptor-anchored Unix notes; run in WSL", allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import integrations_apps as apps
import integrations_notes as notes
from integrations_common import IntegrationError, Store, default_config
from integrations_service import Integrations


@pytest.fixture
def world(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "folder").mkdir(parents=True)
    (vault / "folder/Example.md").write_text("needle: original private body\n")
    (vault / "Other.md").write_text("A different owner-selected note.\n")
    store = Store(tmp_path / "state")
    config = default_config()
    config["notes"].update(enabled=True, vaultPath=str(vault), write="ask")
    for app in config["apps"]:
        config["apps"][app].update(enabled=True, url=f"https://{app}.example.invalid")
    config["apps"]["news"]["username"] = "fixture-owner"
    store.save_config(config)
    service = Integrations(store)
    settings = {key: config[key] for key in ("notes", "notifications", "mcp", "apps")}
    keys = {"apps": {app: app + "-fixture-secret" for app in config["apps"]}}
    service.save(config["revision"], settings, keys)
    effects = []

    def forbidden(*_args, **_kwargs):
        effects.append("unexpected actuator")
        raise AssertionError("A review reference must not execute a mutation")

    monkeypatch.setattr(notes, "write", forbidden)
    monkeypatch.setattr(apps, "add_watch", forbidden)
    monkeypatch.setattr(apps, "remove_watch", forbidden)
    yield service, store, vault, effects
    assert effects == []


def call(world, action="notes-search", **fields):
    return world[0].handle("agent", {"schemaVersion": 1, "action": action, "recoveryReviewOnly": True, **fields})


def mcp(world, app, tool, arguments=None, **fields):
    return call(world, "mcp-call", server="app:" + app, tool=tool, arguments=arguments or {}, **fields)


def test_notes_references_are_lookup_descriptors_and_never_bodies_or_roots(world):
    reply = call(world, query="needle")
    ref = reply["reviewReferences"]
    assert set(ref) == {"version", "source", "binding", "entries", "truncated"}
    assert ref["source"] == "obsidian" and ref["version"] == 1 and not ref["truncated"]
    assert ref["entries"][0]["path"] == "folder/Example.md"
    assert len(ref["binding"]) == len(ref["entries"][0]["binding"]) == 64
    assert all(value not in json.dumps(ref) for value in (str(world[2]), "original private body", "snippet"))
    read = call(world, "notes-read", path=" folder/Example ", recoveryReferenceContext=ref)
    assert read["path"] == "folder/Example.md" and "original private body" in read["text"]
    assert "reviewReferences" not in read
    pending = call(world, "notes-write", path="folder/Example", text="Ask only.", recoveryReferenceContext=ref)
    assert pending["status"] == "approval-required" and "reviewReferences" not in pending
    assert world[1].approvals()[0]["state"] == "pending"


@pytest.mark.parametrize("replacement", ["root", "parent", "target"])
@pytest.mark.parametrize("action", ["notes-read", "notes-write"])
def test_replacement_refuses_before_content_or_proposal(world, monkeypatch, replacement, action):
    ref = call(world, query="needle")["reviewReferences"]
    service, store, vault, _ = world
    if replacement == "root":
        vault.rename(vault.with_name("old-vault"))
        (vault / "folder").mkdir(parents=True)
        (vault / "folder/Example.md").write_text("replacement body")
    elif replacement == "parent":
        (vault / "folder").rename(vault / "old-folder")
        (vault / "folder").mkdir()
        (vault / "old-folder/Example.md").rename(vault / "folder/Example.md")
    else:
        (vault / "new.md").write_text("replacement body")
        os.replace(vault / "new.md", vault / "folder/Example.md")
    monkeypatch.setattr(notes, "_read_fd", lambda *_: pytest.fail("stale reference read content"))
    monkeypatch.setattr(service, "_review_approval", lambda *_: pytest.fail("stale reference proposed an action"))
    with pytest.raises(IntegrationError) as caught:
        call(world, action, path="folder/Example", text="Never proposed.", recoveryReferenceContext=ref)
    assert caught.value.code == "stale-review-reference"
    assert store.approvals() == []


def test_lookup_binds_actual_read_descriptor_not_a_later_replacement(world, monkeypatch):
    vault = world[2]
    before = call(world, query="needle")["reviewReferences"]
    replacement = vault.parent / "replacement.md"
    replacement.write_text("different body")
    original = notes._read_fd

    def replace_after_read(fd):
        text = original(fd)
        if "needle" in text:
            os.replace(replacement, vault / "folder/Example.md")
        return text

    with monkeypatch.context() as patch:
        patch.setattr(notes, "_read_fd", replace_after_read)
        ref = call(world, query="needle")["reviewReferences"]
    assert ref == before  # The lookup did not adopt a later path stat as its evidence.
    with pytest.raises(IntegrationError) as caught:
        call(world, "notes-read", path="folder/Example.md", recoveryReferenceContext=ref)
    assert caught.value.code == "stale-review-reference"


def test_reference_does_not_invent_a_lookup_requirement_for_owner_named_targets(world):
    ref = call(world, query="needle")["reviewReferences"]
    assert "owner-selected" in call(world, "notes-read", path="Other", recoveryReferenceContext=ref)["text"]
    normal = world[0].handle("agent", {"schemaVersion": 1, "action": "notes-search", "query": "needle"})
    assert "reviewReferences" not in normal and normal["results"][0]["path"] == "folder/Example.md"


def test_paperless_ids_keep_all_decimal_digits_and_titles_are_redacted(world, monkeypatch):
    calls = []
    identity = 9007199254740993

    def paperless(_settings, _key, path, **_kwargs):
        calls.append(path)
        if path == "/api/documents/":
            return {"count": 1, "results": [{"id": identity, "title": "Lease documents-fixture-secret", "content": "needle body"}]}
        return {"content": "read exactly that large ID", "title": "Lease"}

    monkeypatch.setattr(apps, "_paperless", paperless)
    ref = mcp(world, "documents", "search", {"query": "needle"})["reviewReferences"]
    assert ref["source"] == "paperless"
    assert ref["entries"] == [{"id": str(identity), "title": "Lease [redacted]"}]
    assert "needle body" not in json.dumps(ref)
    result = mcp(world, "documents", "read", {"id": str(identity)}, recoveryReferenceContext=ref)
    assert "read exactly" in result["text"] and calls[-1] == f"/api/documents/{identity}/"
    assert "reviewReferences" not in result


@pytest.mark.parametrize("app,tool,args", [("documents", "read", {"id": 1}), ("documents", "search", {"query": "needle"}),
                                         ("watch", "list", {}), ("watch", "remove", {"id": "w1"})])
def test_changed_coherent_app_pairing_refuses_before_provider(world, monkeypatch, app, tool, args):
    monkeypatch.setattr(apps, "documents_search", lambda *_: {"results": [], "total": 0})
    monkeypatch.setattr(apps, "watches", lambda *_: {"w1": {"url": "https://example.invalid/page", "title": "Page"}})
    ref = mcp(world, app, "search" if app == "documents" else "list", {"query": "needle"} if app == "documents" else {})["reviewReferences"]
    service, store, *_ = world
    config = store.config()
    settings = {key: config[key] for key in ("notes", "notifications", "mcp", "apps")}
    settings["apps"][app]["url"] = f"https://new-{app}.example.invalid"
    service.save(config["revision"], settings, {})
    monkeypatch.setattr(apps, "documents_search", lambda *_: pytest.fail("stale source reached provider"))
    monkeypatch.setattr(apps, "documents_read", lambda *_: pytest.fail("stale source reached provider"))
    monkeypatch.setattr(apps, "watches", lambda *_: pytest.fail("stale source reached provider"))
    with pytest.raises(IntegrationError) as caught:
        mcp(world, app, tool, args, recoveryReferenceContext=ref)
    assert caught.value.code == "stale-review-reference"
    assert store.approvals() == []


def test_watch_envelope_uses_raw_structured_rows_and_bounds_redaction_and_utf8(world, monkeypatch):
    listing = {str(index): {"title": "🦊" * 200, "url": "https://example.invalid/" + "🦊" * 400} for index in range(40)}
    listing["watch-fixture-secret"] = {"title": "hidden identity", "url": "https://example.invalid"}
    listing["credential-url"] = {"title": "hidden credentials", "url": "https://" + "x" * 510 + ":password@example.invalid"}
    monkeypatch.setattr(apps, "watches", lambda *_: listing)
    ref = mcp(world, "watch", "list")["reviewReferences"]
    assert ref["source"] == "watch" and ref["truncated"]
    assert 0 < len(ref["entries"]) <= 16
    assert len(json.dumps(ref, ensure_ascii=False, separators=(",", ":")).encode()) <= 16384
    assert len(json.dumps(ref, separators=(",", ":")).encode()) <= 16384
    assert all(entry["id"] not in {"watch-fixture-secret", "credential-url"} for entry in ref["entries"])
    assert "password" not in json.dumps(ref)
    ordinary = world[0].handle("agent", {"schemaVersion": 1, "action": "mcp-call", "server": "app:watch", "tool": "list"})
    assert "reviewReferences" not in ordinary and "_reviewEntries" not in ordinary


@pytest.mark.parametrize("change", [lambda r: r.update(version=True), lambda r: r.update(source="watch"),
                                   lambda r: r.update(extra="untrusted"), lambda r: r.update(entries=r["entries"] * 17),
                                   lambda r: r["entries"][0].update(path="../escape.md"),
                                   lambda r: r["entries"][0].update(binding="not-a-binding")])
def test_malformed_private_context_refuses_before_read(world, monkeypatch, change):
    ref = copy.deepcopy(call(world, query="needle")["reviewReferences"])
    change(ref)
    monkeypatch.setattr(notes, "read", lambda *_args, **_kwargs: pytest.fail("malformed reference reached a read"))
    with pytest.raises(IntegrationError) as caught:
        call(world, "notes-read", path="folder/Example", recoveryReferenceContext=ref)
    assert caught.value.code == "invalid-review-reference"


def test_nonreview_request_cannot_silently_ignore_private_context(world):
    ref = call(world, query="needle")["reviewReferences"]
    with pytest.raises(IntegrationError) as caught:
        world[0].handle("agent", {"schemaVersion": 1, "action": "notes-read", "path": "Other", "recoveryReferenceContext": ref})
    assert caught.value.code == "invalid-review-reference"


def test_unrelated_metadata_or_custom_tools_cannot_ignore_a_source_context(world):
    ref = call(world, query="needle")["reviewReferences"]
    for action, fields in (("mcp-tools", {}), ("mcp-call", {"server": "custom", "tool": "opaque", "arguments": {}}),
                           ("mcp-call", {"server": "app:photos", "tool": "search", "arguments": {"place": "Lisbon"}})):
        with pytest.raises(IntegrationError) as caught:
            call(world, action, recoveryReferenceContext=ref, **fields)
        assert caught.value.code == "invalid-review-reference"


def test_oversized_expected_context_and_forged_binding_refuse_before_provider(world, monkeypatch):
    ref = {"version": 1, "source": "watch", "binding": "0" * 64, "truncated": False,
           "entries": [{"id": str(index), "title": "🦊" * 200, "url": "https://example.invalid/" + "🦊" * 400}
                       for index in range(16)]}
    monkeypatch.setattr(apps, "watches", lambda *_: pytest.fail("invalid context reached provider"))
    with pytest.raises(IntegrationError) as caught:
        mcp(world, "watch", "list", recoveryReferenceContext=ref)
    assert caught.value.code == "invalid-review-reference"
    ref["entries"] = []
    with pytest.raises(IntegrationError) as caught:
        mcp(world, "watch", "list", recoveryReferenceContext=ref)
    assert caught.value.code == "stale-review-reference"


def test_reference_identifiers_are_never_redacted_into_a_different_identity(world):
    service = world[0]
    docs = service._reference_envelope("paperless", "0" * 64,
        [{"id": value, "title": "Fixture"} for value in (12345, 0, True, -1, 2**63, "١", 2**63 - 1)], {"key": "12345"})
    assert docs["entries"] == [{"id": str(2**63 - 1), "title": "Fixture"}] and docs["truncated"]
    obsidian = service._reference_envelope("obsidian", "0" * 64,
        [{"path": value, "binding": "1" * 64} for value in ("private-key.md", " note.md", "../escape.md", "Note.md")],
        {"key": "private-key"})
    assert obsidian["entries"] == [{"path": "Note.md", "binding": "1" * 64}] and obsidian["truncated"]


def test_watch_urls_are_full_exact_public_identifiers_or_omitted(world, monkeypatch):
    listing = {"long": {"title": "Long", "url": "https://example.invalid/" + "x" * 501},
               "redacted": {"title": "Secret", "url": "https://example.invalid/watch-fixture-secret"},
               "credentials": {"title": "Unsafe", "url": "https://user:password@example.invalid"},
               "good": {"title": "Safe", "url": "https://example.invalid/complete"}}
    monkeypatch.setattr(apps, "watches", lambda *_: listing)
    ref = mcp(world, "watch", "list")["reviewReferences"]
    assert ref["entries"] == [{"id": "good", "title": "Safe", "url": "https://example.invalid/complete"}]
    assert ref["truncated"]


@pytest.mark.parametrize("listing", [{"w1": {"title": "New", "url": "https://example.invalid/different"}}, {}])
def test_remote_watch_replacement_refuses_after_one_metadata_read_before_proposal(world, monkeypatch, listing):
    monkeypatch.setattr(apps, "watches", lambda *_: {"w1": {"title": "Old", "url": "https://example.invalid/old"}})
    ref = mcp(world, "watch", "list")["reviewReferences"]
    reads = []

    def current(*_args):
        reads.append("metadata")
        return listing

    monkeypatch.setattr(apps, "watches", current)
    monkeypatch.setattr(world[0], "_review_approval", lambda *_: pytest.fail("changed URL reached proposal"))
    with pytest.raises(IntegrationError) as caught:
        mcp(world, "watch", "remove", {"id": "w1"}, recoveryReferenceContext=ref)
    assert caught.value.code == "stale-review-reference"
    assert reads == ["metadata"] and world[1].approvals() == []


def test_actual_same_inode_recreation_is_refused_and_legacy_binding_shape_is_unchanged(world):
    service, store, vault, _ = world
    path = vault / "folder/Example.md"
    config = store.config()
    legacy = notes.write_binding(config, "folder/Example.md", "append")
    ref = call(world, query="needle")["reviewReferences"]
    assert notes.write_binding(config, "folder/Example.md", "append", reference_context=ref) == legacy
    before = path.stat()
    for _attempt in range(256):
        path.unlink()
        path.write_text("replacement invented content")
        if path.stat().st_ino == before.st_ino:
            break
    else:
        pytest.skip("This filesystem did not reuse the bounded fixture inode; descriptor replacement controls still run")
    assert path.stat().st_ctime_ns != before.st_ctime_ns
    with pytest.raises(IntegrationError) as caught:
        call(world, "notes-read", path="folder/Example.md", recoveryReferenceContext=ref)
    assert caught.value.code == "stale-review-reference"
    assert set(legacy[1]) == {"vault", "root", "parents", "missing", "target"}
    assert store.approvals() == []


@pytest.mark.parametrize("change", ["body", "parent", "root"])
def test_reference_freshness_stamps_conservatively_expire_without_inode_replacement(world, monkeypatch, change):
    ref = call(world, query="needle")["reviewReferences"]
    vault = world[2]
    if change == "body":
        (vault / "folder/Example.md").write_text("modified same-inode body")
    else:
        ((vault / "folder") if change == "parent" else vault).joinpath("New.md").write_text("new directory entry")
    monkeypatch.setattr(notes, "_read_fd", lambda *_: pytest.fail("expired freshness reached note content"))
    with pytest.raises(IntegrationError) as caught:
        call(world, "notes-read", path="folder/Example.md", recoveryReferenceContext=ref)
    assert caught.value.code == "stale-review-reference"


@pytest.mark.parametrize("app,tool,args", [("documents", "search", {"query": "x" * 201}),
    ("photos", "search", {"query": "x" * 201}), ("photos", "search", {"place": "x" * 101}),
    ("photos", "search", {"place": {"city": "Lisbon"}}),
    ("watch", "add", {"url": "https://example.invalid", "title": "x" * 201}),
    ("watch", "add", {"url": "https://example.invalid/" + "x" * 2000})])
def test_review_truncation_or_wrong_text_type_refuses_before_private_app_access(world, monkeypatch, app, tool, args):
    monkeypatch.setattr(world[0], "_verified_app_snapshot", lambda *_args, **_kw: pytest.fail("truncated input reached snapshot"))
    with pytest.raises(IntegrationError) as caught:
        mcp(world, app, tool, args)
    assert caught.value.code == "invalid-request"


def test_notes_query_bound_and_type_are_refused_before_private_read(world, monkeypatch):
    monkeypatch.setattr(notes, "search", lambda *_args, **_kw: pytest.fail("truncated input reached vault"))
    for value in ("x" * 301, {"query": "needle"}, "\ud800"):
        with pytest.raises(IntegrationError) as caught:
            call(world, query=value)
        assert caught.value.code == "invalid-request"


def test_actual_review_text_boundaries_and_normal_coercions_are_preserved(world, monkeypatch):
    service, store, *_ = world
    seen = []
    monkeypatch.setattr(apps, "documents_search", lambda _settings, _key, query: seen.append(query) or {"results": [], "total": 0})
    mcp(world, "documents", "search", {"query": " " + "x" * 200 + " "})
    assert seen == [" " + "x" * 200 + " "]  # The actual app still owns whitespace normalization.
    seen.clear()
    service.handle("agent", {"schemaVersion": 1, "action": "mcp-call", "server": "app:documents", "tool": "search",
                             "arguments": {"query": "x" * 201}})
    assert seen == ["x" * 201]  # Ordinary first-pass behavior is deliberately unchanged.
    assert notes.write_binding(store.config(), "folder/Example.md", "append")[0] == "folder/Example.md"


def test_factory_literal_utf8_context_budget_is_distinct_from_host_ascii_output_budget(world):
    context = {"version": 1, "source": "watch", "binding": "0" * 64, "truncated": True,
               "entries": [{"id": str(index), "title": "🦊" * 100, "url": "https://example.invalid/" + "🦊" * 300}
                           for index in range(8)]}
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()) < 16384
    assert len(json.dumps(context, separators=(",", ":")).encode()) > 16384
    detached = world[0]._reference_context({"recoveryReferenceContext": context}, "watch")
    assert detached == context and detached is not context


@pytest.mark.parametrize("failure", ["unavailable", "failed"])
def test_review_never_drops_photo_query_but_normal_fallback_stays_intact(world, monkeypatch, failure):
    paths = []

    def immich(_settings, _key, path, body=None):
        paths.append((path, body))
        if path == "/api/server/features":
            return {"smartSearch": failure != "unavailable"}
        if path == "/api/search/smart":
            assert body["query"] == "dogs"
            raise IntegrationError("app-failed", "Invented ML failure")
        assert path == "/api/search/metadata"
        return {"assets": {"items": [{"id": "broad-result", "originalFileName": "All at that date", "exifInfo": {}}]}}

    monkeypatch.setattr(apps, "_immich", immich)
    arguments = {"query": "dogs", "after": "2026-01-01"}
    with pytest.raises(IntegrationError) as caught:
        mcp(world, "photos", "search", arguments)
    assert caught.value.code == "photos-no-description"
    assert not any(path == "/api/search/metadata" for path, _body in paths)
    paths.clear()
    normal = world[0].handle("agent", {"schemaVersion": 1, "action": "mcp-call", "server": "app:photos", "tool": "search", "arguments": arguments})
    assert any(path == "/api/search/metadata" for path, _body in paths)
    assert 'Searched by place and date only, not for "dogs"' in normal["text"]
    assert "broad-result" in normal["text"]
