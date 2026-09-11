"""Gate qualification uses actual edge HTTP admission and held upstream streams."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, UnixConnector, web
from test_pixel_edge import TOKEN, _set_env, _start_upstream, _stop_upstream
from transition_gate import TransitionGate, initialize

OWNER = "owner-service-key-distinct-0123456789abcdef"
BINDING = "a" * 64
OTHER = "b" * 64


@unittest.skipUnless(os.name == "posix", "durable gate requires POSIX")
class TestTransitionGate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        os.chmod(self.directory.name, 0o700)
        initialize(self.directory.name)
        self.up_sock, self.up_runner = await _start_upstream()
        self.environment = patch.dict(os.environ, {
            "PIXEL_TRANSITION_STATE_DIR": self.directory.name,
            "PIXEL_PREVIEW_PROXY_KEY": OWNER,
        })
        self.environment.start()
        _set_env(self.up_sock)
        os.environ["PIXEL_PREVIEW_PROXY_KEY"] = OWNER
        import pixel_edge
        self.pe = importlib.reload(pixel_edge)
        await self._start_edge()

    async def _start_edge(self):
        self.app = self.pe.create_app()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        fd, self.sock = tempfile.mkstemp(suffix=".gate.sock")
        os.close(fd)
        os.unlink(self.sock)
        await web.UnixSite(self.runner, self.sock).start()
        self.client = ClientSession(connector=UnixConnector(path=self.sock))

    async def _stop_edge(self):
        await self.client.close()
        await self.runner.cleanup()
        if os.path.exists(self.sock):
            os.unlink(self.sock)

    async def asyncTearDown(self):
        self.up_runner.app["release_stream"].set()
        await self._stop_edge()
        await _stop_upstream(self.up_runner, self.up_sock)
        self.environment.stop()
        self.directory.cleanup()

    @staticmethod
    def auth(token=OWNER):
        return {"Authorization": f"Bearer {token}"}

    async def status(self):
        async with self.client.get("http://edge/v1/transition", headers=self.auth()) as response:
            self.assertEqual(response.status, 200)
            return await response.json()

    async def operation(self, operation, revision, token=BINDING):
        async with self.client.post("http://edge/v1/transition/" + operation,
                                    headers=self.auth(), json={"token": token, "revision": revision}) as response:
            return response.status, await response.json()

    async def chat(self, *, hold=False):
        return await self.client.post("http://edge/v1/chat/completions", headers=self.auth(TOKEN),
                                      json={"model": "pixel/default", "messages": [],
                                            "stream": hold, "trigger_cancel_wait": hold})

    async def test_status_requires_owner_credential_and_never_returns_binding(self):
        for headers in ({}, self.auth(TOKEN), self.auth("wrong")):
            async with self.client.get("http://edge/v1/transition", headers=headers) as response:
                self.assertEqual(response.status, 401)
            for operation in ("acquire", "release", "recover"):
                async with self.client.post("http://edge/v1/transition/" + operation,
                                            headers=headers, json={}) as response:
                    self.assertEqual(response.status, 401)
        initial = await self.status()
        self.assertEqual(initial["capability"], "available")
        code, result = await self.operation("acquire", initial["revision"])
        self.assertEqual(code, 200)
        self.assertFalse(result["host_runtime_verified"])
        self.assertNotIn(BINDING, json.dumps(result))
        self.assertNotIn("token_hash", json.dumps(await self.status()))

    async def test_strict_request_shapes_and_methods(self):
        revision = (await self.status())["revision"]
        bodies = [[], None, {}, {"token": BINDING}, {"token": BINDING, "revision": True},
                  {"token": "A" * 64, "revision": revision},
                  {"token": BINDING, "revision": revision, "command": "ignored"}]
        for operation in ("acquire", "release", "recover"):
            for body in bodies:
                async with self.client.post("http://edge/v1/transition/" + operation,
                                            headers=self.auth(), json=body) as response:
                    self.assertIn(response.status, (400, 415))
        raw = '{"token":"' + BINDING + '","token":"' + OTHER + '","revision":"' + revision + '"}'
        async with self.client.post("http://edge/v1/transition/acquire",
                                    headers={**self.auth(), "Content-Type": "application/json"}, data=raw) as response:
            self.assertEqual(response.status, 400)
        async with self.client.post("http://edge/v1/transition/acquire",
                                    headers={**self.auth(), "Content-Type": "application/json"}, data=" " * 1025) as response:
            self.assertEqual(response.status, 413)
        async with self.client.post("http://edge/v1/transition/acquire", headers=self.auth(), data="{}") as response:
            self.assertEqual(response.status, 415)
        async with self.client.get("http://edge/v1/transition?token=secret", headers=self.auth()) as response:
            self.assertEqual(response.status, 400)
        async with self.client.post("http://edge/v1/transition/override", headers=self.auth(), json={}) as response:
            self.assertEqual(response.status, 404)

    async def test_bound_retry_release_and_stale_replays(self):
        revision = (await self.status())["revision"]
        self.assertEqual((await self.operation("acquire", revision))[0], 200)
        self.assertEqual((await self.operation("acquire", revision))[0], 200)
        self.assertEqual((await self.operation("release", revision, OTHER))[0], 409)
        self.assertEqual((await self.operation("release", OTHER))[0], 409)
        blocked = await self.chat()
        self.assertEqual(blocked.status, 409)
        self.assertEqual(await blocked.json(), {"error": "pixel_transition_in_progress"})
        self.assertEqual(self.up_runner.app["chat_requests"], [])
        code, released = await self.operation("release", revision)
        self.assertEqual(code, 200)
        self.assertNotEqual(released["revision"], revision)
        self.assertEqual((await self.operation("release", revision))[0], 200)
        self.assertEqual((await self.operation("acquire", revision))[0], 409)
        self.assertEqual((await self.operation("acquire", released["revision"], OTHER))[0], 200)
        self.assertEqual((await self.operation("release", revision))[0], 409)
        self.assertEqual((await self.status())["phase"], "held")

    async def test_active_stream_refuses_acquisition_and_preserves_activity_schema(self):
        response = await self.chat(hold=True)
        await asyncio.wait_for(self.up_runner.app["stream_started"].wait(), 2)
        busy = await self.status()
        self.assertEqual(busy["phase"], "busy")
        self.assertEqual(busy["streams"], 1)
        code, result = await self.operation("acquire", busy["revision"])
        self.assertEqual((code, result), (409, {"error": "active_turns"}))
        async with self.client.get("http://edge/v1/activity", headers=self.auth(TOKEN)) as activity:
            self.assertEqual(await activity.json(), {"active": True, "streams": 1})
        self.up_runner.app["release_stream"].set()
        await response.read()
        for _ in range(20):
            idle = await self.status()
            if idle["phase"] == "idle":
                break
            await asyncio.sleep(0)
        self.assertEqual(idle["streams"], 0)
        self.assertEqual((await self.operation("acquire", idle["revision"]))[0], 200)

    async def test_simultaneous_acquisition_and_actual_chat_admission_are_exclusive(self):
        for chat_first in (False, True):
            revision = (await self.status())["revision"]
            gate = self.app[self.pe._TRANSITION_GATE_KEY]
            admitted_before = len(self.up_runner.app["chat_requests"])
            await gate.mutex.acquire()
            if chat_first:
                chat_task = asyncio.create_task(self.chat(hold=True))
                acquire_task = asyncio.create_task(self.operation("acquire", revision))
            else:
                acquire_task = asyncio.create_task(self.operation("acquire", revision))
                chat_task = asyncio.create_task(self.chat(hold=True))
            try:
                await asyncio.sleep(0.02)
                admitted_while_locked = len(self.up_runner.app["chat_requests"])
            finally:
                gate.mutex.release()
            response, (code, _) = await asyncio.gather(chat_task, acquire_task)
            if code == 200:
                self.assertEqual((await self.operation("release", revision))[0], 200)
            self.up_runner.app["release_stream"].set()
            await response.read()
            for _ in range(20):
                if (await self.status())["phase"] == "idle":
                    break
                await asyncio.sleep(0)
            self.up_runner.app["release_stream"].clear()
            self.assertEqual(admitted_while_locked, admitted_before,
                             "chat bypassed the shared admission/acquisition mutex")
            self.assertIn((response.status, code), ((200, 409), (409, 200)))

    async def test_two_simultaneous_owners_cannot_both_acquire(self):
        revision = (await self.status())["revision"]
        results = await asyncio.gather(self.operation("acquire", revision),
                                       self.operation("acquire", revision, OTHER))
        self.assertEqual(sorted(code for code, _ in results), [200, 409])

    async def test_held_gate_survives_restart_and_requires_exact_token(self):
        revision = (await self.status())["revision"]
        await self.operation("acquire", revision)
        await self._stop_edge()
        await self._start_edge()
        self.assertEqual((await self.status())["phase"], "held")
        self.assertEqual((await self.operation("release", revision, OTHER))[0], 409)
        self.assertEqual((await self.chat()).status, 409)
        self.assertEqual((await self.operation("release", revision))[0], 200)
        response = await self.chat()
        self.assertEqual(response.status, 200)
        await response.read()

    async def test_shutdown_during_stream_requires_explicit_recovery_to_held_state(self):
        response = await self.chat(hold=True)
        await self.up_runner.app["stream_started"].wait()
        await self.app.shutdown()  # Real application shutdown runs before handler cancellation.
        self.up_runner.app["release_stream"].set()
        await response.read()
        await self._stop_edge()
        await self._start_edge()
        interrupted = await self.status()
        self.assertEqual(interrupted["phase"], "interrupted")
        self.assertEqual((await self.chat()).status, 409)
        self.assertEqual((await self.operation("acquire", interrupted["revision"]))[0], 409)
        code, result = await self.operation("recover", interrupted["revision"])
        self.assertEqual(code, 200)
        self.assertEqual(result["phase"], "held")
        self.assertTrue(result["admission_blocked"])
        self.assertFalse(result["host_runtime_verified"])
        self.assertEqual((await self.operation("release", interrupted["revision"]))[0], 200)

    async def test_process_crash_retains_busy_marker(self):
        await self._stop_edge()
        program = """import asyncio, os, sys
from transition_gate import TransitionGate
gate = TransitionGate(sys.argv[1], set())
asyncio.run(gate.admit(object()))
os._exit(0)
"""
        subprocess.run([sys.executable, "-c", program, self.directory.name],
                       cwd=Path(__file__).resolve().parents[1], check=True)
        await self._start_edge()
        self.assertEqual((await self.status())["phase"], "interrupted")
        self.assertEqual((await self.chat()).status, 409)

    async def test_corrupt_or_removed_state_never_silently_resets(self):
        state = Path(self.directory.name, "transition.json")
        state.write_text("{broken", encoding="utf-8")
        self.assertEqual((await self.status())["capability"], "unavailable")
        self.assertEqual((await self.chat()).status, 503)
        await self._stop_edge()
        state.unlink()
        await self._start_edge()
        self.assertEqual((await self.status())["capability"], "unavailable")
        self.assertFalse(state.exists())
        self.assertEqual((await self.operation("acquire", OTHER))[0], 503)

    async def test_failed_durable_write_blocks_chat_and_transition(self):
        revision = (await self.status())["revision"]
        with patch("transition_gate.os.replace", side_effect=OSError("private path secret")):
            code, result = await self.operation("acquire", revision)
        self.assertEqual(code, 503)
        self.assertNotIn("private path", json.dumps(result))
        self.assertEqual((await self.chat()).status, 503)
        self.assertEqual((await self.status())["capability"], "unavailable")

    async def test_second_process_instance_cannot_bypass_live_gate(self):
        revision = (await self.status())["revision"]
        await self.operation("acquire", revision)
        second = TransitionGate(self.directory.name, set())
        try:
            self.assertEqual((await second.status())["capability"], "unavailable")
            self.assertEqual((await self.status())["phase"], "held")
        finally:
            second.close()

    async def test_default_disabled_status_is_truthful_and_chat_still_works(self):
        await self._stop_edge()
        os.environ.pop("PIXEL_TRANSITION_STATE_DIR")
        await self._start_edge()
        disabled = await self.status()
        self.assertEqual(disabled["capability"], "disabled")
        self.assertFalse(disabled["admission_blocked"])
        self.assertEqual((await self.operation("acquire", OTHER))[0], 503)
        response = await self.chat()
        self.assertEqual(response.status, 200)
        await response.read()

    async def test_shared_chat_owner_credential_cannot_enable_gate(self):
        await self._stop_edge()
        with patch.object(self.pe, "preview_proxy_token", TOKEN):
            await self._start_edge()
        self.assertEqual((await self.status())["capability"], "unavailable")
        self.assertEqual((await self.chat()).status, 503)

    async def test_symlink_or_unsafe_permissions_are_unavailable(self):
        await self._stop_edge()
        state = Path(self.directory.name, "transition.json")
        original = state.read_bytes()
        state.unlink()
        target = Path(self.directory.name, "real-state")
        target.write_bytes(original)
        state.symlink_to(target)
        await self._start_edge()
        self.assertEqual((await self.status())["capability"], "unavailable")
        await self._stop_edge()
        state.unlink()
        state.write_bytes(original)
        os.chmod(state, 0o644)
        await self._start_edge()
        self.assertEqual((await self.status())["capability"], "unavailable")

    async def test_initialization_refuses_existing_state(self):
        before = Path(self.directory.name, "transition.json").read_bytes()
        with self.assertRaises(OSError):
            initialize(self.directory.name)
        self.assertEqual(Path(self.directory.name, "transition.json").read_bytes(), before)
