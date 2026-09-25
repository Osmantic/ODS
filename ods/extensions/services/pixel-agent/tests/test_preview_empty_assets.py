"""Empty support assets must survive publication, verification and HTTP reads."""

import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX ownership; run under Linux/WSL")

from contextlib import closing
import hashlib
import http.client
import json
import os
import threading

import pytest

from test_workspace_preview import MODULE


def make_site(tmp_path):
    workspace, previews = tmp_path / "workspace", tmp_path / "previews"
    workspace.mkdir(mode=0o700)
    previews.mkdir(mode=0o700)
    site = workspace / "demo"
    site.mkdir(mode=0o700)
    for name, data in {
        "index.html": b'<link rel="stylesheet" href="style.css"><script src="app.js"></script>',
        "style.css": b"", "app.js": b"",
    }.items():
        (site / name).write_bytes(data)
        (site / name).chmod(0o600)
    return workspace, previews, site


def test_empty_assets_survive_publish_manifest_http_and_comparison(tmp_path):
    workspace, previews, site = make_site(tmp_path)
    receipt = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
    assert receipt["files"] == 3
    assert MODULE.publish_snapshot(workspace, previews, "demo", os.getuid()) == receipt
    manifest = json.loads(MODULE.snapshot_manifest(previews, receipt["siteId"]))
    empty_digest = hashlib.sha256(b"").hexdigest()
    for name in ("app.js", "style.css"):
        assert next(row for row in manifest["files"] if row["path"] == name) == {
            "path": name, "bytes": 0, "sha256": empty_digest,
        }

    with MODULE.PreviewHTTPServer(("127.0.0.1", 0), previews) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for method in ("GET", "HEAD"):
                with closing(http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)) as conn:
                    conn.request(method, f"/{receipt['siteId']}/style.css", headers={
                        "Host": f"{receipt['siteId']}.localhost:{server.server_port}",
                    })
                    response = conn.getresponse()
                    assert response.status == 200
                    assert response.getheader("Content-Length") == "0"
                    assert response.getheader("X-Preview-SHA256") == empty_digest
                    assert response.read() == b""
        finally:
            server.shutdown()
            thread.join(timeout=5)

    (site / "style.css").write_text("body { color: red; }\n")
    next_receipt = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
    changes = json.loads(MODULE.snapshot_changes(
        previews, next_receipt["siteId"], receipt["siteId"],
    ))["changes"]
    assert [(row["path"], row["change"], row["additions"], row["deletions"]) for row in changes] == [
        ("style.css", "modified", 1, 0),
    ]
    # Earlier snapshots remain immutable after the support asset gains content.
    assert (previews / receipt["siteId"] / "style.css").read_bytes() == b""


def test_empty_entry_is_still_rejected(tmp_path):
    workspace, previews, site = make_site(tmp_path)
    (site / "index.html").write_bytes(b"")
    with pytest.raises(MODULE.PreviewError):
        MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
    assert list(previews.iterdir()) == []


def test_empty_assets_do_not_bypass_file_count_limit(tmp_path, monkeypatch):
    workspace, previews, _ = make_site(tmp_path)
    monkeypatch.setattr(MODULE, "MAX_FILES", 2)
    with pytest.raises(MODULE.PreviewError):
        MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
    assert list(previews.iterdir()) == []


def test_receipt_lists_empty_published_files(tmp_path):
    workspace, previews, site = make_site(tmp_path)
    receipt = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
    assert receipt["publishedEmptyPaths"] == ["app.js", "style.css"]
    assert receipt["publishedEmptyPathsOmitted"] == 0
    (site / "style.css").write_text("body { color: red; }\n")
    receipt = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
    assert receipt["publishedEmptyPaths"] == ["app.js"]
    (site / "app.js").write_text("console.log(1);\n")
    receipt = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
    assert receipt["publishedEmptyPaths"] == []
    assert receipt["publishedEmptyPathsOmitted"] == 0


def test_empty_test_output_is_published_and_named(tmp_path):
    # tower1 fleet run: `python3 -m unittest -v 2>&1 > public/test-results.txt`
    # sent the runner's stderr to the exec result and left this file empty.
    workspace, previews = tmp_path / "workspace", tmp_path / "previews"
    workspace.mkdir(mode=0o700)
    previews.mkdir(mode=0o700)
    public = workspace / "coding" / "public"
    public.mkdir(mode=0o700, parents=True)
    (workspace / "coding").chmod(0o700)
    for name, data in {"index.html": b"<h1>Totals</h1>", "sources.json": b"{}",
                       "test-results.txt": b""}.items():
        (public / name).write_bytes(data)
        (public / name).chmod(0o600)
    receipt = MODULE.publish_snapshot(workspace, previews, "coding/public", os.getuid())
    assert receipt["status"] == "succeeded"
    assert receipt["publishedPaths"] == ["index.html", "sources.json", "test-results.txt"]
    assert receipt["publishedEmptyPaths"] == ["test-results.txt"]
    assert receipt["publishedEmptyPathsOmitted"] == 0
