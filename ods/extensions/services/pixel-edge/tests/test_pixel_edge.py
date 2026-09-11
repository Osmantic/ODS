import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

"""Tests for pixel_edge — upstream Unix socket + edge proxy routes."""

import asyncio

try:
    from asyncio import timeout as async_timeout
except ImportError:  # Python 3.10; installed by this runtime's requirements.
    from async_timeout import timeout as async_timeout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import warnings

warnings.filterwarnings("ignore", message=".*Sending a large body.*")
warnings.filterwarnings("ignore", message=".*ResourceWarning.*")

from aiohttp import web, ClientSession, UnixConnector  # noqa: E402

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

TOKEN = "test-token-abc123-0123456789abcdef"
FRAGMENTED_CSV = b"id,amount\n" + b"123,45.67\n" * 64000


# ---------------------------------------------------------------------------
# Mock upstream on a Unix socket
# ---------------------------------------------------------------------------

async def _upstream_chat(request):
    data = await request.json()
    request.app["chat_requests"].append(data)
    stream = data.get("stream", False)

    if data.get('trigger_live'):
        task = {"schemaVersion":1,"runId":"chatcmpl_11111111-2222-4333-8444-555555555555","startedAt":"2026-09-10T20:00:00.000Z","finishedAt":None,"state":"running","calls":1,"failures":0,"blocked":0,"truncated":False,"activities":[{"kind":"read","calls":1,"failures":0,"blocked":0}]}
        packet = {"object":"ods.task.activity","id":task['runId'],"pixel_task":task}
        resp = web.StreamResponse(headers={'Content-Type':'text/event-stream'})
        await resp.prepare(request)
        await resp.write(('data: ' + json.dumps({**packet,'prompt':'must-not-leak'}) + '\n\n').encode())
        await resp.write(('data: ' + json.dumps(packet) + '\n\n').encode())
        await request.app['release_stream'].wait()
        await resp.write(b'data: {"choices":[{"delta":{"content":"Verified reply"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        return resp

    if data.get("trigger_error"):
        return web.json_response({"error": "upstream-secret-path-/private/token"}, status=500)

    if data.get("trigger_detached_native"):
        stop = asyncio.Event()
        task = asyncio.create_task(stop.wait())
        request.app["native_runs"][data["user"]] = (stop, task)
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        try:
            while not task.done():
                await resp.write(b'data: {"choices":[{"delta":{"content":"Native work continues. "}}]}\n\n')
                await asyncio.sleep(0.02)
            await resp.write(b'data: [DONE]\n\n')
        except ConnectionError:
            # Native execution is independent of the response transport.
            pass
        return resp

    if stream:
        async def generate():
            if data.get("trigger_cancel_wait"):
                yield b'data: {"id":"1","model":"openclaw/default","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                request.app["stream_started"].set()
                await request.app["release_stream"].wait()
                if data.get("trigger_cancel_eof"):
                    return
                yield b'data: {"id":"1","model":"openclaw/default","choices":[{"index":0,"delta":{"content":"LLM request timed out."},"finish_reason":null}]}\n\n'
                yield b'data: {"id":"1","model":"openclaw/default","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                yield b'data: [DONE]\n\n'
                return
            if data.get("trigger_stream_error"):
                yield b'data: {"error":{"message":"upstream failed"}}\n\n'
                yield b'data: [DONE]\n\n'
                return
            yield b'data: {"id":"1","model":"openclaw/default","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            if data.get("trigger_reserved"):
                yield b'data: {"id":"1","model":"openclaw/default","choices":[{"index":0,"delta":{"content":"No response "},"finish_reason":null}]}\n\n'
                yield b'data: {"id":"1","model":"openclaw/default","choices":[{"index":0,"delta":{"content":"from OpenClaw."},"finish_reason":null}]}\n\n'
            else:
                yield b'data: {"id":"2","model":"openclaw/default","choices":[{"index":0,"delta":{"content":"openclaw/default is assistant text"}}]}\n\n'
            yield b'data: {"id":"1","model":"openclaw/default","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b'data: [DONE]\n\n'
        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
        )
        await resp.prepare(request)
        async for chunk in generate():
            await resp.write(chunk)
        await resp.write_eof()
        return resp

    return web.json_response({
        "id": "chat-1",
        "model": "openclaw/default",
        "choices": [{"message": {"content": (
            "No response from OpenClaw."
            if data.get("trigger_reserved")
            else "openclaw/default is assistant text"
        )}}],
    })


async def _upstream_models(_request):
    return web.json_response({
        "object": "list",
        "data": [{"id": "openclaw/default", "object": "model", "owned_by": "openclaw"}],
    })


async def _upstream_health(_request):
    return web.json_response({"status": "ok"})


async def _upstream_cancel(request):
    data = await request.json()
    request.app["cancel_users"].append(data.get("user"))
    if request.app["native_runs"]:
        native = request.app["native_runs"].get(data.get("user"))
        if native is None or native[1].done():
            return web.json_response({"aborted": False})
        native[0].set()
        await native[1]
        return web.json_response({"aborted": True})
    if request.app["release_on_cancel"]:
        asyncio.get_running_loop().call_later(0.05, request.app["release_stream"].set)
    return web.json_response({"aborted": True})


async def _upstream_preview(request):
    request.app["preview_hosts"].append(request.headers.get("Host"))
    site_id = "site-" + "a" * 24
    if request.match_info.get("site_id") != site_id:
        return web.Response(status=404)
    if request.match_info.get("tail") == "fragmented.csv":
        headers = {
            "Content-Type": "text/csv",
            "Content-Length": str(len(FRAGMENTED_CSV)),
            "X-Preview-SHA256": hashlib.sha256(FRAGMENTED_CSV).hexdigest(),
        }
        response = web.StreamResponse(headers=headers)
        await response.prepare(request)
        # HEAD has no upstream body: the edge must request GET to verify bytes.
        if request.method != "HEAD":
            try:
                await response.write(FRAGMENTED_CSV[:128])
                await asyncio.sleep(0.02)
                for offset in range(128, len(FRAGMENTED_CSV), 8192):
                    await response.write(FRAGMENTED_CSV[offset:offset + 8192])
                await response.write_eof()
            except ConnectionResetError:
                # Oversize rejection closes the upstream before it finishes.
                pass
        return response
    body = b"<button id=launch>Remote preview</button>"
    digest = (
        "b" * 64
        if request.match_info.get("tail") == "tampered.html"
        else hashlib.sha256(body).hexdigest()
    )
    return web.Response(
        body=body,
        content_type="text/html",
        headers={"X-Preview-SHA256": digest},
    )


async def _start_upstream():
    fd, path = tempfile.mkstemp(suffix=".sock")
    os.close(fd)
    os.unlink(path)
    app = web.Application()
    app["chat_requests"] = []
    app["cancel_users"] = []
    app["native_runs"] = {}
    app["stream_started"] = asyncio.Event()
    app["release_stream"] = asyncio.Event()
    app["release_on_cancel"] = False
    app["preview_hosts"] = []
    app.router.add_post("/v1/chat/completions", _upstream_chat)
    app.router.add_post("/v1/chat/cancel", _upstream_cancel)
    app.router.add_get("/v1/models", _upstream_models)
    app.router.add_get("/health", _upstream_health)
    app.router.add_get("/{site_id}/{tail:.*}", _upstream_preview)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, path)
    await site.start()
    return path, runner


async def _stop_upstream(runner, path=None):
    for stop, task in runner.app["native_runs"].values():
        stop.set()
        await task
    await runner.cleanup()
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _set_env(sock_path):
    os.environ["PIXEL_OPENWEBUI_KEY"] = TOKEN
    os.environ["PIXEL_INGRESS_SOCKET"] = sock_path
    os.environ["PIXEL_PREVIEW_PROXY_KEY"] = TOKEN
    os.environ["PIXEL_PREVIEW_SOCKET"] = sock_path


# ---------------------------------------------------------------------------
# Base test: real edge app + mock upstream
# ---------------------------------------------------------------------------

class BaseEdgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.up_sock, self.up_runner = await _start_upstream()
        _set_env(self.up_sock)

        import importlib
        import pixel_edge
        self.pe = importlib.reload(pixel_edge)

        self.edge_app = self.pe.create_app()
        self.edge_runner = web.AppRunner(self.edge_app)
        await self.edge_runner.setup()

        fd, self.edge_sock = tempfile.mkstemp(suffix=".edge.sock")
        os.close(fd)
        os.unlink(self.edge_sock)
        self.edge_site = web.UnixSite(self.edge_runner, self.edge_sock)
        await self.edge_site.start()

        self.client = ClientSession(connector=UnixConnector(path=self.edge_sock))

    async def asyncTearDown(self):
        await self.client.close()
        await self.edge_runner.cleanup()
        await _stop_upstream(self.up_runner, self.up_sock)
        try:
            os.unlink(self.edge_sock)
        except OSError:
            pass

    def auth(self):
        return {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# Startup config validation
# ---------------------------------------------------------------------------

class TestConfigValidation(unittest.TestCase):
    def setUp(self):
        # Pytest preserves source order and reaches this class before any
        # BaseEdgeTest has imported pixel_edge. Load one known-good module
        # first so each case exercises the intended reload boundary rather
        # than raising outside assertRaises during an initial import.
        _set_env("/tmp/pixel-edge-config-test.sock")
        import importlib
        import pixel_edge
        self.pixel_edge = importlib.reload(pixel_edge)

    def _reload_with_env(self, env):
        old = {k: os.environ.get(k) for k in (
            "PIXEL_OPENWEBUI_KEY", "PIXEL_INGRESS_SOCKET",
            "PIXEL_PREVIEW_PROXY_KEY", "PIXEL_PREVIEW_SOCKET",
        )}
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v
        for k, v in env.items():
            os.environ[k] = v
        return old

    def _restore(self, old):
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_blank_token_exits(self):
        old = self._reload_with_env({"PIXEL_OPENWEBUI_KEY": ""})
        try:
            import importlib
            with self.assertRaises(SystemExit):
                importlib.reload(self.pixel_edge)
        finally:
            self._restore(old)

    def test_missing_token_exits(self):
        old = self._reload_with_env({})
        os.environ.pop("PIXEL_OPENWEBUI_KEY", None)
        try:
            import importlib
            with self.assertRaises(SystemExit):
                importlib.reload(self.pixel_edge)
        finally:
            self._restore(old)

    def test_oversized_token_exits(self):
        old = self._reload_with_env({"PIXEL_OPENWEBUI_KEY": "x" * 4097})
        try:
            import importlib
            with self.assertRaises(SystemExit):
                importlib.reload(self.pixel_edge)
        finally:
            self._restore(old)

    def test_short_or_whitespace_token_exits(self):
        for value in ("too-short", "x" * 31, ("x" * 32) + "\n"):
            old = self._reload_with_env({"PIXEL_OPENWEBUI_KEY": value})
            try:
                import importlib
                with self.assertRaises(SystemExit):
                    importlib.reload(self.pixel_edge)
            finally:
                self._restore(old)

    def test_blank_preview_proxy_token_exits(self):
        old = self._reload_with_env({"PIXEL_PREVIEW_PROXY_KEY": ""})
        try:
            import importlib
            with self.assertRaises(SystemExit):
                importlib.reload(self.pixel_edge)
        finally:
            self._restore(old)

    def test_chat_timeout_budget_outlives_private_host_ingress(self):
        self.assertEqual(self.pixel_edge._TOTAL_TIMEOUT, 1980)
        self.assertEqual(self.pixel_edge._SOCK_READ_TIMEOUT, 1980)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth(BaseEdgeTest):
    async def test_health_ok_no_auth(self):
        async with self.client.get("http://localhost/health") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(await resp.json(), {"status": "ok"})

    async def test_health_fails_closed_when_ingress_socket_is_absent(self):
        original = self.pe._SOCKET_PATH
        self.pe._SOCKET_PATH = "/tmp/definitely-missing-pixel-ingress.sock"
        try:
            async with self.client.get("http://localhost/health") as resp:
                self.assertEqual(resp.status, 503)
                self.assertEqual(await resp.json(), {"status": "unavailable"})
        finally:
            self.pe._SOCKET_PATH = original

    async def test_models_fail_closed_when_ingress_socket_is_absent(self):
        original = self.pe._SOCKET_PATH
        self.pe._SOCKET_PATH = "/tmp/definitely-missing-pixel-ingress.sock"
        try:
            async with self.client.get(
                "http://localhost/v1/models", headers=self.auth()
            ) as resp:
                self.assertEqual(resp.status, 503)
                self.assertEqual(await resp.json(), {"error": "service unavailable"})
        finally:
            self.pe._SOCKET_PATH = original


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth(BaseEdgeTest):
    async def test_models_missing_auth(self):
        async with self.client.get("http://localhost/v1/models") as resp:
            self.assertEqual(resp.status, 401)

    async def test_models_wrong_token(self):
        async with self.client.get("http://localhost/v1/models",
                                   headers={"Authorization": "Bearer wrong"}) as resp:
            self.assertEqual(resp.status, 401)

    async def test_models_correct_token(self):
        async with self.client.get("http://localhost/v1/models",
                                   headers=self.auth()) as resp:
            self.assertEqual(resp.status, 200)

    async def test_activity_requires_auth(self):
        async with self.client.get("http://localhost/v1/activity") as resp:
            self.assertEqual(resp.status, 401)

    async def test_activity_is_content_free_when_idle(self):
        async with self.client.get(
            "http://localhost/v1/activity", headers=self.auth()
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(await resp.json(), {"active": False, "streams": 0})

    async def test_chat_missing_auth(self):
        async with self.client.post("http://localhost/v1/chat/completions",
                                    json={"model": "pixel/default", "messages": []}) as resp:
            self.assertEqual(resp.status, 401)

    async def test_chat_wrong_token(self):
        async with self.client.post("http://localhost/v1/chat/completions",
                                    headers={"Authorization": "Bearer wrong"},
                                    json={"model": "pixel/default", "messages": []}) as resp:
            self.assertEqual(resp.status, 401)

    async def test_cancel_requires_auth(self):
        async with self.client.post(
            "http://localhost/v1/chat/cancel", json={"user": "conversation-1"}
        ) as resp:
            self.assertEqual(resp.status, 401)


class TestPreviewRelay(BaseEdgeTest):
    async def test_manifest_route_is_exact_and_keeps_authentication(self):
        from pixel_edge import _preview_upstream_path
        site = "site-" + "a" * 24
        self.assertEqual(_preview_upstream_path(site, "__ods_manifest__.json"), f"/{site}/__ods_manifest__.json")
        self.assertEqual(_preview_upstream_path(site, "__ods_view__.html"), f"/{site}/__ods_view__.html")
        self.assertEqual(_preview_upstream_path(site, f"__ods_changes__/{site}.json"), f"/{site}/__ods_changes__/{site}.json")
        self.assertEqual(_preview_upstream_path(site, "__ods_changes__/initial.json"), f"/{site}/__ods_changes__/initial.json")
        for tail in ["__ods_changes__/../index.html", "__ods_changes__/initial.json?path=secret", "__ods_changes__/garbage.json"]:
            self.assertIsNone(_preview_upstream_path(site, tail))
        for tail in ["__ods_view__.html?path=secret", "../__ods_view__.html", "__ods_view__.html/extra"]:
            self.assertIsNone(_preview_upstream_path(site, tail))
        for tail in ["__ods_manifest__.json/other", "__ods_manifest__.json?path=secret", "../__ods_manifest__.json", "__anything"]:
            self.assertIsNone(_preview_upstream_path(site, tail))
        self.assertIsNone(_preview_upstream_path("not-a-site", "__ods_manifest__.json"))
        async with self.client.get(f"http://localhost/preview/{site}/__ods_manifest__.json") as resp:
            self.assertEqual(resp.status, 401)

    async def test_fragmented_csv_get_relays_all_verified_bytes(self):
        site_id = "site-" + "a" * 24
        async with self.client.get(
            f"http://localhost/preview/{site_id}/fragmented.csv", headers=self.auth()
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), FRAGMENTED_CSV)
            self.assertEqual(int(response.headers["Content-Length"]), len(FRAGMENTED_CSV))
            self.assertEqual(response.headers["X-Preview-SHA256"], hashlib.sha256(FRAGMENTED_CSV).hexdigest())

    async def test_fragmented_csv_head_verifies_full_body_without_relaying_it(self):
        site_id = "site-" + "a" * 24
        async with self.client.head(
            f"http://localhost/preview/{site_id}/fragmented.csv", headers=self.auth()
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), b"")
            self.assertEqual(int(response.headers["Content-Length"]), len(FRAGMENTED_CSV))
            self.assertEqual(response.headers["X-Preview-SHA256"], hashlib.sha256(FRAGMENTED_CSV).hexdigest())

    async def test_fragmented_csv_still_rejects_oversized_body(self):
        site_id = "site-" + "a" * 24
        original = self.pe._MAX_PREVIEW_RESPONSE_BYTES
        self.pe._MAX_PREVIEW_RESPONSE_BYTES = 256
        try:
            async with self.client.get(
                f"http://localhost/preview/{site_id}/fragmented.csv", headers=self.auth()
            ) as response:
                self.assertEqual(response.status, 502)
                self.assertEqual(await response.json(), {"error": "preview too large"})
        finally:
            self.pe._MAX_PREVIEW_RESPONSE_BYTES = original

    async def test_preview_requires_the_dashboard_proxy_token(self):
        site_id = "site-" + "a" * 24
        async with self.client.get(f"http://localhost/preview/{site_id}/") as resp:
            self.assertEqual(resp.status, 401)
            self.assertNotIn("Access-Control-Allow-Origin", resp.headers)

    async def test_preview_relays_exact_bytes_with_an_opaque_browser_sandbox(self):
        site_id = "site-" + "a" * 24
        async with self.client.get(
            f"http://localhost/preview/{site_id}/", headers={**self.auth(), "Origin": "null"}
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(
                await resp.read(), b"<button id=launch>Remote preview</button>"
            )
            self.assertEqual(
                resp.headers["X-Preview-SHA256"],
                hashlib.sha256(b"<button id=launch>Remote preview</button>").hexdigest(),
            )
            csp = resp.headers["Content-Security-Policy"]
            self.assertIn("sandbox allow-scripts allow-forms allow-downloads;", csp)
            self.assertNotIn("allow-same-origin", csp)
            self.assertNotIn("allow-popups", csp)
            self.assertNotIn("allow-top-navigation", csp)
            self.assertIn("form-action 'none'", csp)
            self.assertIn("connect-src 'self'", csp)
            self.assertIn("frame-ancestors 'self'", csp)
            self.assertEqual(resp.headers["Cross-Origin-Resource-Policy"], "cross-origin")
            self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")
            self.assertNotIn("Access-Control-Allow-Credentials", resp.headers)
        self.assertEqual(self.up_runner.app["preview_hosts"], ["pixel-preview.internal"])

    async def test_preview_cors_does_not_extend_to_control_endpoints(self):
        async with self.client.get(
            "http://localhost/v1/activity", headers={**self.auth(), "Origin": "null"}
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertNotIn("Access-Control-Allow-Origin", resp.headers)

    async def test_preview_rejects_invalid_or_unknown_snapshot_paths(self):
        async with self.client.get(
            "http://localhost/preview/not-a-site/index.html", headers=self.auth()
        ) as resp:
            self.assertEqual(resp.status, 404)
        unknown = "site-" + "c" * 24
        async with self.client.get(
            f"http://localhost/preview/{unknown}/", headers=self.auth()
        ) as resp:
            self.assertEqual(resp.status, 404)

    async def test_preview_rejects_bytes_that_do_not_match_the_host_digest(self):
        site_id = "site-" + "a" * 24
        async with self.client.get(
            f"http://localhost/preview/{site_id}/tampered.html", headers=self.auth()
        ) as resp:
            self.assertEqual(resp.status, 502)


# ---------------------------------------------------------------------------
# Refused routes / methods
# ---------------------------------------------------------------------------

class TestRefusedRoutes(BaseEdgeTest):
    async def test_unknown_path(self):
        async with self.client.get("http://localhost/unknown") as resp:
            self.assertEqual(resp.status, 404)

    async def test_post_to_models(self):
        async with self.client.post("http://localhost/v1/models", headers=self.auth()) as resp:
            self.assertEqual(resp.status, 404)

    async def test_delete_to_chat(self):
        async with self.client.delete("http://localhost/v1/chat/completions") as resp:
            self.assertEqual(resp.status, 404)

    async def test_get_to_health_wrong_method(self):
        async with self.client.post("http://localhost/health") as resp:
            self.assertEqual(resp.status, 404)


# ---------------------------------------------------------------------------
# Content type
# ---------------------------------------------------------------------------

class TestContentType(BaseEdgeTest):
    async def test_wrong_content_type(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers={**self.auth(), "Content-Type": "text/plain"},
            data="not json") as resp:
            self.assertEqual(resp.status, 415)

    async def test_cancel_wrong_content_type(self):
        async with self.client.post(
            "http://localhost/v1/chat/cancel",
            headers={**self.auth(), "Content-Type": "text/plain"},
            data="not json",
        ) as resp:
            self.assertEqual(resp.status, 415)


class TestCancellation(BaseEdgeTest):
    async def test_activity_counts_an_openwebui_stream_without_a_cancellable_chat_id(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "long task"}],
                "stream": True,
                "trigger_cancel_wait": True,
            },
        ) as stream_response:
            await asyncio.wait_for(self.up_runner.app["stream_started"].wait(), timeout=1)
            async with self.client.get(
                "http://localhost/v1/activity", headers=self.auth()
            ) as activity_response:
                self.assertEqual(activity_response.status, 200)
                activity = await activity_response.json()
                self.assertEqual(activity, {"active": True, "streams": 1})
            self.up_runner.app["release_stream"].set()
            await stream_response.text()

        async with self.client.get(
            "http://localhost/v1/activity", headers=self.auth()
        ) as activity_response:
            self.assertEqual(await activity_response.json(), {"active": False, "streams": 0})

    async def test_cancel_forwards_only_the_validated_chat_id(self):
        async with self.client.post(
            "http://localhost/v1/chat/cancel",
            headers=self.auth(),
            json={"user": "conversation-42"},
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(await resp.json(), {"aborted": True})
        self.assertEqual(self.up_runner.app["cancel_users"], ["conversation-42"])

    async def test_cancel_rejects_ambiguous_or_unsafe_bodies(self):
        cases = (
            {},
            {"user": "../../escape"},
            {"user": "safe", "extra": True},
            {"user": 7},
            ["conversation-42"],
        )
        for body in cases:
            async with self.client.post(
                "http://localhost/v1/chat/cancel",
                headers=self.auth(),
                json=body,
            ) as resp:
                self.assertEqual(resp.status, 400)
        self.assertEqual(self.up_runner.app["cancel_users"], [])

    async def test_cancelled_stream_ends_cleanly_without_late_upstream_error(self):
        self.up_runner.app["release_on_cancel"] = True
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "long task"}],
                "stream": True,
                "user": "conversation-cancel-clean",
                "trigger_cancel_wait": True,
            },
        ) as stream_response:
            await asyncio.wait_for(self.up_runner.app["stream_started"].wait(), timeout=1)
            async with self.client.post(
                "http://localhost/v1/chat/cancel",
                headers=self.auth(),
                json={"user": "conversation-cancel-clean"},
            ) as cancel_response:
                self.assertEqual(cancel_response.status, 200)
                self.assertEqual(await cancel_response.json(), {"aborted": True})
            body = await stream_response.text()

        self.assertNotIn("LLM request timed out", body)
        self.assertTrue(body.endswith("data: [DONE]\n\n"))
        self.assertEqual(self.edge_app[self.pe._CANCEL_EVENTS_KEY], {})

    async def test_cancelled_stream_with_immediate_upstream_eof_is_only_done(self):
        self.up_runner.app["release_on_cancel"] = True
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "long task"}],
                "stream": True,
                "user": "conversation-cancel-eof",
                "trigger_cancel_wait": True,
                "trigger_cancel_eof": True,
            },
        ) as stream_response:
            await asyncio.wait_for(self.up_runner.app["stream_started"].wait(), timeout=1)
            async with self.client.post(
                "http://localhost/v1/chat/cancel",
                headers=self.auth(),
                json={"user": "conversation-cancel-eof"},
            ) as cancel_response:
                self.assertEqual(cancel_response.status, 200)
                self.assertEqual(await cancel_response.json(), {"aborted": True})
            body = await stream_response.text()

        self.assertEqual(body, "data: [DONE]\n\n")
        self.assertEqual(self.edge_app[self.pe._CANCEL_EVENTS_KEY], {})


# ---------------------------------------------------------------------------
# Model allowlist / rewrite
# ---------------------------------------------------------------------------

class TestModelAllowlist(BaseEdgeTest):
    async def test_allowed_model_ok(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={"model": "pixel/default", "messages": []}) as resp:
            self.assertEqual(resp.status, 200)

    async def test_disallowed_model(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={"model": "gpt-4", "messages": []}) as resp:
            self.assertEqual(resp.status, 400)

    async def test_json_not_object(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json=["not", "an", "object"]) as resp:
            self.assertEqual(resp.status, 400)

    async def test_owner_message_gets_first_turn_delivery_contract(self):
        original = "Reply with exactly: READY"
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": original}],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        forwarded = self.up_runner.app["chat_requests"][-1]
        content = forwarded["messages"][-1]["content"]
        self.assertTrue(content.startswith(original))
        self.assertIn(self.pe._INTERACTIVE_DELIVERY_CONTRACT, content)

    async def test_multimodal_owner_message_keeps_parts_and_appends_contract(self):
        parts = [
            {"type": "text", "text": "Describe this image."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ]
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": parts}],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertEqual(content[:2], parts)
        self.assertEqual(content[-1]["type"], "text")
        self.assertIn("Answer the owner's complete message", content[-1]["text"])

    async def test_owner_chat_preserves_model_sampling_defaults(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "Check status."}],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        self.assertNotIn("temperature", self.up_runner.app["chat_requests"][-1])

    async def test_explicit_sampling_temperature_is_preserved(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "Write creatively."}],
                "temperature": 0.4,
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(self.up_runner.app["chat_requests"][-1]["temperature"], 0.4)

    async def test_host_inspection_gets_exact_replay_safe_observation_route(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": "Inspect this laptop OS, kernel, memory, disks, and processes.",
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertIn("Use the visible tool_call Tool Search control", content)
        self.assertEqual(content.count("id pixel_ods_host_observe"), 1)
        self.assertIn(
            'args {"actions":["host.os-release","host.kernel","host.memory",'
            '"host.storage","host.processes"]}',
            content,
        )
        for action in (
            "host.os-release",
            "host.kernel",
            "host.memory",
            "host.storage",
            "host.processes",
        ):
            self.assertIn(action, content)
        self.assertIn("This one read-only tool returns the terminal Operations receipt", content)
        self.assertIn("Generic sandbox commands and status projections", content)
        self.assertNotIn("pixel_ods_status", content)
        self.assertNotIn("pixel_ops_workflow_submit", content)
        self.assertNotIn("pixel_ops_job_wait", content)

    async def test_host_inspection_route_honors_network_disclosure_exclusion(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": (
                        "Inspect this laptop OS, CPU, memory, disks, services, and IP addresses. "
                        "Do not reveal IP addresses or other network location details."
                    ),
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertIn("id pixel_ods_host_observe", content)
        self.assertIn("host.os-release", content)
        self.assertIn("host.cpu", content)
        self.assertNotIn("host.network-addresses", content)
        self.assertNotIn("host.network-routes", content)
        self.assertNotIn("host.listening-ports", content)

    async def test_remote_target_guidance_does_not_prescribe_local_identity(self):
        for prompt in (
            "Check LAN target: physical Tower2 at 192.168.0.175. "
            "I obtained the address from a host route lookup. Verify remote hostname if authorized.",
            "Check the local host CPU and memory. Separately check reachability of nas on the LAN.",
        ):
            async with self.client.post(
                "http://localhost/v1/chat/completions", headers=self.auth(),
                json={"model": "pixel/default", "messages": [{"role": "user", "content": prompt}]},
            ) as resp:
                self.assertEqual(resp.status, 200)
            content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
            self.assertIn("host.network-peer", content)
            self.assertIn("independently requested local observations", content)
            self.assertNotIn('"actions":["host.identity"]', content)
            self.assertNotIn("exactly once", content)

    async def test_filesystem_resolution_is_not_a_remote_inspection_route(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions", headers=self.auth(),
            json={"model": "pixel/default", "messages": [{"role": "user", "content": "Resolve its path and check the module."}]},
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertNotIn("ODS Pixel network inspection route", content)

    async def test_code_about_a_machine_does_not_get_host_execution_route(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": "Write a test fixture describing a machine memory report.",
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertNotIn("pixel_ops_workflow_submit", content)

    async def test_website_preview_verification_does_not_get_host_inspection_route(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": (
                        "Build a polished interactive website in a new folder under your "
                        "workspace, inspect every file, and do not claim success until the "
                        "host can verify the preview."
                    ),
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertIn("[ODS assistant workspace task route:", content)
        self.assertNotIn("[ODS assistant host inspection route:", content)
        self.assertNotIn("pixel_ods_host_observe", content)

    async def test_workspace_mutation_gets_mutate_before_verify_route(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": "Create /workspace/demo/hello.txt, read it, and hash it.",
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertIn("use it with id write", content)
        self.assertIn("edit cannot create a file", content)
        self.assertIn("Perform the requested workspace mutation before verification", content)
        self.assertIn("Do not repeatedly list directories", content)

    async def test_run_and_wait_gets_one_exec_then_exact_process_poll_route(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": (
                        "Run this exact cancellable workspace command and wait for its real "
                        "result: python3 -c 'import time; time.sleep(30); print(\"done\")'."
                    ),
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertIn("ODS assistant command completion route", content)
        self.assertIn("Call exec exactly once", content)
        self.assertIn("tool_call control with id process", content)
        self.assertIn("action poll", content)
        self.assertIn("do not call exec again", content)

    async def test_exact_single_line_file_route_preserves_the_trailing_newline(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": (
                        "Create the file /workspace/demo/hello.txt with exactly this single line "
                        "followed by a newline: exact bytes. Then read it and hash it."
                    ),
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertIn("ODS assistant exact workspace route", content)
        self.assertIn("tool_call exactly once with id write", content)
        self.assertIn("tool_call once with id read", content)
        self.assertIn("tool_call once with id exec", content)
        self.assertIn(
            r'{"path":"/workspace/demo/hello.txt","content":"exact bytes.\n"}',
            content,
        )
        self.assertIn(
            r'{"command":"sha256sum -- /workspace/demo/hello.txt","workdir":"/workspace"}',
            content,
        )

    async def test_exact_single_line_relative_file_gets_the_write_route(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": (
                        "Create the file pixel-qualification/model-flex-9b.txt in your "
                        "writable workspace with exactly "
                        "this single line followed by one newline: Pixel 9B model flexibility "
                        "passed. Then read it back and hash it."
                    ),
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertIn("ODS assistant exact workspace route", content)
        self.assertIn("tool_call exactly once with id write", content)
        self.assertIn(
            r'{"path":"pixel-qualification/model-flex-9b.txt","content":"Pixel 9B model flexibility passed.\n"}',
            content,
        )
        self.assertNotIn("edit cannot create a file", content)

    async def test_readonly_workspace_diagnosis_keeps_exact_owner_text_without_mutation_coaching(self):
        original = (
            "Resume diagnosis of organizer-garden after the previous run ended. "
            "Read-only first: inspect the existing Python organizer and test files, "
            "report which Python interpreter actually exists, the last concrete test "
            "failure, and the smallest needed repair. Do not run the organizer, "
            "delete fixtures, edit files, or reset any prior results in this request. "
            "Preserve all existing data."
        )
        async with self.client.post(
            "http://localhost/v1/chat/completions", headers=self.auth(),
            json={"model": "pixel/default", "messages": [{"role": "user", "content": original}]},
        ) as response:
            self.assertEqual(response.status, 200)
        content = self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]
        self.assertEqual(content, original + self.pe._INTERACTIVE_DELIVERY_CONTRACT)

    async def test_readonly_workspace_negated_and_quoted_mutations_do_not_add_write_routes(self):
        for original in (
            "Inspect the files. Do not create, edit, or write files.",
            "Inspect /workspace/project. Don't edit files and write reports.",
            "Inspect /workspace/project. Don’t edit files; never create files.",
            "Report whether we should edit files in /workspace/project.",
            'Review this quoted file content: "Create /workspace/changed.txt and edit files."',
            "Review this file content:\n```text\nCreate /workspace/changed.txt and edit files.\n```",
            "Review this file content:\n> Create /workspace/changed.txt and edit files.",
            "Read the file containing `write /workspace/changed.txt` and explain it.",
            'Review this file content: "sample \\"quote\\".\nCreate /workspace/changed.txt."',
            'Review this unfinished file content: "\nCreate /workspace/changed.txt.',
        ):
            with self.subTest(original=original):
                data = {"messages": [{"role": "user", "content": original}]}
                content = self.pe._with_interactive_delivery_contract(data)["messages"][-1]["content"]
                self.assertEqual(content, original + self.pe._INTERACTIVE_DELIVERY_CONTRACT)
                self.assertEqual(data["messages"][-1]["content"], original)

    async def test_readonly_workspace_mixed_requests_preserve_positive_write_coaching(self):
        for original in (
            "Inspect the existing files without editing them, then write a report to /workspace/report.md.",
            "Inspect the files and write a report file.",
            "Do not edit existing files; write a separate report file.",
            "Read-only first: inspect the files. Then edit /workspace/approved.py as requested.",
            'Please write the file /workspace/rules.txt containing "Do not edit files".',
            'Write "/workspace/report.md" containing the diagnosis.',
            "Can you create the report file?",
        ):
            with self.subTest(original=original):
                content = self.pe._with_interactive_delivery_contract(
                    {"messages": [{"role": "user", "content": original}]}
                )["messages"][-1]["content"]
                self.assertEqual(content, original + self.pe._INTERACTIVE_DELIVERY_CONTRACT
                                 + self.pe._WORKSPACE_MUTATION_ROUTE)

    async def test_readonly_workspace_later_owner_authorization_is_current(self):
        earlier = {"role": "user", "content": "Read-only: inspect /workspace/project. Do not edit files."}
        current = {"role": "user", "content": "Now edit the file /workspace/project/fix.py."}
        data = {"messages": [earlier, {"role": "assistant", "content": "Diagnosis complete."}, current]}
        result = self.pe._with_interactive_delivery_contract(data)
        self.assertEqual(result["messages"][:-1], data["messages"][:-1])
        self.assertEqual(result["messages"][-1]["content"], current["content"]
                         + self.pe._INTERACTIVE_DELIVERY_CONTRACT + self.pe._WORKSPACE_MUTATION_ROUTE)
        self.assertEqual(data["messages"][-1], current)

    async def test_readonly_workspace_quoted_exact_write_cannot_replace_authorized_report(self):
        original = (
            'Inspect the file containing "Create the file /workspace/unrequested.txt with exactly '
            'this single line followed by a newline: wrong. Then read it and hash it." '
            'Then write a report file /workspace/report.md.'
        )
        content = self.pe._with_interactive_delivery_contract(
            {"messages": [{"role": "user", "content": original}]}
        )["messages"][-1]["content"]
        self.assertEqual(content, original + self.pe._INTERACTIVE_DELIVERY_CONTRACT
                         + self.pe._WORKSPACE_MUTATION_ROUTE)

    async def test_readonly_workspace_exact_file_content_remains_literal(self):
        original = (
            'Create the file /workspace/rules.txt with exactly this single line followed by a newline: '
            '"Do not edit files". Then read it and hash it.'
        )
        content = self.pe._with_interactive_delivery_contract(
            {"messages": [{"role": "user", "content": original}]}
        )["messages"][-1]["content"]
        self.assertTrue(content.startswith(original + self.pe._INTERACTIVE_DELIVERY_CONTRACT))
        self.assertIn("[ODS assistant exact workspace route:", content)
        self.assertIn(json.dumps({"path": "/workspace/rules.txt", "content": '"Do not edit files".\n'},
                                 separators=(",", ":")), content)

    async def test_readonly_workspace_multimodal_parts_remain_intact(self):
        parts = [
            {"type": "text", "text": "Inspect the files. Do not edit files."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ]
        data = {"messages": [{"role": "user", "content": parts}]}
        content = self.pe._with_interactive_delivery_contract(data)["messages"][-1]["content"]
        self.assertEqual(content[:-1], parts)
        self.assertEqual(content[-1], {"type": "text", "text": self.pe._INTERACTIVE_DELIVERY_CONTRACT.lstrip()})
        self.assertEqual(data["messages"][-1]["content"], parts)


# ---------------------------------------------------------------------------
# Header stripping
# ---------------------------------------------------------------------------

class TestHeaderStripping(BaseEdgeTest):
    async def test_blocked_headers_never_forwarded(self):
        from pixel_edge import _sanitize_headers, _HOP_BY_HOP

        inbound = {
            "Authorization": "Bearer client-token",
            "Cookie": "session=123",
            "X-Openclaw-Auth": "secret",
            "X-Openclaw-Session": "sess-1",
            "X-Openclaw-Token": "tok-1",
            "X-Openclaw-Future-Privileged": "must-also-be-blocked",
            "X-Forwarded-For": "1.2.3.4",
            "X-Forwarded-Host": "proxy",
            "X-Forwarded-Proto": "https",
            "Forwarded": "for=1.2.3.4",
            "Via": "proxy",
            "X-Real-Ip": "1.2.3.4",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "test",
            "X-Custom-Unsafe": "should-be-blocked",
        }
        sanitized = _sanitize_headers(inbound)
        keys = {k.lower() for k in sanitized}

        blocked = {
            "authorization", "cookie", "x-openclaw-auth", "x-openclaw-session",
            "x-openclaw-token", "x-openclaw-future-privileged", "x-forwarded-for", "x-forwarded-host",
            "x-forwarded-proto", "forwarded", "via", "x-real-ip",
        }
        for bh in blocked | _HOP_BY_HOP:
            self.assertNotIn(bh, keys, f"{bh} was forwarded")
        self.assertNotIn("x-custom-unsafe", keys)
        self.assertNotIn("Content-Type", sanitized)
        self.assertIn("Accept", sanitized)
        self.assertIn("User-Agent", sanitized)

    async def test_edge_never_forwards_browser_auth_to_upstream(self):
        seen = {}

        async def capture(request):
            seen["authorization"] = request.headers.get("Authorization", "")
            data = await request.json()
            seen["model"] = data.get("model")
            seen["cookie"] = request.headers.get("Cookie", "")
            seen["xopenclaw"] = request.headers.get("X-Openclaw-Auth", "")
            seen["xforwarded"] = request.headers.get("X-Forwarded-For", "")
            return web.json_response({"id": "1", "model": "openclaw/default", "choices": []})

        fd, path = tempfile.mkstemp(suffix=".cap.sock")
        os.close(fd)
        os.unlink(path)
        app = web.Application()
        app.router.add_post("/v1/chat/completions", capture)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.UnixSite(runner, path)
        await site.start()

        try:
            import importlib
            import pixel_edge
            os.environ["PIXEL_INGRESS_SOCKET"] = path
            importlib.reload(pixel_edge)

            cap_app = pixel_edge.create_app()
            cap_runner = web.AppRunner(cap_app)
            await cap_runner.setup()
            fd2, cap_sock = tempfile.mkstemp(suffix=".cap.edge.sock")
            os.close(fd2)
            os.unlink(cap_sock)
            cap_site = web.UnixSite(cap_runner, cap_sock)
            await cap_site.start()

            async with ClientSession(connector=UnixConnector(path=cap_sock)) as c:
                async with c.post(
                    "http://localhost/v1/chat/completions",
                    headers={**self.auth(), "Cookie": "session=secret",
                             "X-Openclaw-Auth": "secret-key",
                             "X-Forwarded-For": "1.2.3.4"},
                    json={"model": "pixel/default", "messages": []}) as resp:
                    self.assertEqual(resp.status, 200)

            self.assertFalse(seen.get("authorization"))
            self.assertEqual(seen.get("model"), "openclaw/default")
            self.assertFalse(seen.get("cookie"))
            self.assertFalse(seen.get("xopenclaw"))
            self.assertFalse(seen.get("xforwarded"))

            await cap_runner.cleanup()
            os.unlink(cap_sock)
        finally:
            os.environ["PIXEL_INGRESS_SOCKET"] = self.up_sock
            importlib.reload(pixel_edge)
            await runner.cleanup()
            os.unlink(path)


# ---------------------------------------------------------------------------
# Size limit
# ---------------------------------------------------------------------------

class TestSizeLimit(BaseEdgeTest):
    async def test_oversized_body_rejected(self):
        big = json.dumps({"model": "pixel/default",
                          "messages": [{"role": "user", "content": "x" * (2 * 1024 * 1024 + 1)}]})
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers={**self.auth(), "Content-Type": "application/json"},
            data=io.BytesIO(big.encode())) as resp:
            self.assertEqual(resp.status, 413)

    async def test_invalid_json(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers={**self.auth(), "Content-Type": "application/json"},
            data=b"{invalid json}") as resp:
            self.assertEqual(resp.status, 400)


# ---------------------------------------------------------------------------
# Synthetic model list
# ---------------------------------------------------------------------------

class TestSyntheticModels(BaseEdgeTest):
    async def test_models_is_synthetic(self):
        async with self.client.get("http://localhost/v1/models",
                                   headers=self.auth()) as resp:
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data["object"], "list")
            ids = [m["id"] for m in data["data"]]
            self.assertIn("pixel/default", ids)
            self.assertNotIn("openclaw/default", ids)
            for m in data["data"]:
                self.assertEqual(m["owned_by"], "pixel")


# ---------------------------------------------------------------------------
# Response rewrite
# ---------------------------------------------------------------------------

class TestResponseRewrite(BaseEdgeTest):
    async def test_live_activity_arrives_before_terminal_answer_without_leaking_extra_fields(self):
        async with self.client.post('http://localhost/v1/chat/completions',headers=self.auth(),json={'model':'pixel/default','stream':True,'trigger_live':True,'messages':[{'role':'user','content':'observe'}]}) as resp:
            try:
                async with async_timeout(2):
                    line = await resp.content.readline()
                self.assertIn(b'ods.task.activity',line)
                self.assertNotIn(b'must-not-leak',line)
                self.assertFalse(self.up_runner.app['release_stream'].is_set())
            finally:
                self.up_runner.app['release_stream'].set()
            remainder = await resp.text()
            self.assertIn('Verified reply',remainder)
            self.assertNotIn('must-not-leak',remainder)

    async def test_non_stream_model_rewritten(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={"model": "pixel/default", "messages": [{"role": "user", "content": "hi"}]}) as resp:
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data.get("model"), "pixel/default")
            self.assertEqual(
                data["choices"][0]["message"]["content"],
                "openclaw/default is assistant text",
            )

    async def test_non_stream_reserved_reply_becomes_natural_test_acknowledgement(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "testing 123"}],
                "trigger_reserved": True,
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(
                data["choices"][0]["message"]["content"],
                self.pe._SHORT_TEST_REPLY,
            )
            self.assertNotIn("OpenClaw", data["choices"][0]["message"]["content"])

    async def test_non_stream_reserved_reply_is_transparent_for_substantive_work(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "Refactor the parser and test it."}],
                "trigger_reserved": True,
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(
                data["choices"][0]["message"]["content"],
                self.pe._EMPTY_REPLY,
            )


# ---------------------------------------------------------------------------
# Private URL requests reach the capability-aware gateway
# ---------------------------------------------------------------------------

class TestPrivateUrlBoundary(BaseEdgeTest):
    async def test_non_stream_private_url_reaches_gateway(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "Open http://127.0.0.1:3000."}],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data["model"], "pixel/default")
            self.assertEqual(
                data["choices"][0]["message"]["content"],
                "openclaw/default is assistant text",
            )
        self.assertEqual(len(self.up_runner.app["chat_requests"]), 1)
        self.assertIn("http://127.0.0.1:3000", str(self.up_runner.app["chat_requests"][0]["messages"]))

    async def test_stream_private_url_reaches_gateway(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Tell me what's at "},
                        {"type": "text", "text": "http://dashboard.local/status"},
                    ],
                }],
                "stream": True,
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
            body = await resp.text()
            self.assertIn("openclaw/default is assistant text", body)
            self.assertIn('"model": "pixel/default"', body)
            self.assertIn('"finish_reason": "stop"', body)
            self.assertTrue(body.endswith("data: [DONE]\n\n"))
        self.assertEqual(len(self.up_runner.app["chat_requests"]), 1)
        self.assertIn("http://dashboard.local/status", str(self.up_runner.app["chat_requests"][0]["messages"]))

    async def test_public_url_forwards_normally(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "Read https://docs.python.org/3/."}],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(len(self.up_runner.app["chat_requests"]), 1)

    async def test_documentation_mention_of_private_url_forwards_normally(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": "Write documentation that mentions http://127.0.0.1:3000.",
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(len(self.up_runner.app["chat_requests"]), 1)

    async def test_coding_request_with_private_url_fixture_forwards_normally(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": "Write a test whose fixture calls http://127.0.0.1:3000.",
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(len(self.up_runner.app["chat_requests"]), 1)

    async def test_draft_then_access_private_url_reaches_gateway(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{
                    "role": "user",
                    "content": (
                        "Write a test for http://127.0.0.1:3000, then open the page "
                        "and tell me its title."
                    ),
                }],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(
                data["choices"][0]["message"]["content"],
                "openclaw/default is assistant text",
            )
        self.assertEqual(len(self.up_runner.app["chat_requests"]), 1)


# ---------------------------------------------------------------------------
# SSE incremental passthrough
# ---------------------------------------------------------------------------

class TestSSE(BaseEdgeTest):
    async def test_sse_streams_incrementally(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={"model": "pixel/default", "messages": [], "stream": True}) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
            self.assertEqual(resp.headers.get("Cache-Control"), "no-cache")

            collected = []
            async for chunk in resp.content.iter_any():
                collected.append(chunk)
                self.assertIsInstance(chunk, bytes)
                self.assertTrue(len(chunk) > 0)

            full = b"".join(collected).decode()
            self.assertIn('data: {"id": "1"', full)
            self.assertIn('data: [DONE]', full)
            self.assertIn('"model": "pixel/default"', full)
            self.assertIn('"content": "openclaw/default is assistant text"', full)

    async def test_fragmented_reserved_stream_becomes_one_visible_reply(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "testing 123"}],
                "stream": True,
                "trigger_reserved": True,
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            self.assertIn(self.pe._SHORT_TEST_REPLY, body)
            self.assertNotIn("No response from OpenClaw", body)
            self.assertIn('"model": "pixel/default"', body)
            self.assertIn('"finish_reason": "stop"', body)
            self.assertTrue(body.endswith("data: [DONE]\n\n"))

    async def test_stream_error_is_not_masked_by_the_empty_reply_fallback(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "testing 123"}],
                "stream": True,
                "trigger_stream_error": True,
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            self.assertIn("upstream failed", body)
            self.assertNotIn(self.pe._SHORT_TEST_REPLY, body)
            self.assertTrue(body.endswith("data: [DONE]\n\n"))


# ---------------------------------------------------------------------------
# Sanitized upstream errors
# ---------------------------------------------------------------------------

class TestSanitizedErrors(BaseEdgeTest):
    async def test_upstream_error_body_is_not_forwarded(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={"model": "pixel/default", "messages": [], "trigger_error": True},
        ) as resp:
            self.assertEqual(resp.status, 502)
            body = await resp.text()
            self.assertIn("pixel request rejected", body)
            self.assertNotIn("upstream-secret", body)
            self.assertNotIn("private/token", body)

    async def test_upstream_down_returns_502(self):
        # Point edge at a dead socket by overriding module global
        import pixel_edge
        old = pixel_edge._SOCKET_PATH
        dead = tempfile.mktemp(suffix=".dead.sock")
        pixel_edge._SOCKET_PATH = dead

        try:
            app = pixel_edge.create_app()
            runner = web.AppRunner(app)
            await runner.setup()
            fd, sock = tempfile.mkstemp(suffix=".dead.edge.sock")
            os.close(fd)
            os.unlink(sock)
            site = web.UnixSite(runner, sock)
            await site.start()

            async with ClientSession(connector=UnixConnector(path=sock)) as c:
                async with c.post(
                    "http://localhost/v1/chat/completions",
                    headers=self.auth(),
                    json={"model": "pixel/default", "messages": []}) as resp:
                    self.assertEqual(resp.status, 502)
                    data = await resp.json()
                    self.assertIn("error", data)
                    self.assertNotIn(dead, json.dumps(data))
                    self.assertNotIn("Traceback", json.dumps(data))

            await runner.cleanup()
            os.unlink(sock)
        finally:
            pixel_edge._SOCKET_PATH = old


class TestHostRequestIntent(BaseEdgeTest):
    async def forwarded_content(self, prompt):
        async with self.client.post(
            "http://localhost/v1/chat/completions", headers=self.auth(),
            json={"model": "pixel/default", "messages": [{"role": "user", "content": prompt}]},
        ) as resp:
            self.assertEqual(resp.status, 200)
            await resp.read()
        return self.up_runner.app["chat_requests"][-1]["messages"][-1]["content"]

    async def test_native_sandbox_request_does_not_revive_excluded_host_route(self):
        for prompt in [
            "The prior reply only reported the host OS and omitted the runtime checks. "
            "In your sandbox workspace, execute exactly a small shell availability check for "
            "node, nodejs, npm, bun, deno, python3 and git using command -v, plus pwd. "
            "Return the actual output. Do not inspect the host operating system or use the "
            "host inventory shortcut. No installations and no website.",
            "In the sandbox, check for Python. Don\u2019t inspect the host OS.",
        ]:
            content = await self.forwarded_content(prompt)
            self.assertIn(prompt, content)
            self.assertIn("[ODS assistant delivery requirement:", content)
            self.assertNotIn("[ODS assistant host inspection route:", content)

    async def test_host_route_uses_only_positive_facets(self):
        content = await self.forwarded_content(
            "Check this system CPU and memory, but do not inspect the host network addresses."
        )
        self.assertIn('args {"actions":["host.memory","host.cpu"]}', content)
        self.assertNotIn("host.network-addresses", content)

    async def test_positive_followup_does_not_restore_excluded_os(self):
        for prompt in [
            "Do not inspect the host OS. Check the kernel and memory.",
            "Don\u2019t inspect this machine OS, but report the kernel and memory.",
        ]:
            content = await self.forwarded_content(prompt)
            self.assertIn('args {"actions":["host.kernel","host.memory"]}', content)
            self.assertNotIn("host.os-release", content)


class TestChatActivity(BaseEdgeTest):
    async def activity(self, user):
        async with self.client.post("http://localhost/v1/chat/activity", headers=self.auth(), json={"user": user}) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
            return await resp.json()

    async def test_exact_chat_activity_tracks_active_then_terminal_without_global_inference(self):
        self.assertEqual(await self.activity("known-chat"), {"state": "unknown"})
        async with self.client.post("http://localhost/v1/chat/completions", headers=self.auth(), json={
            "model": "pixel/default", "messages": [{"role": "user", "content": "private content"}],
            "stream": True, "user": "known-chat", "trigger_cancel_wait": True,
        }) as stream:
            await asyncio.wait_for(self.up_runner.app["stream_started"].wait(), 1)
            self.assertEqual(await self.activity("known-chat"), {"state": "active"})
            self.assertEqual(await self.activity("different-chat"), {"state": "unknown"})
            self.up_runner.app["release_stream"].set()
            await stream.text()
        self.assertEqual(await self.activity("known-chat"), {"state": "terminal"})
        self.assertEqual(await self.activity("different-chat"), {"state": "unknown"})

    async def test_truncated_transport_is_unknown_and_exact_cancellation_is_terminal(self):
        for cancelled in (False, True):
            self.up_runner.app["stream_started"].clear()
            self.up_runner.app["release_stream"].clear()
            user = "cancelled-chat" if cancelled else "truncated-chat"
            self.assertEqual(await self.activity(user), {"state": "unknown"})
            async with self.client.post("http://localhost/v1/chat/completions", headers=self.auth(), json={
                "model": "pixel/default", "messages": [], "stream": True, "user": user,
                "trigger_cancel_wait": True, "trigger_cancel_eof": True,
            }) as stream:
                await asyncio.wait_for(self.up_runner.app["stream_started"].wait(), 1)
                self.assertEqual(await self.activity(user), {"state": "active"})
                if cancelled:
                    async with self.client.post("http://localhost/v1/chat/cancel", headers=self.auth(), json={"user": user}) as resp:
                        self.assertEqual(await resp.json(), {"aborted": True})
                self.up_runner.app["release_stream"].set()
                await stream.text()
            self.assertEqual(await self.activity(user), {"state": "terminal" if cancelled else "unknown"})

    async def test_activity_requires_exact_authenticated_bounded_request(self):
        async with self.client.post("http://localhost/v1/chat/activity", json={"user": "safe"}) as resp:
            self.assertEqual(resp.status, 401)
        for raw in ('{}', '{"user":"safe","other":"secret"}', '{"user":"../escape"}',
                    '{"user":true}', '{"user":"safe","user":"other"}', '{"user":"' + 'x' * 2048 + '"}'):
            async with self.client.post("http://localhost/v1/chat/activity", headers={**self.auth(), "Content-Type": "application/json"}, data=raw) as resp:
                self.assertEqual(resp.status, 400)
        async with self.client.post("http://localhost/v1/chat/activity?user=other", headers=self.auth(), json={"user": "safe"}) as resp:
            self.assertEqual(resp.status, 400)
        self.assertEqual(self.up_runner.app["chat_requests"], [])

    async def test_uncertain_sibling_and_bounded_history_never_invent_completion(self):
        app = self.edge_app
        first, second = asyncio.Event(), asyncio.Event()
        app[self.pe._CANCEL_EVENTS_KEY]["same-chat"] = {first, second}
        self.pe._finish_chat_activity(app, "same-chat", first, False)
        self.assertEqual(await self.activity("same-chat"), {"state": "active"})
        self.pe._finish_chat_activity(app, "same-chat", second, True)
        self.assertEqual(await self.activity("same-chat"), {"state": "unknown"})
        for index in range(1030):
            event = asyncio.Event()
            user = f"bounded-{index}"
            app[self.pe._CANCEL_EVENTS_KEY][user] = {event}
            self.pe._finish_chat_activity(app, user, event, True)
        self.assertLessEqual(len(app[self.pe._CHAT_ACTIVITY_KEY]), 1024)
        self.assertEqual(await self.activity("bounded-0"), {"state": "unknown"})

    async def test_disconnected_edge_keeps_native_run_unknown_and_exact_cancel_recovers_control(self):
        user = "detached-chat"
        self.assertEqual(await self.activity(user), {"state": "unknown"})
        stream = await self.client.post("http://localhost/v1/chat/completions", headers=self.auth(), json={
            "model": "pixel/default", "messages": [], "stream": True,
            "user": user, "trigger_detached_native": True,
        })
        await asyncio.wait_for(stream.content.readany(), 2)
        self.assertEqual(await self.activity(user), {"state": "active"})
        stream.close()
        async with async_timeout(2):
            while self.edge_app[self.pe._CANCEL_EVENTS_KEY].get(user):
                await asyncio.sleep(0.02)
        native = self.up_runner.app["native_runs"][user][1]
        self.assertFalse(native.done())
        self.assertEqual(await self.activity(user), {"state": "unknown"})
        async with self.client.post("http://localhost/v1/chat/cancel", headers=self.auth(), json={"user": "other-chat"}) as resp:
            self.assertEqual(await resp.json(), {"aborted": False})
        self.assertFalse(native.done())
        async with self.client.post("http://localhost/v1/chat/cancel", headers=self.auth(), json={"user": user}) as resp:
            self.assertEqual(await resp.json(), {"aborted": True})
        self.assertTrue(native.done())
        self.assertEqual(await self.activity(user), {"state": "terminal"})
        self.assertEqual(len(self.up_runner.app["chat_requests"]), 1)
        async with self.client.post("http://localhost/v1/chat/cancel", headers=self.auth(), json={"user": user}) as resp:
            self.assertEqual(await resp.json(), {"aborted": False})
        self.assertEqual(await self.activity(user), {"state": "terminal"})


if __name__ == "__main__":
    unittest.main()

