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
from pathlib import Path
import platform
import re
import selectors
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import pixel_access_protocol as protocol

UNIT = "openclaw-gateway.service"
STATE = Path("/var/lib/ods-pixel-access")
DROPIN = Path("/etc/systemd/system/openclaw-gateway.service.d/90-ods-full-access.conf")
HEX = re.compile(r"^[a-f0-9]{64}$")
OWNER_TIMEOUT = 300
OWNER_EXIT_TIMEOUT = 5
OWNER_TERMINATE_TIMEOUT = 10
_DEADLINE = contextvars.ContextVar("pixel_access_operation_deadline", default=None)


class AccessError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


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


def _edge_container_request(container_id, path, key, payload, timeout=20):
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
            bufsize=0)
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
    def __init__(self, install_dir, edge_key, *, state=STATE, dropin=DROPIN, installed_binary=None, gateway_owner=None, settings_data_dir=None):
        self.install = Path(install_dir).resolve()
        self.edge_key = edge_key
        self.state = Path(state)
        self.dropin = Path(dropin)
        self.installed_binary, self.gateway_owner = installed_binary, gateway_owner
        self.settings_data_dir = settings_data_dir

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

    def command(self, args, timeout=20):
        timeout = remaining(timeout)
        try:
            result = subprocess.run(args, check=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, timeout=timeout)
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raise AccessError("host-command-failed") from None

    def discover(self):
        if platform.system() != "Linux":
            raise AccessError("macos-launchd-adapter-missing" if platform.system() == "Darwin" else "native-windows-adapter-missing")
        if os.geteuid() != 0: raise AccessError("root-host-adapter-required")
        if not Path("/run/systemd/system").is_dir(): raise AccessError("systemd-unavailable")
        program = Path(__file__).resolve()
        for path in (program, *program.parents):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise AccessError("root-program-custody-required")
        # A root host agent must also execute protected code. Normal installs
        # run it as the owner; historical root overrides need an explicit repair.
        agent_user = self.command(["systemctl", "show", "ods-host-agent.service", "--property=User", "--value"])
        if agent_user in ("", "root", "0"):
            pid = int(self.command(["systemctl", "show", "ods-host-agent.service", "--property=MainPID", "--value"]))
            args = Path("/proc/%d/cmdline" % pid).read_bytes().split(b"\0")
            if b"-I" not in args: raise AccessError("root-host-agent-isolation-required")
            scripts = [Path(os.fsdecode(arg)) for arg in args if arg.endswith(b"ods-host-agent.py")]
            if len(scripts) != 1 or not scripts[0].is_absolute(): raise AccessError("root-host-agent-custody-required")
            for path in (scripts[0], *scripts[0].parents):
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                    raise AccessError("root-host-agent-custody-required")
            for directory, folders, files in os.walk(scripts[0].parent, followlinks=False):
                for name in folders + files:
                    info = (Path(directory) / name).lstat()
                    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                        raise AccessError("root-host-agent-custody-required")
        import pwd
        user = self.command(["systemctl", "show", UNIT, "--property=User", "--value"])
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user): raise AccessError("gateway-owner-unavailable")
        if user != self.gateway_owner: raise AccessError("installed-owner-changed")
        owner = pwd.getpwnam(user)
        home = Path(owner.pw_dir)
        if owner.pw_uid == 0 or not home.is_absolute() or home.resolve() != home:
            raise AccessError("unsafe-gateway-owner")
        marker = private_json(home / ".config/ods/pixel-managed.json", owner.pw_uid, 65536)
        if (marker.get("schema_version") != 2 or marker.get("manager") != "ods"
                or marker.get("state") != "ready" or Path(marker.get("install_dir", "")).resolve() != self.install):
            raise AccessError("managed-owner-mismatch")
        config = private_json(home / ".openclaw/openclaw.json", owner.pw_uid)
        binary = self.installed_binary
        if not isinstance(binary, str) or not Path(binary).is_absolute() or not os.access(binary, os.X_OK):
            raise AccessError("installed-validator-unavailable")
        port = config.get("gateway", {}).get("port", 18789)
        token = config.get("gateway", {}).get("auth", {}).get("token")
        if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str) or not 16 <= len(token) <= 4096:
            raise AccessError("gateway-auth-unavailable")
        self.owner, self.home, self.binary = owner, home, binary
        self.native_origin, self.native_key = "http://127.0.0.1:%d" % port, token
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
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=budget)
        timer = None
        expired = threading.Event()
        try:
            connection.connect()
            transport = connection.sock
            budget = deadline - time.monotonic()
            if budget <= 0: raise AccessError("runtime-operation-timeout")
            def interrupt():
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
                if response.status != 200: raise AccessError("runtime-unavailable-or-busy")
                raw = response.read(65537)
            if expired.is_set() or time.monotonic() >= deadline:
                raise AccessError("runtime-operation-timeout")
            if len(raw) > 65536: raise ValueError()
            value = protocol.decode_frame(raw.decode("utf-8") + "\n", 65537)
            if not isinstance(value, dict): raise ValueError()
            return value
        except (OSError, http.client.HTTPException, ValueError, UnicodeError):
            if expired.is_set() or time.monotonic() >= deadline:
                raise AccessError("runtime-operation-timeout") from None
            raise AccessError("runtime-unavailable-or-busy") from None
        finally:
            if timer is not None: timer.cancel()
            connection.close()
            if timer is not None: timer.join(timeout=1)

    def native(self, operation=None, token=None, *, timeout=60):
        payload = None
        if operation:
            snapshot = self.native(timeout=timeout)
            if snapshot.get("stopped"):
                if operation != "acquire": raise AccessError("gateway-restart-required")
                return self.stopped_native(token)
            payload = dict(operation=operation, token=token, revision=snapshot["revision"])
        try:
            return self.http(self.native_origin, "/pixel-ods/access-runtime", self.native_key, payload, timeout=timeout)
        except AccessError:
            pending = self.pending()
            if operation is None and pending:
                return self.stopped_native(pending["token"])
            raise

    def stopped_native(self, token):
        """Crash recovery only: an owned durable hold and an empty stopped unit.

        An unreachable HTTP endpoint alone never proves idle. Never stop/kill an
        active unit to satisfy this check.
        """
        values = self.command(["systemctl", "show", UNIT, "--property=MainPID,ActiveState,ControlGroup"])
        fields = dict(line.split("=", 1) for line in values.splitlines() if "=" in line)
        if fields.get("MainPID") != "0" or fields.get("ActiveState") not in ("inactive", "failed"):
            raise AccessError("native-idle-unconfirmed")
        group = fields.get("ControlGroup", "")
        if group:
            root = Path("/sys/fs/cgroup")
            path = (root / group.lstrip("/")).resolve()
            if root not in path.parents: raise AccessError("native-idle-unconfirmed")
            if path.exists() and (path / "cgroup.procs").read_text().strip():
                raise AccessError("native-idle-unconfirmed")
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
        identity = self.command(["docker", "inspect", "ods-pixel-edge", "--format",
                                 "{{.Id}} {{.State.Running}}"], timeout=budget).split()
        if len(identity) != 2 or not HEX.fullmatch(identity[0]) or identity[1] != "true":
            raise AccessError("edge-container-unavailable")
        payload = dict(token=token, revision=revision) if operation else None
        return _edge_container_request(identity[0],
            "/v1/transition" + ("/" + operation if operation else ""),
            self.edge_key, payload, timeout=budget - (time.monotonic() - started))

    def worker(self, operation="status", *, confirmed=False, config_hash=None, busy=None, restart=None,
               transaction_id=None, settings_revision=None, preferences=None, capabilities=None, activate_settings=None,
               binding=None, activate_provider=None, expected_projection=None, provider_probe=None):
        script = Path(__file__).resolve().parent / "access_mode_worker.py"
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
        env = {"HOME": str(self.home), "USER": self.owner.pw_name, "LOGNAME": self.owner.pw_name,
               "PATH": str(Path(self.binary).parent) + ":/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
        request = dict(operation=operation, openclaw=self.binary, config_sha256=config_hash, confirmed=confirmed)
        if operation == 'provider-worker-status':
            request['provider_probe'] = provider_probe
        if operation in ("settings-apply", "settings-recover", "provider-change", "provider-recover"):
            request["transaction_id"] = transaction_id
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
        process = subprocess.Popen([launcher, "-u", self.owner.pw_name, "--", sys.executable, "-I", "-u", str(script)],
                                   cwd="/", env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, bufsize=1)
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

    def inspect(self):
        self.discover()
        config, native, edge = self.worker(), self.native(), self.edge()
        if not native.get("available") or edge.get("capability") != "available": raise AccessError("admission-gate-unavailable")
        pid = int(self.command(["systemctl", "show", UNIT, "--property=MainPID", "--value"]))
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
        self.command(["systemctl", "daemon-reload"])

    def unit_boundary(self):
        return self.command(["systemctl", "show", UNIT, "--property=ProtectSystem,ProtectHome,NoNewPrivileges,CapabilityBoundingSet,BindReadOnlyPaths,ReadOnlyPaths,PrivateTmp"])

    def provision_probe(self):
        base = Path("/var/lib/ods-pixel-access-probes")
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
            # Missing kind is the legacy access journal. Never consume a
            # settings (or unknown) journal through access-mode restoration.
            if pending and pending.get("kind", "access") != "access":
                raise AccessError("settings-recovery-required")
            if pending and request["mode"] != "sandboxed": raise AccessError("restore-required")
            if not pending:
                pending = {"kind": "access", "token": os.urandom(32).hex(), "phase": "acquiring", "edge_revision": snapshot["_edge"]["revision"]}
                atomic_json(self.state / "transition.json", pending)
            token = pending["token"]
            try:
                if snapshot["_edge"]["phase"] == "idle": pending["edge_revision"] = snapshot["_edge"]["revision"]
                edge = self.edge("recover" if snapshot["_edge"]["phase"] == "interrupted" else "acquire", token, pending["edge_revision"])
                pending["edge_revision"] = edge["revision"]
                atomic_json(self.state / "transition.json", pending)
                self.native("acquire", token)

                def busy():
                    native = self.native("acquire", token)
                    edge = self.edge("acquire", token, pending["edge_revision"])
                    return native.get("phase") != "held" or edge.get("phase") != "held" or bool(native.get("active") or edge.get("streams"))

                def restart():
                    if busy(): return False
                    current = private_json(self.home / ".openclaw/openclaw.json", self.owner.pw_uid)
                    agents = [agent for agent in current.get("agents", {}).get("list", []) if agent.get("id") == "pixel"]
                    if len(agents) != 1: return False
                    self.dropin_for(agents[0].get("sandbox", {}).get("mode") == "off" and agents[0].get("tools", {}).get("exec", {}).get("host") == "gateway")
                    old_pid = self.native()["pid"]
                    self.command(["systemctl", "restart", UNIT], timeout=60)
                    # The pinned runtime can take over a minute to initialize
                    # on a supported guest. Observe the same restarted process;
                    # neither a failed poll nor slow readiness proves it idle.
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        try:
                            status = self.native(timeout=min(3, max(0.1, deadline - time.monotonic())))
                            if status.get("available") and status.get("pid") != old_pid and status.get("phase") == "held":
                                # The same durable token must still own the restarted gateway.
                                self.native("acquire", token, timeout=3)
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
                elif self.dropin.exists():
                    if not restart(): raise AccessError("restore-restart-failed")
                # A pending journal alone does not mean this pristine config
                # changed. Recheck the actual service boundary and core tools
                # below; restarting again can perpetually interrupt recovery.
                boundary = self.unit_boundary()
                baseline = private_json(baseline_file, 0, 8192)["boundary"]
                if request["mode"] == "sandboxed":
                    if boundary != baseline: raise AccessError("service-restore-mismatch")
                else:
                    def other_settings(value):
                        return [line for line in value.splitlines() if not line.startswith(("ProtectSystem=", "ProtectHome="))]
                    if ("ProtectSystem=no" not in boundary or "ProtectHome=no" not in boundary
                            or other_settings(boundary) != other_settings(baseline)):
                        raise AccessError("service-boundary-mismatch")
                proof = self.native("probe", token)
                if proof.get("proof", {}).get("mode") != request["mode"]: raise AccessError("runtime-proof-failed")
                verified_config = self.worker()
                atomic_json(self.state / "verified.json", {"pid": proof["pid"], "proof": proof["proof"],
                            "config_sha256": verified_config["config_sha256"], "boundary": boundary})
                pending["phase"] = "releasing"
                atomic_json(self.state / "transition.json", pending)
                self.edge("release", token, pending["edge_revision"])
                self.native("release", token)
                (self.state / "transition.json").unlink()
                return self.status()
            except Exception as error:
                pending["phase"] = "error"
                pending["error"] = error.code if isinstance(error, AccessError) else "transition-failed"
                atomic_json(self.state / "transition.json", pending)
                if isinstance(error, AccessError): raise
                raise AccessError("transition-failed") from None
