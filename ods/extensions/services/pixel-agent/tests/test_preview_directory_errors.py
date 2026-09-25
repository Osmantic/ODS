"""Never issue a successful publication receipt for an unreadable source tree."""
import sys
from unittest import SkipTest

if sys.platform == "win32":
    raise SkipTest("Preview publication uses POSIX ownership and Unix peer credentials")

import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import threading

import pytest

SPEC = importlib.util.spec_from_file_location(
    "preview_directory_errors", Path(__file__).parents[1] / "host/workspace_preview.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def publish_over_socket(workspace, previews):
    with MODULE.PreviewHTTPServer(("127.0.0.1", 0), previews) as httpd:
        server = threading.Thread(target=httpd.serve_forever, daemon=True)
        server.start()
        client, peer = socket.socketpair()
        client.settimeout(5)
        worker = threading.Thread(target=MODULE._serve_connection, args=(peer,),
                                  kwargs=dict(workspace=workspace, previews=previews,
                                              owner_uid=os.getuid(), port=httpd.server_port))
        worker.start()
        try:
            client.sendall(b'{"schemaVersion":1,"action":"publish","relativeDirectory":"demo"}\n')
            with client.makefile("rb") as reader:
                response = json.loads(reader.readline())
            worker.join(timeout=5)
            assert not worker.is_alive()
            if response["status"] == "succeeded":
                connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                connection.request("GET", f"/{response['siteId']}/assets/style.css",
                                   headers={"Host": f"{response['siteId']}.localhost:{httpd.server_port}"})
                result = connection.getresponse()
                response["assetStatus"] = result.status
                result.read()
                connection.close()
            return response
        finally:
            client.close()
            peer.close()
            httpd.shutdown()
            server.join(timeout=5)


def test_unreadable_subdirectory_fails_without_publishing_a_partial_site(tmp_path):
    if os.getuid() == 0:
        pytest.skip("Permission denial needs an unprivileged owner")
    workspace, previews = tmp_path / "workspace", tmp_path / "previews"
    workspace.mkdir(mode=0o700)
    previews.mkdir(mode=0o700)
    site = workspace / "demo"
    site.mkdir(mode=0o700)
    index = site / "index.html"
    index.write_text('<link rel="stylesheet" href="assets/style.css"><p>Complete site</p>')
    index.chmod(0o600)
    assets = site / "assets"
    assets.mkdir(mode=0o700)
    css = assets / "style.css"
    css.write_text("p { color: red; }")
    css.chmod(0o600)
    assets.chmod(0)
    try:
        with pytest.raises(PermissionError):
            list(assets.iterdir())
        response = publish_over_socket(workspace, previews)
        assert response["status"] == "failed", json.dumps(response, sort_keys=True)
        assert response["errorCode"] == "unsafe_directory"
        assert list(previews.iterdir()) == []
    finally:
        assets.chmod(0o700)
    recovered = publish_over_socket(workspace, previews)
    assert recovered["status"] == "succeeded"
    assert recovered["files"] == 2
    assert recovered["assetStatus"] == 200
    assert css.read_text() == "p { color: red; }"


def test_unreadable_version_control_metadata_is_still_excluded(tmp_path):
    workspace, previews = tmp_path / "workspace", tmp_path / "previews"
    workspace.mkdir(mode=0o700)
    previews.mkdir(mode=0o700)
    site = workspace / "demo"
    site.mkdir(mode=0o700)
    entry = site / "index.html"
    entry.write_text("<p>Site</p>")
    entry.chmod(0o600)
    metadata = site / ".git"
    metadata.mkdir(mode=0)
    try:
        response = publish_over_socket(workspace, previews)
        assert response["status"] == "succeeded"
        assert response["files"] == 1
    finally:
        metadata.chmod(0o700)
