#!/usr/bin/env python3
"""Privileged migration-journal custody helper (runs as root in production via sudo).

This is the single authority for the root-owned mode-0600 migration transaction journal inside
the fixed root-owned mode-0700 custody directory. It is invoked by the non-root restore
orchestrator through ``sudo`` so that the orchestrator can never test availability or write
inside the root-0700 custody. Every operation is descriptor-bound (dirfd + O_NOFOLLOW) and
serialized by an exclusive ``flock`` on the custody directory; it never follows or chowns an
attacker-controlled path and never GNU-installs over a journal. Production requires the root
euid and invokes only fixed absolute trusted binaries (``/usr/bin/systemctl``,
``/usr/bin/rm``, ``/usr/bin/mv``) directly with no sudo wrapper, no PATH lookup, and no
environment override. Every armed/progress/commit/rollback/finalization state transition is a
crash-safe atomic replacement (O_EXCL 0600 temp + fsync + renameat via dirfds + custody-dir
fsync), never an in-place ftruncate that a power loss could leave truncated.

Subcommands (all invoked as root via sudo):
  ensure    CUSTODY
            Create/validate the root-owned mode-0700 custody directory through a bound
            directory descriptor and hold an exclusive flock on it.
  reserve   CUSTODY JOURNAL CONTRACT BACKUP_SHA
            Atomically create the exact journal basename (O_CREAT|O_EXCL|O_NOFOLLOW, mode
            0600) under the custody lock and write an exact reserved state bound to the
            authenticated contract, exact backup SHA256, and the fixed source/target versions
            (SOURCE_PIXEL/TARGET_PIXEL). O_EXCL means EVERY pre-existing marker is refused, including a stale
            reservation for the same contract: there is no implicit stale reclaim in normal
            activation. A per-invocation cryptographic reservation token is generated and
            printed to stdout; arm and abort must present that exact token. Ownership of a
            reservation is therefore bound to the invoking restore process, so a concurrent
            same-contract activation can never reclaim or arm another invocation's reservation.
  arm       CUSTODY JOURNAL TOKEN CONTRACT BACKUP_SHA COUNT DEST... OLD... TEMP... HADOLD...
            UNITS... [SPECFILE]
            Atomically replace the exact reserved marker with the fully armed transaction,
            failing closed unless the exact token, contract, and backup identity match. The
            full transaction is validated with the same exact path/sibling/contract/unit
            validator used by commit and rollback BEFORE anything is recorded. Arm runs
            BEFORE any live destination rename and fails closed unless every hadOld=1
            destination still exists, every hadOld=0 destination is still absent, no oldPath
            pre-exists, and every prepared temporary path is present and a safe top-level
            temp root (a non-symlink directory or a regular single-link file). Records
            per-root old-snapshot evidence captured from each live
            destination while it is still at destination (inode+device+type, which follows
            the content to oldPath if moved) for exact recognition after a sibling rename.
            An optional trailing SPECFILE (an absolute path to a strict JSON document holding
            exactly a deploymentItems array) extends the same armed transaction to also
            protect deployment-state transitions in addition to the private roots: the active
            current symlink, selected single-link regular config/workspace/installed-unit
            files (each present or absent). Each item is a fixed, explicitly validated
            absolute normalized non-root deployment path (rejecting arbitrary/broad/root/
            glob/traversal/symlink-escape inputs) with an exact derived sibling oldPath and
            an exact prepared sibling newPath carrying the target object. Before any mutation
            arm freezes BOTH the live old state (type/owner/mode/device/inode/content-hash/
            target from the live path) and the prepared new object (newEvidence), so install
            detects prepared-new tamper and rollback restores the exact pre-mutation state;
            per-item durable rollback progress and idempotent commit cleanup are shared with
            the private roots under the same exact service quiescence/restart state.
  abort     CUSTODY JOURNAL TOKEN CONTRACT BACKUP_SHA
            Remove only the exact reserved inode if and only if it is still the reserved marker
            for this exact token/contract/backup. Never erases an armed or foreign transaction.
  inspect   CUSTODY JOURNAL
            Read-only validation and truthful JSON summary of the journal/reserved state. No
            state is mutated.
  install   CUSTODY JOURNAL
            Helper-owned idempotent install/swap for every armed deployment item, by bound
            parent dirfd only: move the live old object at path to oldPath when present and
            the prepared new object from newPath to path, fsync after each mutation, and
            persist the durable per-item install/swap state machine before and after every
            step. A crash at any boundary is classifiable and resumable/rollback-safe; the
            next install resumes from the actual filesystem state. This is the ONLY path
            that mutates a journal-managed live deployment path.
  commit    CUSTODY JOURNAL
            Finalize an armed migration swap: mark committed durably BEFORE any destructive
            cleanup and delete the old snapshots. Terminal commit never restarts services
            (they are already running and passed the outer verify) and marks finalization
            complete truthfully without a disruptive restart. Idempotent and resumable; a
            cleanup failure is reported truthfully and never triggers rollback.
  rollback  CUSTODY JOURNAL
            Undo an armed migration swap. The exact validated units are quiesced (stopped and
            verified inactive) BEFORE any filesystem mutation; if any stop or verification
            fails, every validated unit is restarted best-effort and no mutation or rollback
            progress is recorded. Then each root is restored using exact old evidence,
            progress is persisted per root, and services are restarted/finalized idempotently.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from typing import Any

MIGRATION_JOURNAL_BASENAME = re.compile(r"^[A-Za-z0-9._-]+$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
TOKEN_RE = re.compile(r"^[a-f0-9]{32}$")
MAX_JSON_BYTES = 2 * 1024 * 1024
# Bounded intake cap for a single staged target deployment candidate (config/env/unit/workspace
# files are small; a cap prevents an attacker from staging an unbounded object).
MAX_STAGE_CANDIDATE_BYTES = 8 * 1024 * 1024
RESERVED_KIND = "pixel-restore-migration-reserved"
JOURNAL_KIND = "pixel-restore-migration-journal"
VERSION_RE = re.compile(r"^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$")
SOURCE_PIXEL = "3.2.2"
_RELEASE_VERSION_MAX_BYTES = 64
_RELEASE_MANIFEST_MAX_BYTES = 1024 * 1024


def _release_identity(entry) -> tuple:
    """Stable release-identity metadata proven equal across pre fstat, post fstat, and path
    lstat: device, inode, regular file type, single link, size, mtime_ns, ctime_ns."""
    return (entry.st_dev, entry.st_ino, stat.S_ISREG(entry.st_mode), entry.st_nlink,
            entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns)


def _read_release_file(repo: str, name: str, *, maximum: int) -> bytes:
    """Secure in-tree release-identity read, mirroring the helper's file-custody style.

    Opens with O_NOFOLLOW + O_CLOEXEC, fstats a regular single-link bounded file, then loops
    os.read until exactly the pre-read st_size bytes are captured (rejecting early EOF and
    any growth), fstats the still-open descriptor again, and finally lstat-verifies the path.
    The pre fstat, post fstat, and path lstat must all agree on the same stable identity
    metadata - device, inode, regular file type, single link, size, and mtime/ctime - so an
    in-place truncate/replace/write race fails closed, not only a pathname swap. This proves
    only bounded single-link regular-file descriptor-path identity/coherence; it does not by
    itself prove ownership or parent-directory custody, and it does not authenticate the
    source or release tree - authenticity/ownership/directory custody come from the later
    exact-source and release-tree gates."""
    if not (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_CLOEXEC")):
        raise RuntimeError("platform lacks O_NOFOLLOW/O_CLOEXEC release-identity guarantees")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    path = os.path.join(repo, name)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"release {name} is unavailable or unsafe") from exc
    try:
        pre = os.fstat(descriptor)
        if not stat.S_ISREG(pre.st_mode) or pre.st_nlink != 1 \
                or not 0 <= pre.st_size <= maximum:
            raise RuntimeError(f"release {name} is not a bounded single-link regular file")
        payload = bytearray()
        while len(payload) < pre.st_size:
            chunk = os.read(descriptor, pre.st_size - len(payload))
            if not chunk:
                raise RuntimeError(f"release {name} ended early during read")
            payload += chunk
        if os.read(descriptor, 1):
            raise RuntimeError(f"release {name} grew during read")
        post = os.fstat(descriptor)
        if _release_identity(pre) != _release_identity(post):
            raise RuntimeError(f"release {name} changed during read")
        current = os.lstat(path)
        if _release_identity(post) != _release_identity(current):
            raise RuntimeError(f"release {name} changed during read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _parse_release_manifest(payload: bytes) -> dict:
    """Strict release-manifest JSON that fails closed on duplicate keys at any depth and on
    non-finite numbers, so a crafted manifest cannot silently bind a second value."""
    def reject_duplicates(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise RuntimeError("release manifest contains duplicate fields")
            value[key] = child
        return value
    try:
        value = json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _v: (_ for _ in ()).throw(
                RuntimeError("release manifest contains a non-finite number")),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("release manifest is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("release manifest is not an object")
    return value


def _load_release_contract() -> str:
    """Derive the exact legacy migration target from the COHERENT in-tree release identity
    (VERSION == RELEASE-MANIFEST pixel == legacy target) of the release tree that physically
    contains this tool.

    This loader establishes coherence only, never authenticity: incoherent field or source
    substitution fails closed, while a coherent future signed release identity is accepted
    only as a release identity. Authenticity and custody come from the exact clean/signed
    release and release-tree gates, never from these agreeing raw files. No CLI/env override,
    basename inference, loose semver, or fallback."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        version = _read_release_file(repo, "VERSION", maximum=_RELEASE_VERSION_MAX_BYTES) \
            .decode("ascii").strip()
    except RuntimeError as exc:
        raise RuntimeError("release VERSION is unavailable or unsafe") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError("release VERSION is not ascii") from exc
    if VERSION_RE.fullmatch(version) is None:
        raise RuntimeError("release VERSION is malformed")
    manifest = _parse_release_manifest(
        _read_release_file(repo, "RELEASE-MANIFEST.json", maximum=_RELEASE_MANIFEST_MAX_BYTES))
    legacy = manifest.get("legacyCleanMigration")
    v1 = legacy.get("v1Contract") if isinstance(legacy, dict) else None
    if not isinstance(v1, dict):
        raise RuntimeError("release manifest has no legacy clean migration contract")
    source = v1.get("sourcePixel")
    target = v1.get("targetPixel")
    if source != SOURCE_PIXEL:
        raise RuntimeError("release manifest legacy source does not match the contract")
    if not isinstance(target, str) or VERSION_RE.fullmatch(target) is None:
        raise RuntimeError("release manifest legacy target is malformed")
    if manifest.get("pixel") != version:
        raise RuntimeError("release manifest version does not match VERSION")
    if target != version:
        raise RuntimeError("release manifest legacy target does not match VERSION")
    return target


TARGET_PIXEL = _load_release_contract()

JOURNAL_KEYS = {
    "schemaVersion", "kind", "backupSha256", "sourcePixel", "targetPixel",
    "committed", "cleanup", "rolledBack", "finalization",
    "contractRoots", "destinations", "oldPaths", "temporaryPaths",
    "hadOld", "units", "rollbackProgress", "oldEvidence",
}
RESERVED_KEYS = {
    "schemaVersion", "kind", "reservationToken", "contractSha256", "backupSha256",
    "sourcePixel", "targetPixel",
}

# Deployed-unit PATH allowlist: the complete exact Pixel installed-unit contract, derived
# from the configured unit names in scripts/configure.mjs (PIXEL_*_UNIT / PIXEL_*_TIMER /
# PIXEL_*_PATH_UNIT) plus the dynamic pixel-work-* unit family. This is the ONLY set of
# unit filenames the privileged helper may manage as deployment items; a drift-preventing
# contract test re-derives the list from configure.mjs so the two cannot diverge. Arbitrary
# systemd files are never permitted: a sudo caller can never name a unit outside this set.
DEPLOYED_UNIT_ALLOWLIST = {
    "openclaw-gateway.service",
    "pixel-web-courier.service",
    "pixel-source-broker.service",
    "pixel-source-broker.timer",
    "pixel-source-action@.service",
    "pixel-source-reconcile@.service",
    "pixel-source-direct.service",
    "pixel-source-direct.path",
    "pixel-ops-broker.service",
    "pixel-frontier-broker.service",
}
DEEP_UNIT = re.compile(r"^pixel-work-[a-z0-9][a-z0-9.-]*\.(service|timer|path)$")

# Service QUIESCENCE list: the actual running units the helper stops/restarts during
# rollback/commit. This is deliberately SEPARATE from the deployment path allowlist above:
# quiescence names live services to stop/restart, while the allowlist names every installed
# unit FILE the transaction may manage. A unit can be on the allowlist (a managed file)
# without being quiesced, and the quiescence list is validated independently so a managed
# unit file is never required to be a running service.
QUIESCE_UNITS = {"openclaw-gateway.service", "pixel-source-broker.timer",
                 "pixel-ops-broker.service", "pixel-frontier-broker.service",
                 "pixel-web-courier.service"}

# The privileged helper runs as the root euid via sudo. Installed Pixel unit files are
# root-owned; user-state deployment items (active current symlink, config/workspace files)
# are owned by the *invoking* non-root user (michael), never by root. ROOT_UID is the owner
# of installed unit files and UNIT_PARENT is the only directory unit deployment items may
# name. The isolated test harness mechanically replaces ROOT_UID/ROOT_GID with the test
# euid/egid and UNIT_PARENT with a temp directory in a clearly non-root test copy (no
# runtime override).
ROOT_UID = 0
ROOT_GID = 0
UNIT_PARENT = "/etc/systemd/system"

# Production executes as the root euid and invokes only these fixed absolute trusted
# binaries directly (no sudo wrapper, no PATH lookup, no environment override). The
# isolated test harness mechanically copies this script and replaces these constants in a
# clearly non-root test copy; there is no runtime environment override that a privileged
# euid would accept.
REQUIRE_ROOT = True
SYSTEMCTL_BIN = "/usr/bin/systemctl"
RM_BIN = "/usr/bin/rm"
MV_BIN = "/usr/bin/mv"


def fail(message: str) -> None:
    raise SystemExit("migration journal: " + message)


def _require_privileged() -> None:
    """Require the root euid for every production helper operation (no sudo, no PATH)."""
    if REQUIRE_ROOT and os.geteuid() != 0:
        fail("this privileged helper operation must run as the root euid")


def _systemctl(*args: str) -> None:
    _require_privileged()
    subprocess.run([SYSTEMCTL_BIN, *args], check=True)


def _systemctl_quiet(*args: str) -> bool:
    _require_privileged()
    result = subprocess.run([SYSTEMCTL_BIN, *args])
    return result.returncode == 0


def _systemctl_read(*args: str) -> str:
    _require_privileged()
    result = subprocess.run([SYSTEMCTL_BIN, *args], capture_output=True, text=True)
    return (result.stdout or "").strip()


# Only these explicitly enumerated systemctl states are ever accepted for a managed unit.
# Any unknown/failed/indeterminate state (e.g. "failed", "unknown", "static", "masked",
# "not-found") fails closed BEFORE any mutation - the transaction never guesses at service
# state and never rewrites indeterminate live state.
SERVICE_ACTIVE_STATES = {"active", "inactive"}
SERVICE_ENABLED_STATES = {"enabled", "disabled"}
# A unit that is not installed ("not-found") has no running/enabled state to restore: it is
# treated as inactive+disabled for capture and skipped for finalize (never enabled/stated).
NOT_FOUND_STATE = "not-found"


def _snapshot_service_state(units: list[str]) -> dict[str, dict[str, bool]]:
    """Snapshot exact active/inactive + enabled/disabled prestate for every managed unit.

    Called at the install mutation boundary, immediately BEFORE any live deployment or
    private-root mutation (and before quiescence). The prestate is bound into the journal so
    rollback can restore the exact pre-active/pre-enabled state rather than indiscriminately
    restarting every quiesced unit.
    """
    prestate: dict[str, dict[str, bool]] = {}
    for unit in units:
        active_raw = _systemctl_read("is-active", unit)
        enabled_raw = _systemctl_read("is-enabled", unit)
        if active_raw not in SERVICE_ACTIVE_STATES and active_raw != NOT_FOUND_STATE:
            fail(f"service active state is indeterminate for {unit}: {active_raw!r}")
        if enabled_raw not in SERVICE_ENABLED_STATES and enabled_raw != NOT_FOUND_STATE:
            fail(f"service enabled state is indeterminate for {unit}: {enabled_raw!r}")
        prestate[unit] = {"active": active_raw == "active", "enabled": enabled_raw == "enabled"}
    return prestate


def _apply_service_map(value: dict[str, Any], service_map: dict[str, dict[str, bool]]) -> None:
    """Apply an exact per-unit enabled/active map (prestate or reviewed desired state),
    verifying every transition against the exact raw systemctl state (finding 4).

    Quiet boolean checks are never used: they falsely treat failed/masked/static/unknown as
    inactive/disabled. For desired true this requires the exact ``active``/``enabled`` raw
    state (any not-found fails). For desired false it explicitly stops an active unit and
    disables an enabled unit, then accepts only exact ``inactive``/``not-found`` and exact
    ``disabled``/``not-found`` - every other raw state (failed, masked, static, unknown,
    activating, deactivating, empty) fails closed. The legitimate false/false absent-unit
    case is preserved. Units absent from the map are left untouched. Failures are re-raised
    so the caller records finalization as failed/pending (resumable, no false pass).
    """
    _systemctl("daemon-reload")
    for unit in value["units"]:
        rec = service_map.get(unit)
        if rec is None:
            continue
        desired_active = rec["active"]
        desired_enabled = rec["enabled"]
        enabled_raw = _systemctl_read("is-enabled", unit)
        active_raw = _systemctl_read("is-active", unit)
        # Each dimension is validated and processed independently: a unit whose enabled
        # state is not-found is still subject to the active dimension and vice versa. Only
        # exact raw states are accepted - enabled/disabled/not-found for enabled and
        # active/inactive/not-found for active; any other raw state fails closed.
        if enabled_raw not in ("enabled", "disabled", NOT_FOUND_STATE):
            raise subprocess.CalledProcessError(1, ["systemctl", "disable", unit])
        if desired_enabled:
            # Desired true requires the exact positive raw state; not-found fails closed so
            # the reviewed target contract never silently leaves a unit it wants enabled
            # uninstalled.
            if enabled_raw == "disabled":
                _systemctl("enable", unit)
                if _systemctl_read("is-enabled", unit) != "enabled":
                    raise subprocess.CalledProcessError(1, ["systemctl", "is-enabled", unit])
            elif enabled_raw != "enabled":
                raise subprocess.CalledProcessError(1, ["systemctl", "enable", unit])
        else:
            if enabled_raw == "enabled":
                _systemctl("disable", unit)
            if _systemctl_read("is-enabled", unit) not in ("disabled", NOT_FOUND_STATE):
                raise subprocess.CalledProcessError(1, ["systemctl", "is-enabled", unit])
        if active_raw not in ("active", "inactive", NOT_FOUND_STATE):
            raise subprocess.CalledProcessError(1, ["systemctl", "stop", unit])
        if desired_active:
            # Desired true requires the exact positive raw state; not-found fails closed.
            if active_raw == "inactive":
                _systemctl("start", unit)
                if _systemctl_read("is-active", unit) != "active":
                    raise subprocess.CalledProcessError(1, ["systemctl", "is-active", unit])
            elif active_raw != "active":
                raise subprocess.CalledProcessError(1, ["systemctl", "start", unit])
        else:
            if active_raw == "active":
                # A unit the reviewed contract leaves inactive (e.g. a previously running
                # courier that the target disables) must be explicitly stopped and verified, never
                # left running.
                _systemctl("stop", unit)
            if _systemctl_read("is-active", unit) not in ("inactive", NOT_FOUND_STATE):
                raise subprocess.CalledProcessError(1, ["systemctl", "is-active", unit])


def _restore_service_state(value: dict[str, Any]) -> None:
    """Restore the exact pre-active/pre-enabled state captured before outer quiescence for
    each managed unit, verifying every transition. A unit that was inactive/disabled before
    the migration is never indiscriminately restarted/enabled. Failures are re-raised so the
    caller records finalization as failed/pending (resumable, no false pass)."""
    prestate = value.get("servicePrestate")
    units = value["units"]
    if not isinstance(prestate, dict) or not units:
        # Legacy journal without a recorded service prestate (constructed before the
        # prestate-bound transaction): fall back to restarting every quiesced unit, the
        # pre-state-transaction contract. New transactions always record a prestate and are
        # restored exactly below - never indiscriminately restarting a disabled/inactive unit.
        _finalize_units(units)
        return
    _apply_service_map(value, prestate)


def _restore_exact_or_legacy(value: dict[str, Any]) -> None:
    """Restore exact captured prestate on a pre-mutation quiesce failure or early abort; only
    legacy journals without a captured prestate fall back to restarting every unit."""
    prestate = value.get("servicePrestate")
    if isinstance(prestate, dict) and value.get("units"):
        _apply_service_map(value, prestate)
        return
    _finalize_units(value["units"])


def _validate_affected_units(units: Any, require_full_fixed: bool) -> list[str]:
    """Validate the exact affected-unit set (finding 1).

    One strict reusable validator used by capture, quiesce, arm, journal validation, install,
    rollback, and abort/finalize. A new migration affected-unit set must be unique, include
    every fixed QUIESCE_UNITS member exactly once, and contain no names outside the fixed
    Pixel services plus the dynamic pixel-work-* (DEEP_UNIT) family.
    This closes the privileged arbitrary-unit stop: a sudo caller can never reserve, capture,
    or quiesce ssh.service or any other unit outside the contract. ``require_full_fixed`` is
    true for new/deployment transactions and false for legacy roots-only journals (which
    retain backward-compatible unit handling but still reject arbitrary/duplicate names).
    """
    if not isinstance(units, list):
        fail("affected units must be a list")
    seen: set[str] = set()
    fixed_seen: set[str] = set()
    for unit in units:
        if not isinstance(unit, str):
            fail("affected unit must be a string")
        if unit in seen:
            fail("affected units must be unique")
        seen.add(unit)
        if unit in QUIESCE_UNITS:
            if unit in fixed_seen:
                fail("affected units must be unique")
            fixed_seen.add(unit)
        elif DEEP_UNIT.fullmatch(unit):
            pass
        else:
            fail("unit is outside the fixed Pixel service contract: " + repr(unit))
    if require_full_fixed and fixed_seen != QUIESCE_UNITS:
        fail("affected units must include every fixed Pixel service exactly once")
    return units


def _check_service_prestate(prestate: Any, units: list[str]) -> None:
    """Validate an exact service-prestate map (active/enabled booleans for every unit).

    Keys must equal the affected units exactly - no omitted unit and no extra key - so a
    captured prestate can never diverge from the reserved affected-unit set.
    """
    if not isinstance(prestate, dict):
        fail("journal servicePrestate must be an object")
    if set(prestate.keys()) != set(units):
        fail("journal servicePrestate keys must equal the affected units exactly")
    for unit in units:
        rec = prestate.get(unit)
        if not isinstance(rec, dict) or set(rec.keys()) != {"active", "enabled"}:
            fail("journal servicePrestate is malformed for unit: " + str(unit))
        if not isinstance(rec.get("active"), bool) or not isinstance(rec.get("enabled"), bool):
            fail("journal servicePrestate must be boolean for unit: " + str(unit))


def _check_service_desired(desired: Any, require_full_fixed: bool = False) -> None:
    """Validate an exact reviewed serviceDesired contract (enabled/active booleans per unit).

    For a new/deployment transaction the desired keys must EXACTLY equal the full fixed
    QUIESCE_UNITS set: no omitted fixed unit, no dynamic deep-work unit, and no extra or
    arbitrary name. Every desired key is therefore always present in the affected units.
    """
    if not isinstance(desired, dict) or not desired:
        fail("journal serviceDesired must be a non-empty object")
    for unit, rec in desired.items():
        if not isinstance(unit, str) or not isinstance(rec, dict) or set(rec.keys()) != {"enabled", "active"}:
            fail("journal serviceDesired entry is malformed")
        if not isinstance(rec.get("enabled"), bool) or not isinstance(rec.get("active"), bool):
            fail("journal serviceDesired entry must be boolean")
    if require_full_fixed and set(desired.keys()) != QUIESCE_UNITS:
        fail("journal serviceDesired must cover every fixed Pixel service exactly once")


def _run_rm(*args: str) -> None:
    _require_privileged()
    subprocess.run([RM_BIN, *args], check=True)


def _run_mv(*args: str) -> None:
    _require_privileged()
    subprocess.run([MV_BIN, *args], check=True)


def open_custody(custody: str) -> int:
    """Open the custody directory through a bound descriptor and return its fd."""
    if os.path.normpath(custody) != custody or not custody.startswith("/"):
        fail("custody directory must be an absolute normalized path")
    try:
        fd = os.open(custody, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        fail("custody directory is not accessible: " + os.strerror(exc.errno))
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        fail("custody path is not a directory")
    if info.st_uid != os.geteuid():
        os.close(fd)
        fail("custody directory is not owned by the privileged euid")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        fail("custody directory must be owner-only 0700")
    return fd


def lock_custody(custody: str) -> int:
    fd = open_custody(custody)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _check_basename(custody: str, journal: str) -> str:
    if os.path.dirname(journal) != custody:
        fail("journal must be directly beneath the custody directory")
    basename = os.path.basename(journal)
    if not MIGRATION_JOURNAL_BASENAME.fullmatch(basename):
        fail("journal basename is unsafe")
    return basename


def _check_contract(contract: str) -> None:
    if not isinstance(contract, str) or not HASH_RE.fullmatch(contract):
        fail("contract evidence must be a 64-character lowercase hex digest")


def _check_backup(backup: str) -> None:
    if not isinstance(backup, str) or not HASH_RE.fullmatch(backup):
        fail("backup identity must be a 64-character lowercase hex digest")


def _check_token(token: str) -> None:
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        fail("reservation token is malformed")


def _parse_had_old(raw: str) -> int:
    """Parse an arm hadOld flag: only the literal strings 0 or 1 are accepted.

    A truthy int coercion (bool(int(x))) would silently accept values like 2 or -1 as
    hadOld=1, which could arm a root with fabricated old-rollback evidence. Arm must
    require the exact literal flags 0 or 1 so hadOld always matches what the
    orchestrator actually observed at swap time.
    """
    if raw not in ("0", "1"):
        fail("hadOld must be the literal string 0 or 1")
    return int(raw)


def read_object(fd: int) -> dict[str, Any]:
    os.lseek(fd, 0, os.SEEK_SET)
    payload = os.read(fd, MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        fail("journal is oversized")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError):
        fail("journal is not valid JSON")
    if not isinstance(value, dict):
        fail("journal is not an object")
    return value


def _write_journal(custody_fd: int, basename: str, value: dict[str, Any]) -> None:
    """Crash-safe atomic journal replacement beneath the bound custody dirfd.

    The armed/progress/commit/rollback/finalization states are never written in place with
    ftruncate (a power loss between truncate and fsync could destroy the only rollback
    state). Instead each transition writes a fresh O_EXCL mode-0600 temp beneath the bound
    custody dirfd, fsyncs it, verifies it, and renames it over the journal via dirfds before
    fsyncing the custody directory. The custody directory is root-owned and locked, so the
    replacement is safe and every state transition is all-or-nothing.
    """
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_JSON_BYTES:
        fail("journal write is oversized")
    tmp_name = ".pixel-journal-" + secrets.token_hex(8) + ".tmp"
    try:
        tmp_fd = os.open(tmp_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                         | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=custody_fd)
    except OSError as exc:
        fail("journal temp could not be created: " + os.strerror(exc.errno))
    renamed = False
    try:
        try:
            view = memoryview(payload)
            written = 0
            while written < len(view):
                written += os.write(tmp_fd, view[written:])
            if written != len(payload):
                fail("journal write was truncated")
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)
        try:
            os.rename(tmp_name, basename, src_dir_fd=custody_fd, dst_dir_fd=custody_fd)
        except OSError as exc:
            fail("journal could not be atomically replaced: " + os.strerror(exc.errno))
        renamed = True
    finally:
        if not renamed:
            try:
                os.unlink(tmp_name, dir_fd=custody_fd)
            except OSError:
                pass
    os.fsync(custody_fd)


def cmd_ensure(custody: str) -> None:
    if os.path.normpath(custody) != custody or not custody.startswith("/"):
        fail("custody directory must be an absolute normalized path")
    try:
        if not os.path.isdir(custody) and not os.path.islink(custody):
            os.mkdir(custody, 0o700)
    except OSError as exc:
        fail("custody directory could not be created: " + os.strerror(exc.errno))
    fd = lock_custody(custody)
    os.close(fd)


def _reserved_value(contract: str, backup: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": RESERVED_KIND,
        "reservationToken": secrets.token_hex(16),
        "contractSha256": contract,
        "backupSha256": backup,
        "sourcePixel": SOURCE_PIXEL,
        "targetPixel": TARGET_PIXEL,
    }


def cmd_reserve(custody: str, journal: str, contract: str, backup: str) -> str:
    _check_contract(contract)
    _check_backup(backup)
    basename = _check_basename(custody, journal)
    custody_fd = lock_custody(custody)
    try:
        try:
            fd = os.open(
                basename,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=custody_fd,
            )
        except FileExistsError:
            fail("migration journal path is already occupied; refusing to reclaim any existing marker")
        except OSError as exc:
            fail("journal path could not be created: " + os.strerror(exc.errno))
        try:
            reservation = _reserved_value(contract, backup)
            payload = json.dumps(reservation, sort_keys=True, separators=(",", ":")).encode("utf-8")
            view = memoryview(payload)
            written = 0
            while written < len(view):
                written += os.write(fd, view[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(custody_fd)
    return reservation["reservationToken"]


def cmd_capture(custody: str, journal: str, token: str, contract: str, backup: str, units: list[str]) -> None:
    """Mutation-free exact service-prestate capture, BEFORE outer quiescence (finding 3).

    Records the exact active/inactive + enabled/disabled prestate for every managed unit
    into the reserved journal (carried through arm). This runs BEFORE any unit is stopped so
    an originally active/enabled unit is never durably recorded as inactive, and abort/early
    failure can restore exact state. It only reads systemctl state and rewrites the reserved
    marker; it never stops, starts, enables, or disables any service.
    """
    _check_token(token)
    _check_contract(contract)
    _check_backup(backup)
    basename = _check_basename(custody, journal)
    # Capture is a migration-only step, so the affected-unit set must be the strict new
    # migration set: unique, every fixed QUIESCE_UNITS member exactly once, and no
    # arbitrary/deep-unsafe names (closes the privileged arbitrary-unit capture).
    _validate_affected_units(units, require_full_fixed=True)
    custody_fd = lock_custody(custody)
    try:
        fd = _open_journal(custody_fd, basename, os.O_RDWR)
        try:
            _check_owned_regular(fd)
            reserved = read_object(fd)
            if reserved.get("kind") != RESERVED_KIND:
                fail("journal is not in reserved state; refusing to capture a foreign transaction")
            if not RESERVED_KEYS.issubset(reserved.keys()) \
               or not set(reserved.keys()).issubset(RESERVED_KEYS | {"servicePrestate", "units", "quiesced"}):
                fail("reservation is missing or has unexpected keys")
            if reserved.get("reservationToken") != token:
                fail("reservation token does not match this invocation; refusing to capture")
            if reserved.get("contractSha256") != contract or reserved.get("backupSha256") != backup:
                fail("journal reservation does not match this contract/backup; refusing to capture")
            # One-shot capture: reject a quiesced reservation first, then reject recapture/overwrite.
            if reserved.get("quiesced"):
                fail("reservation is already quiesced; refusing to capture a quiesced reservation")
            if "units" in reserved or "servicePrestate" in reserved:
                fail("reservation has already captured service prestate; refusing to recapture/overwrite")
            prestate = _snapshot_service_state(units)
            reserved["units"] = units
            reserved["servicePrestate"] = prestate
            _write_journal(custody_fd, basename, reserved)
        finally:
            os.close(fd)
    finally:
        os.close(custody_fd)
    print('{"status":"pass","mode":"capture","prestate":true}')


def cmd_quiesce(custody: str, journal: str, token: str, contract: str, backup: str) -> None:
    """Helper-owned verified quiescence (finding 3), AFTER capture and BEFORE any
    backup/staging or arm. Stops every affected active unit, verifies each is inactive,
    records the transition durably in the reserved journal, and on ANY early failure
    restores the exact captured prestate (never the legacy blind restart-all). This
    replaces the outer shell's manual ``stop || true`` so a stop/verify failure can never
    leave a unit half-down or overwrite the captured prestate."""
    _check_token(token)
    _check_contract(contract)
    _check_backup(backup)
    basename = _check_basename(custody, journal)
    custody_fd = lock_custody(custody)
    try:
        fd = _open_journal(custody_fd, basename, os.O_RDWR)
        try:
            _check_owned_regular(fd)
            reserved = read_object(fd)
            if reserved.get("kind") != RESERVED_KIND:
                fail("journal is not in reserved state; refusing to quiesce a foreign transaction")
            if not RESERVED_KEYS.issubset(reserved.keys()) \
               or not set(reserved.keys()).issubset(RESERVED_KEYS | {"servicePrestate", "units", "quiesced"}):
                fail("reservation is missing or has unexpected keys")
            if reserved.get("reservationToken") != token:
                fail("reservation token does not match this invocation; refusing to quiesce")
            if reserved.get("contractSha256") != contract or reserved.get("backupSha256") != backup:
                fail("journal reservation does not match this contract/backup; refusing to quiesce")
            prestate = reserved.get("servicePrestate")
            units = reserved.get("units")
            if not isinstance(prestate, dict) or not isinstance(units, list):
                fail("cannot quiesce a reservation that has not captured exact service prestate")
            _validate_affected_units(units, require_full_fixed=True)
            _check_service_prestate(prestate, units)
            if reserved.get("quiesced"):
                fail("reservation is already quiesced; refusing to re-quiesce")
            # Helper-owned verified quiescence: stop only ACTIVE units, leave inactive /
            # not-found optional units alone, reject failed/unknown/activating/deactivating/
            # empty, and verify each unit is EXACTLY inactive or not-found after stopping.
            # ANY stop OR verification failure (including a verify-still-active SystemExit)
            # restores the exact captured prestate before re-raising, so a unit A is never
            # left half-down by a failure on unit B.
            _quiesce_active_units(units, prestate)
            reserved["quiesced"] = True
            _write_journal(custody_fd, basename, reserved)
        finally:
            os.close(fd)
    finally:
        os.close(custody_fd)
    print('{"status":"pass","mode":"quiesce","quiesced":true}')


def _open_journal(custody_fd: int, basename: str, flags: int) -> int:
    try:
        return os.open(basename, flags | getattr(os, "O_NOFOLLOW", 0), dir_fd=custody_fd)
    except FileNotFoundError:
        fail("journal does not exist in custody")
    except OSError as exc:
        fail("journal path could not be opened: " + os.strerror(exc.errno))


def _check_owned_regular(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fail("journal must be a bounded regular single-link file")
    if info.st_uid != os.geteuid():
        fail("journal owner is not the privileged euid")
    if stat.S_IMODE(info.st_mode) != 0o600:
        fail("journal must be owner-only 0600")


def _evidence(path: str) -> dict[str, Any] | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISDIR(info.st_mode):
        kind = "dir"
    elif stat.S_ISREG(info.st_mode):
        kind = "file"
    elif stat.S_ISLNK(info.st_mode):
        kind = "symlink"
    else:
        kind = "other"
    return {"ino": info.st_ino, "dev": info.st_dev, "size": info.st_size, "nlink": info.st_nlink, "type": kind}


def _matches(evidence: dict[str, Any], path: str) -> bool:
    info = _evidence(path)
    if info is None:
        return False
    return (info["ino"] == evidence["ino"] and info["dev"] == evidence["dev"]
            and info["type"] == evidence["type"])


DEPLOY_KINDS = {"symlink", "config", "workspace", "unit"}
# Journaled deployment item. newPath is the prepared exact sibling (the target object staged
# beside the live path); newEvidence is its frozen snapshot taken at arm so install can
# detect prepared-new tamper and rollback can prove what was installed. installProgress is
# the durable install/swap state machine.
# Base deployment-item keys (legacy standalone items carry exactly these). A folded item
# (owned by a whole private-root swap rather than a standalone install) additionally carries
# ``folded`` True, the owning private-root index ``rootIndex``, the subpath ``relative`` below
# that root, and the prepared content bind: ``sha256`` (content digest or None for a
# desired-absent nested item), ``target`` (symlink), and ``mode`` (present file). Folded items
# are never given a standalone newPath install and never carry a separate newEvidence snapshot;
# commit verifies the composed live state and rollback is covered by the owning root swap.
DEPLOY_ITEM_KEYS = {"kind", "path", "oldPath", "newPath", "hadOld", "evidence",
                    "newEvidence", "parent", "progress", "installProgress"}
DEPLOY_ITEM_KEYS_FOLDED = (DEPLOY_ITEM_KEYS - {"newEvidence"}) | {"folded", "rootIndex", "relative", "sha256", "target", "mode"}
# The prepare spec may carry an optional per-item ``sha256`` (content digest of the staged
# target candidate, used as the cryptographic bind so activation's staged-candidate intake
# verifies the exact reviewed bytes) and an optional ``target`` for symlink candidates.
# ``sha256`` is None for a desired-absent item (an explicit, bounded absent-transaction).
DEPLOY_SPEC_ITEM_KEYS = {"kind", "path", "oldPath", "newPath", "hadOld", "sha256", "target"}
# Every deployment item must carry these base keys before any field is accessed, so a
# missing required key is refused cleanly (never a KeyError traceback) before any mutation
# or newPath. ``sha256``/``target`` remain optional per the legacy rules below.
DEPLOY_SPEC_BASE_KEYS = {"kind", "path", "oldPath", "newPath", "hadOld"}
GLOB_CHARS = set("*?[]{}")

# Valid install/swap state machine states (durably recorded in the journal).
INSTALL_PROGRESS_STATES = {"pending", "in-progress", "old-moved", "new-installed", "complete"}


def _invoking_uid() -> int:
    """Derive the non-root uid of the invoking orchestrator from trusted sudo state.

    In production the helper runs as the root euid via sudo, which sets SUDO_UID to the
    real uid of the invoking non-root user. User-state deployment items (active current
    symlink, config/workspace files) are owned by that invoking user, not by root, so every
    user-state owner check must compare against SUDO_UID rather than os.geteuid(). SUDO_UID
    is trusted because it is set by the sudo binary itself, never by the caller. The
    isolated test copy (REQUIRE_ROOT False) runs unprivileged and uses its own euid as the
    invoking owner. Caller-supplied owner authority is never accepted: there is no path for
    the caller to name an owner uid.
    """
    if not REQUIRE_ROOT:
        return os.geteuid()
    raw = os.getenv("SUDO_UID")
    if raw is None or not raw.isdigit() or raw == "0" or int(raw) <= 0:
        fail("cannot derive the invoking non-root uid from trusted sudo state (SUDO_UID)")
    return int(raw)


def _invoking_gid() -> int:
    """Derive the invoking orchestrator's gid from trusted sudo state (SUDO_GID).

    User-state deployment items are owned by the invoking user's primary group so the frozen
    evidence and the live object match exactly. Mirrors ``_invoking_uid`` and is never taken
    from the caller.
    """
    if not REQUIRE_ROOT:
        return os.getgid()
    raw = os.getenv("SUDO_GID")
    if raw is None or not raw.isdigit() or raw == "0" or int(raw) <= 0:
        fail("cannot derive the invoking non-root gid from trusted sudo state (SUDO_GID)")
    return int(raw)


def _deploy_owner(kind: str) -> int:
    """Required owner uid for a deployment item.

    User-state items (symlink/config/workspace) are owned by the invoking non-root user;
    installed unit files are root-owned. This is the only authority used for ownership
    checks and is never taken from the caller.
    """
    if kind == "unit":
        return ROOT_UID
    return _invoking_uid()


def _deploy_group(kind: str) -> int:
    """Required group id for a deployment item, from the same fixed authority split."""
    if kind == "unit":
        return ROOT_GID
    return _invoking_gid()


def _unit_path_ok(path: Any) -> bool:
    """Unit deployment items are locked to UNIT_PARENT plus a fixed/deep Pixel unit name.

    A sudo caller can therefore never name an arbitrary root-owned unit file; the mutation
    is confined to the exact installed-unit contract that the target orchestrator manages.
    """
    if not isinstance(path, str):
        return False
    parent = os.path.dirname(path)
    base = os.path.basename(path)
    return parent == UNIT_PARENT and (base in DEPLOYED_UNIT_ALLOWLIST or DEEP_UNIT.fullmatch(base))


def _paths_collide(a: str, b: str) -> bool:
    """True when two absolute normalized paths overlap or nest (a==b or one is a prefix)."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _deployment_root_overlap(path: str, destinations: list[str]) -> tuple[bool, int | None, str | None]:
    """Classify a deployment item path against the authenticated private-root destinations.

    This is the cross-domain overlap classification used to fold a deployment item into the
    private root that owns it (composing it into the root's non-live restore staging tree
    before the whole-root copy/swap) rather than installing it standalone. It is a PURE
    path invariant: it never touches the filesystem and never creates anything, so it runs
    before any mutation or intake.

    Returns (folded, root_index, relative_subpath):
      - (False, None, None): standalone - the item overlaps no private root.
      - (True, index, rel): the item overlaps EXACTLY ONE private root and is either the root
        file itself (rel == "") or a nested item strictly below it (rel is the subpath under
        the root). rel is empty for an exact file-root overlap.
    Refuses (SystemExit) - never folds - for:
      - ambiguous multi-root overlap (the item overlaps more than one private root);
      - an inverse overlap where a private root nests strictly inside the item's path (the
        root would be carried by a narrower deployment item);
      - any traversal that escapes the owning root.
    """
    hits = [i for i, root in enumerate(destinations) if _paths_collide(path, root)]
    if not hits:
        return (False, None, None)
    if len(hits) > 1:
        fail("deployment item overlaps multiple private roots; ambiguous fold refused")
    index = hits[0]
    root = destinations[index]
    if root.startswith(path + "/"):
        fail("deployment item is an ancestor of a private root; inverse fold refused")
    if path == root:
        # Exact file-root overlap: the item IS the root file.
        return (True, index, "")
    if not path.startswith(root + "/"):
        fail("deployment item overlap is indeterminate; refused")
    rel = path[len(root) + 1:]
    if not rel or "/../" in rel or rel.startswith("../") or rel.endswith("/.."):
        fail("deployment item traversal escapes its private root; refused")
    return (True, index, rel)


def _derived_stem(destination: str) -> str:
    return os.path.join(os.path.dirname(destination), ".pixel-restore-" + os.path.basename(destination))


def _deploy_new_path(old_path: str) -> str:
    """The exact prepared-new sibling derived from a deployment oldPath.

    The prepared target object is staged beside the live path with the same pid/index token as
    the oldPath but a ``.new`` suffix, so a crash at any boundary leaves both siblings
    classifiable and resumable.
    """
    if not old_path.endswith(".old"):
        fail("deployment oldPath does not carry the expected .old suffix")
    return old_path[:-4] + ".new"


def _deploy_path_ok(path: Any) -> bool:
    """Deployment paths are fixed, explicitly validated, non-broad non-root paths.

    Reject arbitrary/broad/root/glob/traversal/symlink-escape inputs: the path must be an
    absolute normalized non-root path with no control characters, no glob metacharacters, and
    no ``..`` traversal that a normpath would collapse. The caller (trusted target code) derives
    the exact fixed paths; this helper fails closed on anything else.
    """
    return (isinstance(path, str) and path.startswith("/") and path != "/"
            and "\n" not in path and "\r" not in path
            and os.path.normpath(path) == path
            and not GLOB_CHARS.intersection(path)
            and "/.." not in path and not path.endswith("/.."))


def _reject_symlink_ancestors(path: str) -> None:
    """Reject a deployment path whose any ancestor is a symlink (symlink escape).

    Every ancestor directory from the filesystem root down to (but not including) the final
    component must be a real directory, so a substituted ``/etc`` -> attacker directory can
    never redirect the privileged mutation or snapshot.
    """
    parts = path.split("/")
    current = ""
    for part in parts[1:-1]:
        current = current + "/" + part
        try:
            info = os.lstat(current)
        except OSError:
            fail("deployment path ancestor is not accessible")
        if stat.S_ISLNK(info.st_mode):
            fail("deployment path has a symlink ancestor; refusing symlink escape")
        if not stat.S_ISDIR(info.st_mode):
            fail("deployment path ancestor is not a directory")


def _same_object(a: Any, b: Any) -> bool:
    return a.st_dev == b.st_dev and a.st_ino == b.st_ino and stat.S_IFMT(a.st_mode) == stat.S_IFMT(b.st_mode)


def _metadata_changed(a: Any, b: Any) -> bool:
    """True when any exact metadata that rollback depends on changed between two stats."""
    return (a.st_uid != b.st_uid or a.st_gid != b.st_gid
            or stat.S_IMODE(a.st_mode) != stat.S_IMODE(b.st_mode)
            or a.st_size != b.st_size or a.st_nlink != b.st_nlink
            or a.st_mtime_ns != b.st_mtime_ns or a.st_ctime_ns != b.st_ctime_ns)


def _kind_of(st: Any) -> str:
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if stat.S_ISREG(st.st_mode):
        return "file"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    return "other"


def _open_parent_dirfd(path: str, required_uid: int) -> int:
    """Open the immediate parent of a deployment path as a bound dirfd.

    Uses O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC so a substituted symlink parent can never be
    followed, and verifies the parent is a real directory owned by ``required_uid`` with no
    group/other write and no setuid/setgid. The returned fd is the only handle later
    mutation may use (rollback/commit re-open and re-validate by armed evidence).
    """
    parent = os.path.dirname(path)
    try:
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        fail("deployment parent directory is not accessible: " + os.strerror(exc.errno))
    try:
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        fail("deployment parent is not a real directory")
    if info.st_uid != required_uid:
        os.close(fd)
        fail("deployment parent is not owned by the required owner uid")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022:
        os.close(fd)
        fail("deployment parent is group/other writable")
    if mode & (stat.S_ISUID | stat.S_ISGID):
        os.close(fd)
        fail("deployment parent mode is unsafe (setuid/setgid)")
    return fd


def _open_parent_validated(path: str, parent_ev: dict[str, Any]) -> int:
    """Re-open the immediate parent dirfd and prove it matches the armed parent evidence.

    Used in rollback/commit so a parent substituted between arm and mutation (dev/inode/
    type/uid/gid/mode changed) is refused rather than followed. This closes the
    parent-swap privilege/race hole.
    """
    parent = os.path.dirname(path)
    try:
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        fail("deployment parent could not be reopened: " + os.strerror(exc.errno))
    try:
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not (stat.S_ISDIR(info.st_mode)
            and info.st_dev == parent_ev["dev"] and info.st_ino == parent_ev["ino"]
            and info.st_uid == parent_ev["uid"] and info.st_gid == parent_ev["gid"]
            and stat.S_IMODE(info.st_mode) == parent_ev["mode"]):
        os.close(fd)
        fail("deployment parent changed since arm; refusing to mutate")
    return fd


def _parent_evidence(parent_fd: int) -> dict[str, Any]:
    info = os.fstat(parent_fd)
    return {"type": "dir", "dev": info.st_dev, "ino": info.st_ino,
            "uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}


def _check_parent_evidence(evidence: Any) -> None:
    if not isinstance(evidence, dict):
        fail("deployment parent evidence is malformed")
    for key in ("type", "dev", "ino", "uid", "gid", "mode"):
        if key not in evidence:
            fail("deployment parent evidence is incomplete")
    if evidence["type"] != "dir":
        fail("deployment parent evidence type mismatch")
    for key in ("dev", "ino", "uid", "gid", "mode"):
        if not isinstance(evidence[key], int) or isinstance(evidence[key], bool):
            fail("deployment parent evidence is not an exact integer")


def _stat_nofollow(parent_fd: int, basename: str) -> Any:
    """No-follow stat of a basename beneath a bound parent dirfd (None if absent)."""
    try:
        return os.stat(basename, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        fail("deployment item is not accessible: " + os.strerror(exc.errno))


def _unlink_basename(parent_fd: int, basename: str) -> None:
    try:
        os.unlink(basename, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        fail("deployment item could not be unlinked: " + os.strerror(exc.errno))


def _snapshot_file_dirfd(parent_fd: int, basename: str, required_uid: int) -> dict[str, Any]:
    """Stable descriptor-bound snapshot of a regular deployment file.

    Opens through the bound parent dirfd with O_NOFOLLOW|O_CLOEXEC, proves the opened object
    matches the pre-open no-follow stat, hashes via the fd, fstats again, and fails if
    identity/type/owner/mode/link count/size/mtime/ctime changed during the read. EVERY
    journal-managed regular file (installed unit, user config, or user workspace file) is
    required to have no group/other write and no setuid/setgid: a world- or group-writable
    managed file could be substituted by an unprivileged peer between arm and mutation, so
    the group/other-write check is never relaxed for user-state files.
    """
    pre = _stat_nofollow(parent_fd, basename)
    if pre is None:
        fail("deployment file is missing during snapshot")
    if not stat.S_ISREG(pre.st_mode):
        fail("deployment file path is not a regular file")
    if pre.st_uid != required_uid:
        fail("deployment file owner is not the required owner uid")
    if pre.st_nlink != 1:
        fail("deployment file must be a single-link regular file")
    mode = stat.S_IMODE(pre.st_mode)
    if mode & (stat.S_ISUID | stat.S_ISGID):
        fail("deployment file mode is unsafe (setuid/setgid)")
    if mode & 0o022:
        fail("deployment file mode is unsafe (group/other writable)")
    fd = os.open(basename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
    try:
        first = os.fstat(fd)
        if not _same_object(pre, first):
            fail("deployment file changed between stat and open (identity)")
        hasher = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            hasher.update(chunk)
        final = os.fstat(fd)
        if not _same_object(pre, final) or _metadata_changed(pre, final):
            fail("deployment file changed during read")
        return {"type": "file", "sha256": hasher.hexdigest(),
                "uid": final.st_uid, "gid": final.st_gid, "mode": stat.S_IMODE(final.st_mode),
                "ino": final.st_ino, "dev": final.st_dev, "size": final.st_size,
                "nlink": final.st_nlink, "mtime_ns": final.st_mtime_ns, "ctime_ns": final.st_ctime_ns}
    finally:
        os.close(fd)


def _snapshot_symlink_dirfd(parent_fd: int, basename: str, required_uid: int) -> dict[str, Any]:
    """Stable dirfd/no-follow snapshot of a deployment symlink (dirfd stat + dirfd readlink)."""
    info = _stat_nofollow(parent_fd, basename)
    if info is None:
        fail("deployment symlink is missing during snapshot")
    if not stat.S_ISLNK(info.st_mode):
        fail("deployment symlink path is not a symlink")
    if info.st_uid != required_uid:
        fail("deployment symlink owner is not the required owner uid")
    target = os.readlink(basename, dir_fd=parent_fd)
    info2 = _stat_nofollow(parent_fd, basename)
    if info2 is None or not _same_object(info, info2) or _metadata_changed(info, info2):
        fail("deployment symlink changed during snapshot")
    return {"type": "symlink", "target": target,
            "uid": info2.st_uid, "gid": info2.st_gid, "mode": stat.S_IMODE(info2.st_mode),
            "ino": info2.st_ino, "dev": info2.st_dev, "nlink": info2.st_nlink,
            "mtime_ns": info2.st_mtime_ns, "ctime_ns": info2.st_ctime_ns}


def _validate_deployment_live_state(kind: str, path: str, old_path: str, had_old: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Snapshot exact deployment-item evidence + parent evidence from the live path.

    Runs before first mutation (arm), through a bound parent dirfd. The old content must
    still live at ``path`` (hadOld=1) or be absent (hadOld=0), the prepared sibling
    ``oldPath`` must be absent, and the immediate parent must be a safe directory owned by
    the required owner. Returns (item_evidence, parent_evidence); item evidence is None when
    hadOld=0.
    """
    _reject_symlink_ancestors(path)
    _reject_symlink_ancestors(old_path)
    required_uid = _deploy_owner(kind)
    parent_fd = _open_parent_dirfd(path, required_uid)
    try:
        parent_ev = _parent_evidence(parent_fd)
        if _stat_nofollow(parent_fd, os.path.basename(old_path)) is not None:
            fail("deployment oldPath already exists; refusing to arm")
        basename = os.path.basename(path)
        live = _stat_nofollow(parent_fd, basename)
        if had_old == 1:
            if live is None:
                fail("hadOld=1 but deployment path is missing; refusing to arm")
            if kind == "symlink":
                if not stat.S_ISLNK(live.st_mode):
                    fail("deployment symlink path is not a symlink; refusing to arm")
                if live.st_uid != required_uid:
                    fail("deployment symlink owner is not the required owner uid; refusing to arm")
                return (_snapshot_symlink_dirfd(parent_fd, basename, required_uid), parent_ev)
            if not stat.S_ISREG(live.st_mode):
                fail("deployment file path is not a regular file; refusing to arm")
            if live.st_uid != required_uid:
                fail("deployment file owner is not the required owner uid; refusing to arm")
            if live.st_nlink != 1:
                fail("deployment file must be a single-link regular file; refusing to arm")
            mode = stat.S_IMODE(live.st_mode)
            if mode & (stat.S_ISUID | stat.S_ISGID):
                fail("deployment file mode is unsafe (setuid/setgid); refusing to arm")
            if mode & 0o022:
                fail("deployment file mode is unsafe (group/other writable); refusing to arm")
            return (_snapshot_file_dirfd(parent_fd, basename, required_uid), parent_ev)
        if live is not None:
            fail("hadOld=0 but deployment path exists; refusing to arm")
        return (None, parent_ev)
    finally:
        os.close(parent_fd)



def _intake_stage_candidate(stage_root: str, name: str, parent_fd: int, new_path: str,
                            kind: str, expected_sha: Any, expected_target: Any,
                            required_uid: int) -> dict[str, Any]:
    """Copy a prepared target candidate from the non-live stage root into a live newPath sibling.

    Runs inside the privileged helper (never the ordinary restore shell) under the custody
    lock, descriptor-bound through the stage root dirfd and the deployment parent dirfd.
    Rejects symlink/hard-link/oversized candidates, verifies the copied content digest
    matches the prepared per-item ``sha256`` (the cryptographic bind against a stage swap
    after prepare), rechecks the frozen snapshot after copy, and returns newEvidence.
    """
    if not _deploy_path_ok(stage_root):
        fail("stage root is not a safe absolute normalized path")
    try:
        stage_fd = os.open(stage_root, os.O_RDONLY | os.O_DIRECTORY
                           | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        fail("stage root is not an accessible directory: " + stage_root)
    try:
        if not stat.S_ISDIR(os.fstat(stage_fd).st_mode):
            fail("stage root is not a directory")
        src = _stat_nofollow(stage_fd, name)
        if src is None:
            fail("stage candidate is missing: " + name)
        new_basename = os.path.basename(new_path)
        if _stat_nofollow(parent_fd, new_basename) is not None:
            fail("deployment newPath already exists during intake; refusing")
        if stat.S_ISLNK(src.st_mode):
            if kind != "symlink":
                fail("stage candidate for a file deployment item is a symlink; refusing intake")
            target = os.readlink(name, dir_fd=stage_fd)
            if expected_target is not None and target != expected_target:
                fail("stage symlink target does not match the prepared target; refusing intake")
            os.symlink(target, new_basename, dir_fd=parent_fd)
            try:
                # Linux-only: reference the just-created symlink through the parent dirfd via
                # /proc/self/fd and chown it without following (os.lchown has no dir_fd).
                os.lchown(f"/proc/self/fd/{parent_fd}/{new_basename}",
                          required_uid, _invoking_gid())
            except OSError:
                pass
            os.fsync(parent_fd)
            return _snapshot_symlink_dirfd(parent_fd, new_basename, required_uid)
        if kind == "symlink":
            fail("stage candidate for a symlink deployment item is not a symlink; refusing intake")
        if src.st_nlink != 1:
            fail("stage candidate is a multi-link file; refusing intake")
        if src.st_size > MAX_STAGE_CANDIDATE_BYTES:
            fail("stage candidate is oversized; refusing intake")
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_CLOEXEC", 0), dir_fd=stage_fd)
        try:
            pre = os.fstat(fd)
            if not _same_object(src, pre):
                fail("stage candidate changed between stat and open")
            hasher = hashlib.sha256()
            data = bytearray()
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                hasher.update(chunk)
                data.extend(chunk)
                if len(data) > MAX_STAGE_CANDIDATE_BYTES:
                    fail("stage candidate is oversized")
            post = os.fstat(fd)
            if not _same_object(src, post) or _metadata_changed(src, post):
                fail("stage candidate changed during read")
            digest = hasher.hexdigest()
        finally:
            os.close(fd)
        if expected_sha is not None and digest != expected_sha:
            fail("stage candidate content does not match the prepared digest; refusing intake")
        required_mode = 0o644 if kind == "unit" else 0o600
        out = os.open(new_basename, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                      | getattr(os, "O_CLOEXEC", 0), required_mode, dir_fd=parent_fd)
        try:
            os.write(out, bytes(data))
            try:
                os.fchown(out, required_uid, _deploy_group(kind))
            except OSError:
                pass
            # The privileged restore runs under an owner-private umask. Normalize the fixed
            # runtime mode explicitly after creation so installed systemd units are the
            # root:root 0644 objects required by verify.sh, while private user config and
            # workspace candidates remain 0600. Content digest binding is unchanged.
            os.fchmod(out, required_mode)
            os.fsync(out)
        finally:
            os.close(out)
        os.fsync(parent_fd)
        return _snapshot_file_dirfd(parent_fd, new_basename, required_uid)
    finally:
        os.close(stage_fd)


def _stage_candidate_mode(stage_root: str, name: str) -> int | None:
    """Read the exact prepared mode of a stage candidate for a folded present file.

    Descriptor-bound through the stage root dirfd (never by path), requiring a regular
    single-link candidate so the recorded mode is the exact reviewed prepared mode (e.g.
    the 0700 workspace scripts). Returns None when the candidate is absent (desired-absent
    nested item).
    """
    if stage_root is None:
        return None
    if not _deploy_path_ok(stage_root):
        fail("stage root is not a safe absolute normalized path")
    try:
        stage_fd = os.open(stage_root, os.O_RDONLY | os.O_DIRECTORY
                           | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        fail("stage root is not an accessible directory: " + stage_root)
    try:
        if not stat.S_ISDIR(os.fstat(stage_fd).st_mode):
            fail("stage root is not a directory")
        src = _stat_nofollow(stage_fd, name)
        if src is None:
            return None
        if not stat.S_ISREG(src.st_mode) or src.st_nlink != 1:
            fail("stage candidate for a folded file item is unsafe")
        if stat.S_IMODE(src.st_mode) & (stat.S_ISUID | stat.S_ISGID) or stat.S_IMODE(src.st_mode) & 0o022:
            fail("stage candidate for a folded file item has an unsafe mode")
        return stat.S_IMODE(src.st_mode)
    finally:
        os.close(stage_fd)


def _stage_sibling_mode(new_path: str, parent_ev: dict[str, Any]) -> int | None:
    """Read the exact prepared mode of a folded file's staged newPath sibling (no --stage).

    Used only by the isolated no-stage harness where the prepared candidate is already staged
    at the newPath sibling; production always supplies the migration stage root. Requires a
    regular single-link candidate with a safe mode and returns its exact mode, or None when
    absent.
    """
    parent_fd = _open_parent_validated(new_path, parent_ev)
    try:
        st = _stat_nofollow(parent_fd, os.path.basename(new_path))
        if st is None:
            return None
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            fail("folded file item's staged newPath sibling is unsafe")
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o022 or mode & (stat.S_ISUID | stat.S_ISGID):
            fail("folded file item's staged newPath sibling has an unsafe mode")
        return mode
    finally:
        os.close(parent_fd)


def _intake_deployment_item(job: dict[str, Any], stage_root: str | None) -> dict[str, Any]:
    """Intake one standalone desired-present deployment item, creating its newPath sibling.

    Runs descriptor-bound through the bound parent dirfd, exactly as the single-shot arm
    intake did: when ``stage_root`` is supplied, the helper copies the prepared candidate
    from the non-live stage and verifies its digest (binding); otherwise the prepared object
    must already be staged at the newPath sibling. Returns the frozen newEvidence snapshot.
    This is the ONLY mutation that creates a deployment newPath, and it is called only after
    the armed journal durably owns the intake intent (every pure invariant already passed).
    """
    parent_fd = _open_parent_validated(job["path"], job["parent"])
    try:
        new_basename = os.path.basename(job["new_path"])
        kind = job["kind"]
        if kind == "symlink":
            if stage_root is not None:
                return _intake_stage_candidate(stage_root, new_basename, parent_fd, job["new_path"],
                                               kind, None, job["target"], job["required_uid"])
            if _stat_nofollow(parent_fd, new_basename) is None:
                fail("prepared new deployment object is missing; refusing intake")
            return _snapshot_symlink_dirfd(parent_fd, new_basename, job["required_uid"])
        if stage_root is not None:
            return _intake_stage_candidate(stage_root, new_basename, parent_fd, job["new_path"],
                                           kind, job["sha256"], None, job["required_uid"])
        if _stat_nofollow(parent_fd, new_basename) is None:
            fail("prepared new deployment object is missing; refusing intake")
        return _snapshot_file_dirfd(parent_fd, new_basename, job["required_uid"])
    finally:
        os.close(parent_fd)


def _arm_deployment_items(raw_items: list[Any], stage_root: str | None = None,
                          destinations: list[str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate and classify deployment items from the arm spec (PURE, no intake).

    Returns (records, intake_jobs). Classification against the authenticated private-root
    ``destinations`` runs as a PURE invariant for EVERY item before any mutation, so a
    pre-arm validation or classification failure creates no deployment newPath. An item
    overlapping exactly one private root is FOLDED into that root (recorded with the owning
    ``rootIndex`` and relative subpath, composed into the root's staging tree by the
    orchestrator before the root copy/swap; no standalone install). Any other overlap
    (ambiguous multi-root, inverse, unsafe) is refused by classification. A standalone
    desired-present item is recorded with a durable ``intake-pending`` newEvidence marker and
    an intake job; the caller writes the armed journal (owning the intake) BEFORE invoking
    ``_intake_deployment_item`` for each job, so a hard kill mid-intake leaves the journal
    with per-item evidence sufficient for deterministic cleanup/recovery. Desired-absent
    items (sha256 None) are recorded directly.
    """
    destinations = destinations or []
    plans = []
    for item in raw_items:
        if not isinstance(item, dict):
            fail("deployment item is malformed")
        if not DEPLOY_SPEC_ITEM_KEYS.issuperset(item.keys()):
            fail("deployment spec item contains unsupported keys")
        if not DEPLOY_SPEC_BASE_KEYS.issubset(item.keys()):
            fail("deployment spec item is missing a required key")
        kind = item["kind"]
        if kind not in DEPLOY_KINDS:
            fail("deployment item kind is unsupported")
        path, old_path, new_path = item["path"], item["oldPath"], item["newPath"]
        if not (_deploy_path_ok(path) and _deploy_path_ok(old_path) and _deploy_path_ok(new_path)):
            fail("deployment path must be an absolute normalized non-root path with no glob/traversal")
        if kind == "unit" and not _unit_path_ok(path):
            fail("unit deployment item must be within the fixed Pixel unit contract")
        if os.path.dirname(old_path) != os.path.dirname(path) or os.path.dirname(new_path) != os.path.dirname(path):
            fail("deployment oldPath and newPath must be siblings of the deployment path")
        if new_path != _deploy_new_path(old_path):
            fail("deployment newPath is not exactly derived from its oldPath")
        if new_path == path or old_path == new_path:
            fail("deployment newPath must not collide with the path or oldPath")
        had_old = item["hadOld"]
        if had_old not in (0, 1):
            fail("deployment hadOld must be the literal 0 or 1")
        folded, root_index, rel = _deployment_root_overlap(path, destinations)
        if folded:
            if kind == "symlink" and item.get("target") is None:
                fail("folded symlink item must carry a target")
            if kind != "symlink" and "sha256" not in item:
                fail("folded file item must carry a sha256 bind")
        plans.append((item, kind, path, old_path, new_path, had_old, folded, root_index, rel))
    result: list[dict[str, Any]] = []
    intake_jobs: list[dict[str, Any]] = []
    for (item, kind, path, old_path, new_path, had_old, folded, root_index, rel) in plans:
        evidence, parent_ev = _validate_deployment_live_state(kind, path, old_path, had_old)
        required_uid = _deploy_owner(kind)
        if folded:
            if kind == "symlink":
                rec = {"kind": kind, "path": path, "oldPath": old_path, "newPath": new_path,
                       "hadOld": had_old, "evidence": evidence, "parent": parent_ev,
                       "folded": True, "rootIndex": root_index, "relative": rel,
                       "sha256": None, "target": item.get("target"), "mode": None,
                       "progress": "pending", "installProgress": "complete"}
            elif item["sha256"] is None:
                rec = {"kind": kind, "path": path, "oldPath": old_path, "newPath": new_path,
                       "hadOld": had_old, "evidence": evidence, "parent": parent_ev,
                       "folded": True, "rootIndex": root_index, "relative": rel,
                       "sha256": None, "target": None, "mode": None,
                       "progress": "pending", "installProgress": "complete"}
            else:
                if stage_root is not None:
                    mode = _stage_candidate_mode(stage_root, os.path.basename(new_path))
                else:
                    mode = _stage_sibling_mode(new_path, parent_ev)
                if mode is None:
                    fail("folded file item's staged candidate is missing; refusing to arm")
                rec = {"kind": kind, "path": path, "oldPath": old_path, "newPath": new_path,
                       "hadOld": had_old, "evidence": evidence, "parent": parent_ev,
                       "folded": True, "rootIndex": root_index, "relative": rel,
                       "sha256": item["sha256"], "target": None, "mode": mode,
                       "progress": "pending", "installProgress": "complete"}
            result.append(rec)
            continue
        is_absent = (kind != "symlink" and "sha256" in item and item["sha256"] is None)
        if not is_absent:
            # Validate the standalone symlink/file required fields PURELY before intake
            # (before any newPath is created). On the production --stage path every present
            # standalone symlink must carry its target and every present standalone file its
            # content digest, so a malformed item is refused before the armed journal owns an
            # intake that could never complete. A legacy no-stage item without an explicit
            # bind remains compatible (its prepared object is already staged at the newPath
            # sibling and verified by snapshot at intake).
            if stage_root is not None:
                if kind == "symlink" and not isinstance(item.get("target"), str):
                    fail("standalone symlink item must carry a target for stage intake")
                if kind != "symlink" and not (isinstance(item.get("sha256"), str)
                                              and HASH_RE.fullmatch(item["sha256"])):
                    fail("standalone file item must carry a sha256 bind for stage intake")
            # Bind the exact expected owner (uid/gid) and prepared mode durably into the
            # intake-pending marker so interrupted-intake cleanup never deletes a same-content
            # object with the wrong owner or mode. The armed journal owns the intake before
            # any newPath is created. (A legacy spec item without an explicit ``sha256`` is
            # still desired-present: its prepared object is already staged at the newPath
            # sibling and verified by snapshot.)
            if kind == "symlink":
                pending_mode = None
            elif stage_root is not None:
                pending_mode = 0o600
            else:
                pending_mode = _stage_sibling_mode(new_path, parent_ev)
                if pending_mode is None:
                    # A new no-stage desired-present file with no prepared sibling could
                    # never be intaken; refusing before the journal write avoids durably
                    # arming an impossible intake (legacy journals remain read-compatible).
                    fail("standalone desired-present file item's prepared sibling is missing; refusing to arm")
            pending_ev = {"type": "intake-pending", "sha256": item.get("sha256"),
                          "target": item.get("target"), "uid": required_uid,
                          "gid": _invoking_gid(), "mode": pending_mode}
            rec = {"kind": kind, "path": path, "oldPath": old_path, "newPath": new_path,
                   "hadOld": had_old, "evidence": evidence,
                   "newEvidence": pending_ev,
                   "parent": parent_ev, "progress": "pending", "installProgress": "pending"}
            result.append(rec)
            intake_jobs.append({"index": len(result) - 1, "kind": kind, "path": path,
                                "new_path": new_path, "sha256": item.get("sha256"),
                                "target": item.get("target"), "required_uid": required_uid,
                                "parent": parent_ev})
        else:
            # Standalone desired-absent item: no candidate, final state is absence.
            result.append({"kind": kind, "path": path, "oldPath": old_path, "newPath": new_path,
                           "hadOld": had_old, "evidence": evidence, "newEvidence": {"type": "absent"},
                           "parent": parent_ev, "progress": "pending", "installProgress": "pending"})
    return result, intake_jobs


def _load_deployment_spec(spec_file: str) -> tuple[list[Any], str, dict[str, Any]]:
    """Descriptor-bound, race-free intake of the deployment-items spec.

    Opens through a bound safe parent dirfd with O_NOFOLLOW|O_CLOEXEC, requires an absolute
    normalized path to a regular single-link file owned by the invoking user with a safe
    mode, and verifies stable fstat identity/size/mtime/ctime before and after the complete
    read. Parses only the frozen bytes and returns (items, sha256_of_frozen_bytes,
    serviceDesired) so the armed transaction can be audited without retaining the content.
    """
    if not _deploy_path_ok(spec_file):
        fail("deployment spec path must be an absolute normalized non-root path")
    required_uid = _invoking_uid()
    parent_fd = _open_parent_dirfd(spec_file, required_uid)
    try:
        basename = os.path.basename(spec_file)
        pre = _stat_nofollow(parent_fd, basename)
        if pre is None:
            fail("deployment spec file is missing")
        if not stat.S_ISREG(pre.st_mode):
            fail("deployment spec must be a regular single-link file")
        if pre.st_nlink != 1:
            fail("deployment spec must be a regular single-link file")
        if pre.st_uid != required_uid:
            fail("deployment spec must be owned by the invoking user")
        mode = stat.S_IMODE(pre.st_mode)
        if mode & 0o022 or mode & (stat.S_ISUID | stat.S_ISGID):
            fail("deployment spec mode is unsafe")
        fd = os.open(basename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
        try:
            first = os.fstat(fd)
            if not _same_object(pre, first):
                fail("deployment spec changed between stat and open (identity)")
            data = bytearray()
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_JSON_BYTES:
                    fail("deployment spec is oversized")
            final = os.fstat(fd)
            if not _same_object(pre, final) or _metadata_changed(pre, final):
                fail("deployment spec changed during read")
            payload = bytes(data)
            digest = hashlib.sha256(payload).hexdigest()
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)
    try:
        spec = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError):
        fail("deployment spec is not valid JSON")
    if not isinstance(spec, dict) or not (
            set(spec.keys()) == {"deploymentItems"}
            or set(spec.keys()) == {"deploymentItems", "serviceDesired"}):
        fail("deployment spec must contain exactly deploymentItems (and optionally serviceDesired)")
    items = spec["deploymentItems"]
    if not isinstance(items, list):
        fail("deploymentItems must be a list")
    service_desired = spec.get("serviceDesired")
    if service_desired is not None:
        if not isinstance(service_desired, dict) or not service_desired:
            fail("serviceDesired must be a non-empty object")
        for unit, rec in service_desired.items():
            if not isinstance(unit, str) or not isinstance(rec, dict) or set(rec.keys()) != {"enabled", "active"}:
                fail("serviceDesired entry is malformed")
            if not isinstance(rec.get("enabled"), bool) or not isinstance(rec.get("active"), bool):
                fail("serviceDesired entry must be boolean")
    return items, digest, service_desired


def _check_deployment_evidence(kind: str, evidence: Any) -> None:
    if not isinstance(evidence, dict):
        fail("deployment evidence is malformed")
    if evidence.get("type") == "intake-pending":
        # Intermediate arm state: the armed journal durably owns a standalone intake that has
        # not yet created its newPath. It carries the prepared spec content bind (sha256 for a
        # file, target for a symlink) so a hard-kill mid-intake can be cleaned up
        # deterministically: an intaken newPath is removed only when it exactly matches the
        # bind. Only meaningful while the journal's armIntakeComplete is false; install/commit
        # refuse it and rollback cleans it up deterministically.
        extra = set(evidence) - {"type"}
        if extra - {"sha256", "target", "uid", "gid", "mode"}:
            fail("intake-pending deployment evidence is malformed")
        if evidence.get("sha256") is not None and not (isinstance(evidence["sha256"], str) and HASH_RE.fullmatch(evidence["sha256"])):
            fail("intake-pending file hash is malformed")
        if evidence.get("target") is not None and (not isinstance(evidence["target"], str) or "\n" in evidence["target"]):
            fail("intake-pending symlink target is malformed")
        for key in ("uid", "gid", "mode"):
            if key in evidence and evidence[key] is not None \
               and (not isinstance(evidence[key], int) or isinstance(evidence[key], bool)):
                fail("intake-pending owner/mode evidence is not an exact integer")
        return
    if evidence.get("type") == "absent":
        # Desired-absent transaction: the final installed state is "path absent".
        if set(evidence) != {"type"}:
            fail("absent deployment evidence is malformed")
        return
    if kind == "symlink":
        for key in ("type", "target", "uid", "gid", "mode", "ino", "dev", "nlink",
                    "mtime_ns", "ctime_ns"):
            if key not in evidence:
                fail("deployment symlink evidence is incomplete")
        if evidence["type"] != "symlink":
            fail("deployment symlink evidence type mismatch")
        if not isinstance(evidence["target"], str) or "\n" in evidence["target"]:
            fail("deployment symlink target is malformed")
        for key in ("uid", "gid", "mode", "ino", "dev", "nlink", "mtime_ns", "ctime_ns"):
            if not isinstance(evidence[key], int) or isinstance(evidence[key], bool):
                fail("deployment symlink evidence is not an exact integer")
        return
    for key in ("type", "sha256", "uid", "gid", "mode", "ino", "dev", "size", "nlink",
                "mtime_ns", "ctime_ns"):
        if key not in evidence:
            fail("deployment file evidence is incomplete")
    if evidence["type"] != "file":
        fail("deployment file evidence type mismatch")
    if not isinstance(evidence["sha256"], str) or not HASH_RE.fullmatch(evidence["sha256"]):
        fail("deployment file hash is malformed")
    for key in ("uid", "gid", "mode", "ino", "dev", "size", "nlink", "mtime_ns", "ctime_ns"):
        if not isinstance(evidence[key], int) or isinstance(evidence[key], bool):
            fail("deployment file evidence is not an exact integer")


def _validate_folded_item(item: dict[str, Any], seen: set, all_deploy_paths: list[str]) -> None:
    """Validate a folded deployment item (owned by a whole private-root swap).

    A folded item is composed into the owning root's non-live restore staging tree before the
    whole-root copy/swap, so it has NO standalone newPath install and NO newEvidence snapshot.
    Commit verifies the composed live state; rollback is covered by the owning root swap.
    """
    kind = item["kind"]
    path, old_path, new_path = item["path"], item["oldPath"], item["newPath"]
    if not (_deploy_path_ok(path) and _deploy_path_ok(old_path) and _deploy_path_ok(new_path)):
        fail("deployment path must be an absolute normalized non-root path with no glob/traversal")
    if kind == "unit" and not _unit_path_ok(path):
        fail("unit deployment item must be within the fixed Pixel unit contract")
    if os.path.dirname(old_path) != os.path.dirname(path) or os.path.dirname(new_path) != os.path.dirname(path):
        fail("deployment oldPath and newPath must be siblings of the deployment path")
    stem = _derived_stem(path)
    exact_old = re.compile(r"^" + re.escape(stem) + r"-(\d+)-(\d+)\.old$")
    if not exact_old.fullmatch(old_path):
        fail("deployment oldPath is not exactly derived from its deployment path")
    if new_path != _deploy_new_path(old_path):
        fail("deployment newPath is not exactly derived from its oldPath")
    if (old_path == path or new_path == path or old_path == new_path
            or path.startswith(old_path + "/") or old_path.startswith(path + "/")
            or path.startswith(new_path + "/") or new_path.startswith(path + "/")):
        fail("deployment path, oldPath and newPath must not collide or nest")
    if not isinstance(item.get("rootIndex"), int) or isinstance(item.get("rootIndex"), bool) \
            or item["rootIndex"] < 0:
        fail("folded deployment item must carry a non-negative owning root index")
    if not isinstance(item.get("relative"), str):
        fail("folded deployment item must carry a relative subpath below its root")
    if item["relative"]:
        if "/../" in item["relative"] or item["relative"].startswith("../") or item["relative"].endswith("/.."):
            fail("folded deployment item relative subpath is unsafe")
    _check_parent_evidence(item["parent"])
    had_old = item["hadOld"]
    if had_old not in (0, 1):
        fail("deployment hadOld must be 0 or 1")
    if had_old == 1:
        _check_deployment_evidence(kind, item["evidence"])
    elif item["evidence"] is not None:
        fail("hadOld=0 deployment item must not carry evidence")
    if kind == "symlink":
        if not isinstance(item.get("target"), str) or "\n" in item["target"]:
            fail("folded symlink target is malformed")
    else:
        if "sha256" in item and item["sha256"] is not None:
            if not isinstance(item["sha256"], str) or not HASH_RE.fullmatch(item["sha256"]):
                fail("folded deployment file hash is malformed")
            if not isinstance(item.get("mode"), int) or isinstance(item.get("mode"), bool):
                fail("folded deployment file mode is malformed")
        elif item.get("sha256") is None:
            if item.get("mode") is not None:
                fail("folded absent item must not carry a mode")
        else:
            fail("folded deployment file must carry a sha256 bind")
    if item["installProgress"] != "complete":
        fail("folded deployment item installProgress must be complete (root swap owns it)")
    if item["progress"] not in {"pending", "in-progress", "restored"}:
        fail("deployment item progress is malformed")
    if path in seen:
        fail("deployment item paths must be unique")
    seen.add(path)
    all_deploy_paths.extend((path, old_path, new_path))


def _validate_deployment_items(items: Any, intake_complete: bool = True) -> list[Any]:
    if not isinstance(items, list):
        fail("deploymentItems must be a list")
    seen = set()
    all_deploy_paths: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            fail("deployment item must be an object")
        if set(item.keys()) == DEPLOY_ITEM_KEYS_FOLDED:
            _validate_folded_item(item, seen, all_deploy_paths)
            continue
        if set(item.keys()) != DEPLOY_ITEM_KEYS:
            fail("deployment item must contain exactly the deployment item keys")
        kind = item["kind"]
        if kind not in DEPLOY_KINDS:
            fail("deployment item kind is unsupported")
        path, old_path, new_path = item["path"], item["oldPath"], item["newPath"]
        if not (_deploy_path_ok(path) and _deploy_path_ok(old_path) and _deploy_path_ok(new_path)):
            fail("deployment path must be an absolute normalized non-root path with no glob/traversal")
        if kind == "unit" and not _unit_path_ok(path):
            fail("unit deployment item must be within the fixed Pixel unit contract")
        if os.path.dirname(old_path) != os.path.dirname(path) or os.path.dirname(new_path) != os.path.dirname(path):
            fail("deployment oldPath and newPath must be siblings of the deployment path")
        stem = _derived_stem(path)
        exact_old = re.compile(r"^" + re.escape(stem) + r"-(\d+)-(\d+)\.old$")
        if not exact_old.fullmatch(old_path):
            fail("deployment oldPath is not exactly derived from its deployment path")
        if new_path != _deploy_new_path(old_path):
            fail("deployment newPath is not exactly derived from its oldPath")
        if (old_path == path or new_path == path or old_path == new_path
                or path.startswith(old_path + "/") or old_path.startswith(path + "/")
                or path.startswith(new_path + "/") or new_path.startswith(path + "/")):
            fail("deployment path, oldPath and newPath must not collide or nest")
        _check_parent_evidence(item["parent"])
        had_old = item["hadOld"]
        if had_old not in (0, 1):
            fail("deployment hadOld must be 0 or 1")
        if had_old == 1:
            _check_deployment_evidence(kind, item["evidence"])
        elif item["evidence"] is not None:
            fail("hadOld=0 deployment item must not carry evidence")
        _check_deployment_evidence(kind, item["newEvidence"])
        # While the journal owns an in-progress intake (armIntakeComplete false) a standalone
        # item may be either already intaken (real evidence) or still pending. Once the
        # journal is intake-complete, no item may remain pending.
        if intake_complete and item["newEvidence"].get("type") == "intake-pending":
            fail("standalone deployment item intake was never completed; refusing an unarmed journal")
        if item["installProgress"] not in INSTALL_PROGRESS_STATES:
            fail("deployment item installProgress is malformed")
        if item["progress"] not in {"pending", "in-progress", "restored"}:
            fail("deployment item progress is malformed")
        if path in seen:
            fail("deployment item paths must be unique")
        seen.add(path)
        all_deploy_paths.append(path)
        all_deploy_paths.append(old_path)
        all_deploy_paths.append(new_path)
    # Uniqueness and pairwise non-overlap/collision across every deployment path, oldPath
    # and newPath.
    for left in range(len(all_deploy_paths)):
        for right in range(left + 1, len(all_deploy_paths)):
            if _paths_collide(all_deploy_paths[left], all_deploy_paths[right]):
                fail("deployment paths/oldPaths/newPaths must be pairwise non-overlapping")
    return items


def _hash_matches_dirfd(parent_fd: int, basename: str, st: Any, expected: str) -> bool:
    """Stable descriptor-bound hash of a basename via dirfd; False on any change."""
    try:
        fd = os.open(basename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
    except OSError:
        return False
    try:
        first = os.fstat(fd)
        if not _same_object(st, first):
            return False
        hasher = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            hasher.update(chunk)
        final = os.fstat(fd)
        if not _same_object(st, final) or _metadata_changed(first, final):
            return False
        return hasher.hexdigest() == expected
    except OSError:
        return False
    finally:
        os.close(fd)


def _item_evidence_matches(evidence: dict[str, Any], parent_fd: int, basename: str) -> bool:
    """Compare exact recorded deployment evidence to a basename via the bound parent dirfd.

    Compares all exact metadata (uid, gid, mode, dev, ino, type, nlink, size, mtime/ctime,
    and content hash for files; target and complete metadata for symlinks) using
    descriptor-bound stable reads. Refuses indeterminate state by returning False whenever
    the live object no longer matches the recorded evidence.
    """
    st = _stat_nofollow(parent_fd, basename)
    if evidence.get("type") == "absent":
        return st is None
    if st is None:
        return False
    if not (st.st_dev == evidence["dev"] and st.st_ino == evidence["ino"]
            and _kind_of(st) == evidence["type"]
            and st.st_uid == evidence["uid"] and st.st_gid == evidence["gid"]
            and stat.S_IMODE(st.st_mode) == evidence["mode"]
            and st.st_nlink == evidence["nlink"]):
        return False
    if evidence["type"] == "file":
        if st.st_size != evidence["size"]:
            return False
        return _hash_matches_dirfd(parent_fd, basename, st, evidence["sha256"])
    if evidence["type"] == "symlink":
        try:
            return os.readlink(basename, dir_fd=parent_fd) == evidence["target"]
        except OSError:
            return False
    return False


def _validate_arm_state(
    destinations: list[str], old_paths: list[str], temporary_paths: list[str], had_old: list[int],
) -> list[dict[str, Any] | None]:
    """Fail closed on live-state that would make deterministic rollback impossible.

    Called under the exclusive custody lock before the armed journal is atomically written.
    Because arm precedes any live destination rename, the exact preconditions rollback will
    later rely on must already hold: every hadOld=1 destination still exists with evidence,
    every hadOld=0 destination is still absent, no oldPath pre-exists (each oldPath must be
    created solely by the swap's destination rename), and every prepared temporary path is
    present and a safe top-level temp root: a non-symlink directory, or a regular file with a
    single hard link. A symlink, device/fifo/socket, or regular file with multiple links is
    refused so deterministic byte-exact rollback is never predicated on an attacker-controlled
    or aliased root. Returns the old-snapshot evidence captured from each live destination
    while it is still at destination (the inode/device/type follows the content to oldPath if
    moved).
    """
    old_evidence: list[dict[str, Any] | None] = []
    temporary_evidence: list[dict[str, Any]] = []
    for index, destination in enumerate(destinations):
        dest_evidence = _evidence(destination)
        if had_old[index] == 1:
            if dest_evidence is None:
                fail("hadOld=1 but the live destination is missing; refusing to arm")
            old_evidence.append(dest_evidence)
        elif dest_evidence is not None:
            fail("hadOld=0 but the live destination exists; refusing to arm")
        else:
            old_evidence.append(None)
        if _evidence(old_paths[index]) is not None:
            fail("oldPath already exists; refusing to arm")
        temp_evidence = _evidence(temporary_paths[index])
        if temp_evidence is None:
            fail("prepared temporary path is missing; refusing to arm")
        if temp_evidence["type"] == "dir":
            pass
        elif temp_evidence["type"] == "file" and temp_evidence.get("nlink") == 1:
            pass
        elif temp_evidence["type"] == "file":
            fail("prepared temporary path is unsafe (multi-link regular file); refusing to arm")
        else:
            fail("prepared temporary path is unsafe; refusing to arm")
        # Persist the exact prepared temporary evidence so rollback/commit can later prove a
        # live destination (or the temporary path itself) is exactly the prepared object
        # before ever removing it, and a commit cannot happen after only a partial swap.
        temporary_evidence.append(temp_evidence)
    return old_evidence, temporary_evidence


def _fold_descend(stage_fd: int, rel: str, *, create: bool) -> int:
    """Descend into a relative subpath below the stage root dirfd, returning the child dirfd.

    Walks each component with O_DIRECTORY|O_NOFOLLOW, refusing any symlink component (a
    symlink ancestor would escape the composed stage tree). When ``create`` is set, missing
    directories are created owner-private (0700), chowned to the exact invoking uid/gid, and
    fsynced BEFORE they can receive content; an existing restored parent's mode is never
    mutated. Otherwise a missing component fails. The returned dirfd is the immediate parent
    of the final component; the caller's ``stage_fd`` is never closed and intermediate
    children are closed before returning.
    """
    parts = [x for x in rel.split("/") if x]
    fds = [stage_fd]
    for part in parts:
        parent = fds[-1]
        try:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY
                            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=parent)
        except FileNotFoundError:
            if not create:
                raise
            os.mkdir(part, 0o700, dir_fd=parent)
            # Secure the newly created missing ancestor (exact invoking uid/gid + 0700)
            # before any content can be placed inside it. Never mutate an existing parent.
            invoking_uid = _invoking_uid()
            invoking_gid = _invoking_gid()
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY
                            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=parent)
            secured = None
            try:
                os.fchown(child, invoking_uid, invoking_gid)
                os.fchmod(child, 0o700)
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode) \
                   or info.st_uid != invoking_uid or info.st_gid != invoking_gid \
                   or stat.S_IMODE(info.st_mode) != 0o700:
                    fail("fold could not secure a newly created ancestor directory")
                os.fsync(child)
                secured = info
            finally:
                os.close(child)
            os.fsync(parent)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY
                            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=parent)
            reopened = os.fstat(child)
            if not _same_object(secured, reopened):
                os.close(child)
                fail("fold newly created ancestor was substituted after securing")
        except OSError:
            fail("fold could not descend into the restore stage tree: " + part)
        fds.append(child)
    result = fds[-1]
    for fd in fds[1:-1]:
        os.close(fd)
    return result


def _cleanup_fold_temp(parent_fd: int, tmp_name: str | None, created_id) -> None:
    """Remove exactly the temporary object this invocation created; surface any mismatch.

    ``tmp_name`` is only ever unlinked when it still carries the exact no-follow identity
    (dev/ino/type) this invocation created. A missing or substituted temp is indeterminate
    and surfaced, never silently unlinked or swallowed.
    """
    if tmp_name is None or created_id is None:
        return
    st = _stat_nofollow(parent_fd, tmp_name)
    if st is None:
        fail("fold temp is missing during cleanup; live state is indeterminate")
    if not _same_object(created_id, st):
        fail("fold temp was substituted during cleanup; live state is indeterminate")
    os.unlink(tmp_name, dir_fd=parent_fd)
    os.fsync(parent_fd)


_fold_tmp_seq = [0]


def _fold_tmp_name(basename: str) -> str:
    """A unique same-parent temporary name for a folded compose (never follows a symlink)."""
    _fold_tmp_seq[0] += 1
    return f".{basename}.pixel-fold-{os.getpid()}-{_fold_tmp_seq[0]}.tmp"


def _fold_target_replaceable(parent_fd: int, basename: str) -> None:
    """Refuse an unsafe existing folded compose target (never recurses, never rm -rf).

    A folded item may replace an existing non-directory file or symlink in the non-live
    restore stage tree (the extracted backup already contains the old exact-root/nested
    object), but a directory, an unsafe object type, or a multi-link file is refused:
    replacing those would either recurse into a subtree or silently drop an inode the old
    object shares.
    """
    st = _stat_nofollow(parent_fd, basename)
    if st is None:
        return
    if stat.S_ISDIR(st.st_mode):
        fail("fold target is a directory in the restore stage tree; refusing to overwrite")
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
        fail("fold target is an unsafe object type in the restore stage tree; refusing to overwrite")
    if stat.S_ISREG(st.st_mode) and st.st_nlink != 1:
        fail("fold target is a multi-link file; refusing to overwrite")


def _verify_fold_temp_file(parent_fd: int, tmp_name: str, tmp_created: Any,
                            expected_sha: str, mode: int, invoking_uid: int,
                            invoking_gid: int, expected_size: int) -> None:
    """Prove the fold temp is EXACTLY the object this invocation created before it may rename.

    Opens the temp through the bound parent dirfd with O_NOFOLLOW|O_CLOEXEC (never by path)
    and refuses unless the descriptor identity still matches the exact dev/ino/type captured
    when the temp was created (``tmp_created``), with stable pre/post metadata, the exact
    single-link/owner/mode/size, and a descriptor-bound digest equal to the prepared sha.
    A same-user race that substitutes same-size/owner/mode content after ``out_fd`` closes is
    therefore refused BEFORE the authenticated old stage target is replaced, never caught
    only after a post-rename failure.
    """
    tmp_st = _stat_nofollow(parent_fd, tmp_name)
    if tmp_st is None or not stat.S_ISREG(tmp_st.st_mode) or tmp_st.st_nlink != 1:
        fail("fold temp is not a single-link regular file before rename")
    if not _same_object(tmp_created, tmp_st):
        fail("fold temp was substituted before rename (identity)")
    if tmp_st.st_uid != invoking_uid or tmp_st.st_gid != invoking_gid:
        fail("fold temp owner does not match the invoking owner before rename")
    if stat.S_IMODE(tmp_st.st_mode) != mode:
        fail("fold temp mode does not match the prepared mode before rename")
    if tmp_st.st_size != expected_size:
        fail("fold temp size does not match the candidate before rename")
    fd = os.open(tmp_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
    try:
        first = os.fstat(fd)
        if not _same_object(tmp_created, first):
            fail("fold temp changed between stat and open (identity)")
        hasher = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            hasher.update(chunk)
        final = os.fstat(fd)
        if not _same_object(tmp_created, final) or _metadata_changed(first, final):
            fail("fold temp changed during read")
        if hasher.hexdigest() != expected_sha:
            fail("fold temp content digest does not match the prepared candidate before rename")
    finally:
        os.close(fd)


def _fold_compose_file(stage_fd: int, rel: str, migration_stage: str, name: str,
                       expected_sha: str, mode: int) -> None:
    """Compose a present folded file into the restore stage tree, preserving content + mode.

    Runs in the non-live restore stage tree under sudo. An existing non-directory file or
    symlink at the target is replaced atomically via a verified same-parent temporary
    (write with a complete-write loop, fchmod, fchown to the exact invoking owner, fsync,
    then rename) so the composed object carries the exact prepared mode AND the exact trusted
    invoking owner (uid + gid), never a root-owned object. Directories, unsafe object types,
    and multi-link targets are refused (never recursed). The source candidate is size-bounded
    with stable pre/post metadata verification, the full content is written and hashed during
    the same stable read, and the temporary is verified for exact owner, mode, type, single
    link, size, and digest BEFORE the atomic rename publishes it; the composed object is
    verified again after rename.
    """
    parent_fd = _fold_descend(stage_fd, os.path.dirname(rel), create=True)
    try:
        basename = os.path.basename(rel)
        _fold_target_replaceable(parent_fd, basename)
        invoking_uid = _invoking_uid()
        invoking_gid = _invoking_gid()
        # Read the prepared candidate from the migration stage, size-bounded.
        ms_fd = os.open(migration_stage, os.O_RDONLY | os.O_DIRECTORY
                        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            src = _stat_nofollow(ms_fd, name)
            if src is None or not stat.S_ISREG(src.st_mode) or src.st_nlink != 1:
                fail("fold candidate is missing or unsafe in the migration stage")
            if src.st_size > MAX_STAGE_CANDIDATE_BYTES:
                fail("fold candidate is oversized")
            src_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_CLOEXEC", 0), dir_fd=ms_fd)
        finally:
            os.close(ms_fd)
        out_fd = None
        tmp_name = None
        tmp_created = None
        try:
            pre = os.fstat(src_fd)
            if not _same_object(src, pre):
                fail("fold candidate changed between stat and open")
            tmp_name = _fold_tmp_name(basename)
            out_fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=parent_fd)
            tmp_created = os.fstat(out_fd)
            written = 0
            hasher = hashlib.sha256()
            while True:
                chunk = os.read(src_fd, 65536)
                if not chunk:
                    break
                offset = 0
                while offset < len(chunk):
                    n = os.write(out_fd, chunk[offset:])
                    if n is None or n <= 0:
                        fail("fold candidate could not be fully written (short/zero write)")
                    offset += n
                hasher.update(chunk)
                written += len(chunk)
            post = os.fstat(src_fd)
            if not _same_object(src, post) or _metadata_changed(src, post):
                fail("fold candidate changed during read")
            if written != src.st_size:
                fail("fold candidate was not fully written")
            if hasher.hexdigest() != expected_sha:
                fail("fold candidate digest mismatch during copy")
            os.fchmod(out_fd, mode)
            os.fchown(out_fd, invoking_uid, invoking_gid)
            os.fsync(out_fd)
            os.close(out_fd)
            out_fd = None
            # Verify the exact temporary BEFORE the atomic rename publishes it: prove it is
            # still exactly the object this invocation created (dev/ino/type) with the exact
            # single-link/owner/mode/size AND a descriptor-bound digest equal to the prepared
            # sha, so a same-user substitution after out_fd closes can never replace the
            # authenticated old stage target.
            _verify_fold_temp_file(parent_fd, tmp_name, tmp_created, expected_sha,
                                   mode, invoking_uid, invoking_gid, src.st_size)
            os.rename(tmp_name, basename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            tmp_name = None
            tmp_created = None
            os.fsync(parent_fd)
        finally:
            os.close(src_fd)
            if out_fd is not None:
                os.close(out_fd)
            _cleanup_fold_temp(parent_fd, tmp_name, tmp_created)
        # Verify the composed object: exact owner, exact prepared mode, single link, digest.
        st = _stat_nofollow(parent_fd, basename)
        if st is None or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            fail("fold composed candidate is not a single-link regular file")
        if st.st_uid != invoking_uid or st.st_gid != invoking_gid:
            fail("fold composed candidate owner does not match the invoking owner")
        if stat.S_IMODE(st.st_mode) != mode:
            fail("fold composed candidate mode does not match the prepared mode")
        if st.st_size != src.st_size:
            fail("fold composed candidate size does not match the prepared size")
        if not _hash_matches_dirfd(parent_fd, basename, st, expected_sha):
            fail("fold composed candidate digest mismatch")
    finally:
        os.close(parent_fd)


def _fold_compose_symlink(stage_fd: int, rel: str, target: str) -> None:
    """Compose a present folded symlink into the restore stage tree.

    Replaces an existing non-directory file/symlink atomically via a same-parent temporary
    symlink (chowned to the exact invoking owner, verified for exact owner + target, then
    renamed) so the composed object is owned by the invoking non-root user, never root. A
    failed owner change fails before publication rather than being ignored until after
    replacement. Directory/unsafe/multi-link targets are refused, never recursed.
    """
    parent_fd = _fold_descend(stage_fd, os.path.dirname(rel), create=True)
    try:
        basename = os.path.basename(rel)
        _fold_target_replaceable(parent_fd, basename)
        invoking_uid = _invoking_uid()
        invoking_gid = _invoking_gid()
        tmp_name = None
        tmp_created = None
        try:
            tmp_name = _fold_tmp_name(basename)
            os.symlink(target, tmp_name, dir_fd=parent_fd)
            tmp_created = _stat_nofollow(parent_fd, tmp_name)
            # The owner change must succeed and be verified before publication; it is never
            # ignored until after replacement.
            os.lchown(f"/proc/self/fd/{parent_fd}/{tmp_name}", invoking_uid, invoking_gid)
            tmp_st = _stat_nofollow(parent_fd, tmp_name)
            if tmp_st is None or not stat.S_ISLNK(tmp_st.st_mode):
                fail("fold temp is not a symlink before rename")
            if not _same_object(tmp_created, tmp_st):
                fail("fold temp was substituted before rename (identity)")
            if tmp_st.st_uid != invoking_uid or tmp_st.st_gid != invoking_gid:
                fail("fold temp symlink owner does not match the invoking owner before rename")
            if os.readlink(tmp_name, dir_fd=parent_fd) != target:
                fail("fold temp symlink target mismatch before rename")
            os.rename(tmp_name, basename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            tmp_name = None
            tmp_created = None
            os.fsync(parent_fd)
        finally:
            _cleanup_fold_temp(parent_fd, tmp_name, tmp_created)
        # Verify the composed object: exact owner + target.
        st = _stat_nofollow(parent_fd, basename)
        if st is None or not stat.S_ISLNK(st.st_mode):
            fail("fold composed candidate is not a symlink")
        if st.st_uid != invoking_uid or st.st_gid != invoking_gid:
            fail("fold composed symlink owner does not match the invoking owner")
        if os.readlink(basename, dir_fd=parent_fd) != target:
            fail("fold composed symlink target mismatch")
    finally:
        os.close(parent_fd)


def _fold_remove_absent(stage_fd: int, rel: str) -> None:
    """Remove a desired-absent folded item from the restore stage tree (never best-effort).

    A desired-absent item under a missing ancestor is already satisfied and is a no-op
    (as is a missing target). Reuses the same replaceability/type guard as present folded
    compose: only a regular single-link file or symlink may be removed; a directory, an
    unsafe object type (FIFO/socket/device), or a multi-link file is refused, never removed
    recursively.
    """
    try:
        parent_fd = _fold_descend(stage_fd, os.path.dirname(rel), create=False)
    except FileNotFoundError:
        return
    try:
        basename = os.path.basename(rel)
        _fold_target_replaceable(parent_fd, basename)
        if _stat_nofollow(parent_fd, basename) is not None:
            os.unlink(basename, dir_fd=parent_fd)
            os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def cmd_fold(custody: str, spec_file: str, migration_stage: str, restore_stage: str,
             destinations: list[str]) -> None:
    """Compose folded deployment items into the authenticated restore staging tree.

    ``destinations`` are the absolute private-root destination paths (the exact set passed to
    arm). Classification runs as a PURE invariant (no mutation); only items that fold into
    exactly one private root are composed into that root's non-live restore staging tree
    (``restore_stage/<root_relative>/<rel>``) before the whole-root copy/swap. Ambiguous,
    inverse, unsafe-type, traversal, and symlink-ancestor cases are refused. This is a pre-arm
    orchestration step: it never creates a deployment newPath and never mutates live state.
    """
    raw_items, _spec_sha, _sd = _load_deployment_spec(spec_file)
    if not (os.path.isdir(restore_stage)):
        fail("restore stage is not a directory")
    # A deployment fold must validate the COMPLETE reviewed serviceDesired contract before
    # the first stage mutation, matching cmd_arm. A missing/partial/malformed serviceDesired
    # is refused here so the first valid target is never composed.
    if _sd is None:
        fail("a deployment fold must carry the reviewed serviceDesired state")
    _check_service_desired(_sd, require_full_fixed=True)
    # Run the COMPLETE PURE deployment-spec/item validation for ALL items BEFORE the first
    # stage mutation, so a malformed/duplicate/unsupported later item can never leave the
    # non-live stage partially composed. This reuses the exact arm classification
    # (required fields/types, supported kind, exact old/new derivation, live-state/hadOld
    # preconditions, stage candidate binds/modes, folded root ownership, ambiguous/inverse/
    # cross-domain refusal, standalone required fields) and the shared journal-item validator
    # (unique/pairwise path-old-new collisions, folded root ownership/classification) rather
    # than duplicating weak per-item checks. Only the frozen validated folded records are
    # then composed.
    records, _intake_jobs = _arm_deployment_items(
        raw_items, stage_root=migration_stage, destinations=destinations)
    _validate_deployment_items(records, intake_complete=False)
    stage_fd = os.open(restore_stage, os.O_RDONLY | os.O_DIRECTORY
                       | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        folded_plan = [(rec, rec["rootIndex"], rec["relative"]) for rec in records if rec.get("folded")]
        # Compose each folded item under its owning root's stage-relative path (the manifest
        # relative path is the destination with the leading "/" removed).
        for rec, root_index, rel in folded_plan:
            root_rel = destinations[root_index].lstrip("/")
            target_rel = os.path.join(root_rel, rel) if rel else root_rel
            kind = rec["kind"]
            if kind == "symlink":
                _fold_compose_symlink(stage_fd, target_rel, rec["target"])
            elif rec.get("sha256") is None:
                _fold_remove_absent(stage_fd, target_rel)
            else:
                mode = rec.get("mode")
                if mode is None:
                    fail("folded file item's staged candidate is missing in the migration stage")
                _fold_compose_file(stage_fd, target_rel, migration_stage,
                                   os.path.basename(rec["newPath"]), rec["sha256"], mode)
    finally:
        os.close(stage_fd)
    print(json.dumps({"status": "pass", "mode": "fold", "folded": len(folded_plan)}, sort_keys=True))


def _recover_armed_intake(custody_fd: int, basename: str, value: dict[str, Any],
                          reserved: dict[str, Any]) -> None:
    """Deterministically clean a post-journal intake failure and restore the reserved marker.

    On success the armed journal is replaced by the exact captured reservation so the
    wrapper's abort restores the exact service prestate. If the deterministic cleanup or the
    reserved-marker write itself fails (indeterminate state), the armed journal is retained
    conservatively and a specific indeterminate-cleanup error is surfaced chained from the
    cleanup failure - never swallowed, never implying the reserved marker was restored.
    """
    try:
        _cleanup_intake_newpaths(value)
        _write_journal(custody_fd, basename, reserved)
    except BaseException as exc:
        raise SystemExit("migration journal: deterministic intake cleanup could not complete; "
                         "armed journal retained and live state is indeterminate") from exc


def cmd_arm(custody: str, journal: str, token: str, contract: str, backup: str, argv: list[str]) -> None:
    _check_token(token)
    _check_contract(contract)
    _check_backup(backup)
    basename = _check_basename(custody, journal)
    # Optional trailing deployment-items spec: a fixed absolute path to a regular file (a
    # unit name can never start with "/", so this is unambiguous). When present it carries
    # the deployment-state items (active current symlink, single-link config/workspace/unit
    # files, each present or absent) that the same armed transaction protects in addition to
    # the private roots. The spec is strict JSON with exactly a deploymentItems array.
    deployment_spec_file = None
    deployment_spec_sha = None
    stage_root = None
    # Optional --stage STAGEDIR marks the non-live prepare stage root; when present the
    # helper performs descriptor-bound candidate intake during arm (never the restore shell).
    if "--stage" in argv:
        at = argv.index("--stage")
        if at + 1 >= len(argv):
            fail("arm --stage requires a stage root argument")
        stage_root = argv[at + 1]
        del argv[at:at + 2]
        if not _deploy_path_ok(stage_root):
            fail("stage root must be an absolute normalized non-root path")
    if argv and argv[-1].startswith("/"):
        deployment_spec_file = argv[-1]
        argv = argv[:-1]
    count = int(argv[0])
    offset = 1
    destinations = argv[offset:offset + count]; offset += count
    old_paths = argv[offset:offset + count]; offset += count
    temporary_paths = argv[offset:offset + count]; offset += count
    had_old = [_parse_had_old(x) for x in argv[offset:offset + count]]; offset += count
    units = argv[offset:]
    if not (len(destinations) == count and len(old_paths) == count
            and len(temporary_paths) == count and len(had_old) == count):
        fail("transaction arrays are malformed")
    raw_deployment_items = []
    service_desired = None
    if deployment_spec_file is not None:
        raw_deployment_items, deployment_spec_sha, service_desired = _load_deployment_spec(deployment_spec_file)
    custody_fd = lock_custody(custody)
    try:
        fd = _open_journal(custody_fd, basename, os.O_RDWR)
        try:
            _check_owned_regular(fd)
            reserved = read_object(fd)
            if reserved.get("kind") != RESERVED_KIND:
                fail("journal is not in reserved state; refusing to arm a foreign transaction")
            if not RESERVED_KEYS.issubset(reserved.keys()) \
               or not set(reserved.keys()).issubset(RESERVED_KEYS | {"servicePrestate", "units", "quiesced"}):
                fail("reservation is missing or has unexpected keys")
            is_deployment = deployment_spec_file is not None
            _validate_affected_units(units, require_full_fixed=is_deployment)
            service_prestate = reserved.get("servicePrestate")
            if service_prestate is not None:
                _check_service_prestate(service_prestate, units)
            if is_deployment:
                # A deployment/new target transaction must bind the captured reservation exactly
                # (finding 2): it must be quiesced, the arm units must EQUAL the captured
                # reserved list (never merely a subset whose prestate entries happen to
                # exist), the exact servicePrestate keys must equal the units, and the spec
                # must carry the reviewed serviceDesired state. A roots-only legacy
                # transaction retains deliberate backward compatibility.
                if not reserved.get("quiesced"):
                    fail("a deployment transaction requires the reservation to be quiesced before arm")
                reserved_units = reserved.get("units")
                if not isinstance(reserved_units, list) or units != reserved_units:
                    fail("arm units must exactly equal the captured reserved affected-unit list")
                if service_prestate is None:
                    fail("a deployment transaction requires captured service prestate before arm")
                if service_desired is None:
                    fail("a deployment transaction must carry the reviewed serviceDesired state")
                _check_service_desired(service_desired, require_full_fixed=True)
            if reserved.get("reservationToken") != token:
                fail("reservation token does not match this invocation; refusing to arm")
            if reserved.get("contractSha256") != contract:
                fail("journal reservation does not match this contract; refusing to arm")
            if reserved.get("backupSha256") != backup:
                fail("journal reservation does not match this backup identity; refusing to arm")
            if reserved.get("sourcePixel") != SOURCE_PIXEL or reserved.get("targetPixel") != TARGET_PIXEL:
                fail(f"journal reservation is not the authenticated {SOURCE_PIXEL} -> {TARGET_PIXEL} migration")
            # Arm runs BEFORE any live destination rename, so it must fail closed unless the
            # live state already satisfies every precondition rollback will later rely on.
            # old-snapshot evidence is captured from each live destination while it is still
            # at destination (the inode/device/type follows the content to oldPath if moved),
            # never from oldPath after a swap. All checks run under the exclusive custody
            # lock before the armed state is atomically written.
            old_evidence, temporary_evidence = _validate_arm_state(
                destinations, old_paths, temporary_paths, had_old)
            # Two-phase arm: classification + every pure invariant run first (no newPath is
            # created), then the armed journal is durably written owning the standalone intake
            # BEFORE the first newPath, and each intake updates the journal durably. A hard
            # kill mid-intake therefore leaves the journal with per-item evidence sufficient
            # for deterministic cleanup/recovery.
            deployment_items, intake_jobs = _arm_deployment_items(raw_deployment_items, stage_root, destinations)
            if deployment_spec_file is not None:
                # A spec file must not overlap or nest into any managed deployment path or
                # private-root destination/old/temp path.
                for item in deployment_items:
                    for dep_path in (item["path"], item["oldPath"], item["newPath"]):
                        if _paths_collide(deployment_spec_file, dep_path):
                            fail("deployment spec file must not overlap any managed path")
                for managed in destinations + old_paths + temporary_paths:
                    if _paths_collide(deployment_spec_file, managed):
                        fail("deployment spec file must not overlap any managed path")
            value = {
                "schemaVersion": 1,
                "kind": JOURNAL_KIND,
                "backupSha256": backup,
                "sourcePixel": SOURCE_PIXEL,
                "targetPixel": TARGET_PIXEL,
                "committed": False,
                "cleanup": "armed",
                "rolledBack": False,
                "finalization": "armed",
                "contractRoots": destinations,
                "destinations": destinations,
                "oldPaths": old_paths,
                "temporaryPaths": temporary_paths,
                "hadOld": had_old,
                "units": units,
                "rollbackProgress": ["pending"] * count,
                "oldEvidence": old_evidence,
                "temporaryEvidence": temporary_evidence,
                "deploymentItems": deployment_items,
                "armIntakeComplete": False,
            }
            if service_prestate is not None:
                value["servicePrestate"] = service_prestate
            if service_desired is not None:
                value["serviceDesired"] = service_desired
            if deployment_spec_sha is not None:
                value["deploymentSpecSha256"] = deployment_spec_sha
            # Validate the COMPLETE pure journal/path/duplicate/cross-domain invariants while
            # armIntakeComplete is still false and BEFORE the first durable journal write and
            # before any newPath is created. This is the same validator commit/rollback use,
            # so a malformed/duplicate/derived-path/cross-domain arm is refused without ever
            # creating a deployment newPath (the journal stays reserved and abort remains the
            # exact recovery authority).
            _validate_journal(value)
            # Durably own the standalone intake BEFORE any newPath is created. The journal is
            # written (armed, armIntakeComplete=false) with every deployment item's pure
            # metadata; each standalone present item carries an intake-pending marker.
            _write_journal(custody_fd, basename, value)
            try:
                # Intake each standalone desired-present item, durably recording its frozen
                # newEvidence after each so a hard kill leaves deterministic per-item evidence.
                for job in intake_jobs:
                    new_evidence = _intake_deployment_item(job, stage_root)
                    deployment_items[job["index"]]["newEvidence"] = new_evidence
                    _write_journal(custody_fd, basename, value)
                value["armIntakeComplete"] = True
                # Re-validate the full armed transaction with the exact validator used by
                # commit and rollback AFTER intake, so a malicious or malformed arm is refused
                # (any partially-created newPaths remain owned by the armed journal for
                # deterministic rollback).
                _validate_journal(value)
                _write_journal(custody_fd, basename, value)
            except BaseException:
                # Ordinary post-journal intake/validation failure: the journal already replaced
                # the reservation, so the wrapper's abort (which only handles a reserved
                # marker) could not clean it or restore service prestate. Deterministically
                # clean the exact owned newPaths and restore the reserved marker so the
                # wrapper's abort restores the exact service prestate and removes the
                # reservation. If the deterministic cleanup itself fails (indeterminate
                # state), the armed journal is retained for an explicit rollback rather than
                # implying a clean reserved state, and a specific indeterminate-cleanup error
                # is surfaced (never swallowed). A hard kill never runs this handler, so it
                # leaves the armed journal (armIntakeComplete=false) for explicit rollback.
                _recover_armed_intake(custody_fd, basename, value, reserved)
                raise
        finally:
            os.close(fd)
    finally:
        os.close(custody_fd)


def cmd_abort(custody: str, journal: str, token: str, contract: str, backup: str) -> None:
    _check_token(token)
    _check_contract(contract)
    _check_backup(backup)
    basename = _check_basename(custody, journal)
    custody_fd = lock_custody(custody)
    try:
        try:
            fd = os.open(basename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=custody_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            fail("journal path could not be opened: " + os.strerror(exc.errno))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                fail("refusing to remove a non-regular journal object")
            reserved = read_object(fd)
        finally:
            os.close(fd)
        if reserved.get("kind") != RESERVED_KIND:
            fail("refusing to erase an armed or foreign transaction")
        if reserved.get("reservationToken") != token:
            fail("refusing to erase a reservation for a different invocation token")
        if reserved.get("contractSha256") != contract or reserved.get("backupSha256") != backup:
            fail("refusing to erase a reservation for a different contract/backup")
        # Early abort must restore the exact captured prestate (finding 3/4): the units may
        # already have been stopped by the outer flow, so an originally active/enabled unit
        # must be restored exactly, never left down. A capture that recorded prestate always
        # restores it; only a legacy reserved marker without prestate removes nothing extra.
        if isinstance(reserved.get("servicePrestate"), dict) and isinstance(reserved.get("units"), list):
            _restore_exact_or_legacy({"units": reserved["units"], "servicePrestate": reserved["servicePrestate"]})
        os.unlink(basename, dir_fd=custody_fd)
    finally:
        os.close(custody_fd)


def _validate_journal(value: dict[str, Any]) -> dict[str, Any]:
    keys = set(value.keys())
    # A valid journal is either the legacy roots-only shape or the deployment-extended shape
    # (JOURNAL_KEYS plus exactly deploymentItems + deploymentSpecSha256). Anything else is
    # rejected with the exact message the control verbs expect.
    # Accepted shapes: the legacy roots-only shape (with or without the new
    # temporaryEvidence), and the deployment-extended shape (with or without the new
    # temporaryEvidence). New arms always record temporaryEvidence; legacy journals without
    # it remain read/validation compatible.
    optional_keys = {"temporaryEvidence", "deploymentItems", "deploymentSpecSha256", "servicePrestate", "serviceDesired", "armIntakeComplete"}
    if not (JOURNAL_KEYS <= keys and (keys - JOURNAL_KEYS) <= optional_keys):
        fail("journal must contain exactly the transaction keys")
    if value["schemaVersion"] != 1 or value["kind"] != JOURNAL_KIND:
        fail("journal schema is unsupported")
    if value["sourcePixel"] != SOURCE_PIXEL or value["targetPixel"] != TARGET_PIXEL:
        fail("journal source/target identity is not the authenticated migration")
    if not isinstance(value["backupSha256"], str) or not HASH_RE.fullmatch(value["backupSha256"]):
        fail("journal backup identity is malformed")
    if not isinstance(value["committed"], bool):
        fail("journal committed state is malformed")
    if value["cleanup"] not in {"armed", "pending", "complete", "failed"}:
        fail("journal cleanup state is malformed")
    if value["finalization"] not in {"armed", "pending", "complete", "failed"}:
        fail("journal finalization state is malformed")
    if not isinstance(value["rolledBack"], bool):
        fail("journal rollback evidence is malformed")

    destinations, contract = value["destinations"], value["contractRoots"]
    old_paths, temp_paths = value["oldPaths"], value["temporaryPaths"]
    had_old, units = value["hadOld"], value["units"]
    if not (isinstance(destinations, list) and isinstance(contract, list)
            and isinstance(old_paths, list) and isinstance(temp_paths, list)
            and isinstance(had_old, list) and isinstance(units, list)
            and len(destinations) == len(contract) == len(old_paths) == len(temp_paths) == len(had_old)):
        fail("journal transaction arrays are malformed")
    count = len(destinations)
    if destinations != contract:
        fail("destination contract membership is inconsistent")

    def path_ok(path):
        return (isinstance(path, str) and path.startswith("/") and path != "/"
                and "\n" not in path and "\r" not in path and os.path.normpath(path) == path)

    if any(not path_ok(path) for path in destinations):
        fail("destination must be absolute normalized non-root")
    if len(set(destinations)) != count:
        fail("destination contract must be unique")
    for left in range(count):
        for right in range(left + 1, count):
            a, b = destinations[left], destinations[right]
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                fail("destination contract must be pairwise non-overlapping")

    for index in range(count):
        destination = destinations[index]
        old_path, temp_path = old_paths[index], temp_paths[index]
        if not (path_ok(old_path) and path_ok(temp_path)):
            fail("transaction sibling must be absolute normalized non-root")
        if os.path.dirname(old_path) != os.path.dirname(destination) or os.path.dirname(temp_path) != os.path.dirname(destination):
            fail("transaction sibling must be a sibling of its destination")
        stem = _derived_stem(destination)
        exact = re.compile(r"^" + re.escape(stem) + r"-(\d+)-(\d+)\.(old|new)$")
        m_old = exact.fullmatch(old_path)
        m_new = exact.fullmatch(temp_path)
        if not m_old or not m_new:
            fail("transaction sibling is not exactly derived from its destination")
        if m_old.group(1) != m_new.group(1) or m_old.group(2) != m_new.group(2):
            fail("old and temporary siblings must share the identical pid/index token")
        if old_path == destination or temp_path == destination or old_path == temp_path:
            fail("transaction sibling collides with its destination")
        if destination.startswith(old_path + "/") or destination.startswith(temp_path + "/") \
           or old_path.startswith(destination + "/") or temp_path.startswith(destination + "/"):
            fail("transaction sibling must not nest into its destination")
        if had_old[index] not in (0, 1):
            fail("hadOld must be 0 or 1")

    progress, old_evidence = value["rollbackProgress"], value["oldEvidence"]
    if not (isinstance(progress, list) and len(progress) == count
            and isinstance(old_evidence, list) and len(old_evidence) == count):
        fail("rollback progress/evidence arrays are malformed")
    if any(state not in {"pending", "in-progress", "restored"} for state in progress):
        fail("rollback progress state is malformed")
    for index in range(count):
        if had_old[index] == 1:
            _check_evidence(old_evidence[index])
        elif old_evidence[index] is not None:
            fail("hadOld=0 root must not carry old rollback evidence")

    temporary_evidence = value.get("temporaryEvidence")
    if temporary_evidence is not None:
        if not (isinstance(temporary_evidence, list) and len(temporary_evidence) == count):
            fail("temporary evidence array is malformed")
        for index in range(count):
            _check_evidence(temporary_evidence[index])

    deployment_items = value.get("deploymentItems", [])
    deployment_spec_sha = value.get("deploymentSpecSha256")
    # A deployment transaction is defined by its deployment spec/items/digest, not by
    # whether serviceDesired happens to exist (finding 6). The affected-unit set of a new
    # deployment transaction must be unique and include every fixed QUIESCE_UNITS member
    # exactly once (finding 1/5); a legacy roots-only journal retains
    # backward-compatible unit handling but still rejects arbitrary/duplicate names.
    is_deployment = bool(deployment_items) or deployment_spec_sha is not None
    _validate_affected_units(units, require_full_fixed=is_deployment)

    service_prestate = value.get("servicePrestate")
    if service_prestate is not None:
        _check_service_prestate(service_prestate, units)
    service_desired = value.get("serviceDesired")
    if service_desired is not None:
        _check_service_desired(service_desired, require_full_fixed=is_deployment)

    intake_complete = value.get("armIntakeComplete", True)
    if not isinstance(intake_complete, bool):
        fail("journal armIntakeComplete state is malformed")
    _validate_deployment_items(deployment_items, intake_complete=intake_complete)
    if deployment_items:
        if not isinstance(deployment_spec_sha, str) or not HASH_RE.fullmatch(deployment_spec_sha):
            fail("armed deployment transaction must carry a valid spec digest")
    elif deployment_spec_sha is not None:
        fail("journal carries a spec digest without any deployment items")
    # Cross-domain overlap guard: every deployment path must be pairwise non-colliding with
    # every private-root destination/old/temp path, EXCEPT a deployment item that folds into
    # exactly one authenticated private root. The cross-domain guard is not weakened: a
    # folded item is owned by exactly one private-root destination (composed into that root's
    # non-live staging tree before the whole-root copy/swap) and is still refused if it
    # overlaps any swap sibling (old/temp), any other managed root, or is ambiguous/inverse.
    managed_roots = destinations + old_paths + temp_paths
    for item in deployment_items:
        if item.get("folded"):
            folded, root_index, rel = _deployment_root_overlap(item["path"], destinations)
            if not folded or root_index != item.get("rootIndex") or rel != item.get("relative"):
                fail("folded deployment item classification does not match its owning root")
            for dep_path in (item["path"], item["oldPath"], item["newPath"]):
                for managed in old_paths + temp_paths:
                    if _paths_collide(dep_path, managed):
                        fail("folded deployment item must not overlap a private-root swap path")
                for other in destinations:
                    if other != destinations[item["rootIndex"]] and _paths_collide(dep_path, other):
                        fail("folded deployment item must not overlap another private root")
            continue
        for dep_path in (item["path"], item["oldPath"], item["newPath"]):
            for managed in managed_roots:
                if _paths_collide(dep_path, managed):
                    fail("deployment item path must not overlap or nest into a private root")
    return value


def _check_evidence(evidence: dict[str, Any]) -> None:
    """Validate old-snapshot rollback evidence exactly (inode+device+type, strict ints)."""
    if not isinstance(evidence, dict):
        fail("old rollback evidence is malformed")
    for key in ("ino", "dev", "size", "type"):
        if key not in evidence:
            fail("old rollback evidence is incomplete")
    for key in ("ino", "dev", "size"):
        if not isinstance(evidence[key], int) or isinstance(evidence[key], bool):
            fail("old rollback evidence is not an exact integer")
    if not isinstance(evidence["type"], str) or evidence["type"] not in {"dir", "file", "symlink", "other"}:
        fail("old rollback type evidence is malformed")


def _quiesce_active_units(units: list[str], prestate: Any) -> None:
    """Stop only ACTIVE units and verify each is EXACTLY inactive or not-found (finding 3).

    Reads each unit's exact raw active state first: ``active`` units are stopped, while
    ``inactive``/``not-found`` units (e.g. a disabled optional target unit genuinely absent on a
    host) are left alone - ``systemctl stop`` on a not-found unit fails, so the present green
    fake tests do not model production. Any ``failed``/empty/``unknown``/``activating``/
    ``deactivating`` state is rejected. After stopping, requires the exact ``inactive`` or
    ``not-found`` raw state - never ``is-active --quiet`` which also passes failed/unknown.
    On ANY stop or verification failure (including a verify-still-active SystemExit) the
    exact captured prestate is restored before re-raising, so a unit A is never left half-down
    by a failure on unit B. ``prestate`` may be None only for legacy journals, which fall back
    to best-effort restart-all on failure.
    """
    try:
        for unit in units:
            active_raw = _systemctl_read("is-active", unit)
            if active_raw in (NOT_FOUND_STATE, "inactive"):
                continue
            if active_raw != "active":
                fail(f"service active state is indeterminate for {unit}: {active_raw!r}")
            try:
                _systemctl("stop", unit)
            except subprocess.CalledProcessError:
                fail("a Pixel service could not be stopped; exact service state restored and live state is untouched")
        for unit in units:
            raw = _systemctl_read("is-active", unit)
            if raw not in (NOT_FOUND_STATE, "inactive"):
                fail("a Pixel service could not be quiesced; exact state restored and live state is untouched"
                     f" ({unit}: {raw!r})")
    except BaseException:
        if isinstance(prestate, dict):
            _restore_exact_or_legacy({"units": units, "servicePrestate": prestate})
        raise


def _quiesce_units(value: dict[str, Any]) -> None:
    """Stop every validated unit and verify it is inactive before any filesystem mutation.

    If any stop fails (or a unit cannot be verified inactive) after some units were already
    stopped, restore the exact captured prestate (never indiscriminately restarting every
    unit - an originally inactive/disabled unit is restored inactive/disabled), then fail
    without recording any rollback progress or mutating live state, so unit A is never left
    down by a failure on unit B. Legacy journals without a captured prestate fall back to
    restarting every validated unit best-effort.
    """
    units = value["units"]
    prestate = value.get("servicePrestate")
    try:
        _quiesce_active_units(units, prestate)
    except BaseException:
        if prestate is None:
            _finalize_units(units)
        raise


def _restart_best_effort(units: list[str]) -> None:
    """Legacy best-effort restart (used only for legacy journals without a captured prestate)."""
    for unit in units:
        try:
            _systemctl("restart", unit)
        except subprocess.CalledProcessError:
            pass


def _finalize_units(units: list[str]) -> None:
    for unit in units:
        _systemctl("restart", unit)


def _install_deployment_step(value: dict[str, Any], index: int) -> str:
    """Perform exactly one next mutation of the install/swap for deployment item ``index``.

    Runs by bound parent dirfd only (never by path), re-validating the parent against the
    armed parent evidence. It is idempotent and resumes from the actual descriptor-bound
    filesystem state, not from the recorded progress, so a crash at any boundary is
    classifiable and resumable. Returns the resulting durable state: "old-moved",
    "new-installed", or "complete".

    Step 1 moves the live old object at ``path`` to ``oldPath`` when it is still present
    (hadOld=1). Step 2 installs the prepared new object from ``newPath`` to ``path`` only
    after proving newPath still matches the frozen newEvidence (prepared-new tamper fails
    closed) and that ``path`` is not occupied by an unexpected object. Step 3 verifies the
    installed object matches newEvidence. The helper never removes an unexpected live object.
    """
    item = value["deploymentItems"][index]
    path = item["path"]
    parent_fd = _open_parent_validated(path, item["parent"])
    try:
        basename = os.path.basename(path)
        old_basename = os.path.basename(item["oldPath"])
        new_basename = os.path.basename(item["newPath"])
        new_is_absent = item["newEvidence"].get("type") == "absent"
        # Classify the already-installed final state FIRST: if the live path already exactly
        # matches the prepared newEvidence (for a present item, the exact new object; for a
        # desired-absent item, absence), the swap is complete with no further mutation
        # (idempotent retry / crash-after-install resume).
        if _item_evidence_matches(item["newEvidence"], parent_fd, basename):
            return "complete"
        # Otherwise, for a desired-present item the prepared new object MUST still exist and
        # exactly match the frozen newEvidence BEFORE any old move. A missing or tampered
        # prepared new fails closed with ZERO live mutation (the old object stays at path,
        # rollback-safe). Desired-absent items have no staged new object.
        if not new_is_absent:
            if _stat_nofollow(parent_fd, new_basename) is None:
                fail("prepared new object is missing; refusing to install")
            if not _item_evidence_matches(item["newEvidence"], parent_fd, new_basename):
                fail("prepared new object was tampered; refusing to install")
        # Step 1: preserve the live old object when it is still at path.
        if item["hadOld"] == 1 and _item_evidence_matches(item["evidence"], parent_fd, basename):
            if _stat_nofollow(parent_fd, old_basename) is not None:
                fail("install oldPath already occupied by an unexpected object; refusing to mutate")
            os.rename(basename, old_basename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
            return "old-moved"
        if item["hadOld"] == 1 and _item_evidence_matches(item["evidence"], parent_fd, old_basename):
            pass
        elif item["hadOld"] == 1:
            fail("install could not preserve the old path exactly; live state is indeterminate")
        # Step 2: for a desired-present item, install the prepared new object. For a
        # desired-absent item the old object is already preserved at oldPath and the path is
        # (or becomes) absent, which the final verification confirms.
        if not new_is_absent:
            if _stat_nofollow(parent_fd, new_basename) is not None:
                if _stat_nofollow(parent_fd, basename) is not None:
                    fail("deployment path is already occupied by an unexpected object; refusing to install")
                os.rename(new_basename, basename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
                return "new-installed"
        # Step 3: final verification of the installed state.
        if not _item_evidence_matches(item["newEvidence"], parent_fd, basename):
            fail("installed object does not match prepared evidence; live state is indeterminate")
        return "complete"
    finally:
        os.close(parent_fd)


def cmd_install(custody: str, journal: str) -> None:
    """Perform the helper-owned idempotent install/swap for every armed deployment item.

    Runs BEFORE the outer live verification, with the armed transaction already frozen (old
    evidence + prepared new evidence). Each item's install/swap state machine is persisted
    durably in the journal before and after every step; a crash at any boundary leaves a
    resumable journal whose next run resumes from the actual filesystem state. This is the
    ONLY path that mutates a journal-managed live deployment path: ordinary apply/deployment
    shell code must never rename/unlink these paths outside this helper.
    """
    basename = _check_basename(custody, journal)
    custody_fd = lock_custody(custody)
    try:
        fd = _open_journal(custody_fd, basename, os.O_RDWR)
        try:
            _check_owned_regular(fd)
            value = _validate_journal(read_object(fd))
            if value.get("armIntakeComplete") is False:
                fail("cannot install before standalone intake is complete; the armed journal owns the pending intake")
            if value["committed"]:
                fail("cannot install into an already-committed migration transaction")
            if value["rolledBack"]:
                fail("cannot install into an already-rolled-back migration transaction")
            items = value.get("deploymentItems", [])
            if not items:
                fail("install requires armed deployment items")
            # Install is the mutation boundary. The exact service prestate is ALREADY
            # captured by the mutation-free `capture` helper BEFORE outer quiescence and
            # carried through arm into this journal; it is NEVER re-snapshotted here after
            # the outer stop (that would durably record an originally active unit as
            # inactive and break rollback). Quiesce the exact validated units and verify
            # they are inactive BEFORE the first deployment mutation, leaving them stopped
            # for the private-root swap. A quiescence failure restores the exact captured
            # prestate and mutates nothing.
            _quiesce_units(value)
            for index, item in enumerate(items):
                if item.get("folded"):
                    # A folded item is composed into its owning private root's staging tree
                    # and carried by the whole-root swap; the helper never performs a second
                    # live write inside a swapped private root. Its install state is already
                    # complete by construction.
                    continue
                if item["installProgress"] == "complete":
                    # Idempotent retry: prove the final state already holds.
                    parent_fd = _open_parent_validated(item["path"], item["parent"])
                    try:
                        if not _item_evidence_matches(item["newEvidence"], parent_fd,
                                                      os.path.basename(item["path"])):
                            fail("installed object does not match prepared evidence; live state is indeterminate")
                    finally:
                        os.close(parent_fd)
                    continue
                while item["installProgress"] != "complete":
                    # Durable pre-step marker (before this step's mutation).
                    item["installProgress"] = "in-progress"
                    _write_journal(custody_fd, basename, value)
                    state = _install_deployment_step(value, index)
                    # Durable post-step state (after this step's mutation).
                    item["installProgress"] = state
                    _write_journal(custody_fd, basename, value)
        finally:
            os.close(fd)
    finally:
        os.close(custody_fd)
    print('{"status":"pass","mode":"install","installed":true}')


def _verify_folded_item_live(item: dict[str, Any]) -> None:
    """Verify a folded deployment item's composed live state at its path.

    A folded item is composed into the owning private root's non-live staging tree before the
    whole-root copy/swap, so commit verifies the composed live state directly: exact content
    (digest for a present file, target for a symlink, absence for a desired-absent nested
    item) and, for a present file, the exact prepared mode (e.g. the 0700 workspace scripts)
    and owner. The parent is reopened and validated against the armed parent evidence first.
    """
    kind = item["kind"]
    path = item["path"]
    # A folded item's parent lives inside the swapped root, so its armed pre-swap parent
    # evidence is intentionally stale. Re-open the parent safely (no symlink ancestors, no
    # follow, owner-validated) rather than against the stale evidence.
    _reject_symlink_ancestors(path)
    parent_fd = _open_parent_dirfd(path, _deploy_owner(kind))
    try:
        basename = os.path.basename(path)
        required_uid = _deploy_owner(kind)
        st = _stat_nofollow(parent_fd, basename)
        if item["sha256"] is None and kind != "symlink":
            # Desired-absent nested item: the composed root must not contain it.
            if st is not None:
                fail("folded deployment item must be absent in the composed root; live state is indeterminate")
            return
        if st is None:
            fail("folded deployment item is missing in the composed root; live state is indeterminate")
        if kind == "symlink":
            if not stat.S_ISLNK(st.st_mode):
                fail("folded deployment item is not a symlink; live state is indeterminate")
            if st.st_uid != required_uid:
                fail("folded deployment item owner is not the required owner uid")
            if os.readlink(basename, dir_fd=parent_fd) != item.get("target"):
                fail("folded deployment symlink target does not match the prepared target")
            return
        if not stat.S_ISREG(st.st_mode):
            fail("folded deployment item is not a regular file; live state is indeterminate")
        if st.st_uid != required_uid:
            fail("folded deployment item owner is not the required owner uid")
        if st.st_nlink != 1:
            fail("folded deployment item must be a single-link regular file")
        if stat.S_IMODE(st.st_mode) != item.get("mode"):
            fail("folded deployment item mode does not match the prepared mode")
        if stat.S_IMODE(st.st_mode) & 0o022 or stat.S_IMODE(st.st_mode) & (stat.S_ISUID | stat.S_ISGID):
            fail("folded deployment item mode is unsafe")
        if not _hash_matches_dirfd(parent_fd, basename, st, item["sha256"]):
            fail("folded deployment item content does not match the prepared digest")
    finally:
        os.close(parent_fd)


def _verify_deployment_installed(value: dict[str, Any]) -> None:
    """Refuse an early commit unless every deployment item is fully installed and still exact.

    Before the terminal commit marker, every deployment item must have installProgress
    "complete" AND the live path must still exactly match the armed newEvidence (live tamper
    fails closed). Roots-only legacy journals have no deploymentItems and are unaffected.
    """
    for item in value.get("deploymentItems", []):
        if item["installProgress"] != "complete":
            fail("cannot commit: a deployment item was never fully installed; refusing early commit")
        if item.get("folded"):
            # A folded item is composed into the swapped private root; commit verifies the
            # composed live state (content/digest/target/mode/absence) at its path.
            _verify_folded_item_live(item)
            continue
        parent_fd = _open_parent_validated(item["path"], item["parent"])
        try:
            if not _item_evidence_matches(item["newEvidence"], parent_fd,
                                          os.path.basename(item["path"])):
                fail("cannot commit: installed deployment object no longer matches prepared evidence; live state is indeterminate")
        finally:
            os.close(parent_fd)


def _verify_roots_committed(value: dict[str, Any]) -> None:
    """Refuse an early/partial commit for the private-root swap before marking committed.

    For journals carrying temporaryEvidence (all new arms): every destination must exactly
    match its prepared temporary evidence, every hadOld=1 old snapshot must be at oldPath,
    and every temporary path must be absent. A commit immediately after arm or after only
    part of the root swap therefore fails before marking committed. Legacy journals without
    temporaryEvidence retain read/validation compatibility (only the oldPath/temp-absent
    invariants that a completed swap inherently satisfies are enforced).
    """
    temp_ev = value.get("temporaryEvidence")
    for index in range(len(value["destinations"])):
        if value["hadOld"][index] == 1:
            if not _matches(value["oldEvidence"][index], value["oldPaths"][index]):
                fail("cannot commit: old snapshot is not at oldPath; refusing early commit")
        if temp_ev is not None:
            if not _matches(temp_ev[index], value["destinations"][index]):
                fail("cannot commit: live destination does not match the prepared temporary evidence; refusing early/partial commit")
        if _evidence(value["temporaryPaths"][index]) is not None:
            fail("cannot commit: a temporary path is still present; refusing early/partial commit")


def cmd_finalize(custody: str, journal: str) -> None:
    """Apply the strict reviewed serviceDesired poststate AFTER the filesystem/private-root
    swap (finding 5). daemon-reloads, applies the exact desired enabled/active state for
    every reviewed unit, preserves the captured prestate for dynamic deep-work units,
    verifies every transition, and journals progress durably. Any failure before runtime
    verify invokes exact rollback (the outer EXIT trap); unknown systemctl state stays
    indeterminate/stopped and never passes."""
    basename = _check_basename(custody, journal)
    custody_fd = lock_custody(custody)
    try:
        fd = _open_journal(custody_fd, basename, os.O_RDWR)
        try:
            _check_owned_regular(fd)
            value = _validate_journal(read_object(fd))
            if value["committed"]:
                fail("cannot finalize an already-committed migration transaction")
            if value["rolledBack"]:
                fail("cannot finalize an already-rolled-back migration transaction")
            prestate = value.get("servicePrestate")
            desired = value.get("serviceDesired")
            if not isinstance(prestate, dict) or not isinstance(desired, dict):
                fail("finalize requires captured service prestate and reviewed desired state")
            merged: dict[str, dict[str, bool]] = {}
            for unit in value["units"]:
                if unit in desired:
                    merged[unit] = desired[unit]
                elif unit in prestate:
                    # Dynamic deep-work units preserve their captured prestate.
                    merged[unit] = prestate[unit]
                else:
                    fail(f"finalize has no desired/prestate for unit: {unit}")
            try:
                _apply_service_map(value, merged)
            except subprocess.CalledProcessError:
                value["finalization"] = "failed"
                _write_journal(custody_fd, basename, value)
                print('{"status":"service-failed","mode":"finalize","finalization":"failed"}')
                raise SystemExit("migration journal: service finalization failed; exact rollback required")
            value["finalization"] = "complete"
            _write_journal(custody_fd, basename, value)
        finally:
            os.close(fd)
    finally:
        os.close(custody_fd)
    print('{"status":"pass","mode":"finalize","finalization":"complete"}')


def cmd_commit(custody: str, journal: str) -> None:
    basename = _check_basename(custody, journal)
    custody_fd = lock_custody(custody)
    try:
        fd = _open_journal(custody_fd, basename, os.O_RDWR)
        try:
            _check_owned_regular(fd)
            value = _validate_journal(read_object(fd))
            if value.get("armIntakeComplete") is False:
                fail("cannot commit before standalone intake is complete; the armed journal owns the pending intake")
            if value["rolledBack"]:
                fail("cannot commit an already-rolled-back migration transaction")
            # A new/deployment transaction is defined by its deployment spec/items/digest, not
            # by whether serviceDesired happens to exist (finding 6). It must carry the
            # reviewed serviceDesired map and require successful verified service finalization
            # before commit; commit rejects absent desired as well as armed/failed/pending
            # finalization. Only a genuinely roots-only legacy journal (no deployment items)
            # retains the backward-compatible finalization completion.
            is_deployment = bool(value.get("deploymentItems")) or value.get("deploymentSpecSha256") is not None
            if is_deployment:
                if value.get("serviceDesired") is None:
                    fail("commit refused: deployment transaction requires the reviewed serviceDesired state")
                if value["finalization"] != "complete":
                    fail("commit refused: new migration transaction requires verified service finalization (run finalize) before commit")
            if not value["committed"]:
                # Before the terminal commit marker: prove the deployment items were fully
                # installed and the private roots are in the complete swapped state. A commit
                # immediately after arm, after only part of the swap, or with a tampered live
                # object fails closed before marking committed. Once committed, an idempotent
                # re-commit only resumes cleanup (old snapshots may already be gone), so these
                # exact-swap-state checks apply only to the terminal commit transition.
                _verify_deployment_installed(value)
                _verify_roots_committed(value)
                value["committed"] = True
                value["cleanup"] = "pending"
                value["finalization"] = "complete"
                _write_journal(custody_fd, basename, value)
            if value["cleanup"] != "complete":
                failed = False
                for index in range(len(value["destinations"])):
                    if value["hadOld"][index] == 1:
                        try:
                            _run_rm("-rf", "--", value["oldPaths"][index])
                        except (subprocess.CalledProcessError, OSError):
                            failed = True
                for item in value.get("deploymentItems", []):
                    if item["hadOld"] == 1 and not item.get("folded"):
                        try:
                            _cleanup_deployment_item(item)
                        except (OSError, SystemExit):
                            failed = True
                if failed:
                    # A committed verified deployment is never rolled back by a cleanup
                    # failure. Record the failure durably so an operator can re-run commit
                    # (which is idempotent) to finish or confirm cleanup.
                    value["cleanup"] = "failed"
                    try:
                        _write_journal(custody_fd, basename, value)
                    except OSError:
                        # The cleanup-failed state could not be recorded durably. Keep the
                        # already-committed marker semantics (committed is durably true) but
                        # never claim a durable cleanup-failed state we could not record:
                        # report the actual durable cleanup state conservatively.
                        print('{"status":"cleanup-failed","mode":"commit","committed":true,"cleanup":"pending"}')
                        raise SystemExit(1)
                    print('{"status":"cleanup-failed","mode":"commit","committed":true,"cleanup":"failed"}')
                    raise SystemExit(1)
                value["cleanup"] = "complete"
                _write_journal(custody_fd, basename, value)
            # Terminal commit never restarts services: they are already running and just
            # passed the outer verify, so a restart would be an unnecessary disruption.
            # Finalization is marked complete truthfully without restarting; rollback is
            # the only path that stops, mutates, and restarts.
            if value["finalization"] != "complete":
                value["finalization"] = "complete"
                _write_journal(custody_fd, basename, value)
        finally:
            os.close(fd)
    finally:
        os.close(custody_fd)
    print('{"status":"pass","mode":"commit","committed":true,"cleanup":"complete","finalization":"complete"}')


def _temp_evidence_at(value: dict[str, Any], index: int) -> dict[str, Any] | None:
    """The recorded prepared temporary evidence for a private root (None for legacy journals)."""
    temp_ev = value.get("temporaryEvidence")
    if temp_ev is None:
        return None
    return temp_ev[index]


def _cleanup_temp_root(temp_path: str, temp_evidence: dict[str, Any] | None) -> None:
    """Remove a prepared temporary root only when it is absent or exactly matches its evidence.

    An unexpected staged object at the temporary path is never deleted: it fails closed.
    """
    ev = _evidence(temp_path)
    if ev is None:
        return
    if temp_evidence is not None and not _matches(temp_evidence, temp_path):
        fail("rollback refused: staged temporary object is not the prepared object; live state is indeterminate")
    _run_rm("-rf", "--", temp_path)


def _restore_root(value: dict[str, Any], index: int) -> None:
    had_old = value["hadOld"][index]
    destination = value["destinations"][index]
    old_path = value["oldPaths"][index]
    temp_path = value["temporaryPaths"][index]
    temp_evidence = _temp_evidence_at(value, index)
    if had_old == 1:
        old_ev = value["oldEvidence"][index]
        if _matches(old_ev, old_path):
            # The swap happened (old snapshot at oldPath). destination may hold the swapped-in
            # prepared temporary object - remove it only when it exactly matches the prepared
            # temporary evidence (never rm an unexpected object).
            if _evidence(destination) is not None:
                if temp_evidence is not None and not _matches(temp_evidence, destination):
                    fail("rollback refused: live destination is not the prepared temporary object; live state is indeterminate")
                _run_rm("-rf", "--", destination)
            _run_mv("-T", "--", old_path, destination)
            _cleanup_temp_root(temp_path, temp_evidence)
        elif not _evidence(old_path):
            if _matches(old_ev, destination):
                _cleanup_temp_root(temp_path, temp_evidence)
            else:
                fail("rollback could not be completed exactly; live state is indeterminate")
        else:
            fail("rollback could not be completed exactly; live state is indeterminate")
    else:
        if _evidence(destination) is not None:
            if temp_evidence is not None and not _matches(temp_evidence, destination):
                fail("rollback refused: live destination is not the prepared temporary object; live state is indeterminate")
            _run_rm("-rf", "--", destination)
        _cleanup_temp_root(temp_path, temp_evidence)


def _cleanup_deployment_item(item: dict[str, Any]) -> None:
    """Remove a committed deployment oldPath sibling by dirfd and fsync the parent.

    Deployment items are files or symlinks, never directories, so a plain dirfd unlink is
    used (never a recursive rm) and the bound parent directory is fsynced so the cleanup is
    durable. The parent is re-opened and validated against the armed parent evidence first.
    """
    if item["hadOld"] != 1:
        return
    parent_fd = _open_parent_validated(item["path"], item["parent"])
    try:
        _unlink_basename(parent_fd, os.path.basename(item["oldPath"]))
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _restore_deployment_item(value: dict[str, Any], index: int) -> None:
    """Roll back one deployment item by dirfd only.

    Re-opens the immediate parent with O_DIRECTORY|O_NOFOLLOW and validates dev/inode/type/
    uid/gid/mode against the armed parent evidence, then unlinks/renames by basename beneath
    that bound dirfd and fsyncs the parent after each successful mutation. The helper NEVER
    deletes an unexpected live object merely because rollback was requested: before unlinking
    the installed new object at ``path`` it proves that object matches the armed newEvidence,
    and before restoring it proves the old object at ``oldPath`` matches the armed old
    evidence. An object that does not match its recorded evidence (substituted, tampered, or
    an unexpected live object) is indeterminate and refused without mutation.
    """
    item = value["deploymentItems"][index]
    path = item["path"]
    parent_fd = _open_parent_validated(path, item["parent"])
    try:
        basename = os.path.basename(path)
        old_basename = os.path.basename(item["oldPath"])
        new_basename = os.path.basename(item["newPath"])
        evidence = item["evidence"]
        new_evidence = item["newEvidence"]
        if item["hadOld"] == 1:
            if _item_evidence_matches(evidence, parent_fd, old_basename):
                # Restoring the old object. path may hold the installed new object (verify it
                # matches the armed newEvidence before removing it - never delete an
                # unexpected live object) or be absent (crash before install), in which case
                # there is nothing to remove.
                if _stat_nofollow(parent_fd, basename) is not None:
                    if not _item_evidence_matches(new_evidence, parent_fd, basename):
                        fail("rollback refused: installed object at deployment path is not the armed new object; live state is indeterminate")
                    _unlink_basename(parent_fd, basename)
                os.rename(old_basename, basename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
            elif _stat_nofollow(parent_fd, old_basename) is None:
                if _item_evidence_matches(evidence, parent_fd, basename):
                    pass
                else:
                    fail("rollback could not be completed exactly; live state is indeterminate")
            else:
                fail("rollback could not be completed exactly; live state is indeterminate")
        else:
            # hadOld=0: path may hold the installed new object (verify it exactly matches
            # newEvidence before removing it - never delete an unexpected live object) or be
            # absent (crash before install). An unexpected object at path must remain and
            # rollback must fail closed.
            if _stat_nofollow(parent_fd, basename) is not None:
                if not _item_evidence_matches(new_evidence, parent_fd, basename):
                    fail("rollback refused: installed object at deployment path is not the armed new object; live state is indeterminate")
                _unlink_basename(parent_fd, basename)
                os.fsync(parent_fd)
        # Remove an uninstalled/staged newPath (crash before install) only when it exactly
        # matches the armed newEvidence; an unexpected staged object is never deleted.
        if _stat_nofollow(parent_fd, new_basename) is not None:
            if not _item_evidence_matches(new_evidence, parent_fd, new_basename):
                fail("rollback refused: staged newPath is not the armed new object; live state is indeterminate")
            _unlink_basename(parent_fd, new_basename)
            os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _newpath_matches_spec(item: dict[str, Any], parent_fd: int, new_basename: str, st) -> bool:
    """True when a pending-intake newPath exactly matches the prepared spec content bind.

    Used only for deterministic cleanup of an intake-interrupted journal: a newPath left by a
    hard kill mid-intake is removed only when it exactly matches the prepared spec digest
    (file) or target (symlink) recorded in the intake-pending evidence. Anything that does not
    match exactly is indeterminate and retained for manual recovery - never a best-effort
    generic deletion.
    """
    ev = item["newEvidence"]
    if item["kind"] == "symlink":
        if not stat.S_ISLNK(st.st_mode):
            return False
        if "uid" in ev and ev["uid"] is not None and st.st_uid != ev["uid"]:
            return False
        if "gid" in ev and ev["gid"] is not None and st.st_gid != ev["gid"]:
            return False
        if ev.get("target") is None:
            return False
        try:
            return os.readlink(new_basename, dir_fd=parent_fd) == ev["target"]
        except OSError:
            return False
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        return False
    if "uid" in ev and ev["uid"] is not None and st.st_uid != ev["uid"]:
        return False
    if "gid" in ev and ev["gid"] is not None and st.st_gid != ev["gid"]:
        return False
    if "mode" in ev and ev["mode"] is not None and stat.S_IMODE(st.st_mode) != ev["mode"]:
        return False
    if ev.get("sha256") is None:
        return False
    return _hash_matches_dirfd(parent_fd, new_basename, st, ev["sha256"])


def _cleanup_intake_newpaths(value: dict[str, Any]) -> None:
    """Deterministically remove standalone intake newPath siblings left by an interrupted arm.

    Runs only for an intake-interrupted journal (armIntakeComplete false) where NO root swap
    and NO standalone install has occurred. A newPath created by a completed intake must
    exactly match its recorded newEvidence; a pending intake's newPath is removed only when it
    exactly matches the prepared spec bind. Any mismatch is indeterminate and refused (never a
    best-effort generic deletion).
    """
    for item in value.get("deploymentItems", []):
        if item.get("folded"):
            continue
        parent_fd = _open_parent_validated(item["path"], item["parent"])
        try:
            new_basename = os.path.basename(item["newPath"])
            st = _stat_nofollow(parent_fd, new_basename)
            if st is None:
                continue
            new_evidence = item["newEvidence"]
            if new_evidence.get("type") == "intake-pending":
                if not _newpath_matches_spec(item, parent_fd, new_basename, st):
                    fail("rollback refused: a pending intake newPath is not the prepared object; live state is indeterminate")
            else:
                if not _item_evidence_matches(new_evidence, parent_fd, new_basename):
                    fail("rollback refused: staged newPath is not the armed new object; live state is indeterminate")
            _unlink_basename(parent_fd, new_basename)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)


def cmd_rollback(custody: str, journal: str) -> None:
    basename = _check_basename(custody, journal)
    custody_fd = lock_custody(custody)
    try:
        fd = _open_journal(custody_fd, basename, os.O_RDWR)
        try:
            _check_owned_regular(fd)
            value = _validate_journal(read_object(fd))
            if value["committed"]:
                fail("cannot roll back an already-committed migration transaction")
            if not value["rolledBack"] and value.get("armIntakeComplete") is False:
                # Intake-interrupted arm (hard kill mid-intake before any root swap or
                # standalone install): no live destination was mutated. Deterministically
                # remove any created standalone intake newPath siblings and restore the exact
                # service prestate; the whole-root swap and folded items never ran, so they
                # need no per-item rollback.
                _quiesce_units(value)
                _cleanup_intake_newpaths(value)
                value["rolledBack"] = True
                value["finalization"] = "pending"
                _write_journal(custody_fd, basename, value)
            if not value["rolledBack"] and value.get("armIntakeComplete") is not False:
                # Quiesce the exact validated units BEFORE any filesystem mutation; if a unit
                # cannot be stopped, refuse to mutate (indeterminate state is never rewritten).
                _quiesce_units(value)
                try:
                    for index in range(len(value["destinations"]) - 1, -1, -1):
                        if value["rollbackProgress"][index] == "restored":
                            continue
                        value["rollbackProgress"][index] = "in-progress"
                        _write_journal(custody_fd, basename, value)
                        _restore_root(value, index)
                        value["rollbackProgress"][index] = "restored"
                        _write_journal(custody_fd, basename, value)
                    if any(state != "restored" for state in value["rollbackProgress"]):
                        fail("rollback restoration could not be verified; live state is indeterminate")
                    deployment_items = value.get("deploymentItems", [])
                    for index in range(len(deployment_items) - 1, -1, -1):
                        item = deployment_items[index]
                        if item["progress"] == "restored":
                            continue
                        if item.get("folded"):
                            # A folded item has no standalone install/old snapshot: rollback is
                            # covered by the owning whole-root swap (the root oldPath snapshot
                            # carries its exact pre-migration state). Mark it restored without a
                            # second live write inside a swapped private root.
                            item["progress"] = "restored"
                            _write_journal(custody_fd, basename, value)
                            continue
                        item["progress"] = "in-progress"
                        _write_journal(custody_fd, basename, value)
                        _restore_deployment_item(value, index)
                        item["progress"] = "restored"
                        _write_journal(custody_fd, basename, value)
                    if any(item["progress"] != "restored" for item in deployment_items):
                        fail("rollback restoration could not be verified; live state is indeterminate")
                    value["rolledBack"] = True
                    value["finalization"] = "pending"
                    _write_journal(custody_fd, basename, value)
                except (subprocess.CalledProcessError, OSError):
                    # A mutation/durability failure leaves progress conservatively non-pass
                    # (the failing item stays "in-progress", durably recorded) and services
                    # stopped (they were quiesced and are not restarted here because the
                    # finalization step is never reached). The state is indeterminate, so a
                    # retry is deterministic: the evidence-based restore resumes from the
                    # actual filesystem state.
                    fail("rollback could not be completed exactly; live state is indeterminate")
            if value["finalization"] != "complete":
                try:
                    _restore_service_state(value)
                except subprocess.CalledProcessError:
                    value["finalization"] = "failed"
                    _write_journal(custody_fd, basename, value)
                    print('{"status":"service-pending","mode":"rollback","rolledBack":true,"finalization":"failed"}')
                    raise SystemExit(1)
                value["finalization"] = "complete"
                _write_journal(custody_fd, basename, value)
        finally:
            os.close(fd)
    finally:
        os.close(custody_fd)
    print('{"status":"pass","mode":"rollback","rolledBack":true,"finalization":"complete"}')


def cmd_inspect(custody: str, journal: str) -> None:
    basename = _check_basename(custody, journal)
    custody_fd = lock_custody(custody)
    try:
        fd = _open_journal(custody_fd, basename, os.O_RDONLY)
        try:
            _check_owned_regular(fd)
            value = read_object(fd)
        finally:
            os.close(fd)
    finally:
        os.close(custody_fd)
    if value.get("kind") == RESERVED_KIND:
        print(json.dumps({
            "kind": RESERVED_KIND,
            "contractSha256": value.get("contractSha256"),
            "backupSha256": value.get("backupSha256"),
            "sourcePixel": value.get("sourcePixel"),
            "targetPixel": value.get("targetPixel"),
        }, sort_keys=True))
        return
    validated = _validate_journal(value)
    print(json.dumps({
        "kind": JOURNAL_KIND,
        "backupSha256": validated["backupSha256"],
        "committed": validated["committed"],
        "rolledBack": validated["rolledBack"],
        "cleanup": validated["cleanup"],
        "finalization": validated["finalization"],
        "rollbackProgress": validated["rollbackProgress"],
        "deploymentItems": len(validated.get("deploymentItems", [])),
        "servicePrestate": validated.get("servicePrestate"),
        "serviceDesired": validated.get("serviceDesired"),
    }, sort_keys=True))


def main(argv: list[str]) -> int:
    if not argv:
        fail("missing migration-journal subcommand")
    _require_privileged()
    command = argv[0]
    rest = argv[1:]
    if command == "ensure":
        if len(rest) != 1:
            fail("ensure requires CUSTODY")
        cmd_ensure(rest[0])
    elif command == "reserve":
        if len(rest) != 4:
            fail("reserve requires CUSTODY JOURNAL CONTRACT BACKUP_SHA")
        token = cmd_reserve(rest[0], rest[1], rest[2], rest[3])
        print(token)
    elif command == "arm":
        if len(rest) < 6:
            fail("arm requires CUSTODY JOURNAL TOKEN CONTRACT BACKUP_SHA COUNT [UNITS...] [SPECFILE]")
        cmd_arm(rest[0], rest[1], rest[2], rest[3], rest[4], rest[5:])
    elif command == "capture":
        if len(rest) < 5:
            fail("capture requires CUSTODY JOURNAL TOKEN CONTRACT BACKUP_SHA [UNITS...]")
        cmd_capture(rest[0], rest[1], rest[2], rest[3], rest[4], rest[5:])
    elif command == "quiesce":
        if len(rest) != 5:
            fail("quiesce requires CUSTODY JOURNAL TOKEN CONTRACT BACKUP_SHA")
        cmd_quiesce(rest[0], rest[1], rest[2], rest[3], rest[4])
    elif command == "abort":
        if len(rest) != 5:
            fail("abort requires CUSTODY JOURNAL TOKEN CONTRACT BACKUP_SHA")
        cmd_abort(rest[0], rest[1], rest[2], rest[3], rest[4])
    elif command == "inspect":
        if len(rest) != 2:
            fail("inspect requires CUSTODY JOURNAL")
        cmd_inspect(rest[0], rest[1])
    elif command == "fold":
        if len(rest) < 5:
            fail("fold requires CUSTODY SPEC MIGRATION_STAGE RESTORE_STAGE ROOT_REL...")
        cmd_fold(rest[0], rest[1], rest[2], rest[3], rest[4:])
    elif command == "finalize":
        if len(rest) != 2:
            fail("finalize requires CUSTODY JOURNAL")
        cmd_finalize(rest[0], rest[1])
    elif command == "commit":
        if len(rest) != 2:
            fail("commit requires CUSTODY JOURNAL")
        cmd_commit(rest[0], rest[1])
    elif command == "install":
        if len(rest) != 2:
            fail("install requires CUSTODY JOURNAL")
        cmd_install(rest[0], rest[1])
    elif command == "rollback":
        if len(rest) != 2:
            fail("rollback requires CUSTODY JOURNAL")
        cmd_rollback(rest[0], rest[1])
    else:
        fail("unknown migration-journal subcommand: " + command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except (OSError, ValueError, json.JSONDecodeError, UnicodeError, subprocess.CalledProcessError) as error:
        print(f"pixel-migration-journal: {error}", file=sys.stderr)
        raise SystemExit(1)
