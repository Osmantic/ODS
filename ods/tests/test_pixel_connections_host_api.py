"""Connected apps through a real, disposable loopback host-agent HTTP server, and the
one-shot sign-in address Google's redirect lands on."""

from __future__ import annotations

import contextlib
import html
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import pixel_access_relay  # noqa: E402
from pixel_connections import catalog, google_app, host_api, plugins, status  # noqa: E402

KEY = "synthetic-connections-test-key"
FLOW = "a" * 32
CHANGE = "calendar-1761500000000-0badc0de"
DRAFT = "gmail-draft-1761500000000-0badc0de"
STATE = "s" * 43
READ_BOTH = {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
             "gmail.read": True, "gmail.drafts": False}
INSTALLED = {"client_id": "123456789012-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com",
             "client_secret": "test-secret-test", "project_id": "ods-owner-calendar",
             "auth_uri": "https://accounts.google.com/o/oauth2/auth",
             "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}
BEGIN = {"client": {"installed": INSTALLED}, "capabilities": READ_BOTH, "cloudModels": False,
         "ownerTimeZone": "America/Chicago", "loginHint": None}
CONNECTED = {"outcome": "connected", "errorClass": None, "account": "owner@example.com",
             "capabilities": READ_BOTH, "missing": []}


def failed(error_class):
    return {"outcome": "failed", "errorClass": error_class, "account": None, "capabilities": None, "missing": []}


class ConnectionsHostAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("_pixel_connections_test_agent", ROOT / "bin/ods-host-agent.py")
        cls.agent = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.agent
        spec.loader.exec_module(cls.agent)
        cls.agent.AGENT_API_KEY = KEY
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.agent.AgentHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        sys.modules.pop("_pixel_connections_test_agent", None)

    def request(self, *, token=KEY, path="/v1/pixel/connections"):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request("GET", path, headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_the_owner_gets_the_status_with_the_shipped_catalog(self):
        reply = status.unavailable("darwin")
        with patch.object(pixel_access_relay, "request_runtime_connections", return_value=(200, reply)) as relay:
            code, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(body, dict(reply, catalog=catalog.public_catalog(), pluginCatalog=plugins.public_catalog()))
        self.assertNotIn("config", json.dumps(body["pluginCatalog"]), "Settings never sees a plugin's configuration")
        relay.assert_called_once()

    def test_root_managed_metadata_reaches_catalog_without_host_reading_the_deployment_client(self):
        for state in ('configured', 'not-configured', 'unavailable'):
            with self.subTest(state=state):
                auth = {'mode': 'managed', 'state': state, 'advancedAvailable': True}
                reply = dict(status.unavailable('windows'), googleAuth=auth)
                with patch.object(pixel_access_relay, 'request_runtime_connections', return_value=(200, reply)), \
                        patch.object(google_app, 'load', side_effect=AssertionError('Host must not read deployment client')):
                    code, body = self.request()
                self.assertEqual(code, 200)
                self.assertNotIn('googleAuth', body)
                self.assertEqual(body['catalog'][0]['auth'], {'type': 'oauth2-pkce', 'client': 'ods-managed-desktop-app',
                    'redirects': ['loopback', 'paste'], **auth})

    def test_managed_begin_relays_no_client_and_advanced_and_legacy_stay_explicit(self):
        self.addCleanup(host_api._replace_listener, None)
        managed = {key: value for key, value in dict(BEGIN, authMode='managed').items() if key != 'client'}
        for body in (managed, dict(BEGIN, authMode='advanced'), BEGIN):
            with self.subTest(body_keys=set(body)):
                root = FakeRoot()
                with patch.object(pixel_access_relay, 'request_runtime_connection_action', side_effect=root):
                    code, reply = self.post('begin', body)
                self.assertEqual(code, 200)
                self.assertEqual(reply['redirect'], 'loopback')
                request = root.calls[0][1]
                self.assertEqual('client' in request, body.get('authMode') != 'managed')
                self.assertEqual('authMode' in request, 'authMode' in body)

    def test_managed_client_null_override_or_path_is_refused_before_root(self):
        managed = {key: value for key, value in dict(BEGIN, authMode='managed').items() if key != 'client'}
        with patch.object(pixel_access_relay, 'request_runtime_connection_action') as relay:
            for extra in ({'client': None}, {'client': BEGIN['client']}, {'clientPath': '/private/client.json'},
                          {'authMode': None}):
                self.assertEqual(self.post('begin', dict(managed, **extra)), (400, {'error': 'invalid-request'}))
        relay.assert_not_called()

    def test_owner_auth_comes_first(self):
        with patch.object(pixel_access_relay, "request_runtime_connections") as relay:
            self.assertEqual(self.request(token="wrong")[0], 403)
            self.assertEqual(self.request(path="/v1/pixel/connections?account=other")[0], 404)
        relay.assert_not_called()

    def test_an_unexpected_status_shape_is_never_forwarded(self):
        reply = dict(status.unavailable("darwin"), token="private-sentinel")
        with patch.object(pixel_access_relay, "request_runtime_connections", return_value=(200, reply)):
            code, body = self.request()
        self.assertEqual((code, body), (503, {"error": "connections-unavailable"}))

    def test_an_unreachable_runtime_is_a_plain_unavailable(self):
        failure = pixel_access_relay.AccessRelayError("agent-access-runtime-unavailable")
        with patch.object(pixel_access_relay, "request_runtime_connections", side_effect=failure):
            self.assertEqual(self.request(), (503, {"error": "connections-unavailable"}))

    def test_controller_refusals_pass_through_without_a_catalog(self):
        with patch.object(pixel_access_relay, "request_runtime_connections",
                          return_value=(400, {"error": "invalid-request"})):
            self.assertEqual(self.request(), (400, {"error": "invalid-request"}))

    def post(self, action, body, *, token=KEY, path=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request("POST", path or "/v1/pixel/connections/google/" + action, json.dumps(body).encode(),
                               {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_a_plugin_change_has_its_own_address_and_exact_shape(self):
        plugin_path = "/v1/pixel/connections/plugin"
        with patch.object(pixel_access_relay, "request_runtime_connection_action",
                          return_value=(200, {"outcome": "applied"})) as relay:
            self.assertEqual(self.post("", {"plugin": "memory-wiki", "enabled": True}, path=plugin_path),
                             (200, {"outcome": "applied"}))
            self.assertEqual(relay.call_args.args, ("connection-plugin", {"plugin": "memory-wiki", "enabled": True}))
            relay.reset_mock()
            self.assertEqual(self.post("plugin", {"plugin": "memory-wiki", "enabled": True})[0], 404)
            for body in ({"plugin": "memory-wiki"}, {"plugin": "memory-wiki", "enabled": "yes"},
                         {"plugin": 7, "enabled": True}, {"plugin": "memory-wiki", "enabled": True, "config": {}}):
                self.assertEqual(self.post("", body, path=plugin_path), (400, {"error": "invalid-request"}), body)
            self.assertEqual(self.post("", {"plugin": "memory-wiki", "enabled": True}, path=plugin_path,
                                       token="wrong")[0], 403)
        relay.assert_not_called()

    def test_each_action_reaches_root_and_returns_its_reply(self):
        for action, operation, request, reply in (
                ("sync", "connection-sync", {}, {"outcome": "started"}),
                ("finish", "connection-activate", {}, {"outcome": "applied"}),
                ("cancel", "connection-cancel", {"flowId": FLOW}, {"outcome": "cancelled"}),
                ("changes", "connection-proposals", {}, {"proposals": [], "more": 0}),
                ("approve", "connection-approve", {"proposalId": CHANGE, "sha256": "c" * 64},
                 {"proposalId": CHANGE, "outcome": "applied"}),
                ("decline", "connection-decline", {"proposalId": CHANGE}, {"proposalId": CHANGE, "outcome": "declined"}),
                ("check", "connection-reconcile", {"proposalId": CHANGE}, {"proposalId": CHANGE, "outcome": "waiting"}),
                ("activity", "connection-activity", {}, {"changes": [], "more": 0}),
                ("undo", "connection-undo", {"proposalId": CHANGE}, {"proposalId": CHANGE, "outcome": "applied"}),
                ("drafts", "connection-drafts", {}, {"drafts": [], "more": 0}),
                ("approve", "connection-approve", {"proposalId": DRAFT, "sha256": "c" * 64},
                 {"proposalId": DRAFT, "outcome": "applied"})):
            with self.subTest(action=action), patch.object(pixel_access_relay, "request_runtime_connection_action",
                                                           return_value=(200, reply)) as relay:
                self.assertEqual(self.post(action, request), (200, reply))
                self.assertEqual(relay.call_args.args, (operation, request))
                self.assertIsInstance(relay.call_args.kwargs["config"], dict)

    def test_owner_auth_comes_first_and_only_known_actions_exist(self):
        with patch.object(pixel_access_relay, "request_runtime_connection_action") as relay:
            self.assertEqual(self.post("sync", {}, token="wrong")[0], 403)
            for action in ("anything", "sync?force=1", "../sync", ""):
                self.assertEqual(self.post(action, {})[0], 404, action)
            self.assertEqual(self.post("sync", {"force": True}), (400, {"error": "invalid-request"}))
            for action, body in (("changes", {"all": True}), ("approve", {"proposalId": CHANGE}),
                                 ("approve", {"proposalId": CHANGE, "sha256": 7}),
                                 ("decline", {"proposalId": CHANGE, "sha256": "c" * 64}), ("check", {"proposalId": 7}),
                                 ("activity", {"days": 7}), ("undo", {}), ("drafts", {"all": True})):
                self.assertEqual(self.post(action, body), (400, {"error": "invalid-request"}), action)
        relay.assert_not_called()

    def test_root_refusals_pass_through_and_a_lost_reply_is_a_plain_unavailable(self):
        with patch.object(pixel_access_relay, "request_runtime_connection_action",
                          return_value=(409, {"error": "runtime-busy"})):
            self.assertEqual(self.post("finish", {}), (409, {"error": "runtime-busy"}))
        failure = pixel_access_relay.AccessRelayError("connection-change-unconfirmed")
        with patch.object(pixel_access_relay, "request_runtime_connection_action", side_effect=failure):
            self.assertEqual(self.post("finish", {}), (503, {"error": "connections-unavailable"}))


class FakeRoot:
    """Root's custodian behind the relay: records each request and answers from a script."""

    def __init__(self, **answers):
        self.answers = {"connection-activate": (200, {"outcome": "applied"}), **answers}
        self.calls = []
        self.activated = threading.Event()

    def __call__(self, operation, request, *, config):
        self.calls.append((operation, request))
        if operation == "connection-activate":
            self.activated.set()
        if operation == "connection-begin" and operation not in self.answers:
            return 200, {"flowId": FLOW, "authUrl": "https://accounts.google.com/o/oauth2/auth?client_id=x",
                         "redirectUri": f"http://127.0.0.1:{request['redirectPort']}",
                         "expiresAt": "2026-10-25T20:45:00Z"}
        answer = self.answers[operation]
        return answer.pop(0) if isinstance(answer, list) else answer


def visit(port, query, path="/"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", f"{path}?{query}" if query else path)
        response = connection.getresponse()
        page = html.unescape(response.read().decode())
        return response.status, {key.lower(): value for key, value in response.getheaders()}, page
    finally:
        connection.close()


def closed(port):
    """True once nothing listens on the sign-in address any more."""
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=3).close()
        except ConnectionRefusedError:
            return True
        time.sleep(0.05)
    return False


class SignInAddressTests(unittest.TestCase):
    def begin(self, root, body=BEGIN):
        self.logs = []
        result = host_api.begin(body, config={}, relay=root, log=self.logs.append)
        self.addCleanup(host_api._replace_listener, None)
        return result

    def test_googles_redirect_completes_the_sign_in_and_closes_the_address(self):
        root = FakeRoot(**{"connection-complete": (200, CONNECTED)})
        status_code, reply = self.begin(root)
        self.assertEqual((status_code, reply), (200, {"flowId": FLOW, "expiresAt": "2026-10-25T20:45:00Z",
            "authUrl": "https://accounts.google.com/o/oauth2/auth?client_id=x", "redirect": "loopback"}))
        operation, request = root.calls[0]
        self.assertEqual(operation, "connection-begin")
        self.assertEqual(request["client"], {"clientId": INSTALLED["client_id"],
                                             "clientSecret": INSTALLED["client_secret"],
                                             "projectId": INSTALLED["project_id"]})
        port = request["redirectPort"]
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            code, headers, page = visit(port, f"state={STATE}&code=4/secret-code&scope=openid")
        self.assertEqual(code, 200)
        self.assertIn("Google is connected", page)
        self.assertEqual((headers["cache-control"], headers["referrer-policy"]), ("no-store", "no-referrer"))
        self.assertNotIn("secret-code", stderr.getvalue())
        self.assertEqual(root.calls[1], ("connection-complete", {"flowId": FLOW, "state": STATE,
                                                                 "code": "4/secret-code"}))
        self.assertTrue(root.activated.wait(3), "Portal's gateway update starts after connecting")
        self.assertTrue(closed(port))
        self.assertNotIn("secret-code", json.dumps(self.logs))

    def test_an_out_of_date_page_keeps_the_owners_sign_in_open(self):
        root = FakeRoot(**{"connection-complete": [(409, {"error": "sign-in-state-mismatch"}), (200, CONNECTED)]})
        self.begin(root)
        port = root.calls[0][1]["redirectPort"]
        self.assertIn("out of date", visit(port, f"state={'t' * 43}&code=4/stale")[2])
        self.assertIn("Google is connected", visit(port, f"state={STATE}&code=4/fresh")[2])
        self.assertTrue(closed(port))

    def test_a_page_that_is_not_googles_redirect_changes_nothing(self):
        root = FakeRoot(**{"connection-cancel": (200, {"outcome": "cancelled"})})
        self.begin(root)
        port = root.calls[0][1]["redirectPort"]
        self.assertEqual(visit(port, "")[0], 404)
        self.assertEqual(visit(port, f"state={STATE}&code=4/x", path="/favicon.ico")[0], 404)
        for query in ("state=short&code=4/x", f"state={STATE}&code=4/x&extra=1", f"state={STATE}&state={STATE}"):
            self.assertIn("isn't the page Google sends you to", visit(port, query)[2])
        self.assertEqual([operation for operation, _request in root.calls], ["connection-begin"])
        self.assertEqual(host_api.cancel({"flowId": FLOW}, config={}, relay=root), (200, {"outcome": "cancelled"}))
        self.assertTrue(closed(port))

    def test_a_declined_consent_needs_no_help_link_but_a_real_failure_has_one(self):
        for error_class, query, sent, help_link in (
                ("access-denied", f"error=access_denied&state={STATE}", {"error": "access-denied"}, False),
                ("invalid-grant", f"state={STATE}&code=4/x", {"code": "4/x"}, True)):
            with self.subTest(error_class=error_class):
                root = FakeRoot(**{"connection-complete": (200, failed(error_class))})
                self.begin(root)
                port = root.calls[0][1]["redirectPort"]
                page = visit(port, query)[2]
                self.assertIn("Google didn't connect", page)
                self.assertEqual(host_api.DISCORD in page, help_link)
                self.assertEqual(root.calls[1], ("connection-complete", {"flowId": FLOW, "state": STATE, **sent}))
                self.assertTrue(closed(port))
                self.assertFalse(root.activated.is_set())

    def test_an_address_pasted_from_another_device_completes_the_same_way(self):
        root = FakeRoot(**{"connection-complete": (200, CONNECTED)})
        address = f"http://localhost:53682/?state={STATE}&code=4/pasted"
        self.assertEqual(host_api.complete({"flowId": FLOW, "address": address}, config={}, relay=root),
                         (200, CONNECTED))
        self.assertEqual(root.calls[0], ("connection-complete", {"flowId": FLOW, "state": STATE, "code": "4/pasted"}))
        self.assertTrue(root.activated.wait(3))
        quiet = FakeRoot()
        for body, error in (({"flowId": FLOW, "address": f"https://example.com/?state={STATE}&code=4/x"},
                             "address-not-from-sign-in"),
                            ({"flowId": "not-a-flow", "address": address}, "invalid-request"),
                            ({"flowId": FLOW, "address": "http://127.0.0.1:53682/?state=short&code=4/x"},
                             "invalid-callback")):
            self.assertEqual(host_api.complete(body, config={}, relay=quiet), (400, {"error": error}))
        self.assertEqual(quiet.calls, [])

    def test_begin_refuses_other_clients_and_closes_the_address_when_root_refuses(self):
        quiet = FakeRoot()
        web = dict(BEGIN, client={"web": INSTALLED})
        self.assertEqual(self.begin(quiet, web), (400, {"error": "client-not-desktop"}))
        self.assertEqual(self.begin(quiet, dict(BEGIN, extra=True)), (400, {"error": "invalid-request"}))
        self.assertEqual(quiet.calls, [])
        busy = FakeRoot(**{"connection-begin": (409, {"error": "runtime-busy"})})
        self.assertEqual(self.begin(busy), (409, {"error": "runtime-busy"}))
        self.assertTrue(closed(busy.calls[0][1]["redirectPort"]))

    def test_a_new_sign_in_closes_the_previous_address(self):
        first, second = FakeRoot(), FakeRoot()
        self.begin(first)
        self.begin(second)
        self.assertTrue(closed(first.calls[0][1]["redirectPort"]))
        self.assertEqual(visit(second.calls[0][1]["redirectPort"], "")[0], 404, "the new address still listens")

    def test_disconnect_updates_portal_in_the_background_and_logs_only_a_type_if_it_cannot(self):
        root = FakeRoot(**{"connection-disconnect": (200, {"outcome": "disconnected", "revoked": True})})
        self.assertEqual(host_api.disconnect({}, config={}, relay=root), (200, {"outcome": "disconnected",
                                                                                 "revoked": True}))
        self.assertTrue(root.activated.wait(3))
        self.assertEqual(host_api.disconnect({"connector": "google"}, config={}, relay=root),
                         (400, {"error": "invalid-request"}))
        logs, logged = [], threading.Event()

        def unreachable(operation, request, *, config):
            if operation == "connection-activate":
                raise pixel_access_relay.AccessRelayError("private-sentinel")
            return 200, {"outcome": "disconnected", "revoked": False}
        host_api.disconnect({}, config={}, relay=unreachable, log=lambda line: (logs.append(line), logged.set()))
        self.assertTrue(logged.wait(3))
        self.assertEqual(logs, ["connected apps: Portal's gateway update did not run (AccessRelayError)"])


if __name__ == "__main__":
    unittest.main()
