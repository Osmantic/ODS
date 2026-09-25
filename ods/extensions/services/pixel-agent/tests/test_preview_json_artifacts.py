import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX owner-bound preview snapshots")

import importlib.util
import json
import os
import pathlib
import subprocess
import socket
import threading

import pytest

SPEC = importlib.util.spec_from_file_location(
    "preview_json_artifacts",
    pathlib.Path(__file__).parents[1] / "host" / "workspace_preview.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def site(tmp_path):
    workspace = tmp_path / "workspace"
    public = workspace / "project" / "public"
    public.mkdir(parents=True, mode=0o700)
    workspace.chmod(0o700)
    public.parent.chmod(0o700)
    previews = tmp_path / "previews"
    previews.mkdir(mode=0o700)
    (public / "index.html").write_text("<!doctype html><title>Export</title>")
    (public / "index.html").chmod(0o600)
    return workspace, public, previews


@pytest.mark.parametrize("content", [
    b'{"module.py": """print(1)\n"""}',  # Real failed source-export shape.
    b'{"source": "unescaped\nnewline"}',
    b'{"value": NaN}',
    b'{"value": Infinity}',
    b'{"module.py":"first","module.py":"different"}',
    b'{"text":"\xff"}',
    b'',
    pytest.param(b'[' * 10000 + b'0' + b']' * 10000, id='excessive-decoder-nesting'),
])
def test_invalid_json_never_publishes_or_rewrites_source(tmp_path, content):
    workspace, public, previews = site(tmp_path)
    artifact = public / "export.json"
    artifact.write_bytes(content)
    artifact.chmod(0o600)
    with pytest.raises(MODULE.JsonArtifactError, match="invalid preview JSON artifact") as caught:
        MODULE.publish_snapshot(workspace, previews, "project/public", os.getuid())
    assert caught.value.diagnostic["path"] == "export.json"
    response = MODULE._error_result("invalid_json_artifact", caught.value.diagnostic)
    assert set(response["artifactError"]) == {"path", "line", "column"}
    assert "content" not in json.dumps(response)
    assert artifact.read_bytes() == content
    assert list(previews.iterdir()) == []
    assert MODULE.PREVIEW_FAILURE_CODES["invalid preview JSON artifact"] == "invalid_json_artifact"


def test_json_syntax_location_is_precise_without_source_excerpt():
    with pytest.raises(MODULE.JsonArtifactError) as caught:
        MODULE._validate_json_artifact(b'{\n  "module.py": """source"""\n}', "nested/export.json")
    assert caught.value.diagnostic == {"path": "nested/export.json", "line": 2, "column": 18}


def test_control_socket_reports_only_relative_json_location(tmp_path):
    workspace, public, previews = site(tmp_path)
    (public / "export.json").write_bytes(b'{\n  "module.py": """PRIVATE_CONTENT"""\n}')
    (public / "export.json").chmod(0o600)
    client, server = socket.socketpair()
    thread = threading.Thread(target=MODULE._serve_connection, args=(server,), kwargs={
        "workspace": workspace, "previews": previews, "owner_uid": os.getuid(), "port": 9437,
    })
    thread.start()
    try:
        client.settimeout(5)
        client.sendall(b'{"schemaVersion":1,"action":"publish","relativeDirectory":"project/public"}\n')
        client.shutdown(socket.SHUT_WR)
        raw = client.makefile("rb").readline()
        result = json.loads(raw)
        assert result["status"] == "failed"
        assert result["errorCode"] == "invalid_json_artifact"
        assert result["artifactError"] == {"path": "export.json", "line": 2, "column": 18}
        assert b"PRIVATE_CONTENT" not in raw and str(tmp_path).encode() not in raw
    finally:
        thread.join(timeout=5)
        client.close()
        server.close()


def test_serialized_export_preserves_executed_source_copies_and_real_output(tmp_path):
    workspace, public, previews = site(tmp_path)
    source = public.parent / "calculate.py"
    source.write_text('"""Quoted source and unicode: café."""\nprint(sum(i*i for i in range(1, 101)))\n', encoding="utf-8")
    execution = subprocess.run([sys.executable, str(source)], capture_output=True, check=True)
    assert execution.stdout == b"338350\n"
    raw = source.read_bytes()
    exported = json.dumps({source.name: raw.decode("utf-8")}, ensure_ascii=False).encode("utf-8")
    (public / "export.json").write_bytes(exported)
    (public / "calculate.py.txt").write_bytes(raw)
    (public / "output.txt").write_bytes(execution.stdout + execution.stderr)
    for path in public.iterdir():
        path.chmod(0o600)
    receipt = MODULE.publish_snapshot(workspace, previews, "project/public", os.getuid())
    snapshot = previews / receipt["siteId"]
    assert (snapshot / "export.json").read_bytes() == exported
    assert json.loads((snapshot / "export.json").read_bytes())[source.name].encode("utf-8") == raw
    assert (snapshot / "calculate.py.txt").read_bytes() == raw
    assert (snapshot / "output.txt").read_bytes() == execution.stdout + execution.stderr


@pytest.mark.parametrize("content", [b'null', b'[]', b'42', b'"text"'])
def test_json_artifacts_do_not_impose_an_application_schema(tmp_path, content):
    workspace, public, previews = site(tmp_path)
    (public / "data.json").write_bytes(content)
    (public / "data.json").chmod(0o600)
    receipt = MODULE.publish_snapshot(workspace, previews, "project/public", os.getuid())
    assert (previews / receipt["siteId"] / "data.json").read_bytes() == content
