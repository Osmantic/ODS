"""Portal's "Put back files": restore one Playground project to the copy kept before a turn touched it."""

from __future__ import annotations

import json
import os
import pathlib
import sys
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError

os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-test-key")
os.environ.setdefault("ODS_INSTALL_DIR", "/tmp/ods-test-install")
os.environ.setdefault("ODS_DATA_DIR", "/tmp/ods-test-data")
os.environ.setdefault("ODS_EXTENSIONS_DIR", "/tmp/ods-test-extensions")
os.environ.setdefault("GPU_BACKEND", "nvidia")
os.environ.setdefault("ODS_MODE", "local")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pixel_chat_results import owner_namespace  # noqa: E402
from routers import pixel  # noqa: E402

EDGE_KEY = "e" * 64
OWNER = "dashboard-test-key"
SNAPSHOT = "chatcmpl_11111111-2222-4333-8444-555555555555"
UNDO = "restore_99999999-2222-4333-8444-555555555555"
RECEIPT = {"schemaVersion": 1, "project": "Playground/game", "snapshot": SNAPSHOT, "written": 1, "removed": 2,
           "kept": ["node_modules"], "keptCount": 1, "failed": [], "failedCount": 0, "complete": True, "undo": UNDO}


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


def request(**changes):
    return pixel.ProjectRestoreRequest(**{"chat_id": "chat-1", "project": "Playground/game", "snapshot": SNAPSHOT, **changes})


@pytest.mark.asyncio
async def test_restore_relays_one_exact_request_and_returns_the_validated_receipt():
    calls: list[dict] = []
    with client(Response(body=RECEIPT), calls) as factory:
        assert await pixel.pixel_project_restore(request(), OWNER) == RECEIPT
    assert calls == [{"method": "POST", "url": "http://pixel-edge:9595/v1/project-restore",
                      "json": {"user": "chat-1", "project": "Playground/game", "snapshot": SNAPSHOT},
                      "headers": {"Authorization": f"Bearer {EDGE_KEY}", "Accept": "application/json",
                                  "Content-Type": "application/json"}}]
    assert factory.call_args.kwargs["trust_env"] is False
    assert factory.call_args.kwargs["follow_redirects"] is False
    # The undo a restore returned puts the files back as they were before it.
    with client(Response(body={**RECEIPT, "snapshot": UNDO, "undo": None}), calls):
        assert (await pixel.pixel_project_restore(request(snapshot=UNDO), OWNER))["undo"] is None


@pytest.mark.asyncio
async def test_restore_is_refused_while_this_process_works_on_the_chat(monkeypatch):
    conversation = (owner_namespace(OWNER), "chat-1")
    calls: list[dict] = []
    for fence in ("_result_stops", "_result_preflights"):
        monkeypatch.setattr(pixel, fence, {conversation} if fence == "_result_stops" else {(*conversation, "request-1")})
        with client(Response(body=RECEIPT), calls):
            with pytest.raises(HTTPException) as raised:
                await pixel.pixel_project_restore(request(), OWNER)
        assert raised.value.status_code == 409
        assert raised.value.detail == "Portal is still working. Put the files back when it finishes."
        monkeypatch.setattr(pixel, fence, set())
    assert calls == []
    # Another chat of the same owner is not held by it.
    monkeypatch.setattr(pixel, "_result_stops", {conversation})
    with client(Response(body=RECEIPT), calls):
        assert await pixel.pixel_project_restore(request(chat_id="chat-2"), OWNER) == RECEIPT


@pytest.mark.asyncio
async def test_host_refusals_keep_their_meaning_and_anything_else_is_503():
    for status, detail in ((409, "Portal is still working. Put the files back when it finishes."),
                           (404, "ODS no longer keeps that copy of this project, so nothing was changed."),
                           (422, "The copy ODS kept of this project is damaged, so nothing was changed.")):
        with client(Response(status=status, body={"error": "x", "private": "/home/owner"}), []):
            with pytest.raises(HTTPException) as raised:
                await pixel.pixel_project_restore(request(), OWNER)
        assert (raised.value.status_code, raised.value.detail) == (status, detail)
    for response in (Response(status=503, body={"error": "private"}), Response(content_type="text/plain"),
                     Response(body={**RECEIPT, "project": "Playground/other"}), Response(body={**RECEIPT, "snapshot": UNDO}),
                     Response(body={**RECEIPT, "undo": "../x"}), Response(body={**RECEIPT, "extra": "/home/owner"}),
                     Response(body={**RECEIPT, "written": True}), Response(body={**RECEIPT, "kept": ["a\nb"]}),
                     Response(body={**RECEIPT, "failed": ["a"], "failedCount": 0}), Response(body=b"x" * 17000)):
        with client(response, []):
            with pytest.raises(HTTPException) as raised:
                await pixel.pixel_project_restore(request(), OWNER)
        assert raised.value.status_code == 503
        assert "private" not in raised.value.detail and "/home" not in raised.value.detail


@pytest.mark.asyncio
async def test_no_portal_means_no_restore(monkeypatch):
    monkeypatch.delenv("PIXEL_OPENWEBUI_KEY")
    with pytest.raises(HTTPException) as raised:
        await pixel.pixel_project_restore(request(), OWNER)
    assert raised.value.status_code == 503


@pytest.mark.parametrize("changes", [
    {"project": "Playground/../etc"}, {"project": "Playground/game/src"}, {"project": "/etc/passwd"},
    {"project": "Other/game"}, {"project": "Playground/"}, {"project": "Playground/CON"}, {"project": "Playground/game."},
    {"project": "playground/game"}, {"project": "Playground\\game"}, {"project": "C:/Users/owner/Playground/game"},
    {"snapshot": "restore_x"}, {"snapshot": "../" + SNAPSHOT}, {"chat_id": "../chat"}, {"path": "/etc"},
])
def test_only_one_playground_project_and_copy_name_are_accepted(changes):
    body = {"chat_id": "chat-1", "project": "Playground/game", "snapshot": SNAPSHOT, **changes}
    with pytest.raises(ValidationError):
        pixel.ProjectRestoreRequest.model_validate(body)


def test_the_route_needs_the_dashboard_key():
    route = next(route for route in pixel.router.routes
                 if isinstance(route, APIRoute) and route.path == "/api/pixel/projects/restore")
    assert route.methods == {"POST"}
    assert any(dependency.call is pixel.verify_api_key for dependency in route.dependant.dependencies)
