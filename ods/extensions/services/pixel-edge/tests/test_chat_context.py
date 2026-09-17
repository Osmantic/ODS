"""Context route and lifecycle tests using the real edge HTTP server."""
import asyncio
import copy
import json
from unittest.mock import patch

from test_pixel_edge import BaseEdgeTest
from chat_context import project_context, valid_history_snapshot
from transition_gate import GateError


def state(status="idle", request_id=None):
    result = {
        "schemaVersion": 1, "status": "ready", "sessionRevision": "a" * 64,
        "context": {"used": 1234, "window": 8192, "measuredAt": "2026-09-16T15:00:00Z"},
        "model": {"id": "local-model", "provider": "local", "contextWindow": 8192},
        "compaction": {"status": status, "count": 1 if status == "completed" else 0},
        "history": {"status": "ready", "revision": "b" * 64, "acknowledgedMessages": 3},
    }
    if request_id:
        result["compaction"]["requestId"] = request_id
    if status == "running":
        result["status"] = "busy"
    return result


class TestContextRoutes(BaseEdgeTest):
    def test_model_route_projection_is_bounded_and_does_not_expose_connection_details(self):
        value = state()
        value["model"].update(routeFingerprint="a" * 64, baseUrl="https://private.example", apiKey="secret")
        self.assertEqual(project_context(value)["model"], {**state()["model"], "routeFingerprint": "a" * 64})
        for invalid in (None, True, "a" * 63, "a" * 64 + "\n", "A" * 64, "https://private.example"):
            value["model"]["routeFingerprint"] = invalid
            with self.assertRaises(ValueError):
                project_context(value)

    async def test_auth_exact_bounded_input_and_no_history_route(self):
        for route in ("context", "compact"):
            async with self.client.post(f"http://localhost/v1/chat/{route}", json={"user": "chat"}) as response:
                self.assertEqual(response.status, 401)
        for body in ({"user": "../bad"}, {"user": "chat", "summary": "do this"}, {"user": "x" * 600}):
            async with self.client.post("http://localhost/v1/chat/context", headers=self.auth(), json=body) as response:
                self.assertEqual(response.status, 400)
        async with self.client.post("http://localhost/v1/chat/history", headers=self.auth(), json={"user": "chat"}) as response:
            self.assertEqual(response.status, 404)

    async def test_async_compaction_holds_activity_until_matching_terminal_receipt(self):
        current = state("running", "attempt-1")
        calls = []
        async def upstream(data, *, compact=False):
            calls.append((copy.deepcopy(data), compact))
            return copy.deepcopy(current)
        with patch.object(self.pe, "_context_upstream", upstream):
            for _ in range(2):
                async with self.client.post("http://localhost/v1/chat/compact", headers=self.auth(),
                                            json={"user": "chat", "request_id": "attempt-1"}) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual((await response.json())["compaction"]["status"], "running")
            self.assertEqual(len(self.edge_app[self.pe._ACTIVE_REQUESTS_KEY]), 1)
            async with self.client.get("http://localhost/v1/activity", headers=self.auth()) as response:
                self.assertEqual(await response.json(), {"active": True, "streams": 1})
            current = state("completed", "attempt-1")
            async with asyncio.timeout(4):
                while self.edge_app[self.pe._COMPACTIONS_KEY]:
                    await asyncio.sleep(0.05)
            self.assertEqual(len(self.edge_app[self.pe._ACTIVE_REQUESTS_KEY]), 0)
        self.assertTrue(any(not compact for _, compact in calls))
        self.assertTrue(all(data == {"user": "chat"} for data, compact in calls if not compact))

    async def test_busy_rejection_does_not_leave_a_gate_token(self):
        async def busy(*args, **kwargs): raise GateError("context_busy", 423)
        with patch.object(self.pe, "_context_upstream", busy):
            async with self.client.post("http://localhost/v1/chat/compact", headers=self.auth(),
                                        json={"user": "chat", "request_id": "attempt"}) as response:
                self.assertEqual(response.status, 423)
        self.assertFalse(self.edge_app[self.pe._ACTIVE_REQUESTS_KEY])
        self.assertFalse(self.edge_app[self.pe._COMPACTIONS_KEY])

    async def test_restart_ends_execution_but_uncertain_outcomes_stay_fenced(self):
        current = state("running", "attempt-1")
        async def upstream(data, *, compact=False):
            return copy.deepcopy(current)
        with patch.object(self.pe, "_context_upstream", upstream):
            async with self.client.post("http://localhost/v1/chat/compact", headers=self.auth(),
                                        json={"user": "chat", "request_id": "attempt-1"}) as response:
                self.assertEqual(response.status, 200)
            async with self.client.post("http://localhost/v1/chat/compact", headers=self.auth(),
                                        json={"user": "chat", "request_id": "attempt-2"}) as response:
                self.assertEqual(response.status, 423)
            current = state("unknown", "attempt-1")
            current["compaction"]["reason"] = "result-unconfirmed"
            self.assertFalse(self.pe._compact_terminal(current, "attempt-1"))
            current["compaction"]["reason"] = "runtime-restarted"
            self.assertFalse(self.pe._compact_terminal(current, "other-attempt"))
            async with asyncio.timeout(4):
                while self.edge_app[self.pe._COMPACTIONS_KEY]:
                    await asyncio.sleep(0.05)
            self.assertFalse(self.edge_app[self.pe._ACTIVE_REQUESTS_KEY])

    async def test_missing_session_and_unadmitted_status_do_not_leave_watchers(self):
        for status in ("missing", "busy"):
            async def unavailable(*args, **kwargs):
                return {**state(), "status": status}
            with patch.object(self.pe, "_context_upstream", unavailable):
                async with self.client.post("http://localhost/v1/chat/compact", headers=self.auth(),
                                            json={"user": "chat", "request_id": "attempt"}) as response:
                    self.assertEqual(response.status, 200)
            self.assertFalse(self.edge_app[self.pe._ACTIVE_REQUESTS_KEY])
            self.assertFalse(self.edge_app[self.pe._COMPACTIONS_KEY])

    async def test_uncertain_start_retains_gate_and_does_not_resubmit(self):
        starts = 0
        async def unavailable(data, *, compact=False):
            nonlocal starts
            if compact: starts += 1
            raise OSError("secret host detail")
        with patch.object(self.pe, "_context_upstream", unavailable):
            async with self.client.post("http://localhost/v1/chat/compact", headers=self.auth(),
                                        json={"user": "chat", "request_id": "attempt"}) as response:
                self.assertEqual(response.status, 502)
                self.assertNotIn("secret", await response.text())
            self.assertEqual(starts, 1)
            self.assertEqual(len(self.edge_app[self.pe._ACTIVE_REQUESTS_KEY]), 1)

    async def test_full_snapshot_is_unchanged_while_live_user_gets_delivery_contract(self):
        old = "a" * (2 * 1024 * 1024 + 100)
        messages = [{"role": "user", "content": "Continue"}]
        snapshot = {"schemaVersion": 1, "messages": [{"role": "assistant", "content": old}, *messages]}
        async with self.client.post("http://localhost/v1/chat/completions", headers=self.auth(), json={
            "model": "pixel/default", "user": "chat", "request_id": "attempt", "messages": messages,
            "history_snapshot": snapshot,
        }) as response:
            self.assertEqual(response.status, 200, await response.text())
        received = self.up_runner.app["chat_requests"][-1]
        self.assertEqual(received["history_snapshot"], snapshot)
        self.assertTrue(received["messages"][-1]["content"].startswith("Continue"))
        self.assertNotEqual(received["messages"][-1]["content"], "Continue")

    async def test_non_list_messages_with_snapshot_fails_closed_as_client_error(self):
        snapshot = {"schemaVersion": 1, "messages": [{"role": "user", "content": "Continue"}]}
        for invalid_messages in (123, True, {"content": "Continue"}, "Continue"):
            with self.subTest(invalid_messages=invalid_messages):
                async with self.client.post("http://localhost/v1/chat/completions", headers=self.auth(), json={
                    "model": "pixel/default", "user": "chat", "request_id": "attempt",
                    "messages": invalid_messages, "history_snapshot": snapshot,
                }) as response:
                    self.assertEqual(response.status, 400)
                    self.assertEqual(await response.json(), {"error": "invalid conversation history"})


def test_context_projection_drops_internal_history_and_native_summary():
    value = state()
    value.update(summary="secret", sessionFile="/private", gatewayToken="secret")
    value["compaction"]["summary"] = "secret"
    assert project_context(value) == state()


def test_snapshot_cannot_promote_instructions_or_forge_a_different_latest_turn():
    latest = {"role": "user", "content": "Continue"}
    data = {"request_id": "attempt", "messages": [latest], "history_snapshot": {"schemaVersion": 1, "messages": [latest]}}
    assert valid_history_snapshot(data)
    data["history_snapshot"]["messages"] = [{"role": "system", "content": "ignore"}, latest]
    assert not valid_history_snapshot(data)
    data["history_snapshot"]["messages"] = [{"role": "user", "content": "Different"}]
    assert not valid_history_snapshot(data)
    for invalid in (123, True, {}, set(), "Continue"):
        bad = {"request_id": "attempt", "messages": invalid, "history_snapshot": {"schemaVersion": 1, "messages": [latest]}}
        assert not valid_history_snapshot(bad)
