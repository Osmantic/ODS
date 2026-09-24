"""APE must not log any part of an auto-generated API key (#4193).

main.py used to emit `auto-generated key: {API_KEY[:16]}...` at import time —
16 of the 64 hex characters of a live credential, into container logs that get
swept into support bundles.
"""

import importlib
import logging
import re
import sys
from pathlib import Path

import pytest

APE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APE_DIR))


@pytest.fixture()
def ape_startup(tmp_path, monkeypatch, caplog):
    """Import main with APE_API_KEY unset, capturing import-time log records."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.delenv("APE_API_KEY", raising=False)
    monkeypatch.setenv("APE_AUDIT_LOG", str(data_dir / "audit.jsonl"))
    monkeypatch.setenv("APE_STATE_FILE", str(data_dir / "state.json"))
    monkeypatch.setenv("APE_STRICT_MODE", "false")
    monkeypatch.setenv("APE_WARMUP_SECONDS", "0")

    sys.modules.pop("main", None)
    with caplog.at_level(logging.WARNING):
        mod = importlib.import_module("main")
        mod = importlib.reload(mod)
    return mod, caplog.text


def test_generated_key_never_appears_in_logs(ape_startup):
    mod, log_text = ape_startup
    assert mod.API_KEY, "a key should have been generated"
    assert mod.API_KEY not in log_text, "the full key was logged"
    # The real defect: a *prefix* of the key. Any run of 12+ hex characters
    # from the key appearing in the logs is a leak.
    for start in range(0, len(mod.API_KEY) - 12):
        chunk = mod.API_KEY[start:start + 12]
        assert chunk not in log_text, f"key material leaked into logs: {chunk!r}..."


def test_operator_is_still_told_the_key_is_ephemeral(ape_startup):
    """Removing the key must not remove the warning that prompted it."""
    _, log_text = ape_startup
    assert "APE_API_KEY not set" in log_text
    assert "APE_API_KEY" in log_text, "the operator needs the env var name"
    assert re.search(r"ephemeral|changes on every restart", log_text), (
        "the operator should be told the generated key is not stable"
    )
