import sys

if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('Preview broker requires POSIX; run this contract under Linux/WSL')

import hashlib
import http.client
import json
import os
import threading

import pytest
from test_workspace_preview import MODULE


def make_site(tmp_path, files):
    workspace, previews = tmp_path / 'workspace', tmp_path / 'previews'
    workspace.mkdir(mode=0o700)
    previews.mkdir(mode=0o700)
    site = workspace / 'demo'
    site.mkdir(mode=0o700)
    for name, data in {'index.html': b'<h1>Project</h1>', **files}.items():
        path = site / name
        path.write_bytes(data)
        path.chmod(0o600)
    return workspace, previews


def test_publish_project_documents_and_serve_exact_inert_bytes(tmp_path):
    names = ('README', 'LICENSE', 'NOTICE', 'Dockerfile', 'Containerfile', 'Makefile', 'GNUmakefile')
    files = {name: f'# {name}\n<script>alert(1)</script>\n'.encode() for name in names}
    workspace, previews = make_site(tmp_path, files)
    receipt = MODULE.publish_snapshot(workspace, previews, 'demo', os.getuid())
    manifest = json.loads(MODULE.snapshot_manifest(previews, receipt['siteId']))
    assert {item['path'] for item in manifest['files']} == {'index.html', *names}
    with MODULE.PreviewHTTPServer(('127.0.0.1', 0), previews) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for name, data in files.items():
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                try:
                    connection.request('GET', f'/{receipt["siteId"]}/{name}',
                                       headers={'Host':f'{receipt["siteId"]}.localhost:{server.server_port}'})
                    response = connection.getresponse()
                    assert response.status == 200
                    assert response.headers.get_content_type() == 'text/plain'
                    assert response.headers['X-Content-Type-Options'] == 'nosniff'
                    assert response.headers['X-Preview-SHA256'] == hashlib.sha256(data).hexdigest()
                    assert response.read() == data
                finally:
                    connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize('name', ['program', 'secret.env', 'credentials', 'Dockerfile.exe'])
def test_unknown_extensionless_or_executable_names_remain_rejected(tmp_path, name):
    workspace, previews = make_site(tmp_path, {name:b'not public documentation'})
    with pytest.raises(MODULE.PreviewError, match='unsupported preview file type'):
        MODULE.publish_snapshot(workspace, previews, 'demo', os.getuid())
    assert not list(previews.iterdir())
