"""Native background model downloader and storage provider for Windows Desktop.

Replaces the containerized host agent daemon in WINDOWS_DESKTOP_PROFILE mode.
Provides:
1. Cancellable background downloads using system curl.exe (with resumable -C -).
2. Live atomic status writing to model-download-status.json for useDownloadProgress.js.
3. SHA-256 sidecar stamp generation upon successful download.
4. Direct disk space checking via shutil.disk_usage.
5. Direct local model deletion.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any

from config import DATA_DIR, INSTALL_DIR

logger = logging.getLogger(__name__)

_active_download_lock = threading.Lock()
_active_download_thread: threading.Thread | None = None
_active_download_proc: subprocess.Popen | None = None
_active_download_cancel = threading.Event()
_active_download_info: dict[str, Any] = {}


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def get_models_directory() -> Path:
    """Return the designated models directory on Windows Desktop."""
    env_dir = os.environ.get("ODS_MODELS_DIR", "").strip()
    if env_dir:
        p = Path(env_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p
    if DATA_DIR:
        p = Path(DATA_DIR) / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p
    p = Path(INSTALL_DIR) / "data" / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_status_path() -> Path:
    base = Path(DATA_DIR) if DATA_DIR else (Path(INSTALL_DIR) / "data")
    base.mkdir(parents=True, exist_ok=True)
    return base / "model-download-status.json"


def get_download_status() -> dict[str, Any]:
    """Read the current model download and storage status."""
    status_file = get_status_path()
    if status_file.exists():
        try:
            return json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    fallback = Path(DATA_DIR) / "model-download-status.json" if DATA_DIR else None
    if fallback and fallback.exists() and fallback != status_file:
        try:
            return json.loads(fallback.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "status": "idle",
        "active": False,
        "isDownloading": False,
        "lifecycleActive": False,
        "activeOperation": None,
        "activeTarget": None,
        "modelTransactionPending": False,
    }


def write_model_download_status(
    status: str,
    model: str,
    downloaded: int = 0,
    total: int = 0,
    error: str = "",
    speed_bytes_sec: int = 0,
    eta_seconds: int | None = None,
) -> None:
    """Atomically record download status for useDownloadProgress.js."""
    data = {
        "status": status,
        "model": model,
        "bytesDownloaded": int(downloaded),
        "bytesTotal": int(total),
        "percent": round((downloaded / total) * 100, 1) if total > 0 else (100.0 if status in {"verifying", "complete"} else 0.0),
        "speedBytesPerSec": int(speed_bytes_sec),
        "eta": eta_seconds,
        "updatedAt": _iso_now(),
        "active": status in {"starting", "downloading", "verifying"},
        "isDownloading": status == "downloading",
    }
    if error:
        data["error"] = error

    status_file = get_status_path()
    tmp = status_file.with_name(f"{status_file.name}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(status_file))
    except OSError as exc:
        logger.warning("Failed to write model download status: %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def get_native_storage_status() -> dict[str, Any]:
    """Return free and total bytes for model storage on Windows Desktop."""
    models_dir = get_models_directory()
    usage = shutil.disk_usage(models_dir)
    margin = min(10 * 1024 * 1024 * 1024, int(usage.total * 0.05))
    return {
        "freeBytes": int(usage.free),
        "totalBytes": int(usage.total),
        "marginBytes": margin,
    }


def compute_file_sha256(file_path: Path, cancel_event: threading.Event | None = None) -> str:
    """Compute SHA-256 in 1MB chunks with periodic cancel check."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Cancelled during hashing")
            h.update(chunk)
    return h.hexdigest().lower()


def write_sidecar_stamp(model_path: Path, sha256_hash: str) -> None:
    """Write <model>.gguf.sha256.json sidecar stamp for fast subsequent verification."""
    stat = model_path.stat()
    stamp_path = Path(f"{model_path}.sha256.json")
    stamp_data = {
        "schemaVersion": 1,
        "file": model_path.name,
        "size": stat.st_size,
        "lastWriteUtc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "sha256": sha256_hash.strip().lower(),
    }
    stamp_path.write_text(json.dumps(stamp_data, indent=2), encoding="utf-8")


def _resolve_content_length(url: str) -> int:
    """Query Content-Length of the final target file via curl HEAD request."""
    try:
        res = subprocess.run(
            ["curl.exe", "-sI", "-L", "--connect-timeout", "10", url],
            capture_output=True,
            text=True,
            timeout=25,
        )
        content_len = 0
        for line in res.stdout.splitlines():
            if line.lower().startswith("content-length:"):
                val = int(line.split(":", 1)[1].strip())
                if val > 10000:
                    content_len = val
        return content_len
    except Exception:
        return 0


def _worker_download_plan(
    download_plan: list[tuple[str, str, str]],
    model_label: str,
) -> None:
    global _active_download_proc
    models_dir = get_models_directory()

    write_model_download_status("starting", model_label)

    created_targets: list[Path] = []
    temp_targets: list[Path] = []

    try:
        total_plan_bytes = 0
        part_lengths: list[int] = []
        for fn, url, _sha in download_plan:
            clen = _resolve_content_length(url)
            part_lengths.append(clen)
            total_plan_bytes += clen

        for idx, (fn, url, expected_sha) in enumerate(download_plan, 1):
            if _active_download_cancel.is_set():
                break

            target_file = models_dir / fn
            part_file = models_dir / f"{fn}.part"
            temp_targets.append(part_file)

            part_total = part_lengths[idx - 1]
            label = fn if len(download_plan) == 1 else f"{fn} ({idx}/{len(download_plan)})"

            # Progress polling loop
            stop_polling = threading.Event()
            last_bytes = [0]
            last_time = [time.monotonic()]

            def _poll():
                while not stop_polling.is_set():
                    if _active_download_cancel.is_set():
                        proc = _active_download_proc
                        if proc is not None:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        break
                    try:
                        if part_file.exists():
                            cur = part_file.stat().st_size
                            now = time.monotonic()
                            dt = now - last_time[0]
                            db = cur - last_bytes[0]
                            speed = int(db / dt) if dt > 0.5 else 0
                            if dt > 1.0:
                                last_bytes[0] = cur
                                last_time[0] = now
                            eta = int((part_total - cur) / speed) if speed > 1000 and part_total > cur else None
                            write_model_download_status(
                                "downloading",
                                label,
                                downloaded=cur,
                                total=part_total,
                                speed_bytes_sec=speed,
                                eta_seconds=eta,
                            )
                    except OSError:
                        pass
                    stop_polling.wait(1.5)

            poll_thread = threading.Thread(target=_poll, daemon=True)
            poll_thread.start()

            # Execute curl with resume support
            success = False
            last_error = ""
            for attempt in range(1, 4):
                if _active_download_cancel.is_set():
                    break
                if attempt > 1:
                    _active_download_cancel.wait(3)
                    if _active_download_cancel.is_set():
                        break

                cmd = [
                    "curl.exe",
                    "-fSL",
                    "-sS",
                    "-C",
                    "-",
                    "--connect-timeout",
                    "30",
                    "-o",
                    str(part_file),
                    url,
                ]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                _active_download_proc = proc
                stderr_output = ""
                try:
                    _, stderr_output = proc.communicate(timeout=14400)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _, stderr_output = proc.communicate(timeout=5)
                finally:
                    _active_download_proc = None

                if _active_download_cancel.is_set():
                    break

                if proc.returncode == 0 and part_file.exists() and part_file.stat().st_size > 0:
                    success = True
                    break
                else:
                    last_error = f"curl error (code {proc.returncode}): {stderr_output.strip()}"

            stop_polling.set()
            poll_thread.join(timeout=2)

            if _active_download_cancel.is_set():
                break

            if not success:
                raise RuntimeError(last_error or f"Failed to download {fn} after 3 attempts")

            # Verification step
            write_model_download_status("verifying", label, downloaded=part_file.stat().st_size, total=part_total)
            actual_sha = compute_file_sha256(part_file, _active_download_cancel)
            if expected_sha and actual_sha != expected_sha.strip().lower():
                part_file.unlink(missing_ok=True)
                raise ValueError(
                    f"Integrity check failed for {fn}: expected SHA-256 {expected_sha}, got {actual_sha}"
                )

            # Move .part into final location
            if target_file.exists():
                target_file.unlink(missing_ok=True)
            part_file.replace(target_file)
            created_targets.append(target_file)
            write_sidecar_stamp(target_file, actual_sha)

        if _active_download_cancel.is_set():
            for p in temp_targets:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            write_model_download_status("cancelled", model_label, error="Download cancelled by user")
            logger.info("Native model download cancelled: %s", model_label)
        else:
            write_model_download_status(
                "completed",
                model_label,
                downloaded=total_plan_bytes,
                total=total_plan_bytes,
            )
            logger.info("Native model download completed: %s", model_label)

    except Exception as exc:
        logger.exception("Native model download failed for %s: %s", model_label, exc)
        for p in temp_targets:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        write_model_download_status("failed", model_label, error=str(exc))
    finally:
        _active_download_cancel.clear()


def start_native_model_download(
    gguf_file: str,
    gguf_url: str = "",
    gguf_parts: list[dict[str, str]] | None = None,
    expected_sha256: str = "",
    mmproj: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Start an asynchronous, cancellable native model download."""
    global _active_download_thread, _active_download_info

    with _active_download_lock:
        if _active_download_thread is not None and _active_download_thread.is_alive():
            raise RuntimeError("Another model download is currently in progress")

        # Build download plan: list of (filename, url, sha256)
        plan: list[tuple[str, str, str]] = []
        if gguf_parts:
            for p in gguf_parts:
                fn = p.get("file") or ""
                url = p.get("url") or ""
                sha = p.get("sha256") or ""
                if fn and url:
                    plan.append((fn, url, sha))
        elif gguf_file and gguf_url:
            plan.append((gguf_file, gguf_url, expected_sha256))

        if mmproj and mmproj.get("file") and mmproj.get("url"):
            plan.append((mmproj["file"], mmproj["url"], mmproj.get("sha256") or ""))

        if not plan:
            raise ValueError("No valid artifacts specified for download")

        label = gguf_file if len(plan) == 1 else f"{gguf_file} ({len(plan)} parts)"

        _active_download_cancel.clear()
        _active_download_info = {
            "model": label,
            "startedAt": _iso_now(),
            "planCount": len(plan),
        }

        _active_download_thread = threading.Thread(
            target=_worker_download_plan,
            args=(plan, label),
            daemon=True,
        )
        _active_download_thread.start()

        return {
            "status": "starting",
            "model": label,
            "active": True,
            "isDownloading": True,
        }


def cancel_native_model_download() -> dict[str, Any]:
    """Signal the active download worker and curl process to stop."""
    with _active_download_lock:
        if _active_download_thread is None or not _active_download_thread.is_alive():
            return {"status": "idle", "active": False}

        _active_download_cancel.set()
        proc = _active_download_proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

        return {"status": "cancelling", "cancelled": True}


def delete_native_model_file(filename: str, parts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Delete local GGUF file and its sidecar stamp."""
    models_dir = get_models_directory()
    targets = [filename]
    if parts:
        for p in parts:
            if isinstance(p, dict) and p.get("file"):
                targets.append(p["file"])

    deleted_count = 0
    for target_name in targets:
        path = models_dir / target_name
        stamp = Path(f"{path}.sha256.json")
        part = Path(f"{path}.part")
        for f in (path, stamp, part):
            if f.exists():
                try:
                    f.unlink()
                    deleted_count += 1
                except OSError as exc:
                    logger.warning("Could not unlink %s: %s", f, exc)

    return {"status": "deleted", "deletedFiles": deleted_count}
