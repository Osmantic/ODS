import httpx
import pytest

from lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    LemonadeSettings,
    classify_status,
    normalize_base_url,
)


def test_normalize_base_url_strips_api_suffixes():
    assert normalize_base_url("http://localhost:13305/api/v1") == "http://localhost:13305"
    assert normalize_base_url("http://engine:8080/v1") == "http://engine:8080"
    assert normalize_base_url("http://engine:8080") == "http://engine:8080"


def test_settings_from_env_prefers_container_base_url_and_key():
    env = {
        "LEMONADE_CONTAINER_BASE_URL": "http://host.docker.internal:13305/api/v1",
        "LEMONADE_API_BASE_PATH": "/api/v1",
        "LEMONADE_API_KEY": "secret",
    }

    settings = LemonadeSettings.from_env(env)

    assert settings.base_url == "http://host.docker.internal:13305"
    assert settings.api_root == "http://host.docker.internal:13305/api/v1"
    assert settings.api_key == "secret"


def test_classify_status():
    assert classify_status(401) == "auth_rejected"
    assert classify_status(404) == "not_found"
    assert classify_status(504) == "timeout"
    assert classify_status(500) == "provider_error"
    assert classify_status(400) == "request_rejected"


@pytest.mark.asyncio
async def test_health_sends_bearer_header():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "ok", "version": "10.7.0"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = LemonadeClient(
        LemonadeSettings(base_url="http://lemonade:13305", api_key="secret"),
        client=client,
    )

    payload = await adapter.health()

    assert payload["status"] == "ok"
    assert seen["url"] == "http://lemonade:13305/api/v1/health"
    assert seen["authorization"] == "Bearer secret"
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_completion_posts_openai_shape():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = LemonadeClient(
        LemonadeSettings(base_url="http://lemonade:13305/api/v1"),
        client=client,
    )

    payload = await adapter.chat_completion(
        "Qwen3-0.6B-GGUF",
        [{"role": "user", "content": "ping"}],
        max_tokens=4,
        extra_body={"temperature": 0},
    )

    assert payload["choices"][0]["message"]["content"] == "ok"
    assert seen["url"] == "http://lemonade:13305/api/v1/chat/completions"
    assert b'"model":"Qwen3-0.6B-GGUF"' in seen["body"]
    assert b'"temperature":0' in seen["body"]
    await client.aclose()


@pytest.mark.asyncio
async def test_models_returns_data_list():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "model-a"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = LemonadeClient(client=client)

    assert await adapter.models() == [{"id": "model-a"}]
    await client.aclose()


@pytest.mark.asyncio
async def test_http_status_errors_are_classified():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "missing bearer"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = LemonadeClient(client=client)

    with pytest.raises(LemonadeClientError) as exc:
        await adapter.health()

    assert exc.value.kind == "auth_rejected"
    assert exc.value.status_code == 401
    assert "missing bearer" in str(exc.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_requests_inherit_client_timeout():
    # Regression: _request_json must not pass an explicit timeout=None. httpx
    # reads that as "no timeout" and would silently disable the client's
    # configured timeout on every core call, so a hung Lemonade backend would
    # hang the request forever instead of failing fast.
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=20.0)
    adapter = LemonadeClient(
        LemonadeSettings(base_url="http://lemonade:13305"),
        client=client,
    )

    await adapter.health()
    await client.aclose()

    assert seen["timeout"] is not None, "request must carry a resolved timeout"
    assert seen["timeout"].get("read") == 20.0, (
        f"core requests must inherit the client timeout, got {seen['timeout']}"
    )


@pytest.mark.asyncio
async def test_explicit_timeout_override_is_applied():
    # An explicit per-request timeout still wins over the client default.
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=20.0)
    adapter = LemonadeClient(
        LemonadeSettings(base_url="http://lemonade:13305"),
        client=client,
    )

    await adapter._request_json("GET", "health", timeout=5.0)
    await client.aclose()

    assert seen["timeout"].get("read") == 5.0
