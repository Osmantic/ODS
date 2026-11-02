"""Shared pieces of Portal integrations: errors, limits and the owner-only state files."""
import base64
import ctypes
import email.message
import errno
import hashlib
import hmac
import http.client
import io
import math
import json
import os
import pathlib
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import uuid
import urllib.request

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_HTTP_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RESULT_CHARS = 8000
NETWORK_TIMEOUT = 20
# UTF8 agent JSON can expand3x as MCP ASCII escapes, then4/3 as private base64.
MAX_HTTP_WORKER_INPUT = 8 * MAX_REQUEST_BYTES
ACTIVITY_LINES = 500
# The owner's apps and the local addresses the ODS extension catalog publishes them on.
APP_URLS = {"documents": "http://127.0.0.1:7807", "photos": "http://127.0.0.1:2283",
            "watch": "http://127.0.0.1:11039", "news": "http://127.0.0.1:11029"}
NATIVE_AGENT_SOCKET = "/private/var/lib/ods-pixel-integrations/control.sock"
NATIVE_AUTH_ROOT = pathlib.Path("/private/var/lib/ods-pixel-integrations-auth")
OWNER_AUTH_DOMAIN = b"ODS native integrations owner request v1\0"
OWNER_REPLY_DOMAIN = b"ODS native integrations owner reply v1\0"
OWNER_AUTH_SECONDS = 5
_HTTP_LOCK = threading.Lock()
_HTTP_STOP = threading.Event()
_HTTP_WORKERS = set()


def _native_acl_library():
    if sys.platform != 'darwin':
        raise ValueError('native-integrations-acl-platform-required')
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    pointer = ctypes.c_void_p
    signatures = {
        'acl_get_fd_np': ([ctypes.c_int, ctypes.c_int], pointer),
        'acl_valid': ([pointer], ctypes.c_int),
        'acl_get_entry': ([pointer, ctypes.c_int, ctypes.POINTER(pointer)], ctypes.c_int),
        'acl_get_tag_type': ([pointer, ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
        'acl_get_permset_mask_np': ([pointer, ctypes.POINTER(ctypes.c_uint64)], ctypes.c_int),
        'acl_get_flagset_np': ([pointer, ctypes.POINTER(pointer)], ctypes.c_int),
        'acl_get_flag_np': ([pointer, ctypes.c_int], ctypes.c_int),
        'acl_get_qualifier': ([pointer], pointer),
        'acl_free': ([pointer], ctypes.c_int),
        'mbr_uid_to_uuid': ([ctypes.c_uint32, pointer], ctypes.c_int),
    }
    for name, (arguments, result) in signatures.items():
        try:
            function = getattr(lib, name)
        except AttributeError:
            raise ValueError('native-integrations-acl-api-unavailable') from None
        function.argtypes, function.restype = arguments, result
    return lib


def verify_native_no_acl(fd):
    lib = _native_acl_library()
    ctypes.set_errno(0)
    acl = lib.acl_get_fd_np(fd, 0x100)
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:
            return
        raise ValueError('native-integrations-auth-custody-required')
    try:
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        if (lib.acl_valid(acl) != 0 or lib.acl_get_entry(acl, 0, ctypes.byref(entry)) != -1
                or ctypes.get_errno() != errno.EINVAL):
            raise ValueError('native-integrations-auth-custody-required')
    finally:
        lib.acl_free(acl)


def verify_native_owner_acl(fd, uid, *, directory=False):
    """Exactly one non-inheriting owner-read ACL, queried on the open inode.

    Public ABI: Apple Libc include/sys/acl.h and Libinfo membership.h. No ls
    text/name parsing, staff-group grant, inherited ACE or write permission.
    This is a descriptor ACL check, not a Seatbelt or same-UID isolation proof.
    """
    lib = _native_acl_library()
    pointer = ctypes.c_void_p
    acl = lib.acl_get_fd_np(fd, 0x100)
    if not acl:
        raise ValueError('native-integrations-owner-acl-required')
    try:
        entry, tag, mask = pointer(), ctypes.c_int(), ctypes.c_uint64()
        if (lib.acl_valid(acl) != 0 or lib.acl_get_entry(acl, 0, ctypes.byref(entry)) != 0
                or lib.acl_get_tag_type(entry, ctypes.byref(tag)) != 0 or tag.value != 1
                or lib.acl_get_permset_mask_np(entry, ctypes.byref(mask)) != 0):
            raise ValueError('native-integrations-owner-acl-required')
        expected = (1 << 1) | (1 << 7) | (1 << 11) | ((1 << 3) if directory else (1 << 9))
        if mask.value != expected:
            raise ValueError('native-integrations-owner-acl-required')
        for item in (acl, entry):
            flags = pointer()
            if lib.acl_get_flagset_np(item, ctypes.byref(flags)) != 0:
                raise ValueError('native-integrations-owner-acl-required')
            for flag in (1, 1 << 4, 1 << 5, 1 << 6, 1 << 7, 1 << 8, 1 << 17):
                if lib.acl_get_flag_np(flags, flag) != 0:
                    raise ValueError('native-integrations-owner-acl-required')
        qualifier = lib.acl_get_qualifier(entry)
        if not qualifier:
            raise ValueError('native-integrations-owner-acl-required')
        try:
            wanted = (ctypes.c_ubyte * 16)()
            if lib.mbr_uid_to_uuid(uid, wanted) != 0 or ctypes.string_at(qualifier, 16) != bytes(wanted):
                raise ValueError('native-integrations-owner-acl-required')
        finally:
            lib.acl_free(qualifier)
        ctypes.set_errno(0)
        if lib.acl_get_entry(acl, -1, ctypes.byref(entry)) != -1 or ctypes.get_errno() != errno.EINVAL:
            raise ValueError('native-integrations-owner-acl-required')
    finally:
        lib.acl_free(acl)


def _native_auth_directory():
    if sys.platform != 'darwin':
        return os.open(NATIVE_AUTH_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in NATIVE_AUTH_ROOT.parts[1:]:
            info = os.fstat(parent)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('native-integrations-auth-custody-required')
            verify_native_no_acl(parent)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        verify_native_no_acl(parent)
        return parent
    except BaseException:
        os.close(parent)
        raise


def native_owner_key(uid=None):
    """Read the root-provisioned credential, never a request/env-selected path.

    The root-owned directory/file grant only the selected owner search/read
    through Darwin ACLs. They cannot be replaced by that account. The gateway
    profile explicitly denies this entire tree; physical Seatbelt/alias
    qualification is additional to these descriptor/custody checks.
    """
    uid = os.getuid() if uid is None else uid
    if type(uid) is not int or not 0 < uid < 4294967295:
        raise ValueError("native-integrations-owner-required")
    parent = _native_auth_directory()
    try:
        info = os.fstat(parent)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o755:
            raise ValueError("native-integrations-auth-custody-required")
        folder = os.open(str(uid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(folder)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("native-integrations-auth-custody-required")
            if sys.platform == 'darwin':
                verify_native_owner_acl(folder, uid, directory=True)
            fd = os.open("owner.key", os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=folder)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 32):
                    raise ValueError("native-integrations-auth-custody-required")
                if sys.platform == 'darwin':
                    verify_native_owner_acl(fd, uid)
                value = os.read(fd, 33)
                if len(value) != 32:
                    raise ValueError("native-integrations-auth-custody-required")
                return value
            finally:
                os.close(fd)
        finally:
            os.close(folder)
    finally:
        os.close(parent)


def validate_native_state(path, *, uid=None):
    """Reject exposed/aliased state rather than chmod or adopt it at startup."""
    uid = os.getuid() if uid is None else uid
    if sys.platform != 'darwin' or type(uid) is not int or uid <= 0:
        raise ValueError('native-integrations-owner-state-required')
    parent = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(parent)
        if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('native-integrations-private-state-required')
        verify_native_no_acl(parent)
        entries = os.listdir(parent)
        if len(entries) > 512:
            raise ValueError('native-integrations-state-inventory-too-large')
        for name in entries:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if name == 'owner.sock' and stat.S_ISSOCK(info.st_mode):
                if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                    raise ValueError('native-integrations-private-state-required')
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('native-integrations-private-state-required')
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
            try:
                current = os.fstat(fd)
                if (current.st_dev != info.st_dev or current.st_ino != info.st_ino or current.st_uid != uid
                        or stat.S_IMODE(current.st_mode) != 0o600 or current.st_nlink != 1):
                    raise ValueError('native-integrations-private-state-required')
                verify_native_no_acl(fd)
            finally:
                os.close(fd)
    finally:
        os.close(parent)


def prepare_native_state(path):
    """Create private config components as the owner, never mkdir then root chown."""
    if sys.platform != 'darwin' or os.getuid() <= 0:
        raise ValueError('native-integrations-owner-state-required')
    path = pathlib.Path(path)
    if not path.is_absolute() or path.parts[-3:] != ('.config', 'ods', 'pixel-integrations'):
        raise ValueError('native-integrations-owner-state-required')
    parent = os.open(path.parents[2], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        home = os.fstat(parent)
        if home.st_uid != os.getuid() or home.st_mode & 0o022:
            raise ValueError('native-integrations-private-state-required')
        for index, name in enumerate(('.config', 'ods', 'pixel-integrations')):
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(child)
                if info.st_uid != os.getuid() or info.st_mode & (0o077 if index == 2 else 0o022):
                    raise ValueError('native-integrations-private-state-required')
                verify_native_no_acl(child)
                os.fsync(child)
                os.fsync(parent)
            except BaseException:
                os.close(child)
                raise
            os.close(parent)
            parent = child
        os.fsync(parent)
    finally:
        os.close(parent)
    validate_native_state(path)


def owner_proof(key, nonce, request):
    if type(key) is not bytes or len(key) != 32 or type(nonce) is not str or len(nonce) != 64:
        raise ValueError("invalid-owner-authentication")
    try:
        challenge = bytes.fromhex(nonce)
    except ValueError:
        raise ValueError("invalid-owner-authentication") from None
    if (len(challenge) != 32 or challenge.hex() != nonce or type(request) is not dict
            or type(request.get('schemaVersion')) is not int or request['schemaVersion'] != 1):
        raise ValueError("invalid-owner-authentication")
    body = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("invalid-owner-authentication")
    return hmac.new(key, OWNER_AUTH_DOMAIN + challenge + body, hashlib.sha256).hexdigest()


def owner_auth_binding(server_nonce, client_nonce):
    values = []
    for value in (server_nonce, client_nonce):
        if type(value) is not str or len(value) != 64:
            raise ValueError('invalid-owner-authentication')
        raw = bytes.fromhex(value)
        if len(raw) != 32 or raw.hex() != value:
            raise ValueError('invalid-owner-authentication')
        values.append(raw)
    return hashlib.sha256(b'ODS native integrations connection v1\0' + b''.join(values)).hexdigest()


def owner_reply_proof(key, nonce, request, reply):
    binding = bytes.fromhex(owner_proof(key, nonce, request))
    if type(reply) is not dict:
        raise ValueError('invalid-owner-response')
    body = json.dumps(reply, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError('invalid-owner-response')
    return hmac.new(key, OWNER_REPLY_DOMAIN + bytes.fromhex(nonce) + binding + body, hashlib.sha256).hexdigest()


def read_owner_frame(connection, limit, deadline):
    """One complete frame under a whole-operation deadline, including drips."""
    data = bytearray()
    while b"\n" not in data and len(data) <= limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("owner authentication timed out")
        connection.settimeout(remaining)
        piece = connection.recv(min(65536, limit + 1 - len(data)))
        if not piece:
            break
        data.extend(piece)
    if not data.endswith(b"\n") or data.count(b"\n") != 1 or len(data) > limit:
        raise ValueError("invalid-owner-frame")
    return bytes(data)


def native_owner_request(socket_path, request, timeout=130):
    """Authenticate one exact request; connection loss never triggers a retry."""
    key = native_owner_key()
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(OWNER_AUTH_SECONDS)
    authentication_deadline = time.monotonic() + OWNER_AUTH_SECONDS
    try:
        connection.connect(str(socket_path))
        challenge = json.loads(read_owner_frame(connection, 256, authentication_deadline))
        if (type(challenge) is not dict or set(challenge) != {"schemaVersion", "challenge"}
                or type(challenge["schemaVersion"]) is not int or challenge["schemaVersion"] != 1):
            raise ValueError("invalid-owner-authentication")
        client_nonce = os.urandom(32).hex()
        binding = owner_auth_binding(challenge['challenge'], client_nonce)
        envelope = {"schemaVersion": 1, "clientNonce": client_nonce, "request": request,
                    "proof": owner_proof(key, binding, request)}
        remaining = authentication_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('owner authentication timed out')
        connection.settimeout(remaining)
        connection.sendall((json.dumps(envelope, separators=(",", ":"), allow_nan=False) + "\n").encode())
        result = json.loads(read_owner_frame(connection, MAX_RESPONSE_BYTES + 512, time.monotonic() + timeout))
        if (type(result) is not dict or set(result) != {'schemaVersion', 'reply', 'proof'}
                or type(result['schemaVersion']) is not int or result['schemaVersion'] != 1
                or type(result['proof']) is not str or len(result['proof']) != 64
                or any(char not in '0123456789abcdef' for char in result['proof'])
                or not hmac.compare_digest(result['proof'], owner_reply_proof(key, binding, request, result['reply']))):
            raise ValueError("invalid-owner-response")
        return result['reply']
    finally:
        connection.close()


class IntegrationError(Exception):
    """A refusal or failure with a code for programs and a sentence for people."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message



class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Authorization must never leave the configured endpoint, even if it
        # redirects to another port on the same machine. The owner can review
        # and save the final address explicitly instead.
        raise IntegrationError("redirect-refused", "The integration endpoint redirected the request. "
                               "Use its final address instead.")


class _BufferedResponse(io.BytesIO):
    def __init__(self, body, status, headers):
        super().__init__(body)
        self.status = self.code = status
        self.headers = email.message.Message()
        for name, value in headers.items():
            self.headers[name] = value

    def getcode(self):
        return self.status


def stop_http_workers():
    """Native shutdown: close admission and reap only our own subprocess handles."""
    with _HTTP_LOCK:
        _HTTP_STOP.set()
        workers = list(_HTTP_WORKERS)
    for worker in workers:
        if worker.poll() is None:
            worker.kill()
    for worker in workers:
        worker.wait(timeout=3)


def safe_urlopen(request, *, timeout=NETWORK_TIMEOUT, max_response_bytes=MAX_RESPONSE_BYTES):
    """One bounded HTTP operation, including DNS, headers and the full body.

    urllib's socket timeout only bounds inactivity. A private owned worker lets
    us stop/reap an operation even when a peer drips headers or body forever.
    Credentials travel only through its private pipe, never argv/environment.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise IntegrationError("integration-timeout", "The integration request timed out; its result could not be verified.")
    if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= MAX_HTTP_RESPONSE_BYTES:
        raise IntegrationError("integration-response-limit", "Invalid integration response limit.")
    timeout = min(float(timeout), NETWORK_TIMEOUT)
    deadline = time.monotonic() + timeout
    payload = json.dumps({"url": request.full_url, "method": request.get_method(),
                          "headers": dict(request.header_items()),
                          "body": base64.b64encode(request.data or b"").decode("ascii"),
                          "responseLimit": max_response_bytes}).encode()
    if len(payload) > MAX_HTTP_WORKER_INPUT:
        raise IntegrationError("request-too-large", "The integration request is too large.")
    with _HTTP_LOCK:
        if _HTTP_STOP.is_set():
            raise IntegrationError('integration-stopping', 'The integrations service is stopping; no new request was started.')
        worker = subprocess.Popen([sys.executable, "-I", "-B", str(pathlib.Path(__file__).resolve()), "--http-worker"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        _HTTP_WORKERS.add(worker)
    try:
        output, _ = worker.communicate(payload, timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.communicate()  # No terminal receipt until this exact owned worker is reaped.
        raise IntegrationError("integration-timeout", "The integration request timed out; its result could not be verified. "
                               "Check the service before trying again.") from None
    finally:
        with _HTTP_LOCK:
            _HTTP_WORKERS.discard(worker)
    if worker.returncode != 0 or len(output) > 2 * max_response_bytes + 128 * 1024:
        raise IntegrationError("integration-unavailable", "The integration request did not complete.")
    try:
        value = json.loads(output)
        if value.get("kind") == "http-error":
            raise urllib.error.HTTPError(request.full_url, value["status"], "Integration endpoint refused the request.", {}, None)
        if value.get("kind") == "network-error":
            raise urllib.error.URLError("Integration endpoint could not be reached.")
        if value.get("kind") == "not-connected":
            reason = (socket.gaierror("Integration endpoint name could not be resolved.")
                      if value.get("reason") == "dns" else ConnectionRefusedError("Integration endpoint refused the connection."))
            raise urllib.error.URLError(reason)
        if value.get("kind") == "timeout":
            raise IntegrationError("integration-timeout", "The integration request timed out; its result could not be verified. "
                                   "Check the service before trying again.")
        if value.get("kind") == "redirect-refused":
            raise IntegrationError("redirect-refused", "The integration endpoint redirected the request. Use its final address instead.")
        if value.get("kind") == "too-large":
            raise IntegrationError("integration-response-too-large", "The integration endpoint sent too much data.")
        if value.get("kind") != "response":
            raise ValueError
        body = base64.b64decode(value["body"], validate=True)
        if len(body) > max_response_bytes:
            raise IntegrationError("integration-response-too-large", "The integration endpoint sent too much data.")
        return _BufferedResponse(body, value["status"], value["headers"])
    except (KeyError, TypeError, ValueError):
        raise IntegrationError("integration-protocol", "The integration endpoint returned an invalid response.") from None


def _http_worker():
    # No inherited owner environment, hooks, plugins or subprocess commands.
    # This worker performs only one HTTP request and writes a bounded envelope.
    http.client._MAXLINE = 16 * 1024
    http.client._MAXHEADERS = 64
    try:
        raw = sys.stdin.buffer.read(MAX_HTTP_WORKER_INPUT + 1)
        if len(raw) > MAX_HTTP_WORKER_INPUT:
            raise ValueError
        value = json.loads(raw)
        response_limit = value.get("responseLimit", MAX_RESPONSE_BYTES)
        if type(response_limit) is not int or not 1 <= response_limit <= MAX_HTTP_RESPONSE_BYTES:
            raise ValueError
        request = urllib.request.Request(value["url"], data=base64.b64decode(value["body"], validate=True),
                                         headers=value["headers"], method=value["method"])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirects())
        with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
            if sum(len(key) + len(item) for key, item in response.headers.items()) > 16 * 1024:
                raise ValueError
            body = response.read(response_limit + 1)
            # Keep the size failure fixed; never echo remote content or URLs.
            if len(body) > response_limit:
                result = {"kind": "too-large"}
            else:
                headers = {key: response.headers[key] for key in ("Content-Type", "Mcp-Session-Id") if key in response.headers}
                result = {"kind": "response", "status": response.status, "headers": headers,
                          "body": base64.b64encode(body).decode("ascii")}
    except urllib.error.HTTPError as error:
        result = {"kind": "http-error", "status": error.code}
        error.close()
    except IntegrationError:
        result = {"kind": "redirect-refused"}
    except TimeoutError:
        result = {"kind": "timeout"}
    except urllib.error.URLError as error:
        if isinstance(error.reason, (ConnectionRefusedError, socket.gaierror)):
            result = {"kind": "not-connected", "reason": "dns" if isinstance(error.reason, socket.gaierror) else "refused"}
        else:
            result = {"kind": "timeout" if isinstance(error.reason, TimeoutError) else "network-error"}
    except OSError:
        result = {"kind": "network-error"}
    except Exception:
        result = {"kind": "protocol-error"}
    sys.stdout.buffer.write(json.dumps(result, separators=(",", ":")).encode())
    sys.stdout.buffer.flush()


def redact_secrets(value, secrets):
    """Remove known configured plaintext string values before output clipping.

    Preserve structural JSON field names, including when a short secret is
    itself a word such as "id" or "name".

    This is output hygiene, not a sandbox for a malicious server: transformed,
    encoded or independently exfiltrated values are outside this guarantee.
    """
    known = set()

    def collect(item):
        if isinstance(item, str):
            if item:
                known.add(item)
                # stderr is consumed line by line; a multiline configured
                # value must not reveal its individual nonempty lines either.
                known.update(part for part in item.splitlines() if part)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for child in item:
                collect(child)

    collect(secrets)
    ordered = sorted(known, key=lambda item: (-len(item), item))

    def clean(item):
        if isinstance(item, str):
            for secret in ordered:
                item = item.replace(secret, "[redacted]")
            return item
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, tuple):
            return tuple(clean(child) for child in item)
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items()}
        return item

    return clean(value)


def now():
    return int(time.time())


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clip(text, limit=MAX_RESULT_CHARS):
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def default_apps():
    return {"documents": {"enabled": False, "url": APP_URLS["documents"]},
            "photos": {"enabled": False, "url": APP_URLS["photos"]},
            "watch": {"enabled": False, "url": APP_URLS["watch"], "changes": "ask"},
            "news": {"enabled": False, "url": APP_URLS["news"], "username": "",
                     "digest": {"enabled": False, "time": "07:00", "timezone": "UTC"}}}


def default_config():
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": 0,
        "notifications": {"enabled": False, "url": "http://127.0.0.1:8097", "topic": "portal",
                          "username": "", "notifyApprovals": True, "approvalLink": "",
                          "scheduledTasks": "off"},
        "notes": {"enabled": False, "vaultPath": "", "write": "ask"},
        "mcp": {"servers": []},
        "apps": default_apps(),
    }


def upgrade_config(config):
    """Settings saved by an earlier version, with the fields added since."""
    config["notifications"].setdefault("approvalLink", "")
    config["notifications"].setdefault("scheduledTasks", "off")
    config.setdefault("apps", default_apps())
    for server in config["mcp"]["servers"]:
        server.setdefault("workingFolder", "")
        server.setdefault("envNames", [])
        for tool in server["tools"]:
            tool.setdefault("new", False)
    return config


def app_context(config, app, key):
    """Private credential/destination pairing, committed with the public settings."""
    return digest("app-admission-v1", app, config["apps"][app], key)


def notification_context(config, secrets):
    """Private cohesion marker for the ntfy destination/account and password."""
    return digest("ntfy-admission-v1", config["notifications"], secrets.get("ntfyPassword", ""))


def watch_context(config, key):
    """Private history identity; never reuse another endpoint/account's watch IDs."""
    epoch = config.get("approvalBindings", {}).get("apps", {}).get("watch")
    return digest("watch-provider-v2", config["apps"]["watch"]["url"], key, epoch)


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


class Store:
    """Settings, secrets, approvals and the activity log, each an owner-only file."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.lock = threading.RLock()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _read(self, name, default):
        try:
            return json.loads((self.root / name).read_text())
        except FileNotFoundError:
            return default
        except ValueError:
            raise IntegrationError("state-damaged", f"The integrations file {self.root / name} is damaged. "
                                   "Restore it from a backup, or remove it to start that part over.") from None

    def _write(self, name, value):
        path = self.root / name
        temporary = path.with_name(f".{name}.{uuid.uuid4().hex}")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=1, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Approval consumption is committed before the external attempt. The
        # rename must be durable too; a failed sync must not qualify an effect.
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def config(self):
        value = self._read("config.json", None)
        return upgrade_config(value) if value is not None else default_config()

    def save_config(self, value):
        self._write("config.json", value)

    def secrets(self):
        return {"ntfyPassword": "", "mcp": {}, "mcpEnv": {}, "apps": {}, **self._read("secrets.json", {})}

    def save_secrets(self, value):
        self._write("secrets.json", value)

    def approvals(self):
        current = now()
        return [item for item in self._read("approvals.json", []) if item["expiresAt"] > current]

    def save_approvals(self, value):
        self._write("approvals.json", value[-200:])

    def _watch_document(self):
        value = self._read("watch-state.json", {})
        if value == {}:
            return {"schemaVersion": 2, "providers": {}}
        if (not isinstance(value, dict) or set(value) != {"schemaVersion", "providers"}
                or value["schemaVersion"] != 2 or not isinstance(value["providers"], dict)
                or len(value["providers"]) > 32
                or any(not isinstance(key, str) or len(key) != 64
                       or any(character not in "0123456789abcdef" for character in key)
                       or not isinstance(state, dict)
                       or any(not isinstance(identity, str) or not isinstance(entry, dict)
                              or set(entry) != {"lastChanged", "below", "lastPrice"}
                              or any(number is not None and not _finite_number(number) for number in entry.values())
                              for identity, entry in state.items())
                       for key, state in value["providers"].items())):
            raise IntegrationError("watch-state-unbound", "Saved watch history has no verified provider identity. "
                                   "Keep watch-state.json for diagnosis; it was not reset.")
        return value

    def _watch_context(self):
        config = self.config()
        return watch_context(config, self.secrets()["apps"].get("watch", ""))

    def watch_state(self, context=None):
        context = context or self._watch_context()
        providers = self._watch_document()["providers"]
        if context not in providers and len(providers) >= 32:
            raise IntegrationError("watch-state-full", "Saved watch history contains too many provider identities. "
                                   "Keep watch-state.json for diagnosis; no history was removed.")
        return providers.get(context, {})

    def save_watch_state(self, value, context=None):
        context = context or self._watch_context()
        document = self._watch_document()
        if context not in document["providers"] and len(document["providers"]) >= 32:
            raise IntegrationError("watch-state-full", "Saved watch history contains too many provider identities. "
                                   "Keep watch-state.json for diagnosis; no history was removed.")
        document["providers"][context] = value
        self._write("watch-state.json", document)

    def digest_sent(self):
        return self._read("digest-sent.json", [])

    def save_digest_sent(self, value):
        self._write("digest-sent.json", value)

    def log(self, action, target, outcome):
        path = self.root / "activity.jsonl"
        entry = json.dumps({"at": now(), "action": action, "target": target[:200], "outcome": outcome[:200]})
        lines = path.read_text().splitlines() if path.exists() else []
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join((lines + [entry])[-ACTIVITY_LINES:]) + "\n")

    def clear_activity(self):
        fd = os.open(self.root / "activity.jsonl", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)

    def activity(self, limit=50):
        path = self.root / "activity.jsonl"
        if not path.exists():
            return []
        entries = []
        for line in path.read_text().splitlines()[-limit:]:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue  # A line cut short by a crash; the rest of the log is still useful.
        return entries[::-1]


if __name__ == "__main__":
    if sys.argv[1:] == ["--http-worker"]:
        _http_worker()
    elif len(sys.argv) == 3 and sys.argv[1] == '--prepare-native-state':
        prepare_native_state(sys.argv[2])
        print('{"schemaVersion":1,"status":"prepared"}')
    else:
        raise SystemExit(2)
