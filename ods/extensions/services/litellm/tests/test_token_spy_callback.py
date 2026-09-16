"""LiteLLM-to-Token-Spy callback contracts."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

CALLBACK_PATH = Path(__file__).resolve().parent.parent / "ods_token_spy_callback.py"


def load_callback(monkeypatch):
    class CustomLogger:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    litellm = types.ModuleType("litellm")
    integrations = types.ModuleType("litellm.integrations")
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = CustomLogger
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    monkeypatch.setitem(sys.modules, "litellm.integrations", integrations)
    monkeypatch.setitem(
        sys.modules, "litellm.integrations.custom_logger", custom_logger
    )
    spec = importlib.util.spec_from_file_location(
        f"ods_token_spy_callback_{uuid4().hex}", CALLBACK_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_callback_builds_metadata_only_event(monkeypatch):
    callback = load_callback(monkeypatch)
    start = datetime.now(timezone.utc)
    response = {
        "model": "Qwen3.5-2B-Q4_K_M.gguf",
        "choices": [{
            "message": {"content": "private answer"},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 18,
            "completion_tokens": 4,
        },
    }
    kwargs = {
        "model": "openai/default",
        "messages": [{"role": "user", "content": "private prompt"}],
        "optional_params": {
            "tools": [{"type": "function", "function": {"name": "private_tool"}}]
        },
        "litellm_params": {
            "api_base": "http://host.docker.internal:8080/api/v1",
            "custom_llm_provider": "openai",
        },
    }

    event = callback.build_event(
        kwargs, response, start, start + timedelta(milliseconds=250)
    )

    assert event["agent"] == "litellm"
    assert event["model"] == "Qwen3.5-2B-Q4_K_M.gguf"
    assert event["provider_name"] == "local"
    assert event["input_tokens"] == 18
    assert event["output_tokens"] == 4
    assert event["message_count"] == 1
    assert event["tool_count"] == 1
    assert event["duration_ms"] == 250
    serialized = json.dumps(event)
    assert "private prompt" not in serialized
    assert "private answer" not in serialized
    assert "private_tool" not in serialized


def test_callback_skips_litellm_event_when_switchboard_is_enabled(
    monkeypatch,
):
    monkeypatch.setenv("TOKEN_SPY_URL", "http://token-spy:8080")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "shared-secret")
    monkeypatch.setenv("ODS_MODEL_SWITCHBOARD", "enabled")
    callback = load_callback(monkeypatch)
    instance = callback.ODSTokenSpyCallback()

    asyncio.run(instance.async_log_success_event(
        {"model": "default"},
        {"model": "Concrete.gguf", "usage": {}},
        1.0,
        2.0,
    ))

    assert instance.queue.empty()
    assert instance.worker is None


def test_callback_bounds_provider_counters_to_ingest_contract(monkeypatch):
    callback = load_callback(monkeypatch)
    event = callback.build_event(
        {"messages": [{}] * 100_001},
        {
            "model": "model",
            "usage": {
                "prompt_tokens": 3_000_000_000,
                "completion_tokens": -1,
            },
        },
        1.0,
        2.0,
    )

    assert event["message_count"] == 100_000
    assert event["input_tokens"] == 2_000_000_000
    assert event["output_tokens"] == 0


def test_callback_enqueues_without_waiting_for_token_spy(monkeypatch):
    monkeypatch.setenv("TOKEN_SPY_URL", "http://token-spy:8080")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "shared-secret")
    monkeypatch.setenv("ODS_MODEL_SWITCHBOARD", "observe")
    callback = load_callback(monkeypatch)
    instance = callback.ODSTokenSpyCallback()

    async def scenario():
        wait_forever = asyncio.Event()

        async def idle_worker():
            await wait_forever.wait()

        instance._run = idle_worker
        await instance.async_log_success_event(
            {"model": "default", "messages": []},
            {
                "model": "Concrete.gguf",
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
            1.0,
            2.0,
        )
        assert instance.queue.qsize() == 1
        assert instance.worker is not None
        instance.worker.cancel()
        try:
            await instance.worker
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def test_callback_closes_http_client_during_repeated_cancellation(monkeypatch):
    monkeypatch.setenv("TOKEN_SPY_URL", "http://token-spy:8080")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "shared-secret")
    callback = load_callback(monkeypatch)
    closing = asyncio.Event()
    release_close = asyncio.Event()
    closed = asyncio.Event()

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def aclose(self):
            closing.set()
            await release_close.wait()
            closed.set()

    monkeypatch.setattr(callback.httpx, "AsyncClient", Client)
    instance = callback.ODSTokenSpyCallback()

    async def scenario():
        worker = asyncio.create_task(instance._run())
        await asyncio.sleep(0)
        worker.cancel()
        await closing.wait()
        worker.cancel()
        await asyncio.sleep(0)
        release_close.set()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        assert closed.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("responses", [False, True], ids=["chat", "responses"])
@pytest.mark.parametrize("cached,written,expected", [
    (0, 0, (20, 0, 0)),
    (8, 0, (12, 8, 0)),
    (20, 0, (0, 20, 0)),
    (8, 4, (8, 8, 4)),
    (30, 4, (0, 20, 0)),
])
def test_enqueued_cache_categories_partition_prompt_total(monkeypatch, tmp_path, responses, cached, written, expected):
    monkeypatch.setenv("TOKEN_SPY_URL", "http://token-spy:8080")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "test-key")
    monkeypatch.setenv("ODS_MODEL_SWITCHBOARD", "observe")
    callback = load_callback(monkeypatch)
    instance = callback.ODSTokenSpyCallback()
    usage = ({
        "input_tokens": 20, "output_tokens": 5,
        "input_tokens_details": {"cached_tokens": cached},
    } if responses else {
        "prompt_tokens": 20, "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": cached},
    })
    usage["cache_write_tokens"] = written

    async def scenario():
        async def idle():
            await asyncio.Event().wait()
        instance._run = idle
        try:
            await instance.async_log_success_event(
                {"model": "default"}, {"usage": usage}, 1.0, 2.0,
            )
            event = instance.queue.get_nowait()
            categories = tuple(event[key] for key in (
                "input_tokens", "cache_read_tokens", "cache_write_tokens"
            ))
            assert categories == expected
            assert sum(categories) + event["output_tokens"] == 25
            modules = {}
            for name in ("db", "routed_telemetry"):
                spec = importlib.util.spec_from_file_location(
                    f"cache_report_{name}_{uuid4().hex}",
                    CALLBACK_PATH.parent.parent / "token-spy" / f"{name}.py",
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                modules[name] = module
            db = modules["db"]
            routed = modules["routed_telemetry"]
            db.DB_PATH = str(tmp_path / "usage.db")
            try:
                db.init_db()
                db.log_usage(routed.routed_event_to_usage(routed.validate_routed_event(event)))
                today = datetime.now(timezone.utc).date().isoformat()
                report = db.query_report(today, today)
                assert report["summary"]["total_tokens"] == 25
            finally:
                db._get_conn().close()
        finally:
            if instance.worker:
                instance.worker.cancel()
                try:
                    await instance.worker
                except asyncio.CancelledError:
                    pass
    asyncio.run(scenario())
