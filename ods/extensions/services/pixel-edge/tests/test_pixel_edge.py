"""Tests for pixel_edge — upstream Unix socket + edge proxy routes."""

import asyncio
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


# ---------------------------------------------------------------------------
# Mock upstream on a Unix socket
# ---------------------------------------------------------------------------

async def _upstream_chat(request):
    data = await request.json()
    request.app["chat_requests"].append(data)
    stream = data.get("stream", False)

    if data.get("trigger_error"):
        return web.json_response({"error": "upstream-secret-path-/private/token"}, status=500)

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
    if request.app["release_on_cancel"]:
        asyncio.get_running_loop().call_later(0.05, request.app["release_stream"].set)
    return web.json_response({"aborted": True})


async def _upstream_preview(request):
    request.app["preview_hosts"].append(request.headers.get("Host"))
    site_id = "site-" + "a" * 24
    if request.match_info.get("site_id") != site_id:
        return web.Response(status=404)
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
    async def test_preview_requires_the_dashboard_proxy_token(self):
        site_id = "site-" + "a" * 24
        async with self.client.get(f"http://localhost/preview/{site_id}/") as resp:
            self.assertEqual(resp.status, 401)

    async def test_preview_relays_exact_bytes_with_an_opaque_browser_sandbox(self):
        site_id = "site-" + "a" * 24
        async with self.client.get(
            f"http://localhost/preview/{site_id}/", headers=self.auth()
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
            self.assertIn("sandbox allow-scripts", csp)
            self.assertNotIn("allow-same-origin", csp)
            self.assertIn("frame-ancestors 'self'", csp)
        self.assertEqual(self.up_runner.app["preview_hosts"], ["pixel-preview.internal"])

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

    async def test_owner_chat_defaults_to_deterministic_sampling(self):
        async with self.client.post(
            "http://localhost/v1/chat/completions",
            headers=self.auth(),
            json={
                "model": "pixel/default",
                "messages": [{"role": "user", "content": "Check status."}],
            },
        ) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(self.up_runner.app["chat_requests"][-1]["temperature"], 0)

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
        self.assertIn("[ODS Pixel workspace task route:", content)
        self.assertNotIn("[ODS Pixel host inspection route:", content)
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
        self.assertIn("ODS Pixel command completion route", content)
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
        self.assertIn("ODS Pixel exact workspace route", content)
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
        self.assertIn("ODS Pixel exact workspace route", content)
        self.assertIn("tool_call exactly once with id write", content)
        self.assertIn(
            r'{"path":"pixel-qualification/model-flex-9b.txt","content":"Pixel 9B model flexibility passed.\n"}',
            content,
        )
        self.assertNotIn("edit cannot create a file", content)


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
# Private URL boundary
# ---------------------------------------------------------------------------

class TestPrivateUrlBoundary(BaseEdgeTest):
    async def test_non_stream_private_url_returns_exact_local_reply(self):
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
                self.pe._PRIVATE_URL_REPLY,
            )
        self.assertEqual(self.up_runner.app["chat_requests"], [])

    async def test_stream_private_url_returns_exact_local_reply(self):
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
            self.assertIn(self.pe._PRIVATE_URL_REPLY, body)
            self.assertIn('"model": "pixel/default"', body)
            self.assertIn('"finish_reason": "stop"', body)
            self.assertTrue(body.endswith("data: [DONE]\n\n"))
        self.assertEqual(self.up_runner.app["chat_requests"], [])

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

    async def test_draft_then_access_private_url_is_still_blocked(self):
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
                self.pe._PRIVATE_URL_REPLY,
            )
        self.assertEqual(self.up_runner.app["chat_requests"], [])


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


if __name__ == "__main__":
    unittest.main()
