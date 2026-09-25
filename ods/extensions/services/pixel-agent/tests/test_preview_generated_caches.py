"""Running a project's own Python code must not make its site unpublishable.

Regression for the coding-v3 Tower2 failure: `python3 -m unittest` created
__pycache__/ beside index.html and the host rejected the whole directory as
unsafe_directory, which named no entry and sent the model renaming folders.
"""
import sys
from unittest import SkipTest

if sys.platform == "win32":
    raise SkipTest("Preview publication uses POSIX ownership and Unix peer credentials")

import importlib.util
import json
import os
from pathlib import Path
import socket
import threading

SPEC = importlib.util.spec_from_file_location(
    "preview_generated_caches", Path(__file__).parents[1] / "host/workspace_preview.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def publish(workspace, previews, relative):
    with MODULE.PreviewHTTPServer(("127.0.0.1", 0), previews) as httpd:
        server = threading.Thread(target=httpd.serve_forever, daemon=True)
        server.start()
        client, peer = socket.socketpair()
        client.settimeout(5)
        worker = threading.Thread(target=MODULE._serve_connection, args=(peer,), kwargs=dict(
            workspace=workspace, previews=previews, owner_uid=os.getuid(), port=httpd.server_port))
        worker.start()
        try:
            client.sendall((json.dumps({"schemaVersion": 1, "action": "publish",
                                        "relativeDirectory": relative}) + "\n").encode())
            with client.makefile("rb") as reader:
                response = json.loads(reader.readline())
            worker.join(timeout=5)
            assert not worker.is_alive()
            return response
        finally:
            client.close()
            peer.close()
            httpd.shutdown()
            server.join(timeout=5)


def roots(tmp_path):
    workspace, previews = tmp_path / "workspace", tmp_path / "previews"
    workspace.mkdir(mode=0o700)
    previews.mkdir(mode=0o700)
    return workspace, previews


def site_with_entry(workspace, name="site"):
    site = workspace / name
    site.mkdir(mode=0o755)
    (site / "index.html").write_text("<!doctype html><h1>Report</h1>")
    (site / "index.html").chmod(0o600)
    return site


def test_bytecode_cache_is_excluded_without_being_entered(tmp_path):
    workspace, previews = roots(tmp_path)
    site = site_with_entry(workspace)
    cache = site / "__pycache__"
    cache.mkdir(mode=0o755)
    (cache / "totals.cpython-311.pyc").write_bytes(b"\x00compiled")
    cache.chmod(0)  # an unreadable cache proves it is pruned, not walked
    try:
        response = publish(workspace, previews, "site")
    finally:
        cache.chmod(0o755)
    assert response["status"] == "succeeded", response
    assert response["publishedPaths"] == ["index.html"]


def test_python_project_root_reports_the_real_unsupported_type(tmp_path):
    workspace, previews = roots(tmp_path)
    project = site_with_entry(workspace, "performance-t2-coding-20260925-coding")
    (project / "report.py").write_text("print('{}')\n")
    (project / "__pycache__").mkdir(mode=0o755)
    (project / "__pycache__/totals.cpython-311.pyc").write_bytes(b"\x00")
    response = publish(workspace, previews, "performance-t2-coding-20260925-coding")
    assert response["status"] == "failed"
    assert response["errorCode"] == "unsupported_file_type"
    public = site_with_entry(workspace, "performance-t2-coding-20260925-coding/public")
    (public / "report.py.txt").write_text("print('{}')\n")
    published = publish(workspace, previews, "performance-t2-coding-20260925-coding/public")
    assert published["status"] == "succeeded", published
    assert published["publishedPaths"] == ["index.html", "report.py.txt"]


def test_cache_name_never_follows_a_symlink_or_admits_a_file(tmp_path):
    workspace, previews = roots(tmp_path)
    site = site_with_entry(workspace)
    os.symlink("/etc", site / "__pycache__")
    response = publish(workspace, previews, "site")
    assert response["status"] == "succeeded", response
    assert response["publishedPaths"] == ["index.html"]
    (site / "__pycache__").unlink()
    (site / "__pycache__").write_text("not a directory")
    assert publish(workspace, previews, "site")["errorCode"] == "unsafe_file"


def test_pytest_cache_is_excluded_without_being_entered(tmp_path):
    workspace, previews = roots(tmp_path)
    site = site_with_entry(workspace)
    cache = site / ".pytest_cache"
    (cache / "v" / "cache").mkdir(parents=True, mode=0o755)
    (cache / "v" / "cache" / "lastfailed").write_text("{}")
    cache.chmod(0)
    try:
        response = publish(workspace, previews, "site")
    finally:
        cache.chmod(0o755)
    assert response["status"] == "succeeded", response
    assert response["publishedPaths"] == ["index.html"]
