"""The connected-apps custodian end to end, with fake installed services and a fake Google.

Root custody, the real Source Broker sign-in exchange and revocation, the owner
transaction and the gated gateway restart all run for real; systemd, the gateway
process and Google's endpoints are simulated. Not installed runtime acceptance.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX owner coordination runs under Linux/WSL")

import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import types
import threading
import time
import urllib.error
import urllib.parse

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from test_access_bridge import FakeBridge  # noqa: E402
import connection_transaction as tx  # noqa: E402
import pixel_access_bridge as access  # noqa: E402
from pixel_access_bridge import AccessError, atomic_json  # noqa: E402
import pixel_model_coordinator as model_coordinator  # noqa: E402
from pixel_connections import coordinator as c, custody, lifecycle, oauth, planner, state, status  # noqa: E402

SPEC = importlib.util.spec_from_file_location("pixel_source_broker_under_test",
                                              ROOT / "vendor/pixel/deploy/source-broker/broker.py")
BROKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BROKER)

CLIENT = {"clientId": "123456789012-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com",
          "clientSecret": "test-secret-test", "projectId": "ods-owner-calendar"}
READ_BOTH = {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
             "gmail.read": True, "gmail.drafts": False}
GMAIL = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR = "https://www.googleapis.com/auth/calendar.events.readonly"
ENV = ("OPENCLAW_STATE_DIR='/home/owner/.openclaw'\nPIXEL_CALENDAR_DIRECT_ENABLED='0'\n"
       "PIXEL_LIMB_EMAIL_ENABLED='0'\nPIXEL_LIMB_CALENDAR_ENABLED='0'\nPIXEL_LIMB_CALENDAR_WRITE_ENABLED='0'\n")
KIT_ENV = ("PIXEL_SOURCE_TOKEN_PATH='/var/lib/pixel-source-broker/private/google-token.json'\n"
           "PIXEL_SOURCE_CALENDAR_ID='primary'\nPIXEL_SOURCE_TIME_ZONE='UTC'\n"
           "PIXEL_SOURCE_GMAIL_QUERY='in:inbox'\nPIXEL_SOURCE_GMAIL_SENT_QUERY='in:sent'\n"
           "PIXEL_LIMB_EMAIL_ENABLED='0'\nPIXEL_LIMB_CALENDAR_ENABLED='0'\nPIXEL_LIMB_CALENDAR_WRITE_ENABLED='0'\n"
           "PIXEL_CALENDAR_DIRECT_ENABLED='0'\nPYTHONDONTWRITEBYTECODE='1'\n")


class Crash(BaseException):
    """The coordinator process dying mid-step: no handler of the code under test sees it."""


def gateway_config(install):
    allow = sorted(["read", "write", "exec", "pixel_ods_status", "update_plan"])
    return {"gateway": {"auth": {"token": "t" * 32}},
            "plugins": {"allow": ["pixel-ods", "searxng", "llama-cpp", "pixel-operations-broker"],
                        "entries": {"pixel-ods": {"enabled": True}, "llama-cpp": {"enabled": True}},
                        "load": {"paths": ["/opt/ods/plugin", f"{install}/current/plugin-ops"]}},
            "tools": {"alsoAllow": list(allow), "sandbox": {"tools": {"allow": list(allow)}}},
            "agents": {"defaults": {"bootstrapMaxChars": 32000},
                       "list": [{"id": "pixel", "sandbox": {"mode": "all"},
                                 "tools": {"deny": ["discord", "message", *planner.SOURCE_TOOLS]}}]}}


class ConnectionBridge(FakeBridge):
    """Fake systemd and gateway around the real owner transaction, as access_mode_worker runs it."""

    def __init__(self, root):
        super().__init__(root)
        self.started = 1000
        self.plugin_ready = lambda enabled: True
        self.discover()
        self.config_path = self.home / ".openclaw/openclaw.json"
        self.config_path.parent.mkdir(mode=0o700)
        self.owner_state = self.config_path.parent / ".ods-access-mode"
        self.install_dir = self.home / ".local/share/pixel"
        atomic_json(self.config_path, gateway_config(self.install_dir))
        (self.home / ".config").mkdir(mode=0o700)
        self.env_path = self.home / ".config/pixel-agent/gateway.env"
        self.env_path.parent.mkdir(mode=0o700)
        self.env_path.write_text(ENV)
        self.env_path.chmod(0o600)
        self.answers_path = self.install / "data/pixel/onboarding.json"
        self.answers_path.parent.mkdir(parents=True)
        self.answers_path.write_text(json.dumps({"agentId": "pixel", **state.DISCONNECTED_ANSWERS}, sort_keys=True))
        self.answers_path.chmod(0o600)
        self.marker_path = self.home / ".config/ods/pixel-managed.json"
        self.marker_path.parent.mkdir(mode=0o700)
        atomic_json(self.marker_path, {
            "schema_version": 2, "manager": "ods", "state": "ready", "initial_active_state": "absent",
            "install_dir": str(self.install),
            "configuration_sha256": model_coordinator._marker_digest(json.loads(self.config_path.read_text()))})

    def command(self, args, timeout=20):
        if "restart" in args:
            self.started += 1000
            return super().command(args, timeout)
        if any("ExecMainStartTimestampMonotonic" in arg for arg in args):
            return f"MainPID={self.pid}\nActiveState=active\nExecMainStartTimestampMonotonic={self.started}"
        return str(self.pid)

    def worker(self, operation="status", **kwargs):
        if operation == "status":
            return {"configured_status": self.mode, "config_sha256": access.digest(self.mode), "managed": False}
        self.log.append(operation)
        files = dict(state_dir=str(self.owner_state))
        if operation == "connection-owner-status":
            return tx.connection_status(str(self.config_path), env_path=str(self.env_path),
                                        answers_path=str(self.answers_path), **files)
        files.update(install_dir=str(self.install_dir), openclaw="/opt/openclaw/bin/openclaw",
                     transaction_id=kwargs["transaction_id"], expected_config_sha256=kwargs["config_hash"],
                     validate_config=lambda staged: True, check_no_active_run=kwargs["busy"],
                     activate=kwargs["activate_connection"])
        if operation == "connection-apply":
            return tx.apply_connection(str(self.config_path), kwargs["connection_target"], kwargs["connection_answers"],
                                       env_path=str(self.env_path), answers_path=str(self.answers_path), **files)
        return tx.recover_connection(str(self.config_path), **files)


def id_token(email="owner@example.com"):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT["clientId"], "email": email, "email_verified": True}
    return ".".join((encode({"alg": "RS256"}), encode(claims), "fixture-signature"))


def granted(*scopes, with_identity=True):
    """Google's token endpoint granting these scopes for the code."""
    def answer(_body):
        response = {"access_token": "fixture-access", "refresh_token": "example-refresh-from-google",
                    "scope": " ".join(("openid", "https://www.googleapis.com/auth/userinfo.email", *scopes))}
        if with_identity:
            response["id_token"] = id_token()
        return 200, response
    return answer


class FakeGoogle:
    """Google's token and revocation endpoints, as the broker's urlopen reaches them."""

    def __init__(self):
        self.token = granted(GMAIL, CALENDAR)
        self.calls = []

    def __call__(self, request, timeout=None):
        body = urllib.parse.parse_qs(request.data.decode("ascii"))
        self.calls.append((request.full_url, body))
        status, payload = self.token(body) if request.full_url == BROKER.GOOGLE_TOKEN_URI else (200, {})
        raw = json.dumps(payload).encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "fixture", {}, io.BytesIO(raw))
        return io.BytesIO(raw)


@pytest.fixture
def world(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    original = access.private_json
    read = lambda path, _uid, maximum=1048576: original(path, os.getuid(), maximum)  # noqa: E731
    for module in (access, model_coordinator):
        monkeypatch.setattr(module, "private_json", read)
    (tmp_path / "home").mkdir(mode=0o700)
    bridge = ConnectionBridge(tmp_path / "home")
    kit = tmp_path / "program" / "pixel_connections" / "source-broker-kit"
    kit.mkdir(parents=True)
    for directory in (kit, kit.parent, kit.parent.parent):
        directory.chmod(0o755)
    for name in custody.KIT_FILES:
        (kit / name).write_text(KIT_ENV if name == "source-broker.env" else f"# {name}\n")
        (kit / name).chmod(0o644)
    paths = custody.Paths(install=tmp_path / "opt/pixel-source-broker", state=tmp_path / "var/pixel-source-broker",
                          env=tmp_path / "etc/pixel-source-broker.env", units=tmp_path / "systemd", kit=kit)
    for directory in (paths.install.parent, paths.state.parent, paths.env.parent, paths.units):
        directory.mkdir(parents=True, exist_ok=True)
    record = tmp_path / "etc-ods" / "portal-connections.json"
    record.parent.mkdir(mode=0o755)
    monkeypatch.setattr(c, "PATHS", paths)
    monkeypatch.setattr(c, "RECORD", record)
    monkeypatch.setattr(c, "OWNERS", (0, os.getuid()))
    # The broker identity and the owner are this test's account; root's chown is a no-op.
    ids = (os.getuid(), os.getgid())
    monkeypatch.setattr(custody, "account", lambda name: ids)
    monkeypatch.setattr(custody.os, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(custody.os, "fchown", lambda *args, **kwargs: None)
    monkeypatch.setattr(tx, "_plugin_ready", lambda openclaw, install_dir, *, enabled: bridge.plugin_ready(enabled))
    google = FakeGoogle()
    monkeypatch.setattr(BROKER.urllib.request, "urlopen", google)
    monkeypatch.setenv("PIXEL_SOURCE_OAUTH_DIR", str(paths.oauth))
    monkeypatch.setenv("PIXEL_SOURCE_TOKEN_PATH", str(paths.token))
    commands = []

    def run(arguments, timeout):
        """systemctl, running the broker's oneshot units in this process."""
        commands.append(arguments)
        if arguments[:2] == ["systemctl", "start"] and arguments[-1].startswith("pixel-source-exchange@"):
            return BROKER.exchange(arguments[-1].split("@", 1)[1].removesuffix(".service"))
        if arguments == ["systemctl", "start", "pixel-source-revoke.service"]:
            return BROKER.revoke()
        return 0
    monkeypatch.setattr(c, "RUN", run)
    return types.SimpleNamespace(bridge=bridge, paths=paths, record=record, commands=commands, google=google)


def begin(world, **changes):
    request = {"connector": "google", "client": CLIENT, "capabilities": READ_BOTH, "cloudModels": False,
               "ownerTimeZone": "America/Chicago", "loginHint": None, "redirectPort": 53682, **changes}
    return c.control(world.bridge, "connection-begin", request)


def consent_state(world, flow_id):
    """The state Google echoes back; it sits only in the broker's private flow handoff."""
    return json.loads((world.paths.oauth / f"{flow_id}.flow.json").read_text())["state"]


def journal(world):
    return lifecycle.read(Path(world.bridge.state) / "connection.json", owners=(0, os.getuid()))


def record(world):
    return state.read(world.record, owners=(0, os.getuid()))


def connect(world):
    flow = begin(world)
    return c.control(world.bridge, "connection-complete",
                     {"flowId": flow["flowId"], "state": consent_state(world, flow["flowId"]), "code": "4/fixture-code"})


def public_status(world):
    value = status.report(surface="linux-systemd", record_path=world.record, token_path=world.paths.token,
        status_path=world.paths.state / "projection/status.json", broker_state=world.paths.state,
        lifecycle_path=Path(world.bridge.state) / "connection.json", owners=c.OWNERS)
    return status.validate_public(value)


def test_actual_edge_stream_drains_before_exchange_and_stays_closed_until_new_gateway(world, monkeypatch, tmp_path):
    """Actual durable Edge gate + a held HTTP response, simulated native/systemd.

    No installed service or Google account is contacted. Native activity counts
    model direct/cron/detached work separately from the HTTP stream counter.
    """
    spec = importlib.util.spec_from_file_location("connection_real_edge_gate", ROOT / "extensions/services/pixel-edge/transition_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    directory = tmp_path / "edge"
    directory.mkdir(mode=0o700)
    module.initialize(str(directory))
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    gate = module.TransitionGate(str(directory), set())
    drained, finish_old = threading.Event(), threading.Event()

    def call(coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result(5)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            token = object()
            try:
                call(gate.admit(token))
            except module.GateError as error:
                self.send_response(error.status)
                self.end_headers()
                return
            try:
                self.send_response(200)
                self.send_header("Content-Length", "1")
                self.end_headers()
                if self.path == "/old":
                    assert finish_old.wait(10), "fixture stream was not allowed to finish"
                self.wfile.write(b"x")
            finally:
                call(gate.finish(token))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def edge(operation=None, token=None, revision=None):
        try:
            if operation:
                world.bridge.log.append("edge-" + operation)
            if operation == "release":
                return call(gate.release(token, revision))
            if operation:
                value = call(gate.acquire(token, revision, recover=operation == "recover", drain=operation == "drain"))
                if operation == "drain":
                    drained.set()
                return value
            return call(gate.status())
        except module.GateError as error:
            raise AccessError("edge-fixture-" + error.reason) from None

    monkeypatch.setattr(world.bridge, "edge", edge)
    old = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    fresh = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        old.request("GET", "/old")
        response = old.getresponse()
        assert response.status == 200 and edge()["streams"] == 1
        flow = begin(world)
        fields = {"flowId": flow["flowId"], "state": consent_state(world, flow["flowId"]), "code": "4/fixture"}
        with pytest.raises(AccessError, match="sign-in-state-mismatch"):
            c.control(world.bridge, "connection-complete", dict(fields, state="z" * 43))
        assert not drained.is_set() and not world.google.calls
        world.bridge.active = 3  # cron, direct and detached work outside the HTTP stream.
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(c.control, world.bridge, "connection-complete", fields)
            assert drained.wait(3)
            assert edge()["phase"] == "held" and edge()["streams"] == 1
            observed = public_status(world)
            assert observed["reason"] == "connection-admission-held"
            assert observed["connectors"]["google"]["pending"]["phase"] == "consent"
            fresh.request("GET", "/new")
            assert fresh.getresponse().status == 409
            assert not world.google.calls and not result.done()
            finish_old.set()
            assert response.read() == b"x"
            deadline = time.monotonic() + 3
            while edge()["streams"]:
                assert time.monotonic() < deadline
                time.sleep(.01)
            assert not world.google.calls and not result.done(), "native work must also finish"
            world.bridge.active = 0
            assert result.result(5)["outcome"] == "connected"
        assert edge()["phase"] == "held" and world.bridge.native_phase == "held"
        old_pid = world.bridge.pid
        assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
        assert world.bridge.pid != old_pid
        assert world.bridge.log.index("native-release") < world.bridge.log.index("edge-release")
        assert edge()["phase"] == "idle"
        fresh.close()
        fresh = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        fresh.request("GET", "/new")
        assert fresh.getresponse().status == 200
    finally:
        finish_old.set()
        old.close()
        fresh.close()
        server.shutdown()
        server.server_close()
        server_thread.join(3)
        gate.close()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(3)
        loop.close()


def test_drain_deadline_keeps_visible_finish_and_never_replays_oauth(world, monkeypatch):
    flow = begin(world)
    world.bridge.active = 1
    monkeypatch.setattr(c, "GOOGLE_DRAIN_SECONDS", .03)
    with pytest.raises(AccessError, match="runtime-busy"):
        c.control(world.bridge, "connection-complete", {"flowId": flow["flowId"],
            "state": consent_state(world, flow["flowId"]), "code": "4/not-exchanged"})
    assert not world.google.calls and not list(world.paths.oauth.glob("*.code.json"))
    observed = public_status(world)
    assert observed["reason"] == "connection-admission-held"
    assert observed["connectors"]["google"]["pending"]["error"] == "runtime-busy"
    with pytest.raises(AccessError, match="connection-transition-in-progress"):
        begin(world)
    world.bridge.active = 0
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert not world.google.calls and record(world) is None
    assert public_status(world)["connectors"]["google"]["pending"]["phase"] == "failed"


@pytest.mark.skipif(shutil.which("node") is None, reason="The composed native admission fixture requires Node")
def test_actual_native_cron_direct_and_detached_work_settle_before_exchange_and_survive_restart(world, monkeypatch, tmp_path):
    """Actual Native hooks/journal/process identity, fake systemd/probe/Google.

    Only the disposable child created here is restarted, after its actual
    counters reach zero. The exec-mode probe remains a simulated trust boundary.
    """
    directory = tmp_path / "native-child"
    module = (ROOT / "extensions/services/pixel-agent/plugin/access-runtime.mjs").as_uri()
    script = """
import {createAccessRuntime} from MODULE;
import readline from 'node:readline';
const runtime = createAccessRuntime({directory:process.argv[1],runtimeVersion:'2026.6.33',hooksAllowed:true});
console.log(JSON.stringify(runtime.status()));
readline.createInterface({input:process.stdin}).on('line', line => {
  const request=JSON.parse(line);
  if(request.op==='exit')process.exit(0);
  try {
    const result=request.op==='status'?runtime.status():
      request.op==='acquire'?runtime.acquire(request.token,runtime.status().revision):
      request.op==='release'?runtime.release(request.token):
      runtime[request.op](request.event??{},request.context??{});
    console.log(JSON.stringify({ok:true,result:result??null}));
  } catch { console.log(JSON.stringify({ok:false})); }
});
""".replace("MODULE", json.dumps(module))
    child = None
    ipc_lock = threading.Lock()

    def line():
        assert select.select([child.stdout], [], [], 5)[0], "native child did not answer"
        raw = child.stdout.readline()
        assert raw, "native fixture exited before answering"
        return json.loads(raw)

    def start():
        nonlocal child
        child = subprocess.Popen([shutil.which("node"), "--input-type=module", "-e", script, str(directory)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        snapshot = line()
        assert snapshot["available"] is True
        world.bridge.pid = snapshot["pid"]

    def send(op, **fields):
        with ipc_lock:
            child.stdin.write(json.dumps({"op": op, **fields}) + "\n")
            child.stdin.flush()
            value = line()
            if not value["ok"]:
                raise AccessError("native-fixture-refused")
            return value["result"]

    def close():
        if child is not None and child.poll() is None:
            child.stdin.write('{"op":"exit"}\n')
            child.stdin.flush()
            child.wait(5)
        if child is not None:
            child.stdin.close()
            child.stdout.close()
            child.stderr.close()

    start()
    original_command = world.bridge.command
    drained = threading.Event()
    original_edge = world.bridge.edge

    def native(operation=None, token=None, *, timeout=60):
        if operation:
            world.bridge.log.append("native-" + operation)
        if operation == "probe":
            world.bridge.proof = {"mode": world.bridge.mode, "executed": True, "pid": world.bridge.pid}
            return dict(send("status"), proof=world.bridge.proof)
        return dict(send(operation or "status", token=token), proof=world.bridge.proof)

    def command(arguments, timeout=20):
        if "restart" in arguments:
            snapshot = send("status")
            assert snapshot["active"] == 0 and snapshot["phase"] == "held"
            world.bridge.log.append("restart")
            world.bridge.started += 1000
            world.bridge.proof = None
            close()
            start()
            assert send("status")["phase"] == "held", "the same durable lease survives the new PID"
            return ""
        return original_command(arguments, timeout)

    def edge(operation=None, token=None, revision=None):
        value = original_edge(operation, token, revision)
        if operation == "drain":
            drained.set()
        return value

    monkeypatch.setattr(world.bridge, "native", native)
    monkeypatch.setattr(world.bridge, "command", command)
    monkeypatch.setattr(world.bridge, "edge", edge)
    try:
        assert send("admit", context={"runId": "cron-old-turn"})["outcome"] == "pass"
        assert send("beforeTool", event={"toolCallId": "direct-call"}) is None
        send("afterTool", event={"toolName": "exec", "toolCallId": "detached-start", "result": {
            "details": {"status": "running", "sessionId": "only-created-fixture", "startedAt": 1}}})
        assert send("status")["activity"] == {"runs": 1, "tools": 1, "detached": 1}
        flow = begin(world)
        with ThreadPoolExecutor(max_workers=1) as executor:
            completed = executor.submit(c.control, world.bridge, "connection-complete", {
                "flowId": flow["flowId"], "state": consent_state(world, flow["flowId"]), "code": "4/fixture"})
            assert drained.wait(3)
            assert not world.google.calls and not completed.done()
            send("finish", context={"runId": "cron-old-turn"})
            send("afterTool", event={"toolCallId": "direct-call"})
            assert send("status")["active"] == 1
            assert not world.google.calls and not completed.done()
            send("afterTool", event={"toolName": "process", "toolCallId": "detached-end",
                "params": {"sessionId": "only-created-fixture"}, "result": {"details": {"status": "exited", "exitCode": 0}}})
            assert completed.result(5)["outcome"] == "connected"
        assert send("admit", context={"runId": "must-block"})["outcome"] == "block"
        assert send("beforeTool", event={"toolCallId": "must-block-direct"})["block"] is True
        before_pid = world.bridge.pid
        assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
        assert world.bridge.pid != before_pid and send("status")["phase"] == "idle"
        assert send("admit", context={"runId": "fresh-turn"})["outcome"] == "pass"
        send("finish", context={"runId": "fresh-turn"})
    finally:
        close()


@pytest.mark.parametrize("gate_name", ["native", "edge"])
@pytest.mark.parametrize("fault", ["before", "lost-reply", "crash-after"])
def test_release_progress_resumes_without_exchange_apply_or_restart(world, monkeypatch, gate_name, fault):
    connect(world)
    original = getattr(world.bridge, gate_name)
    once = False

    def injected(operation=None, *args, **kwargs):
        nonlocal once
        if operation == "release" and not once:
            once = True
            if fault == "before":
                raise AccessError("injected-release-unavailable")
            original(operation, *args, **kwargs)
            if fault == "crash-after":
                raise Crash()
            raise AccessError("injected-release-lost-reply")
        return original(operation, *args, **kwargs)

    monkeypatch.setattr(world.bridge, gate_name, injected)
    if fault == "lost-reply":
        assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    else:
        with pytest.raises(Crash if fault == "crash-after" else AccessError):
            c.control(world.bridge, "connection-activate", {})
        hold = world.bridge.pending()
        assert hold["google"][gate_name + "ReleaseRequested"]
        before = (len(world.google.calls), world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"))
        assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
        assert before == (len(world.google.calls), world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"))
    assert world.bridge.pending() is None
    assert world.bridge.native_phase == world.bridge.edge_phase == "idle"


def test_same_config_reauthorization_still_retires_the_old_conversation_process(world):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    before = (world.bridge.pid, world.bridge.config_path.read_bytes())
    connect(world)
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    c.control(world.bridge, "connection-activate", {})
    assert world.bridge.config_path.read_bytes() == before[1]
    assert world.bridge.pid != before[0]
    assert world.bridge.log.count("restart") == 2


def test_owner_applied_lost_reply_joins_exact_receipt_without_replaying_consent(world, monkeypatch):
    connect(world)
    original = world.bridge.worker
    once = False

    def lost(operation="status", **kwargs):
        nonlocal once
        value = original(operation, **kwargs)
        if operation == "connection-apply" and not once:
            once = True
            raise Crash()
        return value

    monkeypatch.setattr(world.bridge, "worker", lost)
    with pytest.raises(Crash):
        c.control(world.bridge, "connection-activate", {})
    before = (world.bridge.pid, len(world.google.calls), world.bridge.log.count("connection-apply"))
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert before == (world.bridge.pid, len(world.google.calls), world.bridge.log.count("connection-apply"))


@pytest.mark.parametrize("boundary", ["withdraw-record", "forget-token", "before-new-step", "after-ready-commit"])
def test_fallback_crash_prefixes_finish_under_the_same_lease_without_replaying_old_child(world, monkeypatch, boundary):
    connect(world)
    world.bridge.plugin_ready = lambda enabled: not enabled
    original_record, original_forget = c._write_record, custody.forget_sign_in
    original_reset, original_write = c._google_reset_step, c._write
    once = False

    def crash_here(name):
        nonlocal once
        if not once and boundary == name and world.bridge.pending()["google"]["stage"] == "abandoning":
            once = True
            raise Crash()

    def withdraw(value):
        original_record(value)
        if "google" not in (value or {}).get("connectors", {}):
            crash_here("withdraw-record")

    def forget(*args):
        original_forget(*args)
        crash_here("forget-token")

    def reset(*args):
        crash_here("before-new-step")
        original_reset(*args)

    def write(bridge, value):
        nonlocal once
        previous = bridge.pending()
        original_write(bridge, value)
        if (not once and boundary == "after-ready-commit" and previous["google"]["stage"] == "abandoning"
                and value["google"]["stage"] == "ready"):
            once = True
            raise Crash()

    monkeypatch.setattr(c, "_write_record", withdraw)
    monkeypatch.setattr(custody, "forget_sign_in", forget)
    monkeypatch.setattr(c, "_google_reset_step", reset)
    monkeypatch.setattr(c, "_write", write)
    with pytest.raises(Crash):
        c.control(world.bridge, "connection-activate", {})
    hold = world.bridge.pending()
    token = hold["token"]
    assert hold["google"]["stage"] == ("ready" if boundary == "after-ready-commit" else "abandoning")
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    before_exchanges = len(world.google.calls)
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert len(world.google.calls) == before_exchanges
    assert world.bridge.pending() is None and not world.paths.token.exists()
    assert "google" not in (record(world) or {}).get("connectors", {})
    assert world.bridge.native_phase == world.bridge.edge_phase == "idle"
    assert journal(world)["phase"] == "failed" and journal(world)["error"] == "gateway-change-rolled-back"
    assert token not in json.dumps(public_status(world))


def test_status_exposes_retained_hold_without_lifecycle_or_private_fields(world):
    connect(world)
    hold = world.bridge.pending()
    c._lifecycle_path(world.bridge).unlink()
    observed = public_status(world)
    assert observed["reason"] == "connection-admission-held"
    assert observed["connectors"]["google"]["pending"]["phase"] == "activating"
    text = json.dumps(observed)
    for key in (hold["token"], hold["google"]["flowId"], "beforeRecordSha", "releaseIdentity"):
        assert key not in text
    assert world.bridge.pending() == hold, "status must not mutate or guess callback liveness"
    c._write(world.bridge, hold)
    with pytest.raises(AccessError, match="connection-transition-in-progress"):
        c._resume(world.bridge, hold)
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"


def test_old_account_during_new_consent_is_not_projected_as_new_authorization(world):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    flow = begin(world)
    with world.bridge.locked():
        c._google_reserve(world.bridge, flow["flowId"])
    observed = public_status(world)
    google = observed["connectors"]["google"]
    assert observed["reason"] == "connection-admission-held"
    assert google["account"] == "owner@example.com" and google["pending"]["phase"] == "consent"
    assert google["pending"]["error"] is None


def test_private_hold_failure_remains_visible_if_public_lifecycle_projection_was_not_written(world):
    flow = begin(world)
    with world.bridge.locked():
        hold = c._google_reserve(world.bridge, flow["flowId"])
        hold["google"]["failure"] = "runtime-busy"
        c._write(world.bridge, hold)
    assert journal(world)["phase"] == "consent" and journal(world)["error"] is None
    observed = public_status(world)
    assert observed["reason"] == "connection-admission-held"
    pending = observed["connectors"]["google"]["pending"]
    assert pending["phase"] == "activating" and pending["error"] == "runtime-busy"
    assert journal(world)["phase"] == "consent", "status is a projection, not recovery"


@pytest.mark.parametrize("callback", ["denial", "expiry"])
def test_repeated_denial_or_expiry_cannot_replace_an_already_admitted_callback(world, monkeypatch, callback):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    old_account, old_token = record(world), custody.sign_in_identity(world.paths)
    flow = begin(world)
    request = {"flowId": flow["flowId"], "state": consent_state(world, flow["flowId"])}
    request.update({"error": "access-denied"} if callback == "denial" else {"code": "4/expired"})
    with world.bridge.locked():
        hold = c._google_reserve(world.bridge, flow["flowId"])
        c._google_drain(world.bridge, hold)
    if callback == "expiry":
        later = c._now() + timedelta(seconds=oauth.FLOW_SECONDS + 1)
        monkeypatch.setattr(c, "_now", lambda: later)
    before = {str(p): p.read_bytes() for p in world.bridge.home.parent.rglob("*") if p.is_file()}
    before_calls, before_commands = world.google.calls[:], world.commands[:]
    for _ in range(2):
        with pytest.raises(AccessError, match="^connection-transition-in-progress$"):
            c.control(world.bridge, "connection-complete", request)
        assert {str(p): p.read_bytes() for p in world.bridge.home.parent.rglob("*") if p.is_file()} == before
        assert world.google.calls == before_calls and world.commands == before_commands
        assert world.bridge.native_phase == world.bridge.edge_phase == "held"
        observed = public_status(world)
        assert observed["reason"] == "connection-admission-held"
        assert observed["connectors"]["google"]["pending"]["phase"] == "consent"
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert record(world) == old_account and custody.sign_in_identity(world.paths) == old_token
    assert world.google.calls == before_calls and not list(world.paths.oauth.iterdir())
    assert world.bridge.pending() is None and world.bridge.native_phase == world.bridge.edge_phase == "idle"
    assert journal(world)["phase"] == "failed" and journal(world)["error"] == "sign-in-not-completed"


def test_failed_lifecycle_with_retained_admission_still_projects_finish_without_writes(world):
    flow = begin(world)
    with world.bridge.locked():
        c._google_reserve(world.bridge, flow["flowId"])
        c._write_lifecycle(world.bridge, c._lifecycle("failed", error="access-denied"))
    before = c._lifecycle_path(world.bridge).read_bytes(), world.bridge.pending()
    observed = public_status(world)
    assert observed["reason"] == "connection-admission-held"
    pending = observed["connectors"]["google"]["pending"]
    assert pending["phase"] == "activating" and pending["error"] == "access-denied"
    assert before == (c._lifecycle_path(world.bridge).read_bytes(), world.bridge.pending())


def original_google_drain(world):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    flow = begin(world)
    with world.bridge.locked():
        hold = c._google_reserve(world.bridge, flow["flowId"])
        c._google_drain(world.bridge, hold)
    return hold


@pytest.mark.parametrize("interruption", ["restart", "reboot", "reload-during-acquire"])
def test_finish_recovers_a_never_exchanged_consent_after_process_interruption(world, monkeypatch, interruption):
    if interruption == "reload-during-acquire":
        connect(world)
        c.control(world.bridge, "connection-activate", {})
        flow = begin(world)
        original_native = world.bridge.native
        once = False

        def reload(operation=None, *args, **kwargs):
            nonlocal once
            value = original_native(operation, *args, **kwargs)
            if operation == "acquire" and not once:
                once = True
                world.bridge.command(["restart"])
            return value

        monkeypatch.setattr(world.bridge, "native", reload)
        with pytest.raises(AccessError, match="connection-original-state-changed"):
            c.control(world.bridge, "connection-complete", {"flowId": flow["flowId"],
                "state": consent_state(world, flow["flowId"]), "code": "4/must-not-exchange"})
        hold = world.bridge.pending()
    else:
        hold = original_google_drain(world)
        if interruption == "reboot":
            world.bridge.gateway_service.boot_identity = lambda: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            world.bridge.pid += 1
            world.bridge.started = 1
            world.bridge.proof = None
        else:
            world.bridge.command(["restart"])
    before = record(world), custody.sign_in_identity(world.paths), world.bridge.config_path.read_bytes(), world.bridge.unit_boundary()
    before_pid, before_exchanges = world.bridge.pid, len(world.google.calls)
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert before == (record(world), custody.sign_in_identity(world.paths), world.bridge.config_path.read_bytes(), world.bridge.unit_boundary())
    assert world.bridge.pid != before_pid, "Finish must retire the freshly proved recovery process too"
    assert len(world.google.calls) == before_exchanges and not list(world.paths.oauth.iterdir())
    owner = world.bridge.worker("connection-owner-status")
    assert owner["completion"]["transactionId"] != hold["transactionId"]
    assert world.bridge.pending() is None and world.bridge.native_phase == world.bridge.edge_phase == "idle"


@pytest.mark.parametrize("boundary", ["before-rebase-write", "after-rebase-write"])
def test_recovery_identity_rebase_is_durable_and_still_requires_a_new_pid(world, monkeypatch, boundary):
    hold = original_google_drain(world)
    world.bridge.command(["restart"])
    current = c._identity(world.bridge)
    original_write = c._write
    once = False

    def crash(bridge, value):
        nonlocal once
        rebase = (not once and value["google"]["stage"] == "draining"
                  and value["beforeIdentity"] == current and value["transactionId"] != hold["transactionId"])
        if rebase:
            once = True
            if boundary == "before-rebase-write":
                raise Crash()
        original_write(bridge, value)
        if rebase:
            raise Crash()

    monkeypatch.setattr(c, "_write", crash)
    before_exchanges = len(world.google.calls)
    with pytest.raises(Crash):
        c.control(world.bridge, "connection-activate", {})
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    persisted = world.bridge.pending()
    assert (persisted["beforeIdentity"] == current) == (boundary == "after-rebase-write")
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert world.bridge.pid != current["pid"] and len(world.google.calls) == before_exchanges
    assert world.bridge.pending() is None and world.bridge.native_phase == world.bridge.edge_phase == "idle"


@pytest.mark.parametrize("drift", ["record", "token", "config", "boundary", "owner-pending", "busy", "stopped"])
def test_restart_recovery_never_adopts_changed_data_or_unproved_idle(world, monkeypatch, drift):
    original_google_drain(world)
    world.bridge.command(["restart"])
    original_native, original_worker = world.bridge.native, world.bridge.worker
    if drift == "record":
        c._write_record(dict(record(world), updatedAt="2026-10-28T00:00:00Z"))
    elif drift == "token":
        world.paths.token.touch()
    elif drift == "config":
        world.bridge.config_path.write_bytes(world.bridge.config_path.read_bytes() + b" ")
    elif drift == "boundary":
        world.bridge.dropin.write_text("only-created-fixture-boundary")
    elif drift == "owner-pending":
        def pending(operation="status", **kwargs):
            value = original_worker(operation, **kwargs)
            return dict(value, pending=True) if operation == "connection-owner-status" else value
        monkeypatch.setattr(world.bridge, "worker", pending)
    elif drift == "busy":
        world.bridge.active = 1
    else:
        monkeypatch.setattr(world.bridge, "native", lambda *args, **kwargs: dict(original_native(*args, **kwargs), stopped=True))
    monkeypatch.setattr(c, "GOOGLE_DRAIN_SECONDS", .03)
    before = record(world), custody.sign_in_identity(world.paths), world.bridge.config_path.read_bytes(), world.bridge.unit_boundary()
    before_pid, before_exchanges = world.bridge.pid, len(world.google.calls)
    expected = "runtime-busy" if drift in ("busy", "stopped") else "connection-owner-state-changed" if drift == "owner-pending" else "connection-original-state-changed"
    for _ in range(2):
        with pytest.raises(AccessError, match="^" + expected + "$"):
            c.control(world.bridge, "connection-activate", {})
        assert before == (record(world), custody.sign_in_identity(world.paths), world.bridge.config_path.read_bytes(), world.bridge.unit_boundary())
        assert world.bridge.pid == before_pid and len(world.google.calls) == before_exchanges
        assert world.bridge.pending()["google"]["stage"] == "draining"
        assert world.bridge.edge_phase == "held" and "native-release" not in world.bridge.log[-5:]


def committed_google_prefix(world, monkeypatch, prefix):
    connect(world)
    with monkeypatch.context() as fault:
        if prefix == "applying-before-owner":
            original = world.bridge.worker
            def worker(operation="status", **kwargs):
                if operation == "connection-apply":
                    raise Crash()
                return original(operation, **kwargs)
            fault.setattr(world.bridge, "worker", worker)
        elif prefix == "applying-after-receipt":
            original = c._google_owner_step
            def step(*args):
                original(*args)
                raise Crash()
            fault.setattr(c, "_google_owner_step", step)
        elif prefix == "releasing-before-native":
            def release(*args):
                raise Crash()
            fault.setattr(c, "_google_release", release)
        elif prefix == "releasing-after-native":
            original = world.bridge.edge
            def edge(operation=None, *args, **kwargs):
                if operation == "release":
                    raise Crash()
                return original(operation, *args, **kwargs)
            fault.setattr(world.bridge, "edge", edge)
        else:
            original = c._write
            def write(bridge, value):
                original(bridge, value)
                if value["google"]["stage"] == "released":
                    raise Crash()
            fault.setattr(c, "_write", write)
        with pytest.raises(Crash):
            c.control(world.bridge, "connection-activate", {})


@pytest.mark.parametrize("prefix", ["applying-before-owner", "applying-after-receipt", "releasing-before-native", "releasing-after-native", "released"])
@pytest.mark.parametrize("interruption", ["restart", "reboot"])
def test_committed_or_applying_google_recovery_after_restart_never_replays_authorization(world, monkeypatch, prefix, interruption):
    committed_google_prefix(world, monkeypatch, prefix)
    if interruption == "reboot":
        world.bridge.gateway_service.boot_identity = lambda: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        world.bridge.pid += 1
        world.bridge.started = 1
        world.bridge.proof = None
    else:
        world.bridge.command(["restart"])
    before = world.bridge.pid, world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"), len(world.google.calls)
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert world.bridge.pending() is None and world.bridge.native_phase == world.bridge.edge_phase == "idle"
    assert len(world.google.calls) == before[3]
    if prefix == "applying-before-owner":
        assert "google" not in (record(world) or {}).get("connectors", {})
    else:
        assert "google" in record(world)["connectors"]
        assert before[:3] == (world.bridge.pid, world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"))
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "unchanged"


@pytest.mark.parametrize("boundary", ["intent-before", "intent-after", "edge-accepted", "native-accepted", "proof-after", "reset-before", "reset-after"])
def test_release_revalidation_crash_prefixes_resume_without_apply_restart_or_oauth(world, monkeypatch, boundary):
    committed_google_prefix(world, monkeypatch, "released")
    world.bridge.command(["restart"])
    before = world.bridge.pid, world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"), len(world.google.calls)
    original_write, original_edge, original_native, original_verify = c._write, world.bridge.edge, world.bridge.native, c._verify
    once = False

    def stop(name):
        nonlocal once
        if boundary == name and not once:
            once = True
            raise Crash()

    def write(bridge, value):
        previous = bridge.pending()
        intent = previous["google"]["stage"] != "revalidating" and value["google"]["stage"] == "revalidating"
        reset = previous["google"]["stage"] == "revalidating" and value["google"]["stage"] == "releasing"
        if intent:
            stop("intent-before")
        if reset:
            stop("reset-before")
        original_write(bridge, value)
        if intent:
            stop("intent-after")
        if reset:
            stop("reset-after")

    def edge(operation=None, *args, **kwargs):
        result = original_edge(operation, *args, **kwargs)
        if operation == "drain":
            stop("edge-accepted")
        return result

    def native(operation=None, *args, **kwargs):
        result = original_native(operation, *args, **kwargs)
        if operation == "acquire":
            stop("native-accepted")
        return result

    def verify(*args):
        result = original_verify(*args)
        stop("proof-after")
        return result

    with monkeypatch.context() as fault:
        fault.setattr(c, "_write", write)
        fault.setattr(world.bridge, "edge", edge)
        fault.setattr(world.bridge, "native", native)
        fault.setattr(c, "_verify", verify)
        with pytest.raises(Crash):
            c.control(world.bridge, "connection-activate", {})
    assert world.bridge.pending()["google"]["stage"] in ("released", "revalidating", "releasing")
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert before == (world.bridge.pid, world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"), len(world.google.calls))
    assert world.bridge.pending() is None and world.bridge.native_phase == world.bridge.edge_phase == "idle"


@pytest.mark.parametrize("drift", ["record", "token", "config", "boundary", "marker", "receipt", "answers"])
def test_committed_release_revalidation_never_adopts_any_target_drift(world, monkeypatch, drift):
    committed_google_prefix(world, monkeypatch, "released")
    world.bridge.command(["restart"])
    if drift == "record":
        c._write_record(dict(record(world), updatedAt="2026-10-28T00:00:00Z"))
    elif drift == "token":
        world.paths.token.touch()
    elif drift == "config":
        world.bridge.config_path.write_bytes(world.bridge.config_path.read_bytes() + b" ")
    elif drift == "boundary":
        world.bridge.dropin.write_text("only-created-fixture-boundary")
    elif drift == "marker":
        world.bridge.marker_path.unlink()
    elif drift == "receipt":
        path = world.bridge.owner_state / tx.COMPLETED
        value = json.loads(path.read_bytes())
        atomic_json(path, dict(value, transactionId="e" * 64))
    else:
        value = json.loads(world.bridge.answers_path.read_bytes())
        atomic_json(world.bridge.answers_path, dict(value, emailLimbEnabled=False))
    before = world.bridge.pid, world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"), len(world.google.calls)
    expected = "^model-marker-missing$" if drift == "marker" else "^connection-(custody-changed|completion-mismatch)$"
    for _ in range(2):
        with pytest.raises(AccessError, match=expected):
            c.control(world.bridge, "connection-activate", {})
        assert world.bridge.pending()["google"]["stage"] == "released"
        assert world.bridge.native_phase == world.bridge.edge_phase == "idle"
        assert before == (world.bridge.pid, world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"), len(world.google.calls))


@pytest.mark.parametrize("activity", ["native", "edge", "stopped"])
def test_committed_release_revalidation_waits_for_actual_idle_without_force(world, monkeypatch, activity):
    committed_google_prefix(world, monkeypatch, "released")
    world.bridge.command(["restart"])
    original_native, original_edge = world.bridge.native, world.bridge.edge
    blocked = [True]
    if activity == "native":
        world.bridge.active = 1
    elif activity == "edge":
        monkeypatch.setattr(world.bridge, "edge", lambda *args, **kwargs: dict(original_edge(*args, **kwargs), streams=int(blocked[0])))
    else:
        monkeypatch.setattr(world.bridge, "native", lambda *args, **kwargs: dict(original_native(*args, **kwargs), stopped=blocked[0]))
    monkeypatch.setattr(c, "GOOGLE_DRAIN_SECONDS", .03)
    before = world.bridge.pid, world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"), len(world.google.calls)
    with pytest.raises(AccessError, match="^runtime-busy$"):
        c.control(world.bridge, "connection-activate", {})
    assert world.bridge.pending()["google"]["stage"] == "revalidating"
    assert world.bridge.edge_phase == "held"
    blocked[0] = False
    world.bridge.active = 0
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert before == (world.bridge.pid, world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"), len(world.google.calls))
    assert world.bridge.pending() is None and world.bridge.native_phase == world.bridge.edge_phase == "idle"


def interrupt_google_exchange(world, monkeypatch):
    original = custody.exchange
    def lost(*args):
        original(*args)
        raise Crash()
    with monkeypatch.context() as fault:
        fault.setattr(custody, "exchange", lost)
        with pytest.raises(Crash):
            connect(world)


def test_one_safe_finish_preserves_the_error_but_allows_a_new_signin_without_replaying_oauth(world, monkeypatch):
    interrupt_google_exchange(world, monkeypatch)
    original = c._write_lifecycle
    completions = []
    def write(bridge, value):
        if value.get("admissionComplete"):
            hold = bridge.pending()
            assert hold["google"]["stage"] == "released"
            assert hold["google"]["nativeReleased"] and hold["google"]["edgeReleased"]
            assert bridge.native_phase == bridge.edge_phase == "idle"
            completions.append(True)
        original(bridge, value)
    monkeypatch.setattr(c, "_write_lifecycle", write)
    before_calls = len(world.google.calls)
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert completions == [True] and world.bridge.pending() is None
    assert journal(world)["phase"] == "failed" and journal(world)["error"] == c.EXCHANGE_INTERRUPTED
    assert journal(world)["admissionComplete"] is True
    assert "admissionComplete" not in json.dumps(public_status(world))
    assert not world.paths.token.exists() and "google" not in (record(world) or {}).get("connectors", {})
    flow = begin(world)
    assert flow["flowId"] == journal(world)["flowId"] and journal(world)["phase"] == "consent"
    assert "admissionComplete" not in journal(world) and len(world.google.calls) == before_calls


@pytest.mark.parametrize("phase", ["activating", "failed"])
def test_legacy_ambiguous_exchange_without_terminal_proof_still_blocks_no_google_no_token(world, phase):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    # No record/token does not prove that the old gateway's enabled tools were
    # safely withdrawn. This models an old ambiguous cleanup before its apply.
    c._write_record(None)
    custody.forget_sign_in(world.paths)
    c._write_lifecycle(world.bridge, c._lifecycle(phase, error=c.EXCHANGE_INTERRUPTED))
    before = world.bridge.config_path.read_bytes(), c._lifecycle_path(world.bridge).read_bytes(), world.commands[:]
    assert world.bridge.pending() is None and not world.paths.token.exists()
    assert planner.PLUGIN_ID in json.loads(before[0])["plugins"]["allow"]
    for _ in range(2):
        with pytest.raises(AccessError, match="^" + c.EXCHANGE_INTERRUPTED + "$"):
            begin(world)
    assert before == (world.bridge.config_path.read_bytes(), c._lifecycle_path(world.bridge).read_bytes(), world.commands)


@pytest.mark.parametrize("phase,marker", [("failed", False), ("failed", "yes"), ("failed", 1), ("activating", True)])
def test_invalid_or_active_completion_marker_cannot_waive_legacy_exchange_barrier(world, phase, marker):
    with world.bridge.locked():
        value = dict(c._lifecycle(phase, error=c.EXCHANGE_INTERRUPTED), admissionComplete=marker)
        atomic_json(c._lifecycle_path(world.bridge), value)
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.validate(value)
    before = c._lifecycle_path(world.bridge).read_bytes()
    with pytest.raises(AccessError, match="^" + c.EXCHANGE_INTERRUPTED + "$"):
        begin(world)
    assert c._lifecycle_path(world.bridge).read_bytes() == before and not world.google.calls


def test_completion_marker_before_root_unlink_never_overrides_a_retained_hold(world, monkeypatch):
    interrupt_google_exchange(world, monkeypatch)
    original = c._write_lifecycle
    once = False
    def crash(bridge, value):
        nonlocal once
        original(bridge, value)
        if value.get("admissionComplete") and not once:
            once = True
            raise Crash()
    with monkeypatch.context() as fault:
        fault.setattr(c, "_write_lifecycle", crash)
        with pytest.raises(Crash):
            c.control(world.bridge, "connection-activate", {})
    assert journal(world)["admissionComplete"] is True
    assert world.bridge.pending()["google"]["stage"] == "released"
    with pytest.raises(AccessError, match="^connection-transition-in-progress$"):
        begin(world)
    before = len(world.google.calls), world.bridge.log.count("connection-apply"), world.bridge.log.count("restart")
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert before == (len(world.google.calls), world.bridge.log.count("connection-apply"), world.bridge.log.count("restart"))
    assert world.bridge.pending() is None
    begin(world)
    assert len(world.google.calls) == before[0]


def test_unconfirmed_new_gateway_keeps_both_leases_and_exposes_finish(world, monkeypatch):
    connect(world)
    monkeypatch.setattr(c, "_restart", lambda *_: "verified")  # Supervisor's unproven successful reply.
    before_pid = world.bridge.pid
    with pytest.raises(AccessError, match="connection-restart-unconfirmed"):
        c.control(world.bridge, "connection-activate", {})
    assert world.bridge.pid == before_pid
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    assert "native-release" not in world.bridge.log and "edge-release" not in world.bridge.log
    observed = public_status(world)
    assert observed["reason"] == "connection-admission-held"
    assert observed["connectors"]["google"]["pending"]["error"] == "connection-restart-unconfirmed"
    with pytest.raises(AccessError, match="connection-transition-in-progress"):
        begin(world)


@pytest.mark.parametrize("operation", ["begin", "callback", "disconnect", "plugin", "activate"])
@pytest.mark.parametrize("damage", ["held", "invalid"])
def test_source_reserved_before_root_journal_excludes_fresh_connection_writers(world, operation, damage):
    import pixel_source_upgrade as source
    callback = None
    if operation == "callback":
        flow = begin(world)
        callback = {"flowId": flow["flowId"], "state": consent_state(world, flow["flowId"]), "code": "4/fixture"}
    with world.bridge.locked():
        directory = Path(world.bridge.state) / "source-upgrade"
        directory.mkdir(mode=0o700)
        manager = source.SourceUpgrade(directory, world.bridge.install, world.bridge.owner.pw_uid, state_uid=os.geteuid())
        manager._save({"version": 1, "install": str(world.bridge.install), "uid": world.bridge.owner.pw_uid,
            "identity": {"beforeRef": "a" * 40, "afterRef": "b" * 40, "markerSha256": "c" * 64,
                         "configSha256": "d" * 64, "receiptSha256": None},
            "before": {}, "after": {}, "candidate": {}, "hold": "e" * 64, "phase": "held", "outcome": None})
        if damage == "invalid":
            (directory / "source-upgrade.json").write_text("{invalid")
    before = {str(p): p.read_bytes() for p in world.bridge.home.parent.rglob("*") if p.is_file()}
    error = "source-transition-in-progress" if damage == "held" else "source-transition-unavailable"
    with pytest.raises(AccessError, match="^" + error + "$"):
        if operation == "begin":
            begin(world)
        elif operation == "callback":
            c.control(world.bridge, "connection-complete", callback)
        elif operation == "disconnect":
            c.control(world.bridge, "connection-disconnect", {"connector": "google"})
        elif operation == "plugin":
            c.control(world.bridge, "connection-plugin", {"plugin": "memory-wiki", "enabled": True})
        else:
            c.control(world.bridge, "connection-activate", {})
    assert {str(p): p.read_bytes() for p in world.bridge.home.parent.rglob("*") if p.is_file()} == before
    assert not world.google.calls and world.bridge.pending() is None
    assert world.bridge.native_phase == world.bridge.edge_phase == "idle"


@pytest.mark.parametrize("kind", ["google", "source", "native", "model"])
def test_access_mode_pre_and_finally_hooks_cannot_mutate_another_admitted_snapshot(world, kind):
    import pixel_source_upgrade as source
    import test_connection_native_guard as native_fixture
    direct = dict(READ_BOTH, **{"calendar.write": True, "calendar.direct": True})
    world.google.token = granted(GMAIL, "https://www.googleapis.com/auth/calendar.events")
    flow = begin(world, capabilities=direct)
    c.control(world.bridge, "connection-complete", {"flowId": flow["flowId"],
        "state": consent_state(world, flow["flowId"]), "code": "4/fixture"})
    c.control(world.bridge, "connection-activate", {})
    assert record(world)["connectors"]["google"]["directPaused"] is False
    if kind == "google":
        flow = begin(world, capabilities=direct)
        with world.bridge.locked():
            hold = c._google_reserve(world.bridge, flow["flowId"])
            c._google_drain(world.bridge, hold)
        expected = "connection-transition-in-progress"
    elif kind == "native":
        native_fixture.journal(world, "prepared")
        expected = "native-app-transition-in-progress"
    elif kind == "source":
        directory = Path(world.bridge.state) / "source-upgrade"
        directory.mkdir(mode=0o700)
        manager = source.SourceUpgrade(directory, world.bridge.install, world.bridge.owner.pw_uid, state_uid=os.geteuid())
        manager._save({"version": 1, "install": str(world.bridge.install), "uid": world.bridge.owner.pw_uid,
            "identity": {"beforeRef": "a" * 40, "afterRef": "b" * 40, "markerSha256": "c" * 64,
                         "configSha256": "d" * 64, "receiptSha256": None},
            "before": {}, "after": {}, "candidate": {}, "hold": "e" * 64, "phase": "held", "outcome": None})
        expected = "source-transition-in-progress"
    else:
        atomic_json(Path(world.bridge.state) / "transition.json", {"kind": "model"})
        expected = "transition-recovery-required"
    before = {str(p): p.read_bytes() for p in world.bridge.home.parent.rglob("*") if p.is_file()}
    commands = world.commands[:]
    with pytest.raises(AccessError, match="^" + expected + "$"):
        c.pause_for_full_access(world.bridge)
    assert c.follow_access_mode(world.bridge) is False
    assert {str(p): p.read_bytes() for p in world.bridge.home.parent.rglob("*") if p.is_file()} == before
    assert world.commands == commands
    if kind == "google":
        assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
        assert world.bridge.pending() is None


@pytest.mark.parametrize("damage", ["null", "bad-stage", "bad-release", "oversize", "symlink"])
def test_malformed_admission_is_conservative_and_cannot_reopen_signin(world, damage):
    connect(world)
    path = Path(world.bridge.state) / "transition.json"
    value = world.bridge.pending()
    if damage == "null":
        path.write_text("null")
    elif damage == "oversize":
        path.write_text(" " * (lifecycle.MAX_BYTES + 1))
    elif damage == "symlink":
        copy = path.with_name("bad-private-copy.json")
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
    else:
        if damage == "bad-stage":
            value["google"]["stage"] = "released"
        else:
            value["google"]["edgeReleaseRequested"] = True
        path.write_text(json.dumps(value))
    observed = public_status(world)
    assert observed["reason"] == "journal-unusable"
    assert observed["connectors"]["google"]["pending"]["error"] == "connection-transition-unavailable"
    with pytest.raises(AccessError):
        begin(world)
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"


def test_a_google_account_connects_through_the_broker_and_activates_portal(world):
    flow = begin(world)
    handoff = json.loads((world.paths.oauth / f"{flow['flowId']}.flow.json").read_text())
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(flow["authUrl"]).query)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(handoff["codeVerifier"].encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [challenge] and query["code_challenge_method"] == ["S256"]
    assert query["scope"] == [f"openid email {CALENDAR} {GMAIL}"]
    assert handoff["codeVerifier"] not in json.dumps(flow) and CLIENT["clientSecret"] not in json.dumps(flow)
    assert journal(world)["phase"] == "consent"

    reply = c.control(world.bridge, "connection-complete",
                      {"flowId": flow["flowId"], "state": handoff["state"], "code": "4/fixture-code"})
    assert reply == {"outcome": "connected", "errorClass": None, "account": "owner@example.com",
                     "capabilities": READ_BOTH, "missing": []}
    url, sent = world.google.calls[0]
    assert url == BROKER.GOOGLE_TOKEN_URI
    assert sent["code_verifier"] == [handoff["codeVerifier"]] and sent["redirect_uri"] == ["http://127.0.0.1:53682"]
    assert not list(world.paths.oauth.iterdir()), "the handoffs and the result were consumed"
    assert json.loads(world.paths.token.read_text())["email"] == "owner@example.com"
    assert record(world)["connectors"]["google"]["account"] == "owner@example.com"
    assert record(world)["ownerTimeZone"] == "America/Chicago"
    env = dict(custody.parse_env(world.paths.env.read_text()))
    assert env["PIXEL_LIMB_EMAIL_ENABLED"] == env["PIXEL_LIMB_CALENDAR_ENABLED"] == "0"
    assert ["systemctl", "enable", "--now", "pixel-source-broker.timer"] not in world.commands
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    assert sorted(path.name for path in world.paths.units.iterdir()) == sorted(custody.UNITS)
    assert journal(world)["phase"] == "activating"

    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "applied"}
    env = dict(custody.parse_env(world.paths.env.read_text()))
    assert env["PIXEL_LIMB_EMAIL_ENABLED"] == env["PIXEL_LIMB_CALENDAR_ENABLED"] == "1"
    assert env["PIXEL_SOURCE_GMAIL_QUERY"] == "in:inbox newer_than:180d"
    assert env["PIXEL_SOURCE_TIME_ZONE"] == "America/Chicago"
    assert ["systemctl", "enable", "--now", "pixel-source-broker.timer"] in world.commands
    config = json.loads(world.bridge.config_path.read_text())
    assert planner.PLUGIN_ID in config["plugins"]["allow"]
    assert config["agents"]["defaults"]["userTimezone"] == "America/Chicago"
    assert planner.gateway_limbs(world.bridge.env_path.read_text())["email"] is True
    assert json.loads(world.bridge.answers_path.read_text())["googleAccount"] == "owner@example.com"
    assert world.bridge.log.count("restart") == 1
    assert world.bridge.native_phase == world.bridge.edge_phase == "idle"
    assert not (Path(world.bridge.state) / "transition.json").exists()
    assert journal(world) is None
    marker = json.loads(world.bridge.marker_path.read_text())
    assert marker["configuration_sha256"] == model_coordinator._marker_digest(config)
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "unchanged"}
    assert world.bridge.log.count("restart") == 1


def test_a_forged_callback_leaves_the_owners_sign_in_open(world):
    flow = begin(world)
    with pytest.raises(AccessError, match="sign-in-state-mismatch"):
        c.control(world.bridge, "connection-complete", {"flowId": flow["flowId"], "state": "x" * 43, "code": "4/forged"})
    assert (world.paths.oauth / f"{flow['flowId']}.flow.json").exists()
    assert journal(world)["phase"] == "consent"
    assert world.google.calls == []


@pytest.mark.parametrize("boundary", ["native-channel", "service-process"])
def test_connection_activation_waits_for_the_gateways_config_reload(world, monkeypatch, boundary):
    """The real gateway may restart itself after the owner's atomic config replace."""
    connect(world)
    attempts = []
    original_native, original_identity = world.bridge.native, c._identity

    def after_owner_write():
        config = json.loads(world.bridge.config_path.read_bytes())
        return planner.PLUGIN_ID in config["plugins"]["allow"] and not world.bridge.log.count("restart")

    def native(operation=None, token=None, **kwargs):
        if boundary == "native-channel" and after_owner_write() and not attempts:
            attempts.append(boundary)
            raise AccessError("runtime-unavailable-or-busy")
        return original_native(operation, token, **kwargs)

    def identity(bridge):
        if boundary == "service-process" and after_owner_write() and not attempts:
            attempts.append(boundary)
            raise AccessError("settings-process-not-active")
        return original_identity(bridge)

    monkeypatch.setattr(world.bridge, "native", native)
    monkeypatch.setattr(c, "_identity", identity)
    monkeypatch.setattr(c.time, "sleep", lambda _seconds: None)
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "applied"}
    assert attempts == [boundary]
    assert world.bridge.log.count("connection-apply") == world.bridge.log.count("restart") == 1
    assert world.bridge.native_phase == world.bridge.edge_phase == "idle"
    assert not (Path(world.bridge.state) / "transition.json").exists()


def test_reload_wait_does_not_retry_a_gateway_identity_failure(world, monkeypatch):
    connect(world)
    original = world.bridge.native
    failures = []

    def native(operation=None, token=None, **kwargs):
        config = json.loads(world.bridge.config_path.read_bytes())
        if operation == "acquire" and planner.PLUGIN_ID in config["plugins"]["allow"]:
            failures.append(True)
            raise AccessError("gateway-process-mismatch")
        return original(operation, token, **kwargs)

    monkeypatch.setattr(world.bridge, "native", native)
    with pytest.raises(AccessError, match="gateway-process-mismatch"):
        c.control(world.bridge, "connection-activate", {})
    assert failures == [True]
    assert world.bridge.log.count("restart") == 0
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    assert (Path(world.bridge.state) / "transition.json").is_file()


def test_reload_wait_expires_without_a_restart_or_admission_write(world, monkeypatch):
    clock, operations = [0.0], []

    def unavailable(operation=None, token=None, **kwargs):
        operations.append(operation)
        raise AccessError("runtime-unavailable-or-busy")

    monkeypatch.setattr(world.bridge, "native", unavailable)
    monkeypatch.setattr(c, "RELOAD_READY_SECONDS", 1)
    monkeypatch.setattr(c.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(c.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert c._restart(world.bridge, {"phase": "invoking", "token": "a" * 64}) == "unavailable"
    assert clock[0] == 1 and operations and all(item is None for item in operations)
    assert world.bridge.log.count("restart") == world.bridge.log.count("connection-apply") == 0


@pytest.mark.parametrize("http_status", [401, 403, 500])
def test_reload_wait_does_not_retry_an_http_refusal(world, monkeypatch, http_status):
    reads = []

    def refused(*args, **kwargs):
        reads.append(True)
        raise AccessError("runtime-unavailable-or-busy", http_status=http_status)

    monkeypatch.setattr(world.bridge, "native", refused)
    with pytest.raises(AccessError) as failure:
        c._restart(world.bridge, {"phase": "invoking", "token": "a" * 64})
    assert failure.value.http_status == http_status and reads == [True]
    assert world.bridge.log.count("restart") == 0


def test_reload_wait_never_replays_a_possibly_accepted_admission_post(world, monkeypatch):
    connect(world)
    original = world.bridge.native
    writes = []

    def native(operation=None, token=None, **kwargs):
        config = json.loads(world.bridge.config_path.read_bytes())
        if operation == "acquire" and planner.PLUGIN_ID in config["plugins"]["allow"]:
            writes.append(True)
            raise AccessError("runtime-operation-timeout")
        return original(operation, token, **kwargs)

    monkeypatch.setattr(world.bridge, "native", native)
    with pytest.raises(AccessError, match="runtime-operation-timeout"):
        c.control(world.bridge, "connection-activate", {})
    assert writes == [True] and world.bridge.log.count("restart") == 0
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    assert (Path(world.bridge.state) / "transition.json").is_file()


def test_reload_wait_on_resume_waits_for_an_active_process_without_a_channel(world, monkeypatch):
    original, reads = world.bridge.native, []
    identity = c._identity(world.bridge)

    def native(*args, **kwargs):
        reads.append(True)
        if len(reads) == 1:
            raise AccessError("runtime-unavailable-or-busy")
        return original(*args, **kwargs)

    def not_stopped(_token):
        raise AccessError("native-idle-unconfirmed")

    monkeypatch.setattr(world.bridge, "native", native)
    monkeypatch.setattr(world.bridge, "stopped_native", not_stopped)
    monkeypatch.setattr(c.time, "sleep", lambda _seconds: None)
    assert c._restart_baseline(world.bridge, {"phase": "restarting", "token": "a" * 64,
                                             "restartIdentity": identity}) == identity
    assert reads == [True, True]


def test_reload_wait_preserves_proven_stopped_recovery_for_an_already_journaled_restart(world, monkeypatch):
    identity = c._identity(world.bridge)
    holds = []

    def stopped_process(_bridge):
        raise AccessError("settings-process-not-active")

    def stopped_hold(token):
        holds.append(token)
        return {"stopped": True}

    monkeypatch.setattr(c, "_identity", stopped_process)
    monkeypatch.setattr(world.bridge, "stopped_native", stopped_hold)
    assert c._restart_baseline(world.bridge, {"phase": "restarting", "token": "a" * 64,
                                             "restartIdentity": identity}) == identity
    assert holds == ["a" * 64] and world.bridge.log.count("restart") == 0


def test_reload_wait_does_not_accept_a_native_snapshot_from_another_process(world, monkeypatch):
    identity = c._identity(world.bridge)
    monkeypatch.setattr(world.bridge, "native", lambda **_kwargs: {"pid": identity["pid"] + 1})
    with pytest.raises(AccessError, match="connection-process-changed"):
        c._restart_baseline(world.bridge, {"phase": "invoking", "token": "a" * 64})
    assert world.bridge.log.count("restart") == 0


def failed_because(world, error):
    """Settings learns why the last sign-in did not connect, even when only Google's redirect saw it."""
    value = journal(world)
    if value["error"] != error:
        return False
    if value["phase"] == "failed":
        return lifecycle.public(value)["action"] == "connect"
    hold = world.bridge.pending()
    return (value["phase"] in ("activating", "deactivating") and hold is not None
            and hold.get("google", {}).get("result") == "failed")


def test_a_denied_or_expired_sign_in_leaves_nothing_behind_but_the_reason(world, monkeypatch):
    flow = begin(world)
    reply = c.control(world.bridge, "connection-complete",
                      {"flowId": flow["flowId"], "state": consent_state(world, flow["flowId"]), "error": "access-denied"})
    assert reply["outcome"] == "failed" and reply["errorClass"] == "access-denied"
    assert failed_because(world, "access-denied")
    assert not world.record.exists() and not list(world.paths.oauth.iterdir())
    flow = begin(world)
    assert journal(world)["phase"] == "consent", "a new sign-in replaces the last reason"
    later = c._now() + timedelta(seconds=oauth.FLOW_SECONDS + 1)
    monkeypatch.setattr(c, "_now", lambda: later)
    with pytest.raises(AccessError, match="sign-in-flow-expired"):
        c.control(world.bridge, "connection-complete",
                  {"flowId": flow["flowId"], "state": consent_state(world, flow["flowId"]), "code": "4/late"})
    assert failed_because(world, "sign-in-flow-expired") and not list(world.paths.oauth.iterdir())
    assert world.google.calls == []


def test_google_refusing_the_code_records_nothing(world):
    world.google.token = lambda body: (400, {"error": "invalid_grant", "error_description": "Bad Request"})
    reply = connect(world)
    assert reply["outcome"] == "failed" and reply["errorClass"] == "invalid-grant"
    assert not world.record.exists() and not world.paths.token.exists() and failed_because(world, "invalid-grant")
    assert not list(world.paths.oauth.iterdir())
    # The admitted callback retains its gates until Finish proves the safe
    # no-Google runtime; a failed exchange is not authority to reopen it.
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "applied"}
    assert failed_because(world, "invalid-grant") and world.bridge.pending() is None


def test_a_broker_that_cannot_run_the_exchange_is_reported(world, monkeypatch):
    flow = begin(world)
    monkeypatch.setattr(c, "RUN", lambda arguments, timeout:
                        1 if arguments[-1].startswith("pixel-source-exchange@") else 0)
    with pytest.raises(AccessError, match="sign-in-exchange-unavailable"):
        c.control(world.bridge, "connection-complete",
                  {"flowId": flow["flowId"], "state": consent_state(world, flow["flowId"]), "code": "4/fixture-code"})
    assert failed_because(world, "sign-in-exchange-unavailable")
    assert not world.record.exists() and not list(world.paths.oauth.iterdir())


def seed_old_google_data(world):
    """Synthetic old account data; no installed configuration or real provider."""
    for name in ("email.json", "calendar.json", ".gmail-sync-progress.json", ".calendar-sync-progress.json"):
        (world.paths.projection / name).write_text(json.dumps({"oldAccount": name}))
    (world.paths.projection / "social.json").write_text('{"social":"independent"}')
    (world.paths.proposals / "old-calendar.pending.json").write_text('{"oldProposal":true}')
    (world.paths.results / "old-calendar.result.json").write_text('{"oldReceipt":true}')


def archived_actions(world):
    archive = world.paths.private / "retired-google-actions"
    return {path.name: path.read_text() for path in archive.rglob("*.json")} if archive.exists() else {}


def reconnect_callback(world):
    flow = begin(world)
    return {"flowId": flow["flowId"], "state": consent_state(world, flow["flowId"]), "code": "4/new-account"}


def test_reconnect_quiesces_readers_and_actions_before_the_real_token_exchange(world, monkeypatch):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    seed_old_google_data(world)
    before_token = custody.sign_in_identity(world.paths)
    callback = reconnect_callback(world)
    original = c.RUN
    examined = []

    def run(arguments, timeout):
        if arguments[-1].startswith("pixel-source-exchange@"):
            examined.append(True)
            for unit in ("pixel-source-broker.service", "pixel-source-direct.service",
                         "pixel-source-action@*.service", "pixel-source-reconcile@*.service"):
                assert ["systemctl", "stop", unit] in world.commands
            for unit in ("pixel-source-broker.timer", "pixel-source-direct.path"):
                assert ["systemctl", "disable", "--now", unit] in world.commands
            assert journal(world)["error"] is None
            assert world.bridge.pending()["google"]["stage"] == "exchanging"
            assert world.bridge.native_phase == world.bridge.edge_phase == "held"
            assert "google" not in record(world)["connectors"]
            assert {path.name for path in world.paths.projection.iterdir()} == {"social.json"}
            assert not list(world.paths.proposals.iterdir()) and not list(world.paths.results.iterdir())
            assert set(archived_actions(world)) == {"old-calendar.pending.json", "old-calendar.result.json"}
            assert (world.paths.oauth / f"{callback['flowId']}.flow.json").exists()
            assert (world.paths.oauth / f"{callback['flowId']}.code.json").exists()
        return original(arguments, timeout)

    world.commands.clear()
    monkeypatch.setattr(c, "RUN", run)
    response = c.control(world.bridge, "connection-complete", callback)
    assert response["outcome"] == "connected" and examined == [True]
    assert custody.sign_in_identity(world.paths) != before_token
    assert journal(world)["phase"] == "activating" and journal(world)["error"] is None
    assert ["systemctl", "enable", "--now", "pixel-source-broker.timer"] not in world.commands
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    exchange_index = next(i for i, command in enumerate(world.commands) if command[-1].startswith("pixel-source-exchange@"))
    assert world.commands.index(["systemctl", "enable", "--now", "pixel-source-broker.timer"]) > exchange_index
    private_archive = world.paths.private / "retired-google-actions"
    assert private_archive.stat().st_mode & 0o777 == 0o700
    assert not list(private_archive.rglob("*token*")) and not list(private_archive.rglob("*flow*"))


def test_a_forged_reconnect_never_stops_or_retires_a_working_account(world):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    seed_old_google_data(world)
    callback = reconnect_callback(world)
    before = (world.record.read_bytes(), world.paths.token.read_bytes(), list(world.commands))
    with pytest.raises(AccessError, match="sign-in-state-mismatch"):
        c.control(world.bridge, "connection-complete", dict(callback, state="x" * 43))
    assert (world.record.read_bytes(), world.paths.token.read_bytes(), world.commands) == before
    assert (world.paths.proposals / "old-calendar.pending.json").exists()
    assert not archived_actions(world)


def test_failed_reconnect_resumes_only_the_same_private_grant_without_dispatching_old_actions(world):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    seed_old_google_data(world)
    original_record, token_identity = record(world), custody.sign_in_identity(world.paths)
    callback = reconnect_callback(world)
    world.google.token = lambda _body: (400, {"error": "invalid_grant"})
    response = c.control(world.bridge, "connection-complete", callback)
    assert response["errorClass"] == "invalid-grant"
    assert record(world) == original_record and custody.sign_in_identity(world.paths) == token_identity
    assert failed_because(world, "invalid-grant")
    assert set(archived_actions(world)) == {"old-calendar.pending.json", "old-calendar.result.json"}
    assert not list(world.paths.proposals.iterdir()) and not list(world.paths.results.iterdir())
    assert not (world.paths.projection / "email.json").exists()
    assert (world.paths.projection / "social.json").exists()


def test_a_lost_exchange_reply_never_reactivates_the_old_record_with_the_new_grant(world, monkeypatch):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    seed_old_google_data(world)
    callback = reconnect_callback(world)
    original = custody.exchange

    def lost(*args):
        assert original(*args)["outcome"] == "connected"
        raise custody.CustodyError("sign-in-exchange-unavailable")

    monkeypatch.setattr(custody, "exchange", lost)
    world.commands.clear()
    with pytest.raises(AccessError, match="sign-in-exchange-unavailable"):
        c.control(world.bridge, "connection-complete", callback)
    assert "google" not in record(world)["connectors"] and not world.paths.token.exists()
    assert ["systemctl", "enable", "--now", "pixel-source-broker.timer"] not in world.commands
    assert archived_actions(world)
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert not planner.gateway_limbs(world.bridge.env_path.read_text())["email"]


@pytest.mark.parametrize("boundary", ["before-exchange", "after-token", "after-record", "after-commit"])
def test_reconnect_crash_recovery_cannot_pair_an_old_record_or_queue_with_a_new_grant(world, monkeypatch, boundary):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    seed_old_google_data(world)
    callback = reconnect_callback(world)
    original_exchange, original_write, original_ready = custody.exchange, c._write_record, c._google_ready

    def exchange(*args):
        if boundary == "before-exchange":
            raise Crash()
        result = original_exchange(*args)
        if boundary == "after-token":
            raise Crash()
        return result

    def write(value):
        original_write(value)
        if boundary == "after-record" and (value or {}).get("connectors", {}).get("google"):
            raise Crash()

    def ready(*args, **kwargs):
        original_ready(*args, **kwargs)
        if boundary == "after-commit":
            raise Crash()

    monkeypatch.setattr(custody, "exchange", exchange)
    monkeypatch.setattr(c, "_write_record", write)
    monkeypatch.setattr(c, "_google_ready", ready)
    with pytest.raises(Crash):
        c.control(world.bridge, "connection-complete", callback)
    assert journal(world)["phase"] == "activating"
    assert not list(world.paths.proposals.iterdir())
    if boundary != "after-commit":
        assert world.bridge.pending()["google"]["stage"] == "exchanging"
        for operation, request in (("connection-sync", {}), ("connection-begin", {
                "connector": "google", "client": CLIENT, "capabilities": READ_BOTH,
                "cloudModels": False, "ownerTimeZone": None, "loginHint": None, "redirectPort": 53682})):
            with pytest.raises(AccessError, match="connection-transition-in-progress"):
                c.control(world.bridge, operation, request)
        with pytest.raises(AccessError, match="connection-transition-in-progress"):
            c._connected_google(world.bridge)
    monkeypatch.setattr(c, "_write_record", original_write)
    monkeypatch.setattr(c, "_google_ready", original_ready)
    assert c.control(world.bridge, "connection-activate", {})["outcome"] in ("applied", "unchanged")
    assert world.bridge.pending() is None and archived_actions(world)
    assert (world.paths.projection / "social.json").exists()
    if boundary == "after-commit":
        assert journal(world) is None
        assert world.paths.token.exists() and "google" in record(world)["connectors"]
        assert ["systemctl", "enable", "--now", "pixel-source-broker.timer"] in world.commands
    else:
        assert failed_because(world, c.EXCHANGE_INTERRUPTED)
        assert not world.paths.token.exists() and "google" not in record(world)["connectors"]
        assert not planner.gateway_limbs(world.bridge.env_path.read_text())["email"]


def test_a_failed_stop_prevents_any_token_exchange_and_leaves_explicit_recovery(world, monkeypatch):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    callback = reconnect_callback(world)
    previous_record, token_identity = record(world), custody.sign_in_identity(world.paths)
    original = c.RUN
    calls = len(world.google.calls)
    monkeypatch.setattr(c, "RUN", lambda args, timeout:
                        1 if args == ["systemctl", "stop", "pixel-source-broker.service"] else original(args, timeout))
    with pytest.raises(AccessError, match="host-command-failed"):
        c.control(world.bridge, "connection-complete", callback)
    assert record(world) == previous_record and custody.sign_in_identity(world.paths) == token_identity
    assert len(world.google.calls) == calls and journal(world)["error"] == "host-command-failed"
    monkeypatch.setattr(c, "RUN", original)
    assert c.control(world.bridge, "connection-activate", {})["outcome"] == "applied"
    assert "google" not in record(world)["connectors"] and not world.paths.token.exists()


def test_a_capability_google_did_not_grant_stays_off(world):
    world.google.token = granted(CALENDAR)
    reply = connect(world)
    assert reply["outcome"] == "connected" and reply["missing"] == ["gmail.read"]
    assert record(world)["connectors"]["google"]["capabilities"]["gmail.read"] is False
    assert dict(custody.parse_env(world.paths.env.read_text()))["PIXEL_LIMB_EMAIL_ENABLED"] == "0"


def test_a_sign_in_without_a_verified_account_is_revoked_and_deleted(world):
    world.google.token = granted(GMAIL, CALENDAR, with_identity=False)
    reply = connect(world)
    assert reply == {"outcome": "failed", "errorClass": "account-unverified", "account": None,
                     "capabilities": None, "missing": []}
    assert [url for url, _body in world.google.calls] == [BROKER.GOOGLE_TOKEN_URI, BROKER.GOOGLE_REVOKE_URI]
    assert not world.paths.token.exists() and not world.record.exists() and failed_because(world, "account-unverified")


def test_a_busy_portal_is_never_interrupted_and_activation_resumes_later(world):
    connect(world)
    before = world.bridge.config_path.read_bytes()
    world.bridge.active = 1
    with pytest.raises(AccessError, match="runtime-busy"):
        c.control(world.bridge, "connection-activate", {})
    assert world.bridge.config_path.read_bytes() == before and world.bridge.log.count("restart") == 0
    assert world.bridge.pending()["google"]["stage"] == "ready"
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    assert journal(world)["phase"] == "activating" and journal(world)["error"] == "runtime-busy"
    world.bridge.active = 0
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "applied"}
    assert journal(world) is None


def test_a_plugin_that_does_not_load_finishes_a_proven_no_google_target_without_false_connection(world):
    connect(world)
    world.bridge.plugin_ready = lambda enabled: not enabled
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "applied"}
    assert "google" not in record(world)["connectors"] and not world.paths.token.exists()
    assert not planner.gateway_limbs(world.bridge.env_path.read_text())["email"]
    assert not planner.gateway_limbs(world.bridge.env_path.read_text())["calendar"]
    assert world.bridge.log.count("restart") == 2, "rollback stays held until the no-Google step restarts and proves"
    assert journal(world)["error"] == "gateway-change-rolled-back"
    assert world.bridge.native_phase == world.bridge.edge_phase == "idle"
    assert not (Path(world.bridge.state) / "transition.json").exists()


def test_an_interrupted_activation_is_settled_and_never_applied_twice(world, monkeypatch):
    connect(world)
    real = c._bind_marker

    def crash(bridge, journal):
        monkeypatch.setattr(c, "_bind_marker", real)
        raise Crash()
    monkeypatch.setattr(c, "_bind_marker", crash)
    with pytest.raises(Crash):
        c.control(world.bridge, "connection-activate", {})
    assert world.bridge.pending()["kind"] == "connection"
    assert world.bridge.native_phase == world.bridge.edge_phase == "held"
    assert journal(world)["phase"] == "activating"
    assert c.control(world.bridge, "connection-activate", {})["outcome"] in ("applied", "unchanged")
    assert world.bridge.log.count("restart") == 1
    assert world.bridge.native_phase == world.bridge.edge_phase == "idle"
    assert world.bridge.pending() is None and journal(world) is None
    config = json.loads(world.bridge.config_path.read_text())
    assert planner.PLUGIN_ID in config["plugins"]["allow"]
    marker = json.loads(world.bridge.marker_path.read_text())
    assert marker["configuration_sha256"] == model_coordinator._marker_digest(config)


def test_sync_runs_only_for_a_held_sign_in(world):
    with pytest.raises(AccessError, match="connection-not-connected"):
        c.control(world.bridge, "connection-sync", {})
    connect(world)
    with pytest.raises(AccessError, match="connection-transition-in-progress"):
        c.control(world.bridge, "connection-sync", {})
    c.control(world.bridge, "connection-activate", {})
    assert c.control(world.bridge, "connection-sync", {}) == {"outcome": "started"}
    assert world.commands[-1] == ["systemctl", "start", "--no-block", "pixel-source-broker.service"]


def test_disconnect_stops_revokes_erases_and_then_portal_forgets_the_tools(world):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    (world.paths.projection / "email.json").write_text("{}")
    (world.paths.results / ".journal").mkdir()
    (world.paths.results / ".journal" / "entry").write_text("{}")
    reply = c.control(world.bridge, "connection-disconnect", {"connector": "google"})
    assert reply == {"outcome": "disconnected", "revoked": True}
    assert world.google.calls[-1][0] == BROKER.GOOGLE_REVOKE_URI
    assert world.google.calls[-1][1]["token"] == ["example-refresh-from-google"]
    assert ["systemctl", "disable", "--now", "pixel-source-broker.timer"] in world.commands
    assert not world.paths.token.exists() and not world.paths.env.exists()
    assert not list(world.paths.projection.iterdir()) and not list(world.paths.results.iterdir())
    assert record(world)["connectors"] == {} and record(world)["ownerTimeZone"] == "America/Chicago"
    assert journal(world)["phase"] == "deactivating"
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "applied"}
    config = json.loads(world.bridge.config_path.read_text())
    assert planner.PLUGIN_ID not in config["plugins"]["allow"]
    assert config["agents"]["defaults"]["userTimezone"] == "America/Chicago"
    assert planner.gateway_limbs(world.bridge.env_path.read_text()) == planner.limbs(None)
    assert json.loads(world.bridge.answers_path.read_text())["googleAccount"] == "ods@localhost.local"
    assert journal(world) is None
    # Running it again is safe and changes nothing.
    calls = len(world.google.calls)
    assert c.control(world.bridge, "connection-disconnect", {"connector": "google"}) == {
        "outcome": "disconnected", "revoked": False}
    assert len(world.google.calls) == calls
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "unchanged"}


def test_an_unreadable_record_blocks_nothing_but_says_disconnect(world):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    world.record.write_text("{not json")
    world.record.chmod(0o644)
    with pytest.raises(AccessError, match="connection-record-unusable"):
        c.control(world.bridge, "connection-activate", {})
    # Disconnect, which the refusal names as the fix, still works and replaces the record.
    assert c.control(world.bridge, "connection-disconnect", {"connector": "google"}) == {
        "outcome": "disconnected", "revoked": True}
    assert not world.record.exists() and not world.paths.token.exists()
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "applied"}
    assert planner.PLUGIN_ID not in json.loads(world.bridge.config_path.read_text())["plugins"]["allow"]
    connect(world)
    assert record(world)["connectors"]["google"]["account"] == "owner@example.com"


def test_an_unusable_journal_never_blocks_the_owner(world):
    connect(world)
    c.control(world.bridge, "connection-activate", {})
    path = Path(world.bridge.state) / "connection.json"
    path.write_text("{not json")
    path.chmod(0o600)
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "unchanged"}
    assert not path.exists()
    path.write_text("{not json")
    path.chmod(0o600)
    stale = world.paths.oauth / f"{'b' * 32}.flow.json"
    stale.write_text("{}")
    flow = begin(world)
    assert not stale.exists() and journal(world)["flowId"] == flow["flowId"]


def test_a_new_sign_in_replaces_an_unfinished_one(world):
    first = begin(world)
    second = begin(world)
    assert first["flowId"] != second["flowId"]
    assert not (world.paths.oauth / f"{first['flowId']}.flow.json").exists()
    with pytest.raises(AccessError, match="sign-in-in-progress"):
        c.control(world.bridge, "connection-activate", {})
    assert c.control(world.bridge, "connection-cancel", {"flowId": second["flowId"]}) == {"outcome": "cancelled"}
    assert journal(world) is None and not list(world.paths.oauth.iterdir())


def test_requests_outside_the_contract_are_refused_before_any_change(world):
    base = {"connector": "google", "client": CLIENT, "capabilities": READ_BOTH, "cloudModels": False,
            "ownerTimeZone": None, "loginHint": None, "redirectPort": 53682}
    refused = [({"connector": "google"}, "invalid-connection-request"),
               (dict(base, ownerTimeZone="Not/AZone"), "invalid-connection-request"),
               (dict(base, redirectPort=80), "invalid-connection-request"),
               (dict(base, client=dict(CLIENT, clientId="not-a-google-client")), "client-invalid"),
               (dict(base, capabilities=dict(READ_BOTH, **{"gmail.read": False, "gmail.drafts": True})),
                "capability-unavailable"),
               (dict(base, capabilities=dict(READ_BOTH, **{"calendar.direct": True})), "capability-unavailable"),
               (dict(base, capabilities=dict(READ_BOTH, **{"calendar.read": False, "calendar.write": True})),
                "capability-unavailable"),
               (dict(base, capabilities=dict.fromkeys(READ_BOTH, False)), "invalid-connection-request")]
    for request, code in refused:
        with pytest.raises(AccessError, match=code):
            c.control(world.bridge, "connection-begin", request)
    assert not world.paths.state.exists() and world.commands == []
    with pytest.raises(AccessError, match="invalid-connection-operation"):
        c.control(world.bridge, "connection-anything", {})
