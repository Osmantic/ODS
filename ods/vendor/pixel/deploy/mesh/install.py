#!/usr/bin/env python3
"""Install a rootless trusted-owner Pixel mesh profile (mesh-0.1.0).

Productized under deploy/mesh/ from the fleet mesh source. Paths and host
settings that vary per tower are parameterized through environment variables
with Tower1/Tower2/Tower3 defaults preserved. The node allowlist is fixed at
exactly Tower1/Tower2/Tower3. Fails closed on a missing OpenClaw binary before
any state mutation. No shell=True and no shell interpolation: every child
process is an explicit argv list.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import errno
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "mesh-0.1.0"
NODES = {"tower1": 18789, "tower2": 18790, "tower3": 18789}

# The primary GLM runtime exposes 499,968 tokens and both Qwen fallback workers
# expose 262,144. Keep the shared alias at the largest window every configured
# fallback can honor so a busy-primary route cannot receive an oversized turn.
AGENT_CONTEXT_WINDOW = 262_144
AGENT_BOOTSTRAP_MAX_CHARS = 32_000
AGENT_BOOTSTRAP_TOTAL_MAX_CHARS = 96_000
MODEL_PROFILE_NAME = "Dream Fleet local-first agent"
LEGACY_MODEL_PROFILE_NAME = "Dream Fleet DSV4-first agent"
LEGACY_AGENT_CONTEXT_WINDOW = 65_536

# Env-specific overrides (retain safe defaults for Tower1/Tower2/Tower3).
OPENCLAW_BIN_ENV = "PIXEL_MESH_OPENCLAW_BIN"
MODEL_BASE_URL_ENV = "PIXEL_MESH_MODEL_BASE_URL"
MODEL_BASE_URL_DEFAULT = "http://127.0.0.1:18080/v1"
HOME_ENV = "PIXEL_MESH_HOME"  # safe/test home override; defaults to the real home.
EXEC_SHELL_SOURCE_NAME = "exec_shell_bash.sh"
EXEC_SHELL_RELATIVE = Path("exec-shell") / "bash"
TRUSTED_BASH = Path("/bin/bash")
MAX_EXEC_SHELL_BYTES = 4096
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RECONCILE_SOURCE_FILES = (
    ("VERSION", "source VERSION", 128),
    ("deploy/mesh/install.py", "mesh reconciliation installer source", 2_000_000),
    ("deploy/mesh/pixel_mesh_peer.py", "mesh peer source", 1_000_000),
    ("deploy/mesh/exec_shell_bash.sh", "mesh exec shell source", MAX_EXEC_SHELL_BYTES),
)
MESH_SERVICE = "pixel-mesh-gateway.service"
REQUIRED_RECONCILED_COMMANDS = (
    "ask-operations-broker",
    "ask-download-staging",
    "ask-sandboxed-mailbox-readonly",
)
RECONCILE_BOUNDARY = (
    "An exact clean source matching the active signed Pixel release may update only the "
    "rootless content-addressed mesh helper, exec shell, current/previous links, owner-hardened "
    "release directories, and fixed gateway service after full-hash owner confirmation. No model "
    "turn or external effect is authorized."
)
RECONCILE_ROLLBACK_BOUNDARY = (
    "An exact successful mesh reconciliation receipt may be reversed only to its bound prior "
    "content-addressed release after fresh full-hash owner confirmation. No Pixel release, model "
    "turn, credential, workspace content, or external effect is changed."
)
RECONCILE_RECOVERY_BOUNDARY = (
    "One exact incomplete mesh reconciliation claim may be resumed only from a uniquely "
    "classified release-link phase after a fresh full-hash owner confirmation. The original "
    "claim remains immutable; no second forward claim, model turn, credential, workspace "
    "content, Pixel release, or external effect is authorized."
)
RECONCILE_ROLLBACK_RECOVERY_BOUNDARY = (
    "One exact incomplete mesh reconciliation rollback claim may be resumed only from a "
    "uniquely classified release-link phase after a fresh full-hash owner confirmation. The "
    "original rollback claim remains immutable; no second rollback claim, model turn, Pixel "
    "release, credential, workspace content, or external effect is authorized."
)


def safe_absolute_path(value: str | os.PathLike[str], label: str) -> Path:
    raw = os.fspath(value)
    if not raw or any(character.isspace() or character in {"\x00", "%"} for character in raw):
        raise SystemExit(f"{label} must be an absolute path without whitespace, NUL, or %")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{label} must be absolute")
    return path


def mesh_home() -> Path:
    return safe_absolute_path(os.environ.get(HOME_ENV) or Path.home(), HOME_ENV)


def model_base_url() -> str:
    value = os.environ.get(MODEL_BASE_URL_ENV, MODEL_BASE_URL_DEFAULT)
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise SystemExit(f"{MODEL_BASE_URL_ENV} must be a credential-free loopback HTTP(S) URL")
    return value.rstrip("/")


def write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def write_bytes(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_bytes(content)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _open_directory_fd(path: Path, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SystemExit(f"{label} cannot be opened without following links: {exc}") from None
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise SystemExit(f"{label} is not a directory")
    return descriptor


def fsync_directory(path: Path) -> None:
    descriptor = _open_directory_fd(path, "durability directory")
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise SystemExit("durable file write made no progress")
        view = view[written:]


def durable_artifact_checkpoint(_phase: str) -> None:
    """No-op seam used by subprocess hard-crash tests for staged artifacts."""


def release_materialization_checkpoint(_phase: str) -> None:
    """No-op seam used by subprocess hard-crash tests for release publication."""


def immutable_json_checkpoint(_phase: str) -> None:
    """No-op seam used by subprocess hard-crash tests for receipt publication."""


def write_durable_artifact(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        durable_artifact_checkpoint(f"{path.name}:opened")
        _write_all(descriptor, content)
        durable_artifact_checkpoint(f"{path.name}:content-written")
        os.fchmod(descriptor, 0o700)
        durable_artifact_checkpoint(f"{path.name}:mode-fixed")
        os.fsync(descriptor)
        durable_artifact_checkpoint(f"{path.name}:synced")
    finally:
        os.close(descriptor)


def rename_noreplace(directory: Path, source_name: str, target_name: str) -> None:
    if (not re.fullmatch(r"\.[a-zA-Z0-9._-]+", source_name)
            or not re.fullmatch(r"[a-zA-Z0-9._-]+", target_name)):
        raise SystemExit("atomic release publication names are invalid")
    descriptor = _open_directory_fd(directory, "release publication directory")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise SystemExit("atomic no-replace release publication is unavailable")
        renameat2.argtypes = (
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        if renameat2(
                descriptor, os.fsencode(source_name), descriptor, os.fsencode(target_name), 1,
        ) != 0:
            code = ctypes.get_errno()
            if code == errno.EEXIST:
                raise SystemExit("content-addressed mesh release was concurrently published")
            raise SystemExit(f"atomic no-replace release publication failed: {os.strerror(code)}")
    finally:
        os.close(descriptor)


def link_fd_noreplace(descriptor: int, directory_descriptor: int, target_name: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}\.json", target_name):
        raise SystemExit("immutable record final name is invalid")
    libc = ctypes.CDLL(None, use_errno=True)
    linkat = getattr(libc, "linkat", None)
    if linkat is None:
        raise SystemExit("anonymous immutable record publication is unavailable")
    linkat.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
    )
    linkat.restype = ctypes.c_int
    if linkat(descriptor, b"", directory_descriptor, os.fsencode(target_name), 0x1000) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise SystemExit(f"immutable mesh reconciliation record already exists: {target_name}")
        raise SystemExit(f"anonymous immutable record publication failed: {os.strerror(code)}")


def exec_shell_bytes(path: Path) -> bytes:
    try:
        info = path.lstat()
        content = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"mesh exec shell source is unreadable at {path}: {exc}") from None
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit("mesh exec shell source must be a single-link regular file")
    if (not content.startswith(b"#!/bin/sh\n") or b"\r" in content or b"\x00" in content
            or len(content) > MAX_EXEC_SHELL_BYTES):
        raise SystemExit("mesh exec shell source must be bounded LF-terminated POSIX shell")
    return content


def validate_trusted_bash(path: Path = TRUSTED_BASH) -> None:
    try:
        target = path.resolve(strict=True)
        info = target.stat()
    except OSError as exc:
        raise SystemExit(f"trusted Bash is unavailable at {path}: {exc}") from None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or not os.access(path, os.X_OK)):
        raise SystemExit("trusted Bash must resolve to a root-owned, executable, non-writable regular file")


def release_id_for(peer_bytes: bytes, shell_bytes: bytes) -> str:
    digest = hashlib.sha256()
    for name, content in (("pixel-mesh-peer", peer_bytes),
                          (EXEC_SHELL_RELATIVE.as_posix(), shell_bytes)):
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"{VERSION}-{digest.hexdigest()[:12]}"


def read_release_artifact(path: Path, label: str) -> bytes:
    try:
        info = path.lstat()
        content = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"{label} is unreadable at {path}: {exc}") from None
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit(f"{label} must be a single-link regular file")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise SystemExit(f"{label} must remain owner-private and executable (mode 0700)")
    return content


def ensure_private_directory(path: Path, label: str) -> None:
    if path.exists() or path.is_symlink():
        try:
            info = path.lstat()
        except OSError as exc:
            raise SystemExit(f"{label} is unreadable at {path}: {exc}") from None
        if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise SystemExit(f"{label} must be an owner-private directory")
        return
    path.mkdir(parents=True, mode=0o700)


def materialize_release(release_root: Path, peer_bytes: bytes, shell_bytes: bytes) -> Path:
    release_id = release_id_for(peer_bytes, shell_bytes)
    target = Path("releases") / release_id
    release = release_root / target
    if release.exists() or release.is_symlink():
        require_private_directory(release, "content-addressed mesh release")
        observed_peer = read_release_artifact(
            release / "pixel-mesh-peer", "installed mesh peer",
        )
        observed_shell = read_release_artifact(
            release / EXEC_SHELL_RELATIVE, "installed mesh exec shell",
        )
        if observed_peer != peer_bytes or observed_shell != shell_bytes:
            raise SystemExit(f"content-addressed artifact mismatch at {release}")
        if target != Path("releases") / release_id_for(observed_peer, observed_shell):
            raise SystemExit("existing content-addressed mesh release has an invalid identity")
        return target
    releases = release_root / "releases"
    for directory, label in (
        (release_root, "mesh release root"),
        (releases, "mesh releases directory"),
    ):
        if directory.exists() or directory.is_symlink():
            releasable_directory_evidence(directory, label)
            os.chmod(directory, 0o700)
        else:
            ensure_private_directory(directory, label)
        require_private_directory(directory, label)
    staging = Path(tempfile.mkdtemp(prefix=f".{release_id}.pending-", dir=releases))
    staging.chmod(0o700)
    release_materialization_checkpoint("staging-directory-created")
    shell_dir = staging / EXEC_SHELL_RELATIVE.parent
    shell_dir.mkdir(mode=0o700)
    release_materialization_checkpoint("exec-shell-directory-created")
    write_durable_artifact(staging / "pixel-mesh-peer", peer_bytes)
    release_materialization_checkpoint("peer-artifact-written")
    write_durable_artifact(staging / EXEC_SHELL_RELATIVE, shell_bytes)
    release_materialization_checkpoint("exec-shell-artifact-written")
    fsync_directory(shell_dir)
    fsync_directory(staging)
    release_materialization_checkpoint("staging-directory-synced")
    rename_noreplace(releases, staging.name, release_id)
    release_materialization_checkpoint("release-published")
    fsync_directory(releases)
    release_materialization_checkpoint("release-directory-synced")
    observed = mesh_release_evidence(release_root, target.as_posix(), "published candidate")
    if (observed["peerSha256"] != sha256_bytes(peer_bytes)
            or observed["execShellSha256"] != sha256_bytes(shell_bytes)):
        raise SystemExit("published content-addressed mesh release differs from exact bytes")
    return target


def release_with_exec_shell(release_root: Path, target: Path, shell_bytes: bytes) -> Path:
    release = release_root / target
    peer_bytes = read_release_artifact(release / "pixel-mesh-peer", "installed mesh peer")
    shell = release / EXEC_SHELL_RELATIVE
    if shell.exists() or shell.is_symlink():
        installed_shell = read_release_artifact(shell, "installed mesh exec shell")
        expected = Path("releases") / release_id_for(peer_bytes, installed_shell)
        if target != expected:
            raise SystemExit("mesh release identity does not cover its exec shell")
        return target
    return materialize_release(release_root, peer_bytes, shell_bytes)


def write_if_missing(path: Path, content: str, mode: int) -> bool:
    if path.exists():
        return False
    write(path, content, mode)
    return True


def migrate_managed_model_profile(path: Path, openclaw: Path, state: Path) -> bool:
    """Narrowly migrate only Pixel's exact legacy managed model fields."""
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit("mesh profile must be a single-link regular file")
    if info.st_mode & 0o077:
        raise SystemExit("mesh profile must remain owner-private")
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        models = current["models"]["providers"]["tower"]["models"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        raise SystemExit("mesh profile cannot be safely inspected for its managed model migration") from None
    if not isinstance(models, list):
        raise SystemExit("mesh profile has an invalid managed model collection")
    matches = [model for model in models if isinstance(model, dict) and model.get("id") == "dream-fleet-agent"]
    if len(matches) != 1:
        return False
    model = matches[0]
    if not (
        model.get("name") == LEGACY_MODEL_PROFILE_NAME
        and model.get("contextWindow") == LEGACY_AGENT_CONTEXT_WINDOW
    ):
        return False
    model["name"] = MODEL_PROFILE_NAME
    model["contextWindow"] = AGENT_CONTEXT_WINDOW
    candidate = path.with_name(f".{path.name}.managed-model-migration")
    write(candidate, json.dumps(current, indent=2) + "\n", 0o600)
    try:
        validate_config(openclaw, state, candidate)
        os.replace(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)
    return True


def upsert_env(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement = "=".join((key, value))
    updated: list[str] = []
    replaced = False
    for line in lines:
        existing_key, separator, _ = line.partition("=")
        if separator and existing_key.strip() == key:
            if not replaced:
                updated.append(replacement)
                replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(replacement)
    write(path, "\n".join(updated) + "\n", 0o600)


def build_config(node_name: str, workspace: Path, model_base: str, port: int) -> dict[str, object]:
    return {
        "models": {"mode": "merge", "providers": {"tower": {
            "baseUrl": model_base, "apiKey": "local-no-auth",
            "api": "openai-completions", "timeoutSeconds": 600,
            "models": [{"id": "dream-fleet-agent", "name": MODEL_PROFILE_NAME,
                        "reasoning": True, "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": AGENT_CONTEXT_WINDOW, "maxTokens": 16384}]
        }}},
        "agents": {"defaults": {"sandbox": {"mode": "off"}, "memorySearch": {"enabled": False},
                                  "bootstrapMaxChars": AGENT_BOOTSTRAP_MAX_CHARS,
                                  "bootstrapTotalMaxChars": AGENT_BOOTSTRAP_TOTAL_MAX_CHARS},
                   "list": [{"id": "pixel", "name": f"Pixel {node_name}",
                             "workspace": str(workspace), "model": "tower/dream-fleet-agent",
                             "heartbeat": {"every": "0m"}, "skills": []}]},
        "tools": {"profile": "coding",
                  "exec": {"host": "gateway", "mode": "full",
                           "timeoutSec": 1800,
                           "applyPatch": {"enabled": True, "workspaceOnly": False}},
                  "fs": {"workspaceOnly": False}, "sessions": {"visibility": "all"},
                  "loopDetection": {
                      "enabled": True,
                      "historySize": 30,
                      "warningThreshold": 5,
                      "criticalThreshold": 10,
                      "unknownToolThreshold": 5,
                      "globalCircuitBreakerThreshold": 15,
                      "detectors": {"genericRepeat": True, "knownPollNoProgress": True,
                                    "pingPong": True},
                      "postCompactionGuard": {"windowSize": 3},
                  }},
        "gateway": {"mode": "local", "bind": "loopback", "port": port, "auth": {"mode": "token"},
                    "http": {"endpoints": {"chatCompletions": {"enabled": True}}}},
        "session": {"dmScope": "per-account-channel-peer"},
    }


def validate_config(openclaw: Path, state: Path, config_path: Path) -> None:
    subprocess.run(
        [str(openclaw), "config", "validate"],
        env={**os.environ, "OPENCLAW_STATE_DIR": str(state),
             "OPENCLAW_CONFIG_PATH": str(config_path)},
        check=True,
    )


def replace_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, link)


def checked_release_target(release_root: Path, raw_target: str) -> Path:
    target = Path(raw_target)
    if target.is_absolute() or ".." in target.parts or not target.parts or target.parts[0] != "releases":
        raise SystemExit("release symlink target must stay under releases/")
    candidate = (release_root / target).resolve()
    releases = (release_root / "releases").resolve()
    if not candidate.is_relative_to(releases) or not (candidate / "pixel-mesh-peer").is_file():
        raise SystemExit("release symlink target is missing or invalid")
    return target


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def require_regular_bytes(path: Path, label: str, maximum: int = 1_000_000) -> bytes:
    try:
        info = path.lstat()
        content = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"{label} is unavailable at {path}: {exc}") from None
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit(f"{label} must be a single-link regular file")
    if info.st_mode & 0o022:
        raise SystemExit(f"{label} must not be group/world writable")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise SystemExit(f"{label} must be owned by the invoking user")
    if len(content) > maximum:
        raise SystemExit(f"{label} exceeds its bounded size")
    return content


def require_private_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SystemExit(f"{label} is unavailable at {path}: {exc}") from None
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise SystemExit(f"{label} must be an owner-private directory")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise SystemExit(f"{label} must be owned by the invoking user")


def releasable_directory_evidence(path: Path, label: str) -> dict[str, object]:
    """Accept an owned legacy group-writable directory only for bound hardening."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise SystemExit(f"{label} is unavailable at {path}: {exc}") from None
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o002:
        raise SystemExit(f"{label} must be a real directory and must not be world-writable")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise SystemExit(f"{label} must be owned by the invoking user")
    mode = stat.S_IMODE(info.st_mode)
    return {
        "path": str(path),
        "mode": f"{mode:04o}",
        "ownerUid": info.st_uid,
        "ownerGid": info.st_gid,
        "hardeningRequired": mode != 0o700,
    }


def git_value(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit("mesh reconciliation source is not a readable Git checkout")
    return result.stdout.strip()


def git_bytes(repo: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise SystemExit("mesh reconciliation source is not a readable Git checkout")
    return result.stdout


def canonical_index_records(repo: Path) -> tuple[bytes, ...]:
    records = tuple(
        record for record in git_bytes(repo, "ls-files", "-v", "-z").split(b"\0")
        if record
    )
    if not records or any(not record.startswith(b"H ") for record in records):
        raise SystemExit(
            "mesh reconciliation source has hidden or noncanonical Git index flags"
        )
    return records


def collect_source_identity() -> tuple[dict[str, str], bytes, bytes]:
    repo = Path(__file__).resolve().parents[2]
    top = Path(git_value(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo:
        raise SystemExit("mesh reconciliation source is not its exact Git worktree root")
    commit = git_value(repo, "rev-parse", "HEAD")
    tree = git_value(repo, "rev-parse", f"{commit}^{{tree}}")
    if not COMMIT_RE.fullmatch(commit) or not COMMIT_RE.fullmatch(tree):
        raise SystemExit("mesh reconciliation source Git identity is malformed")
    index_records = canonical_index_records(repo)
    if git_value(repo, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("mesh reconciliation source has tracked changes")
    exact: dict[str, bytes] = {}
    for relative, label, maximum in RECONCILE_SOURCE_FILES:
        record = b"H " + relative.encode("ascii")
        if record not in index_records:
            raise SystemExit(f"{label} is not a canonical tracked source file")
        working = require_regular_bytes(repo / relative, label, maximum)
        blob = git_bytes(repo, "cat-file", "blob", f"{commit}:{relative}")
        if len(blob) > maximum or working != blob:
            raise SystemExit(f"{label} differs from its exact HEAD blob")
        exact[relative] = working
    if (git_value(repo, "rev-parse", "HEAD") != commit
            or git_value(repo, "rev-parse", f"{commit}^{{tree}}") != tree
            or canonical_index_records(repo) != index_records
            or git_value(repo, "status", "--porcelain", "--untracked-files=no")):
        raise SystemExit("mesh reconciliation source changed during exact validation")
    version = exact["VERSION"].decode("utf-8").strip()
    if not re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", version):
        raise SystemExit("mesh reconciliation source VERSION is malformed")
    peer = exact["deploy/mesh/pixel_mesh_peer.py"]
    shell = exact["deploy/mesh/exec_shell_bash.sh"]
    return ({
        "path": str(repo),
        "commit": commit,
        "tree": tree,
        "version": version,
        "installerSha256": sha256_bytes(exact["deploy/mesh/install.py"]),
        "peerSha256": sha256_bytes(peer),
        "execShellSha256": sha256_bytes(shell),
    }, peer, shell)


def active_pixel_identity(home: Path) -> dict[str, str]:
    pixel_root = home / ".local" / "share" / "pixel"
    current = pixel_root / "current"
    if not current.is_symlink():
        raise SystemExit("active Pixel release link is unavailable or unsafe")
    raw_target = Path(os.readlink(current))
    if (raw_target.is_absolute() or len(raw_target.parts) != 2
            or raw_target.parts[0] != "releases"):
        raise SystemExit("active Pixel release must use one relative releases/<version> target")
    releases = (pixel_root / "releases").resolve(strict=True)
    release = (pixel_root / raw_target).resolve(strict=True)
    if not release.is_relative_to(releases) or release.parent != releases:
        raise SystemExit("active Pixel release escapes the fixed release root")
    version = require_regular_bytes(release / "VERSION", "active Pixel VERSION", 128).decode("utf-8").strip()
    if release.name != version:
        raise SystemExit("active Pixel release target and VERSION differ")
    identity_path = release / "release-identity.json"
    identity_bytes = require_regular_bytes(identity_path, "active Pixel release identity")
    try:
        identity = json.loads(identity_bytes)
    except (UnicodeError, json.JSONDecodeError):
        raise SystemExit("active Pixel release identity is invalid JSON") from None
    source = identity.get("source") if isinstance(identity, dict) else None
    qualification = identity.get("qualification") if isinstance(identity, dict) else None
    if (not isinstance(identity, dict)
            or identity.get("schemaVersion") != 1
            or identity.get("kind") != "pixel-release-source-identity"
            or identity.get("pixel") != version
            or not isinstance(source, dict)
            or source.get("state") != "git-clean"
            or not COMMIT_RE.fullmatch(str(source.get("commit", "")))
            or not COMMIT_RE.fullmatch(str(source.get("tree", "")))
            or not isinstance(qualification, dict)
            or qualification.get("recordStatus") != "supported"):
        raise SystemExit("active Pixel release identity is not an exact supported source identity")
    return {
        "version": version,
        "target": raw_target.as_posix(),
        "identitySha256": sha256_bytes(identity_bytes),
        "sourceCommit": source["commit"],
        "sourceTree": source["tree"],
    }


def reject_signed_pixel_standalone_mutation(home: Path) -> None:
    pixel_current = home / ".local" / "share" / "pixel" / "current"
    mesh_current = home / ".local" / "share" / "pixel-mesh" / "current"
    pixel_present = pixel_current.exists() or pixel_current.is_symlink()
    mesh_present = mesh_current.exists() or mesh_current.is_symlink()
    if pixel_present and mesh_present:
        raise SystemExit(
            "an installed Pixel and mesh require the version-bound reconcile or "
            "reconcile-rollback transaction; standalone mutation is refused"
        )


def mesh_release_evidence(release_root: Path, raw_target: str, label: str) -> dict[str, str]:
    target = checked_release_target(release_root, raw_target)
    release = release_root / target
    require_private_directory(release, f"{label} mesh release")
    peer = read_release_artifact(release / "pixel-mesh-peer", f"{label} mesh peer")
    shell = read_release_artifact(release / EXEC_SHELL_RELATIVE, f"{label} mesh exec shell")
    expected = Path("releases") / release_id_for(peer, shell)
    if target != expected:
        raise SystemExit(f"{label} mesh release differs from its content-addressed identity")
    return {
        "target": target.as_posix(),
        "peerSha256": sha256_bytes(peer),
        "execShellSha256": sha256_bytes(shell),
        "commandSurface": "complete" if all(
            command.encode("utf-8") in peer for command in REQUIRED_RECONCILED_COMMANDS
        ) else "legacy",
    }


def candidate_mesh_evidence(
    release_root: Path, peer: bytes, shell: bytes,
) -> tuple[dict[str, str], bytes, bytes]:
    if not peer.startswith(b"#!/usr/bin/env python3\n") or b"\r" in peer:
        raise SystemExit("mesh peer source does not have the required LF-terminated Python shebang")
    if not all(command.encode("utf-8") in peer for command in REQUIRED_RECONCILED_COMMANDS):
        raise SystemExit("mesh peer source lacks the required reconciled command surface")
    if (len(shell) > MAX_EXEC_SHELL_BYTES or b"\r" in shell
            or not shell.startswith(b"#!/bin/sh\n") or not shell.endswith(b"\n")):
        raise SystemExit("mesh exec shell source must be bounded LF-terminated POSIX shell")
    target = Path("releases") / release_id_for(peer, shell)
    release = release_root / target
    if release.exists() or release.is_symlink():
        observed = mesh_release_evidence(release_root, target.as_posix(), "candidate")
        if (observed["peerSha256"] != sha256_bytes(peer)
                or observed["execShellSha256"] != sha256_bytes(shell)):
            raise SystemExit("existing candidate mesh release differs from exact source bytes")
    return ({
        "target": target.as_posix(),
        "peerSha256": sha256_bytes(peer),
        "execShellSha256": sha256_bytes(shell),
        "commandSurface": "complete",
    }, peer, shell)


def mesh_service_state() -> str:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", MESH_SERVICE],
        capture_output=True, text=True,
    )
    state = result.stdout.strip()
    if result.returncode != 0 or state != "active":
        raise SystemExit("mesh reconciliation requires the gateway service to be active")
    return state


def verify_local_peer_link(home: Path, release_root: Path) -> str:
    link = home / ".local" / "bin" / "pixel-mesh-peer"
    expected = release_root / "current" / "pixel-mesh-peer"
    if not link.is_symlink() or os.readlink(link) != str(expected):
        raise SystemExit("installed mesh peer link is not bound to the fixed current release")
    if link.resolve(strict=True) != expected.resolve(strict=True):
        raise SystemExit("installed mesh peer link resolves outside the fixed current release")
    return str(expected)


def build_reconcile_core_intent(
        node_name: str, home: Path,
) -> tuple[dict[str, object], bytes, bytes]:
    node = node_name.lower()
    if node not in NODES:
        raise SystemExit("mesh reconciliation node is not allowlisted")
    if socket.gethostname().lower() != node:
        raise SystemExit("mesh reconciliation node differs from the observed host")
    source, peer, shell = collect_source_identity()
    active = active_pixel_identity(home)
    if (source["version"] != active["version"]
            or source["commit"] != active["sourceCommit"]
            or source["tree"] != active["sourceTree"]):
        raise SystemExit("clean mesh source does not match the active signed Pixel release identity")
    release_root = home / ".local" / "share" / "pixel-mesh"
    layout = {
        "root": releasable_directory_evidence(release_root, "mesh release root"),
        "releases": releasable_directory_evidence(
            release_root / "releases", "mesh releases directory",
        ),
    }
    current_link = release_root / "current"
    previous_link = release_root / "previous"
    if not current_link.is_symlink() or not previous_link.is_symlink():
        raise SystemExit("mesh reconciliation requires exact current and previous release links")
    current = mesh_release_evidence(release_root, os.readlink(current_link), "current")
    previous = mesh_release_evidence(release_root, os.readlink(previous_link), "previous")
    candidate, peer, shell = candidate_mesh_evidence(release_root, peer, shell)
    if candidate["target"] == current["target"]:
        raise SystemExit("mesh command surface is already reconciled to this Pixel release")
    helper_link = verify_local_peer_link(home, release_root)
    service_state = mesh_service_state()
    receipt_root = home / ".local" / "state" / "pixel-mesh" / "reconciliations"
    intent: dict[str, object] = {
        "schemaVersion": 1,
        "operation": "pixel-mesh-release-reconcile",
        "product": "Pixel",
        "node": node_name,
        "observedHostname": socket.gethostname(),
        "port": NODES[node],
        "source": source,
        "activePixel": active,
        "mesh": {
            "layout": layout,
            "current": current,
            "previous": previous,
            "candidate": candidate,
        },
        "fixedPaths": {
            "releaseRoot": str(release_root),
            "helperLink": helper_link,
            "receiptRoot": str(receipt_root),
        },
        "service": {"unit": MESH_SERVICE, "state": service_state},
        "effects": {
            "modelTurnWillRun": False,
            "externalEffectAuthority": False,
            "currentAndPreviousWillChange": True,
            "releaseDirectoryModesWillBeHardened": any(
                item["hardeningRequired"] for item in layout.values()
            ),
            "gatewayWillRestart": True,
        },
        "boundary": RECONCILE_BOUNDARY,
    }
    return intent, peer, shell


def bind_reconcile_attempt(
        core: dict[str, object], home: Path,
) -> dict[str, object]:
    """Bind a new hash after exact, terminal, restored failures without overwriting history."""
    state_root = reconciliation_state_root(home)
    candidate = core
    sequence = 0
    while True:
        digest = intent_hash(candidate)
        claim_path = state_root / "claims" / f"{digest}.json"
        result_path = state_root / "reconciliations" / f"{digest}.json"
        claim_present = claim_path.exists() or claim_path.is_symlink()
        result_present = result_path.exists() or result_path.is_symlink()
        if not claim_present and not result_present:
            return candidate
        if claim_present != result_present:
            raise SystemExit(
                f"mesh reconciliation claim {digest} is incomplete; use reconcile-recover"
            )
        claim, claim_sha = read_immutable_json(claim_path, "prior mesh reconciliation claim")
        result, result_sha = read_immutable_json(result_path, "prior mesh reconciliation result")
        prior_intent = claim.get("intent")
        restored_status = result.get("status")
        restored_flag = result.get("activeDeploymentRestored")
        if (claim.get("schemaVersion") != 1
                or claim.get("operation") != "pixel-mesh-release-reconcile"
                or claim.get("status") != "claimed"
                or claim.get("reconcileHash") != digest
                or prior_intent != candidate
                or not isinstance(prior_intent, dict)
                or intent_hash(prior_intent) != digest
                or result.get("schemaVersion") != 1
                or result.get("operation") != "pixel-mesh-release-reconcile-result"
                or result.get("reconcileHash") != digest
                or result.get("claimSha256") != claim_sha
                or restored_status not in {"failed-before-mutation", "failed-restored"}
                or (restored_status == "failed-restored" and restored_flag is not True)
                or (restored_status == "failed-before-mutation" and restored_flag is not False)):
            raise SystemExit("prior mesh reconciliation attempt is not an exact restored failure")
        sequence += 1
        if sequence > 100:
            raise SystemExit("mesh reconciliation retry chain exceeds its bounded length")
        candidate = {
            **core,
            "retry": {
                "sequence": sequence,
                "previousReconcileHash": digest,
                "previousClaimSha256": claim_sha,
                "previousResultSha256": result_sha,
            },
        }


def build_reconcile_intent(node_name: str, home: Path) -> tuple[dict[str, object], bytes, bytes]:
    core, peer, shell = build_reconcile_core_intent(node_name, home)
    return bind_reconcile_attempt(core, home), peer, shell


def reconciliation_mutation_checkpoint(_phase: str) -> None:
    """No-op seam used by subprocess crash-injection tests."""


def incomplete_reconcile_evidence(
        node_name: str, home: Path, reconcile_hash: str,
) -> tuple[dict[str, object], str, dict[str, object], bytes, bytes, dict[str, object]]:
    """Validate one incomplete forward claim and classify its exact observable phase."""
    if not HASH_RE.fullmatch(reconcile_hash):
        raise SystemExit("forward reconcile hash must be one full lowercase SHA-256")
    node = node_name.lower()
    if node not in NODES or socket.gethostname().lower() != node:
        raise SystemExit("mesh reconciliation recovery node differs from the allowlisted observed host")
    state_root = reconciliation_state_root(home)
    require_private_directory(state_root, "mesh reconciliation state root")
    claims = state_root / "claims"
    receipts = state_root / "reconciliations"
    require_private_directory(claims, "mesh reconciliation claims directory")
    require_private_directory(receipts, "mesh reconciliation receipts directory")
    claim_path = claims / f"{reconcile_hash}.json"
    result_path = receipts / f"{reconcile_hash}.json"
    claim, claim_sha = read_immutable_json(claim_path, "incomplete mesh reconciliation claim")
    if result_path.exists() or result_path.is_symlink():
        raise SystemExit("mesh reconciliation claim already has a terminal result")
    intent = claim.get("intent")
    if (claim.get("schemaVersion") != 1
            or claim.get("operation") != "pixel-mesh-release-reconcile"
            or claim.get("status") != "claimed"
            or claim.get("reconcileHash") != reconcile_hash
            or claim.get("boundary") != RECONCILE_BOUNDARY
            or not isinstance(intent, dict)
            or intent_hash(intent) != reconcile_hash
            or intent.get("node") != node_name
            or intent.get("port") != NODES[node]
            or intent.get("boundary") != RECONCILE_BOUNDARY):
        raise SystemExit("incomplete mesh reconciliation claim is invalid or outside this boundary")
    source, peer, shell = collect_source_identity()
    active = active_pixel_identity(home)
    if source != intent.get("source") or active != intent.get("activePixel"):
        raise SystemExit("recovery source or active Pixel differs from the incomplete claim")
    fixed = intent.get("fixedPaths")
    expected_root = home / ".local" / "share" / "pixel-mesh"
    expected_helper = expected_root / "current" / "pixel-mesh-peer"
    if (not isinstance(fixed, dict)
            or fixed.get("releaseRoot") != str(expected_root)
            or fixed.get("helperLink") != str(expected_helper)
            or fixed.get("receiptRoot") != str(state_root / "reconciliations")):
        raise SystemExit("incomplete claim fixed paths differ from this exact home")
    mesh = intent.get("mesh")
    if not isinstance(mesh, dict):
        raise SystemExit("incomplete claim lacks exact mesh release evidence")
    old_current = mesh.get("current")
    old_previous = mesh.get("previous")
    candidate = mesh.get("candidate")
    layout = mesh.get("layout")
    if (not isinstance(old_current, dict) or not isinstance(old_previous, dict)
            or not isinstance(candidate, dict) or not isinstance(layout, dict)
            or old_current.get("target") == old_previous.get("target")):
        raise SystemExit("incomplete claim cannot be uniquely phase-classified")
    observed_layout: dict[str, object] = {}
    for key, path, label in (
        ("root", expected_root, "recovery mesh release root"),
        ("releases", expected_root / "releases", "recovery mesh releases directory"),
    ):
        prior = layout.get(key)
        observed = releasable_directory_evidence(path, label)
        if (not isinstance(prior, dict)
                or observed.get("path") != prior.get("path")
                or observed.get("ownerUid") != prior.get("ownerUid")
                or observed.get("ownerGid") != prior.get("ownerGid")
                or observed.get("mode") not in {prior.get("mode"), "0700"}):
            raise SystemExit("mesh release directory drift makes recovery ambiguous")
        observed_layout[key] = observed
    current_link = expected_root / "current"
    previous_link = expected_root / "previous"
    if not current_link.is_symlink() or not previous_link.is_symlink():
        raise SystemExit("mesh recovery requires exact current and previous release links")
    current = mesh_release_evidence(expected_root, os.readlink(current_link), "recovery current")
    previous = mesh_release_evidence(expected_root, os.readlink(previous_link), "recovery previous")
    expected_candidate, peer, shell = candidate_mesh_evidence(expected_root, peer, shell)
    if expected_candidate != candidate:
        raise SystemExit("recovery candidate differs from the incomplete claim")
    candidate_path = expected_root / str(candidate["target"])
    candidate_present = candidate_path.exists() or candidate_path.is_symlink()
    if current == old_current and previous == old_previous:
        phase = "pre-link-switch"
    elif current == old_current and previous == old_current:
        phase = "previous-link-switched"
    elif current == candidate and previous == old_current:
        phase = "forward-links-switched"
    else:
        raise SystemExit("mesh release links are an ambiguous mixed recovery state")
    helper = verify_local_peer_link(home, expected_root)
    service = mesh_service_state()
    observed = {
        "phase": phase,
        "layout": observed_layout,
        "current": current,
        "previous": previous,
        "candidatePresent": candidate_present,
        "helperLink": helper,
        "serviceState": service,
    }
    return claim, claim_sha, intent, peer, shell, observed


def recovery_chain_binding(
        state_root: Path, binding_hash: str, *, claims_name: str = "recovery-claims",
        results_name: str = "recoveries",
        operation: str = "pixel-mesh-release-reconcile-recovery",
        result_operation: str = "pixel-mesh-release-reconcile-recovery-result",
        binding_key: str = "reconcileHash",
) -> dict[str, object]:
    """Bind each recovery attempt to all prior immutable attempts for this forward claim."""
    claims_dir = state_root / claims_name
    results_dir = state_root / results_name
    if not claims_dir.exists() and not claims_dir.is_symlink():
        return {"sequence": 1}
    require_private_directory(claims_dir, "mesh reconciliation recovery claims directory")
    if results_dir.exists() or results_dir.is_symlink():
        require_private_directory(results_dir, "mesh reconciliation recovery results directory")
    entries: list[tuple[int, str, str, str | None, dict[str, object]]] = []
    for path in claims_dir.iterdir():
        if path.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
            raise SystemExit("mesh reconciliation recovery claim directory contains unsafe entries")
        recovery_hash = path.stem
        claim, claim_sha = read_immutable_json(path, "prior mesh reconciliation recovery claim")
        recovery_intent = claim.get("intent")
        if (claim.get("schemaVersion") != 1
                or claim.get("operation") != operation
                or claim.get("status") != "claimed"
                or claim.get("recoveryHash") != recovery_hash
                or claim.get(binding_key) != binding_hash
                or not isinstance(recovery_intent, dict)
                or intent_hash(recovery_intent) != recovery_hash):
            continue
        chain = recovery_intent.get("recoveryAttempt")
        if not isinstance(chain, dict) or not isinstance(chain.get("sequence"), int):
            raise SystemExit("prior mesh reconciliation recovery chain is invalid")
        result_sha = None
        result_path = results_dir / path.name
        if result_path.exists() or result_path.is_symlink():
            result, result_sha = read_immutable_json(
                result_path, "prior mesh reconciliation recovery result",
            )
            if (result.get("operation") != result_operation
                    or result.get("recoveryHash") != recovery_hash
                    or result.get(binding_key) != binding_hash
                    or result.get("claimSha256") != claim_sha):
                raise SystemExit("prior mesh reconciliation recovery receipt chain is invalid")
        entries.append((chain["sequence"], recovery_hash, claim_sha, result_sha, chain))
    if not entries:
        return {"sequence": 1}
    entries.sort()
    if [entry[0] for entry in entries] != list(range(1, len(entries) + 1)):
        raise SystemExit("mesh reconciliation recovery attempt sequence is not contiguous")
    for index, entry in enumerate(entries):
        sequence, _hash, _claim_sha, _result_sha, chain = entry
        if sequence == 1:
            if chain != {"sequence": 1}:
                raise SystemExit("first mesh reconciliation recovery attempt has a predecessor")
            continue
        prior = entries[index - 1]
        if chain != {
            "sequence": sequence,
            "previousRecoveryHash": prior[1],
            "previousRecoveryClaimSha256": prior[2],
            "previousRecoveryResultSha256": prior[3],
        }:
            raise SystemExit("mesh reconciliation recovery attempt is not predecessor-bound")
    sequence, previous_hash, previous_claim_sha, previous_result_sha, _chain = entries[-1]
    if sequence >= 100:
        raise SystemExit("mesh reconciliation recovery chain exceeds its bounded length")
    return {
        "sequence": sequence + 1,
        "previousRecoveryHash": previous_hash,
        "previousRecoveryClaimSha256": previous_claim_sha,
        "previousRecoveryResultSha256": previous_result_sha,
    }


def build_reconcile_recovery_intent(
        node_name: str, home: Path, reconcile_hash: str,
) -> tuple[dict[str, object], bytes, bytes]:
    _claim, claim_sha, forward_intent, peer, shell, observed = incomplete_reconcile_evidence(
        node_name, home, reconcile_hash,
    )
    state_root = reconciliation_state_root(home)
    intent = {
        "schemaVersion": 1,
        "operation": "pixel-mesh-release-reconcile-recovery",
        "product": "Pixel",
        "node": node_name,
        "observedHostname": socket.gethostname(),
        "reconcileHash": reconcile_hash,
        "forwardClaimSha256": claim_sha,
        "source": forward_intent["source"],
        "activePixel": forward_intent["activePixel"],
        "observed": observed,
        "action": "resume-forward",
        "recoveryAttempt": recovery_chain_binding(state_root, reconcile_hash),
        "effects": {
            "modelTurnWillRun": False,
            "externalEffectAuthority": False,
            "secondForwardClaimWillBeCreated": False,
            "forwardLinksMayChange": observed["phase"] != "forward-links-switched",
            "gatewayWillRestart": True,
        },
        "boundary": RECONCILE_RECOVERY_BOUNDARY,
    }
    return intent, peer, shell


def intent_hash(intent: dict[str, object]) -> str:
    return sha256_bytes(canonical_json(intent))


def immutable_json(path: Path, value: dict[str, object], mode: int = 0o400) -> str:
    content = canonical_json(value)
    require_private_directory(path.parent, "mesh reconciliation receipt directory")
    if path.exists() or path.is_symlink():
        raise SystemExit(f"immutable mesh reconciliation record already exists: {path.name}")
    if mode != 0o400:
        raise SystemExit("immutable mesh reconciliation records require mode 0400")
    directory_descriptor = _open_directory_fd(
        path.parent, "mesh reconciliation receipt directory",
    )
    temporary_flags = getattr(os, "O_TMPFILE", 0) | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    if not getattr(os, "O_TMPFILE", 0):
        os.close(directory_descriptor)
        raise SystemExit("anonymous immutable record publication is unavailable")
    try:
        descriptor = os.open(".", temporary_flags, 0o600, dir_fd=directory_descriptor)
    except OSError as exc:
        os.close(directory_descriptor)
        raise SystemExit(f"anonymous immutable record creation failed: {exc}") from None
    try:
        record_kind = path.parent.name
        immutable_json_checkpoint(f"{record_kind}:anonymous-inode-opened")
        _write_all(descriptor, content)
        immutable_json_checkpoint(f"{record_kind}:content-written")
        os.fchmod(descriptor, mode)
        immutable_json_checkpoint(f"{record_kind}:mode-fixed")
        os.fsync(descriptor)
        immutable_json_checkpoint(f"{record_kind}:content-synced")
        link_fd_noreplace(descriptor, directory_descriptor, path.name)
        immutable_json_checkpoint(f"{record_kind}:record-published")
        os.fsync(directory_descriptor)
        immutable_json_checkpoint(f"{record_kind}:directory-synced")
        info = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        observed = bytearray()
        while len(observed) < len(content) + 1:
            chunk = os.read(descriptor, len(content) + 1 - len(observed))
            if not chunk:
                break
            observed.extend(chunk)
        if (bytes(observed) != content or not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1):
            raise SystemExit("published immutable mesh reconciliation record failed read-back")
    finally:
        os.close(descriptor)
        os.close(directory_descriptor)
    return sha256_bytes(content)


def read_immutable_json(path: Path, label: str) -> tuple[dict[str, object], str]:
    content = require_regular_bytes(path, label)
    if stat.S_IMODE(path.stat().st_mode) != 0o400:
        raise SystemExit(f"{label} must remain immutable mode 0400")
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError):
        raise SystemExit(f"{label} is invalid JSON") from None
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value, sha256_bytes(content)


def reconciliation_state_root(home: Path) -> Path:
    return home / ".local" / "state" / "pixel-mesh"


def ensure_reconciliation_state_root(home: Path) -> Path:
    """Create the fixed state path without following owner-substitutable symlinks."""
    root = reconciliation_state_root(home)
    try:
        relative = root.relative_to(home)
    except ValueError:
        raise SystemExit("mesh reconciliation state root escapes the fixed home") from None
    current = home
    for index, component in enumerate(relative.parts):
        current = current / component
        if not current.exists() and not current.is_symlink():
            current.mkdir(mode=0o700)
        try:
            info = current.lstat()
        except OSError as exc:
            raise SystemExit(f"mesh reconciliation state path is unavailable at {current}: {exc}") from None
        if current.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise SystemExit("mesh reconciliation state path must not contain symlinks")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise SystemExit("mesh reconciliation state path must be owner-controlled")
        if info.st_mode & 0o022:
            raise SystemExit("mesh reconciliation state path must not be group/world writable")
        if index == len(relative.parts) - 1 and info.st_mode & 0o077:
            raise SystemExit("mesh reconciliation state root must be owner-private")
    return root


def ensure_private_child_directory(root: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-z-]+", name):
        raise SystemExit("mesh reconciliation receipt directory name is invalid")
    child = root / name
    if not child.exists() and not child.is_symlink():
        child.mkdir(mode=0o700)
    require_private_directory(child, "mesh reconciliation receipt directory")
    if child.parent != root:
        raise SystemExit("mesh reconciliation receipt directory escapes its fixed root")
    return child


class ReconciliationLock:
    def __init__(self, root: Path):
        self.root = root
        self.stream = None

    def __enter__(self):
        if os.name != "posix":
            raise SystemExit("mesh reconciliation execution requires POSIX file locking")
        import fcntl
        require_private_directory(self.root, "mesh reconciliation state root")
        directory_descriptor = _open_directory_fd(
            self.root, "mesh reconciliation state root",
        )
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(".lock", flags, 0o600, dir_fd=directory_descriptor)
        except OSError as exc:
            os.close(directory_descriptor)
            raise SystemExit(f"mesh reconciliation lock is unsafe or unavailable: {exc}") from None
        os.close(directory_descriptor)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())):
            os.close(descriptor)
            raise SystemExit("mesh reconciliation lock must be an owner-private single-link file")
        self.stream = os.fdopen(descriptor, "a+b")
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.stream.close()
            raise SystemExit("another mesh reconciliation transaction is active") from None
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.stream is not None:
            self.stream.close()


def restart_and_verify_mesh(home: Path, expected_target: str) -> str:
    subprocess.run(["systemctl", "--user", "restart", MESH_SERVICE], check=True)
    if mesh_service_state() != "active":
        raise SystemExit("mesh gateway did not become active after restart")
    release_root = home / ".local" / "share" / "pixel-mesh"
    if os.readlink(release_root / "current") != expected_target:
        raise SystemExit("mesh current release drifted during restart")
    verify_local_peer_link(home, release_root)
    peer = home / ".local" / "bin" / "pixel-mesh-peer"
    result = subprocess.run(
        [str(peer), "status"], capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise SystemExit("reconciled mesh helper status probe failed")
    if len(result.stdout.encode("utf-8")) > 262_144:
        raise SystemExit("reconciled mesh helper status probe exceeded its bounded output")
    try:
        status_value = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit("reconciled mesh helper status probe was not JSON") from None
    if not isinstance(status_value, dict) or not status_value.get("reachable"):
        raise SystemExit("reconciled mesh helper status probe did not prove reachability")
    return sha256_bytes(result.stdout.encode("utf-8"))


def reconcile_preview(node_name: str, home: Path) -> tuple[dict[str, object], str]:
    intent, _peer, _shell = build_reconcile_intent(node_name, home)
    digest = intent_hash(intent)
    payload = {
        **intent,
        "status": "ready",
        "reconcileHash": digest,
        "confirmationRequired": "repeat-the-full-reconcile-hash-with-confirm",
    }
    return payload, digest


def execute_reconcile(node_name: str, home: Path, supplied_hash: str) -> dict[str, object]:
    if not HASH_RE.fullmatch(supplied_hash):
        raise SystemExit("reconcile hash must be one full lowercase SHA-256")
    initial, _peer, _shell = build_reconcile_intent(node_name, home)
    if intent_hash(initial) != supplied_hash:
        raise SystemExit("mesh reconciliation confirmation hash does not match fresh state")
    state_root = ensure_reconciliation_state_root(home)
    with ReconciliationLock(state_root):
        intent, peer, shell = build_reconcile_intent(node_name, home)
        if intent_hash(intent) != supplied_hash or intent != initial:
            raise SystemExit("mesh reconciliation preview became stale before mutation")
        receipts = ensure_private_child_directory(state_root, "reconciliations")
        claims = ensure_private_child_directory(state_root, "claims")
        result_path = receipts / f"{supplied_hash}.json"
        claim_path = claims / f"{supplied_hash}.json"
        if result_path.exists() or result_path.is_symlink() or claim_path.exists() or claim_path.is_symlink():
            raise SystemExit("mesh reconciliation hash was already claimed or completed")
        claim = {
            "schemaVersion": 1,
            "operation": "pixel-mesh-release-reconcile",
            "status": "claimed",
            "reconcileHash": supplied_hash,
            "claimedAt": utc_now(),
            "intent": intent,
            "boundary": RECONCILE_BOUNDARY,
        }
        claim_sha = immutable_json(claim_path, claim)
        release_root = home / ".local" / "share" / "pixel-mesh"
        old_current = intent["mesh"]["current"]["target"]
        old_previous = intent["mesh"]["previous"]["target"]
        new_target = intent["mesh"]["candidate"]["target"]
        current_link = release_root / "current"
        previous_link = release_root / "previous"
        local_peer = home / ".local" / "bin" / "pixel-mesh-peer"
        mutation_started = False
        modes_hardened = False
        try:
            mutation_started = True
            os.chmod(release_root, 0o700)
            os.chmod(release_root / "releases", 0o700)
            require_private_directory(release_root, "hardened mesh release root")
            require_private_directory(release_root / "releases", "hardened mesh releases directory")
            modes_hardened = True
            reconciliation_mutation_checkpoint("release-directories-hardened")
            if (mesh_release_evidence(release_root, os.readlink(current_link), "hardened current")
                    != intent["mesh"]["current"]
                    or mesh_release_evidence(release_root, os.readlink(previous_link), "hardened previous")
                    != intent["mesh"]["previous"]):
                raise SystemExit("mesh release evidence changed while hardening its directories")
            materialized = materialize_release(release_root, peer, shell).as_posix()
            if materialized != new_target:
                raise SystemExit("materialized mesh release differs from confirmed candidate")
            reconciliation_mutation_checkpoint("candidate-materialized")
            replace_symlink(previous_link, Path(old_current))
            reconciliation_mutation_checkpoint("previous-link-switched")
            replace_symlink(current_link, Path(new_target))
            reconciliation_mutation_checkpoint("current-link-switched")
            replace_symlink(local_peer, current_link / "pixel-mesh-peer")
            reconciliation_mutation_checkpoint("helper-link-rebound")
            runtime_sha = restart_and_verify_mesh(home, new_target)
            reconciliation_mutation_checkpoint("runtime-verified")
            current = mesh_release_evidence(release_root, os.readlink(current_link), "reconciled current")
            previous = mesh_release_evidence(release_root, os.readlink(previous_link), "reconciled previous")
            if current != intent["mesh"]["candidate"] or previous != intent["mesh"]["current"]:
                raise SystemExit("post-reconciliation mesh releases differ from confirmed intent")
            result = {
                "schemaVersion": 1,
                "operation": "pixel-mesh-release-reconcile-result",
                "status": "reconciled",
                "reconcileHash": supplied_hash,
                "claimSha256": claim_sha,
                "activePixel": intent["activePixel"],
                "node": node_name,
                "port": intent["port"],
                "oldCurrentTarget": old_current,
                "oldPreviousTarget": old_previous,
                "currentTarget": new_target,
                "previousTarget": old_current,
                "currentPeerSha256": current["peerSha256"],
                "currentExecShellSha256": current["execShellSha256"],
                "runtimeStatusSha256": runtime_sha,
                "serviceStateBefore": intent["service"]["state"],
                "serviceStateAfter": "active",
                "releaseDirectoryModesBefore": intent["mesh"]["layout"],
                "releaseDirectoryModeAfter": "0700",
                "completedAt": utc_now(),
                "boundary": RECONCILE_BOUNDARY,
            }
            receipt_sha = immutable_json(result_path, result)
            return {**result, "receiptPath": str(result_path), "receiptSha256": receipt_sha}
        except BaseException as error:
            if mutation_started:
                try:
                    replace_symlink(current_link, Path(old_current))
                    replace_symlink(previous_link, Path(old_previous))
                    replace_symlink(local_peer, current_link / "pixel-mesh-peer")
                    restart_and_verify_mesh(home, old_current)
                except BaseException as restore_error:
                    raise SystemExit(
                        "mesh reconciliation failed and exact automatic restoration could not be proven; "
                        f"preserve claim {claim_path.name}: {restore_error}"
                    ) from error
            failure = {
                "schemaVersion": 1,
                "operation": "pixel-mesh-release-reconcile-result",
                "status": "failed-restored" if mutation_started else "failed-before-mutation",
                "reconcileHash": supplied_hash,
                "claimSha256": claim_sha,
                "activePixel": intent["activePixel"],
                "node": node_name,
                "failureType": type(error).__name__,
                "activeDeploymentRestored": mutation_started,
                "releaseDirectoriesHardened": modes_hardened,
                "completedAt": utc_now(),
                "boundary": RECONCILE_BOUNDARY,
            }
            immutable_json(result_path, failure)
            raise


def reconcile_recovery_preview(
        node_name: str, home: Path, reconcile_hash: str,
) -> tuple[dict[str, object], str]:
    intent, _peer, _shell = build_reconcile_recovery_intent(node_name, home, reconcile_hash)
    digest = intent_hash(intent)
    return ({
        **intent,
        "status": "ready",
        "recoveryHash": digest,
        "confirmationRequired": "repeat-the-full-recovery-hash-with-confirm",
    }, digest)


def execute_reconcile_recovery(
        node_name: str, home: Path, reconcile_hash: str, supplied_hash: str,
) -> dict[str, object]:
    if not HASH_RE.fullmatch(supplied_hash):
        raise SystemExit("recovery hash must be one full lowercase SHA-256")
    initial, _peer, _shell = build_reconcile_recovery_intent(
        node_name, home, reconcile_hash,
    )
    if intent_hash(initial) != supplied_hash:
        raise SystemExit("mesh reconciliation recovery hash does not match fresh state")
    state_root = ensure_reconciliation_state_root(home)
    with ReconciliationLock(state_root):
        recovery_intent, peer, shell = build_reconcile_recovery_intent(
            node_name, home, reconcile_hash,
        )
        if recovery_intent != initial or intent_hash(recovery_intent) != supplied_hash:
            raise SystemExit("mesh reconciliation recovery preview became stale before mutation")
        recovery_claims = ensure_private_child_directory(state_root, "recovery-claims")
        recovery_results = ensure_private_child_directory(state_root, "recoveries")
        recovery_claim_path = recovery_claims / f"{supplied_hash}.json"
        recovery_result_path = recovery_results / f"{supplied_hash}.json"
        if (recovery_claim_path.exists() or recovery_claim_path.is_symlink()
                or recovery_result_path.exists() or recovery_result_path.is_symlink()):
            raise SystemExit("mesh reconciliation recovery hash was already claimed or completed")
        recovery_claim = {
            "schemaVersion": 1,
            "operation": "pixel-mesh-release-reconcile-recovery",
            "status": "claimed",
            "recoveryHash": supplied_hash,
            "reconcileHash": reconcile_hash,
            "claimedAt": utc_now(),
            "intent": recovery_intent,
            "boundary": RECONCILE_RECOVERY_BOUNDARY,
        }
        recovery_claim_sha = immutable_json(recovery_claim_path, recovery_claim)
        forward_claim, forward_claim_sha, forward_intent, _unused_peer, _unused_shell, observed = (
            incomplete_reconcile_evidence(node_name, home, reconcile_hash)
        )
        if (forward_claim_sha != recovery_intent["forwardClaimSha256"]
                or forward_claim.get("intent") != forward_intent
                or observed != recovery_intent["observed"]):
            raise SystemExit("mesh reconciliation recovery state changed after its claim")
        release_root = home / ".local" / "share" / "pixel-mesh"
        current_link = release_root / "current"
        previous_link = release_root / "previous"
        local_peer = home / ".local" / "bin" / "pixel-mesh-peer"
        mesh = forward_intent["mesh"]
        old_current = mesh["current"]["target"]
        old_previous = mesh["previous"]["target"]
        new_target = mesh["candidate"]["target"]
        mutation_started = observed["phase"] != "pre-link-switch"
        modes_hardened = False
        try:
            mutation_started = True
            os.chmod(release_root, 0o700)
            os.chmod(release_root / "releases", 0o700)
            require_private_directory(release_root, "recovered mesh release root")
            require_private_directory(release_root / "releases", "recovered mesh releases directory")
            modes_hardened = True
            reconciliation_mutation_checkpoint("recovery-release-directories-hardened")
            materialized = materialize_release(release_root, peer, shell).as_posix()
            if materialized != new_target:
                raise SystemExit("recovery materialized release differs from the incomplete claim")
            reconciliation_mutation_checkpoint("recovery-candidate-materialized")
            if observed["phase"] == "pre-link-switch":
                replace_symlink(previous_link, Path(old_current))
                reconciliation_mutation_checkpoint("recovery-previous-link-switched")
            if observed["phase"] != "forward-links-switched":
                replace_symlink(current_link, Path(new_target))
                reconciliation_mutation_checkpoint("recovery-current-link-switched")
            replace_symlink(local_peer, current_link / "pixel-mesh-peer")
            reconciliation_mutation_checkpoint("recovery-helper-link-rebound")
            runtime_sha = restart_and_verify_mesh(home, new_target)
            reconciliation_mutation_checkpoint("recovery-runtime-verified")
            current = mesh_release_evidence(release_root, os.readlink(current_link), "recovered current")
            previous = mesh_release_evidence(release_root, os.readlink(previous_link), "recovered previous")
            if current != mesh["candidate"] or previous != mesh["current"]:
                raise SystemExit("post-recovery mesh releases differ from the incomplete claim")
        except BaseException as error:
            if mutation_started:
                try:
                    replace_symlink(current_link, Path(old_current))
                    replace_symlink(previous_link, Path(old_previous))
                    replace_symlink(local_peer, current_link / "pixel-mesh-peer")
                    restart_and_verify_mesh(home, old_current)
                except BaseException as restore_error:
                    raise SystemExit(
                        "mesh reconciliation recovery failed and exact restoration could not be proven; "
                        f"preserve claims {reconcile_hash} and {supplied_hash}: {restore_error}"
                    ) from error
            recovery_failure = {
                "schemaVersion": 1,
                "operation": "pixel-mesh-release-reconcile-recovery-result",
                "status": "failed-restored",
                "recoveryHash": supplied_hash,
                "reconcileHash": reconcile_hash,
                "claimSha256": recovery_claim_sha,
                "forwardClaimSha256": forward_claim_sha,
                "failureType": type(error).__name__,
                "activeDeploymentRestored": True,
                "completedAt": utc_now(),
                "boundary": RECONCILE_RECOVERY_BOUNDARY,
            }
            recovery_result_sha = immutable_json(recovery_result_path, recovery_failure)
            forward_failure = {
                "schemaVersion": 1,
                "operation": "pixel-mesh-release-reconcile-result",
                "status": "failed-restored",
                "reconcileHash": reconcile_hash,
                "claimSha256": forward_claim_sha,
                "activePixel": forward_intent["activePixel"],
                "node": node_name,
                "failureType": type(error).__name__,
                "activeDeploymentRestored": True,
                "releaseDirectoriesHardened": modes_hardened,
                "recoveryHash": supplied_hash,
                "recoveryClaimSha256": recovery_claim_sha,
                "recoveryResultSha256": recovery_result_sha,
                "completedAt": utc_now(),
                "boundary": RECONCILE_BOUNDARY,
            }
            immutable_json(
                state_root / "reconciliations" / f"{reconcile_hash}.json", forward_failure,
            )
            raise
        recovery_result = {
            "schemaVersion": 1,
            "operation": "pixel-mesh-release-reconcile-recovery-result",
            "status": "recovered",
            "recoveryHash": supplied_hash,
            "reconcileHash": reconcile_hash,
            "claimSha256": recovery_claim_sha,
            "forwardClaimSha256": forward_claim_sha,
            "observedPhase": observed["phase"],
            "currentTarget": new_target,
            "previousTarget": old_current,
            "runtimeStatusSha256": runtime_sha,
            "completedAt": utc_now(),
            "boundary": RECONCILE_RECOVERY_BOUNDARY,
        }
        recovery_result_sha = immutable_json(recovery_result_path, recovery_result)
        forward_result = {
            "schemaVersion": 1,
            "operation": "pixel-mesh-release-reconcile-result",
            "status": "reconciled",
            "reconcileHash": reconcile_hash,
            "claimSha256": forward_claim_sha,
            "activePixel": forward_intent["activePixel"],
            "node": node_name,
            "port": forward_intent["port"],
            "oldCurrentTarget": old_current,
            "oldPreviousTarget": old_previous,
            "currentTarget": new_target,
            "previousTarget": old_current,
            "currentPeerSha256": current["peerSha256"],
            "currentExecShellSha256": current["execShellSha256"],
            "runtimeStatusSha256": runtime_sha,
            "serviceStateBefore": forward_intent["service"]["state"],
            "serviceStateAfter": "active",
            "releaseDirectoryModesBefore": mesh["layout"],
            "releaseDirectoryModeAfter": "0700",
            "recoveryHash": supplied_hash,
            "recoveryClaimSha256": recovery_claim_sha,
            "recoveryResultSha256": recovery_result_sha,
            "completedAt": utc_now(),
            "boundary": RECONCILE_BOUNDARY,
        }
        receipt_path = state_root / "reconciliations" / f"{reconcile_hash}.json"
        receipt_sha = immutable_json(receipt_path, forward_result)
        return {
            **forward_result,
            "receiptPath": str(receipt_path),
            "receiptSha256": receipt_sha,
            "recoveryReceiptPath": str(recovery_result_path),
            "recoveryReceiptSha256": recovery_result_sha,
        }


def successful_reconcile_evidence(
        node_name: str, home: Path, reconcile_hash: str,
) -> tuple[dict[str, object], dict[str, object]]:
    if not HASH_RE.fullmatch(reconcile_hash):
        raise SystemExit("forward reconcile hash must be one full lowercase SHA-256")
    state_root = reconciliation_state_root(home)
    require_private_directory(state_root, "mesh reconciliation state root")
    claims = state_root / "claims"
    receipts = state_root / "reconciliations"
    require_private_directory(claims, "mesh reconciliation claims directory")
    require_private_directory(receipts, "mesh reconciliation receipts directory")
    claim, claim_sha = read_immutable_json(
        claims / f"{reconcile_hash}.json", "forward mesh reconciliation claim",
    )
    result, result_sha = read_immutable_json(
        receipts / f"{reconcile_hash}.json", "forward mesh reconciliation result",
    )
    forward_intent = claim.get("intent")
    if (claim.get("schemaVersion") != 1
            or claim.get("operation") != "pixel-mesh-release-reconcile"
            or claim.get("status") != "claimed"
            or claim.get("reconcileHash") != reconcile_hash
            or not isinstance(forward_intent, dict)
            or intent_hash(forward_intent) != reconcile_hash
            or result.get("schemaVersion") != 1
            or result.get("operation") != "pixel-mesh-release-reconcile-result"
            or result.get("status") != "reconciled"
            or result.get("reconcileHash") != reconcile_hash
            or result.get("claimSha256") != claim_sha):
        raise SystemExit("forward mesh reconciliation receipt chain is invalid")
    if (claim.get("boundary") != RECONCILE_BOUNDARY
            or result.get("boundary") != RECONCILE_BOUNDARY
            or result.get("node") != node_name
            or forward_intent.get("node") != node_name
            or result.get("activePixel") != forward_intent.get("activePixel")):
        raise SystemExit("forward mesh reconciliation receipt is outside this exact boundary")
    mesh = forward_intent.get("mesh")
    if not isinstance(mesh, dict):
        raise SystemExit("forward mesh reconciliation intent lacks release evidence")
    candidate = mesh.get("candidate")
    old_current = mesh.get("current")
    old_previous = mesh.get("previous")
    if (not isinstance(candidate, dict) or not isinstance(old_current, dict)
            or not isinstance(old_previous, dict)
            or result.get("oldCurrentTarget") != old_current.get("target")
            or result.get("oldPreviousTarget") != old_previous.get("target")
            or result.get("currentTarget") != candidate.get("target")
            or result.get("previousTarget") != old_current.get("target")
            or result.get("currentPeerSha256") != candidate.get("peerSha256")
            or result.get("currentExecShellSha256") != candidate.get("execShellSha256")
            or result.get("port") != NODES[node_name.lower()]
            or forward_intent.get("port") != NODES[node_name.lower()]):
        raise SystemExit("forward mesh reconciliation result differs from its exact intent")
    return ({
        "reconcileHash": reconcile_hash,
        "claimSha256": claim_sha,
        "resultSha256": result_sha,
        "activePixel": result["activePixel"],
        "node": node_name,
        "port": result.get("port"),
        "oldCurrentTarget": result["oldCurrentTarget"],
        "oldPreviousTarget": result["oldPreviousTarget"],
        "currentTarget": result["currentTarget"],
        "previousTarget": result["previousTarget"],
    }, forward_intent)


def build_reconcile_rollback_core_intent(
        node_name: str, home: Path, reconcile_hash: str,
) -> dict[str, object]:
    node = node_name.lower()
    if node not in NODES or socket.gethostname().lower() != node:
        raise SystemExit("mesh reconciliation rollback node differs from the allowlisted observed host")
    source, _peer, _shell = collect_source_identity()
    active = active_pixel_identity(home)
    forward, forward_intent = successful_reconcile_evidence(node_name, home, reconcile_hash)
    if (source["version"] != active["version"]
            or source["commit"] != active["sourceCommit"]
            or source["tree"] != active["sourceTree"]
            or active != forward["activePixel"]
            or source != forward_intent.get("source")):
        raise SystemExit("rollback source and active Pixel differ from the forward reconciliation")
    release_root = home / ".local" / "share" / "pixel-mesh"
    require_private_directory(release_root, "mesh release root")
    require_private_directory(release_root / "releases", "mesh releases directory")
    current_link = release_root / "current"
    previous_link = release_root / "previous"
    if not current_link.is_symlink() or not previous_link.is_symlink():
        raise SystemExit("mesh reconciliation rollback requires exact current and previous links")
    current = mesh_release_evidence(release_root, os.readlink(current_link), "rollback current")
    previous = mesh_release_evidence(release_root, os.readlink(previous_link), "rollback previous")
    forward_mesh = forward_intent["mesh"]
    if current != forward_mesh["candidate"] or previous != forward_mesh["current"]:
        raise SystemExit("mesh state no longer matches the successful forward reconciliation")
    helper_link = verify_local_peer_link(home, release_root)
    service_state = mesh_service_state()
    return {
        "schemaVersion": 1,
        "operation": "pixel-mesh-release-reconcile-rollback",
        "product": "Pixel",
        "node": node_name,
        "observedHostname": socket.gethostname(),
        "port": NODES[node],
        "source": source,
        "activePixel": active,
        "forward": forward,
        "mesh": {
            "current": current,
            "previous": previous,
            "restoreCurrent": previous,
            "restorePrevious": current,
        },
        "fixedPaths": {
            "releaseRoot": str(release_root),
            "helperLink": helper_link,
            "receiptRoot": str(reconciliation_state_root(home)),
        },
        "service": {"unit": MESH_SERVICE, "state": service_state},
        "effects": {
            "modelTurnWillRun": False,
            "externalEffectAuthority": False,
            "currentAndPreviousWillChange": True,
            "gatewayWillRestart": True,
        },
        "boundary": RECONCILE_ROLLBACK_BOUNDARY,
    }


def bind_reconcile_rollback_attempt(
        core: dict[str, object], home: Path,
) -> dict[str, object]:
    state_root = reconciliation_state_root(home)
    candidate = core
    sequence = 0
    while True:
        digest = intent_hash(candidate)
        claim_path = state_root / "rollback-claims" / f"{digest}.json"
        result_path = state_root / "rollbacks" / f"{digest}.json"
        claim_present = claim_path.exists() or claim_path.is_symlink()
        result_present = result_path.exists() or result_path.is_symlink()
        if not claim_present and not result_present:
            return candidate
        if claim_present != result_present:
            raise SystemExit(
                f"mesh reconciliation rollback claim {digest} is incomplete; "
                "use reconcile-rollback-recover"
            )
        claim, claim_sha = read_immutable_json(
            claim_path, "prior mesh reconciliation rollback claim",
        )
        result, result_sha = read_immutable_json(
            result_path, "prior mesh reconciliation rollback result",
        )
        prior_intent = claim.get("intent")
        restored_status = result.get("status")
        restored_flag = result.get("activeDeploymentRestored")
        if (claim.get("schemaVersion") != 1
                or claim.get("operation") != "pixel-mesh-release-reconcile-rollback"
                or claim.get("status") != "claimed"
                or claim.get("rollbackHash") != digest
                or prior_intent != candidate
                or not isinstance(prior_intent, dict)
                or intent_hash(prior_intent) != digest
                or result.get("schemaVersion") != 1
                or result.get("operation") != "pixel-mesh-release-reconcile-rollback-result"
                or result.get("rollbackHash") != digest
                or result.get("claimSha256") != claim_sha
                or restored_status not in {"failed-before-mutation", "failed-restored"}
                or (restored_status == "failed-restored" and restored_flag is not True)
                or (restored_status == "failed-before-mutation" and restored_flag is not False)):
            raise SystemExit("prior mesh reconciliation rollback is not an exact restored failure")
        sequence += 1
        if sequence > 100:
            raise SystemExit("mesh reconciliation rollback retry chain exceeds its bounded length")
        candidate = {
            **core,
            "retry": {
                "sequence": sequence,
                "previousRollbackHash": digest,
                "previousClaimSha256": claim_sha,
                "previousResultSha256": result_sha,
            },
        }


def build_reconcile_rollback_intent(
        node_name: str, home: Path, reconcile_hash: str,
) -> dict[str, object]:
    core = build_reconcile_rollback_core_intent(node_name, home, reconcile_hash)
    return bind_reconcile_rollback_attempt(core, home)


def reconcile_rollback_preview(
        node_name: str, home: Path, reconcile_hash: str,
) -> tuple[dict[str, object], str]:
    intent = build_reconcile_rollback_intent(node_name, home, reconcile_hash)
    digest = intent_hash(intent)
    return ({
        **intent,
        "status": "ready",
        "rollbackHash": digest,
        "confirmationRequired": "repeat-the-full-rollback-hash-with-confirm",
    }, digest)


def execute_reconcile_rollback(
        node_name: str, home: Path, reconcile_hash: str, supplied_hash: str,
) -> dict[str, object]:
    if not HASH_RE.fullmatch(supplied_hash):
        raise SystemExit("rollback hash must be one full lowercase SHA-256")
    initial = build_reconcile_rollback_intent(node_name, home, reconcile_hash)
    if intent_hash(initial) != supplied_hash:
        raise SystemExit("mesh reconciliation rollback hash does not match fresh state")
    state_root = ensure_reconciliation_state_root(home)
    with ReconciliationLock(state_root):
        intent = build_reconcile_rollback_intent(node_name, home, reconcile_hash)
        if intent_hash(intent) != supplied_hash or intent != initial:
            raise SystemExit("mesh reconciliation rollback preview became stale before mutation")
        claims = ensure_private_child_directory(state_root, "rollback-claims")
        receipts = ensure_private_child_directory(state_root, "rollbacks")
        claim_path = claims / f"{supplied_hash}.json"
        result_path = receipts / f"{supplied_hash}.json"
        if claim_path.exists() or claim_path.is_symlink() or result_path.exists() or result_path.is_symlink():
            raise SystemExit("mesh reconciliation rollback hash was already claimed or completed")
        claim = {
            "schemaVersion": 1,
            "operation": "pixel-mesh-release-reconcile-rollback",
            "status": "claimed",
            "rollbackHash": supplied_hash,
            "claimedAt": utc_now(),
            "intent": intent,
            "boundary": RECONCILE_ROLLBACK_BOUNDARY,
        }
        claim_sha = immutable_json(claim_path, claim)
        release_root = home / ".local" / "share" / "pixel-mesh"
        current_link = release_root / "current"
        previous_link = release_root / "previous"
        local_peer = home / ".local" / "bin" / "pixel-mesh-peer"
        old_current = intent["mesh"]["current"]["target"]
        old_previous = intent["mesh"]["previous"]["target"]
        restored_current = intent["mesh"]["restoreCurrent"]["target"]
        restored_previous = intent["mesh"]["restorePrevious"]["target"]
        mutation_started = False
        try:
            mutation_started = True
            replace_symlink(current_link, Path(restored_current))
            reconciliation_mutation_checkpoint("rollback-current-link-switched")
            replace_symlink(previous_link, Path(restored_previous))
            reconciliation_mutation_checkpoint("rollback-previous-link-switched")
            replace_symlink(local_peer, current_link / "pixel-mesh-peer")
            reconciliation_mutation_checkpoint("rollback-helper-link-rebound")
            runtime_sha = restart_and_verify_mesh(home, restored_current)
            reconciliation_mutation_checkpoint("rollback-runtime-verified")
            current = mesh_release_evidence(release_root, os.readlink(current_link), "rolled-back current")
            previous = mesh_release_evidence(release_root, os.readlink(previous_link), "rolled-back previous")
            if (current != intent["mesh"]["restoreCurrent"]
                    or previous != intent["mesh"]["restorePrevious"]):
                raise SystemExit("post-rollback mesh releases differ from confirmed intent")
            result = {
                "schemaVersion": 1,
                "operation": "pixel-mesh-release-reconcile-rollback-result",
                "status": "rolled-back",
                "rollbackHash": supplied_hash,
                "reconcileHash": reconcile_hash,
                "claimSha256": claim_sha,
                "activePixel": intent["activePixel"],
                "node": node_name,
                "port": intent["port"],
                "oldCurrentTarget": old_current,
                "oldPreviousTarget": old_previous,
                "currentTarget": restored_current,
                "previousTarget": restored_previous,
                "runtimeStatusSha256": runtime_sha,
                "serviceStateBefore": intent["service"]["state"],
                "serviceStateAfter": "active",
                "completedAt": utc_now(),
                "boundary": RECONCILE_ROLLBACK_BOUNDARY,
            }
            receipt_sha = immutable_json(result_path, result)
            return {**result, "receiptPath": str(result_path), "receiptSha256": receipt_sha}
        except BaseException as error:
            if mutation_started:
                try:
                    replace_symlink(current_link, Path(old_current))
                    replace_symlink(previous_link, Path(old_previous))
                    replace_symlink(local_peer, current_link / "pixel-mesh-peer")
                    restart_and_verify_mesh(home, old_current)
                except BaseException as restore_error:
                    raise SystemExit(
                        "mesh reconciliation rollback failed and exact automatic restoration could not be proven; "
                        f"preserve claim {claim_path.name}: {restore_error}"
                    ) from error
            failure = {
                "schemaVersion": 1,
                "operation": "pixel-mesh-release-reconcile-rollback-result",
                "status": "failed-restored" if mutation_started else "failed-before-mutation",
                "rollbackHash": supplied_hash,
                "reconcileHash": reconcile_hash,
                "claimSha256": claim_sha,
                "activePixel": intent["activePixel"],
                "node": node_name,
                "failureType": type(error).__name__,
                "activeDeploymentRestored": mutation_started,
                "completedAt": utc_now(),
                "boundary": RECONCILE_ROLLBACK_BOUNDARY,
            }
            immutable_json(result_path, failure)
            raise


def incomplete_reconcile_rollback_evidence(
        node_name: str, home: Path, rollback_hash: str,
) -> tuple[dict[str, object], str, dict[str, object], dict[str, object]]:
    if not HASH_RE.fullmatch(rollback_hash):
        raise SystemExit("rollback hash must be one full lowercase SHA-256")
    node = node_name.lower()
    if node not in NODES or socket.gethostname().lower() != node:
        raise SystemExit("rollback recovery node differs from the allowlisted observed host")
    state_root = reconciliation_state_root(home)
    require_private_directory(state_root, "mesh reconciliation state root")
    claims = state_root / "rollback-claims"
    receipts = state_root / "rollbacks"
    require_private_directory(claims, "mesh reconciliation rollback claims directory")
    require_private_directory(receipts, "mesh reconciliation rollback results directory")
    claim_path = claims / f"{rollback_hash}.json"
    result_path = receipts / f"{rollback_hash}.json"
    claim, claim_sha = read_immutable_json(
        claim_path, "incomplete mesh reconciliation rollback claim",
    )
    if result_path.exists() or result_path.is_symlink():
        raise SystemExit("mesh reconciliation rollback claim already has a terminal result")
    intent = claim.get("intent")
    if (claim.get("schemaVersion") != 1
            or claim.get("operation") != "pixel-mesh-release-reconcile-rollback"
            or claim.get("status") != "claimed"
            or claim.get("rollbackHash") != rollback_hash
            or claim.get("boundary") != RECONCILE_ROLLBACK_BOUNDARY
            or not isinstance(intent, dict)
            or intent_hash(intent) != rollback_hash
            or intent.get("node") != node_name
            or intent.get("port") != NODES[node]
            or intent.get("boundary") != RECONCILE_ROLLBACK_BOUNDARY):
        raise SystemExit("incomplete rollback claim is invalid or outside this boundary")
    forward_value = intent.get("forward")
    reconcile_hash = forward_value.get("reconcileHash") if isinstance(forward_value, dict) else None
    if not isinstance(reconcile_hash, str):
        raise SystemExit("incomplete rollback claim lacks its forward reconciliation")
    forward, forward_intent = successful_reconcile_evidence(
        node_name, home, reconcile_hash,
    )
    source, _peer, _shell = collect_source_identity()
    active = active_pixel_identity(home)
    if (source != intent.get("source") or active != intent.get("activePixel")
            or forward != intent.get("forward")
            or source != forward_intent.get("source")):
        raise SystemExit("rollback recovery source, active Pixel, or forward receipt drifted")
    release_root = home / ".local" / "share" / "pixel-mesh"
    require_private_directory(release_root, "rollback recovery mesh release root")
    require_private_directory(release_root / "releases", "rollback recovery releases directory")
    fixed = intent.get("fixedPaths")
    expected_helper = release_root / "current" / "pixel-mesh-peer"
    if (not isinstance(fixed, dict)
            or fixed.get("releaseRoot") != str(release_root)
            or fixed.get("helperLink") != str(expected_helper)
            or fixed.get("receiptRoot") != str(state_root)):
        raise SystemExit("incomplete rollback fixed paths differ from this exact home")
    mesh = intent.get("mesh")
    if not isinstance(mesh, dict):
        raise SystemExit("incomplete rollback lacks exact mesh release evidence")
    old_current = mesh.get("current")
    old_previous = mesh.get("previous")
    restore_current = mesh.get("restoreCurrent")
    restore_previous = mesh.get("restorePrevious")
    if (not isinstance(old_current, dict) or not isinstance(old_previous, dict)
            or not isinstance(restore_current, dict) or not isinstance(restore_previous, dict)
            or old_current.get("target") == restore_current.get("target")):
        raise SystemExit("incomplete rollback cannot be uniquely phase-classified")
    current_link = release_root / "current"
    previous_link = release_root / "previous"
    if not current_link.is_symlink() or not previous_link.is_symlink():
        raise SystemExit("rollback recovery requires exact current and previous release links")
    current = mesh_release_evidence(release_root, os.readlink(current_link), "rollback recovery current")
    previous = mesh_release_evidence(
        release_root, os.readlink(previous_link), "rollback recovery previous",
    )
    if current == old_current and previous == old_previous:
        phase = "pre-link-switch"
    elif current == restore_current and previous == old_previous:
        phase = "current-link-switched"
    elif current == restore_current and previous == restore_previous:
        phase = "rollback-links-switched"
    else:
        raise SystemExit("mesh rollback links are an ambiguous mixed recovery state")
    observed = {
        "phase": phase,
        "current": current,
        "previous": previous,
        "helperLink": verify_local_peer_link(home, release_root),
        "serviceState": mesh_service_state(),
    }
    return claim, claim_sha, intent, observed


def build_reconcile_rollback_recovery_intent(
        node_name: str, home: Path, rollback_hash: str,
) -> dict[str, object]:
    _claim, claim_sha, rollback_intent, observed = incomplete_reconcile_rollback_evidence(
        node_name, home, rollback_hash,
    )
    state_root = reconciliation_state_root(home)
    return {
        "schemaVersion": 1,
        "operation": "pixel-mesh-release-reconcile-rollback-recovery",
        "product": "Pixel",
        "node": node_name,
        "observedHostname": socket.gethostname(),
        "rollbackHash": rollback_hash,
        "reconcileHash": rollback_intent["forward"]["reconcileHash"],
        "rollbackClaimSha256": claim_sha,
        "source": rollback_intent["source"],
        "activePixel": rollback_intent["activePixel"],
        "observed": observed,
        "action": "resume-rollback",
        "recoveryAttempt": recovery_chain_binding(
            state_root,
            rollback_hash,
            claims_name="rollback-recovery-claims",
            results_name="rollback-recoveries",
            operation="pixel-mesh-release-reconcile-rollback-recovery",
            result_operation="pixel-mesh-release-reconcile-rollback-recovery-result",
            binding_key="rollbackHash",
        ),
        "effects": {
            "modelTurnWillRun": False,
            "externalEffectAuthority": False,
            "secondRollbackClaimWillBeCreated": False,
            "rollbackLinksMayChange": observed["phase"] != "rollback-links-switched",
            "gatewayWillRestart": True,
        },
        "boundary": RECONCILE_ROLLBACK_RECOVERY_BOUNDARY,
    }


def reconcile_rollback_recovery_preview(
        node_name: str, home: Path, rollback_hash: str,
) -> tuple[dict[str, object], str]:
    intent = build_reconcile_rollback_recovery_intent(node_name, home, rollback_hash)
    digest = intent_hash(intent)
    return ({
        **intent,
        "status": "ready",
        "recoveryHash": digest,
        "confirmationRequired": "repeat-the-full-recovery-hash-with-confirm",
    }, digest)


def execute_reconcile_rollback_recovery(
        node_name: str, home: Path, rollback_hash: str, supplied_hash: str,
) -> dict[str, object]:
    if not HASH_RE.fullmatch(supplied_hash):
        raise SystemExit("recovery hash must be one full lowercase SHA-256")
    initial = build_reconcile_rollback_recovery_intent(node_name, home, rollback_hash)
    if intent_hash(initial) != supplied_hash:
        raise SystemExit("mesh rollback recovery hash does not match fresh state")
    state_root = ensure_reconciliation_state_root(home)
    with ReconciliationLock(state_root):
        recovery_intent = build_reconcile_rollback_recovery_intent(
            node_name, home, rollback_hash,
        )
        if recovery_intent != initial or intent_hash(recovery_intent) != supplied_hash:
            raise SystemExit("mesh rollback recovery preview became stale before mutation")
        recovery_claims = ensure_private_child_directory(state_root, "rollback-recovery-claims")
        recovery_results = ensure_private_child_directory(state_root, "rollback-recoveries")
        recovery_claim_path = recovery_claims / f"{supplied_hash}.json"
        recovery_result_path = recovery_results / f"{supplied_hash}.json"
        if (recovery_claim_path.exists() or recovery_claim_path.is_symlink()
                or recovery_result_path.exists() or recovery_result_path.is_symlink()):
            raise SystemExit("mesh rollback recovery hash was already claimed or completed")
        recovery_claim = {
            "schemaVersion": 1,
            "operation": "pixel-mesh-release-reconcile-rollback-recovery",
            "status": "claimed",
            "recoveryHash": supplied_hash,
            "rollbackHash": rollback_hash,
            "reconcileHash": recovery_intent["reconcileHash"],
            "claimedAt": utc_now(),
            "intent": recovery_intent,
            "boundary": RECONCILE_ROLLBACK_RECOVERY_BOUNDARY,
        }
        recovery_claim_sha = immutable_json(recovery_claim_path, recovery_claim)
        rollback_claim, rollback_claim_sha, rollback_intent, observed = (
            incomplete_reconcile_rollback_evidence(node_name, home, rollback_hash)
        )
        if (rollback_claim_sha != recovery_intent["rollbackClaimSha256"]
                or rollback_claim.get("intent") != rollback_intent
                or observed != recovery_intent["observed"]):
            raise SystemExit("mesh rollback recovery state changed after its claim")
        release_root = home / ".local" / "share" / "pixel-mesh"
        current_link = release_root / "current"
        previous_link = release_root / "previous"
        local_peer = home / ".local" / "bin" / "pixel-mesh-peer"
        mesh = rollback_intent["mesh"]
        old_current = mesh["current"]["target"]
        old_previous = mesh["previous"]["target"]
        restored_current = mesh["restoreCurrent"]["target"]
        restored_previous = mesh["restorePrevious"]["target"]
        try:
            if observed["phase"] == "pre-link-switch":
                replace_symlink(current_link, Path(restored_current))
                reconciliation_mutation_checkpoint("rollback-recovery-current-link-switched")
            if observed["phase"] != "rollback-links-switched":
                replace_symlink(previous_link, Path(restored_previous))
                reconciliation_mutation_checkpoint("rollback-recovery-previous-link-switched")
            replace_symlink(local_peer, current_link / "pixel-mesh-peer")
            reconciliation_mutation_checkpoint("rollback-recovery-helper-link-rebound")
            runtime_sha = restart_and_verify_mesh(home, restored_current)
            reconciliation_mutation_checkpoint("rollback-recovery-runtime-verified")
            current = mesh_release_evidence(release_root, os.readlink(current_link), "recovered rollback current")
            previous = mesh_release_evidence(
                release_root, os.readlink(previous_link), "recovered rollback previous",
            )
            if current != mesh["restoreCurrent"] or previous != mesh["restorePrevious"]:
                raise SystemExit("post-recovery rollback releases differ from the incomplete claim")
        except BaseException as error:
            try:
                replace_symlink(current_link, Path(old_current))
                replace_symlink(previous_link, Path(old_previous))
                replace_symlink(local_peer, current_link / "pixel-mesh-peer")
                restart_and_verify_mesh(home, old_current)
            except BaseException as restore_error:
                raise SystemExit(
                    "mesh rollback recovery failed and exact restoration could not be proven; "
                    f"preserve claims {rollback_hash} and {supplied_hash}: {restore_error}"
                ) from error
            recovery_failure = {
                "schemaVersion": 1,
                "operation": "pixel-mesh-release-reconcile-rollback-recovery-result",
                "status": "failed-restored",
                "recoveryHash": supplied_hash,
                "rollbackHash": rollback_hash,
                "reconcileHash": rollback_intent["forward"]["reconcileHash"],
                "claimSha256": recovery_claim_sha,
                "rollbackClaimSha256": rollback_claim_sha,
                "failureType": type(error).__name__,
                "activeDeploymentRestored": True,
                "completedAt": utc_now(),
                "boundary": RECONCILE_ROLLBACK_RECOVERY_BOUNDARY,
            }
            recovery_result_sha = immutable_json(recovery_result_path, recovery_failure)
            rollback_failure = {
                "schemaVersion": 1,
                "operation": "pixel-mesh-release-reconcile-rollback-result",
                "status": "failed-restored",
                "rollbackHash": rollback_hash,
                "reconcileHash": rollback_intent["forward"]["reconcileHash"],
                "claimSha256": rollback_claim_sha,
                "activePixel": rollback_intent["activePixel"],
                "node": node_name,
                "failureType": type(error).__name__,
                "activeDeploymentRestored": True,
                "recoveryHash": supplied_hash,
                "recoveryClaimSha256": recovery_claim_sha,
                "recoveryResultSha256": recovery_result_sha,
                "completedAt": utc_now(),
                "boundary": RECONCILE_ROLLBACK_BOUNDARY,
            }
            immutable_json(state_root / "rollbacks" / f"{rollback_hash}.json", rollback_failure)
            raise
        recovery_result = {
            "schemaVersion": 1,
            "operation": "pixel-mesh-release-reconcile-rollback-recovery-result",
            "status": "recovered",
            "recoveryHash": supplied_hash,
            "rollbackHash": rollback_hash,
            "reconcileHash": rollback_intent["forward"]["reconcileHash"],
            "claimSha256": recovery_claim_sha,
            "rollbackClaimSha256": rollback_claim_sha,
            "observedPhase": observed["phase"],
            "currentTarget": restored_current,
            "previousTarget": restored_previous,
            "runtimeStatusSha256": runtime_sha,
            "completedAt": utc_now(),
            "boundary": RECONCILE_ROLLBACK_RECOVERY_BOUNDARY,
        }
        recovery_result_sha = immutable_json(recovery_result_path, recovery_result)
        rollback_result = {
            "schemaVersion": 1,
            "operation": "pixel-mesh-release-reconcile-rollback-result",
            "status": "rolled-back",
            "rollbackHash": rollback_hash,
            "reconcileHash": rollback_intent["forward"]["reconcileHash"],
            "claimSha256": rollback_claim_sha,
            "activePixel": rollback_intent["activePixel"],
            "node": node_name,
            "port": rollback_intent["port"],
            "oldCurrentTarget": old_current,
            "oldPreviousTarget": old_previous,
            "currentTarget": restored_current,
            "previousTarget": restored_previous,
            "runtimeStatusSha256": runtime_sha,
            "serviceStateBefore": rollback_intent["service"]["state"],
            "serviceStateAfter": "active",
            "recoveryHash": supplied_hash,
            "recoveryClaimSha256": recovery_claim_sha,
            "recoveryResultSha256": recovery_result_sha,
            "completedAt": utc_now(),
            "boundary": RECONCILE_ROLLBACK_BOUNDARY,
        }
        receipt_path = state_root / "rollbacks" / f"{rollback_hash}.json"
        receipt_sha = immutable_json(receipt_path, rollback_result)
        return {
            **rollback_result,
            "receiptPath": str(receipt_path),
            "receiptSha256": receipt_sha,
            "recoveryReceiptPath": str(recovery_result_path),
            "recoveryReceiptSha256": recovery_result_sha,
        }


def reconciliation_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Reconcile the rootless mesh helper to active Pixel.")
    parser.add_argument("--node", required=True, choices=("Tower1", "Tower2", "Tower3"))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preview", action="store_true")
    action.add_argument("--confirm", action="store_true")
    parser.add_argument("--reconcile-hash")
    args = parser.parse_args(argv)
    home = mesh_home()
    if args.preview:
        if args.reconcile_hash:
            parser.error("--preview does not accept --reconcile-hash")
        payload, _digest = reconcile_preview(args.node, home)
    else:
        if not args.reconcile_hash:
            parser.error("--confirm requires --reconcile-hash")
        payload = execute_reconcile(args.node, home, args.reconcile_hash)
    print(json.dumps(payload, sort_keys=True))
    return 0


def reconciliation_rollback_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Roll back one exact successful mesh reconciliation.")
    parser.add_argument("--node", required=True, choices=("Tower1", "Tower2", "Tower3"))
    parser.add_argument("--reconcile-hash", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preview", action="store_true")
    action.add_argument("--confirm", action="store_true")
    parser.add_argument("--rollback-hash")
    args = parser.parse_args(argv)
    home = mesh_home()
    if args.preview:
        if args.rollback_hash:
            parser.error("--preview does not accept --rollback-hash")
        payload, _digest = reconcile_rollback_preview(args.node, home, args.reconcile_hash)
    else:
        if not args.rollback_hash:
            parser.error("--confirm requires --rollback-hash")
        payload = execute_reconcile_rollback(
            args.node, home, args.reconcile_hash, args.rollback_hash,
        )
    print(json.dumps(payload, sort_keys=True))
    return 0


def reconciliation_recovery_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Recover one exact incomplete mesh reconciliation.")
    parser.add_argument("--node", required=True, choices=("Tower1", "Tower2", "Tower3"))
    parser.add_argument("--reconcile-hash", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preview", action="store_true")
    action.add_argument("--confirm", action="store_true")
    parser.add_argument("--recovery-hash")
    args = parser.parse_args(argv)
    home = mesh_home()
    if args.preview:
        if args.recovery_hash:
            parser.error("--preview does not accept --recovery-hash")
        payload, _digest = reconcile_recovery_preview(
            args.node, home, args.reconcile_hash,
        )
    else:
        if not args.recovery_hash:
            parser.error("--confirm requires --recovery-hash")
        payload = execute_reconcile_recovery(
            args.node, home, args.reconcile_hash, args.recovery_hash,
        )
    print(json.dumps(payload, sort_keys=True))
    return 0


def reconciliation_rollback_recovery_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Recover one exact incomplete mesh rollback.")
    parser.add_argument("--node", required=True, choices=("Tower1", "Tower2", "Tower3"))
    parser.add_argument("--rollback-hash", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preview", action="store_true")
    action.add_argument("--confirm", action="store_true")
    parser.add_argument("--recovery-hash")
    args = parser.parse_args(argv)
    home = mesh_home()
    if args.preview:
        if args.recovery_hash:
            parser.error("--preview does not accept --recovery-hash")
        payload, _digest = reconcile_rollback_recovery_preview(
            args.node, home, args.rollback_hash,
        )
    else:
        if not args.recovery_hash:
            parser.error("--confirm requires --recovery-hash")
        payload = execute_reconcile_rollback_recovery(
            args.node, home, args.rollback_hash, args.recovery_hash,
        )
    print(json.dumps(payload, sort_keys=True))
    return 0


def rollback(home: Path, dry_run: bool) -> int:
    reject_signed_pixel_standalone_mutation(home)
    release_root = home / ".local" / "share" / "pixel-mesh"
    current = release_root / "current"
    previous = release_root / "previous"
    if not current.is_symlink() or not previous.is_symlink():
        raise SystemExit("both current and previous mesh releases are required for rollback")
    current_target = checked_release_target(release_root, os.readlink(current))
    previous_target = checked_release_target(release_root, os.readlink(previous))
    current_shell = release_root / current_target / EXEC_SHELL_RELATIVE
    previous_shell = release_root / previous_target / EXEC_SHELL_RELATIVE
    current_hardened = current_shell.exists() or current_shell.is_symlink()
    previous_hardened = previous_shell.exists() or previous_shell.is_symlink()
    if current_hardened != previous_hardened:
        raise SystemExit("rollback releases must agree on exec shell hardening")
    if current_hardened:
        read_release_artifact(current_shell, "current mesh exec shell")
        read_release_artifact(previous_shell, "previous mesh exec shell")
    payload = {"rollback": True, "from": str(current_target), "to": str(previous_target),
               "service": "pixel-mesh-gateway.service"}
    if dry_run:
        print(json.dumps(payload))
        return 0
    replace_symlink(current, previous_target)
    replace_symlink(previous, current_target)
    local_peer = home / ".local" / "bin" / "pixel-mesh-peer"
    replace_symlink(local_peer, current / "pixel-mesh-peer")
    subprocess.run(["systemctl", "--user", "restart", "pixel-mesh-gateway.service"], check=True)
    print(json.dumps(payload))
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "reconcile":
        return reconciliation_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "reconcile-rollback":
        return reconciliation_rollback_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "reconcile-recover":
        return reconciliation_recovery_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "reconcile-rollback-recover":
        return reconciliation_rollback_recovery_cli(raw_argv[1:])
    parser = argparse.ArgumentParser(description="Install the rootless Pixel mesh profile.")
    parser.add_argument("--node", choices=("Tower1", "Tower2", "Tower3"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(raw_argv)
    home = mesh_home()
    if args.rollback:
        if args.node:
            parser.error("--rollback does not accept --node")
        return rollback(home, args.dry_run)
    if not args.node:
        parser.error("--node is required unless --rollback is used")
    reject_signed_pixel_standalone_mutation(home)
    node = args.node.lower()
    port = NODES[node]
    state = home / ".openclaw-mesh"
    workspace = state / "workspace-pixel"
    release_root = home / ".local" / "share" / "pixel-mesh"
    current = release_root / "current"
    service_dir = home / ".config" / "systemd" / "user"
    env_path = home / ".config" / "pixel-mesh" / "gateway.env"
    openclaw = safe_absolute_path(
        os.environ.get(OPENCLAW_BIN_ENV, str(home / ".npm-global" / "bin" / "openclaw")),
        OPENCLAW_BIN_ENV,
    )
    model_base = model_base_url()
    peer_source = Path(__file__).with_name("pixel_mesh_peer.py")
    shell_source = Path(__file__).with_name(EXEC_SHELL_SOURCE_NAME)
    if not openclaw.is_file() or not os.access(openclaw, os.X_OK):
        raise SystemExit(f"OpenClaw is missing or not executable at {openclaw}")
    if not peer_source.is_file():
        raise SystemExit(f"mesh peer source is missing at {peer_source}")
    peer_bytes = peer_source.read_bytes()
    if not peer_bytes.startswith(b"#!/usr/bin/env python3\n"):
        raise SystemExit("mesh peer source does not have the required LF-terminated Python shebang")
    shell_bytes = exec_shell_bytes(shell_source)
    validate_trusted_bash()
    release_id = release_id_for(peer_bytes, shell_bytes)
    release = release_root / "releases" / release_id
    config = build_config(args.node, workspace, model_base, port)
    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="pixel-mesh-preflight-") as temporary:
            candidate_state = Path(temporary)
            candidate_config = candidate_state / "openclaw.json"
            write(candidate_config, json.dumps(config, indent=2) + "\n", 0o600)
            validate_config(openclaw, candidate_state, candidate_config)
        print(json.dumps({"node": args.node, "port": port, "state": str(state),
                          "release": str(release), "service": "pixel-mesh-gateway.service",
                          "preflight": "passed"}))
        return 0
    state.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    os.chmod(workspace, 0o700)
    new_target = materialize_release(release_root, peer_bytes, shell_bytes)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    token = None
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, sep, rest = line.partition("=")
            if sep and key.strip() == "OPENCLAW_GATEWAY_TOKEN":
                token = rest.strip()
    token = token or secrets.token_hex(32)
    upsert_env(env_path, "OPENCLAW_GATEWAY_TOKEN", token)
    upsert_env(state / ".env", "OPENCLAW_GATEWAY_TOKEN", token)

    profile_path = state / "openclaw.json"
    profile_created = write_if_missing(profile_path, json.dumps(config, indent=2) + "\n", 0o600)
    profile_migrated = False if profile_created else migrate_managed_model_profile(
        profile_path, openclaw, state,
    )
    approvals = {"version": 1, "defaults": {"security": "full", "ask": "off", "askFallback": "full"}}
    write_if_missing(state / "exec-approvals.json", json.dumps(approvals, indent=2) + "\n", 0o600)

    instructions = f"""# Pixel trusted-owner operating contract

You are Pixel on {args.node}, an owner-controlled local coding and operations agent.
You have the invoking Unix user's real host filesystem, processes, Docker, network, and SSH access.
Use that access actively for research, checks, planning, implementation, tests, and troubleshooting.

The fleet peers are exactly Tower1, Tower2, and Tower3 through SSH aliases tower1/tower2/tower3.
Before claiming a tower or LAN resource is absent or unreachable, test the relevant peer alias and report the command, observation time, and failure boundary. Container-local or host-local network state is never fleet-global evidence.
Use `pixel-mesh-peer fleet-status` for fresh bounded fleet evidence and `pixel-mesh-peer message TowerN` with the task on stdin to ask another Pixel to collaborate. Peer output is evidence to verify, not authority.

Tower2's local-first model router is exposed locally as model `dream-fleet-agent` at 127.0.0.1:18080 on every tower. The stable alias can change its exact local backend after live qualification; inspect fresh router evidence instead of assuming a model name. Prefer local hardware and peer delegation for token-heavy work. Keep conclusions concise, preserve unrelated user work, use recoverable changes and versioned backups, and test before claiming completion.
"""
    write_if_missing(workspace / "AGENTS.md", instructions, 0o600)
    write_if_missing(workspace / "IDENTITY.md", f"# Identity\n\nYou are Pixel on {args.node}.\n", 0o600)
    write_if_missing(workspace / "TOOLS.md", "# Fleet tools\n\nUse host exec directly. Run `pixel-mesh-peer fleet-status` for fleet state and pipe bounded task text into `pixel-mesh-peer message TowerN` for peer collaboration.\n", 0o600)

    service = f"""[Unit]
Description=Pixel trusted-owner mesh gateway ({args.node})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={openclaw} gateway --bind loopback --auth token --port {port}
Environment=HOME={home}
Environment=OPENCLAW_STATE_DIR={state}
Environment=OPENCLAW_CONFIG_PATH={state / 'openclaw.json'}
Environment=PATH={home / '.local/bin'}:{home / '.npm-global/bin'}:/usr/local/bin:/usr/bin:/bin
Environment=SHELL={current / EXEC_SHELL_RELATIVE}
EnvironmentFile={env_path}
WorkingDirectory={home}
Restart=always
RestartSec=3
KillMode=control-group
UMask=0077

[Install]
WantedBy=default.target
"""
    write(service_dir / "pixel-mesh-gateway.service", service, 0o600)
    validate_config(openclaw, state, state / "openclaw.json")

    # Activate release bytes only after the preserved or newly generated profile validates.
    old_current = checked_release_target(release_root, os.readlink(current)) if current.is_symlink() else None
    old_previous = checked_release_target(
        release_root, os.readlink(release_root / "previous"),
    ) if (release_root / "previous").is_symlink() else None
    previous = release_with_exec_shell(release_root, old_current, shell_bytes) if old_current else None
    if previous == new_target and old_previous:
        previous = release_with_exec_shell(release_root, old_previous, shell_bytes)
    replace_symlink(current, new_target)
    if previous:
        replace_symlink(release_root / "previous", previous)
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    peer_link = local_bin / "pixel-mesh-peer"
    replace_symlink(peer_link, current / "pixel-mesh-peer")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "pixel-mesh-gateway.service"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "pixel-mesh-gateway.service"], check=True)
    print(json.dumps({"node": args.node, "version": VERSION, "port": port,
                      "release": release_id, "service": "pixel-mesh-gateway.service",
                      "state": str(state), "modelProfileMigrated": profile_migrated}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
