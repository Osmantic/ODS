"""Contracts for metadata-only usage events emitted by the model router."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent


def _load(path: Path, prefix: str):
    spec = importlib.util.spec_from_file_location(
        f"{prefix}_{uuid4().hex}", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(**overrides):
    event = {
        "agent": "model-router",
        "model": "extra.Qwen3.5-2B-Q4_K_M.gguf",
        "provider_name": "lemonade",
        "path": "/v1/chat/completions",
        "request_body_bytes": 128,
        "message_count": 2,
        "user_message_count": 1,
        "assistant_message_count": 1,
        "tool_count": 0,
        "input_tokens": 20,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "duration_ms": 250,
        "stop_reason": "stop",
    }
    event.update(overrides)
    return event


def test_routed_event_rejects_content_and_credentials():
    telemetry = _load(
        TOKEN_SPY_DIR / "routed_telemetry.py", "routed_telemetry"
    )

    for forbidden in ("messages", "prompt", "content", "authorization"):
        with pytest.raises(
            telemetry.TelemetryValidationError,
            match="unknown fields",
        ):
            telemetry.validate_routed_event(
                _event(**{forbidden: "must not be stored"})
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("input_tokens", -1),
        ("duration_ms", 86_400_001),
        ("message_count", True),
        ("model", ""),
        ("path", "/not-routed"),
    ],
)
def test_routed_event_rejects_invalid_values(field, value):
    telemetry = _load(
        TOKEN_SPY_DIR / "routed_telemetry.py", "routed_telemetry"
    )

    with pytest.raises(telemetry.TelemetryValidationError):
        telemetry.validate_routed_event(_event(**{field: value}))


def test_routed_event_appears_in_usage_report(tmp_path, monkeypatch):
    telemetry = _load(
        TOKEN_SPY_DIR / "routed_telemetry.py", "routed_telemetry"
    )
    db = _load(TOKEN_SPY_DIR / "db.py", "token_spy_db")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "usage.db"))
    db._local.conn = None
    db.init_db()

    normalized = telemetry.validate_routed_event(_event())
    db.log_usage(telemetry.routed_event_to_usage(normalized))
    today = datetime.now(timezone.utc).date().isoformat()
    report = db.query_report(today, today)

    assert report["summary"]["requests"] == 1
    assert report["summary"]["input_tokens"] == 20
    assert report["summary"]["output_tokens"] == 5
    assert report["summary"]["total_tokens"] == 25
    assert report["summary"]["local_providers"] == 1
    assert report["models"] == [{
        "model": "Qwen3.5-2B-Q4_K_M.gguf",
        "provider": "local",
        "service": "model-router",
        "cost_source": "local_zero_cost",
        "requests": 1,
        "input_tokens": 20,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": 0.0,
    }]


def test_remote_routed_event_records_priced_usage_in_report(tmp_path, monkeypatch):
    telemetry = _load(
        TOKEN_SPY_DIR / "routed_telemetry.py", "routed_telemetry"
    )
    db = _load(TOKEN_SPY_DIR / "db.py", "token_spy_db")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "usage.db"))
    db._local.conn = None
    db.init_db()
    estimator_calls = []

    def estimate(
        model,
        input_tokens,
        output_tokens,
        cache_read,
        cache_write,
        provider,
    ):
        estimator_calls.append(
            (model, input_tokens, output_tokens, cache_read, cache_write, provider)
        )
        return 0.003125

    normalized = telemetry.validate_routed_event(
        _event(
            provider_name="OpenAI",
            model="gpt-4o-mini",
            input_tokens=10_000,
            output_tokens=2_000,
            cache_read_tokens=500,
        )
    )
    db.log_usage(
        telemetry.routed_event_to_usage(normalized, cost_estimator=estimate)
    )
    today = datetime.now(timezone.utc).date().isoformat()
    report = db.query_report(today, today)

    assert estimator_calls == [
        ("gpt-4o-mini", 10_000, 2_000, 500, 0, "openai")
    ]
    assert report["summary"]["paid_cost_usd"] == 0.003125
    assert report["summary"]["billing_providers"] == 1
    assert report["models"][0]["cost_source"] == "priced_from_tokens"
    assert report["models"][0]["cost_usd"] == 0.003125


def test_unknown_remote_routed_model_remains_untracked():
    telemetry = _load(
        TOKEN_SPY_DIR / "routed_telemetry.py", "routed_telemetry"
    )

    usage = telemetry.routed_event_to_usage(
        telemetry.validate_routed_event(
            _event(provider_name="custom-cloud", model="unpriced-model")
        ),
        cost_estimator=lambda *_args: 0.0,
    )

    assert usage["estimated_cost_usd"] == 0
    assert usage["cost_source"] == "untracked"


def test_local_lemonade_namespace_maps_to_one_physical_model():
    telemetry = _load(
        TOKEN_SPY_DIR / "routed_telemetry.py", "routed_telemetry"
    )
    plain = telemetry.routed_event_to_usage(
        telemetry.validate_routed_event(
            _event(model="Qwen3.5-2B-Q4_K_M.gguf")
        )
    )
    namespaced = telemetry.routed_event_to_usage(
        telemetry.validate_routed_event(
            _event(model="extra.Qwen3.5-2B-Q4_K_M.gguf")
        )
    )

    assert plain["model"] == namespaced["model"]


def test_hipfire_backend_is_accounted_as_local_runtime():
    telemetry = _load(
        TOKEN_SPY_DIR / "routed_telemetry.py", "routed_telemetry"
    )

    usage = telemetry.routed_event_to_usage(
        telemetry.validate_routed_event(
            _event(provider_name="hipfire", model="Qwen3.5-2B")
        )
    )

    assert usage["provider_name"] == "local"
    assert usage["cost_source"] == "local_zero_cost"
