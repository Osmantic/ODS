"""Run the container entrypoint with private /root and /opt mount namespaces."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

resource = pytest.importorskip("resource")
pytestmark = pytest.mark.skipif(sys.platform != "linux" or shutil.which("unshare") is None,
                               reason="Linux user/mount namespaces are required")
ENTRYPOINT = Path(__file__).resolve().parents[1] / "extensions/services/llama-server/lemonade-entrypoint.sh"


@pytest.fixture
def runtime(tmp_path):
    probe = subprocess.run(["unshare", "--user", "--map-root-user", "--mount", "true"],
                           capture_output=True, text=True, timeout=10)
    if probe.returncode:
        if os.environ.get("LEMONADE_REQUIRE_NAMESPACE") == "1":
            pytest.fail(f"Required namespace isolation is unavailable: {probe.stderr}")
        pytest.skip("The host does not permit unprivileged user/mount namespaces")
    root, opt = tmp_path / "root", tmp_path / "opt"
    config = root / ".cache/lemonade/config.json"
    config.parent.mkdir(parents=True)
    (opt / "lemonade").mkdir(parents=True)
    # sudo CI maps namespace root to host root, which cannot traverse a
    # runner-owned private home after dropping host-namespace capabilities.
    # Copy the exact entrypoint into the fixture before entering isolation.
    shutil.copyfile(ENTRYPOINT, opt / "lemonade-entrypoint.sh")
    server = opt / "lemonade/lemonade-server"
    server.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$LEMONADE_TEST_RECEIPT"\n', encoding="utf-8")
    server.chmod(0o755)
    receipt = tmp_path / "server-started"

    def start(context="131072", limit=False):
        env = dict(os.environ, LEMONADE_CTX_SIZE=context, LEMONADE_TEST_RECEIPT=str(receipt))
        def file_limit():
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))
        return subprocess.run([
            "unshare", "--user", "--map-root-user", "--mount", "--fork", "--propagation", "private",
            "sh", "-ec", 'mount --bind "$1" /root\nmount --bind "$2" /opt\nshift 2\nexec sh "$@"',
            "lemonade-fixture", str(root), str(opt), "/opt/lemonade-entrypoint.sh", "serve", "--port", "8080",
        ], env=env, capture_output=True, text=True, timeout=20, preexec_fn=file_limit if limit else None)

    return config, receipt, start


def test_sync_updates_context_and_keeps_other_settings_and_mode(runtime):
    config, receipt, start = runtime
    original = {"ctx_size": 65536, "models": {"saved-model": {"reasoning": True}}, "threads": 8}
    config.write_text(json.dumps(original), encoding="utf-8")
    config.chmod(0o640)
    result = start()
    assert result.returncode == 0, result.stderr
    assert json.loads(config.read_text()) == {**original, "ctx_size": 131072}
    assert config.stat().st_mode & 0o777 == 0o640
    assert receipt.read_text().splitlines() == ["serve", "--port", "8080"]


def test_failed_write_keeps_the_complete_previous_config_and_does_not_boot(runtime):
    config, receipt, start = runtime
    original = json.dumps({"ctx_size": 65536, "saved_setting": "x" * 10000}).encode()
    config.write_bytes(original)
    config.chmod(0o640)
    result = start(limit=True)
    assert result.returncode != 0
    assert "File too large" in result.stderr
    assert not receipt.exists(), "Lemonade must not boot after failed context synchronization"
    assert config.read_bytes() == original
    assert config.stat().st_mode & 0o777 == 0o640
    assert sorted(path.name for path in config.parent.iterdir()) == ["config.json"]


def test_matching_context_does_not_rewrite_the_cache(runtime):
    config, receipt, start = runtime
    original = b'{"ctx_size": 131072, "preserve_formatting": true}\n'
    config.write_bytes(original)
    before = config.stat()
    result = start()
    assert result.returncode == 0, result.stderr
    after = config.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert config.read_bytes() == original
    assert receipt.exists()


def test_first_start_leaves_initial_config_creation_to_lemonade(runtime):
    config, receipt, start = runtime
    result = start()
    assert result.returncode == 0, result.stderr
    assert not config.exists()
    assert receipt.exists()


def test_malformed_existing_config_is_retained_and_stops_startup(runtime):
    config, receipt, start = runtime
    original = b'{"ctx_size": '
    config.write_bytes(original)
    result = start()
    assert result.returncode != 0
    assert "JSONDecodeError" in result.stderr
    assert config.read_bytes() == original
    assert not receipt.exists()


def test_sync_preserves_a_link_to_the_operator_config(runtime):
    config, receipt, start = runtime
    target = config.with_name("operator.json")
    target.write_text('{"ctx_size":65536,"threads":4}', encoding="utf-8")
    config.symlink_to(target.name)
    result = start()
    assert result.returncode == 0, result.stderr
    assert config.is_symlink()
    assert json.loads(target.read_text()) == {"ctx_size": 131072, "threads": 4}
    assert receipt.exists()
