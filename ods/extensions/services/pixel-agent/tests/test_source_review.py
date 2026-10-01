import sys

if sys.platform == "win32":
    from unittest import SkipTest

    raise SkipTest("Source capture requires the POSIX owner service")
import hashlib
import http.client
import json
import os
import threading
import pytest
from test_workspace_preview import MODULE as m, UnixHTTPConnection


@pytest.fixture
def project(tmp_path):
    workspace, previews = tmp_path / "workspace", tmp_path / "previews"
    workspace.mkdir(mode=0o700)
    previews.mkdir(mode=0o700)
    for directory in ("demo", "demo/src", "demo/dist", "demo/dist/assets"):
        (workspace / directory).mkdir(mode=0o700)
    for name, text in {
        "src/main.jsx": "export default () => <h1>Finanças 日本語</h1>\r\n",
        "package.json": '{"scripts":{"build":"vite build"}}',
        "dist/index.html": '<script src="./assets/hash.js"></script>',
        "dist/assets/hash.js": 'console.log("compiled")',
    }.items():
        (workspace / "demo" / name).write_bytes(text.encode())
    return workspace, previews


def publish(project):
    return m.publish_snapshot(
        *project, "demo/dist", os.getuid(), source_directory="demo"
    )


def test_sources_stay_separate_from_built_preview_and_are_immutable(project):
    receipt = publish(project)
    source = receipt["source"]
    payload = m._review_source_bytes(
        project[1], receipt["siteId"], source["sourceId"], os.getuid()
    )
    assert hashlib.sha256(payload).hexdigest() == source["sha256"]
    value = json.loads(payload)
    assert [f["path"] for f in value["files"]] == ["package.json", "src/main.jsx"]
    assert value["files"][1]["text"].endswith("\r\n")
    assert "日本語" in value["files"][1]["text"]
    manifest = json.loads(m.snapshot_manifest(project[1], receipt["siteId"]))
    assert [f["path"] for f in manifest["files"]] == ["assets/hash.js", "index.html"]
    (project[0] / "demo/src/main.jsx").write_text("later edit")
    assert (
        m._review_source_bytes(
            project[1], receipt["siteId"], source["sourceId"], os.getuid()
        )
        == payload
    )
    next_receipt = publish(project)
    assert next_receipt["siteId"] == receipt["siteId"]
    assert next_receipt["source"]["sourceId"] != source["sourceId"]


def test_exclusions_never_enter_or_read_secret_and_dependency_trees(project):
    root = project[0] / "demo"
    for name in ("node_modules", ".git", "credentials"):
        (root / name).mkdir()
        (root / name / "secret.js").write_text("should never be read")
    (root / ".env").write_text("TOKEN=private")
    (root / "credentials.json").write_text('{"private":"value"}')
    (root / "src/unsafe.js").write_text('const api_key = "private";')
    receipt = publish(project)
    body = m._review_source_bytes(
        project[1], receipt["siteId"], receipt["source"]["sourceId"], os.getuid()
    )
    assert b"private" not in body and b"should never" not in body
    assert receipt["source"]["omitted"] == {
        "directories": 3,
        "files": 3,
        "sensitiveFiles": 1,
    }


@pytest.mark.parametrize(
    "fault",
    ["symlink", "hardlink", "fifo", "quota", "changed", "traversal", "unrelated"],
)
def test_capture_failures_do_not_claim_source_or_publish_partial_result(
    project, monkeypatch, fault
):
    root = project[0] / "demo"
    victim = root / "src/main.jsx"
    if fault == "symlink":
        (root / "src/leak.js").symlink_to("/etc/passwd")
    elif fault == "hardlink":
        os.link(victim, root / "src/alias.js")
    elif fault == "fifo":
        os.mkfifo(root / "src/pipe.js")
    elif fault == "quota":
        victim.write_bytes(b"x" * (m.SOURCE_FILE_BYTES + 1))
    elif fault == "changed":
        original = os.scandir
        calls = 0

        def mutate(fd):
            nonlocal calls
            calls += 1
            if calls == 3:
                victim.write_text("changed after capture")
            return original(fd)

        monkeypatch.setattr(os, "scandir", mutate)
    with pytest.raises((m.PreviewError, OSError)):
        m.publish_snapshot(
            *project,
            "demo/dist",
            os.getuid(),
            source_directory="../demo"
            if fault == "traversal"
            else "other"
            if fault == "unrelated"
            else "demo",
        )
    assert not list(project[1].glob("site-*"))


def test_corrupt_or_substituted_sidecar_fails_verification(project):
    receipt = publish(project)
    source = receipt["source"]
    path = (
        project[1]
        / ".review-sources"
        / f"{receipt['siteId']}-{source['sourceId']}.json"
    )
    path.chmod(0o600)
    path.write_text("{}")
    path.chmod(0o400)
    with pytest.raises(m.PreviewError):
        m._review_source_bytes(
            project[1], receipt["siteId"], source["sourceId"], os.getuid()
        )


def test_source_route_requires_private_unix_relay_not_public_preview(project, tmp_path):
    receipt = publish(project)
    tail = f"/{receipt['siteId']}/__ods_source__/{receipt['source']['sourceId']}.json"
    with m.PreviewHTTPServer(("127.0.0.1", 0), project[1]) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request(
                "GET",
                tail,
                headers={"Host": f"{receipt['siteId']}.localhost:{server.server_port}"},
            )
            response = connection.getresponse()
            response.read()
            assert response.status == 404
            connection.close()
        finally:
            server.shutdown()
            thread.join()
    # The handler's internal flag is set only by the Unix relay server.
    with m.PreviewUnixHTTPServer(
        str(tmp_path / "http.sock"), project[1], 9437
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = UnixHTTPConnection(str(tmp_path / "http.sock"))
            connection.request("GET", tail, headers={"Host": "pixel-preview.internal"})
            response = connection.getresponse()
            body = response.read()
            assert (
                response.status == 200
                and hashlib.sha256(body).hexdigest() == receipt["source"]["sha256"]
            )
            connection.close()
        finally:
            server.shutdown()
            thread.join()


def test_request_source_root_is_optional_and_closed():
    base = {"schemaVersion": 1, "action": "publish", "relativeDirectory": "demo/dist"}
    assert m.parse_request(json.dumps(base).encode()) == base
    assert (
        m.parse_request(json.dumps({**base, "sourceDirectory": "demo"}).encode())[
            "sourceDirectory"
        ]
        == "demo"
    )
    for directory in ("other", "demo/..", "/demo", "demo/dist/sub", "demoSibling"):
        with pytest.raises(m.PreviewError):
            m.parse_request(json.dumps({**base, "sourceDirectory": directory}).encode())


def test_total_byte_and_file_quotas_fail_instead_of_silently_truncating(project):
    root = project[0] / "demo/src"
    for index in range(5):
        (root / f"large{index}.txt").write_bytes(b"x" * (256 * 1024))
    with pytest.raises(m.PreviewError, match="limit"):
        publish(project)
    for path in root.glob("large*.txt"):
        path.unlink()
    for index in range(129):
        (root / f"file{index}.js").write_text("x")
    with pytest.raises(m.PreviewError, match="limit"):
        publish(project)
    assert not list(project[1].glob("site-*"))


def test_symlink_swap_between_stat_and_open_never_reads_target(project, monkeypatch):
    victim = project[0] / "demo/src/main.jsx"
    original = os.open

    def swap(path, *args, **kwargs):
        if path == "main.jsx" and kwargs.get("dir_fd") is not None:
            victim.unlink()
            victim.symlink_to("/etc/passwd")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap)
    with pytest.raises((OSError, m.PreviewError)):
        publish(project)


def test_store_quota_retains_previous_revisions_and_idempotent_receipt(project, monkeypatch):
    monkeypatch.setattr(m, "SOURCE_STORE_FILES", 2)
    first = publish(project)
    (project[0] / "demo/src/main.jsx").write_text("second source")
    second = publish(project)
    assert first["siteId"] == second["siteId"]
    assert publish(project) == second
    (project[0] / "demo/src/main.jsx").write_text("third source")
    with pytest.raises(m.PreviewError, match="store quota"):
        publish(project)
    store = project[1] / ".review-sources"
    assert len(list(store.glob("*.json"))) == 2
    assert not list(store.glob(".capture-*"))
    for receipt in (first, second):
        assert m._review_source_bytes(project[1], receipt["siteId"], receipt["source"]["sourceId"], os.getuid())


def test_store_byte_quota_and_unknown_files_fail_without_deleting(project, monkeypatch):
    monkeypatch.setattr(m, "SOURCE_STORE_BYTES", 1)
    with pytest.raises(m.PreviewError, match="store quota"):
        publish(project)
    store = project[1] / ".review-sources"
    assert sorted(p.name for p in store.iterdir()) == [".quota.lock"]
    monkeypatch.setattr(m, "SOURCE_STORE_BYTES", 64 * 1024 * 1024)
    unknown = store / "owner-data"
    unknown.write_text("preserve")
    with pytest.raises(m.PreviewError, match="unsafe source review store"):
        publish(project)
    assert unknown.read_text() == "preserve"


def test_concurrent_source_publications_reserve_aggregate_quota(project, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(m, "SOURCE_STORE_FILES", 1)
    captured = m._capture_review_source(project[0], "demo", os.getuid(), "demo/dist")
    site = "site-" + "a" * 24
    def attempt(number):
        different = dict(captured, bytes=captured["bytes"] + number)
        try:
            return m._publish_review_source(project[1], site, different, os.getuid())
        except m.PreviewError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(result == "source review store quota exceeded" for result in results) == 3
    assert len(list((project[1] / ".review-sources").glob("*.json"))) == 1
    assert not list((project[1] / ".review-sources").glob(".capture-*"))


def test_full_source_store_refuses_before_new_output_snapshot(project, monkeypatch):
    monkeypatch.setattr(m, "SOURCE_STORE_FILES", 1)
    original = publish(project)
    before = set(p.name for p in project[1].iterdir())
    (project[0] / "demo/src/main.jsx").write_text("new source revision")
    (project[0] / "demo/dist/index.html").write_text("<h1>Changed build</h1>")
    with pytest.raises(m.PreviewError, match="store quota"):
        publish(project)
    assert set(p.name for p in project[1].iterdir()) == before
    assert m._review_source_bytes(project[1], original["siteId"], original["source"]["sourceId"], os.getuid())
    assert not list((project[1] / ".review-sources").glob(".capture-*"))


def test_output_failure_releases_reserved_capture_without_removing_existing(project, monkeypatch):
    original = publish(project)
    store = project[1] / ".review-sources"
    before = set(p.name for p in store.iterdir())
    (project[0] / "demo/src/main.jsx").write_text("changed source")
    def fail(*args):
        raise m.PreviewError("output failed")
    with monkeypatch.context() as patch:
        patch.setattr(m, "_publish_captured_snapshot", fail)
        with pytest.raises(m.PreviewError, match="output failed"):
            publish(project)
    assert set(p.name for p in store.iterdir()) == before
    assert m._review_source_bytes(project[1], original["siteId"], original["source"]["sourceId"], os.getuid())
    assert publish(project)["source"]["sourceId"] != original["source"]["sourceId"]


def test_full_source_store_reports_fixed_code_over_real_control_socket(project, monkeypatch):
    import socket
    monkeypatch.setattr(m, "SOURCE_STORE_FILES", 0)
    client, server = socket.socketpair()
    thread = threading.Thread(target=m._serve_connection, args=(server,), kwargs={
        "workspace": project[0], "previews": project[1], "owner_uid": os.getuid(), "port": 9437})
    thread.start()
    try:
        client.settimeout(5)
        client.sendall(b'{"schemaVersion":1,"action":"publish","relativeDirectory":"demo/dist","sourceDirectory":"demo"}\n')
        client.shutdown(socket.SHUT_WR)
        raw = client.makefile("rb").readline()
        result = json.loads(raw)
        assert result["errorCode"] == "source_store_full"
        assert result["status"] == "failed"
        assert str(project[0]).encode() not in raw
        assert not list(project[1].glob("site-*"))
    finally:
        thread.join(timeout=5)
        client.close()
        server.close()
