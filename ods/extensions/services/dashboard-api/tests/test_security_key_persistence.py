"""Importing security.py must not write a secret to disk.

The key generation used to run at import time, so anything that imported the
module — a test, a lint pass, a one-off script — created
/data/dashboard-api-key.txt as a side effect. Outside a container that path
resolves against the filesystem root, so it silently created a directory and
a 0600 secret file at the root of whatever drive it ran on.

The file itself is a real contract: the dashboard container's entrypoint reads
it to inject the key into nginx. So the write has to still happen when the
service starts — just not on import.
"""

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path


SECURITY_PY = Path(__file__).resolve().parent.parent / "security.py"


def _reimport_security(monkeypatch, key_path: Path, env_key: str | None):
    monkeypatch.setenv("DASHBOARD_API_KEY_PATH", str(key_path))
    if env_key is None:
        monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    else:
        monkeypatch.setenv("DASHBOARD_API_KEY", env_key)
    sys.modules.pop("security", None)
    return importlib.import_module("security")


class TestImportIsInert:

    def test_import_writes_nothing_when_key_is_absent(self, tmp_path, monkeypatch):
        key_path = tmp_path / "nested" / "dashboard-api-key.txt"
        security = _reimport_security(monkeypatch, key_path, env_key=None)

        assert security.DASHBOARD_API_KEY, "a key must still be available in memory"
        assert not key_path.exists(), "import must not write the key file"
        assert not key_path.parent.exists(), "import must not create the directory either"

    def test_import_writes_nothing_when_key_is_present(self, tmp_path, monkeypatch):
        key_path = tmp_path / "dashboard-api-key.txt"
        security = _reimport_security(monkeypatch, key_path, env_key="configured-key")

        assert security.DASHBOARD_API_KEY == "configured-key"
        assert not key_path.exists()

    def test_no_filesystem_write_runs_at_import(self):
        """Source guard: no write may execute at module level.

        The behavioural tests above can only observe the path they control,
        and the original code wrote to a hardcoded one — so they pass against
        it while the real write still happens. This walks the AST instead:
        anything not inside a function or class body runs on import, including
        statements nested in a module-level `if`, which is exactly where the
        original write lived.
        """
        tree = ast.parse(SECURITY_PY.read_text(encoding="utf-8"))
        offenders = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if child.func.attr in {"write_text", "write_bytes", "mkdir", "chmod"}:
                        offenders.append(f"{child.func.attr}() at line {child.lineno}")
        assert not offenders, (
            f"these run at import and touch the filesystem: {offenders}"
        )


class TestStartupPersistsTheKey:

    def test_generated_key_is_written_on_startup(self, tmp_path, monkeypatch):
        key_path = tmp_path / "nested" / "dashboard-api-key.txt"
        security = _reimport_security(monkeypatch, key_path, env_key=None)

        security.persist_generated_key()

        assert key_path.exists(), "the dashboard entrypoint reads this file"
        assert key_path.read_text() == security.DASHBOARD_API_KEY
        if os.name != "nt":
            assert oct(key_path.stat().st_mode)[-3:] == "600", "key file must stay 0600"

    def test_configured_key_is_not_written(self, tmp_path, monkeypatch):
        """An operator-supplied key is already persisted in .env."""
        key_path = tmp_path / "dashboard-api-key.txt"
        security = _reimport_security(monkeypatch, key_path, env_key="configured-key")

        security.persist_generated_key()

        assert not key_path.exists(), "a configured key must not be rewritten to disk"

    def test_persist_is_idempotent(self, tmp_path, monkeypatch):
        key_path = tmp_path / "dashboard-api-key.txt"
        security = _reimport_security(monkeypatch, key_path, env_key=None)

        security.persist_generated_key()
        first = key_path.read_text()
        security.persist_generated_key()

        assert key_path.read_text() == first, "repeat calls must not rotate the key"


class TestImportInASeparateProcess:
    """The regression in its original form: a bare import in a clean process."""

    def test_bare_import_creates_no_file(self, tmp_path):
        key_path = tmp_path / "root-marker" / "dashboard-api-key.txt"
        env = dict(os.environ)
        env.pop("DASHBOARD_API_KEY", None)
        env["DASHBOARD_API_KEY_PATH"] = str(key_path)
        env["PYTHONPATH"] = str(SECURITY_PY.parent)

        result = subprocess.run(
            [sys.executable, "-c", "import security; print(bool(security.DASHBOARD_API_KEY))"],
            env=env, capture_output=True, text=True, timeout=60,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True", "the module must still expose a key"
        assert not key_path.exists(), (
            "importing security.py in a fresh process must not create the key file"
        )
        assert not key_path.parent.exists(), "nor its parent directory"
