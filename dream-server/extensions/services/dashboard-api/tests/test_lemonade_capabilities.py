import pytest
from pydantic import ValidationError

from lemonade_capabilities import (
    DEFAULT_EXTERNAL_LEMONADE_ACTIVE_PROBE_TIMEOUT,
    DEFAULT_EXTERNAL_LEMONADE_PROBE_TTL,
    _chat_completion_ready,
    _url_points_to_provider,
    external_lemonade_active_probe_timeout,
    external_lemonade_probe_cache_key,
    external_lemonade_probe_ttl,
    provider_capability_summary,
)
from models import ProviderCapabilityStatus


def _env(values):
    return lambda name: values.get(name, "")


def test_provider_capability_summary_distinguishes_ready_degraded_and_blocked():
    ready = [
        {"name": "chat", "status": "ok", "required": True},
        {"name": "stats", "status": "unsupported", "required": False},
    ]
    degraded = [
        {"name": "chat", "status": "ok", "required": True},
        {"name": "stats", "status": "failed", "required": False},
    ]
    blocked = [
        {"name": "chat", "status": "failed", "required": True},
        {"name": "stats", "status": "ok", "required": False},
    ]
    unverified = [
        {"name": "chat", "status": "ok", "required": True},
        {"name": "gateway_chat", "status": "unverified", "required": True},
    ]

    assert provider_capability_summary(ready) == (True, "ready")
    assert provider_capability_summary(degraded) == (True, "degraded")
    assert provider_capability_summary(blocked) == (False, "blocked")
    assert provider_capability_summary(unverified) == (None, "unverified")
    assert provider_capability_summary([]) == (False, "blocked")


def test_external_lemonade_probe_ttl_rejects_invalid_values():
    assert external_lemonade_probe_ttl(_env({})) == DEFAULT_EXTERNAL_LEMONADE_PROBE_TTL
    assert external_lemonade_probe_ttl(_env({"DASHBOARD_LEMONADE_PROBE_TTL": "-1"})) == (
        DEFAULT_EXTERNAL_LEMONADE_PROBE_TTL
    )
    assert external_lemonade_probe_ttl(_env({"DASHBOARD_LEMONADE_PROBE_TTL": "inf"})) == (
        DEFAULT_EXTERNAL_LEMONADE_PROBE_TTL
    )
    assert external_lemonade_probe_ttl(_env({"DASHBOARD_LEMONADE_PROBE_TTL": "30"})) == 30


def test_external_lemonade_active_probe_timeout_rejects_invalid_values():
    assert external_lemonade_active_probe_timeout(_env({})) == DEFAULT_EXTERNAL_LEMONADE_ACTIVE_PROBE_TIMEOUT
    assert external_lemonade_active_probe_timeout(_env({"DASHBOARD_LEMONADE_ACTIVE_PROBE_TIMEOUT": "0"})) == (
        DEFAULT_EXTERNAL_LEMONADE_ACTIVE_PROBE_TIMEOUT
    )
    assert external_lemonade_active_probe_timeout(_env({"DASHBOARD_LEMONADE_ACTIVE_PROBE_TIMEOUT": "nan"})) == (
        DEFAULT_EXTERNAL_LEMONADE_ACTIVE_PROBE_TIMEOUT
    )
    assert external_lemonade_active_probe_timeout(_env({"DASHBOARD_LEMONADE_ACTIVE_PROBE_TIMEOUT": "45"})) == 45


def test_external_lemonade_probe_cache_key_tracks_provider_profile():
    first = external_lemonade_probe_cache_key(
        "http://lemonade:13305/api/v1",
        "/api/v1",
        _env({"LEMONADE_MODEL": "model-a", "ENABLE_RAG": "false"}),
    )
    second = external_lemonade_probe_cache_key(
        "http://lemonade:13305/api/v1",
        "/api/v1",
        _env({"LEMONADE_MODEL": "model-a", "ENABLE_RAG": "true"}),
    )

    assert first != second


def test_chat_completion_ready_requires_non_empty_assistant_content():
    assert _chat_completion_ready({"choices": [{"message": {"content": "ok"}}]}) is True
    assert _chat_completion_ready({"choices": [{"message": {"content": "  "}}]}) is False
    assert _chat_completion_ready({"choices": [{"message": {}}]}) is False
    assert _chat_completion_ready({"choices": []}) is False


def test_chat_completion_ready_accepts_text_blocks_and_legacy_text():
    assert _chat_completion_ready({"choices": [{"message": {"content": [{"type": "text", "text": "ok"}]}}]}) is True
    assert _chat_completion_ready({"choices": [{"text": "ok"}]}) is True


def test_url_points_to_provider_accepts_full_endpoint_urls():
    api_base = "http://lemonade:13305/api/v1"

    assert _url_points_to_provider("http://lemonade:13305", api_base, "/api/v1") is True
    assert _url_points_to_provider("http://lemonade:13305/api/v1", api_base, "/api/v1") is True
    assert _url_points_to_provider("http://lemonade:13305/api/v1/embeddings", api_base, "/api/v1") is True
    assert _url_points_to_provider("http://lemonade:13305/v1/audio/speech", api_base, "/api/v1") is True


def test_url_points_to_provider_rejects_other_services():
    api_base = "http://lemonade:13305/api/v1"

    assert _url_points_to_provider("http://lemonade:13306/api/v1/embeddings", api_base, "/api/v1") is False
    assert _url_points_to_provider("http://embeddings:8000/api/v1/embeddings", api_base, "/api/v1") is False
    assert _url_points_to_provider("http://lemonade:13305/embeddings-service", api_base, "/api/v1") is False


def test_provider_capability_status_rejects_unknown_status():
    with pytest.raises(ValidationError):
        ProviderCapabilityStatus(name="chat", status="suported")
