"""Hidden folders and files beside a static site are skipped, never published.

Regression for an RTX Spark run: Portal's agent kept its own Python lint
helpers in Playground/elf-of-doom/.harness/ next to a static canvas game. The
host refused the whole directory as unsafe_directory because the hidden name
failed the asset-name rule, the generic message named no cause, and the agent
tried rm -r on the folder until Pixel's recursive-deletion guard ended the turn.
"""
import sys
from unittest import SkipTest

if sys.platform == "win32":
    raise SkipTest("Preview publication uses POSIX ownership and Unix peer credentials")

import base64
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import threading

import pytest

HOST = Path(__file__).parents[1] / "host"
SPEC = importlib.util.spec_from_file_location("preview_hidden_entries", HOST / "workspace_preview.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
sys.path.insert(0, str(HOST))
import preview_inspection as inspection  # noqa: E402

SECRET = "HIDDEN_ENTRY_NEVER_PUBLISHED_5c0e"
GAME = ("<!doctype html><canvas id=game></canvas><script src=game.js></script>"
        "<script src=.harness/lint.py></script>")


def get_status(port, site_id, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", f"/{site_id}/{path}", headers={"Host": f"{site_id}.localhost:{port}"})
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def publish(workspace, previews, relative, fetch=()):
    """Publish over the control socket; GET each fetch path from the snapshot."""
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
            statuses = ({path: get_status(httpd.server_port, response["siteId"], path) for path in fetch}
                        if response["status"] == "succeeded" else {})
            return response, statuses
        finally:
            client.close()
            peer.close()
            httpd.shutdown()
            server.join(timeout=5)


def roots(tmp_path):
    workspace, previews = tmp_path / "workspace", tmp_path / "previews"
    workspace.mkdir(mode=0o700)
    previews.mkdir(mode=0o700)
    (workspace / "Playground").mkdir(mode=0o700)
    return workspace, previews


def write(path, text, mode=0o600):
    path.write_text(text)
    path.chmod(mode)


def visible_site(workspace, name):
    site = workspace / "Playground" / name
    site.mkdir(mode=0o700)
    write(site / "index.html", GAME)
    write(site / "game.js", "const ctx = document.getElementById('game').getContext('2d');")
    return site


def snapshot_names(previews, site_id):
    root = previews / site_id
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def test_agent_harness_folder_beside_a_static_game_is_skipped(tmp_path):
    workspace, previews = roots(tmp_path)
    site = visible_site(workspace, "elf-of-doom")
    outside = tmp_path / "outside-secret.txt"
    write(outside, SECRET)
    harness = site / ".harness"
    harness.mkdir()
    write(harness / "lint.py", f"print('{SECRET}')\n", 0o777)
    os.symlink(outside, harness / "secret-link.txt")
    os.symlink("/etc", harness / "etc")
    (harness / "has space").mkdir()
    write(harness / "has space" / "notes.exe", SECRET, 0o666)
    harness.chmod(0o777)
    cache = site / ".cache"
    cache.mkdir(mode=0o700)
    write(cache / "state.json", "not json")
    cache.chmod(0)  # an unreadable hidden directory proves it is pruned, not walked
    (site / "assets").mkdir(mode=0o700)
    (site / "assets" / ".vscode").mkdir()
    (site / "assets" / ".vscode").chmod(0o777)  # nested hidden folders are pruned too
    try:
        response, statuses = publish(workspace, previews, "Playground/elf-of-doom",
                                     fetch=("game.js", ".harness/lint.py", ".harness/secret-link.txt"))
    finally:
        cache.chmod(0o700)
    assert response["status"] == "succeeded", response
    assert response["files"] == 2
    assert response["publishedPaths"] == ["game.js", "index.html"]
    # index.html references .harness/lint.py: it gets a 404, never the bytes.
    assert statuses == {"game.js": 200, ".harness/lint.py": 404, ".harness/secret-link.txt": 404}
    site_id = response["siteId"]
    assert snapshot_names(previews, site_id) == ["game.js", "index.html"]
    manifest = json.loads(MODULE.snapshot_manifest(previews, site_id))
    assert [entry["path"] for entry in manifest["files"]] == ["game.js", "index.html"]
    assert SECRET not in json.dumps(manifest)
    request = {"schemaVersion": 1, "action": "inspect", "siteId": site_id, "sha256": response["sha256"],
               "viewport": {"width": 375, "height": 812},
               "steps": [{"action": "assert-visible", "locator": {"selector": "#game"}}]}
    bundle = inspection.snapshot_bundle(previews, request)
    assert [entry["path"] for entry in bundle["files"]] == ["game.js", "index.html"]
    assert all(SECRET.encode() not in base64.b64decode(entry["base64"]) for entry in bundle["files"])

    # The content-addressed identity is that of the same site without them.
    visible_site(workspace, "elf-of-doom-clean")
    clean, _ = publish(workspace, previews, "Playground/elf-of-doom-clean")
    assert (clean["siteId"], clean["sha256"]) == (site_id, response["sha256"])

    # Re-reading the live directory agrees: hidden edits never make it stale.
    current = {"schemaVersion": 1, "action": "verify-current", "relativeDirectory": "Playground/elf-of-doom",
               "siteId": site_id, "sha256": response["sha256"]}
    write(harness / "lint.py", "print('edited after publication')\n", 0o777)
    assert MODULE.verify_current_snapshot(workspace, previews, current, os.getuid())["status"] == "matched"
    write(site / "game.js", "const changed = true;")
    assert MODULE.verify_current_snapshot(workspace, previews, current, os.getuid())["status"] == "mismatched"


def test_hidden_files_are_skipped_without_being_read(tmp_path):
    workspace, previews = roots(tmp_path)
    site = workspace / "Playground" / "report"
    site.mkdir(mode=0o700)
    write(site / "index.html", "<!doctype html><h1>Report</h1>")
    write(site / ".env", f"API_KEY={SECRET}\n", 0o666)
    write(site / ".eslintrc", "{}", 0o644)
    (site / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1" + SECRET.encode())
    outside = tmp_path / "outside.txt"
    write(outside, SECRET)
    os.symlink(outside, site / ".link.txt")
    os.mkfifo(site / ".pipe")  # opening a FIFO would block: proves it is never read
    response, statuses = publish(workspace, previews, "Playground/report", fetch=(".env", ".DS_Store"))
    assert response["status"] == "succeeded", response
    assert response["publishedPaths"] == ["index.html"]
    assert statuses == {".env": 404, ".DS_Store": 404}
    assert snapshot_names(previews, response["siteId"]) == ["index.html"]
    assert SECRET not in (previews / response["siteId"] / "index.html").read_text()
    assert (site / ".env").read_text() == f"API_KEY={SECRET}\n"


def test_hidden_entries_never_count_toward_file_or_size_limits(tmp_path):
    workspace, previews = roots(tmp_path)
    site = workspace / "Playground" / "gallery"
    site.mkdir(mode=0o700)
    write(site / "index.html", "<!doctype html><h1>Gallery</h1>")
    for index in range(MODULE.MAX_FILES - 1):
        write(site / f"p{index:03}.txt", str(index))
    many = site / ".thumbnails"
    many.mkdir(mode=0o700)
    for index in range(MODULE.MAX_FILES + 50):
        write(many / f"t{index:03}.png", "x")
    for name, size in ((".huge.bin", MODULE.MAX_FILE_BYTES + 1), (".archive.zip", MODULE.MAX_TOTAL_BYTES + 1)):
        with open(site / name, "wb") as handle:
            handle.truncate(size)  # sparse: the size is reported, never read
    response, _ = publish(workspace, previews, "Playground/gallery")
    assert response["status"] == "succeeded", response
    assert response["files"] == MODULE.MAX_FILES
    write(site / "one-more.txt", "visible files still count")
    response, _ = publish(workspace, previews, "Playground/gallery")
    assert response["errorCode"] == "too_many_files"


@pytest.mark.parametrize("fault", ["space", "leading-dash", "reserved", "symlink", "group-writable"])
def test_visible_folders_keep_every_directory_rule(tmp_path, fault):
    workspace, previews = roots(tmp_path)
    site = visible_site(workspace, "elf-of-doom")
    (site / ".harness").mkdir()
    (site / ".harness").chmod(0o777)
    name ={"space": "my assets", "leading-dash": "-dist", "reserved": "__ods_assets"}.get(fault, "assets")
    folder = site / name
    if fault == "symlink":
        target = tmp_path / "linked-assets"
        target.mkdir(mode=0o700)
        os.symlink(target, folder)
    else:
        folder.mkdir(mode=0o700)
        write(folder / "sprite.png", "png")
        if fault == "group-writable":
            folder.chmod(0o770)
    response, _ = publish(workspace, previews, "Playground/elf-of-doom")
    assert response["status"] == "failed"
    assert response["errorCode"] == "unsafe_directory"
    assert list(previews.iterdir()) == []
    # The documented remedy: move the folder outside the site directory.
    os.rename(folder, workspace / "Playground" / "moved-out")
    response, _ = publish(workspace, previews, "Playground/elf-of-doom")
    assert response["status"] == "succeeded", response
    assert response["publishedPaths"] == ["game.js", "index.html"]
