"""Shared fixtures for the APE service unit tests.

Mirrors the dashboard-api test convention: env vars are set BEFORE the app
module is imported (main.py reads config at import time), the service source
is put on sys.path, and each test gets an isolated temp data/config dir so the
persistent governance state never leaks between tests.
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

APE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APE_DIR))

_TEST_API_KEY = "test-ape-key-12345"


@pytest.fixture()
def ape_env(tmp_path, monkeypatch):
    """Isolated data/config dirs + a pinned API key. Returns a small ns."""
    data_dir = tmp_path / "data" / "ape"
    data_dir.mkdir(parents=True)
    cfg_dir = tmp_path / "config" / "ape"
    cfg_dir.mkdir(parents=True)

    monkeypatch.setenv("APE_API_KEY", _TEST_API_KEY)
    monkeypatch.setenv("APE_AUDIT_LOG", str(data_dir / "audit.jsonl"))
    monkeypatch.setenv("APE_STATE_FILE", str(data_dir / "state.json"))
    monkeypatch.setenv("APE_POLICY_FILE", str(cfg_dir / "policy.yaml"))
    monkeypatch.setenv("APE_STRICT_MODE", "false")
    monkeypatch.setenv("APE_WARMUP_SECONDS", "0")
    monkeypatch.setenv("APE_RATE_LIMIT_RPM", "10000")  # don't trip legacy limiter

    class _NS:
        pass

    ns = _NS()
    ns.data_dir = data_dir
    ns.cfg_dir = cfg_dir
    ns.state_file = data_dir / "state.json"
    ns.policy_file = cfg_dir / "policy.yaml"
    ns.audit_log = data_dir / "audit.jsonl"
    ns.api_key = _TEST_API_KEY
    return ns


def _fresh_app(monkeypatch_env: dict | None = None):
    """Re-import main.py so module-level config picks up the current env."""
    for mod in ("main",):
        if mod in sys.modules:
            del sys.modules[mod]
    main = importlib.import_module("main")
    return main


@pytest.fixture()
def make_client(ape_env):
    """Factory: returns (client, main_module) with state loaded.

    Optionally accepts extra env overrides applied before the (re)import.
    """
    from fastapi.testclient import TestClient

    created = []

    def _factory(env: dict | None = None, policy_yaml: str | None = None):
        if env:
            for k, v in env.items():
                os.environ[k] = str(v)
        if policy_yaml is not None:
            ape_env.policy_file.write_text(policy_yaml)
        main = _fresh_app()
        main.load_state()
        client = TestClient(main.app)
        client.headers.update({"X-API-Key": ape_env.api_key})
        created.append(client)
        return client, main

    yield _factory

    for c in created:
        c.close()
