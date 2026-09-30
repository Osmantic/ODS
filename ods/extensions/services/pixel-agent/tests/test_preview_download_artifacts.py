"""Published document/archive bytes are downloadable, never executed or extracted."""
import hashlib
import http.client
import importlib.util
import os
from pathlib import Path
import threading
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    'preview_downloads', Path(__file__).parents[1] / 'host' / 'workspace_preview.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


CASES = [
    ('report.pdf', b'%PDF-1.4\n% binary \xff\x00\n'),
    ('delivery.zip', b'PK\x05\x06' + bytes(18)),
    ('delivery.rar', b'Rar!\x1a\x07\x00'),
    ('REPORT.PDF', b'%PDF-1.7\n'),
]


def check_document(tmp_path, name, payload):
    workspace, snapshots = tmp_path / 'workspace', tmp_path / 'snapshots'
    workspace.mkdir(mode=0o700)
    snapshots.mkdir(mode=0o700)
    site = workspace / 'delivery'
    site.mkdir(mode=0o700)
    for filename, data in [('index.html', b'<h1>Delivery</h1>'), (name, payload)]:
        target = site / filename
        target.write_bytes(data)
        target.chmod(0o600)
    receipt = MODULE.publish_snapshot(workspace, snapshots, 'delivery', os.getuid())
    # Downloads must use the verified immutable copy, not the live workspace.
    (site / name).write_bytes(b'changed after publication')
    with MODULE.PreviewHTTPServer(('127.0.0.1', 0), snapshots) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for method in ('GET', 'HEAD'):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    connection.request(method, f"/{receipt['siteId']}/{name}",
                        headers={'Host': f"{receipt['siteId']}.localhost:{server.server_port}"})
                    response = connection.getresponse()
                    assert response.status == 200
                    assert response.headers['Content-Type'] == 'application/octet-stream'
                    assert response.headers['Content-Disposition'] == f'attachment; filename="{name}"'
                    assert response.headers['X-Content-Type-Options'] == 'nosniff'
                    assert response.headers['X-Preview-SHA256'] == hashlib.sha256(payload).hexdigest()
                    assert int(response.headers['Content-Length']) == len(payload)
                    assert response.read() == (payload if method == 'GET' else b'')
                finally:
                    connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


class DownloadArtifactTests(unittest.TestCase):
    def test_documents_download_exact_snapshot_bytes_with_attachment_headers(self):
        for name, payload in CASES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                check_document(Path(temporary), name, payload)


if __name__ == '__main__':
    unittest.main()
