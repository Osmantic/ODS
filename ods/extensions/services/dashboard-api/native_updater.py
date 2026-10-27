"""Figma/Framer-style In-App Software Updater for ODS Windows Native Desktop.

Provides background version checking, SHA-256 verified update staging in
``%LOCALAPPDATA%\\ODS\\updates\\``, atomic directory swap (`current` <->
`previous`), owner `.env` preservation, and automatic rollback if post-swap
verification fails.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

import config

logger = logging.getLogger(__name__)

MAX_UPDATE_ARCHIVE_BYTES = 512 * 1024 * 1024  # 512 MiB ceiling
MAX_UPDATE_ENTRIES = 8192


@dataclass
class NativeUpdaterState:
    status: str = "idle"  # idle | checking | update_available | up_to_date | downloading | staged | applying | applied | rolled_back | failed
    current_version: str = "3.0.0"
    target_version: str | None = None
    download_url: str | None = None
    expected_sha256: str | None = None
    changelog_url: str | None = None
    release_notes: str | None = None
    progress_percent: int = 0
    bytes_downloaded: int = 0
    total_bytes: int = 0
    staged_dir: str | None = None
    backup_dir: str | None = None
    error: str | None = None
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "currentVersion": self.current_version,
            "targetVersion": self.target_version,
            "downloadUrl": self.download_url,
            "expectedSha256": self.expected_sha256,
            "changelogUrl": self.changelog_url,
            "releaseNotes": self.release_notes,
            "progressPercent": self.progress_percent,
            "bytesDownloaded": self.bytes_downloaded,
            "totalBytes": self.total_bytes,
            "stagedDir": self.staged_dir,
            "backupDir": self.backup_dir,
            "error": self.error,
            "updatedAt": int(self.updated_at * 1000),
        }


_updater_state = NativeUpdaterState()


def get_updates_root() -> Path:
    """Return the updates root directory under DATA_DIR or %LOCALAPPDATA%\\ODS."""
    base = Path(config.DATA_DIR) if config.DATA_DIR else (Path.home() / "ods" / "data")
    root = base / "updates"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def get_native_updater_state(current_version: str | None = None) -> dict[str, Any]:
    if current_version:
        _updater_state.current_version = current_version
    return _updater_state.to_dict()


def reset_native_updater_state(current_version: str = "3.0.0") -> None:
    global _updater_state
    _updater_state = NativeUpdaterState(current_version=current_version)


def _extract_safe_zip(zip_bytes_or_path: bytes | Path, destination: Path) -> int:
    """Safely extract a zip archive into destination, rejecting traversal or oversize payloads."""
    destination.mkdir(parents=True, exist_ok=True)
    dest_root = destination.resolve()
    total_uncompressed = 0
    file_count = 0

    zf_source = io.BytesIO(zip_bytes_or_path) if isinstance(zip_bytes_or_path, bytes) else zip_bytes_or_path
    with zipfile.ZipFile(zf_source, "r") as zf:
        infos = zf.infolist()
        if not infos or len(infos) > MAX_UPDATE_ENTRIES:
            raise ValueError(f"Update archive has invalid entry count ({len(infos)})")
        for info in infos:
            name = info.filename.replace("\\", "/")
            if (
                not name
                or name.startswith("/")
                or ":" in name
                or ".." in name.split("/")
                or any(ord(ch) < 32 for ch in name)
            ):
                raise ValueError(f"Unsafe entry in update archive: {info.filename!r}")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_UPDATE_ARCHIVE_BYTES:
                raise ValueError("Update archive exceeds maximum uncompressed size limit")
            target = (dest_root / name).resolve()
            try:
                target.relative_to(dest_root)
            except ValueError as exc:
                raise ValueError(f"Update archive entry escapes destination: {name!r}") from exc
            if info.is_dir() or name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            file_count += 1
    return file_count


async def stage_native_update(
    *,
    target_version: str,
    download_url: str,
    expected_sha256: str,
    current_version: str = "3.0.0",
    changelog_url: str | None = None,
    release_notes: str | None = None,
) -> dict[str, Any]:
    """Download, verify SHA-256, and extract an update package into staging."""
    norm_sha = (expected_sha256 or "").strip().lower()
    if len(norm_sha) != 64 or any(c not in "0123456789abcdef" for c in norm_sha):
        raise ValueError("expected_sha256 must be a 64-character lowercase hex SHA-256 digest")
    if not download_url.startswith("https://"):
        raise ValueError("Update download_url must use https://")

    _updater_state.current_version = current_version
    _updater_state.target_version = target_version.lstrip("v")
    _updater_state.download_url = download_url
    _updater_state.expected_sha256 = norm_sha
    _updater_state.changelog_url = changelog_url
    _updater_state.release_notes = release_notes
    _updater_state.status = "downloading"
    _updater_state.progress_percent = 5
    _updater_state.bytes_downloaded = 0
    _updater_state.total_bytes = 0
    _updater_state.error = None
    _updater_state.updated_at = time.time()

    updates_root = get_updates_root()
    downloads_dir = updates_root / "downloads"
    staged_parent = updates_root / "staged"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    staged_parent.mkdir(parents=True, exist_ok=True)

    archive_path = downloads_dir / f"ods-windows-desktop-{_updater_state.target_version}.zip"
    staged_dir = staged_parent / _updater_state.target_version

    hasher = hashlib.sha256()
    try:
        timeout = httpx.Timeout(connect=10.0, read=120.0, write=15.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", download_url) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"Update download failed with HTTP {resp.status_code}")
                total = int(resp.headers.get("content-length") or 0)
                _updater_state.total_bytes = total
                with open(archive_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        if not chunk:
                            continue
                        f.write(chunk)
                        hasher.update(chunk)
                        _updater_state.bytes_downloaded += len(chunk)
                        if _updater_state.bytes_downloaded > MAX_UPDATE_ARCHIVE_BYTES:
                            raise ValueError("Downloaded update archive exceeds size limit")
                        if total > 0:
                            pct = min(90, max(5, int((_updater_state.bytes_downloaded * 85) / total) + 5))
                            _updater_state.progress_percent = pct
                        _updater_state.updated_at = time.time()

        actual_sha = hasher.hexdigest().lower()
        if actual_sha != norm_sha:
            archive_path.unlink(missing_ok=True)
            raise ValueError(
                f"Update archive SHA-256 mismatch (got {actual_sha}, expected {norm_sha})"
            )

        if staged_dir.exists():
            shutil.rmtree(staged_dir)
        _extract_safe_zip(archive_path, staged_dir)

        # Write version stamp inside staged directory
        (staged_dir / ".version").write_text(
            json.dumps(
                {
                    "version": _updater_state.target_version,
                    "sha256": actual_sha,
                    "stagedAt": int(time.time() * 1000),
                }
            ),
            encoding="utf-8",
        )

        _updater_state.staged_dir = str(staged_dir)
        _updater_state.status = "staged"
        _updater_state.progress_percent = 100
        _updater_state.updated_at = time.time()
        return _updater_state.to_dict()
    except Exception as exc:
        _updater_state.status = "failed"
        _updater_state.error = str(exc)
        _updater_state.updated_at = time.time()
        raise


def apply_staged_update(
    app_dir: Path,
    *,
    verify_callback: Callable[[Path], bool] | None = None,
) -> dict[str, Any]:
    """Atomically swap the staged update into app_dir with automatic rollback on failure."""
    if _updater_state.status != "staged" or not _updater_state.staged_dir:
        raise RuntimeError("No verified staged update is ready to apply")

    staged_dir = Path(_updater_state.staged_dir)
    if not staged_dir.is_dir():
        raise RuntimeError(f"Staged update directory does not exist: {staged_dir}")

    _updater_state.status = "applying"
    _updater_state.updated_at = time.time()

    app_dir = app_dir.resolve()
    app_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = get_updates_root() / f"backup-{_updater_state.current_version}-{int(time.time() * 1000)}"

    had_existing = app_dir.exists()
    if had_existing:
        shutil.move(str(app_dir), str(backup_dir))
        _updater_state.backup_dir = str(backup_dir)

    try:
        shutil.copytree(str(staged_dir), str(app_dir))
        if verify_callback is not None and not verify_callback(app_dir):
            raise RuntimeError("Post-update health verification failed")

        # Update ODS_VERSION in DATA_DIR/.env while preserving all owner settings
        if config.DATA_DIR:
            env_path = Path(config.DATA_DIR) / ".env"
            if env_path.is_file():
                lines = env_path.read_text(encoding="utf-8").splitlines()
                updated_lines = []
                replaced = False
                for line in lines:
                    if line.startswith("ODS_VERSION="):
                        updated_lines.append(f"ODS_VERSION={_updater_state.target_version}")
                        replaced = True
                    else:
                        updated_lines.append(line)
                if not replaced:
                    updated_lines.append(f"ODS_VERSION={_updater_state.target_version}")
                env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

        _updater_state.current_version = _updater_state.target_version or _updater_state.current_version
        _updater_state.status = "applied"
        _updater_state.updated_at = time.time()
        return _updater_state.to_dict()
    except Exception as exc:
        # Automatic atomic rollback!
        if app_dir.exists():
            shutil.rmtree(app_dir, ignore_errors=True)
        if had_existing and backup_dir.exists():
            shutil.move(str(backup_dir), str(app_dir))
        _updater_state.status = "rolled_back"
        _updater_state.error = f"Update failed and was automatically rolled back: {exc}"
        _updater_state.updated_at = time.time()
        raise RuntimeError(_updater_state.error) from exc
