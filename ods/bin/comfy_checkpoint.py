"""Explicit, pinned ComfyUI checkpoint download owned by the ODS host agent.

The default install and Extensions Library Add do not call this module. Only a
separate user-confirmed request starts the large transfer. Python 3.9 stdlib.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional
from urllib import error as urllib_error, request as urllib_request
from urllib.parse import urlparse

try:
    import fcntl
except ImportError:  # Native Windows host agents cannot manage WSL files.
    fcntl = None


MODEL_ID = "sdxl_lightning_4step"
REVISION = "c9a24f48e1c025556787b0c58dd67a091ece2e44"
FILENAME = "sdxl_lightning_4step.safetensors"
SIZE_BYTES = 6_938_040_682
SHA256 = "e0d996ee0013e79d9d3561f50fcafb9a17e3ff07b780358e3b66d67932c4d490"
URL = "https://huggingface.co/ByteDance/SDXL-Lightning/resolve/{}/{}".format(REVISION, FILENAME)
_CHUNK = 1024 * 1024
_STATUS_INTERVAL = 1.0
_TRANSFER_DEADLINE = 4 * 60 * 60
_RESERVE_BYTES = 512 * 1024 * 1024
_ALLOWED_REDIRECT_HOSTS = frozenset({
    "huggingface.co", "cas-bridge.xethub.hf.co",
    "cas-server.xethub.hf.co", "cas-server.xethub-eu.hf.co",
    "transfer.xethub.hf.co", "transfer.xethub-eu.hf.co",
    "us.aws.cdn.hf.co", "us.gcp.cdn.hf.co",
    "cdn-lfs-us-1.hf.co", "cdn-lfs-eu-1.hf.co",
})
_TARGET_PARTS = {
    "nvidia": ("data", "comfyui", "models", "checkpoints"),
    "amd": ("data", "comfyui", "ComfyUI", "models", "checkpoints"),
}


class CheckpointError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


class _Cancelled(Exception):
    pass


class _PinnedRedirect(urllib_request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_REDIRECT_HOSTS:
            raise CheckpointError("redirect_rejected", 502)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def catalog() -> dict:
    return {
        "model_id": MODEL_ID, "name": "SDXL Lightning 4-step",
        "filename": FILENAME, "size_bytes": SIZE_BYTES,
        "sha256": SHA256, "revision": REVISION,
        "size_label": "6.94 GB", "requires_confirmation": True,
    }


def _supported_host() -> bool:
    return fcntl is not None and hasattr(os, "O_NOFOLLOW") and hasattr(os, "geteuid")


def _file_identity(info: os.stat_result) -> dict:
    return {"dev": info.st_dev, "ino": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def _open_dir_chain(root: Path, parts: tuple) -> int:
    """Hold each fixed directory hop so later container-side renames cannot redirect I/O."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(str(root), flags)
    try:
        for part in parts:
            child_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _existing_dir_no_symlink(root: Path, parts: tuple) -> Path:
    """Resolve only the ODS-owned fixed path, rejecting every symlink hop."""
    current = root
    if current.is_symlink() or not current.is_dir():
        raise CheckpointError("unsafe_install_root")
    for part in parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise CheckpointError("checkpoint_directory_missing") from exc
        if not stat.S_ISDIR(mode):
            raise CheckpointError("unsafe_checkpoint_directory")
    return current


def _private_stage(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise CheckpointError("unsafe_install_root")
    stage = root / ".ods-comfy-checkpoint"
    try:
        stage.mkdir(mode=0o700)
    except FileExistsError:
        pass
    mode = stage.lstat().st_mode
    if not stat.S_ISDIR(mode) or stage.stat().st_uid != os.geteuid() or mode & 0o077:
        raise CheckpointError("unsafe_checkpoint_stage")
    return stage


def _atomic_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path: Path) -> dict:
    try:
        if path.is_symlink():
            raise CheckpointError("unsafe_checkpoint_status")
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise CheckpointError("invalid_checkpoint_status") from exc


class CheckpointManager:
    """One checkpoint job; independent of the chat GGUF download lifecycle."""

    def __init__(self, install_dir: Path, gpu_backend: str, *, _opener=None):
        self.root = Path(install_dir)
        self.backend = gpu_backend
        self._opener = _opener or urllib_request.build_opener(_PinnedRedirect())
        self._mutex = threading.RLock()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def _target(self) -> Path:
        if self.backend not in _TARGET_PARTS:
            raise CheckpointError("unsupported_gpu_backend")
        return _existing_dir_no_symlink(self.root, _TARGET_PARTS[self.backend])

    def _target_fd(self) -> int:
        if self.backend not in _TARGET_PARTS:
            raise CheckpointError("unsupported_gpu_backend")
        return _open_dir_chain(self.root, _TARGET_PARTS[self.backend])

    def _stage(self) -> Path:
        return _private_stage(self.root)

    def _status_file(self, stage: Path) -> Path:
        return stage / "status.json"

    def _write(self, stage: Path, state: str, bytes_done: int,
               error: Optional[str] = None, verified_file: Optional[dict] = None) -> dict:
        value = {
            "state": state, "model_id": MODEL_ID, "bytes_done": bytes_done,
            "bytes_total": SIZE_BYTES, "sha256_expected": SHA256,
            "resumable": state in ("cancelled", "error", "interrupted") and bytes_done > 0,
            "error": error, "updated_at": int(time.time()),
        }
        if verified_file is not None:
            value["verified_file"] = verified_file
        with self._mutex:
            _atomic_json(self._status_file(stage), value)
        return value

    def status(self) -> dict:
        if not _supported_host():
            return {"state": "unsupported", "model_id": MODEL_ID, "bytes_done": 0,
                    "bytes_total": SIZE_BYTES, "sha256_expected": SHA256,
                    "resumable": False, "error": "unsupported_checkpoint_host"}
        stage = self.root / ".ods-comfy-checkpoint"
        if not stage.exists():
            if self.backend in _TARGET_PARTS:
                try:
                    target_fd = self._target_fd()
                    try:
                        info = os.stat(FILENAME, dir_fd=target_fd, follow_symlinks=False)
                    finally:
                        os.close(target_fd)
                    if stat.S_ISREG(info.st_mode):
                        return {"state": "existing_unverified", "model_id": MODEL_ID,
                                "bytes_done": info.st_size, "bytes_total": SIZE_BYTES,
                                "sha256_expected": SHA256, "resumable": False, "error": None}
                except (CheckpointError, OSError):
                    pass
            return {"state": "idle", "model_id": MODEL_ID, "bytes_done": 0,
                    "bytes_total": SIZE_BYTES, "sha256_expected": SHA256,
                    "resumable": False, "error": None}
        stage = self._stage()
        with self._mutex:
            value = _read_json(self._status_file(stage))
            if not value:
                return {"state": "idle", "model_id": MODEL_ID, "bytes_done": 0,
                        "bytes_total": SIZE_BYTES, "sha256_expected": SHA256,
                        "resumable": False, "error": None}
            if value.get("state") in ("downloading", "verifying") and not self._running:
                value = self._write(stage, "interrupted", int(value.get("bytes_done") or 0), "interrupted")
            if value.get("state") == "done":
                try:
                    target_fd = self._target_fd()
                    try:
                        info = os.stat(FILENAME, dir_fd=target_fd, follow_symlinks=False)
                    finally:
                        os.close(target_fd)
                    if (not stat.S_ISREG(info.st_mode)
                            or value.get("verified_file") != _file_identity(info)):
                        value = self._write(stage, "error", 0, "checkpoint_missing_or_changed")
                except (CheckpointError, OSError):
                    value = self._write(stage, "error", 0, "checkpoint_missing_or_changed")
            return value

    def _acquire(self) -> int:
        if not _supported_host():
            raise CheckpointError("unsupported_checkpoint_host")
        lock_path = self.root / ".sdxl-checkpoint.lock"
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
        fd = None
        try:
            fd = os.open(str(lock_path), flags, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise CheckpointError("unsafe_checkpoint_lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError as exc:
            if fd is not None:
                os.close(fd)
            raise CheckpointError("checkpoint_busy") from exc
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            raise CheckpointError("checkpoint_lock_unavailable", 503) from exc

    def start(self, model_id: str, acknowledge_size_bytes: int) -> dict:
        if type(model_id) is not str or model_id != MODEL_ID or (
                type(acknowledge_size_bytes) is not int or acknowledge_size_bytes != SIZE_BYTES):
            raise CheckpointError("checkpoint_confirmation_required", 400)
        if not _supported_host():
            raise CheckpointError("unsupported_checkpoint_host")
        self._target()
        stage = self._stage()
        target_fd = self._target_fd()
        try:
            target_device = os.fstat(target_fd).st_dev
        finally:
            os.close(target_fd)
        if target_device != stage.stat().st_dev:
            raise CheckpointError("checkpoint_cross_device")
        with self._mutex:
            if self._running:
                raise CheckpointError("checkpoint_busy")
            fd = self._acquire()
            self._cancel.clear()
            part = stage / (FILENAME + ".part")
            try:
                bytes_done = part.stat().st_size if part.is_file() and not part.is_symlink() else 0
                result = self._write(stage, "downloading", bytes_done)
                self._running = True
                self._thread = threading.Thread(target=self._run, args=(stage, fd), daemon=True)
                self._thread.start()
            except (OSError, RuntimeError):
                self._running = False
                os.close(fd)
                raise CheckpointError("checkpoint_worker_unavailable", 503)
            return result

    def cancel(self) -> dict:
        with self._mutex:
            if not self._running:
                raise CheckpointError("checkpoint_not_running")
            self._cancel.set()
            stage = self._stage()
            result = _read_json(self._status_file(stage)) or self._write(stage, "downloading", 0)
            return {**result, "state": "cancelling"}

    def _run(self, stage: Path, lock_fd: int) -> None:
        part = stage / (FILENAME + ".part")
        meta = stage / (FILENAME + ".part.json")
        target_fd = None
        try:
            target_fd = self._target_fd()
            try:
                existing = os.stat(FILENAME, dir_fd=target_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if not stat.S_ISREG(existing.st_mode):
                    raise CheckpointError("unsafe_existing_checkpoint")
                try:
                    identity = self._verify(Path(FILENAME), stage, dir_fd=target_fd)
                except CheckpointError as exc:
                    if exc.code in ("checkpoint_hash_mismatch", "checkpoint_size_mismatch"):
                        raise CheckpointError("existing_checkpoint_invalid") from exc
                    raise
                if _file_identity(os.stat(
                        FILENAME, dir_fd=target_fd, follow_symlinks=False)) != identity:
                    raise CheckpointError("checkpoint_changed_during_verification")
                part.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
                self._write(stage, "done", SIZE_BYTES, verified_file=identity)
                return
            expected_meta = {"revision": REVISION, "size_bytes": SIZE_BYTES, "sha256": SHA256}
            previous_meta = _read_json(meta)
            if previous_meta != expected_meta:
                if part.exists() or part.is_symlink():
                    if part.is_symlink() or not part.is_file():
                        raise CheckpointError("unsafe_checkpoint_partial")
                    part.unlink()
                _atomic_json(meta, expected_meta)
            if part.is_symlink() or (part.exists() and not part.is_file()):
                raise CheckpointError("unsafe_checkpoint_partial")
            offset = part.stat().st_size if part.exists() else 0
            if offset > SIZE_BYTES:
                part.unlink()
                offset = 0
            if offset < SIZE_BYTES:
                remaining = SIZE_BYTES - offset
                if shutil.disk_usage(stage).free < remaining + _RESERVE_BYTES:
                    raise CheckpointError("insufficient_checkpoint_space")
                self._transfer(part, stage, offset)
            verified_identity = self._verify(part, stage)
            if self._cancel.is_set():
                raise _Cancelled()
            if _file_identity(part.lstat()) != verified_identity:
                raise CheckpointError("checkpoint_changed_during_verification")
            os.chmod(part, 0o644)
            source_fd = os.open(str(stage), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.link(part.name, FILENAME, src_dir_fd=source_fd,
                        dst_dir_fd=target_fd, follow_symlinks=False)
                os.unlink(part.name, dir_fd=source_fd)
            finally:
                os.close(source_fd)
            meta.unlink(missing_ok=True)
            self._write(stage, "done", SIZE_BYTES,
                        verified_file=_file_identity(os.stat(
                            FILENAME, dir_fd=target_fd, follow_symlinks=False)))
        except _Cancelled:
            size = part.stat().st_size if part.is_file() and not part.is_symlink() else 0
            self._write(stage, "cancelled", size)
        except CheckpointError as exc:
            if exc.code in ("checkpoint_hash_mismatch", "checkpoint_size_mismatch"):
                part.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
            size = part.stat().st_size if part.is_file() and not part.is_symlink() else 0
            self._write(stage, "error", size, exc.code)
        except (OSError, urllib_error.URLError, ValueError):
            size = part.stat().st_size if part.is_file() and not part.is_symlink() else 0
            self._write(stage, "error", size, "checkpoint_io_error")
        finally:
            if target_fd is not None:
                os.close(target_fd)
            try:
                os.close(lock_fd)
            except OSError:
                pass
            with self._mutex:
                self._running = False

    def _transfer(self, part: Path, stage: Path, offset: int) -> None:
        deadline = time.monotonic() + _TRANSFER_DEADLINE
        headers = {"Range": "bytes={}-".format(offset)} if offset else {}
        request = urllib_request.Request(URL, headers=headers)
        try:
            response = self._opener.open(request, timeout=45)
        except urllib_error.HTTPError as exc:
            raise CheckpointError("checkpoint_upstream_http", 502) from exc
        except urllib_error.URLError as exc:
            raise CheckpointError("checkpoint_network_unavailable", 502) from exc
        with response:
            code = response.getcode()
            if code == 206:
                content_range = response.headers.get("Content-Range", "")
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                if (match is None or int(match.group(1)) != offset
                        or int(match.group(2)) != SIZE_BYTES - 1
                        or int(match.group(3)) != SIZE_BYTES):
                    raise CheckpointError("checkpoint_range_mismatch", 502)
            elif offset and code == 200:
                offset = 0
            elif code != 200:
                raise CheckpointError("checkpoint_upstream_http", 502)
            mode = "ab" if offset else "wb"
            flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
            flags |= os.O_APPEND if offset else os.O_TRUNC
            fd = os.open(str(part), flags, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise CheckpointError("unsafe_checkpoint_partial")
            done = offset
            last_write = 0.0
            with os.fdopen(fd, mode) as stream:
                while True:
                    if self._cancel.is_set():
                        raise _Cancelled()
                    if time.monotonic() >= deadline:
                        raise CheckpointError("checkpoint_transfer_timeout", 504)
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > SIZE_BYTES:
                        raise CheckpointError("checkpoint_size_mismatch", 502)
                    stream.write(chunk)
                    now = time.monotonic()
                    if now - last_write >= _STATUS_INTERVAL:
                        self._write(stage, "downloading", done)
                        last_write = now
                stream.flush()
                os.fsync(stream.fileno())
            self._write(stage, "downloading", done)

    def _verify(self, path: Path, stage: Path, *, dir_fd: Optional[int] = None) -> dict:
        self._write(stage, "verifying", SIZE_BYTES)
        digest = hashlib.sha256()
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(str(path), flags, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != SIZE_BYTES:
                raise CheckpointError("checkpoint_size_mismatch")
            for chunk in iter(lambda: stream.read(4 * _CHUNK), b""):
                if self._cancel.is_set():
                    raise _Cancelled()
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        if _file_identity(before) != _file_identity(after):
            raise CheckpointError("checkpoint_changed_during_verification")
        if digest.hexdigest() != SHA256:
            raise CheckpointError("checkpoint_hash_mismatch")
        return _file_identity(after)
