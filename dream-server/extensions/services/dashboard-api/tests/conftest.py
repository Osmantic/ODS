"""Shared fixtures for dashboard-api unit tests."""

import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add dashboard-api source to path so we can import modules directly.
DASHBOARD_API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASHBOARD_API_DIR))

# Set env vars BEFORE any app imports so config.py and security.py initialise
# correctly (they read env at module level).
_TEST_API_KEY = "test-key-12345"
_TEST_SHIELD_KEY = "test-shield-key-fixture"
os.environ.setdefault("DASHBOARD_API_KEY", _TEST_API_KEY)
os.environ.setdefault("SHIELD_API_KEY", _TEST_SHIELD_KEY)
os.environ.setdefault("DREAM_INSTALL_DIR", "/tmp/dream-test-install")
os.environ.setdefault("DREAM_DATA_DIR", "/tmp/dream-test-data")
os.environ.setdefault("DREAM_EXTENSIONS_DIR", "/tmp/dream-test-extensions")
os.environ.setdefault("GPU_BACKEND", "nvidia")

if "fcntl" not in sys.modules:
    try:
        import fcntl  # type: ignore # noqa: F401
    except ModuleNotFoundError:
        sys.modules["fcntl"] = types.SimpleNamespace(
            LOCK_EX=0,
            LOCK_UN=0,
            flock=lambda *args, **kwargs: None,
        )

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def install_dir(tmp_path, monkeypatch):
    """Provide an isolated install directory with a .env file."""
    d = tmp_path / "dream-server"
    d.mkdir()
    monkeypatch.setattr("helpers.INSTALL_DIR", str(d))
    return d


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Provide an isolated data directory for bootstrap/token files."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr("helpers.DATA_DIR", str(d))
    monkeypatch.setattr("helpers._TOKEN_FILE", d / "token_counter.json")
    monkeypatch.setattr("helpers._PERF_FILE", d / "model_performance.json")
    return d


@pytest.fixture()
def setup_config_dir(tmp_path, monkeypatch):
    """Provide an isolated config directory for setup/persona files."""
    d = tmp_path / "config"
    d.mkdir()
    import config
    monkeypatch.setattr(config, "SETUP_CONFIG_DIR", d)
    # Also patch the setup router which imports SETUP_CONFIG_DIR at the top
    import routers.setup as setup_router
    monkeypatch.setattr(setup_router, "SETUP_CONFIG_DIR", d)
    return d


@pytest.fixture()
def test_client(monkeypatch):
    """Return a FastAPI TestClient pre-configured with Bearer auth."""
    import security
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", _TEST_API_KEY)

    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app, raise_server_exceptions=True)
    client.auth_headers = {"Authorization": f"Bearer {_TEST_API_KEY}"}
    return client


def load_golden_fixture(name: str):
    """Load a JSON or text fixture from tests/fixtures/.

    Returns parsed JSON for .json files, raw text for anything else.
    """
    path = FIXTURES_DIR / name
    text = path.read_text()
    if path.suffix == ".json":
        return json.loads(text)
    return text


@pytest.fixture()
def mock_aiohttp_session():
    """Return a factory that creates a mock aiohttp.ClientSession.

    Usage::

        session = mock_aiohttp_session(status=200, json_data={"ok": True})
        monkeypatch.setattr("helpers._get_aio_session", AsyncMock(return_value=session))
    """

    def _factory(status: int = 200, json_data=None, text_data: str = "",
                 raise_on_get=None):
        response = AsyncMock()
        response.status = status
        response.json = AsyncMock(return_value=json_data or {})
        response.text = AsyncMock(return_value=text_data)

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=response)
        ctx.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        if raise_on_get:
            session.get = MagicMock(side_effect=raise_on_get)
        else:
            session.get = MagicMock(return_value=ctx)
        return session

    return _factory
