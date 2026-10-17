"""Portal's sidebar lists the shipped example project and opens it without a model run."""

from __future__ import annotations

import json
import os
import pathlib
import sys
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-test-key")
os.environ.setdefault("ODS_INSTALL_DIR", "/tmp/ods-test-install")
os.environ.setdefault("ODS_DATA_DIR", "/tmp/ods-test-data")
os.environ.setdefault("ODS_EXTENSIONS_DIR", "/tmp/ods-test-extensions")
os.environ.setdefault("GPU_BACKEND", "nvidia")
os.environ.setdefault("ODS_MODE", "local")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from routers import pixel  # noqa: E402

EDGE_KEY = "e" * 64
SHA = "a" * 64
SITE = "site-" + SHA[:24]
PREVIEW = {"schemaVersion": 1, "kind": "ods-pixel-workspace-preview", "relativeDirectory": "Playground/elf-of-doom",
           "siteId": SITE, "port": 9437, "url": f"http://{SITE}.localhost:9437/{SITE}/", "files": 9,
           "bytes": 243500, "sha256": SHA, "entrySha256": "b" * 64}


class Response:
    def __init__(self, status=200, body=b"", content_type="application/json"):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    async def aiter_bytes(self):
        yield self.body


class Stream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return False


class Client:
    def __init__(self, response, calls):
        self.response, self.calls = response, calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return Stream(self.response)


@pytest.fixture(autouse=True)
def edge_env(monkeypatch):
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", EDGE_KEY)
    monkeypatch.setenv("PIXEL_EDGE_URL", "http://pixel-edge:9595")


def client(response, calls):
    return patch.object(pixel.httpx, "AsyncClient", return_value=Client(response, calls))


@pytest.mark.asyncio
async def test_list_relays_the_present_examples_with_the_edge_key():
    calls = []
    with client(Response(body={"schemaVersion": 1, "projects": ["elf-of-doom"]}), calls) as factory:
        assert await pixel.pixel_example_projects() == {"schemaVersion": 1, "projects": ["elf-of-doom"]}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "http://pixel-edge:9595/v1/example-projects"
    assert calls[0]["headers"]["Authorization"] == f"Bearer {EDGE_KEY}"
    assert factory.call_args.kwargs["trust_env"] is False
    assert factory.call_args.kwargs["follow_redirects"] is False


@pytest.mark.asyncio
async def test_no_portal_means_no_examples(monkeypatch):
    monkeypatch.delenv("PIXEL_OPENWEBUI_KEY")
    assert await pixel.pixel_example_projects() == {"schemaVersion": 1, "projects": []}
    with pytest.raises(HTTPException) as raised:
        await pixel.pixel_example_project_open(pixel.ExampleProjectOpenRequest(name="elf-of-doom"))
    assert raised.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    Response(status=503, body={"error": "private"}), Response(content_type="text/plain"),
    Response(body={"schemaVersion": 1, "projects": ["../x"]}),
    Response(body={"schemaVersion": 1, "projects": ["elf-of-doom"], "extra": "/home/owner"}),
    Response(body=b"x" * 5000),
])
async def test_an_ambiguous_list_is_an_error_not_a_guess(response):
    with client(response, []):
        with pytest.raises(HTTPException) as raised:
            await pixel.pixel_example_projects()
    assert raised.value.status_code == 503
    assert "private" not in raised.value.detail and "/home" not in raised.value.detail


@pytest.mark.asyncio
async def test_open_returns_the_exact_verified_receipt():
    calls = []
    with client(Response(body={"schemaVersion": 1, "preview": PREVIEW}), calls):
        result = await pixel.pixel_example_project_open(pixel.ExampleProjectOpenRequest(name="elf-of-doom"))
    assert result == {"schemaVersion": 1, "preview": PREVIEW}
    assert calls[0]["method"] == "POST"
    assert calls[0]["json"] == {"name": "elf-of-doom"}


@pytest.mark.asyncio
async def test_a_deleted_example_is_404_and_a_wrong_receipt_503():
    with client(Response(status=404, body={"error": "example-project-unavailable"}), []):
        with pytest.raises(HTTPException) as raised:
            await pixel.pixel_example_project_open(pixel.ExampleProjectOpenRequest(name="elf-of-doom"))
    assert raised.value.status_code == 404
    for preview in ({**PREVIEW, "relativeDirectory": "Playground/other"}, {**PREVIEW, "port": True},
                    {**PREVIEW, "url": "http://evil.example/"}, {**PREVIEW, "secret": "x"}):
        with client(Response(body={"schemaVersion": 1, "preview": preview}), []):
            with pytest.raises(HTTPException) as raised:
                await pixel.pixel_example_project_open(pixel.ExampleProjectOpenRequest(name="elf-of-doom"))
        assert raised.value.status_code == 503


@pytest.mark.parametrize("body", [{"name": "../elf"}, {"name": ""}, {"name": "elf-of-doom", "path": "/etc"}, {}])
def test_open_accepts_only_one_project_name(body):
    with pytest.raises(ValidationError):
        pixel.ExampleProjectOpenRequest.model_validate(body)
