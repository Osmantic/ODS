import http.client
import hashlib
import importlib.util
import os
import pathlib
import tempfile
import threading


MODULE_PATH = pathlib.Path(__file__).parents[1] / "host" / "workspace_preview.py"
SPEC = importlib.util.spec_from_file_location("workspace_preview", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_preview_http_declares_utf8_for_published_text_without_rewriting_bytes():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace, previews = root / "workspace", root / "previews"
        workspace.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        site = workspace / "demo"
        site.mkdir(mode=0o700)
        content = {"index.html": "<h1>Tiếng Việt — 日本語</h1>".encode("utf-8"),
                   "style.css": 'h1::after { content: "café"; }'.encode("utf-8"),
                   "app.js": 'const message = "Olá";'.encode("utf-8"),
                   "image.png": b"\x89PNG\r\n\x1a\n",
                   "legacy.html": b'<meta charset="windows-1252"><p>caf\xe9</p>'}
        for name, data in content.items():
            (site / name).write_bytes(data)
            (site / name).chmod(0o600)
        receipt = MODULE.publish_snapshot(workspace, previews, "demo", os.getuid())
        with MODULE.PreviewHTTPServer(("127.0.0.1", 0), previews) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for name, data in content.items():
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request("GET", f"/{receipt['siteId']}/{name}",
                                       headers={"Host": f"{receipt['siteId']}.localhost:{server.server_port}"})
                    response = connection.getresponse()
                    assert response.status == 200
                    expected_charset = None if name in {"image.png", "legacy.html"} else "utf-8"
                    assert response.headers.get_content_charset() == expected_charset
                    assert response.read() == data
                    assert response.headers["X-Preview-SHA256"] == hashlib.sha256(data).hexdigest()
                    connection.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)
