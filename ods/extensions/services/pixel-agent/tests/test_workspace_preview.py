import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import http.client
import hashlib
import importlib.util
import json
import os
import pathlib
import socket
import tempfile
import threading


MODULE_PATH = pathlib.Path(__file__).parents[1] / "host" / "workspace_preview.py"
SPEC = importlib.util.spec_from_file_location("workspace_preview", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("pixel-preview.internal", timeout=5)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def test_manifest_rehashes_published_files_without_reading_live_workspace():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace, previews = root / "workspace", root / "previews"
        workspace.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        site = workspace / "demo"
        site.mkdir(mode=0o700)
        (site / "assets").mkdir(mode=0o700)
        for name, content in {"index.html": "<h1>Original</h1>", "assets/app.js": "console.log(1)"}.items():
            (site / name).write_text(content)
            (site / name).chmod(0o600)
        receipt = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
        (site / "index.html").write_text("unpublished change")
        manifest = json.loads(MODULE.snapshot_manifest(previews, receipt["siteId"]))
        assert manifest["sha256"] == receipt["sha256"]
        assert manifest["bytes"] == receipt["bytes"]
        assert [f["path"] for f in manifest["files"]] == ["assets/app.js", "index.html"]
        assert manifest["files"][1]["sha256"] == receipt["entrySha256"]
        assert "unpublished" not in json.dumps(manifest)
        with MODULE.PreviewHTTPServer(("127.0.0.1", 0), previews) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for tail, host, expected in [
                    ("__ods_manifest__.json", f"{receipt['siteId']}.localhost:{server.server_port}", 200),
                    ("__ods_manifest__.json", "wrong-host", 404),
                    ("__ods_manifest__.json?path=/etc/passwd", f"{receipt['siteId']}.localhost:{server.server_port}", 404),
                    ("__ods_manifest__.json/other", f"{receipt['siteId']}.localhost:{server.server_port}", 404),
                ]:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                    connection.request("GET", f"/{receipt['siteId']}/{tail}", headers={"Host": host})
                    response = connection.getresponse()
                    body = response.read()
                    assert response.status == expected
                    if expected == 200:
                        assert json.loads(body) == manifest
                        assert response.headers["X-Preview-SHA256"] == hashlib.sha256(body).hexdigest()
                        assert response.headers["Content-Type"] == "application/json"
                    connection.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)
        target = previews / receipt["siteId"] / "index.html"
        target.chmod(0o600)
        target.write_text("modified snapshot")
        target.chmod(0o400)
        try:
            MODULE.snapshot_manifest(previews, receipt["siteId"])
        except MODULE.PreviewError:
            pass
        else:
            raise AssertionError("modified snapshot manifest was accepted")
        target.unlink()
        target.symlink_to(site / "index.html")
        try:
            MODULE.snapshot_manifest(previews, receipt["siteId"])
        except MODULE.PreviewError:
            pass
        else:
            raise AssertionError("symlink was accepted")


def test_snapshot_preserves_csv_and_tsv_app_data_and_prior_versions():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace, previews = root / "workspace", root / "previews"
        site = workspace / "energy-dashboard"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        data = {
            "index.html": '<script src="app.js"></script>',
            "app.js": 'fetch("data.csv").then(r => r.text())',
            "style.css": "body{color:green}",
            "data.csv": "day,kwh\nMonday,12.5\n",
            "data.tsv": "day\tkwh\nMonday\t12.5\n",
        }
        for name, content in data.items():
            (site / name).write_text(content)
            (site / name).chmod(0o600)
        first = MODULE.publish_snapshot(workspace, previews, site.name, os.getuid())
        assert first["files"] == len(data)
        for name, content in data.items():
            assert (previews / first["siteId"] / name).read_text() == content
        (site / "data.csv").write_text("day,kwh\nMonday,14.0\n")
        second = MODULE.publish_snapshot(workspace, previews, site.name, os.getuid())
        assert second["siteId"] != first["siteId"]
        assert (previews / first["siteId"] / "data.csv").read_text() == data["data.csv"]


def test_control_socket_reports_fixed_actionable_errors_without_host_paths():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace, previews = root / "workspace", root / "previews"
        site = workspace / "demo"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        for name in ["style.css", "unsupported.exe"]:
            (site / name).write_text("fixture, not executable")
            (site / name).chmod(0o600)
        for expected in ["unsupported_file_type", "missing_entry"]:
            client, server = socket.socketpair()
            thread = threading.Thread(target=MODULE._serve_connection, args=(server,), kwargs={
                "workspace": workspace, "previews": previews,
                "owner_uid": os.getuid(), "port": 9437,
            })
            thread.start()
            try:
                client.settimeout(5)
                client.sendall(b'{"schemaVersion":1,"action":"publish","relativeDirectory":"demo"}\n')
                client.shutdown(socket.SHUT_WR)
                raw = client.makefile("rb").readline()
                result = json.loads(raw)
                assert result["status"] == "failed"
                assert result["errorCode"] == expected
                assert str(root).encode() not in raw
                assert b"unsupported.exe" not in raw
            finally:
                thread.join(timeout=5)
                client.close()
                server.close()
            if expected == "unsupported_file_type":
                (site / "unsupported.exe").unlink()


def test_snapshot_is_content_addressed_and_create_only():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "demo-site"
        assets = site / "assets"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        assets.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        (site / "index.html").write_text("<h1>Hello</h1>", encoding="utf-8")
        (assets / "styles.css").write_text("h1{color:purple}", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)
        os.chmod(assets / "styles.css", 0o600)

        first = MODULE.publish_snapshot(workspace, previews, "demo-site", os.getuid())
        second = MODULE.publish_snapshot(workspace, previews, "demo-site", os.getuid())

        assert first == second
        assert first["siteId"].startswith("site-")
        assert first["files"] == 2
        assert first["overwritten"] is False
        assert (previews / first["siteId"] / "index.html").read_text() == "<h1>Hello</h1>"
        assert (previews / first["siteId"] / "assets" / "styles.css").read_text() == "h1{color:purple}"


def test_snapshot_rejects_symlinks_and_missing_entrypoint():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "demo-site"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        (site / "styles.css").write_text("body{}", encoding="utf-8")
        os.chmod(site / "styles.css", 0o600)
        try:
            MODULE.publish_snapshot(workspace, previews, "demo-site", os.getuid())
        except MODULE.PreviewError as error:
            assert "index.html" in str(error)
        else:
            raise AssertionError("missing index.html was accepted")

        (site / "index.html").symlink_to(site / "styles.css")
        try:
            MODULE.publish_snapshot(workspace, previews, "demo-site", os.getuid())
        except MODULE.PreviewError as error:
            assert "file" in str(error)
        else:
            raise AssertionError("symlinked index.html was accepted")


def test_request_parser_rejects_escape_and_extra_fields():
    assert MODULE.parse_request(
        b'{"schemaVersion":1,"action":"publish","relativeDirectory":"a/b"}'
    )["relativeDirectory"] == "a/b"
    for payload in (
        b'{"schemaVersion":1,"action":"publish","relativeDirectory":"../a"}',
        b'{"schemaVersion":1,"action":"publish","relativeDirectory":"a","extra":1}',
    ):
        try:
            MODULE.parse_request(payload)
        except MODULE.PreviewError:
            pass
        else:
            raise AssertionError("unsafe request was accepted")


def test_existing_snapshot_is_revalidated_before_receipt_reuse():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "demo-site"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        (site / "index.html").write_text("<h1>Verified</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        receipt = MODULE.publish_snapshot(workspace, previews, "demo-site", os.getuid())
        snapshot = previews / receipt["siteId"] / "index.html"
        os.chmod(snapshot, 0o600)
        snapshot.write_text("<h1>Tampered</h1>", encoding="utf-8")
        os.chmod(snapshot, 0o400)
        try:
            MODULE.publish_snapshot(workspace, previews, "demo-site", os.getuid())
        except MODULE.PreviewError as error:
            assert "verification" in str(error)
        else:
            raise AssertionError("tampered content-addressed preview was reused")


def test_http_preview_allows_only_csp_guarded_cross_origin_embedding():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "demo-site"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        (site / "index.html").write_text("<h1>Interactive</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)
        receipt = MODULE.publish_snapshot(workspace, previews, "demo-site", os.getuid())

        with MODULE.PreviewHTTPServer(("127.0.0.1", 0), previews) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "GET",
                    f"/{receipt['siteId']}/",
                    headers={"Host": f"{receipt['siteId']}.localhost:{port}", "Origin": "null"},
                )
                response = connection.getresponse()
                response.read()
                assert response.status == 200
                assert response.headers["Cross-Origin-Resource-Policy"] == "cross-origin"
                assert response.headers["Access-Control-Allow-Origin"] == "*"
                assert "Access-Control-Allow-Credentials" not in response.headers
                assert "connect-src 'self'" in response.headers["Content-Security-Policy"]
                assert "connect-src 'none'" not in response.headers["Content-Security-Policy"]
                assert "form-action 'none'" in response.headers["Content-Security-Policy"]
                assert "frame-ancestors http://localhost:* http://127.0.0.1:*" in (
                    response.headers["Content-Security-Policy"]
                )
                connection.close()

                cross_site = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                cross_site.request(
                    "GET",
                    f"/{receipt['siteId']}/",
                    headers={"Host": f"site-{'0' * 24}.localhost:{port}"},
                )
                rejected = cross_site.getresponse()
                rejected.read()
                assert rejected.status == 404
                assert "Access-Control-Allow-Origin" not in rejected.headers
                cross_site.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)


def test_published_changes_use_actual_verified_source_versions_and_keep_unknown_baseline_explicit():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace, previews = root / "workspace", root / "previews"
        workspace.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        site = workspace / "demo"
        site.mkdir(mode=0o700)
        entry = site / "index.html"
        entry.write_text("<!doctype html>\n<h1>Cobrinha</h1>\n<p>Keep</p>\n")
        os.chmod(entry, 0o600)
        old = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
        entry.write_text("<!doctype html>\n<h1>Cobrao</h1>\n<p>Keep</p>\n")
        new = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
        value = json.loads(MODULE.snapshot_changes(previews, new["siteId"], old["siteId"]))
        assert value["sha256"] == new["sha256"] and value["beforeSha256"] == old["sha256"]
        file = value["changes"][0]
        assert file["change"] == "modified" and file["additions"] == 1 and file["deletions"] == 1
        assert [row["text"] for row in file["diff"] if row["type"] == "remove"] == ["<h1>Cobrinha</h1>"]
        first = json.loads(MODULE.snapshot_changes(previews, old["siteId"], None))["changes"][0]
        assert first["change"] == "published" and first["additions"] == 3 and first["deletions"] == 0
        assert json.loads(MODULE.snapshot_changes(previews, new["siteId"], new["siteId"]))["changes"] == []


def test_changes_http_preserves_final_newline_edits():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace, previews = root / "workspace", root / "previews"
        workspace.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        site = workspace / "demo"
        site.mkdir(mode=0o700)
        entry = site / "index.html"
        for old_text, new_text in [("<h1>Hi</h1>", "<h1>Hi</h1>\n"),
                                   ("<h1>Hi</h1>\n", "<h1>Hi</h1>")]:
            entry.write_text(old_text, encoding="utf-8")
            entry.chmod(0o600)
            old = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
            entry.write_text(new_text, encoding="utf-8")
            new = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
            with MODULE.PreviewHTTPServer(("127.0.0.1", 0), previews) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request("GET", f"/{new['siteId']}/__ods_changes__/{old['siteId']}.json",
                                       headers={"Host": f"{new['siteId']}.localhost:{server.server_port}"})
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    connection.close()
                    assert response.status == 200
                    assert payload["sha256"] == new["sha256"]
                    file = payload["changes"][0]
                    assert (file["additions"], file["deletions"]) == (1, 1)
                    assert [row["type"] for row in file["diff"]] == ["remove", "add"]
                    assert [row["text"] for row in file["diff"]] == ["<h1>Hi</h1>"] * 2
                    assert [row.get("noFinalNewline", False) for row in file["diff"]] == [
                        not old_text.endswith("\n"), not new_text.endswith("\n")]
                finally:
                    server.shutdown()
                    thread.join(timeout=5)


def test_unix_http_preview_accepts_only_the_internal_relay_authority():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "demo-site"
        socket_path = root / "preview.sock"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        (site / "index.html").write_text("<button>Remote</button>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)
        receipt = MODULE.publish_snapshot(workspace, previews, "demo-site", os.getuid())

        with MODULE.PreviewUnixHTTPServer(str(socket_path), previews, 9437) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = UnixHTTPConnection(str(socket_path))
                connection.request(
                    "GET",
                    f"/{receipt['siteId']}/",
                    headers={"Host": "pixel-preview.internal"},
                )
                response = connection.getresponse()
                assert response.read() == b"<button>Remote</button>"
                assert response.status == 200
                assert response.headers["X-Preview-SHA256"] == receipt["entrySha256"]
                connection.close()

                styled_connection = UnixHTTPConnection(str(socket_path))
                styled_connection.request("GET", f"/{receipt['siteId']}/__ods_view__.html",
                                          headers={"Host": "pixel-preview.internal"})
                styled = styled_connection.getresponse()
                styled_body = styled.read()
                assert styled.status == 200
                assert styled_body == b"<button>Remote</button>" + MODULE.PREVIEW_SCROLLBAR_STYLE
                assert b"scrollbar-color:#3d3f43 #131415" in styled_body
                assert styled.headers["X-Preview-SHA256"] == hashlib.sha256(styled_body).hexdigest()
                assert (previews / receipt["siteId"] / "index.html").read_bytes() == b"<button>Remote</button>"
                assert "allow-same-origin" not in styled.headers["Content-Security-Policy"]
                styled_connection.close()

                rejected_connection = UnixHTTPConnection(str(socket_path))
                rejected_connection.request(
                    "GET",
                    f"/{receipt['siteId']}/",
                    headers={"Host": "attacker.invalid"},
                )
                rejected = rejected_connection.getresponse()
                rejected.read()
                assert rejected.status == 404
                rejected_connection.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)


def test_http_snapshot_ignores_asset_queries_without_changing_path_or_bytes():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace, previews = root / "workspace", root / "previews"
        workspace.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        site = workspace / "site"
        site.mkdir(mode=0o700)
        page = b'<link rel="stylesheet" href="style.css?v=2"><script src="app.js?build=abc"></script>'
        assets = {"index.html": page, "style.css": b"body{color:purple}", "app.js": b"document.title='Ready'"}
        for name, data in assets.items():
            (site / name).write_bytes(data)
            (site / name).chmod(0o600)
        receipt = MODULE.publish_snapshot(workspace, previews, "site", os.getuid())
        with MODULE.PreviewHTTPServer(("127.0.0.1", 0), previews) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            host = f"{receipt['siteId']}.localhost:{port}"

            def request(path, method="GET", authority=host):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request(method, path, headers={"Host": authority})
                    response = connection.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    connection.close()

            try:
                for name, expected in assets.items():
                    path = f"/{receipt['siteId']}/{name}?v=2&path=../../secret"
                    status, headers, body = request(path)
                    assert status == 200
                    assert body == expected
                    assert headers["X-Preview-SHA256"] == hashlib.sha256(expected).hexdigest()
                    assert headers["Cache-Control"] == "no-store"
                    assert "form-action 'none'" in headers["Content-Security-Policy"]
                    status, head, body = request(path, "HEAD")
                    assert status == 200 and body == b""
                    assert int(head["Content-Length"]) == len(expected)
                assert request(f"/{receipt['siteId']}/../secret?v=2")[0] == 404
                assert request(f"/{receipt['siteId']}/style.css?v=2", authority="wrong.localhost")[0] == 404
            finally:
                server.shutdown()
                thread.join(timeout=5)
