"""Authenticated host-agent adapter for the managed POSIX/systemd Pixel.

Root controls the two admission leases and one narrowly scoped systemd drop-in.
The existing config controller and installed validator run as the service owner.
No request can choose a path, command, UID, endpoint or service name.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import http.client
import ipaddress
import json
import os
import plistlib
from pathlib import Path
import platform
import re
import selectors
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import pixel_access_protocol as protocol
from pixel_gateway_service import LaunchdGatewayService, SystemdGatewayService

UNIT = "openclaw-gateway.service"
STATE = Path("/var/lib/ods-pixel-access")
DROPIN = Path("/etc/systemd/system/openclaw-gateway.service.d/90-ods-full-access.conf")
HEX = re.compile(r"^[a-f0-9]{64}$")
OWNER_TIMEOUT = 300
OWNER_EXIT_TIMEOUT = 5
OWNER_TERMINATE_TIMEOUT = 10
MODEL_DRAIN_TIMEOUT = 1800
_DEADLINE = contextvars.ContextVar("pixel_access_operation_deadline", default=None)


class AccessError(Exception):
    def __init__(self, code, *, http_status=None, returncode=None):
        self.code = code
        self.http_status = http_status
        self.returncode = returncode
        super().__init__(code)


PROBE_FAILURES = frozenset((
    "probe-directory-unavailable",
    "sandbox-resolution",
    "core-tool-construction",
    "core-exec",
    "core-cancellation",
    "filesystem-boundary",
))
RUNTIME_TRANSITION_FAILURES = frozenset((
    "managed-transition-busy",
    "managed-transition-invalid-owner",
    "managed-transition-access-owner-refused",
    "native-transition-unavailable",
    "native-transition-revision-changed",
    "native-transition-busy",
    "native-transition-busy-active-run",
    "native-transition-busy-active-tool",
    "native-transition-busy-detached-process",
    "native-transition-busy-held",
    "native-transition-busy-phase",
))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def remaining(timeout):
    deadline = _DEADLINE.get()
    value = min(timeout, deadline - time.monotonic()) if deadline is not None else timeout
    if value <= 0: raise AccessError("operation-deadline-exceeded")
    return value


def _pipe_send(stream, value, deadline):
    data = value.encode("utf-8")
    fd = stream.fileno()
    os.set_blocking(fd, False)
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_WRITE)
        while data:
            budget = min(deadline - time.monotonic(), remaining(OWNER_TIMEOUT))
            if budget <= 0: raise AccessError("owner-worker-timeout")
            if not selector.select(budget): raise AccessError("owner-worker-timeout")
            try: count = os.write(fd, data)
            except BlockingIOError: continue
            if count <= 0: raise AccessError("owner-protocol-failed")
            data = data[count:]


def runtime_config_path(bridge):
    """Use the protected deployment's config, retaining the Linux default."""
    return getattr(bridge, '_runtime_config_path', None) or bridge.home / '.openclaw/openclaw.json'


def private_json(path, uid, maximum=1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_size > maximum):
            raise AccessError("unsafe-owner-state")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return json.load(handle)
    finally:
        os.close(fd)


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".transition-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(parent)
        finally: os.close(parent)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


# This helper is embedded in the existing root-owned bridge so installation
# custody continues to cover all code executed by the access controller.
_EDGE_CONTAINER_SCRIPT = r'''
import http.client, json, math, os, signal, sys
try:
    timeout = float(sys.argv[1])
    if not math.isfinite(timeout) or not 0 < timeout <= 20:
        os._exit(125)
    signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
    signal.setitimer(signal.ITIMER_REAL, timeout)
    raw = sys.stdin.buffer.read(8193)
    if len(raw) > 8192:
        os._exit(125)
    value = json.loads(raw)
    body = json.dumps(value["payload"]).encode() if value["payload"] is not None else None
    connection = http.client.HTTPConnection("127.0.0.1", 9595, timeout=timeout)
    connection.request("POST" if body is not None else "GET", value["path"], body,
                       {"Authorization": "Bearer " + value["key"],
                        "Content-Type": "application/json", "Connection": "close"})
    response = connection.getresponse()
    if response.status != 200:
        os._exit(125)
    raw = response.read(65537)
    if not raw or len(raw) > 65536 or response.length not in (None, 0):
        os._exit(125)
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()
    os._exit(0)
except Exception:
    os._exit(125)
'''


def _edge_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("invalid JSON constant")

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("invalid edge response")
    return value


def _edge_container_request(container_id, path, key, payload, timeout=20, *, process_context=None):
    """Bound one request to the exact running edge, including Docker Desktop.

    Container IPs are not necessarily routable from the systemd host. Docker
    exec reaches its loopback without a new listener or credentials in argv.
    A failed POST has an unknown outcome: never retry it here. The caller must
    retain admission holds until its normal recovery proves the resulting state.
    """
    if (not isinstance(container_id, str) or not HEX.fullmatch(container_id)
            or path not in ("/v1/transition", "/v1/transition/acquire",
                            "/v1/transition/recover", "/v1/transition/release")):
        raise AccessError("edge-container-unavailable")
    if (not isinstance(key, str) or not 32 <= len(key) <= 4096
            or any(ord(char) < 33 or ord(char) > 126 for char in key)):
        raise AccessError("edge-owner-auth-unavailable")
    if path == "/v1/transition":
        valid = payload is None
    else:
        valid = (isinstance(payload, dict) and set(payload) == {"token", "revision"}
                 and all(isinstance(payload[name], str) and HEX.fullmatch(payload[name])
                         for name in ("token", "revision")))
    if not valid:
        raise AccessError("invalid-edge-operation")
    if not isinstance(timeout, (float, int)) or not 0 < timeout <= 20:
        raise AccessError("operation-deadline-exceeded")
    encoded = json.dumps({"path": path, "key": key, "payload": payload}).encode()
    if len(encoded) > 8192:
        raise AccessError("edge-owner-auth-unavailable")
    deadline = time.monotonic() + timeout
    process = None
    try:
        process = subprocess.Popen(
            ["docker", "exec", "-i", container_id, "python3", "-I", "-c",
             _EDGE_CONTAINER_SCRIPT, str(timeout)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, **(process_context or {}))
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            offset = 0
            while offset < len(encoded):
                budget = deadline - time.monotonic()
                if budget <= 0 or not selector.select(budget):
                    raise AccessError("runtime-operation-timeout")
                try:
                    count = os.write(process.stdin.fileno(), encoded[offset:])
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise AccessError("runtime-unavailable-or-busy")
                offset += count
            selector.unregister(process.stdin)
            process.stdin.close()
            selector.register(process.stdout, selectors.EVENT_READ)
            frame = bytearray()
            while True:
                budget = deadline - time.monotonic()
                if budget <= 0 or not selector.select(budget):
                    raise AccessError("runtime-operation-timeout")
                try:
                    chunk = os.read(process.stdout.fileno(), min(8192, 65538 - len(frame)))
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                frame.extend(chunk)
                if len(frame) > 65537:
                    raise AccessError("runtime-unavailable-or-busy")
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
        if code == 124:
            raise AccessError("runtime-operation-timeout")
        if code != 0:
            raise AccessError("runtime-unavailable-or-busy")
        return _edge_json(frame.decode("utf-8"))
    except subprocess.TimeoutExpired:
        raise AccessError("runtime-operation-timeout") from None
    except (OSError, ValueError, UnicodeError):
        raise AccessError("runtime-unavailable-or-busy") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                # The child inside Docker retains its independent alarm even
                # if killing/reaping this CLI cannot terminate the exec child.
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()

class SystemdAccessBridge:
    def __init__(self, install_dir, edge_key, *, state=STATE, dropin=DROPIN, installed_binary=None,
                 gateway_owner=None, gateway_port=None, settings_data_dir=None, gateway_binding=None):
        self.install = Path(install_dir).resolve()
        self.gateway_binding = gateway_binding
        self.edge_key = edge_key
        self.state = Path(state)
        self.dropin = Path(dropin)
        self.installed_binary, self.gateway_owner = installed_binary, gateway_owner
        if gateway_port is not None and (type(gateway_port) is not int or not 1 <= gateway_port <= 65535):
            raise AccessError("gateway-port-unavailable")
        self.gateway_port = gateway_port
        self.settings_data_dir = settings_data_dir
        self.native_port = self.native_key = self.native_origin = None
        self._native_identity = None
        self.gateway_service = SystemdGatewayService(
            lambda *args, **kwargs: self.command(*args, **kwargs), AccessError, UNIT)

    def configured_gateway_port(self, config):
        port = self.gateway_port if self.gateway_port is not None else config.get("gateway", {}).get("port", 18789)
        if type(port) is not int or not 1 <= port <= 65535:
            raise AccessError("gateway-auth-unavailable")
        return port

    @contextlib.contextmanager
    def bounded(self, seconds):
        token = _DEADLINE.set(time.monotonic() + remaining(seconds))
        try:
            yield
            remaining(seconds)  # A late normal return is not successful proof.
        finally: _DEADLINE.reset(token)

    def settings_status(self, *, data_dir_id):
        from pixel_settings.coordinator import status
        with self.bounded(45), self.locked():
            self.discover()
            if data_dir_id != self.settings_source(): raise AccessError("settings-data-directory-changed")
            return status(self)

    def settings_source(self):
        from pixel_settings.runtime import settings_data_directory
        env_file = self.install / ".env"
        raw = b""
        if os.path.lexists(env_file):
            fd = os.open(env_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid not in (0, self.owner.pw_uid) or info.st_mode & 0o022 or info.st_size > 1024 * 1024):
                    raise AccessError("unsafe-settings-environment")
                with os.fdopen(fd, "rb", closefd=False) as handle: raw = handle.read(1024 * 1024 + 1)
            finally: os.close(fd)
        if len(raw) > 1024 * 1024: raise AccessError("unsafe-settings-environment")
        directory = settings_data_directory(self.install, raw.decode("utf-8"))
        if directory is None or directory != str(self.settings_data_dir):
            raise AccessError("settings-data-directory-changed")
        return hashlib.sha256(directory.encode("utf-8")).hexdigest()

    def change_settings(self, request, *, data_dir_id):
        from pixel_settings.coordinator import change
        with self.bounded(250):
            self.discover()
            if data_dir_id != self.settings_source(): raise AccessError("settings-data-directory-changed")
            return change(self, request)

    def provider_status(self, *, data_dir_id):
        from pixel_provider.coordinator import status
        with self.bounded(120), self.locked():
            self.discover()
            if data_dir_id != self.settings_source(): raise AccessError("settings-data-directory-changed")
            return status(self)

    def change_providers(self, request, *, data_dir_id):
        from pixel_provider.coordinator import change
        with self.bounded(300):
            self.discover()
            if data_dir_id != self.settings_source(): raise AccessError("settings-data-directory-changed")
            return change(self, request)

    def model_control(self, operation, request=None):
        from pixel_model_coordinator import control
        return control(self, operation, request)

    def command(self, args, timeout=20):
        timeout = remaining(timeout)
        try:
            result = subprocess.run(args, check=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, timeout=timeout)
            return result.stdout.strip()
        except subprocess.CalledProcessError as error:
            raise AccessError("host-command-failed", returncode=error.returncode) from None
        except (OSError, subprocess.SubprocessError):
            raise AccessError("host-command-failed") from None

    def gateway_installation_binding(self, *, require_running=False):
        """Explicit adoption of an existing root-owned unit, never a ready marker.

        The selected owner/executable and every base unit/drop-in byte are
        pinned. Only our exact reversible mode drop-in may change afterwards.
        """
        raw = self.command(['systemctl', 'show', UNIT,
                            '--property=LoadState,FragmentPath,DropInPaths,User,ExecStart,MainPID'])
        fields = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
        if (fields.get('LoadState') != 'loaded' or fields.get('User') != self.gateway_owner
                or require_running and not fields.get('MainPID', '0').isdigit()
                or require_running and int(fields.get('MainPID', '0')) <= 0):
            raise AccessError('gateway-binding-unavailable')
        executable = re.match(r'^\{ path=([^;]+?) ;', fields.get('ExecStart', ''))
        if executable is None or executable.group(1) != self.installed_binary:
            raise AccessError('gateway-executable-changed')
        def protected_bytes(filename):
            path = Path(filename)
            if not path.is_absolute(): raise AccessError('gateway-unit-custody-required')
            for entry in path.parents:
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                    raise AccessError('gateway-unit-custody-required')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 1024 * 1024:
                    raise AccessError('gateway-unit-custody-required')
                return handle.read(1024 * 1024 + 1)
        unit = fields.get('FragmentPath', '')
        content = protected_bytes(unit)
        if str(self.install).encode() not in content:
            raise AccessError('gateway-installation-mismatch')
        dropins = []
        for filename in shlex.split(fields.get('DropInPaths', '')):
            body = protected_bytes(filename)
            if Path(filename) == DROPIN:
                if body != b'[Service]\nProtectSystem=false\nProtectHome=false\n':
                    raise AccessError('gateway-unit-custody-required')
                continue
            dropins.append({'path':filename, 'sha256':hashlib.sha256(body).hexdigest()})
        return {'schemaVersion':1, 'unit':unit, 'sha256':hashlib.sha256(content).hexdigest(),
                'dropins':dropins, 'owner':self.gateway_owner, 'executable':self.installed_binary}

    def verify_gateway_installation_binding(self):
        if self.gateway_installation_binding() != self.gateway_binding:
            raise AccessError('gateway-installation-changed')

    def verify_host_agent_custody(self):
        # Hybrid installations can run the host agent outside this guest.
        # Absence must be proven, not inferred from an empty User property.
        values = self.command(['systemctl', 'show', 'ods-host-agent.service', '--property=LoadState,ActiveState,User,MainPID'])
        fields = dict(line.split('=', 1) for line in values.splitlines() if '=' in line)
        if fields.get('LoadState') == 'not-found' and fields.get('MainPID') == '0':
            return
        if fields.get('LoadState') != 'loaded':
            raise AccessError('host-agent-state-unavailable')
        if fields.get('User') not in ('', 'root', '0', None):
            return
        if fields.get('ActiveState') != 'active':
            raise AccessError('host-agent-unavailable')
        try:
            pid = int(fields.get('MainPID', '0'))
            if pid <= 0:
                raise ValueError()
            args = Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\0')
        except (OSError, ValueError):
            raise AccessError('host-agent-unavailable') from None
        if not any(args):
            raise AccessError('host-agent-unavailable')
        if b'-I' not in args:
            raise AccessError('root-host-agent-isolation-required')
        scripts = [Path(os.fsdecode(arg)) for arg in args if arg.endswith(b'ods-host-agent.py')]
        if len(scripts) != 1 or not scripts[0].is_absolute():
            raise AccessError('root-host-agent-custody-required')
        for path in (scripts[0], *scripts[0].parents):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise AccessError('root-host-agent-custody-required')
        for directory, folders, files in os.walk(scripts[0].parent, followlinks=False):
            for name in folders + files:
                info = (Path(directory) / name).lstat()
                if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                    raise AccessError('root-host-agent-custody-required')

    def discover(self, *, allow_installing=False):
        if platform.system() != "Linux":
            raise AccessError("macos-launchd-adapter-missing" if platform.system() == "Darwin" else "native-windows-adapter-missing")
        if os.geteuid() != 0: raise AccessError("root-host-adapter-required")
        if not Path("/run/systemd/system").is_dir(): raise AccessError("systemd-unavailable")
        program = Path(__file__).resolve()
        for path in (program, *program.parents):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise AccessError("root-program-custody-required")
        self.verify_host_agent_custody()
        import pwd
        user = self.command(["systemctl", "show", UNIT, "--property=User", "--value"])
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user): raise AccessError("gateway-owner-unavailable")
        if user != self.gateway_owner: raise AccessError("installed-owner-changed")
        owner = pwd.getpwnam(user)
        home = Path(owner.pw_dir)
        if owner.pw_uid == 0 or not home.is_absolute() or home.resolve() != home:
            raise AccessError("unsafe-gateway-owner")
        allowed_states = ("ready", "installing") if allow_installing else ("ready",)
        marker_path = home / '.config/ods/pixel-managed.json'
        if self.gateway_binding is not None:
            self.verify_gateway_installation_binding()
        else:
            marker = private_json(marker_path, owner.pw_uid, 65536)
            if (marker.get("schema_version") != 2 or marker.get("manager") != "ods"
                    or marker.get("state") not in allowed_states
                    or Path(marker.get("install_dir", "")).resolve() != self.install):
                raise AccessError("managed-owner-mismatch")
        config = private_json(home / ".openclaw/openclaw.json", owner.pw_uid)
        binary = self.installed_binary
        if not isinstance(binary, str) or not Path(binary).is_absolute() or not os.access(binary, os.X_OK):
            raise AccessError("installed-validator-unavailable")
        # The root-owned deployment record is authoritative. The owner config
        # may omit gateway.port even when systemd intentionally runs a custom
        # port, and using the default in that case disconnects the access plane
        # from the live gateway it is supposed to prove.
        port = self.configured_gateway_port(config)
        token = config.get("gateway", {}).get("auth", {}).get("token")
        if not isinstance(token, str) or not 16 <= len(token) <= 4096:
            raise AccessError("gateway-auth-unavailable")
        self.owner, self.home, self.binary = owner, home, binary
        if (self.native_port, self.native_key) != (port, token):
            self.native_origin = self._native_identity = None
        self.native_port, self.native_key = port, token
        self.surface = "wsl-systemd" if "microsoft" in platform.release().lower() else "linux-systemd"

    def http(self, origin, path, key, payload=None, timeout=20):
        if any(ord(char) < 32 for char in key): raise AccessError("invalid-service-auth")
        body = json.dumps(payload).encode() if payload is not None else None
        # Only the discovered loopback gateway or private edge IP. No proxies,
        # redirects, or public request-selected origins are accepted here.
        parts = urlsplit(origin)
        try: address = ipaddress.ip_address(parts.hostname)
        except ValueError: raise AccessError("invalid-service-origin") from None
        if (parts.scheme != "http" or not (address.is_loopback or address.is_private)
                or parts.username or parts.password or parts.query or parts.fragment or parts.path
                or path not in ("/health", "/pixel-ods/access-runtime", "/v1/transition", "/v1/transition/acquire",
                                "/v1/transition/recover", "/v1/transition/release")):
            raise AccessError("invalid-service-origin")
        budget = remaining(timeout)
        deadline = time.monotonic() + budget
        for attempt in range(3):
            budget = deadline - time.monotonic()
            if budget <= 0: raise AccessError("runtime-operation-timeout")
            connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=budget)
            timer = None
            expired = threading.Event()
            try:
                connection.connect()
                transport = connection.sock
                budget = deadline - time.monotonic()
                if budget <= 0: raise AccessError("runtime-operation-timeout")
                def interrupt(transport=transport, expired=expired):
                    expired.set()
                    try: transport.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        # Completion can close this exact per-call socket first.
                        return
                timer = threading.Timer(budget, interrupt)
                timer.daemon = True
                timer.start()
                connection.request("POST" if body is not None else "GET", path, body=body,
                                   headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                with connection.getresponse() as response:
                    raw = response.read(65537)
                    if response.status != 200:
                        if len(raw) <= 65536:
                            try:
                                failure = protocol.decode_frame(raw.decode("utf-8") + "\n", 65537).get("error")
                            except (AttributeError, ValueError, UnicodeError):
                                failure = None
                            if isinstance(failure, str) and failure in RUNTIME_TRANSITION_FAILURES:
                                raise AccessError(failure, http_status=response.status)
                        raise AccessError("runtime-unavailable-or-busy", http_status=response.status)
                if expired.is_set() or time.monotonic() >= deadline:
                    raise AccessError("runtime-operation-timeout")
                if len(raw) > 65536: raise ValueError()
                value = protocol.decode_frame(raw.decode("utf-8") + "\n", 65537)
                if not isinstance(value, dict): raise ValueError()
                return value
            except ConnectionResetError:
                if expired.is_set() or time.monotonic() >= deadline:
                    raise AccessError("runtime-operation-timeout") from None
                # Only a fresh read can be repeated after a reset (including
                # RemoteDisconnected). Never replay a possibly accepted POST.
                if body is not None or attempt == 2:
                    raise AccessError("runtime-unavailable-or-busy") from None
            except (OSError, http.client.HTTPException, ValueError, UnicodeError):
                if expired.is_set() or time.monotonic() >= deadline:
                    raise AccessError("runtime-operation-timeout") from None
                raise AccessError("runtime-unavailable-or-busy") from None
            finally:
                if timer is not None: timer.cancel()
                connection.close()
                if timer is not None: timer.join(timeout=1)

    def native_snapshot(self, *, timeout, pinned_origin=None):
        """Qualify the actual local gateway before choosing a mutation target.

        IPv6 loopback avoids optional IPv4 forwarding/proxy layers. An IPv4-only
        gateway remains supported, but only read-only discovery may fall back.
        Both candidates, process checks and connection retries share one budget.
        """
        deadline = time.monotonic() + remaining(timeout)
        def budget(): return remaining(deadline - time.monotonic())
        def process_identity():
            return self.gateway_service.process_identity(timeout=min(3, budget()))
        process = process_identity()
        pid = process[0]
        identity = (self.native_port, self.native_key, process)
        candidates = ["http://[::1]:%d" % self.native_port, "http://127.0.0.1:%d" % self.native_port]
        if pinned_origin is not None:
            if pinned_origin not in candidates:
                raise AccessError("invalid-service-origin")
            candidates = [pinned_origin]
        elif self._native_identity == identity and self.native_origin in candidates:
            candidates.remove(self.native_origin)
            candidates.insert(0, self.native_origin)
        else:
            self.native_origin = self._native_identity = None
        for index, origin in enumerate(candidates):
            # Reserve time for IPv4-only hosts even if an IPv6 listener stalls.
            attempt_budget = min(3, budget() / (len(candidates) - index))
            try:
                snapshot = self.http(origin, "/pixel-ods/access-runtime", self.native_key,
                                     None, timeout=attempt_budget)
            except AccessError as error:
                if (error.http_status is not None or index + 1 == len(candidates)
                        or error.code not in ("runtime-unavailable-or-busy", "runtime-operation-timeout")):
                    raise
                continue
            if (snapshot.get("available") is not True
                    or snapshot.get("phase") not in ("idle", "busy", "held", "interrupted")
                    or type(snapshot.get("active")) is not int or snapshot["active"] < 0
                    or not isinstance(snapshot.get("revision"), str) or not HEX.fullmatch(snapshot["revision"])):
                raise AccessError("admission-gate-unavailable")
            if type(snapshot.get("pid")) is not int or snapshot["pid"] != pid or process_identity() != process:
                raise AccessError("gateway-process-mismatch")
            self.native_origin, self._native_identity = origin, identity
            return snapshot

    def native(self, operation=None, token=None, *, timeout=60):
        deadline = time.monotonic() + remaining(timeout)
        def budget(): return remaining(deadline - time.monotonic())
        payload = None
        owned_hold = False
        try:
            if operation is None:
                return self.native_snapshot(timeout=budget())
            snapshot = self.native(timeout=budget())
            if snapshot.get("stopped"):
                if operation != "acquire": raise AccessError("gateway-restart-required")
                return self.stopped_native(token)
            # Keep this exact target even if a later read invalidates the cache.
            origin = self.native_origin
            payload = dict(operation=operation, token=token, revision=snapshot["revision"])
            if operation == "acquire": owned_hold = self.owns_native_hold(snapshot, token)
            return self.http(origin, "/pixel-ods/access-runtime", self.native_key, payload, timeout=budget())
        except AccessError as error:
            # A hot reload can briefly refuse the management channel while the
            # previous policy drains. Only an already-owned, unchanged hold is
            # idempotent: never retry a new acquisition, a timeout, or a probe.
            if operation == "acquire" and owned_hold and error.http_status == 409:
                current = self.native_snapshot(timeout=min(3, budget()), pinned_origin=origin)
                if (current.get("pid") == snapshot.get("pid")
                        and current.get("revision") == snapshot.get("revision")
                        and self.owns_native_hold(current, token)):
                    return self.http(origin, "/pixel-ods/access-runtime", self.native_key,
                                     payload, timeout=min(3, budget()))
            pending = self.pending()
            if operation is None and pending:
                return self.stopped_native(pending["token"])
            raise

    def owns_native_hold(self, snapshot, token):
        """Read-only custody proof for one retry of an existing native lease."""
        if (snapshot.get("available") is not True or snapshot.get("phase") != "held"
                or type(snapshot.get("active")) is not int or snapshot["active"] != 0
                or type(snapshot.get("pid")) is not int or snapshot["pid"] <= 0
                or not isinstance(token, str) or not HEX.fullmatch(token)
                or not isinstance(snapshot.get("revision"), str) or not HEX.fullmatch(snapshot["revision"])):
            return False
        try:
            state = private_json(self.home / ".openclaw/.ods-access-runtime/state.json", self.owner.pw_uid, 4096)
            return (state.get("phase") == "held" and state.get("revision") == snapshot["revision"]
                    and state.get("tokenHash") == hashlib.sha256(token.encode()).hexdigest())
        except (AccessError, OSError, ValueError):
            return False

    def stopped_native(self, token):
        """Crash recovery only: an owned durable hold and an empty stopped unit.

        An unreachable HTTP endpoint alone never proves idle. Never stop/kill an
        active unit to satisfy this check.
        """
        self.gateway_service.assert_stopped()
        state = private_json(self.home / ".openclaw/.ods-access-runtime/state.json", self.owner.pw_uid, 4096)
        if (state.get("phase") != "held" or state.get("tokenHash") != hashlib.sha256(token.encode()).hexdigest()
                or not HEX.fullmatch(state.get("revision", ""))):
            raise AccessError("native-lease-unconfirmed")
        return {"available": True, "phase": "held", "revision": state["revision"], "active": 0,
                "pid": 0, "proof": None, "stopped": True}

    def edge(self, operation=None, token=None, revision=None):
        if operation not in (None, "acquire", "release", "recover"):
            raise AccessError("invalid-edge-operation")
        budget = remaining(20)
        started = time.monotonic()
        # Pin the inspected running instance, not a replaceable container name.
        identity = self._inspect_edge(timeout=budget).split()
        if len(identity) != 2 or not HEX.fullmatch(identity[0]) or identity[1] != "true":
            raise AccessError("edge-container-unavailable")
        payload = dict(token=token, revision=revision) if operation else None
        return self._request_edge(identity[0],
            "/v1/transition" + ("/" + operation if operation else ""),
            self.edge_key, payload, timeout=budget - (time.monotonic() - started))

    def _inspect_edge(self, *, timeout):
        return self.command(['docker', 'inspect', 'ods-pixel-edge', '--format',
                             '{{.Id}} {{.State.Running}}'], timeout=timeout)

    def _request_edge(self, *args, **kwargs):
        return _edge_container_request(*args, **kwargs)

    def _native_owner_identity(self):
        import pwd
        owner = pwd.getpwnam(self.owner.pw_name)
        if (os.geteuid() != 0 or owner.pw_uid <= 0 or owner.pw_gid < 0
                or owner.pw_uid != self.owner.pw_uid or owner.pw_gid != self.owner.pw_gid):
            raise AccessError('unsafe-owner-identity')
        return {'user': owner.pw_uid, 'group': owner.pw_gid,
                'extra_groups': os.getgrouplist(owner.pw_name, owner.pw_gid)}

    def _launch_owner_worker(self, env):
        script = Path(__file__).resolve().parent / "access_mode_worker.py"
        command = [sys.executable, "-I", "-u", str(script)]
        identity = {}
        if platform.system() == "Darwin":
            # Popen drops groups/GID/UID in the child before exec, without a
            # shell, user-controlled launcher, or thread-unsafe preexec_fn.
            identity = self._native_owner_identity()
        else:
            command = [self._linux_owner_launcher(), "-u", self.owner.pw_name, "--", *command]
        return subprocess.Popen(command, cwd="/", env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, bufsize=1, **identity)

    def _linux_owner_launcher(self):
        # This launcher still runs as root. Never search the owner's validator
        # PATH for it; that PATH is intended only for the unprivileged worker.
        launcher = None
        for candidate in (Path("/usr/sbin/runuser"), Path("/sbin/runuser")):
            try:
                resolved = candidate.resolve(strict=True)
                for entry in (resolved, *resolved.parents):
                    info = entry.lstat()
                    if info.st_uid != 0 or info.st_mode & 0o022:
                        raise AccessError("unsafe-owner-launcher")
                if not resolved.is_file() or not os.access(resolved, os.X_OK):
                    continue
                launcher = str(resolved)
                break
            except FileNotFoundError:
                continue
        if launcher is None:
            raise AccessError("owner-launcher-unavailable")
        return launcher

    def worker_environment(self):
        return {"HOME": str(self.home), "USER": self.owner.pw_name, "LOGNAME": self.owner.pw_name,
                "PATH": str(Path(self.binary).parent) + ":/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}

    def worker(self, operation="status", *, confirmed=False, config_hash=None, busy=None, restart=None,
               transaction_id=None, settings_revision=None, preferences=None, capabilities=None, activate_settings=None,
               binding=None, activate_provider=None, expected_projection=None, provider_probe=None,
               model_target=None, model_outcome=None, relocation=None):
        env = self.worker_environment()
        request = dict(operation=operation, openclaw=self.binary, config_sha256=config_hash, confirmed=confirmed)
        if operation == 'access-relocate':
            request['relocation'] = relocation
        if operation == 'provider-worker-status':
            request['provider_probe'] = provider_probe
        if operation in ("settings-apply", "settings-recover", "provider-change", "provider-recover"):
            request["transaction_id"] = transaction_id
        if operation.startswith("model-") and operation != "model-status":
            request["transaction_id"] = transaction_id
        if operation == "model-apply": request["model_target"] = model_target
        if operation == "model-finish": request["model_outcome"] = model_outcome
        if operation == "settings-apply":
            request.update(settings_revision=settings_revision, preferences=preferences, capabilities=capabilities)
        if operation == "provider-change":
            request["binding"] = binding
            if expected_projection is not None:
                request['expected_projection'] = expected_projection
        try:
            protocol.request(request)
            encoded = json.dumps(request, allow_nan=False) + "\n"
            if len(encoded.encode("utf-8")) > protocol.MAX_REQUEST:
                raise ValueError()
        except (ValueError, TypeError, RecursionError):
            raise AccessError("owner-protocol-failed") from None
        deadline = time.monotonic() + remaining(OWNER_TIMEOUT)
        process = self._launch_owner_worker(env)
        try:
            _pipe_send(process.stdin, encoded, deadline)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                buffered = b""
                while time.monotonic() < deadline:
                    # TextIO.readline can block forever after a partial write,
                    # even after select reports the first byte. Bound each read
                    # and retain framing without losing the outer deadline.
                    if b"\n" not in buffered:
                        if not selector.select(min(1, max(0, deadline - time.monotonic()))): continue
                        chunk = os.read(process.stdout.fileno(), protocol.MAX_REPLY + 1)
                        if not chunk: raise AccessError("owner-protocol-failed")
                        buffered += chunk
                    raw, separator, rest = buffered.partition(b"\n")
                    if len(raw) + 1 > protocol.MAX_REPLY: raise AccessError("owner-protocol-failed")
                    if not separator: continue
                    buffered = rest
                    try:
                        value = protocol.decode_frame(raw.decode("utf-8") + "\n", protocol.MAX_REPLY)
                        if type(value) is not dict: raise ValueError()
                    except ValueError:
                        raise AccessError("owner-protocol-failed") from None
                    if set(value) == {"result"}:
                        try: result = protocol.result(operation, value["result"])
                        except protocol.ProtocolError: raise AccessError("owner-protocol-failed") from None
                        try: process.wait(timeout=remaining(min(OWNER_EXIT_TIMEOUT, deadline - time.monotonic())))
                        except subprocess.TimeoutExpired: raise AccessError("owner-worker-exit-unconfirmed") from None
                        if process.returncode != 0: raise AccessError("owner-worker-failed")
                        remaining(deadline - time.monotonic())
                        return result
                    if set(value) == {"error"}:
                        if type(value["error"]) is not str or not re.fullmatch(r"[a-z][a-z0-9-]{0,95}", value["error"]):
                            raise AccessError("owner-protocol-failed")
                        raise AccessError("controller-" + value["error"])
                    if set(value) != {"hook"}: raise AccessError("owner-protocol-failed")
                    if type(value["hook"]) is not str or value["hook"] not in protocol.HOOKS.get(operation, ()):
                        raise AccessError("owner-protocol-failed")
                    callback = {"busy": busy, "restart": restart, "settings-activate": activate_settings,
                                "provider-activate": activate_provider}.get(value["hook"])
                    if callback is None: raise AccessError("owner-protocol-failed")
                    remaining(deadline - time.monotonic())
                    answer = callback()
                    remaining(deadline - time.monotonic())
                    try: protocol.hook_reply(operation, value["hook"], answer)
                    except protocol.ProtocolError: raise AccessError("host-hook-failed") from None
                    _pipe_send(process.stdin, json.dumps(answer) + "\n", deadline)
            raise AccessError("owner-worker-timeout")
        finally:
            try:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=OWNER_TERMINATE_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        # Only the child launched above, never an installed
                        # gateway/model/other operator process.
                        process.kill()
                        process.wait(timeout=OWNER_EXIT_TIMEOUT)
            finally:
                process.stdin.close()
                process.stdout.close()

    def pending(self):
        file = self.state / "transition.json"
        return private_json(file, 0, 8192) if file.exists() else None

    @contextlib.contextmanager
    def locked(self):
        import fcntl
        self.state.mkdir(mode=0o700, parents=False, exist_ok=True)
        info = self.state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077: raise AccessError("unsafe-host-state")
        fd = os.open(self.state / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077:
                raise AccessError("unsafe-host-lock")
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise AccessError("transition-busy") from None
            yield
        finally: os.close(fd)

    def inspect(self, *, allow_installing=False):
        # Model reconciliation deliberately marks the already verified ODS
        # deployment as installing before it takes both admission gates.  Keep
        # ordinary status/settings/provider inspection ready-only, but let the
        # model-transition entry point admit that exact managed state so a
        # retry can resume instead of deadlocking on its own marker.
        self.discover(allow_installing=allow_installing)
        config, native, edge = self.worker(), self.native(), self.edge()
        if not native.get("available") or edge.get("capability") != "available": raise AccessError("admission-gate-unavailable")
        pid = self.gateway_service.pid()
        if native.get("pid") != pid or (pid <= 0 and not native.get("stopped")): raise AccessError("gateway-process-mismatch")
        pending = self.pending()
        revision = digest([config.get("config_sha256"), native.get("revision"), edge.get("revision"), pid,
                           pending.get("phase") if pending else None, pending.get("kind", "access") if pending else None])
        proof = native.get("proof")
        verified = private_json(self.state / "verified.json", 0, 8192) if (self.state / "verified.json").exists() else {}
        effective = proof.get("mode") if (isinstance(proof, dict) and proof.get("executed") is True and proof.get("pid") == pid
                    and verified.get("pid") == pid and verified.get("config_sha256") == config.get("config_sha256")
                    and verified.get("proof") == proof) else "unknown"
        if effective != config.get("configured_status") or pending or verified.get("boundary") != self.unit_boundary(): effective = "unknown"
        return {"available": True, "surface": self.surface, "configured_mode": config.get("configured_status", "unknown"),
                "effective_mode": effective, "runtime_verified": effective != "unknown", "revision": revision,
                "busy": bool(native.get("active") or edge.get("streams")), "pending": pending is not None,
                "reason": "transition-recovery-required" if pending else ("runtime-proof-required" if effective == "unknown" else None),
                "scope": "owner-host", "_config": config, "_native": native, "_edge": edge}

    def status(self):
        try: return {key: value for key, value in self.inspect().items() if not key.startswith("_")}
        except Exception as error:
            return {"available": False, "surface": platform.system().lower(), "configured_mode": "unknown",
                    "effective_mode": "unknown", "runtime_verified": False, "revision": None, "busy": False,
                    # A transient controller lock or gateway restart must not
                    # erase the durable transition from the UI's polling state.
                    "pending": os.path.lexists(self.state / "transition.json"),
                    "reason": error.code if isinstance(error, AccessError) else "inspection-failed", "scope": "owner-host"}

    def dropin_for(self, enabled):
        # These two namespace restrictions create the filesystem sandbox.
        # Keep UID/DAC, NNP, capability limits, private /tmp, and explicit readonly
        # binary/plugin binds. Never reset a list or overwrite another drop-in.
        content = "[Service]\nProtectSystem=false\nProtectHome=false\n"
        self.dropin.parent.mkdir(mode=0o755, exist_ok=True)
        for path in (self.dropin.parent, *self.dropin.parent.parents):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise AccessError("unsafe-service-dropin-directory")
        if self.dropin.exists():
            info = self.dropin.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022 or self.dropin.read_text() != content:
                raise AccessError("unrecognized-service-dropin")
        if enabled and not self.dropin.exists():
            fd = os.open(self.dropin, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        elif not enabled and self.dropin.exists(): self.dropin.unlink()
        self.gateway_service.reload()

    def unit_boundary(self):
        return self.gateway_service.boundary()

    def service_restore_required(self):
        return self.dropin.exists()

    def provision_probe(self):
        base = (Path("/private/var/lib/ods-pixel-access-probes")
                if platform.system() == "Darwin" else Path("/var/lib/ods-pixel-access-probes"))
        base.mkdir(mode=0o711, exist_ok=True)
        info = base.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise AccessError("unsafe-probe-directory")
        # The coordinator's 0077 umask otherwise makes this root-only and
        # prevents the gateway owner reaching its private child directory.
        os.chmod(base, 0o711)
        target = base / str(self.owner.pw_uid)
        try:
            target.mkdir(mode=0o700)
            os.chown(target, self.owner.pw_uid, self.owner.pw_gid)
        except FileExistsError: pass
        info = target.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != self.owner.pw_uid or info.st_mode & 0o077:
            raise AccessError("unsafe-probe-directory")

    def verify_held_mode(self, token, mode):
        if mode not in ("full-access", "sandboxed") or type(token) is not str or not HEX.fullmatch(token):
            raise AccessError("invalid-model-transition")
        self.provision_probe()
        baseline_file = self.state / "service-baseline.json"
        if not baseline_file.exists():
            raise AccessError("service-baseline-missing")
        boundary = self.unit_boundary()
        baseline = private_json(baseline_file, 0, 8192)["boundary"]
        if mode == "sandboxed":
            if boundary != baseline: raise AccessError("service-restore-mismatch")
        else:
            def other_settings(value):
                return [line for line in value.splitlines()
                        if not line.startswith(("ProtectSystem=", "ProtectHome="))]
            if ("ProtectSystem=no" not in boundary or "ProtectHome=no" not in boundary
                    or other_settings(boundary) != other_settings(baseline)):
                raise AccessError("service-boundary-mismatch")
        try:
            proof = self.native("probe", token)
        except AccessError as error:
            # The runtime deliberately exposes only a fixed proof-stage label.
            # Preserve that bounded diagnostic across the root coordinator so
            # live qualification can distinguish a failed proof from a busy
            # runtime without forwarding exception text or private paths.
            try:
                snapshot = self.native(timeout=10)
                failure = snapshot.get("probe_failure") if snapshot.get("phase") == "held" else None
            except AccessError:
                failure = None
            if failure in PROBE_FAILURES:
                raise AccessError("runtime-proof-" + failure) from None
            raise error
        if proof.get("proof", {}).get("mode") != mode: raise AccessError("runtime-proof-failed")
        verified_config = self.worker()
        if verified_config.get("configured_status") != mode:
            raise AccessError("configured-mode-changed")
        atomic_json(self.state / "verified.json", {"pid": proof["pid"], "proof": proof["proof"],
                    "config_sha256": verified_config["config_sha256"], "boundary": boundary})

    def model_journal(self, transaction_id=None):
        pending = self.pending()
        required = {"kind", "transaction_id", "token", "phase", "edge_revision",
                    "configured_mode", "start_config_sha256"}
        if (type(pending) is not dict or not required.issubset(pending)
                or not set(pending).issubset(required | {"error"})
                or pending.get("kind") != "model"
                or type(pending.get("transaction_id")) is not str
                or not HEX.fullmatch(pending["transaction_id"])
                or type(pending.get("token")) is not str or not HEX.fullmatch(pending["token"])
                or type(pending.get("edge_revision")) is not str
                or not HEX.fullmatch(pending["edge_revision"])
                or pending.get("configured_mode") not in ("full-access", "sandboxed")
                or type(pending.get("start_config_sha256")) is not str
                or not HEX.fullmatch(pending["start_config_sha256"])
                or pending.get("phase") not in ("acquiring", "draining", "held", "finishing",
                                                 "releasing", "native-released", "error")
                or ("error" in pending and (type(pending["error"]) is not str
                    or not re.fullmatch(r"[a-z][a-z0-9-]{0,95}", pending["error"])))):
            raise AccessError("model-recovery-required")
        if transaction_id is not None and transaction_id != pending["transaction_id"]:
            raise AccessError("model-transaction-mismatch")
        return pending

    def model_status(self):
        """Disclose only the validated pending model handle to the host owner."""
        if self.pending() is None:
            return {"pending": False}
        journal = self.model_journal()
        result = {"pending": True, "kind": "model",
                  "transaction_id": journal["transaction_id"],
                  "phase": journal["phase"],
                  "configured_mode": journal["configured_mode"],
                  "start_config_sha256": journal["start_config_sha256"]}
        if "error" in journal:
            result["error"] = journal["error"]
        return result

    def model_error(self, pending, error):
        pending["phase"] = "error"
        pending["error"] = error.code if isinstance(error, AccessError) else "model-transition-failed"
        atomic_json(self.state / "transition.json", pending)

    def remove_model_journal(self):
        try: (self.state / "transition.json").unlink()
        except FileNotFoundError: pass
        directory = os.open(self.state, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)

    def model_completion(self, request=None):
        path = self.state / "model-promotion-completed.json"
        legacy = False
        if not path.exists():
            path = self.state / "model-completed.json"
            legacy = True
        if not path.exists(): return None
        value = private_json(path, 0, 4096)
        # Older releases shared this filename with browser model switching.
        # A valid receipt from that separate transaction is not a promotion
        # completion; malformed state still fails closed.
        if legacy and type(value) is dict and set(value) == {"transactionId", "outcome", "configSha256"}:
            if (type(value["transactionId"]) is not str or not HEX.fullmatch(value["transactionId"])
                    or value["outcome"] not in ("commit", "rollback")
                    or type(value["configSha256"]) is not str or not HEX.fullmatch(value["configSha256"])):
                raise AccessError("model-recovery-required")
            return None
        if (type(value) is not dict
                or set(value) != {"kind", "transaction_id", "outcome", "config_sha256"}
                or value.get("kind") != "model-completion"
                or type(value.get("transaction_id")) is not str
                or not HEX.fullmatch(value["transaction_id"])
                or value.get("outcome") not in ("applied", "rolled-back")
                or type(value.get("config_sha256")) is not str
                or not HEX.fullmatch(value["config_sha256"])):
            raise AccessError("model-recovery-required")
        if request is not None and (value["transaction_id"] != request["transaction_id"]
                                    or value["outcome"] != request["outcome"]):
            return None
        return value

    def model_begin(self):
        with self.bounded(MODEL_DRAIN_TIMEOUT + 30), self.locked():
            if self.pending() is not None:
                raise AccessError("transition-recovery-required")
            snapshot = self.inspect(allow_installing=True)
            configured_mode = snapshot["configured_mode"]
            trusted_snapshot = (
                snapshot["available"] is True
                and snapshot["scope"] == "owner-host"
                and configured_mode in ("full-access", "sandboxed")
                and snapshot["pending"] is False
                and isinstance(snapshot["revision"], str)
                and HEX.fullmatch(snapshot["revision"]) is not None
            )
            runtime_ready = (
                trusted_snapshot
                and snapshot["runtime_verified"] is True
                and snapshot["effective_mode"] == configured_mode
                and snapshot["reason"] is None
            )
            # A gateway process restart deliberately invalidates verified.json.
            # Admit only that exact, idle fail-closed projection; ambiguous,
            # busy, pending, unavailable, or differently configured states
            # must still require explicit recovery. The runtime is re-proved
            # below only after both admission gates are durably held, avoiding
            # an open-admission window between recovery and model mutation.
            stale_runtime_proof = (
                trusted_snapshot
                and snapshot["effective_mode"] == "unknown"
                and snapshot["runtime_verified"] is False
                and snapshot["busy"] is False
                and snapshot["reason"] == "runtime-proof-required"
            )
            if (configured_mode not in ("full-access", "sandboxed")
                    or not (runtime_ready or stale_runtime_proof)):
                raise AccessError("runtime-proof-required")
            if (snapshot["_native"].get("phase") != "idle"
                    or snapshot["_edge"].get("phase") != "idle"):
                raise AccessError("transition-recovery-required")
            pending = {"kind": "model", "transaction_id": os.urandom(32).hex(),
                       "token": os.urandom(32).hex(), "phase": "acquiring",
                       "edge_revision": snapshot["_edge"]["revision"],
                       "configured_mode": snapshot["configured_mode"],
                       "start_config_sha256": snapshot["_config"]["config_sha256"]}
            # Durable intent precedes both admission-gate acquisition calls.
            atomic_json(self.state / "transition.json", pending)
            try:
                edge = self.edge("acquire", pending["token"], pending["edge_revision"])
                pending["edge_revision"] = edge["revision"]
                pending["phase"] = "draining"
                atomic_json(self.state / "transition.json", pending)
                self.native("acquire", pending["token"])
                deadline = time.monotonic() + MODEL_DRAIN_TIMEOUT
                while True:
                    native = self.native("acquire", pending["token"], timeout=5)
                    edge = self.edge("acquire", pending["token"], pending["edge_revision"])
                    if (native.get("phase") == "held" and edge.get("phase") == "held"
                            and not native.get("active") and not edge.get("streams")):
                        break
                    if time.monotonic() >= deadline:
                        raise AccessError("runtime-busy")
                    time.sleep(1)
                # Re-prove the configured mode while both native and external
                # admission remain closed. A failed proof leaves the durable
                # model journal and both gates held for explicit recovery.
                self.verify_held_mode(pending["token"], configured_mode)
                pending["phase"] = "held"
                atomic_json(self.state / "transition.json", pending)
                return {"status": "held", "transaction_id": pending["transaction_id"]}
            except Exception as error:
                self.model_error(pending, error)
                if isinstance(error, AccessError): raise
                raise AccessError("model-transition-failed") from None

    def model_finish(self, request):
        if (type(request) is not dict or set(request) != {"transaction_id", "outcome"}
                or type(request["transaction_id"]) is not str
                or not HEX.fullmatch(request["transaction_id"])
                or request["outcome"] not in ("applied", "rolled-back")):
            raise AccessError("invalid-request")
        with self.bounded(300), self.locked():
            completion = self.model_completion(request)
            if completion is not None:
                # The socket reply may have been lost after both gates released.
                # A root-owned exact completion makes finish safely replayable.
                pending = self.pending()
                if isinstance(pending, dict) and pending.get("transaction_id") == request["transaction_id"]:
                    try:
                        self.model_journal(request["transaction_id"])
                        self.remove_model_journal()
                    except (AccessError, OSError):
                        pass
                return {"status": "released", "outcome": request["outcome"]}
            pending = self.model_journal(request["transaction_id"])
            try:
                self.discover(allow_installing=True)
                token = pending["token"]
                edge_snapshot = self.edge()
                if edge_snapshot.get("phase") == "idle":
                    pending["edge_revision"] = edge_snapshot["revision"]
                    edge = self.edge("acquire", token, pending["edge_revision"])
                elif edge_snapshot.get("phase") == "interrupted":
                    pending["edge_revision"] = edge_snapshot["revision"]
                    edge = self.edge("recover", token, pending["edge_revision"])
                elif edge_snapshot.get("phase") == "held":
                    # The token, not a stale pre-acquire revision, proves that
                    # this controller owns a hold recovered after a crash.
                    pending["edge_revision"] = edge_snapshot["revision"]
                    edge = self.edge("acquire", token, pending["edge_revision"])
                else:
                    raise AccessError("model-lease-lost")
                pending["edge_revision"] = edge["revision"]
                pending["phase"] = "finishing"
                pending.pop("error", None)
                atomic_json(self.state / "transition.json", pending)
                native_snapshot = self.native()
                if (native_snapshot.get("phase") not in ("idle", "interrupted", "held")
                        or native_snapshot.get("stopped")
                        or not isinstance(native_snapshot.get("pid"), int) or native_snapshot["pid"] <= 0):
                    raise AccessError("model-lease-lost")
                # The native runtime has one acquire verb: its implementation
                # explicitly accepts both idle and interrupted states, while a
                # same-token held acquire is idempotent. Unlike edge, it has no
                # distinct recover operation.
                native = self.native("acquire", token, timeout=10)
                edge = self.edge("acquire", token, pending["edge_revision"])
                if (native.get("phase") != "held" or edge.get("phase") != "held"
                        or native.get("active") or edge.get("streams")):
                    raise AccessError("runtime-busy")
                config = self.worker()
                if config.get("configured_status") != pending["configured_mode"]:
                    raise AccessError("configured-mode-changed")
                if (request["outcome"] == "rolled-back"
                        and config.get("config_sha256") != pending["start_config_sha256"]):
                    raise AccessError("rollback-config-mismatch")
                self.verify_held_mode(token, pending["configured_mode"])
                pending["phase"] = "releasing"
                atomic_json(self.state / "transition.json", pending)
                # Keep edge admission closed until the native runtime release
                # succeeds. Any native-release failure therefore returns with
                # the externally reachable gate still held.
                try:
                    self.native("release", token)
                except AccessError:
                    # A lost HTTP reply can follow a successful release. The
                    # edge is still held, so an observed idle native runtime is
                    # an adequate completion receipt without reopening work.
                    released_native = self.native()
                    if (released_native.get("phase") != "idle"
                            or released_native.get("active")
                            or released_native.get("stopped")):
                        raise
                pending["phase"] = "native-released"
                atomic_json(self.state / "transition.json", pending)
                try:
                    self.edge("release", token, pending["edge_revision"])
                except AccessError:
                    # Edge release is idempotent, but a second transport can
                    # also fail. Only a directly observed idle/empty gate is
                    # accepted as proof that external admission reopened.
                    released_edge = self.edge()
                    if (released_edge.get("phase") != "idle"
                            or released_edge.get("streams")):
                        raise
                try:
                    atomic_json(self.state / "model-promotion-completed.json", {
                        "kind": "model-completion", "transaction_id": pending["transaction_id"],
                        "outcome": request["outcome"], "config_sha256": config["config_sha256"]})
                except OSError:
                    # The verified route is already live and both gates are
                    # conclusively open. Treat this as metadata cleanup rather
                    # than inviting an unsafe outer model rollback.
                    try: self.model_error(pending, AccessError("completion-write-failed"))
                    except OSError: pass
                    return {"status": "released", "outcome": request["outcome"]}
                try:
                    self.remove_model_journal()
                except OSError:
                    # Both gates have conclusively released after the selected
                    # outcome was verified. Never turn metadata cleanup into a
                    # model rollback; retain a conservative recovery marker.
                    try: self.model_error(pending, AccessError("journal-cleanup-failed"))
                    except OSError: pass
                return {"status": "released", "outcome": request["outcome"]}
            except Exception as error:
                self.model_error(pending, error)
                if isinstance(error, AccessError): raise
                raise AccessError("model-transition-failed") from None

    def change(self, request):
        if (not isinstance(request, dict) or set(request) != {"mode", "revision", "confirmed"}
                or request["mode"] not in ("full-access", "sandboxed") or type(request["confirmed"]) is not bool
                or not isinstance(request["revision"], str) or not HEX.fullmatch(request["revision"])):
            raise AccessError("invalid-request")
        if request["mode"] == "full-access" and not request["confirmed"]: raise AccessError("confirmation-required")
        with self.locked():
            snapshot = self.inspect()
            if snapshot["revision"] != request["revision"]: raise AccessError("inspection-changed")
            if snapshot["busy"]: raise AccessError("runtime-busy")
            pending = self.pending()
            # Missing kind is the legacy access journal. Never consume another
            # controller's journal through access-mode restoration.
            if pending and pending.get("kind", "access") == "model":
                raise AccessError("transition-recovery-required")
            if pending and pending.get("kind", "access") != "access":
                raise AccessError("settings-recovery-required")
            if pending and request["mode"] != "sandboxed": raise AccessError("restore-required")
            if not pending:
                pending = {"kind": "access", "token": os.urandom(32).hex(), "phase": "acquiring", "edge_revision": snapshot["_edge"]["revision"]}
                atomic_json(self.state / "transition.json", pending)
            token = pending["token"]
            try:
                def native_at(stage, operation=None, operation_token=None, *, timeout=60):
                    try:
                        return self.native(operation, operation_token, timeout=timeout)
                    except AccessError as error:
                        if error.code == "runtime-unavailable-or-busy":
                            raise AccessError("runtime-" + stage + "-unavailable") from None
                        raise

                if snapshot["_edge"]["phase"] == "idle": pending["edge_revision"] = snapshot["_edge"]["revision"]
                edge = self.edge("recover" if snapshot["_edge"]["phase"] == "interrupted" else "acquire", token, pending["edge_revision"])
                pending["edge_revision"] = edge["revision"]
                atomic_json(self.state / "transition.json", pending)
                native_at("initial-acquire", "acquire", token)

                def busy():
                    native = native_at("drain-acquire", "acquire", token)
                    edge = self.edge("acquire", token, pending["edge_revision"])
                    return native.get("phase") != "held" or edge.get("phase") != "held" or bool(native.get("active") or edge.get("streams"))

                def restart():
                    if busy(): return False
                    current = private_json(runtime_config_path(self), self.owner.pw_uid)
                    agents = [agent for agent in current.get("agents", {}).get("list", []) if agent.get("id") == "pixel"]
                    if len(agents) != 1: return False
                    self.dropin_for(agents[0].get("sandbox", {}).get("mode") == "off" and agents[0].get("tools", {}).get("exec", {}).get("host") == "gateway")
                    old_pid = native_at("pre-restart-read")["pid"]
                    self.gateway_service.restart(timeout=60)
                    # The pinned runtime can take over a minute to initialize
                    # on a supported guest. Observe the same restarted process;
                    # neither a failed poll nor slow readiness proves it idle.
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        try:
                            status = native_at("restart-read", timeout=min(3, max(0.1, deadline - time.monotonic())))
                            if status.get("available") and status.get("pid") != old_pid and status.get("phase") == "held":
                                # The same durable token must still own the restarted gateway.
                                native_at("restart-acquire", "acquire", token, timeout=3)
                                health = self.http(self.native_origin, "/health", self.native_key, timeout=3)
                                return health.get("ok") is True
                        except AccessError: pass
                        time.sleep(1)
                    return False

                if busy(): raise AccessError("runtime-busy")
                self.provision_probe()
                baseline_file = self.state / "service-baseline.json"
                if not baseline_file.exists():
                    if self.dropin.exists(): raise AccessError("service-baseline-missing")
                    atomic_json(baseline_file, {"boundary": self.unit_boundary()})
                pending["phase"] = "applying"
                atomic_json(self.state / "transition.json", pending)
                config = snapshot["_config"]
                # A pristine safe configuration can be verified without inventing
                # a baseline or performing an unnecessary restore.
                if not (request["mode"] == "sandboxed" and not config.get("managed") and config.get("configured_status") == "sandboxed"):
                    self.worker(request["mode"], confirmed=request["confirmed"], config_hash=config["config_sha256"], busy=busy, restart=restart)
                elif self.service_restore_required():
                    if not restart(): raise AccessError("restore-restart-failed")
                # A pending journal alone does not mean this pristine config
                # changed. Recheck the actual service boundary and core tools
                # below; restarting again can perpetually interrupt recovery.
                self.verify_held_mode(token, request["mode"])
                pending["phase"] = "releasing"
                atomic_json(self.state / "transition.json", pending)
                native_at("release", "release", token)
                pending["phase"] = "native-released"
                atomic_json(self.state / "transition.json", pending)
                self.edge("release", token, pending["edge_revision"])
                (self.state / "transition.json").unlink()
                return self.status()
            except Exception as error:
                pending["phase"] = "error"
                pending["error"] = error.code if isinstance(error, AccessError) else "transition-failed"
                atomic_json(self.state / "transition.json", pending)
                if isinstance(error, AccessError): raise
                raise AccessError("transition-failed") from None


class LaunchdAccessBridge(SystemdAccessBridge):
    """Darwin bridge for a root-owned system LaunchDaemon gateway.

    The transaction engine is shared with Linux, but every systemd-specific
    assumption is replaced here: launchd custody, plist identity and pinned
    Seatbelt profiles. A user LaunchAgent never qualifies as this bridge.
    """
    def __init__(self, install_dir, edge_key, *, gateway_target, gateway_plist,
                 gateway_process, state, gateway_binding, installed_binary,
                 gateway_owner, gateway_port=None, settings_data_dir=None, gateway_policy=None):
        if (not isinstance(gateway_target, str)
                or not re.fullmatch(r'system/[A-Za-z0-9][A-Za-z0-9.-]{0,127}', gateway_target)):
            raise AccessError('system-launchdaemon-required')
        gateway_plist = Path(gateway_plist) if isinstance(gateway_plist, str) else gateway_plist
        if (not isinstance(gateway_plist, Path) or not gateway_plist.is_absolute()
                or gateway_plist.parent != Path('/Library/LaunchDaemons')
                or gateway_plist.name != gateway_target.rsplit('/', 1)[1] + '.plist'):
            raise AccessError('system-launchdaemon-required')
        super().__init__(install_dir, edge_key, state=state, installed_binary=installed_binary,
                         gateway_owner=gateway_owner, gateway_port=gateway_port,
                         settings_data_dir=settings_data_dir, gateway_binding=gateway_binding)
        self.gateway_target = gateway_target
        self.gateway_plist = gateway_plist
        self.gateway_process = dict(gateway_process) if isinstance(gateway_process, dict) else gateway_process
        self.gateway_policy = gateway_policy
        self.gateway_service = LaunchdGatewayService(
            lambda *args, **kwargs: self.command(*args, **kwargs), AccessError,
            gateway_target, self._verify_launchd_loaded, process=self.gateway_process,
            plist=self.gateway_plist, verify_definition=self._verify_launchd_definition,
            save_stop=self._save_gateway_stop, load_stop=self._load_gateway_stop)
        # The shared change() implementation checks this path only to detect a
        # legacy systemd drop-in. Darwin mode never creates one.
        self.dropin = Path('/private/var/empty/ods-pixel-no-systemd-dropin')

    @contextlib.contextmanager
    def locked(self):
        with super().locked():
            # Do not infer successful recovery from a journal phase alone.
            # The upgrader removes this marker only after live verification.
            if os.path.lexists(self.state / 'runtime-upgrade.json'):
                raise AccessError('runtime-upgrade-recovery-required')
            yield

    @contextlib.contextmanager
    def recovery_locked(self, *, completed_digest=None):
        """Reserve the controller for the explicit runtime recovery path.

        Holding this lock does not authorize journal contents or reopening
        admission. Recovery must validate both before modifying services.
        """
        with super().locked():
            pending = os.path.lexists(self.state / 'runtime-upgrade.json')
            if completed_digest is None:
                if not pending:
                    raise AccessError('runtime-upgrade-journal-required')
            else:
                if type(completed_digest) is not str or not re.fullmatch('[a-f0-9]{64}', completed_digest):
                    raise AccessError('runtime-upgrade-digest-invalid')
                if pending:
                    raise AccessError('runtime-upgrade-pending-recovery')
                if not os.path.lexists(self.state / ('runtime-upgrade-' + completed_digest + '.completed.json')):
                    raise AccessError('runtime-upgrade-archive-required')
            if any(os.path.lexists(self.state / name) for name in
                   ('transition.json', 'policy-activation.json')):
                raise AccessError('runtime-upgrade-pending-recovery')
            yield

    def status(self):
        if os.path.lexists(self.state / 'runtime-upgrade.json'):
            return {'available': False, 'surface': 'darwin', 'configured_mode': 'unknown',
                    'effective_mode': 'unknown', 'runtime_verified': False, 'revision': None,
                    'busy': False, 'pending': True, 'reason': 'runtime-upgrade-recovery-required',
                    'scope': 'owner-host'}
        return super().status()

    def _stop_transaction(self):
        journal = self.pending()
        if (type(journal) is not dict or journal.get('kind') != 'provider'
                or journal.get('phase') not in ('invoking', 'restarting')
                or type(journal.get('token')) is not str or not HEX.fullmatch(journal['token'])):
            raise AccessError('native-stop-transaction-unavailable')
        return journal

    def _save_gateway_stop(self, witness):
        journal = self._stop_transaction()
        atomic_json(self.state / 'launchd-stop.json', {'token': journal['token'], 'witness': witness})

    def _load_gateway_stop(self):
        journal = self._stop_transaction()
        try:
            value = private_json(self.state / 'launchd-stop.json', 0, 256 * 1024)
        except (OSError, ValueError):
            raise AccessError('native-stop-witness-unavailable') from None
        if (type(value) is not dict or set(value) != {'token', 'witness'}
                or value['token'] != journal['token'] or value['witness'] is None):
            raise AccessError('native-stop-witness-unavailable')
        return value['witness']

    def _launchd_document(self):
        import grp
        import pwd
        from pixel_macos_custody import CustodyError, protected_bytes
        try:
            raw = protected_bytes(self.gateway_plist)
            document = plistlib.loads(raw)
        except (CustodyError, OSError, ValueError, TypeError, plistlib.InvalidFileException):
            raise AccessError('gateway-launchd-custody-required') from None
        if type(document) is not dict:
            raise AccessError('gateway-launchd-custody-required')
        try:
            group = grp.getgrgid(pwd.getpwnam(self.gateway_owner).pw_gid).gr_name
        except (KeyError, TypeError):
            raise AccessError('gateway-owner-unavailable') from None
        if (document.get('Label') != self.gateway_target.rsplit('/', 1)[1]
                or document.get('UserName') != self.gateway_owner
                or document.get('GroupName') != group):
            raise AccessError('gateway-launchdaemon-identity-mismatch')
        return raw, document

    def _verify_launchd_definition(self):
        self._launchd_document()
        if (self.gateway_binding is None or
                self.gateway_service.definition() != self.gateway_binding.get('definition')):
            raise AccessError('gateway-installation-changed')

    def _verify_launchd_loaded(self):
        from pixel_macos_custody import CustodyError, verify_loaded_launchd_definition
        _, document = self._launchd_document()
        try:
            self._verify_launchd_definition()
            raw = self.command(['/bin/launchctl', 'print', self.gateway_target])
            verify_loaded_launchd_definition(raw, self.gateway_target, self.gateway_plist, document)
        except CustodyError as error:
            raise AccessError(str(error)) from None

    def gateway_installation_binding(self, *, require_running=False):
        import pwd
        _, document = self._launchd_document()
        try:
            self._verify_launchd_loaded()
        except AccessError as error:
            if require_running or error.code != 'host-command-failed' or error.returncode != 113:
                raise
            # Recovery can rediscover an intentionally unloaded job only with
            # a same-transaction, same-boot root witness and fresh OS checks.
            self.gateway_service.assert_stopped()
        if require_running:
            self.gateway_service.pid(require_running=True)
        owner = pwd.getpwnam(self.gateway_owner)
        if owner.pw_uid == 0:
            raise AccessError('unsafe-gateway-owner')
        process = self.gateway_process
        if (type(process) is not dict or set(process) != {'uid', 'gid', 'executable'}
                or process['uid'] != owner.pw_uid or process['gid'] != owner.pw_gid):
            raise AccessError('gateway-process-specification-required')
        binding = {'schemaVersion': 1, 'target': self.gateway_target,
                   'plist': str(self.gateway_plist), 'definition': self.gateway_service.definition(),
                   'owner': owner.pw_name, 'executable': process['executable'], 'process': process}
        return binding

    def verify_gateway_installation_binding(self):
        if self.gateway_binding is None or self.gateway_installation_binding() != self.gateway_binding:
            raise AccessError('gateway-installation-changed')
        self._policy_state()

    def _policy_state(self):
        from pixel_macos_policy import PolicyError, policy_state
        try:
            state = policy_state(self.gateway_policy)
        except (PolicyError, OSError) as error:
            raise AccessError(str(error) if isinstance(error, PolicyError) else 'policy-unavailable') from None
        _, document = self._launchd_document()
        arguments = document.get('ProgramArguments', [])
        if arguments.count('/usr/bin/sandbox-exec') != 1:
            raise AccessError('gateway-policy-binding-mismatch')
        index = arguments.index('/usr/bin/sandbox-exec')
        if len(arguments) <= index + 3 or arguments[index + 1] != '-f':
            raise AccessError('gateway-policy-binding-mismatch')
        profile = arguments[index + 2]
        # Only Apple's fixed /etc alias is permitted; do not resolve arbitrary
        # symlinks before the custody checker has inspected the actual paths.
        if profile.startswith('/etc/'):
            profile = '/private' + profile
        if profile != state['active']:
            raise AccessError('gateway-policy-binding-mismatch')
        return state

    def _runtime_environment(self):
        _, document = self._launchd_document()
        arguments = document.get('ProgramArguments')
        if (type(arguments) is not list or len(arguments) < 4
                or arguments[:2] != ['/usr/bin/env', '-i']):
            raise AccessError('gateway-runtime-environment-unavailable')
        values = {}
        for argument in arguments[2:]:
            if not isinstance(argument, str):
                raise AccessError('gateway-runtime-environment-unavailable')
            key, separator, value = argument.partition('=')
            if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                break
            if key in values:
                raise AccessError('gateway-runtime-environment-unavailable')
            values[key] = value
        for key in ('HOME', 'OPENCLAW_CONFIG_PATH', 'OPENCLAW_STATE_DIR'):
            value = values.get(key)
            if (not isinstance(value, str) or not value.startswith('/') or value == '/'
                    or any(part in ('', '.', '..') for part in value.split('/')[1:])
                    or any(ord(char) < 32 for char in value)
                    or Path(value).resolve() != Path(value)):
                raise AccessError('gateway-runtime-path-unavailable')
        return values

    def worker_environment(self):
        self.verify_gateway_installation_binding()
        configured = self._runtime_environment()
        if (Path(configured['HOME']) != self.home
                or Path(configured['OPENCLAW_CONFIG_PATH']) != runtime_config_path(self)):
            raise AccessError('gateway-installation-changed')
        env = super().worker_environment()
        for key in ('OPENCLAW_CONFIG_PATH', 'OPENCLAW_STATE_DIR', 'PATH', 'TMPDIR',
                    'DOCKER_HOST', 'DOCKER_CONFIG', 'OPENCLAW_WRAPPER'):
            if key in configured:
                env[key] = configured[key]
        return env

    def _docker_process_context(self):
        # Docker Desktop and its socket are owner-managed. Drop root before
        # exec and use only the environment bound to the protected gateway.
        return dict(cwd='/', env=self.worker_environment(), **self._native_owner_identity())

    def _inspect_edge(self, *, timeout):
        context = self._docker_process_context()
        try:
            result = subprocess.run(['docker', 'inspect', 'ods-pixel-edge', '--format',
                                     '{{.Id}} {{.State.Running}}'],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=remaining(timeout), **context)
            return result.stdout.strip()
        except subprocess.CalledProcessError as error:
            raise AccessError('host-command-failed', returncode=error.returncode) from None
        except (OSError, subprocess.SubprocessError):
            raise AccessError('host-command-failed') from None

    def _request_edge(self, *args, **kwargs):
        return _edge_container_request(*args, **kwargs, process_context=self._docker_process_context())

    def discover(self, *, allow_installing=False):
        if platform.system() != 'Darwin':
            raise AccessError('macos-platform-required')
        if os.geteuid() != 0:
            raise AccessError('root-host-adapter-required')
        program = Path(__file__).resolve()
        for path in (program, *program.parents):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise AccessError('root-program-custody-required')
        if self.gateway_binding is None:
            raise AccessError('gateway-launchd-binding-required')
        self.verify_gateway_installation_binding()
        import pwd
        try:
            owner = pwd.getpwnam(self.gateway_owner)
        except KeyError:
            raise AccessError('gateway-owner-unavailable') from None
        if owner.pw_uid == 0 or not Path(owner.pw_dir).is_absolute() or Path(owner.pw_dir).resolve() != Path(owner.pw_dir):
            raise AccessError('unsafe-gateway-owner')
        # Like Linux's explicit adoption path, the verified root deployment
        # receipt authorizes discovery without inventing an owner ready marker.
        environment = self._runtime_environment()
        runtime_home = Path(environment['HOME'])
        runtime_config = Path(environment['OPENCLAW_CONFIG_PATH'])
        home_info = runtime_home.lstat()
        if (not stat.S_ISDIR(home_info.st_mode) or home_info.st_uid != owner.pw_uid
                or home_info.st_mode & 0o077):
            raise AccessError('unsafe-gateway-home')
        config = private_json(runtime_config, owner.pw_uid)
        binary = self.installed_binary
        if not isinstance(binary, str) or not Path(binary).is_absolute() or not os.access(binary, os.X_OK):
            raise AccessError('installed-validator-unavailable')
        port = self.configured_gateway_port(config)
        token = config.get('gateway', {}).get('auth', {}).get('token')
        if not isinstance(token, str) or not 16 <= len(token) <= 4096:
            raise AccessError('gateway-auth-unavailable')
        self.owner, self.home, self.binary = owner, runtime_home, binary
        self._runtime_config_path = runtime_config
        if (self.native_port, self.native_key) != (port, token):
            self.native_port = self.native_key = self.native_origin = self._native_identity = None
        self.native_port, self.native_key = port, token
        # Public Edge/Dashboard schemas already use darwin for macOS. Keep
        # the launchd-specific identity in the private boundary receipt.
        self.surface = 'darwin'

    def unit_boundary(self):
        definition = self.gateway_service.definition()
        value = json.dumps({'schemaVersion': 1, 'platform': 'macos-launchd',
                            'target': self.gateway_target, 'plist': str(self.gateway_plist),
                            'definition': definition, 'policy': self._policy_state()},
                           sort_keys=True, separators=(',', ':'))
        if len(value) > 4096:
            raise AccessError('gateway-boundary-unavailable')
        return value

    def dropin_for(self, enabled):
        if type(enabled) is not bool:
            raise AccessError('invalid-access-mode')
        self._verify_launchd_loaded()
        self._policy_state()
        mode = 'full-access' if enabled else 'sandboxed'
        # Persist before the file switch: interruption must never let an old
        # process qualify against newly selected on-disk policy bytes.
        atomic_json(self.state / 'policy-activation.json', {
            'mode': mode, 'before': self.gateway_service.transaction_identity()})
        from pixel_macos_policy import PolicyError, select_policy
        try:
            select_policy(self.gateway_policy, mode)
        except (PolicyError, OSError) as error:
            raise AccessError(str(error) if isinstance(error, PolicyError) else 'policy-selection-failed') from None
        # change() must still observe the old PID, restart, and run the core
        # tool proof. Selecting a file alone never establishes an effective mode.

    def service_restore_required(self):
        return (self._policy_state()['activeMode'] != 'sandboxed'
                or (self.state / 'policy-activation.json').exists())

    def _policy_activation_identity(self, mode):
        path = self.state / 'policy-activation.json'
        if not path.exists():
            return None
        record = private_json(path, 0, 8192)
        current = self.gateway_service.transaction_identity()
        try:
            before = record['before']
            if (set(record) != {'mode', 'before'} or record['mode'] != mode
                    or type(before) is not dict or set(before) != {'boot', 'pid', 'started'}
                    or type(before['pid']) is not int or before['pid'] <= 0
                    or type(before['started']) is not int or before['started'] <= 0
                    or type(before['boot']) is not str
                    or current['boot'] != before['boot'] or current['pid'] == before['pid']
                    or current['started'] <= before['started']):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise AccessError('policy-restart-unconfirmed') from None
        return current

    def verify_held_mode(self, token, mode):
        if mode not in ('full-access', 'sandboxed') or type(token) is not str or not HEX.fullmatch(token):
            raise AccessError('invalid-model-transition')
        self.provision_probe()
        baseline_file = self.state / 'service-baseline.json'
        if not baseline_file.exists():
            raise AccessError('service-baseline-missing')
        baseline = private_json(baseline_file, 0, 8192).get('boundary')
        boundary = self.unit_boundary()
        try:
            expected = json.loads(baseline)
            if expected['policy']['activeMode'] != 'sandboxed':
                raise ValueError()
            expected['policy']['activeMode'] = mode
        except (ValueError, TypeError, KeyError):
            raise AccessError('service-baseline-invalid') from None
        if expected != json.loads(boundary):
            raise AccessError('service-boundary-mismatch')
        activation_identity = self._policy_activation_identity(mode)
        try:
            proof = self.native('probe', token)
        except AccessError as error:
            try:
                snapshot = self.native(timeout=10)
                failure = snapshot.get('probe_failure') if snapshot.get('phase') == 'held' else None
            except AccessError:
                failure = None
            if failure in PROBE_FAILURES:
                raise AccessError('runtime-proof-' + failure) from None
            raise error
        if proof.get('proof', {}).get('mode') != mode:
            raise AccessError('runtime-proof-failed')
        verified_config = self.worker()
        if verified_config.get('configured_status') != mode:
            raise AccessError('configured-mode-changed')
        if activation_identity is not None and (
                proof.get('pid') != activation_identity['pid']
                or self.gateway_service.transaction_identity() != activation_identity):
            raise AccessError('policy-restart-unconfirmed')
        atomic_json(self.state / 'verified.json', {'pid': proof['pid'], 'proof': proof['proof'],
                    'config_sha256': verified_config['config_sha256'], 'boundary': boundary})
        if activation_identity is not None:
            (self.state / 'policy-activation.json').unlink()
