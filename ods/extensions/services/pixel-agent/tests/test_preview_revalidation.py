import json
import os
import socket
import threading
import pytest
from test_workspace_preview import MODULE as preview


@pytest.fixture(autouse=True)
def private_test_files():
    previous = os.umask(0o022)
    yield
    os.umask(previous)


@pytest.fixture
def published(tmp_path):
    workspace = tmp_path / "workspace"
    snapshots = tmp_path / "snapshots"
    workspace.mkdir(mode=0o700)
    snapshots.mkdir(mode=0o700)
    site = workspace / "site"
    site.mkdir(mode=0o700)
    (site / "index.html").write_text("<title>Original</title>")
    (site / "style.css").write_text("body{color:navy}")
    receipt = preview.publish_snapshot(workspace, snapshots, "site", os.getuid())
    request = {"schemaVersion":1, "action":"verify-current", "relativeDirectory":"site",
               "siteId":receipt["siteId"], "sha256":receipt["sha256"]}
    return workspace, snapshots, site, request


def test_revalidates_exact_bytes_without_writing_or_republishing(published, monkeypatch):
    workspace, snapshots, site, request = published
    before = [(str(p), p.stat().st_mtime_ns) for p in snapshots.rglob("*")]
    monkeypatch.setattr(preview, "publish_snapshot", lambda *a: pytest.fail("must not publish"))
    result = preview.verify_current_snapshot(workspace, snapshots, request, os.getuid())
    assert result["status"] == "matched"
    assert result["sha256"] == request["sha256"]
    assert before == [(str(p), p.stat().st_mtime_ns) for p in snapshots.rglob("*")]


@pytest.mark.parametrize("change", ["same-size", "added", "deleted", "renamed", "symlink", "hardlink"])
def test_changes_never_match(published, change):
    workspace, snapshots, site, request = published
    if change == "same-size": (site / "style.css").write_text("body{color:pink}")
    if change == "added": (site / "extra.txt").write_text("new")
    if change == "deleted": (site / "style.css").unlink()
    if change == "renamed": (site / "style.css").rename(site / "other.css")
    if change == "symlink": (site / "extra.txt").symlink_to(site / "style.css")
    if change == "hardlink": os.link(site / "style.css", site / "extra.txt")
    try:
        assert preview.verify_current_snapshot(workspace, snapshots, request, os.getuid())["status"] == "mismatched"
    except preview.PreviewError:
        assert change in ["symlink", "hardlink"]


def test_capture_rewalk_rejects_late_added_file(published, monkeypatch):
    workspace, snapshots, site, request = published
    original = preview._read_stable
    def read(source, expected):
        data = original(source, expected)
        if source == site / "style.css": (site / "late.txt").write_text("race")
        return data
    monkeypatch.setattr(preview, "_read_stable", read)
    with pytest.raises(preview.PreviewError, match="source changed"):
        preview.verify_current_snapshot(workspace, snapshots, request, os.getuid())


def test_forged_full_digest_is_rejected(published):
    workspace, snapshots, site, request = published
    request["sha256"] = request["sha256"][:24] + "0" * 40
    with pytest.raises(preview.PreviewError):
        preview.verify_current_snapshot(workspace, snapshots, request, os.getuid())


def test_exact_protocol_and_existing_publish_unchanged(published):
    _, _, _, request = published
    assert preview.parse_request(json.dumps(request).encode()) == request
    publish = {"schemaVersion":1, "action":"publish", "relativeDirectory":"site"}
    assert preview.parse_request(json.dumps(publish).encode()) == publish
    for extra in [{"path":"/tmp"}, {"action":"execute"}, {"sha256":"0"*64}, {"siteId":"../site"}]:
        with pytest.raises(preview.PreviewError):
            preview.parse_request(json.dumps({**request, **extra}).encode())


def test_real_control_socket_verifies_without_http_or_publication(published, monkeypatch):
    workspace, snapshots, _, request = published
    monkeypatch.setattr(preview, "publish_snapshot", lambda *a: pytest.fail("must not publish"))
    monkeypatch.setattr(preview, "_verify_http", lambda *a: pytest.fail("must not claim new HTTP publication"))
    client, server = socket.socketpair()
    thread = threading.Thread(target=preview._serve_connection, args=(server,), kwargs={
        "workspace":workspace, "previews":snapshots, "owner_uid":os.getuid(), "port":9437})
    thread.start()
    try:
        client.settimeout(5)
        client.sendall(json.dumps(request).encode() + b"\n")
        client.shutdown(socket.SHUT_WR)
        result = json.loads(client.makefile("rb").readline())
        assert result["status"] == "matched"
        assert result["sha256"] == request["sha256"]
        assert "url" not in result and "httpStatus" not in result
    finally:
        thread.join(timeout=5)
        client.close()
        server.close()


def test_read_race_that_restores_bytes_still_rejects(published, monkeypatch):
    workspace, snapshots, site, request = published
    original = preview._read_stable
    def read(source, expected):
        data = original(source, expected)
        if source == site / "style.css":
            source.write_bytes(b"changed")
            source.write_bytes(data)
        return data
    monkeypatch.setattr(preview, "_read_stable", read)
    with pytest.raises(preview.PreviewError, match="source changed"):
        preview.verify_current_snapshot(workspace, snapshots, request, os.getuid())


def test_unsafe_root_is_not_revalidated(published):
    workspace, snapshots, _, request = published
    workspace.chmod(0o777)
    with pytest.raises(preview.PreviewError):
        preview.verify_current_snapshot(workspace, snapshots, request, os.getuid())
