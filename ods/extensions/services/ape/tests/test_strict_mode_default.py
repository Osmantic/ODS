"""Regression tests for issue #4170 — APE ships with STRICT_MODE enabled."""

import importlib
import sys

from fastapi.testclient import TestClient


def _reload_main(monkeypatch, *, strict_env: str | None = "__unset__"):
    if strict_env == "__unset__":
        monkeypatch.delenv("APE_STRICT_MODE", raising=False)
    else:
        monkeypatch.setenv("APE_STRICT_MODE", strict_env)
    if "main" in sys.modules:
        del sys.modules["main"]
    return importlib.import_module("main")


def test_strict_mode_defaults_true_when_env_unset(ape_env, monkeypatch):
    main = _reload_main(monkeypatch)
    assert main.STRICT_MODE is True


def test_health_reports_strict_mode_true_by_default(ape_env, monkeypatch):
    main = _reload_main(monkeypatch)
    main.load_state()
    client = TestClient(main.app)
    client.headers.update({"X-API-Key": ape_env.api_key})
    try:
        assert client.get("/health").json()["strict_mode"] is True
    finally:
        client.close()


def test_explicit_false_enables_advisory_mode(ape_env, monkeypatch):
    main = _reload_main(monkeypatch, strict_env="false")
    assert main.STRICT_MODE is False
