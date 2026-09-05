#!/usr/bin/env python3
"""Snapshot and serve bounded static sites from Pixel's owner workspace.

The control socket accepts only one relative workspace directory. Every
regular file is reopened without symlink traversal, bounded, hashed, and copied
create-only into a private state directory. A separate loopback HTTP listener
serves only those immutable snapshots with browser-hardening headers.
"""

from __future__ import annotations

import hashlib
import http.client
import http.server
import json
import mimetypes
import os
import pathlib
import pwd
import re
import shutil
import socket
import socketserver
import stat
import struct
import sys
import tempfile
import threading
import urllib.parse
from typing import Any


SCHEMA_VERSION = 1
KIND = "ods-pixel-workspace-preview"
SOCKET_PATH = pathlib.Path("/run/ods-pixel-preview/control.sock")
HTTP_SOCKET_PATH = pathlib.Path("/run/ods-pixel-preview/http.sock")
PATH_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SITE_ID = re.compile(r"site-[a-f0-9]{24}")
ALLOWED_SUFFIXES = frozenset(
    {
        ".html", ".htm", ".css", ".js", ".mjs", ".json", ".svg",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
        ".woff", ".woff2", ".ttf", ".txt", ".map",
    }
)
MAX_REQUEST_BYTES = 2048
MAX_RESPONSE_BYTES = 8192
MAX_FILES = 128
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
BOUNDARY = (
    "Create-only static-site snapshot from the configured Pixel workspace to a "
    "dedicated loopback preview origin; no arbitrary host path, network "
    "destination, server process, overwrite, or execution authority."
)
CSP = (
    "default-src 'self' data: blob:; connect-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self'; font-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors http://localhost:* http://127.0.0.1:*"
)


class PreviewError(Exception):
    """A generic fail-closed preview error."""


def _parts(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 512
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PreviewError("invalid preview directory")
    parts = tuple(value.split("/"))
    if (
        not 1 <= len(parts) <= 12
        or any(
            part in {"", ".", ".."} or PATH_COMPONENT.fullmatch(part) is None
            for part in parts
        )
    ):
        raise PreviewError("invalid preview directory")
    return parts


def parse_request(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreviewError("invalid preview request") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "action", "relativeDirectory"}
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("action") != "publish"
    ):
        raise PreviewError("invalid preview request")
    _parts(value.get("relativeDirectory"))
    return value


def _safe_root(path: pathlib.Path, owner_uid: int) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != owner_uid
        or info.st_mode & 0o022
        or path.resolve(strict=True) != path
    ):
        raise PreviewError("unsafe preview root")


def _source_files(
    workspace: pathlib.Path, relative_directory: str, owner_uid: int
) -> list[tuple[str, pathlib.Path, os.stat_result]]:
    _safe_root(workspace, owner_uid)
    current = workspace
    for component in _parts(relative_directory):
        current = current / component
        info = current.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != owner_uid
            or info.st_mode & 0o022
        ):
            raise PreviewError("unsafe preview directory")
    if current.resolve(strict=True) != current:
        raise PreviewError("unsafe preview directory")

    files: list[tuple[str, pathlib.Path, os.stat_result]] = []
    for root, directories, names in os.walk(current, topdown=True, followlinks=False):
        root_path = pathlib.Path(root)
        root_info = root_path.lstat()
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or stat.S_ISLNK(root_info.st_mode)
            or root_info.st_uid != owner_uid
            or root_info.st_mode & 0o022
        ):
            raise PreviewError("unsafe preview directory")
        for directory in directories:
            info = (root_path / directory).lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != owner_uid
                or info.st_mode & 0o022
                or PATH_COMPONENT.fullmatch(directory) is None
            ):
                raise PreviewError("unsafe preview directory")
        for name in names:
            source = root_path / name
            info = source.lstat()
            relative = source.relative_to(current).as_posix()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != owner_uid
                or info.st_mode & 0o022
                or not 1 <= info.st_size <= MAX_FILE_BYTES
                or pathlib.PurePosixPath(relative).suffix.lower() not in ALLOWED_SUFFIXES
                or any(PATH_COMPONENT.fullmatch(part) is None for part in relative.split("/"))
            ):
                raise PreviewError("unsafe preview file")
            files.append((relative, source, info))
            if len(files) > MAX_FILES:
                raise PreviewError("preview contains too many files")
    files.sort(key=lambda item: item[0])
    if not files or not any(relative == "index.html" for relative, _, _ in files):
        raise PreviewError("preview requires index.html")
    if sum(info.st_size for _, _, info in files) > MAX_TOTAL_BYTES:
        raise PreviewError("preview is too large")
    return files


def _read_stable(source: pathlib.Path, expected: os.stat_result) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(source, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)
        ):
            raise PreviewError("preview source changed")
        data = bytearray()
        while len(data) <= MAX_FILE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(data) != expected.st_size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        ):
            raise PreviewError("preview source changed")
        return bytes(data)
    finally:
        os.close(descriptor)


def publish_snapshot(
    workspace: pathlib.Path,
    previews: pathlib.Path,
    relative_directory: str,
    owner_uid: int,
) -> dict[str, Any]:
    _safe_root(previews, owner_uid)
    sources = _source_files(workspace, relative_directory, owner_uid)
    captured: list[tuple[str, bytes]] = []
    digest = hashlib.sha256()
    total = 0
    for relative, source, info in sources:
        data = _read_stable(source, info)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise PreviewError("preview is too large")
        encoded_name = relative.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        captured.append((relative, data))
    full_digest = digest.hexdigest()
    site_id = f"site-{full_digest[:24]}"
    destination = previews / site_id
    overwritten = False
    if not destination.exists():
        temporary = pathlib.Path(tempfile.mkdtemp(prefix=".publish-", dir=previews))
        try:
            os.chmod(temporary, 0o700)
            for relative, data in captured:
                target = temporary / relative
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o400,
                )
                try:
                    view = memoryview(data)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise PreviewError("incomplete preview write")
                        view = view[written:]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            os.rename(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    destination_info = destination.lstat()
    if (
        not stat.S_ISDIR(destination_info.st_mode)
        or stat.S_ISLNK(destination_info.st_mode)
        or destination_info.st_uid != owner_uid
        or stat.S_IMODE(destination_info.st_mode) != 0o700
    ):
        raise PreviewError("unsafe preview snapshot")
    expected = {relative: data for relative, data in captured}
    observed: set[str] = set()
    for root, directories, names in os.walk(destination, topdown=True, followlinks=False):
        root_path = pathlib.Path(root)
        root_info = root_path.lstat()
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or stat.S_ISLNK(root_info.st_mode)
            or root_info.st_uid != owner_uid
            or stat.S_IMODE(root_info.st_mode) != 0o700
        ):
            raise PreviewError("unsafe preview snapshot")
        for directory in directories:
            directory_info = (root_path / directory).lstat()
            if (
                not stat.S_ISDIR(directory_info.st_mode)
                or stat.S_ISLNK(directory_info.st_mode)
                or directory_info.st_uid != owner_uid
                or stat.S_IMODE(directory_info.st_mode) != 0o700
            ):
                raise PreviewError("unsafe preview snapshot")
        for name in names:
            target = root_path / name
            relative = target.relative_to(destination).as_posix()
            target_info = target.lstat()
            if (
                relative not in expected
                or not stat.S_ISREG(target_info.st_mode)
                or stat.S_ISLNK(target_info.st_mode)
                or target_info.st_nlink != 1
                or target_info.st_uid != owner_uid
                or stat.S_IMODE(target_info.st_mode) != 0o400
                or _read_stable(target, target_info) != expected[relative]
            ):
                raise PreviewError("preview snapshot verification failed")
            observed.add(relative)
    if observed != set(expected):
        raise PreviewError("preview snapshot verification failed")
    entry_data = expected["index.html"]
    entry_sha256 = hashlib.sha256(entry_data).hexdigest()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "status": "succeeded",
        "relativeDirectory": relative_directory,
        "siteId": site_id,
        "files": len(captured),
        "bytes": total,
        "sha256": full_digest,
        "entryFile": "index.html",
        "entrySha256": entry_sha256,
        "executable": False,
        "overwritten": overwritten,
        "boundary": BOUNDARY,
    }


class PreviewHandler(http.server.BaseHTTPRequestHandler):
    server_version = "ODSPreview"
    sys_version = ""

    def _target(self) -> tuple[pathlib.Path, bytes] | None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.query or parsed.fragment:
            return None
        try:
            decoded = urllib.parse.unquote(parsed.path, errors="strict")
        except UnicodeDecodeError:
            return None
        parts = decoded.lstrip("/").split("/")
        if len(parts) < 1 or SITE_ID.fullmatch(parts[0]) is None:
            return None
        site_id = parts[0]
        if self.server.internal_proxy:  # type: ignore[attr-defined]
            expected_host = "pixel-preview.internal"
        else:
            expected_host = (
                f"{site_id}.localhost:{self.server.preview_port}"  # type: ignore[attr-defined]
            )
        if self.headers.get("Host", "").lower() != expected_host:
            return None
        if parts[-1] == "":
            parts[-1] = "index.html"
        if any(PATH_COMPONENT.fullmatch(part) is None for part in parts[1:]):
            return None
        target = self.server.preview_root.joinpath(*parts)  # type: ignore[attr-defined]
        try:
            info = target.lstat()
            root = self.server.preview_root.resolve(strict=True)  # type: ignore[attr-defined]
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > MAX_FILE_BYTES
                or target.resolve(strict=True).is_relative_to(root) is False
            ):
                return None
            return target, target.read_bytes()
        except (FileNotFoundError, OSError, ValueError):
            return None

    def _send(self, include_body: bool) -> None:
        result = self._target()
        if result is None:
            self.send_error(404)
            return
        target, body = result
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        # The ODS dashboard and preview service intentionally use separate
        # loopback ports. CSP restricts which local parents may frame a site;
        # CORP must therefore permit that cross-origin iframe navigation.
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Preview-SHA256", hashlib.sha256(body).hexdigest())
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._send(True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._send(False)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class PreviewHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], preview_root: pathlib.Path):
        self.preview_root = preview_root
        self.internal_proxy = False
        super().__init__(address, PreviewHandler)
        self.preview_port = self.server_address[1]


class PreviewUnixHTTPServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(
        self, address: str, preview_root: pathlib.Path, preview_port: int
    ):
        self.preview_root = preview_root
        self.preview_port = preview_port
        self.internal_proxy = True
        super().__init__(address, PreviewHandler)


def _verify_http(port: int, site_id: str, entry_sha256: str) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "GET",
            f"/{site_id}/",
            headers={"Host": f"{site_id}.localhost:{port}"},
        )
        response = connection.getresponse()
        body = response.read(MAX_FILE_BYTES + 1)
        if (
            response.status != 200
            or len(body) > MAX_FILE_BYTES
            or hashlib.sha256(body).hexdigest() != entry_sha256
            or response.getheader("X-Preview-SHA256") != entry_sha256
        ):
            raise PreviewError("preview HTTP readback failed")
    finally:
        connection.close()


def _error_result() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "status": "failed",
        "error": "ODS workspace preview publication failed",
        "boundary": BOUNDARY,
    }


def _serve_connection(
    connection: socket.socket,
    *,
    workspace: pathlib.Path,
    previews: pathlib.Path,
    owner_uid: int,
    port: int,
) -> None:
    response: dict[str, Any]
    try:
        peer = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", peer)
        if uid != owner_uid:
            raise PreviewError("unauthorized preview peer")
        connection.settimeout(10)
        payload = bytearray()
        while len(payload) <= MAX_REQUEST_BYTES:
            piece = connection.recv(min(1024, MAX_REQUEST_BYTES + 1 - len(payload)))
            if not piece:
                break
            payload.extend(piece)
            if b"\n" in piece:
                break
        if b"\n" not in payload or payload.count(b"\n") != 1 or not payload.endswith(b"\n"):
            raise PreviewError("invalid preview framing")
        raw = bytes(payload[:-1])
        if raw == b'{"schemaVersion":1,"action":"health"}':
            response = {
                "schemaVersion": SCHEMA_VERSION,
                "kind": KIND,
                "status": "ok",
                "port": port,
                "boundary": BOUNDARY,
            }
        else:
            request = parse_request(raw)
            response = publish_snapshot(
                workspace,
                previews,
                request["relativeDirectory"],
                owner_uid,
            )
            _verify_http(port, response["siteId"], response["entrySha256"])
            response.update(
                {
                    "port": port,
                    "url": (
                        f"http://{response['siteId']}.localhost:{port}/"
                        f"{response['siteId']}/"
                    ),
                    "httpStatus": 200,
                    "readbackVerified": True,
                }
            )
    except (PreviewError, OSError, ValueError, TypeError, KeyError):
        response = _error_result()
    encoded = (json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = (json.dumps(_error_result(), sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        connection.sendall(encoded)
    except OSError:
        return


def serve(
    socket_path: pathlib.Path,
    workspace: pathlib.Path,
    previews: pathlib.Path,
    owner: str,
    port: int,
) -> int:
    if (
        socket_path != SOCKET_PATH
        or not workspace.is_absolute()
        or workspace == pathlib.Path("/")
        or not previews.is_absolute()
        or previews == pathlib.Path("/")
        or not 1 <= port <= 65535
        or re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", owner) is None
    ):
        raise PreviewError("invalid preview service configuration")
    owner_uid = pwd.getpwnam(owner).pw_uid
    _safe_root(workspace, owner_uid)
    previews.mkdir(mode=0o700, parents=True, exist_ok=True)
    _safe_root(previews, owner_uid)
    if socket_path.exists() or socket_path.is_symlink():
        info = socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != owner_uid:
            raise PreviewError("unsafe existing preview socket")
        socket_path.unlink()
    if HTTP_SOCKET_PATH.exists() or HTTP_SOCKET_PATH.is_symlink():
        info = HTTP_SOCKET_PATH.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != owner_uid:
            raise PreviewError("unsafe existing preview HTTP socket")
        HTTP_SOCKET_PATH.unlink()

    httpd = PreviewHTTPServer(("127.0.0.1", port), previews)
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()
    unix_httpd = PreviewUnixHTTPServer(str(HTTP_SOCKET_PATH), previews, port)
    os.chmod(HTTP_SOCKET_PATH, 0o660)
    unix_http_thread = threading.Thread(target=unix_httpd.serve_forever, daemon=True)
    unix_http_thread.start()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        listener.listen(4)
        while True:
            connection, _address = listener.accept()
            with connection:
                _serve_connection(
                    connection,
                    workspace=workspace,
                    previews=previews,
                    owner_uid=owner_uid,
                    port=port,
                )
    finally:
        listener.close()
        httpd.shutdown()
        httpd.server_close()
        unix_httpd.shutdown()
        unix_httpd.server_close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        try:
            HTTP_SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass


def client(socket_path: pathlib.Path, payload: dict[str, Any]) -> dict[str, Any]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(15)
    try:
        connection.connect(str(socket_path))
        connection.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        chunks = bytearray()
        while len(chunks) <= MAX_RESPONSE_BYTES:
            piece = connection.recv(min(4096, MAX_RESPONSE_BYTES + 1 - len(chunks)))
            if not piece:
                break
            chunks.extend(piece)
    finally:
        connection.close()
    if len(chunks) > MAX_RESPONSE_BYTES or chunks.count(b"\n") != 1 or not chunks.endswith(b"\n"):
        raise PreviewError("invalid preview response")
    value = json.loads(bytes(chunks[:-1]).decode("utf-8"))
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise PreviewError("invalid preview response")
    return value


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 7 and argv[1] == "serve":
            return serve(
                pathlib.Path(argv[2]),
                pathlib.Path(argv[3]),
                pathlib.Path(argv[4]),
                argv[5],
                int(argv[6]),
            )
        if len(argv) == 3 and argv[1] == "health":
            value = client(
                pathlib.Path(argv[2]),
                {"schemaVersion": SCHEMA_VERSION, "action": "health"},
            )
            if (
                value.get("kind") != KIND
                or value.get("status") != "ok"
                or value.get("boundary") != BOUNDARY
            ):
                raise PreviewError("invalid preview health response")
            sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
            return 0
        raise PreviewError("invalid preview command")
    except (PreviewError, OSError, ValueError, KeyError, json.JSONDecodeError):
        sys.stderr.write("ODS workspace preview failed\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
