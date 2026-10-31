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
TOKEN = "c" * 64
RECEIPT = {"schemaVersion": 2, "status": "restored", "project": "Playground/game", "snapshot": SNAPSHOT, "written": 1,
           "removed": 2, "left": ["big.bin"], "leftCount": 1, "failed": ["a.js"], "failedCount": 1, "failure": "in-use",
           "late": True, "undo": UNDO, "confirm": None}
ASKED = {"token": TOKEN, "overwrite": ["chars.js"], "overwriteCount": 1, "remove": ["new.js"], "removeCount": 4}
CONFIRM = {**RECEIPT, "status": "confirm", "written": 0, "removed": 0, "failed": [], "failedCount": 0, "failure": None,
           "undo": None, "confirm": ASKED}
OPTION = {"project": "Playground/game", "snapshot": SNAPSHOT, "late": False, "restore": {"undo": UNDO, "side": "before"}}
UNKNOWN = {"code": "project-restore-unavailable", "message": "Portal could not confirm whether the project's files were put back."}


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


def detail(error: HTTPException) -> dict:
    assert isinstance(error.detail, dict)
    return error.detail


async def refused(response, body=None) -> HTTPException:
    with client(response, []):
        with pytest.raises(HTTPException) as raised:
            await pixel.pixel_project_restore(body or request(), OWNER)
    return raised.value


@pytest.mark.asyncio
async def test_restore_relays_one_exact_request_and_returns_the_validated_receipt():
    calls: list[dict] = []
    headers = {"Authorization": f"Bearer {EDGE_KEY}", "Accept": "application/json", "Content-Type": "application/json"}
    with client(Response(body=RECEIPT), calls) as factory:
        assert await pixel.pixel_project_restore(request(), OWNER) == RECEIPT
    assert calls == [{"method": "POST", "url": "http://pixel-edge:9595/v1/project-restore",
                      "json": {"user": "chat-1", "project": "Playground/game", "snapshot": SNAPSHOT}, "headers": headers}]
    assert factory.call_args.kwargs["trust_env"] is False
    assert factory.call_args.kwargs["follow_redirects"] is False
    # Later changes: the first answer names them and changes nothing; the yes carries its token.
    with client(Response(body=CONFIRM), calls):
        assert await pixel.pixel_project_restore(request(), OWNER) == CONFIRM
    with client(Response(body=RECEIPT), calls):
        assert await pixel.pixel_project_restore(request(confirm=TOKEN), OWNER) == RECEIPT
    assert calls[-1]["json"] == {"user": "chat-1", "project": "Playground/game", "snapshot": SNAPSHOT, "confirm": TOKEN}
    # The undo a restore returned puts the files back as they were before it.
    undone = {**RECEIPT, "snapshot": UNDO, "undo": "restore_00000000-2222-4333-8444-555555555555"}
    with client(Response(body=undone), calls):
        assert await pixel.pixel_project_restore(request(snapshot=UNDO), OWNER) == undone
    nothing = {**RECEIPT, "status": "unchanged", "written": 0, "removed": 0, "failed": [], "failedCount": 0, "failure": None,
               "undo": None}
    with client(Response(body=nothing), calls):
        assert await pixel.pixel_project_restore(request(), OWNER) == nothing


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
        assert detail(raised.value)["code"] == "project-restore-busy"
        assert "another" in detail(raised.value)["message"] and "Nothing was changed" in detail(raised.value)["message"]
        monkeypatch.setattr(pixel, fence, set())
    assert calls == []
    # Another chat of the same owner is not held by this process; the host decides.
    monkeypatch.setattr(pixel, "_result_stops", {conversation})
    with client(Response(body=RECEIPT), calls):
        assert await pixel.pixel_project_restore(request(chat_id="chat-2"), OWNER) == RECEIPT


@pytest.mark.asyncio
async def test_host_refusals_keep_their_fixed_code_and_anything_else_leaves_the_outcome_unknown():
    for status, code in ((409, "project-restore-busy"), (404, "project-snapshot-unavailable"), (404, "project-snapshot-pruned"),
                         (422, "project-snapshot-damaged"), (422, "project-folder-unusable"),
                         (503, "project-copies-unusable"), (503, "project-restore-unsupported")):
        error = await refused(Response(status=status, body={"error": code, "private": "/home/owner"}))
        assert (error.status_code, detail(error)["code"]) == (status, code)
        assert "nothing was changed" in detail(error)["message"].lower()
    for response in (Response(status=503, body={"error": "project-restore-unavailable"}), Response(status=503, body={"error": "private"}),
                     Response(status=404, body={"error": "project-folder-unusable"}), Response(status=404, body=b"404: Not Found",
                                                                                               content_type="text/plain"),
                     Response(status=500, body={"error": "project-snapshot-pruned"}), Response(content_type="text/plain"),
                     Response(body={**RECEIPT, "project": "Playground/other"}), Response(body={**RECEIPT, "snapshot": UNDO}),
                     Response(body={**RECEIPT, "schemaVersion": 1}), Response(body={**RECEIPT, "undo": "../x"}),
                     Response(body={**RECEIPT, "undo": None}), Response(body={**RECEIPT, "status": "unchanged"}),
                     Response(body={**RECEIPT, "extra": "/home/owner"}), Response(body={**RECEIPT, "written": True}),
                     Response(body={**RECEIPT, "late": None}), Response(body={**RECEIPT, "failure": "disk"}),
                     Response(body={**RECEIPT, "left": ["a\nb"]}), Response(body={**RECEIPT, "failed": ["a"], "failedCount": 0}),
                     Response(body={**CONFIRM, "confirm": None}), Response(body={**CONFIRM, "confirm": {**ASKED, "token": "x"}}),
                     Response(body={**RECEIPT, "confirm": ASKED}), Response(body=b"x" * 17000)):
        error = await refused(response)
        assert (error.status_code, error.detail) == (503, UNKNOWN), response.body[:80]


@pytest.mark.asyncio
async def test_no_portal_means_no_restore_and_no_options(monkeypatch):
    monkeypatch.delenv("PIXEL_OPENWEBUI_KEY")
    error = await refused(Response(body=RECEIPT))
    assert (error.status_code, detail(error)["code"]) == (503, "project-restore-unsupported")
    assert await pixel.pixel_project_restore_options(pixel.ProjectRestoreOptionsRequest(runs=[SNAPSHOT])) == {
        "schemaVersion": 1, "projects": []}


@pytest.mark.asyncio
async def test_options_relay_the_runs_and_return_only_validated_options():
    calls: list[dict] = []
    body = pixel.ProjectRestoreOptionsRequest(runs=[SNAPSHOT])
    with client(Response(body={"schemaVersion": 1, "projects": [OPTION]}), calls):
        assert await pixel.pixel_project_restore_options(body) == {"schemaVersion": 1, "projects": [OPTION]}
    assert [(call["url"], call["json"]) for call in calls] == [("http://pixel-edge:9595/v1/project-restore-options", {"runs": [SNAPSHOT]})]
    assert await pixel.pixel_project_restore_options(pixel.ProjectRestoreOptionsRequest(runs=[])) == {"schemaVersion": 1, "projects": []}
    assert len(calls) == 1, "no runs, no question"
    for response in (Response(body={"schemaVersion": 1, "projects": [{**OPTION, "snapshot": UNDO}]}),
                     Response(body={"schemaVersion": 1, "projects": [{**OPTION, "restore": {"undo": UNDO, "side": "x"}}]}),
                     Response(body={"schemaVersion": 1, "projects": [{**OPTION, "project": "Playground/a/b"}]}),
                     Response(body={"schemaVersion": 1, "projects": [OPTION] * 33}), Response(body={"schemaVersion": 2, "projects": []}),
                     Response(status=503, body={"error": "project-copies-unusable"}), Response(body=b"not json")):
        with client(response, []):
            with pytest.raises(HTTPException) as raised:
                await pixel.pixel_project_restore_options(body)
        assert (raised.value.status_code, detail(raised.value)["code"]) == (503, "project-restore-unavailable")


@pytest.mark.parametrize("changes", [
    {"project": "Playground/../etc"}, {"project": "Playground/game/src"}, {"project": "/etc/passwd"},
    {"project": "Other/game"}, {"project": "Playground/"}, {"project": "Playground/CON"}, {"project": "Playground/game."},
    {"project": "playground/game"}, {"project": "Playground\\game"}, {"project": "C:/Users/owner/Playground/game"},
    {"snapshot": "restore_x"}, {"snapshot": "../" + SNAPSHOT}, {"chat_id": "../chat"}, {"path": "/etc"},
    {"confirm": "yes"}, {"confirm": TOKEN.upper()}, {"confirm": 1},
])
def test_only_one_playground_project_copy_name_and_yes_are_accepted(changes):
    body = {"chat_id": "chat-1", "project": "Playground/game", "snapshot": SNAPSHOT, **changes}
    with pytest.raises(ValidationError):
        pixel.ProjectRestoreRequest.model_validate(body)


@pytest.mark.parametrize("runs", [[UNDO], ["chatcmpl_x"], [SNAPSHOT] * 65, SNAPSHOT, [1]])
def test_options_accept_only_portal_run_ids(runs):
    with pytest.raises(ValidationError):
        pixel.ProjectRestoreOptionsRequest.model_validate({"runs": runs})


def test_the_routes_need_the_dashboard_key():
    for path in ("/api/pixel/projects/restore", "/api/pixel/projects/restore-options"):
        route = next(route for route in pixel.router.routes if isinstance(route, APIRoute) and route.path == path)
        assert route.methods == {"POST"}
        assert any(dependency.call is pixel.verify_api_key for dependency in route.dependant.dependencies), path
