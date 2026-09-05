import http.client
import importlib.util
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


def test_snapshot_is_content_addressed_and_create_only():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "demo-site"
        assets = site / "assets"
        site.mkdir(parents=True, mode=0o700)
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
        site.mkdir(parents=True, mode=0o700)
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
        site.mkdir(parents=True, mode=0o700)
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
        site.mkdir(parents=True, mode=0o700)
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
                    headers={"Host": f"{receipt['siteId']}.localhost:{port}"},
                )
                response = connection.getresponse()
                response.read()
                assert response.status == 200
                assert response.headers["Cross-Origin-Resource-Policy"] == "cross-origin"
                assert "connect-src 'self'" in response.headers["Content-Security-Policy"]
                assert "connect-src 'none'" not in response.headers["Content-Security-Policy"]
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
                cross_site.close()
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
        site.mkdir(parents=True, mode=0o700)
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
