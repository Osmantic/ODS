"""Real pinned native MCP OAuth, using only a controlled loopback provider.

OpenClaw owns discovery, registration, PKCE, tokens and refresh. The existing
ODS host listener receives the redirect and validates its state before the code
crosses stdin to the native helper. No real account or installed state is used.
This qualifies the native seam; the managed app enrollment/UI is still separate.
"""
import base64
import contextlib
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import urllib.parse
import urllib.request

import test_connection_plugin_runtime as fixture

ROOT = Path(__file__).resolve().parents[4]
NATIVE_MODULE = "dist/agent-bundle-mcp-runtime-tW2Zz-uG.js"
NATIVE_SHA256 = "ed2e1d92189326625e7c8a735b83ede3942e1597e547587d8ee6413a7fd23bf0"
# No public MCP OAuth SDK export exists in this pinned package. This test-only
# seam is deliberately version/hash bound, not a claim of a stable private API.
BRIDGE = r'''
import {pathToFileURL} from 'node:url';
let raw=''; for await (const chunk of process.stdin) raw+=chunk;
const input=JSON.parse(raw);
const native=await import(pathToFileURL(input.packageDir+'/'+input.module));
let result;
{
  const runtime=native.n({sessionId:'ods-native-oauth-qa',sessionKey:'agent:pixel:qa-oauth',
    workspaceDir:process.cwd(),cfg:{mcp:{servers:{[input.name]:input.server}},
      plugins:{allow:[],entries:{}}}});
  try {
    const catalog=await runtime.getCatalog();
    result={connected:!!catalog.servers[input.name],
      tools:catalog.tools.map(tool=>tool.toolName)};
    if(input.operation==='read') result.read=await runtime.callTool(input.name,'notes_read',{});
  } finally {await runtime.dispose();}
}
process.stdout.write(JSON.stringify(result)+'\n');
'''


class OAuthProvider:
    """A provider fixture, never an alternate production OAuth implementation."""

    def __init__(self):
        self.clients = {}
        self.codes = {}
        self.access = None
        self.refresh = None
        self.exchanges = 0
        self.refreshes = 0
        self.reads = 0
        self.token_failure = None
        self.token_entered = threading.Event()
        self.token_gate = None
        self.authority = self
        self.headers_seen = []
        self.failures = []
        self.fact = "Cedar notes: " + secrets.token_hex(16)
        provider = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def reply(self, status, value=None, **headers):
                raw = b"" if value is None else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Type", "application/json")
                for key, item in headers.items():
                    self.send_header(key.replace("_", "-"), item)
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802 - http.server API
                provider.headers_seen.append({key.lower(): value for key, value in self.headers.items()})
                parts = urllib.parse.urlsplit(self.path)
                if parts.path.startswith("/.well-known/oauth-protected-resource"):
                    self.reply(200, {"resource": provider.origin + "/mcp",
                        "authorization_servers": [provider.authority.origin], "scopes_supported": ["notes.read"]})
                elif parts.path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
                    self.reply(200, {"issuer": provider.origin,
                        "authorization_endpoint": provider.origin + "/authorize",
                        "token_endpoint": provider.origin + "/token",
                        "registration_endpoint": provider.origin + "/register",
                        "response_types_supported": ["code"],
                        "grant_types_supported": ["authorization_code", "refresh_token"],
                        "token_endpoint_auth_methods_supported": ["none"],
                        "code_challenge_methods_supported": ["S256"]})
                elif parts.path == "/authorize":
                    fields = dict(urllib.parse.parse_qsl(parts.query, strict_parsing=True))
                    client = provider.clients.get(fields.get("client_id"))
                    if (not client or fields.get("redirect_uri") not in client["redirect_uris"]
                            or fields.get("code_challenge_method") != "S256" or not fields.get("state")
                            or fields.get("scope") != "notes.read"):
                        provider.failures.append("Invalid native authorization request")
                        self.reply(400, {"error": "invalid_request"})
                        return
                    code = secrets.token_urlsafe(24)
                    provider.codes[code] = fields
                    location = fields["redirect_uri"] + "?" + urllib.parse.urlencode({
                        "code": code, "state": fields["state"]})
                    self.reply(302, Location=location)
                else:
                    self.reply(405 if parts.path == "/mcp" else 404)

            def do_POST(self):  # noqa: N802 - http.server API
                provider.headers_seen.append({key.lower(): value for key, value in self.headers.items()})
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 65536:
                    self.reply(413)
                    return
                raw = self.rfile.read(length)
                if self.path == "/register":
                    body = json.loads(raw)
                    if (body.get("token_endpoint_auth_method") != "none"
                            or body.get("grant_types") != ["authorization_code", "refresh_token"]):
                        provider.failures.append("Invalid native client registration")
                        self.reply(400, {"error": "invalid_client_metadata"})
                        return
                    client_id = secrets.token_hex(12)
                    provider.clients[client_id] = body
                    self.reply(201, {**body, "client_id": client_id})
                elif self.path == "/token":
                    provider.token_entered.set()
                    if provider.token_gate is not None:
                        provider.token_gate.wait(20)
                    if provider.token_failure is not None:
                        self.reply(400, provider.token_failure)
                        return
                    fields = dict(urllib.parse.parse_qsl(raw.decode(), strict_parsing=True))
                    if fields.get("grant_type") == "authorization_code":
                        original = provider.codes.pop(fields.get("code"), None)
                        verifier = fields.get("code_verifier", "")
                        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                        if (not original or challenge != original["code_challenge"]
                                or fields.get("client_id") != original["client_id"]
                                or fields.get("redirect_uri") != original["redirect_uri"]):
                            provider.failures.append("Native code exchange did not bind PKCE/client/redirect")
                            self.reply(400, {"error": "invalid_grant"})
                            return
                        provider.exchanges += 1
                    elif fields.get("grant_type") == "refresh_token":
                        if fields.get("refresh_token") != provider.refresh:
                            provider.failures.append("Native refresh did not use the retained token")
                            self.reply(400, {"error": "invalid_grant"})
                            return
                        provider.refreshes += 1
                    else:
                        provider.failures.append("Unexpected grant")
                        self.reply(400, {"error": "unsupported_grant_type"})
                        return
                    provider.access = secrets.token_urlsafe(24)
                    provider.refresh = secrets.token_urlsafe(32)
                    self.reply(200, {"access_token": provider.access, "token_type": "Bearer",
                                     "refresh_token": provider.refresh, "expires_in": 3600, "scope": "notes.read"})
                elif self.path == "/mcp":
                    if self.headers.get("Authorization") != "Bearer " + str(provider.authority.access):
                        self.reply(401, {"error": "invalid_token"}, WWW_Authenticate=(
                            'Bearer resource_metadata="' + provider.origin + '/.well-known/oauth-protected-resource/mcp"'))
                        return
                    body = json.loads(raw)
                    identity, method = body.get("id"), body.get("method")
                    if identity is None:
                        self.reply(202)
                        return
                    if method == "initialize":
                        result = {"protocolVersion": body["params"]["protocolVersion"], "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "ods-oauth-fixture", "version": "1"}}
                    elif method == "tools/list":
                        result = {"tools": [{"name": name, "description": name,
                            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
                            for name in ("notes_read", "notes_delete")]}
                    elif method == "tools/call" and body["params"]["name"] == "notes_read":
                        provider.reads += 1
                        result = {"content": [{"type": "text", "text": provider.fact}]}
                    else:
                        provider.failures.append("Unexpected native MCP request")
                        self.reply(400)
                        return
                    self.reply(200, {"jsonrpc": "2.0", "id": identity, "result": result})
                else:
                    self.reply(404)

            def log_message(self, *_args):
                pass  # OAuth request lines and token responses remain private.

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.origin = "http://127.0.0.1:%d" % self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


@unittest.skipUnless(fixture.PACKAGE and sys.platform == "linux",
                     "Set OPENCLAW_PACKAGE_DIR for the pinned native OAuth check")
class NativeMcpOAuthTests(unittest.TestCase):
    def setUp(self):
        fixture.PluginRuntimeTests.setUpClass()
        self.world = fixture.PluginRuntimeTests()
        self.world.setUp()
        self.addCleanup(self.world.doCleanups)
        actual = hashlib.sha256((self.world.package / NATIVE_MODULE).read_bytes()).hexdigest()
        self.assertEqual(actual, NATIVE_SHA256, "Review the native OAuth seam when its pinned bytes change")
        self.bridge = self.world.home / "native_oauth_fixture.mjs"
        self.bridge.write_text(BRIDGE)
        self.provider = OAuthProvider()
        self.addCleanup(self.provider.close)
        from pixel_connections import host_api, oauth
        self.oauth = oauth
        self.listener = host_api._Listener()
        self.addCleanup(self.listener.close)
        self.server = {"enabled": True, "transport": "streamable-http", "url": self.provider.origin + "/mcp",
            "auth": "oauth", "oauth": {"redirectUrl": "http://127.0.0.1:%d/" % self.listener.port,
                                      "scope": "notes.read"}, "toolFilter": {"include": ["notes_read"]}}

    def call(self, operation, **values):
        if operation in ("status", "begin", "complete", "forget"):
            return self.oauth_call(operation, **values)
        request = {"packageDir": str(self.world.package), "module": NATIVE_MODULE,
                   "name": "qa-notes", "server": self.server, "operation": operation, **values}
        result = subprocess.run(["node", str(self.bridge)], input=json.dumps(request), text=True,
            capture_output=True, env=self.world.env, cwd=self.world.home, timeout=35)
        self.assertEqual(result.returncode, 0, "Native OAuth helper failed (private diagnostics retained by fixture)")
        if self.provider.access:
            self.assertNotIn(self.provider.access, result.stdout)
        return json.loads(result.stdout)

    def oauth_call(self, operation, **values):
        request = {"packageDir": str(self.world.package), "serverName": "qa-notes",
                   "server": self.server, "operation": operation}
        if "code" in values:
            request["authorizationCode"] = values.pop("code")
        request.update(values)
        result = self.adapter(request)
        self.assertEqual(result.returncode, 0, "Owner-side native OAuth adapter failed")
        if self.provider.access:
            self.assertNotIn(self.provider.access, result.stdout + result.stderr)
        frame = json.loads(result.stdout)
        self.assertIs(frame["ok"], True)
        return frame["result"]

    def adapter(self, request):
        program = ROOT / "extensions/services/pixel-agent/host/native_mcp_oauth.mjs"
        return subprocess.run(["node", str(program)], input=json.dumps(request), text=True,
            capture_output=True, env=self.world.env, cwd=self.world.home, timeout=35)

    def test_unreviewed_runtime_invalid_request_and_remote_errors_do_not_leak(self):
        secret = "PRIVATE-" + secrets.token_hex(24)
        base = {"packageDir": str(self.world.package), "serverName": "qa-notes",
                "server": self.server, "operation": "status"}
        invalid = [dict(base, operation="execute", command=secret), dict(base, serverName=secret),
                   dict(base, operation="complete", authorizationCode="bad\n" + secret),
                   dict(base, extra=secret)]
        for request in invalid:
            with self.subTest(operation=request["operation"]):
                result = self.adapter(request)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), {"ok": False, "code": "invalid-native-oauth-request"})
                self.assertNotIn(secret, result.stdout + result.stderr)
        fake = self.world.home / "unreviewed-package"
        (fake / "dist").mkdir(parents=True)
        (fake / "package.json").write_text(json.dumps({"name": "openclaw", "version": "2026.6.33"}))
        (fake / NATIVE_MODULE).write_text("process.stdout.write(" + json.dumps(secret) + ");")
        result = self.adapter(dict(base, packageDir=str(fake)))
        self.assertEqual(json.loads(result.stdout), {"ok": False, "code": "native-oauth-runtime-unsupported"})
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.call("begin")
        self.provider.token_failure = {"error": "invalid_request", "error_description": secret}
        result = self.adapter(dict(base, operation="complete", authorizationCode=secret))
        self.assertEqual(json.loads(result.stdout), {"ok": False, "code": "native-oauth-failed"})
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_begin_requires_the_explicit_host_loopback_redirect(self):
        for redirect in (None, "http://127.0.0.1/", "https://example.com/", "http://127.0.0.1:8000/other",
                         "http://user@127.0.0.1:8000/", "http://127.0.0.1:8000/?other=flow"):
            with self.subTest(redirect=redirect):
                server = dict(self.server, oauth={} if redirect is None else {"redirectUrl": redirect})
                result = self.adapter({"packageDir": str(self.world.package), "serverName": "qa-notes",
                                       "server": server, "operation": "begin"})
                self.assertEqual(json.loads(result.stdout), {"ok": False, "code": "invalid-native-oauth-redirect"})
        self.assertEqual(self.provider.clients, {})

    def test_native_guard_refuses_other_loopback_origin_without_forwarding_headers(self):
        authority = OAuthProvider()
        self.addCleanup(authority.close)
        self.provider.authority = authority
        private_header = "PRIVATE-" + secrets.token_hex(24)
        self.server["headers"] = {"X-Resource-Private": private_header, "Authorization": "Bearer " + private_header}
        result = self.adapter({"packageDir": str(self.world.package), "serverName": "qa-notes",
                               "server": self.server, "operation": "begin"})
        # The resource's allowed loopback origin must not authorize another
        # private origin discovered in OAuth metadata. Keep the native guard;
        # do not disable it to make a two-loopback-provider fixture pass.
        self.assertEqual(json.loads(result.stdout), {"ok": False, "code": "native-oauth-failed"})
        self.assertEqual(authority.headers_seen, [])
        self.assertTrue(any(headers.get("x-resource-private") == private_header
                            for headers in self.provider.headers_seen))
        self.assertFalse(any(private_header in headers.get("authorization", "")
                             for headers in self.provider.headers_seen))
        self.assertNotIn(private_header, result.stdout + result.stderr)
        self.assertEqual(authority.exchanges, 0)
        self.assertEqual(authority.failures + self.provider.failures, [])

    def test_native_login_callback_read_refresh_and_forget(self):
        self.assertIs(self.call("status")["hasTokens"], False)
        begin = self.call("begin")
        self.assertEqual(begin["outcome"], "redirect")
        fields = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(begin["authUrl"]).query))
        self.assertEqual(fields["redirect_uri"], self.server["oauth"]["redirectUrl"])
        self.assertEqual(fields["code_challenge_method"], "S256")
        self.assertIs(self.call("status")["hasCodeVerifier"], True)
        expected = self.oauth.state_hash(fields["state"])
        completed = []

        def callback(query):
            try:
                parsed = self.oauth.parse_callback(query, expected_state_hash=expected)
            except self.oauth.OAuthError:
                return "This sign-in is out of date", "Return to ODS.", None, False
            completed.append(self.call("complete", code=parsed["code"]))
            return "Connected", "Return to ODS.", None, True

        self.listener.serve(secrets.token_hex(16), callback)
        wrong = self.server["oauth"]["redirectUrl"] + "?" + urllib.parse.urlencode({
            "state": secrets.token_urlsafe(32), "code": "other-flow-code"})
        with urllib.request.urlopen(wrong, timeout=5) as response:
            self.assertIn(b"out of date", response.read())
        self.assertEqual(self.provider.exchanges, 0)
        with urllib.request.urlopen(begin["authUrl"], timeout=35) as response:
            self.assertIn(b"Connected", response.read())
        self.assertEqual(completed, [{"outcome": "authorized", "authUrl": None}])
        self.assertIs(self.call("status")["hasTokens"], True)
        store = self.world.state / "mcp-oauth"
        files = list(store.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(store.stat().st_mode & 0o777, 0o700)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(files[0].read_bytes())["tokens"]["access_token"], self.provider.access)
        read = self.call("read")
        self.assertEqual(read["tools"], ["notes_read"])
        self.assertIn(self.provider.fact, json.dumps(read["read"]))
        # Expire the resource-side token, then prove a fresh native process uses
        # the retained private refresh token. Do not manufacture local expiry.
        self.provider.access = secrets.token_urlsafe(24)
        self.assertIn(self.provider.fact, json.dumps(self.call("read")["read"]))
        self.assertEqual(self.provider.refreshes, 1)
        self.assertEqual(self.provider.reads, 2)
        self.assertEqual(self.call("forget"), {"forgotten": True})
        self.assertIs(self.call("status")["hasTokens"], False)
        self.assertEqual(list(store.glob("*.json")), [])
        self.assertIs(self.call("probe")["connected"], False)
        self.assertEqual(self.provider.exchanges, 1)
        self.assertEqual(self.provider.failures, [])

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0, 'Owner-side operation must run as an unprivileged UID')
    def test_durable_claim_recovers_a_lost_real_exchange_without_replaying_it(self):
        from native_mcp_owner import operate
        from pixel_connections import native_mcp as native
        world = self.world
        # Hosted tool-cache ancestors may be group/world writable. The bound
        # owner operation correctly rejects them. Stage the same real Node
        # bytes under this private fixture, as the installed runtime requires;
        # do not relax the production ancestor-custody check for CI.
        node_dir = world.home / 'private-node'
        node_dir.mkdir(mode=0o700)
        node = node_dir / 'node'
        shutil.copyfile(Path(shutil.which('node', path=world.env['PATH'])).resolve(), node)
        node.chmod(0o700)
        world.env['PATH'] = str(node_dir) + os.pathsep + world.env['PATH']
        world.write_config({'mcp': {'servers': {'qa-notes': self.server}}})
        journal_dir = world.home / 'private-custodian'
        journal_dir.mkdir(mode=0o700)

        class OwnerBridge:
            # The real owner operation/native store and callback run here. This
            # fixture does not claim Root socket authentication or UID dropping.
            state, home, binary = journal_dir, world.home, world.openclaw
            owner = SimpleNamespace(pw_uid=os.geteuid())
            lock = threading.RLock()
            lose_complete_reply = True

            def bounded(self, _seconds):
                return contextlib.nullcontext()

            def locked(self):
                return self.lock

            def discover(self):
                pass

            def pending(self):
                return None

            def worker_environment(self):
                return world.env

            def worker(self, operation, *, config_hash, native_oauth_request):
                assert operation == 'native-mcp-oauth'
                result = operate(str(world.state / 'openclaw.json'), str(world.state / '.ods-access-mode'),
                                 openclaw=world.openclaw, request=native_oauth_request,
                                 expected_config_sha256=config_hash)
                if self.lose_complete_reply and native_oauth_request['action'] == 'complete':
                    raise RuntimeError('fixture: response lost after native tokens saved')
                return result

        bridge = OwnerBridge()
        consent = native.Consent(bridge, app='notes', server_name='qa-notes')
        with patch.dict(os.environ, world.env, clear=True):
            begin = consent.begin(redirect_port=self.listener.port)
            with self.assertRaisesRegex(native.NativeError, 'state-mismatch'):
                consent.complete(flow_id=begin['flowId'], callback_url=self.server['oauth']['redirectUrl'] + '?' +
                    urllib.parse.urlencode({'state': secrets.token_urlsafe(32), 'code': 'other-flow'}))
            self.assertEqual(self.provider.exchanges, 0)
            callbacks = []

            def callback(query):
                callbacks.append(self.server['oauth']['redirectUrl'] + '?' + query)
                try:
                    consent.complete(flow_id=begin['flowId'], callback_url=callbacks[-1])
                except RuntimeError:
                    return 'Recovery required', 'Saved state retained.', None, True
                raise AssertionError('The fixture must lose the successful reply')

            self.listener.serve(begin['flowId'], callback)
            with urllib.request.urlopen(begin['authUrl'], timeout=35) as response:
                self.assertIn(b'Recovery required', response.read())
            self.assertEqual(self.provider.exchanges, 1)
            self.assertEqual(consent.status()['phase'], 'exchanging')
            with self.assertRaisesRegex(native.NativeError, 'native-callback-already-claimed'):
                consent.complete(flow_id=begin['flowId'], callback_url=callbacks[0])
            recovered = consent.recover(flow_id=begin['flowId'])
            self.assertEqual(recovered['error'], 'native-oauth-attempt-unconfirmed')
            self.assertIs(recovered['portalAccessVerified'], False)
            # Explicit fresh sign-in may reuse saved valid native credentials.
            # It does not repeat the code exchange or claim a new account choice.
            resumed = consent.begin(redirect_port=self.listener.port)
            self.assertEqual((resumed['phase'], resumed['authUrl']), ('authorized', None))
            self.assertEqual(self.provider.exchanges, 1)
            self.assertIn(self.provider.fact, json.dumps(self.call('read')['read']))
            raw = (journal_dir / 'native-mcp-consent.json').read_text()
            for private in (self.provider.access, self.provider.refresh, urllib.parse.urlsplit(callbacks[0]).query):
                self.assertNotIn(private, raw)
            self.assertEqual((journal_dir / 'native-mcp-consent.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.provider.failures, [])

    def test_guardian_eof_stops_native_child_during_a_pending_exchange(self):
        import http.client
        import socket
        begin = self.call('begin')
        parts = urllib.parse.urlsplit(begin['authUrl'])
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
        connection.request('GET', parts.path + '?' + parts.query)
        response = connection.getresponse()
        self.assertEqual(response.status, 302)
        callback = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(response.getheader('Location')).query))
        response.read()
        connection.close()
        self.provider.token_gate = threading.Event()
        self.addCleanup(self.provider.token_gate.set)
        guardian, watched = socket.socketpair()
        with guardian, watched:
            environment = dict(self.world.env, ODS_NATIVE_OAUTH_GUARD_FD=str(watched.fileno()))
            child = subprocess.Popen(['node', str(ROOT / 'extensions/services/pixel-agent/host/native_mcp_oauth.mjs')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
                cwd=self.world.home, text=True, pass_fds=(watched.fileno(),))
            try:
                watched.close()
                child.stdin.write(json.dumps({'operation': 'complete', 'packageDir': str(self.world.package),
                    'serverName': 'qa-notes', 'server': self.server, 'authorizationCode': callback['code']}))
                child.stdin.close()
                self.assertTrue(self.provider.token_entered.wait(10), 'Native exchange never started')
                guardian.close()  # Models abrupt owner-worker death, not a native timeout.
                self.assertNotEqual(child.wait(timeout=5), 0)
                self.assertNotIn(callback['code'], child.stdout.read() + child.stderr.read())
                self.assertFalse(json.loads(next((self.world.state / 'mcp-oauth').glob('*.json')).read_bytes()).get('tokens'))
            finally:
                self.provider.token_gate.set()
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stdout.close()
                child.stderr.close()


if __name__ == "__main__":
    unittest.main()
