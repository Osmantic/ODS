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
from datetime import timedelta
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import types
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
from pixel_connections import coordinator as c, custody, lifecycle, oauth, planner, state  # noqa: E402

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
    assert env["PIXEL_LIMB_EMAIL_ENABLED"] == env["PIXEL_LIMB_CALENDAR_ENABLED"] == "1"
    assert env["PIXEL_SOURCE_GMAIL_QUERY"] == "in:inbox newer_than:180d"
    assert env["PIXEL_SOURCE_TIME_ZONE"] == "America/Chicago"
    assert ["systemctl", "enable", "--now", "pixel-source-broker.timer"] in world.commands
    assert sorted(path.name for path in world.paths.units.iterdir()) == sorted(custody.UNITS)
    assert journal(world)["phase"] == "activating"

    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "applied"}
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
    return value["phase"] == "failed" and value["error"] == error and lifecycle.public(value)["action"] == "connect"


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
    # Finish has nothing to complete for a sign-in that did not connect; it clears the reason.
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "unchanged"}
    assert journal(world) is None


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
            assert journal(world)["error"] == c.EXCHANGE_INTERRUPTED
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
    original_exchange, original_write, original_start = custody.exchange, c._write_lifecycle, custody.start

    def exchange(*args):
        if boundary == "before-exchange":
            raise Crash()
        result = original_exchange(*args)
        if boundary == "after-token":
            raise Crash()
        return result

    def write(bridge, value):
        if boundary == "after-record" and value["phase"] == "activating" and value["error"] is None:
            raise Crash()
        original_write(bridge, value)

    def start(*args, **kwargs):
        if boundary == "after-commit":
            raise Crash()
        original_start(*args, **kwargs)

    monkeypatch.setattr(custody, "exchange", exchange)
    monkeypatch.setattr(c, "_write_lifecycle", write)
    monkeypatch.setattr(custody, "start", start)
    with pytest.raises(Crash):
        c.control(world.bridge, "connection-complete", callback)
    assert journal(world)["phase"] == "activating"
    assert not list(world.paths.proposals.iterdir())
    if boundary != "after-commit":
        assert journal(world)["error"] == c.EXCHANGE_INTERRUPTED
        for operation, request in (("connection-sync", {}), ("connection-begin", {
                "connector": "google", "client": CLIENT, "capabilities": READ_BOTH,
                "cloudModels": False, "ownerTimeZone": None, "loginHint": None, "redirectPort": 53682})):
            with pytest.raises(AccessError, match=c.EXCHANGE_INTERRUPTED):
                c.control(world.bridge, operation, request)
        with pytest.raises(AccessError, match=c.EXCHANGE_INTERRUPTED):
            c._connected_google(world.bridge)
    monkeypatch.setattr(c, "_write_lifecycle", original_write)
    monkeypatch.setattr(custody, "start", original_start)
    assert c.control(world.bridge, "connection-activate", {})["outcome"] in ("applied", "unchanged")
    assert journal(world) is None and archived_actions(world)
    assert (world.paths.projection / "social.json").exists()
    if boundary == "after-commit":
        assert world.paths.token.exists() and "google" in record(world)["connectors"]
        assert ["systemctl", "enable", "--now", "pixel-source-broker.timer"] in world.commands
    else:
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
    assert len(world.google.calls) == calls and journal(world)["error"] == c.EXCHANGE_INTERRUPTED
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
    assert not (Path(world.bridge.state) / "transition.json").exists()
    assert journal(world)["phase"] == "activating" and journal(world)["error"] == "runtime-busy"
    world.bridge.active = 0
    assert c.control(world.bridge, "connection-activate", {}) == {"outcome": "applied"}
    assert journal(world) is None


def test_a_plugin_that_does_not_load_rolls_back_and_says_so(world):
    connect(world)
    before = {path: path.read_bytes() for path in (world.bridge.config_path, world.bridge.env_path,
                                                   world.bridge.answers_path)}
    world.bridge.plugin_ready = lambda enabled: not enabled
    with pytest.raises(AccessError, match="gateway-change-rolled-back"):
        c.control(world.bridge, "connection-activate", {})
    for path, data in before.items():
        assert path.read_bytes() == data, path.name
    assert world.bridge.log.count("restart") == 1, "only the restart back onto the previous setup"
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
