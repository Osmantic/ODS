"""Root custody of the Source Broker for a connected Google account.

Runs inside the access coordinator. It never runs owner-controlled code: the
installer copies the broker program, its unit files and its environment template
into a root-owned kit beside this module, and this code installs only those bytes.
The Google sign-in reaches the broker identity through a private handoff that
only that identity can read; the token never passes through here.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import errno
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable
import uuid

BROKER_USER = "pixel-source-broker"
KIT = Path(__file__).resolve().parent / "source-broker-kit"
UNITS = ("pixel-source-broker.service", "pixel-source-broker.timer", "pixel-source-action@.service",
         "pixel-source-reconcile@.service", "pixel-source-direct.service", "pixel-source-direct.path",
         "pixel-source-exchange@.service", "pixel-source-revoke.service")
KIT_FILES = ("broker.py", "source-broker.env", *UNITS)
MAX_RESULT_BYTES = 64 * 1024
MAX_RETIRED_ACTION_FILES = 4096
MAX_RETIRED_ACTION_BYTES = 64 * 1024 * 1024
ENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)='((?:[^']|'\\'')*)'$")
FLOW_ID = re.compile(r"^[a-f0-9]{32}$")
FLOW_FILE = re.compile(r"^[a-f0-9]{32}\.(?:flow|code|result)\.json$")
# Exactly the keys the broker writes into a sign-in or revocation result.
EXCHANGE_KEYS = {"schemaVersion", "kind", "finishedAt", "flowId", "outcome", "errorClass", "httpStatus",
                 "errorType", "grantedScopes", "account"}
REVOKE_KEYS = {"schemaVersion", "kind", "finishedAt", "outcome", "errorClass", "httpStatus", "errorType"}
ERROR_CLASS = re.compile(r"^[a-z][a-z-]{1,40}$")
# The broker key that lets queued private calendar changes run.
DIRECT_FLAG = "PIXEL_CALENDAR_DIRECT_ENABLED"
DRAFTS_FLAG = "PIXEL_LIMB_EMAIL_DRAFTS_ENABLED"
PREAUTH_FLAGS = ("PIXEL_LIMB_EMAIL_ENABLED", DRAFTS_FLAG, "PIXEL_LIMB_CALENDAR_ENABLED",
                 "PIXEL_LIMB_CALENDAR_WRITE_ENABLED", "PIXEL_LIMB_SOCIAL_ENABLED", DIRECT_FLAG)
SCOPE = re.compile(r"^(?:https://www\.googleapis\.com/auth/[a-z0-9._-]{1,100}|https://mail\.google\.com/|openid|email|profile)$")
EMAIL = re.compile(r"^[^@\s<>\x00-\x1f\x7f]{1,64}@[^@\s<>\x00-\x1f\x7f]{1,253}$")

# run(arguments, timeout) returns the exit status. A oneshot unit that fails still
# reports through its result file, so only callers decide which statuses are errors.
Run = Callable[[list, int], int]


class CustodyError(RuntimeError):
    """A custody step failed; ``code`` is a fixed, content-free reason."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def system_run(arguments: list, timeout: int) -> int:
    try:
        return subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout, check=False).returncode
    except (OSError, subprocess.TimeoutExpired):
        raise CustodyError("host-command-failed") from None


def _check(run: Run, arguments: list, timeout: int = 30) -> None:
    if run(arguments, timeout) != 0:
        raise CustodyError("host-command-failed")


@dataclass(frozen=True)
class Paths:
    """Where the broker lives; Pixel's configure fixes these same paths."""

    install: Path = Path("/opt/pixel-source-broker")
    state: Path = Path("/var/lib/pixel-source-broker")
    env: Path = Path("/etc/pixel-source-broker.env")
    units: Path = Path("/etc/systemd/system")
    kit: Path = KIT

    @property
    def private(self) -> Path:
        return self.state / "private"

    @property
    def oauth(self) -> Path:
        return self.private / "oauth"

    @property
    def token(self) -> Path:
        return self.private / "google-token.json"

    @property
    def projection(self) -> Path:
        return self.state / "projection"

    @property
    def proposals(self) -> Path:
        return self.state / "proposals"

    @property
    def results(self) -> Path:
        return self.state / "results"


def account(name: str) -> tuple[int, int]:
    """The UID and primary GID of a system account; KeyError when it does not exist."""
    record = pwd.getpwnam(name)
    return record.pw_uid, record.pw_gid


def broker_ids() -> tuple[int, int]:
    return account(BROKER_USER)


def _kit_bytes(paths: Paths, name: str) -> bytes:
    """One kit file, refused unless root owns it and nobody else can write it.

    The access server proves its own program directory, which holds the kit,
    before it serves anything; this checks the kit inside that directory.
    """
    path = paths.kit / name
    for item in (path, path.parent, path.parent.parent):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise CustodyError("source-broker-kit-unavailable")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 2 * 1024 * 1024:
        raise CustodyError("source-broker-kit-unavailable")
    return path.read_bytes()


def kit_present(paths: Paths = Paths()) -> bool:
    return all((paths.kit / name).is_file() for name in KIT_FILES)


def _directory(path: Path, uid: int, gid: int, mode: int) -> None:
    path.mkdir(mode=mode, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise CustodyError("source-broker-state-unsafe")
    os.chown(path, uid, gid, follow_symlinks=False)
    os.chmod(path, mode)


def _install(path: Path, data: bytes, uid: int, gid: int, mode: int) -> None:
    """Replace one file atomically with exactly these bytes, owner and mode."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchown(handle.fileno(), uid, gid)
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _existing_exchange_env(path: Path, broker_gid: int) -> bool:
    """Preserve a protected existing connection; never follow or replace a link."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if os.path.lexists(path):
            raise CustodyError("source-broker-env-unsafe") from None
        return False
    except OSError:
        raise CustodyError("source-broker-env-unsafe") from None
    try:
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or mode not in (0o400, 0o440, 0o600, 0o640) or info.st_size > 2 * 1024 * 1024
                or mode & 0o040 and info.st_gid not in (0, broker_gid)):
            raise CustodyError("source-broker-env-unsafe")
    finally:
        os.close(descriptor)
    return True


def _prepare_exchange_env(paths: Paths, template: bytes, broker_gid: int) -> None:
    """Satisfy the exchange unit's mandatory EnvironmentFile, before consent.

    No account or capability has been granted yet. Only an absent file is
    created, from the protected kit with every source/action disabled. An
    existing connection keeps its exact environment until exchange succeeds.
    """
    parent = paths.env.parent
    info = parent.lstat()
    if (parent.resolve() != parent or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o022):
        raise CustodyError("source-broker-env-unsafe")
    if _existing_exchange_env(paths.env, broker_gid):
        return
    try:
        lines = parse_env(template.decode('utf-8'))
    except UnicodeError:
        raise CustodyError("source-broker-env-invalid") from None
    present = {key for key, _value in lines}
    lines.extend((key, '0') for key in PREAUTH_FLAGS if key not in present)
    disabled = {key: '0' for key, _value in lines
                if key in PREAUTH_FLAGS or key.startswith('PIXEL_LIMB_') and key.endswith('_ENABLED')}
    data = env_text(lines, disabled).encode('utf-8')
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{paths.env.name}.', dir=parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fchown(handle.fileno(), 0, broker_gid)
            os.fchmod(handle.fileno(), 0o640)
            os.fsync(handle.fileno())
        try:
            # Publish only when still absent. A concurrent root installer must
            # never have its legitimate environment silently replaced.
            os.link(temporary, paths.env, follow_symlinks=False)
        except FileExistsError:
            if not _existing_exchange_env(paths.env, broker_gid):
                raise CustodyError("source-broker-env-unsafe") from None
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare(paths: Paths, run: Run, *, reader: str) -> None:
    """Create the broker identity, its private directories and units; start nothing."""
    try:
        broker_ids()
    except KeyError:
        _check(run, ["useradd", "--system", "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", BROKER_USER])
    uid, gid = broker_ids()
    reader_uid, reader_gid = account(reader)
    if reader_uid == 0:
        raise CustodyError("source-broker-reader-unsafe")
    files = {name: _kit_bytes(paths, name) for name in KIT_FILES}
    _prepare_exchange_env(paths, files['source-broker.env'], gid)
    _directory(paths.install, 0, 0, 0o755)
    _directory(paths.state, 0, 0, 0o755)
    _directory(paths.private, uid, gid, 0o700)
    _directory(paths.oauth, uid, gid, 0o700)
    _directory(paths.projection, uid, reader_gid, 0o2750)
    _directory(paths.results, uid, reader_gid, 0o2750)
    _directory(paths.proposals, reader_uid, gid, 0o2750)
    _install(paths.install / "broker.py", files["broker.py"], 0, 0, 0o755)
    for unit in UNITS:
        _install(paths.units / unit, files[unit], 0, 0, 0o644)
    _check(run, ["systemctl", "daemon-reload"])


def write_handoff(paths: Paths, name: str, value: dict[str, Any]) -> None:
    """One sign-in handoff file that only the broker identity can read."""
    uid, gid = broker_ids()
    descriptor = os.open(paths.oauth / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        os.fchown(handle.fileno(), uid, gid)
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())


def discard_flow(paths: Paths, flow_id: str) -> None:
    """Remove every file of one sign-in flow, whether or not it ran."""
    if not FLOW_ID.fullmatch(flow_id):
        raise CustodyError("invalid-flow")
    for suffix in ("flow", "code", "result"):
        (paths.oauth / f"{flow_id}.{suffix}.json").unlink(missing_ok=True)


def discard_flows(paths: Paths) -> None:
    """Remove the files of every sign-in flow; a broker never prepared has none."""
    if paths.oauth.is_symlink() or not paths.oauth.is_dir():
        return
    for entry in os.scandir(paths.oauth):
        if FLOW_FILE.fullmatch(entry.name):
            os.unlink(entry.path)


def _unique(pairs: list) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CustodyError("source-broker-result-invalid")
        value[key] = item
    return value


def _result(path: Path, keys: set, kind: str) -> dict[str, Any] | None:
    """Read and consume one result the broker identity wrote; None when there is none."""
    uid, _gid = broker_ids()
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid
                or info.st_size > MAX_RESULT_BYTES):
            raise CustodyError("source-broker-result-unsafe")
        raw = handle.read(MAX_RESULT_BYTES + 1)
    path.unlink()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, ValueError):
        raise CustodyError("source-broker-result-invalid") from None
    if (not isinstance(value, dict) or set(value) != keys or value.get("schemaVersion") != 1
            or value.get("kind") != kind):
        raise CustodyError("source-broker-result-invalid")
    error_class = value["errorClass"]
    if error_class is not None and (not isinstance(error_class, str) or not ERROR_CLASS.fullmatch(error_class)):
        raise CustodyError("source-broker-result-invalid")
    return value


def exchange(paths: Paths, run: Run, flow_id: str) -> dict[str, Any]:
    """Run one sign-in exchange as the broker identity and return its content-free result."""
    if not FLOW_ID.fullmatch(flow_id):
        raise CustodyError("invalid-flow")
    # A failed exchange exits non-zero after writing its result; the result decides.
    run(["systemctl", "start", f"pixel-source-exchange@{flow_id}.service"], 120)
    result = _result(paths.oauth / f"{flow_id}.result.json", EXCHANGE_KEYS, "pixel-source-oauth-result")
    discard_flow(paths, flow_id)
    if result is None:
        raise CustodyError("sign-in-exchange-unavailable")
    scopes, account = result["grantedScopes"], result["account"]
    if (result["flowId"] != flow_id or result["outcome"] not in ("connected", "failed")
            or (result["outcome"] == "connected") != (result["errorClass"] is None)
            or not isinstance(scopes, list)
            or any(not isinstance(item, str) or not SCOPE.fullmatch(item) for item in scopes)
            or account is not None and (not isinstance(account, str) or not EMAIL.fullmatch(account))):
        raise CustodyError("source-broker-result-invalid")
    return {"outcome": result["outcome"], "errorClass": result["errorClass"],
            "grantedScopes": sorted(set(scopes)), "account": account}


def parse_env(text: str) -> list:
    """The KEY='value' lines Pixel's configure writes, in order."""
    lines = []
    for line in text.splitlines():
        match = ENV_LINE.fullmatch(line)
        if match is None:
            raise CustodyError("source-broker-env-invalid")
        lines.append((match.group(1), match.group(2).replace("'\\''", "'")))
    if len({key for key, _ in lines}) != len(lines):
        raise CustodyError("source-broker-env-invalid")
    return lines


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def env_text(lines: list, overrides: dict[str, str]) -> str:
    """Environment lines with exactly the given keys replaced, in Pixel's format."""
    if not set(overrides) <= {key for key, _ in lines}:
        raise CustodyError("source-broker-env-invalid")
    return "".join(f"{key}={_quote(overrides.get(key, value))}\n" for key, value in lines)


def connection_env(paths: Paths, *, gateway: dict[str, bool], calendar_id: str, time_zone: str | None,
                   gmail_window_days: int) -> str:
    """The broker environment for one Google connection, as Pixel's configure would write it.

    Every other key keeps the value Pixel's configure wrote into the kit template.
    """
    lines = parse_env(_kit_bytes(paths, "source-broker.env").decode("utf-8"))
    values = dict(lines)
    window = f" newer_than:{gmail_window_days}d"
    overrides = {
        "PIXEL_LIMB_EMAIL_ENABLED": "1" if gateway["email"] else "0",
        "PIXEL_LIMB_CALENDAR_ENABLED": "1" if gateway["calendar"] else "0",
        "PIXEL_LIMB_CALENDAR_WRITE_ENABLED": "1" if gateway["calendarWrite"] else "0",
        "PIXEL_CALENDAR_DIRECT_ENABLED": "1" if gateway["calendarDirect"] else "0",
        "PIXEL_SOURCE_CALENDAR_ID": calendar_id,
        "PIXEL_SOURCE_GMAIL_QUERY": values["PIXEL_SOURCE_GMAIL_QUERY"].split(" newer_than:", 1)[0] + window,
        "PIXEL_SOURCE_GMAIL_SENT_QUERY": values["PIXEL_SOURCE_GMAIL_SENT_QUERY"].split(" newer_than:", 1)[0] + window,
    }
    if time_zone:
        overrides["PIXEL_SOURCE_TIME_ZONE"] = time_zone
    # A kit from before Gmail drafts has no drafts flag; env_text refuses one that needs it.
    if gateway["emailDrafts"] or DRAFTS_FLAG in values:
        overrides[DRAFTS_FLAG] = "1" if gateway["emailDrafts"] else "0"
    return env_text(lines, overrides)


def install_env(paths: Paths, text: str) -> None:
    _uid, gid = broker_ids()
    _install(paths.env, text.encode("utf-8"), 0, gid, 0o640)


def start(run: Run, *, direct: bool) -> None:
    """Turn on the refresh timer and begin the first refresh without waiting for it."""
    _check(run, ["systemctl", "daemon-reload"])
    _check(run, ["systemctl", "enable", "--now", "pixel-source-broker.timer"])
    _check(run, ["systemctl", "enable" if direct else "disable", "--now", "pixel-source-direct.path"])
    _check(run, ["systemctl", "start", "--no-block", "pixel-source-broker.service"])


def direct_enabled(paths: Paths) -> bool:
    """Whether the installed broker environment lets queued private changes run."""
    try:
        text = paths.env.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return dict(parse_env(text)).get(DIRECT_FLAG) == "1"


def watch_direct(paths: Paths, run: Run, *, enabled: bool) -> None:
    """Turn the watcher that runs queued private changes on or off; a broker never prepared has none."""
    if (paths.units / "pixel-source-direct.path").exists():
        _check(run, ["systemctl", "enable" if enabled else "disable", "--now", "pixel-source-direct.path"])


def pause_direct(paths: Paths, run: Run) -> None:
    """Stop queued private changes at once, leaving every other part of the broker as it is."""
    if paths.env.exists():
        lines = parse_env(paths.env.read_text(encoding="utf-8"))
        if dict(lines).get(DIRECT_FLAG) == "1":
            install_env(paths, env_text(lines, {DIRECT_FLAG: "0"}))
    watch_direct(paths, run, enabled=False)


def sync(run: Run) -> None:
    _check(run, ["systemctl", "start", "--no-block", "pixel-source-broker.service"])


def stop(paths: Paths, run: Run) -> None:
    """Stop every broker unit that is installed; a never-prepared broker has none."""
    for unit in ("pixel-source-broker.timer", "pixel-source-direct.path"):
        if (paths.units / unit).exists():
            _check(run, ["systemctl", "disable", "--now", unit])
    for unit in ("pixel-source-broker.service", "pixel-source-direct.service"):
        if (paths.units / unit).exists():
            _check(run, ["systemctl", "stop", unit])


def quiesce_for_sign_in(paths: Paths, run: Run) -> None:
    """Wait for every old-account reader and actuator before replacing its grant.

    A timed-out approval may still have a running template instance after its
    Root caller returned. systemctl expands these fixed patterns over loaded
    units and waits for their stop; no owner-supplied unit name is involved.
    """
    stop(paths, run)
    for template in ("pixel-source-action@.service", "pixel-source-reconcile@.service"):
        if (paths.units / template).exists():
            _check(run, ["systemctl", "stop", template.replace("@.", "@*.")])


def sign_in_identity(paths: Paths) -> tuple | None:
    """An unchanged private inode proves a failed exchange kept the old grant.

    Root never reads or backs up the token. This proof is used only while the
    refresh timer and all old broker processes are stopped under Root's lock.
    """
    try:
        info = paths.token.lstat()
    except FileNotFoundError:
        return None
    uid, _gid = broker_ids()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > MAX_RESULT_BYTES):
        raise CustodyError("source-broker-token-unsafe")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink)


def archive_google_actions(paths: Paths, flow_id: str | None = None) -> None:
    """Quarantine old actions privately; never replay them for another account.

    Archive only derived actions/results, never OAuth/token files. Bound the
    retained archive and incoming queues before any rename. The Root-only
    directory also prevents the old gateway from approving an archived item.
    """
    if flow_id is not None and not FLOW_ID.fullmatch(flow_id):
        raise CustodyError("invalid-flow")
    archive = paths.private / "retired-google-actions"
    candidates = []
    for directory in (paths.proposals, paths.results):
        if directory.is_symlink():
            raise CustodyError("source-broker-data-unsafe")
        if directory.exists():
            for entry in os.scandir(directory):
                if len(candidates) >= MAX_RETIRED_ACTION_FILES:
                    raise CustodyError("source-broker-archive-full")
                candidates.append((directory.name, Path(entry.path)))
    if not candidates:
        return
    if archive.exists() or archive.is_symlink():
        info = archive.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise CustodyError("source-broker-data-unsafe")
    count, size = 0, 0

    def bounded(path: Path, depth: int = 0) -> None:
        nonlocal count, size
        info = path.lstat()
        count += 1
        if count > MAX_RETIRED_ACTION_FILES or depth > 32:
            raise CustodyError("source-broker-archive-full")
        if stat.S_ISDIR(info.st_mode):
            for entry in os.scandir(path):
                bounded(Path(entry.path), depth + 1)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            size += info.st_size
            if size > MAX_RETIRED_ACTION_BYTES:
                raise CustodyError("source-broker-archive-full")
        else:
            raise CustodyError("source-broker-data-unsafe")

    if archive.exists():
        bounded(archive)
    for _name, path in candidates:
        bounded(path)
    archive.mkdir(mode=0o700, exist_ok=True)
    retired = Path(tempfile.mkdtemp(prefix=f"{flow_id or 'interrupted'}-", dir=archive))
    for name, path in candidates:
        destination = retired / name
        destination.mkdir(mode=0o700, exist_ok=True)
        os.rename(path, destination / path.name)
    for directory in (paths.proposals, paths.results, retired / "proposals", retired / "results", retired,
                      archive, paths.private):
        if directory.exists():
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def clear_google_data(paths: Paths) -> None:
    """Clear regenerable Google projections/cursors, preserving social and OAuth.

    Called only after quiescence, before a token may change and again before
    the new grant starts. OAuth handoffs must survive until exchange consumes
    them. The social snapshot is independent of the Google account.
    """
    for directory in (paths.projection,):
        if directory.is_symlink():
            raise CustodyError("source-broker-data-unsafe")
        if not directory.exists():
            continue
        for entry in os.scandir(directory):
            if entry.name == "social.json":
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def forget_sign_in(paths: Paths) -> None:
    """Drop uncertain local custody without replaying or restoring any grant."""
    paths.token.unlink(missing_ok=True)
    descriptor = os.open(paths.private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def holds_sign_in(paths: Paths) -> bool:
    return os.path.lexists(paths.token)


def revoke(paths: Paths, run: Run) -> str:
    """Revoke the grant at Google, then make sure no sign-in is left, whatever Google said.

    Returns ``revoked`` when Google confirmed, ``local-only`` when the sign-in was only
    deleted here, and ``not-connected`` when there was none.
    """
    if not holds_sign_in(paths):
        return "not-connected"
    outcome = "local-only"
    if (paths.units / "pixel-source-revoke.service").exists():
        (paths.oauth / "revoke.result.json").unlink(missing_ok=True)
        # The broker deletes the token and writes its result even when Google is unreachable.
        run(["systemctl", "start", "pixel-source-revoke.service"], 60)
        result = _result(paths.oauth / "revoke.result.json", REVOKE_KEYS, "pixel-source-oauth-revoke-result")
        if result is not None and result["outcome"] == "revoked":
            outcome = "revoked"
    if os.path.lexists(paths.token):
        paths.token.unlink()
        outcome = "local-only"
    return outcome


def erase(paths: Paths) -> None:
    """Delete everything the broker derived from the account; the directories stay."""
    for directory in (paths.projection, paths.proposals, paths.results, paths.oauth,
                      paths.private / "retired-google-actions"):
        if directory.is_symlink() or not directory.is_dir():
            continue
        for entry in os.scandir(directory):
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)


def remove_env(paths: Paths) -> None:
    paths.env.unlink(missing_ok=True)


# Native Source custody is deliberately not selected by any public operation yet.
# The existing Linux Paths/Run/functions above retain their commands and outputs.
NATIVE_BROKER = '_ods_pixel_sources'
NATIVE_HOME = '/private/var/empty'
NATIVE_META = Path('/private/var/lib/ods-pixel-access')
NATIVE_DEFINITIONS = Path('/Library/LaunchDaemons')
NATIVE_PROGRAM = Path('/usr/local/libexec/ods-pixel-access')
NATIVE_RUNTIMES = Path('/usr/local/libexec/ods-pixel-runtimes')
NATIVE_RUNTIME_DESCRIPTOR = Path('/private/etc/ods/pixel-provider-runtime.json')
NATIVE_RUNTIME_MANIFEST = Path('/private/etc/ods/pixel-provider-runtime-manifest.json')
NATIVE_STATE = Path('/private/var/lib/pixel-source-broker')
NATIVE_ENV = NATIVE_STATE / 'source-broker.env'
NATIVE_LEGACY_ENV = Path('/private/etc/pixel-source-broker.env')
NATIVE_KIT = Path('/usr/local/libexec/ods-pixel-access/pixel_connections/source-broker-kit')
NATIVE_LOGS = Path('/private/var/log/pixel-source-broker')
NATIVE_LABEL = 'com.ods.pixel-native-source-'
NATIVE_ROLES = ('sync', 'direct', 'action', 'reconcile', 'exchange', 'revoke')
NATIVE_CHILD_BUDGETS = {'sync': 300, 'direct': 90, 'action': 120,
    'reconcile': 120, 'exchange': 120, 'revoke': 60}
NATIVE_PROFILES = tuple('source-' + role + '.sb' for role in NATIVE_ROLES)
_NATIVE_SEAL = object()
_NATIVE_SHA = re.compile(r'[a-f0-9]{64}\Z')
_NATIVE_ACTION = re.compile(r'(?:calendar|gmail-draft)-[0-9]{13}-[a-f0-9]{8}\Z')
_NATIVE_ACL_TYPE = 0x100
_NATIVE_LIST_SEARCH = (1 << 1) | (1 << 3)


@dataclass(frozen=True)
class NativeSourcePaths(Paths):
    """Literal native paths, never a browser/owner-selected relocation."""

    install: Path = NATIVE_KIT
    state: Path = NATIVE_STATE
    env: Path = NATIVE_ENV
    units: Path = Path('/Library/LaunchDaemons')
    kit: Path = NATIVE_KIT

    def __post_init__(self):
        if (self.install, self.state, self.env, self.units, self.kit) != (
                NATIVE_KIT, NATIVE_STATE, NATIVE_ENV, Path('/Library/LaunchDaemons'), NATIVE_KIT):
            raise CustodyError('native-source-paths-invalid')


@dataclass(frozen=True)
class NativeSourceCommand:
    """A closed plan only. It cannot start, stop, reload or repeat a process."""

    role: str
    operation: str
    label: str | None
    identifier: str | None = None
    asynchronous: bool = False


def native_source_command(arguments, timeout):
    """Interpret only the Source Run grammar emitted by existing Root code.

    Not a systemctl emulator, shell command builder, scheduler or executor.
    In particular it does not turn kickstart -k into a one-shot retry.
    """
    if (type(arguments) is not list or any(type(item) is not str for item in arguments)
            or type(timeout) is not int or not 1 <= timeout <= 300):
        raise CustodyError('native-source-command-invalid')
    exact = {
        ('systemctl', 'daemon-reload'): ('definitions', 'verify', None, False),
        ('systemctl', 'enable', '--now', 'pixel-source-broker.timer'): ('sync', 'enable-trigger', NATIVE_LABEL + 'sync', False),
        ('systemctl', 'disable', '--now', 'pixel-source-broker.timer'): ('sync', 'disable-trigger', NATIVE_LABEL + 'sync', False),
        ('systemctl', 'enable', '--now', 'pixel-source-direct.path'): ('direct', 'enable-trigger', NATIVE_LABEL + 'direct', False),
        ('systemctl', 'disable', '--now', 'pixel-source-direct.path'): ('direct', 'disable-trigger', NATIVE_LABEL + 'direct', False),
        ('systemctl', 'start', '--no-block', 'pixel-source-broker.service'): ('sync', 'observe-or-start', NATIVE_LABEL + 'sync', True),
        ('systemctl', 'stop', 'pixel-source-broker.service'): ('sync', 'quiesce', NATIVE_LABEL + 'sync', False),
        ('systemctl', 'stop', 'pixel-source-direct.service'): ('direct', 'quiesce', NATIVE_LABEL + 'direct', False),
        ('systemctl', 'stop', 'pixel-source-action@*.service'): ('action', 'quiesce-family', None, False),
        ('systemctl', 'stop', 'pixel-source-reconcile@*.service'): ('reconcile', 'quiesce-family', None, False),
        ('systemctl', 'start', 'pixel-source-revoke.service'): ('revoke', 'observe-or-start-once', NATIVE_LABEL + 'revoke', False),
    }
    selected = exact.get(tuple(arguments))
    if selected:
        if timeout > (60 if selected[0] == 'revoke' else 30):
            raise CustodyError('native-source-command-invalid')
        return NativeSourceCommand(selected[0], selected[1], selected[2], asynchronous=selected[3])
    if len(arguments) == 3 and arguments[:2] == ['systemctl', 'start']:
        for family, role, pattern in (('action', 'action', _NATIVE_ACTION),
                                      ('reconcile', 'reconcile', _NATIVE_ACTION),
                                      ('exchange', 'exchange', FLOW_ID)):
            match = re.fullmatch(r'pixel-source-' + family + r'@(.+)\.service', arguments[2])
            if match and pattern.fullmatch(match[1]):
                if timeout > 120:
                    raise CustodyError('native-source-command-invalid')
                return NativeSourceCommand(role, 'observe-or-start-once',
                    NATIVE_LABEL + role + '.' + match[1], match[1])
    raise CustodyError('native-source-command-invalid')


def native_child_arguments(role, identifier=None):
    """Pure closed Source argv, not admission or an executor."""
    if type(role) is not str or role not in NATIVE_ROLES:
        raise CustodyError('native-source-role-invalid')
    if role in ('action', 'reconcile', 'exchange'):
        pattern = FLOW_ID if role == 'exchange' else _NATIVE_ACTION
        if type(identifier) is not str or not pattern.fullmatch(identifier):
            raise CustodyError('native-source-command-invalid')
        return (('--approve' if role == 'action' else '--' + role), identifier)
    if identifier is not None:
        raise CustodyError('native-source-command-invalid')
    return () if role == 'sync' else (('--drain-direct',) if role == 'direct' else ('--revoke',))


def native_disabled_definition(role, *, identity, python, identifier=None):
    """One shared inert renderer, reused by publisher AND descriptor verifier."""
    _native_identity_intent(identity)
    native_child_arguments(role, identifier)
    if (type(python) is not str or not python.startswith('/') or any(c in python for c in '\0\r\n')
            or any(part in ('', '.', '..') for part in python.split('/')[1:])):
        raise CustodyError('native-source-python-path-invalid')
    label = NATIVE_LABEL + role + ('.' + identifier if identifier is not None else '')
    environment = {'HOME': '/private/var/empty', 'USER': NATIVE_BROKER, 'LOGNAME': NATIVE_BROKER,
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
    document = {
        'Label': label, 'Disabled': True, 'RunAtLoad': False, 'KeepAlive': False,
        'UserName': NATIVE_BROKER, 'GroupName': NATIVE_BROKER, 'InitGroups': False,
        'ProgramArguments': ['/usr/bin/env', '-i', *(key + '=' + value for key, value in environment.items()),
            python, '-I', '-S', '-B', '/usr/local/libexec/ods-pixel-access/pixel_connections/custody.py',
            '--native-broker-child', role, *([identifier] if identifier is not None else [])],
        'WorkingDirectory': str(NATIVE_STATE), 'Umask': 0o077, 'ProcessType': 'Background',
        'AbandonProcessGroup': False, 'ExitTimeOut': 5, 'StandardInPath': '/dev/null',
        'StandardOutPath': str(NATIVE_LOGS / (role + '.stdout.log')),
        'StandardErrorPath': str(NATIVE_LOGS / (role + '.stderr.log')),
    }
    return Path('/Library/LaunchDaemons') / (label + '.plist'), plistlib.dumps(document, sort_keys=True), 0o644


def _native_root():
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise CustodyError('native-source-root-required')


@dataclass(frozen=True)
class _NativeSourceSelection:
    bridge: Any
    deployment: Any
    seal: Any

    def guard(self):
        _native_root()
        from pixel_access_bridge import LaunchdAccessBridge
        from .planner import ConnectionDeployment
        if (self.seal is not _NATIVE_SEAL or type(self.bridge) is not LaunchdAccessBridge
                or type(self.deployment) is not ConnectionDeployment):
            raise CustodyError('native-source-deployment-required')
        # Invoke the actual protected method, not a request-supplied callback or
        # an instance attribute. It reopens RuntimeCustody and loaded deployment.
        current = LaunchdAccessBridge.connection_deployment(self.bridge)
        if type(current) is not ConnectionDeployment or current != self.deployment:
            raise CustodyError('native-source-deployment-changed')
        doc = current.document
        if (doc['ownerUid'], doc['ownerGid'], doc['home']) != (
                self.bridge.owner.pw_uid, self.bridge.owner.pw_gid, str(self.bridge.home)):
            raise CustodyError('native-source-owner-changed')
        return doc


def native_source_selection(bridge, deployment):
    """Private Root seam. Parsing/checksumming a deployment is not authority.

    Caller must hold the existing Root connection/admission lock. No default
    platform selection, availability toggle, job or installer wiring is added.
    """
    selected = _NativeSourceSelection(bridge, deployment, _NATIVE_SEAL)
    selected.guard()
    return selected


def _native_selected(selection, *, _publication=False):
    # A duck-typed guard/checksum is not a Root deployment capability.
    if type(selection) is not _NativeSourceSelection or selection.seal is not _NATIVE_SEAL:
        raise CustodyError('native-source-deployment-required')
    doc = selection.guard()
    # No native publisher for this obsolete outside-gate path ever existed in
    # this implementation. Preserve any object there; do not adopt or move it.
    if os.path.lexists(NATIVE_LEGACY_ENV):
        raise CustodyError('native-source-legacy-env-preserved')
    if not _publication and os.path.lexists(NATIVE_META):
        from pixel_macos_custody import protected_directory
        with protected_directory(NATIVE_META) as directory:
            publication, _raw = _native_json_at(directory, 'source-publication.json')
            jobs, _jobs_raw = _native_json_at(directory, 'source-jobs-prepare.json')
        # This data-only increment admits no ordinary env/account/kit writer
        # once a generation has been staged. Only explicit recovery may handle
        # its frozen inputs. A completed rollback restores the old v1 boundary.
        if jobs is not None and (type(jobs) is not dict or jobs.get('phase') != 'restored'):
            raise CustodyError('native-source-generation-requires-lifecycle')
        if publication is not None and (type(publication) is not dict
                or publication.get('schemaVersion') != 1
                or publication.get('kind') != 'ods-native-source-publication'
                or publication.get('deploymentSha256') != selection.deployment.checksum
                or publication.get('phase') not in ('prepared-no-jobs', 'restored')):
            raise CustodyError('native-source-publication-pending')
    return doc


def _native_acl_library():
    """Apple public sys/acl.h ABI; no opaque-structure layout assumptions.

    LIST_DIRECTORY=1<<1, SEARCH=1<<3, EXTENDED=0x100. Apple Libc acl_flag.c
    exposes 32-bit flags through acl_get_flag_np; query all bits, including
    currently unnamed ones. Physical Darwin ABI/kernel qualification is pending.
    """
    if sys.platform != 'darwin':
        raise CustodyError('native-source-acl-platform-required')
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
        'acl_init': ([ctypes.c_int], pointer),
        'acl_create_entry': ([ctypes.POINTER(pointer), ctypes.POINTER(pointer)], ctypes.c_int),
        'acl_set_tag_type': ([pointer, ctypes.c_int], ctypes.c_int),
        'acl_set_qualifier': ([pointer, pointer], ctypes.c_int),
        'acl_set_permset_mask_np': ([pointer, ctypes.c_uint64], ctypes.c_int),
        'acl_set_fd_np': ([ctypes.c_int, pointer, ctypes.c_int], ctypes.c_int),
        'acl_free': ([pointer], ctypes.c_int),
        'mbr_uid_to_uuid': ([ctypes.c_uint32, pointer], ctypes.c_int),
        'mbr_gid_to_uuid': ([ctypes.c_uint32, pointer], ctypes.c_int),
        'mbr_check_membership': ([pointer, pointer, ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
    }
    for name, (arguments, result) in signatures.items():
        try:
            function = getattr(lib, name)
        except AttributeError:
            raise CustodyError('native-source-acl-api-unavailable') from None
        function.argtypes, function.restype = arguments, result
    return lib


def _native_uuid(lib, uid):
    if type(uid) is not int or not 0 < uid <= 0xffffffff:
        raise CustodyError('native-source-identity-invalid')
    value = (ctypes.c_ubyte * 16)()
    if lib.mbr_uid_to_uuid(uid, value) != 0:
        raise CustodyError('native-source-uuid-unavailable')
    return bytes(value)


def _native_flags_zero(lib, item):
    flags = ctypes.c_void_p()
    if lib.acl_get_flagset_np(item, ctypes.byref(flags)) != 0 or not flags.value:
        raise CustodyError('native-source-acl-invalid')
    for bit in range(32):
        if lib.acl_get_flag_np(flags, ctypes.c_int(1 << bit).value) != 0:
            raise CustodyError('native-source-acl-flags')


def _native_acl_entries(fd, *, missing=False):
    lib = _native_acl_library()
    ctypes.set_errno(0)
    acl = lib.acl_get_fd_np(fd, _NATIVE_ACL_TYPE)
    if not acl:
        if missing and ctypes.get_errno() == errno.ENOENT:
            os.fstat(fd)
            return ()
        raise CustodyError('native-source-acl-required')
    try:
        if lib.acl_valid(acl) != 0:
            raise CustodyError('native-source-acl-invalid')
        _native_flags_zero(lib, acl)
        entries = []
        for index in range(4):
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            result = lib.acl_get_entry(acl, 0 if index == 0 else -1, ctypes.byref(entry))
            if result == -1 and ctypes.get_errno() == errno.EINVAL:
                return tuple(entries)
            if result != 0 or not entry.value or index >= 2:
                raise CustodyError('native-source-acl-entries')
            _native_flags_zero(lib, entry)
            tag, mask = ctypes.c_int(), ctypes.c_uint64()
            if (lib.acl_get_tag_type(entry, ctypes.byref(tag)) != 0 or tag.value != 1
                    or lib.acl_get_permset_mask_np(entry, ctypes.byref(mask)) != 0
                    or mask.value != _NATIVE_LIST_SEARCH):
                raise CustodyError('native-source-acl-rights')
            qualifier = lib.acl_get_qualifier(entry)
            if not qualifier:
                raise CustodyError('native-source-acl-qualifier')
            try:
                entries.append(ctypes.string_at(qualifier, 16))
            finally:
                lib.acl_free(qualifier)
        raise CustodyError('native-source-acl-entries')
    finally:
        lib.acl_free(acl)


def native_verify_gate(fd, owner_uid, broker_uid, *, prefix=False):
    """Read-only proof on the held Root inode; never repair/adopt a pathname."""
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o700 or owner_uid == broker_uid):
        raise CustodyError('native-source-gate-unsafe')
    lib = _native_acl_library()
    wanted = (_native_uuid(lib, owner_uid), _native_uuid(lib, broker_uid))
    if wanted[0] == wanted[1]:
        raise CustodyError('native-source-identity-uuid-conflict')
    actual = _native_acl_entries(fd, missing=prefix)
    if actual != wanted and not (prefix and actual in ((), wanted[:1])):
        raise CustodyError('native-source-gate-acl-mismatch')
    return (info.st_dev, info.st_ino)


def native_verify_env_fd(fd, broker_uid):
    """Read-only env-file custody, not env-content/deployment admission.

    The future publisher/launcher must prove the fixed gate/Root intent and
    read/validate its bounded environment using this same held descriptor.
    This helper neither reads bytes, installs an ACL nor runs anything.
    """
    return _native_env_fd_guid(fd, _native_uuid(_native_acl_library(), broker_uid))


def _native_env_fd_guid(fd, expected):
    """Same FD/ACL proof using a GUID already attested by the fixed Root Gate.

    The Broker cannot query Directory Services after Seatbelt. This does not
    turn a caller-supplied GUID into authority; the reader first proves Gate
    custody, its exact principal ACL, current credentials and runtime binding.
    """
    if type(expected) is not bytes or len(expected) != 16:
        raise CustodyError('native-source-env-acl-mismatch')
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_RESULT_BYTES):
        raise CustodyError('native-source-env-unsafe')
    lib = _native_acl_library()
    acl = lib.acl_get_fd_np(fd, _NATIVE_ACL_TYPE)
    if not acl:
        raise CustodyError('native-source-env-acl-required')
    try:
        if lib.acl_valid(acl) != 0:
            raise CustodyError('native-source-env-acl-invalid')
        _native_flags_zero(lib, acl)
        entry = ctypes.c_void_p()
        if lib.acl_get_entry(acl, 0, ctypes.byref(entry)) != 0 or not entry.value:
            raise CustodyError('native-source-env-acl-mismatch')
        _native_flags_zero(lib, entry)
        tag, mask = ctypes.c_int(), ctypes.c_uint64()
        if (lib.acl_get_tag_type(entry, ctypes.byref(tag)) != 0 or tag.value != 1
                or lib.acl_get_permset_mask_np(entry, ctypes.byref(mask)) != 0 or mask.value != (1 << 1)):
            raise CustodyError('native-source-env-acl-rights')
        qualifier = lib.acl_get_qualifier(entry)
        if not qualifier:
            raise CustodyError('native-source-env-acl-mismatch')
        try:
            if ctypes.string_at(qualifier, 16) != expected:
                raise CustodyError('native-source-env-acl-mismatch')
        finally:
            lib.acl_free(qualifier)
        extra = ctypes.c_void_p()
        ctypes.set_errno(0)
        if lib.acl_get_entry(acl, -1, ctypes.byref(extra)) != -1 or ctypes.get_errno() != errno.EINVAL:
            raise CustodyError('native-source-env-acl-mismatch')
    finally:
        lib.acl_free(acl)
    def fingerprint(value):
        return (value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode,
                value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if fingerprint(os.fstat(fd)) != fingerprint(info):
        raise CustodyError('native-source-env-changed')
    return fingerprint(info)


def _native_env_at(gate, owner_uid, broker_uid, *, required=False):
    """Validate the fixed leaf under an already Root-admitted gate descriptor.

    This does not qualify a caller-supplied gate as a deployment. B must bind
    the actual fixed gate/Root journal before use; A has no env publisher and
    deliberately does not admit an existing env in prepare_state's namespace.
    """
    if type(required) is not bool or NATIVE_ENV != NATIVE_STATE / 'source-broker.env':
        raise CustodyError('native-source-env-path-invalid')
    native_verify_gate(gate, owner_uid, broker_uid)
    try:
        fd = os.open(NATIVE_ENV.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=gate)
    except FileNotFoundError:
        if required:
            raise CustodyError('native-source-env-unavailable') from None
        return None
    try:
        return native_verify_env_fd(fd, broker_uid)
    finally:
        os.close(fd)


def _native_write_gate_acl(selection, fd, owner_uid, broker_uid):
    """Only a Root-intent-bound created inode may receive the two ACEs."""
    doc = _native_selected(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_META) as directory:
        record, _raw = _native_json_at(directory, 'source-custody-prepare.json')
        identity, _raw = _native_json_at(directory, 'source-identity.json')
    identity = _native_identity_intent(identity)
    _manifest, kit_sha = native_verify_kit(selection)
    lib = _native_acl_library()
    owner_guid = str(uuid.UUID(bytes=_native_uuid(lib, doc['ownerUid']))).upper()
    binding = _native_prepare_binding(doc, identity, owner_guid, kit_sha, selection.deployment.checksum)
    _native_validate_prepare_intent(record, binding)
    info = os.fstat(fd)
    if (owner_uid != doc['ownerUid'] or broker_uid != identity['id']
            or record['gate'] != [info.st_dev, info.st_ino]):
        raise CustodyError('native-source-gate-intent-required')
    with protected_directory(NATIVE_STATE.parent) as parent:
        fixed = os.open(NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            fixed_info = os.fstat(fixed)
            if (fixed_info.st_dev, fixed_info.st_ino) != (info.st_dev, info.st_ino):
                raise CustodyError('native-source-gate-intent-required')
        finally:
            os.close(fixed)
    native_verify_gate(fd, owner_uid, broker_uid, prefix=True)
    lib = _native_acl_library()
    acl = ctypes.c_void_p(lib.acl_init(2))
    if not acl.value:
        raise CustodyError('native-source-acl-create-failed')
    try:
        for uid in (owner_uid, broker_uid):
            entry = ctypes.c_void_p()
            qualifier = (ctypes.c_ubyte * 16).from_buffer_copy(_native_uuid(lib, uid))
            if (lib.acl_create_entry(ctypes.byref(acl), ctypes.byref(entry)) != 0 or not entry.value
                    or lib.acl_set_tag_type(entry, 1) != 0
                    or lib.acl_set_qualifier(entry, qualifier) != 0
                    or lib.acl_set_permset_mask_np(entry, _NATIVE_LIST_SEARCH) != 0):
                raise CustodyError('native-source-acl-create-failed')
        _native_flags_zero(lib, acl)
        if lib.acl_valid(acl) != 0 or lib.acl_set_fd_np(fd, acl, _NATIVE_ACL_TYPE) != 0:
            raise CustodyError('native-source-acl-write-failed')
    finally:
        lib.acl_free(acl)
    native_verify_gate(fd, owner_uid, broker_uid)


def _native_no_acl(fd):
    if _native_acl_entries(fd, missing=True):
        raise CustodyError('native-source-descendant-acl')


def _native_identity_intent(value):
    if (type(value) is not dict or set(value) != {'schema', 'name', 'id', 'userGuid', 'groupGuid'}
            or type(value['schema']) is not int or value['schema'] != 1 or value['name'] != NATIVE_BROKER
            or type(value['id']) is not int or not 60000 <= value['id'] < 65000):
        raise CustodyError('native-source-identity-intent-invalid')
    for key in ('userGuid', 'groupGuid'):
        try:
            if type(value[key]) is not str or str(uuid.UUID(value[key])).upper() != value[key]:
                raise ValueError()
        except (ValueError, AttributeError):
            raise CustodyError('native-source-identity-intent-invalid') from None
    if value['userGuid'] == value['groupGuid']:
        raise CustodyError('native-source-identity-intent-invalid')
    return dict(value)


def _native_identity_attributes(intent, kind):
    intent = _native_identity_intent(intent)
    if kind == 'Groups':
        return {'GeneratedUID': intent['groupGuid'], 'Password': '*', 'PrimaryGroupID': str(intent['id'])}
    if kind != 'Users':
        raise CustodyError('native-source-identity-kind-invalid')
    # Same UUID-bound prefix algorithm as the protected native Operations helper.
    # UniqueID is LAST: a partial user must not acquire a usable UID early.
    return {'GeneratedUID': intent['userGuid'], 'Password': '*', 'UserShell': '/usr/bin/false',
        'NFSHomeDirectory': NATIVE_HOME, 'IsHidden': '1', 'PrimaryGroupID': str(intent['id']),
        'UniqueID': str(intent['id'])}


def _native_identity_record(record, expected, *, complete=False):
    if record is None:
        if complete:
            raise CustodyError('native-source-identity-record-missing')
        return
    if type(record) is not dict or record.get('GeneratedUID') != [expected['GeneratedUID']]:
        raise CustodyError('native-source-identity-foreign-record')
    for key, value in expected.items():
        if (key in record or complete) and record.get(key) != [value]:
            raise CustodyError('native-source-identity-attribute-conflict')
    for key in ('GroupMembership', 'GroupMembers', 'NestedGroups', 'AuthenticationAuthority', 'ShadowHashData'):
        if record.get(key):
            raise CustodyError('native-source-identity-authority-conflict')


def _native_identity_collisions(intent):
    users = pwd.getpwall()
    for entry in users:
        if (entry.pw_uid == intent['id'] and entry.pw_name != NATIVE_BROKER
                or entry.pw_gid == intent['id'] and (entry.pw_uid != intent['id'] or entry.pw_name != NATIVE_BROKER)):
            raise CustodyError('native-source-identity-id-collision')
    for entry in grp.getgrall():
        dedicated = entry.gr_gid == intent['id'] or entry.gr_name == NATIVE_BROKER
        if (dedicated and (entry.gr_gid != intent['id'] or entry.gr_name != NATIVE_BROKER or entry.gr_mem != [])
                or NATIVE_BROKER in entry.gr_mem):
            raise CustodyError('native-source-identity-membership-conflict')
    try:
        named = grp.getgrnam(NATIVE_BROKER)
    except KeyError:
        named = None
    if named is not None and (named.gr_name != NATIVE_BROKER or named.gr_gid != intent['id'] or named.gr_mem != []):
        raise CustodyError('native-source-identity-membership-conflict')
    record = _native_ds_record('Groups')
    _native_identity_record(record, _native_identity_attributes(intent, 'Groups'))
    # A UUID-only DirectoryService prefix has no assigned GID yet. Do not
    # confuse a separately resolved group with that not-yet-published prefix.
    ready = record is not None and record.get('PrimaryGroupID') == [str(intent['id'])]
    if not ready:
        if named is not None:
            raise CustodyError('native-source-identity-membership-conflict')
        return
    if named is None:
        raise CustodyError('native-source-identity-group-unavailable')
    _native_verify_group_readers(intent, users)


def _native_verify_group_readers(intent, users):
    """Verify this dedicated file-reader group, not every broker membership.

    Apple requires mbr_check_membership for effective UUID/nested membership;
    getgrouplist is partial and gr_mem alone cannot prove this boundary. The
    enumerated passwd view is not a universe of remote/hidden identities.
    """
    lib = _native_acl_library()
    group = (ctypes.c_ubyte * 16)()
    if (lib.mbr_gid_to_uuid(intent['id'], group) != 0
            or bytes(group) != uuid.UUID(intent['groupGuid']).bytes):
        raise CustodyError('native-source-identity-group-uuid-conflict')
    for entry in users:
        # UID 0 already has Root authority; it is not an excluded reader.
        if entry.pw_uid == 0 or entry.pw_uid == intent['id'] and entry.pw_name == NATIVE_BROKER:
            continue
        user = (ctypes.c_ubyte * 16).from_buffer_copy(_native_uuid(lib, entry.pw_uid))
        member = ctypes.c_int(-1)
        if lib.mbr_check_membership(user, group, ctypes.byref(member)) != 0 or member.value != 0:
            raise CustodyError('native-source-identity-membership-conflict')


def _native_ds_record(kind):
    if kind not in ('Groups', 'Users'):
        raise CustodyError('native-source-identity-kind-invalid')
    result = subprocess.run(['/usr/bin/dscl', '-plist', '.', '-read', '/' + kind + '/' + NATIVE_BROKER],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=False)
    if result.returncode:
        if b'eDSRecordNotFound' in result.stdout + result.stderr:
            return None
        raise CustodyError('native-source-directory-read-failed')
    try:
        value = plistlib.loads(result.stdout)
    except (ValueError, TypeError):
        raise CustodyError('native-source-directory-record-invalid') from None
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise CustodyError('native-source-directory-record-invalid')
    value = {key.removeprefix('dsAttrTypeStandard:'): item for key, item in value.items()}
    if 'dsAttrTypeNative:IsHidden' in value:
        if 'IsHidden' in value:
            raise CustodyError('native-source-directory-record-invalid')
        value['IsHidden'] = value.pop('dsAttrTypeNative:IsHidden')
    return value


def _native_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CustodyError('native-source-record-invalid')
        result[key] = value
    return result


def _native_json_at(directory, name, *, mode=0o600):
    if name not in ('source-identity.json', 'source-custody-prepare.json', 'source-environment.json', 'source-logs-prepare.json', 'source-publication.json', 'source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json'):
        raise CustodyError('native-source-record-invalid')
    if name in ('source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json'):
        try:
            raw, _inode = _native_root_file(directory, name, mode=mode,
                limit=524288 if name == 'source-execution.json' else 131072 if name == 'source-jobs-active.json' else 65536)
        except FileNotFoundError:
            return None, None
        try:
            return json.loads(raw, object_pairs_hook=_native_pairs), raw
        except (ValueError, UnicodeError, RecursionError):
            raise CustodyError('native-source-record-invalid') from None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None, None
    limit = 65536 if name in ('source-publication.json', 'source-jobs-prepare.json') else 16384
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1 or info.st_size > limit):
            raise CustodyError('native-source-record-unsafe')
        _native_no_acl(stream.fileno())
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise CustodyError('native-source-record-unsafe')
    try:
        value = json.loads(raw, object_pairs_hook=_native_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-record-invalid') from None
    return value, raw


def _native_save_at(selection, directory, name, value, *, previous=None, _publication=False):
    _native_selected(selection, **({'_publication': True} if _publication is True else {}))
    _current, raw = _native_json_at(directory, name)
    if raw != previous:
        raise CustodyError('native-source-record-changed')
    body = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(body) > (524288 if name == 'source-execution.json' else 131072 if name == 'source-jobs-active.json' else
            65536 if name in ('source-publication.json', 'source-jobs-prepare.json') else 16384):
        raise CustodyError('native-source-record-invalid')
    temporary = '.source-' + uuid.uuid4().hex
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                    or info.st_nlink != 1):
                raise CustodyError('native-source-record-unsafe')
            _native_no_acl(stream.fileno())
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        _native_selected(selection, **({'_publication': True} if _publication is True else {}))
        if previous is None:
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        else:
            if _native_json_at(directory, name)[1] != previous:
                raise CustodyError('native-source-record-changed')
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
    os.fsync(directory)
    return body


def native_prepare_identity(selection):
    """Unwired Root-only account preparation. No job, token or provider starts."""
    doc = _native_selected(selection)
    native_verify_kit(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_META) as directory:
        intent, raw = _native_json_at(directory, 'source-identity.json')
        if raw is None:
            if any(_native_ds_record(kind) is not None for kind in ('Groups', 'Users')):
                raise CustodyError('native-source-identity-unmanaged-record')
            # A remote/cached named record absent from the local DS view is
            # not an empty namespace and cannot be adopted by a fresh intent.
            for lookup in (pwd.getpwnam, grp.getgrnam):
                try:
                    lookup(NATIVE_BROKER)
                except KeyError:
                    continue
                raise CustodyError('native-source-identity-unmanaged-record')
            users, groups = pwd.getpwall(), grp.getgrall()
            if (any(entry.pw_name == NATIVE_BROKER for entry in users)
                    or any(entry.gr_name == NATIVE_BROKER or NATIVE_BROKER in entry.gr_mem for entry in groups)):
                raise CustodyError('native-source-identity-unmanaged-record')
            occupied = {doc['ownerUid'], doc['ownerGid']} | {entry.pw_uid for entry in users} | {entry.pw_gid for entry in users} | {entry.gr_gid for entry in groups}
            available = next((value for value in range(60000, 65000) if value not in occupied), None)
            if available is None:
                raise CustodyError('native-source-identity-id-exhausted')
            intent = {'schema': 1, 'name': NATIVE_BROKER, 'id': available,
                'userGuid': str(uuid.uuid4()).upper(), 'groupGuid': str(uuid.uuid4()).upper()}
            _native_save_at(selection, directory, 'source-identity.json', intent)
        intent = _native_identity_intent(intent)
        records = {kind: _native_ds_record(kind) for kind in ('Groups', 'Users')}
        for kind, record in records.items():
            _native_identity_record(record, _native_identity_attributes(intent, kind))
        _native_identity_collisions(intent)
        for kind in ('Groups', 'Users'):
            for key, value in _native_identity_attributes(intent, kind).items():
                record = _native_ds_record(kind)
                _native_identity_record(record, _native_identity_attributes(intent, kind))
                _native_identity_collisions(intent)
                if record is None or key not in record:
                    _native_selected(selection)
                    result = subprocess.run(['/usr/bin/dscl', '-plist', '.', '-create',
                        '/' + kind + '/' + NATIVE_BROKER, key, value], stdin=subprocess.DEVNULL,
                        capture_output=True, timeout=20, check=False)
                    if result.returncode:
                        raise CustodyError('native-source-directory-write-failed')
            _native_identity_record(_native_ds_record(kind), _native_identity_attributes(intent, kind), complete=True)
        _native_identity_collisions(intent)
        user, group = pwd.getpwnam(NATIVE_BROKER), grp.getgrnam(NATIVE_BROKER)
        if user.pw_uid != intent['id'] or user.pw_gid != intent['id'] or group.gr_gid != intent['id']:
            raise CustodyError('native-source-identity-id-conflict')
        lib = _native_acl_library()
        if _native_uuid(lib, intent['id']) != uuid.UUID(intent['userGuid']).bytes:
            raise CustodyError('native-source-identity-uuid-conflict')
        return intent



def native_verify_kit(selection, *, require_profiles=False, _publication=False):
    """A protected but not-yet-published kit is unavailable, never synthesized.

    The future protected native installer must publish this manifest with the
    selected Source blob and deployment. Schema1 is the exact A prefix;
    schema2 has all six profiles, never a subset. require_profiles attests only
    that protected kit, not an active broker or the remaining B lifecycle.
    This function does not publish or migrate any file/Root state.
    """
    if type(require_profiles) is not bool:
        raise CustodyError('native-source-kit-invalid')
    _native_selected(selection, **({'_publication': True} if _publication is True else {}))
    from pixel_macos_custody import protected_directory
    bodies = {}
    with protected_directory(NATIVE_KIT) as directory:
        names = set(os.listdir(directory))
        base = {'source-kit.json', 'broker.py', 'source-broker.env'}
        complete = names == base | set(NATIVE_PROFILES)
        if names != base and not complete:
            raise CustodyError('native-source-kit-invalid')
        if require_profiles and not complete:
            raise CustodyError('native-source-kit-profiles-incomplete')
        files = [('source-kit.json', 0o644), ('broker.py', 0o755), ('source-broker.env', 0o644)]
        if complete:
            files.extend((name, 0o644) for name in NATIVE_PROFILES)
        for name, mode in files:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != mode
                        or info.st_size > 2 * 1024 * 1024):
                    raise CustodyError('native-source-kit-unsafe')
                _native_no_acl(stream.fileno())
                body = stream.read(2 * 1024 * 1024 + 1)
                after = os.fstat(stream.fileno())
                if (len(body) > 2 * 1024 * 1024 or (info.st_dev, info.st_ino, info.st_size,
                        info.st_mtime_ns, info.st_ctime_ns) != (after.st_dev, after.st_ino,
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                    raise CustodyError('native-source-kit-changed')
                bodies[name] = body
    try:
        manifest = json.loads(bodies['source-kit.json'], object_pairs_hook=_native_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-kit-invalid') from None
    if (type(manifest) is not dict or set(manifest) != {'schemaVersion', 'kind', 'deploymentSha256', 'sourceRef', 'files'}
            or type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != (2 if complete else 1)
            or manifest['kind'] != 'ods-native-source-kit'
            or manifest['deploymentSha256'] != selection.deployment.checksum
            or type(manifest['sourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', manifest['sourceRef'])
            or type(manifest['files']) is not dict
            or set(manifest['files']) != {'broker.py', 'source-broker.env'} | (set(NATIVE_PROFILES) if complete else set())):
        raise CustodyError('native-source-kit-invalid')
    for name in manifest['files']:
        if (type(manifest['files'][name]) is not str or not _NATIVE_SHA.fullmatch(manifest['files'][name])
                or hashlib.sha256(bodies[name]).hexdigest() != manifest['files'][name]):
            raise CustodyError('native-source-kit-changed')
    # No shell interpretation. Validate the existing generated format before
    # returning even disabled values; runtime publication is a later slice.
    try:
        lines = parse_env(bodies['source-broker.env'].decode('utf-8'))
    except UnicodeError:
        raise CustodyError('native-source-kit-invalid') from None
    if len({key for key, _value in lines}) != len(lines):
        raise CustodyError('native-source-kit-invalid')
    _native_selected(selection, **({'_publication': True} if _publication is True else {}))
    return manifest, hashlib.sha256(bodies['source-kit.json']).hexdigest()


def _native_layout(doc, identity):
    uid, gid = identity['id'], identity['id']
    return (('private', uid, gid, 0o700), ('private/oauth', uid, gid, 0o700),
        ('projection', uid, doc['ownerGid'], 0o2750),
        ('results', uid, doc['ownerGid'], 0o2750),
        ('proposals', doc['ownerUid'], gid, 0o2750))


def _native_directory_fd(parent, relative):
    fd = os.dup(parent)
    try:
        for part in relative.split('/'):
            if part in ('', '.', '..'):
                raise CustodyError('native-source-layout-invalid')
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _native_descriptor(doc, identity, owner_guid, kit_sha):
    return {'schemaVersion': 1, 'kind': 'ods-native-source-custody',
        'phase': 'prepared-no-jobs', 'deploymentSha256': hashlib.sha256(
            json.dumps(doc, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
        'kitSha256': kit_sha, 'ownerUid': doc['ownerUid'], 'ownerGid': doc['ownerGid'],
        'ownerGuid': owner_guid, 'broker': dict(identity),
        'state': str(NATIVE_STATE), 'env': str(NATIVE_ENV), 'kit': str(NATIVE_KIT),
        'hostPython': doc['hostPythonPath']}


def _native_verify_data_tree(fd, *, owners, group, private, remaining, depth=0):
    """Metadata only, not provider/schema admission or credential inspection."""
    if depth > 32:
        raise CustodyError('native-source-state-too-large')
    for name in os.listdir(fd):
        remaining[0] -= 1
        if remaining[0] < 0:
            raise CustodyError('native-source-state-too-large')
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        directory = stat.S_ISDIR(info.st_mode)
        if not directory and not stat.S_ISREG(info.st_mode):
            raise CustodyError('native-source-descendant-unsafe')
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            | (os.O_DIRECTORY if directory else 0), dir_fd=fd)
        try:
            actual = os.fstat(child)
            if ((actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino)
                    or not (stat.S_ISDIR(actual.st_mode) if directory else stat.S_ISREG(actual.st_mode))):
                raise CustodyError('native-source-descendant-changed')
            allowed_modes = (0o700, 0o2700) if directory else ((0o600,) if private else (0o600, 0o640))
            if (actual.st_uid not in owners or actual.st_gid != group
                    or stat.S_IMODE(actual.st_mode) not in allowed_modes
                    or not directory and actual.st_nlink != 1):
                raise CustodyError('native-source-descendant-unsafe')
            _native_no_acl(child)
            if directory:
                _native_verify_data_tree(child, owners=owners, group=group, private=private,
                    remaining=remaining, depth=depth + 1)
        finally:
            os.close(child)


def _native_prepare_binding(doc, identity, owner_guid, kit_sha, checksum):
    return {'schema': 1, 'kind': 'native-source-prepare', 'deploymentSha256': checksum,
        'kitSha256': kit_sha, 'ownerUid': doc['ownerUid'], 'ownerGid': doc['ownerGid'],
        'ownerGuid': owner_guid, 'brokerUid': identity['id'], 'brokerGuid': identity['userGuid'],
        'groupGuid': identity['groupGuid']}


def _native_inode(value):
    return (type(value) is list and len(value) == 2 and all(type(v) is int for v in value)
            and value[0] >= 0 and value[1] > 0)


def _native_at_inode(directory, name):
    try:
        value = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return [value.st_dev, value.st_ino]


def _native_root_file(directory, name, *, mode=0o600, gid=0, limit=65536):
    """Bounded fixed-namespace FD read, including final path identity."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid) != (0, gid)
                or stat.S_IMODE(before.st_mode) != mode or before.st_nlink != 1 or before.st_size > limit):
            raise CustodyError('native-source-generation-file-unsafe')
        _native_no_acl(stream.fileno())
        body = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        stamp = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        identity = [before.st_dev, before.st_ino]
        if len(body) > limit or stamp(before) != stamp(after) or _native_at_inode(directory, name) != identity:
            raise CustodyError('native-source-generation-file-changed')
        return body, identity


def _native_validate_prepare_intent(intent, binding):
    if (type(intent) is not dict or set(intent) != {*binding, 'gate', 'children'}
            or any(type(intent[key]) is not type(value) or intent[key] != value for key, value in binding.items())
            or intent['gate'] is not None and not _native_inode(intent['gate'])
            or type(intent['children']) is not dict
            or set(intent['children']) - {'private', 'private/oauth', 'projection', 'results', 'proposals'}):
        raise CustodyError('native-source-state-intent-changed')
    for record in intent['children'].values():
        if (type(record) is not dict or set(record) != {'inode', 'ready'}
                or type(record['ready']) is not bool or not _native_inode(record['inode'])):
            raise CustodyError('native-source-state-intent-changed')
    if intent['gate'] is None and intent['children']:
        raise CustodyError('native-source-state-intent-changed')


def native_prepare_state(selection, identity):
    """Prepare only journal-bound native dirs/descriptor, never env/jobs/consent.

    Deliberate slice-A limit: retained action archives need the later lifecycle
    validator; this preparer refuses incompatible/nonconforming existing data.
    A crash before an inode is recorded leaves preserved unadoptable residue.
    """
    doc = _native_selected(selection)
    _manifest, kit_sha = native_verify_kit(selection)
    identity = _native_identity_intent(identity)
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_META) as metadata:
        recorded_identity, _raw = _native_json_at(metadata, 'source-identity.json')
    if _native_identity_intent(recorded_identity) != identity:
        raise CustodyError('native-source-identity-intent-changed')
    lib = _native_acl_library()
    owner_guid = str(uuid.UUID(bytes=_native_uuid(lib, doc['ownerUid']))).upper()
    for kind in ('Groups', 'Users'):
        _native_identity_record(_native_ds_record(kind), _native_identity_attributes(identity, kind), complete=True)
    _native_identity_collisions(identity)
    if _native_uuid(lib, identity['id']) != uuid.UUID(identity['userGuid']).bytes:
        raise CustodyError('native-source-identity-uuid-conflict')
    binding = _native_prepare_binding(doc, identity, owner_guid, kit_sha, selection.deployment.checksum)
    with protected_directory(NATIVE_META) as metadata:
        intent, raw = _native_json_at(metadata, 'source-custody-prepare.json')
        if raw is None:
            if os.path.lexists(NATIVE_STATE):
                raise CustodyError('native-source-state-unmanaged')
            intent = dict(binding, gate=None, children={})
            raw = _native_save_at(selection, metadata, 'source-custody-prepare.json', intent)
        _native_validate_prepare_intent(intent, binding)
        with protected_directory(NATIVE_STATE.parent) as parent:
            if intent['gate'] is None:
                _native_selected(selection)
                try:
                    os.mkdir(NATIVE_STATE.name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    raise CustodyError('native-source-state-unmanaged') from None
                os.fsync(parent)
                gate = os.open(NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    native_verify_gate(gate, doc['ownerUid'], identity['id'], prefix=True)
                    if os.listdir(gate):
                        raise CustodyError('native-source-state-unmanaged')
                    info = os.fstat(gate)
                    intent['gate'] = [info.st_dev, info.st_ino]
                    raw = _native_save_at(selection, metadata, 'source-custody-prepare.json', intent, previous=raw)
                finally:
                    os.close(gate)
            gate = os.open(NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(gate)
                if intent['gate'] != [info.st_dev, info.st_ino]:
                    raise CustodyError('native-source-gate-identity-changed')
                native_verify_gate(gate, doc['ownerUid'], identity['id'], prefix=True)
                if set(os.listdir(gate)) - {'private', 'projection', 'results', 'proposals', 'custody.json'}:
                    raise CustodyError('native-source-state-unmanaged')
                for relative, uid, gid, mode in _native_layout(doc, identity):
                    parts = relative.split('/')
                    container = _native_directory_fd(gate, '/'.join(parts[:-1])) if len(parts) > 1 else os.dup(gate)
                    try:
                        record = intent['children'].get(relative)
                        if record is None:
                            _native_selected(selection)
                            try:
                                os.mkdir(parts[-1], mode=0o700, dir_fd=container)
                            except FileExistsError:
                                raise CustodyError('native-source-descendant-unmanaged') from None
                            os.fsync(container)
                            child = _native_directory_fd(container, parts[-1])
                            try:
                                created = os.fstat(child)
                                if (created.st_uid != 0 or created.st_gid != 0
                                        or stat.S_IMODE(created.st_mode) != 0o700 or os.listdir(child)):
                                    raise CustodyError('native-source-descendant-unmanaged')
                                _native_no_acl(child)
                                intent['children'][relative] = {'inode': [created.st_dev, created.st_ino], 'ready': False}
                                raw = _native_save_at(selection, metadata, 'source-custody-prepare.json', intent, previous=raw)
                            finally:
                                os.close(child)
                        child = _native_directory_fd(container, parts[-1])
                        try:
                            record = intent['children'][relative]
                            actual = os.fstat(child)
                            if (type(record) is not dict or set(record) != {'inode', 'ready'}
                                    or type(record['ready']) is not bool or record['inode'] != [actual.st_dev, actual.st_ino]):
                                raise CustodyError('native-source-descendant-changed')
                            _native_no_acl(child)
                            if not record['ready']:
                                if (os.listdir(child) or (actual.st_uid, actual.st_gid) not in ((0, 0), (uid, gid))
                                        or stat.S_IMODE(actual.st_mode) not in (0o700, mode)):
                                    raise CustodyError('native-source-descendant-unmanaged')
                                _native_selected(selection)
                                if (actual.st_uid, actual.st_gid) != (uid, gid):
                                    os.fchown(child, uid, gid)
                                os.fchmod(child, mode)
                                os.fsync(child)
                                intent['children'][relative]['ready'] = True
                                raw = _native_save_at(selection, metadata, 'source-custody-prepare.json', intent, previous=raw)
                            actual = os.fstat(child)
                            if (actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode)) != (uid, gid, mode):
                                raise CustodyError('native-source-descendant-changed')
                        finally:
                            os.close(child)
                    finally:
                        os.close(container)
                descriptor = _native_descriptor(doc, identity, owner_guid, kit_sha)
                expected = (json.dumps(descriptor, sort_keys=True, separators=(',', ':')) + '\n').encode()
                try:
                    fd = os.open('custody.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=gate)
                except FileNotFoundError:
                    _native_selected(selection)
                    fd = os.open('custody.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=gate)
                    with os.fdopen(fd, 'wb') as stream:
                        info = os.fstat(stream.fileno())
                        if info.st_uid != 0 or info.st_nlink != 1:
                            raise CustodyError('native-source-descriptor-unsafe')
                        _native_no_acl(stream.fileno())
                        os.fchown(stream.fileno(), 0, identity['id'])
                        os.fchmod(stream.fileno(), 0o640)
                        stream.write(expected)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(gate)
                    fd = os.open('custody.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=gate)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != identity['id']
                            or stat.S_IMODE(info.st_mode) != 0o640 or info.st_nlink != 1 or info.st_size > 16384):
                        raise CustodyError('native-source-descriptor-unsafe')
                    _native_no_acl(stream.fileno())
                    if stream.read(16385) != expected:
                        raise CustodyError('native-source-descriptor-changed')
                # Metadata walks never read token, handoff, message or provider bytes.
                for relative, _uid, gid, _mode in _native_layout(doc, identity):
                    if relative == 'private/oauth':
                        continue
                    child = _native_directory_fd(gate, relative)
                    try:
                        _native_verify_data_tree(child, owners={identity['id']} if relative != 'proposals' else {doc['ownerUid']},
                            group=gid, private=relative == 'private', remaining=[MAX_RETIRED_ACTION_FILES])
                    finally:
                        os.close(child)
                _native_selected(selection)
                wanted = (_native_uuid(lib, doc['ownerUid']), _native_uuid(lib, identity['id']))
                if _native_acl_entries(gate, missing=True) != wanted:
                    _native_write_gate_acl(selection, gate, doc['ownerUid'], identity['id'])
                native_verify_gate(gate, doc['ownerUid'], identity['id'])
                return descriptor
            finally:
                os.close(gate)


# Native slice B: an internal Root publisher only. No caller selects this
# backend yet; no launchd job, credential handoff or availability is enabled.
def _native_environment_paths():
    return {'PIXEL_SOURCE_TOKEN_PATH': str(NATIVE_STATE / 'private/google-token.json'),
        'PIXEL_SOURCE_OAUTH_DIR': str(NATIVE_STATE / 'private/oauth'),
        'PIXEL_SOURCE_PROJECTION_DIR': str(NATIVE_STATE / 'projection'),
        'PIXEL_ACTION_PROPOSAL_DIR': str(NATIVE_STATE / 'proposals'),
        'PIXEL_ACTION_RESULT_DIR': str(NATIVE_STATE / 'results')}


def _native_environment_template(selection):
    manifest, checksum = native_verify_kit(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_KIT) as directory:
        fd = os.open('source-broker.env', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            _native_no_acl(stream.fileno())
            data = stream.read(MAX_RESULT_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0
                    or stat.S_IMODE(before.st_mode) != 0o644 or before.st_nlink != 1
                    or len(data) > MAX_RESULT_BYTES
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or hashlib.sha256(data).hexdigest() != manifest['files']['source-broker.env']):
                raise CustodyError('native-source-environment-template-changed')
    try:
        lines = parse_env(data.decode('utf-8'))
    except UnicodeError:
        raise CustodyError('native-source-environment-template-invalid') from None
    if (not _native_environment_paths().keys() <= dict(lines).keys()
            or any(not key.startswith('PIXEL_') and (key, value) != ('PYTHONDONTWRITEBYTECODE', '1')
                   for key, value in lines)):
        raise CustodyError('native-source-environment-template-invalid')
    _native_selected(selection)
    return lines, checksum


def native_environment_text(selection, overrides=None, *, preauth=False):
    """Render only the admitted template; no shell/loader or request paths.

    The Root connection transaction computes these existing capability/query
    choices. This helper is not consent, a permission decision, or a launcher.
    """
    if type(preauth) is not bool or overrides is not None and type(overrides) is not dict:
        raise CustodyError('native-source-environment-invalid')
    lines, _checksum = _native_environment_template(selection)
    supplied = {} if overrides is None else dict(overrides)
    mutable = {*PREAUTH_FLAGS, 'PIXEL_SOURCE_CALENDAR_ID', 'PIXEL_SOURCE_TIME_ZONE',
               'PIXEL_SOURCE_GMAIL_QUERY', 'PIXEL_SOURCE_GMAIL_SENT_QUERY'}
    if (set(supplied) - mutable or any(type(value) is not str or any(c in value for c in '\0\n\r')
                                     for value in supplied.values())):
        raise CustodyError('native-source-environment-invalid')
    if preauth:
        supplied.update({key: '0' for key, _value in lines
            if key in PREAUTH_FLAGS or key.startswith('PIXEL_LIMB_') and key.endswith('_ENABLED')})
    supplied.update(_native_environment_paths())
    return env_text(lines, supplied)


def _native_environment_bytes(selection, text):
    if type(text) is not str or '\0' in text:
        raise CustodyError('native-source-environment-invalid')
    try:
        data = text.encode('utf-8')
        lines = parse_env(text)
    except UnicodeError:
        raise CustodyError('native-source-environment-invalid') from None
    if not 0 < len(data) <= MAX_RESULT_BYTES:
        raise CustodyError('native-source-environment-invalid')
    template, checksum = _native_environment_template(selection)
    values, base = dict(lines), dict(template)
    mutable = {*PREAUTH_FLAGS, 'PIXEL_SOURCE_CALENDAR_ID', 'PIXEL_SOURCE_TIME_ZONE',
               'PIXEL_SOURCE_GMAIL_QUERY', 'PIXEL_SOURCE_GMAIL_SENT_QUERY'}
    fixed = _native_environment_paths()
    if (list(values) != list(base) or any(values.get(key) != value for key, value in fixed.items())
            or any(values[key] != value for key, value in base.items() if key not in mutable and key not in fixed)
            or any(values[key] not in ('0', '1') for key in PREAUTH_FLAGS if key in values)):
        raise CustodyError('native-source-environment-invalid')
    return data, checksum


def _native_environment_snapshot(directory, name, broker_uid):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        before = native_verify_env_fd(stream.fileno(), broker_uid)
        body = stream.read(MAX_RESULT_BYTES + 1)
        if (len(body) > MAX_RESULT_BYTES or native_verify_env_fd(stream.fileno(), broker_uid) != before
                or len(body) != before[6]):
            raise CustodyError('native-source-env-changed')
        return {'inode': list(before[:2]), 'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body)}


def _native_environment_record(value, binding):
    if (type(value) is not dict or set(value) != {*binding, 'current', 'pending'}
            or any(type(value[key]) is not type(item) or value[key] != item for key, item in binding.items())):
        raise CustodyError('native-source-environment-journal-changed')
    current, pending = value['current'], value['pending']
    if current is not None and (type(current) is not dict or set(current) != {'inode', 'sha256', 'size'}
            or not _native_inode(current['inode']) or type(current['sha256']) is not str
            or not _NATIVE_SHA.fullmatch(current['sha256']) or type(current['size']) is not int
            or not 0 < current['size'] <= MAX_RESULT_BYTES):
        raise CustodyError('native-source-environment-journal-changed')
    if pending is not None and (type(pending) is not dict or set(pending) != {'name', 'inode', 'sha256', 'ready'}
            or type(pending['name']) is not str or not re.fullmatch(r'\.source-env-[a-f0-9]{32}', pending['name'])
            or pending['inode'] is not None and not _native_inode(pending['inode'])
            or type(pending['sha256']) is not str or not _NATIVE_SHA.fullmatch(pending['sha256'])
            or type(pending['ready']) is not bool or pending['ready'] and pending['inode'] is None):
        raise CustodyError('native-source-environment-journal-changed')


def _native_environment_binding(selection, metadata, gate, kit_sha, *, _publication=False):
    doc = _native_selected(selection, **({'_publication': True} if _publication is True else {}))
    _manifest, current_kit = native_verify_kit(selection, _publication=_publication)
    if current_kit != kit_sha:
        raise CustodyError('native-source-environment-template-changed')
    identity, _raw = _native_json_at(metadata, 'source-identity.json')
    identity = _native_identity_intent(identity)
    for kind in ('Groups', 'Users'):
        _native_identity_record(_native_ds_record(kind), _native_identity_attributes(identity, kind), complete=True)
    _native_identity_collisions(identity)
    lib = _native_acl_library()
    owner_guid = str(uuid.UUID(bytes=_native_uuid(lib, doc['ownerUid']))).upper()
    if _native_uuid(lib, identity['id']) != uuid.UUID(identity['userGuid']).bytes:
        raise CustodyError('native-source-identity-uuid-conflict')
    intent, _raw = _native_json_at(metadata, 'source-custody-prepare.json')
    prepared = _native_prepare_binding(doc, identity, owner_guid, kit_sha, selection.deployment.checksum)
    _native_validate_prepare_intent(intent, prepared)
    inode = native_verify_gate(gate, doc['ownerUid'], identity['id'])
    if set(os.listdir(gate)) - {'private', 'projection', 'results', 'proposals', 'custody.json', NATIVE_ENV.name}:
        raise CustodyError('native-source-environment-state-unmanaged')
    if intent['gate'] != list(inode) or set(intent['children']) != {row[0] for row in _native_layout(doc, identity)}:
        raise CustodyError('native-source-environment-state-unprepared')
    for relative, uid, gid, mode in _native_layout(doc, identity):
        child = _native_directory_fd(gate, relative)
        try:
            actual, recorded = os.fstat(child), intent['children'][relative]
            if (not recorded['ready'] or recorded['inode'] != [actual.st_dev, actual.st_ino]
                    or (actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode)) != (uid, gid, mode)):
                raise CustodyError('native-source-environment-state-changed')
            _native_no_acl(child)
            if relative != 'private/oauth':
                _native_verify_data_tree(child, owners={uid}, group=gid, private=relative == 'private',
                                         remaining=[MAX_RETIRED_ACTION_FILES])
        finally:
            os.close(child)
    expected = (json.dumps(_native_descriptor(doc, identity, owner_guid, kit_sha),
                           sort_keys=True, separators=(',', ':')) + '\n').encode()
    return identity, {'schemaVersion': 1, 'kind': 'ods-native-source-environment',
        'deploymentSha256': selection.deployment.checksum, 'kitSha256': kit_sha,
        'gate': list(inode), 'ownerUid': doc['ownerUid'], 'brokerUid': identity['id'],
        'brokerGuid': identity['userGuid']}, expected


def _native_environment_context(selection, metadata, gate, kit_sha, *, _publication=False):
    identity, binding, expected = _native_environment_binding(selection, metadata, gate, kit_sha,
        _publication=_publication)
    fd = os.open('custody.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=gate)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != identity['id']
                or stat.S_IMODE(info.st_mode) != 0o640 or info.st_nlink != 1 or not 0 < info.st_size <= 16384):
            raise CustodyError('native-source-descriptor-unsafe')
        _native_no_acl(stream.fileno())
        raw = stream.read(16385)
        current = os.fstat(stream.fileno())
        stamp = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if (len(raw) != info.st_size or stamp(info) != stamp(current)
                or [info.st_dev, info.st_ino] != _native_at_inode(gate, 'custody.json')):
            raise CustodyError('native-source-descriptor-changed')
        if raw != expected:
            _native_validate_inactive_generation(selection, metadata, gate, identity, binding, expected,
                raw, [info.st_dev, info.st_ino])
    return identity, binding


def _native_encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _native_job_record(record, binding, expected):
    """Closed Root intent. Its hashes do not authorize arbitrary plist bytes."""
    keys = {'schemaVersion', 'kind', 'generation', 'phase', 'binding', 'environment',
        'before', 'directory', 'definitions', 'manifest', 'gate', 'witness'}
    phases = {'staging', 'publishing', 'manifest-ready', 'moving-gate',
        'committed-inactive', 'restoring', 'restored', 'unknown', 'retired'}
    if (type(record) is not dict or set(record) != keys or record['schemaVersion'] != 1
            or type(record['schemaVersion']) is not int or record['kind'] != 'ods-native-source-job-prepare'
            or type(record['generation']) is not str or not FLOW_ID.fullmatch(record['generation'])
            or type(record['phase']) is not str or record['phase'] not in phases
            or record['binding'] != binding or type(record['before']) is not dict
            or set(record['before']) != {'inode', 'descriptor'} or not _native_inode(record['before']['inode'])
            or _native_encoded(record['before']['descriptor']) != expected
            or record['directory'] is not None and not _native_inode(record['directory'])
            or type(record['definitions']) is not dict or not 1 <= len(record['definitions']) <= 64):
        raise CustodyError('native-source-job-record-changed')
    environment = record['environment']
    if (type(environment) is not dict or set(environment) != {'inode', 'sha256', 'size'}
            or not _native_inode(environment['inode']) or type(environment['sha256']) is not str
            or not _NATIVE_SHA.fullmatch(environment['sha256']) or type(environment['size']) is not int
            or not 0 < environment['size'] <= MAX_RESULT_BYTES):
        raise CustodyError('native-source-job-record-changed')
    old = record['before']['descriptor']
    for label, item in record['definitions'].items():
        if (type(label) is not str or type(item) is not dict
                or set(item) != {'role', 'identifier', 'sha256', 'inode', 'ready'}
                or item['inode'] is not None and not _native_inode(item['inode'])
                or type(item['ready']) is not bool or item['ready'] and item['inode'] is None):
            raise CustodyError('native-source-job-record-changed')
        path, body, _mode = native_disabled_definition(item['role'], identity=old['broker'],
            python=old['hostPython'], identifier=item['identifier'])
        if label != path.stem or item['sha256'] != hashlib.sha256(body).hexdigest():
            raise CustodyError('native-source-job-record-changed')
    for key in ('manifest', 'gate'):
        image = record[key]
        if (type(image) is not dict or set(image) != {'inode', 'ready'}
                or image['inode'] is not None and not _native_inode(image['inode'])
                or type(image['ready']) is not bool or image['ready'] and image['inode'] is None):
            raise CustodyError('native-source-job-record-changed')
    witness = record['witness']
    if (type(witness) is not dict or set(witness) != {'kind', 'boot', 'loadedSourceJobs', 'brokerProcesses', 'brokerUid'}
            or witness['kind'] != 'never-started-observed' or type(witness['boot']) is not str
            or not re.fullmatch(r'[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', witness['boot'])
            or type(witness['loadedSourceJobs']) is not int or witness['loadedSourceJobs'] != 0
            or type(witness['brokerProcesses']) is not int or witness['brokerProcesses'] != 0
            or witness['brokerUid'] != old['broker']['id']):
        raise CustodyError('native-source-job-record-changed')


def _native_job_manifest(record):
    return {'schemaVersion': 1, 'kind': 'ods-native-source-job-generation',
        'generation': record['generation'], 'phase': 'staged-disabled',
        **{key: record['binding'][key] for key in ('deploymentSha256', 'publicationSha256', 'kitSha256')},
        'environment': {key: record['environment'][key] for key in ('inode', 'sha256')},
        'definitions': {label: {'inode': item['inode'], 'sha256': item['sha256']}
            for label, item in record['definitions'].items()}}


def _native_job_projection(record):
    # Broker-readable commit point. Inactive is explicit, never inferred from
    # old v1 bytes, an exit status, existing files, or a Root intent alone.
    value = dict(record['before']['descriptor'], schemaVersion=2, phase='inactive')
    value['launch'] = {'generation': record['generation'], 'phase': 'inactive',
        'environment': {key: record['environment'][key] for key in ('inode', 'sha256')},
        'definitions': {label: item['sha256'] for label, item in record['definitions'].items()}}
    return _native_encoded(value)


def _native_job_directory(metadata, record):
    fd = os.open('native-source-jobs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)
                or record['directory'] != [info.st_dev, info.st_ino]
                or _native_at_inode(metadata, 'native-source-jobs') != record['directory']):
            raise CustodyError('native-source-job-directory-changed')
        _native_no_acl(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _native_validate_inactive_generation(selection, metadata, gate, identity, binding, expected, raw, inode):
    record, _record_raw = _native_json_at(metadata, 'source-jobs-prepare.json')
    publication, _pub_inode = _native_root_file(metadata, 'source-publication.json')
    binding = dict(binding, publicationSha256=hashlib.sha256(publication).hexdigest())
    _native_job_record(record, binding, expected)
    if (record['phase'] != 'committed-inactive' or not record['gate']['ready']
            or not record['manifest']['ready'] or record['gate']['inode'] != inode
            or any(not item['ready'] for item in record['definitions'].values())
            or raw != _native_job_projection(record)):
        raise CustodyError('native-source-job-generation-incomplete')
    env, _env_raw = _native_json_at(metadata, 'source-environment.json')
    _native_environment_record(env, {key: value for key, value in binding.items() if key != 'publicationSha256'})
    if (env['pending'] is not None or env['current'] != record['environment']
            or _native_environment_snapshot(gate, NATIVE_ENV.name, identity['id']) != record['environment']):
        raise CustodyError('native-source-job-environment-changed')
    jobs = _native_job_directory(metadata, record)
    from pixel_macos_custody import protected_directory
    try:
        if set(os.listdir(jobs)) != {'manifest.json', '.gate-before'}:
            raise CustodyError('native-source-job-directory-changed')
        manifest, manifest_inode = _native_root_file(jobs, 'manifest.json')
        before, before_inode = _native_root_file(jobs, '.gate-before', mode=0o640, gid=identity['id'], limit=16384)
        if (manifest != _native_encoded(_native_job_manifest(record)) or manifest_inode != record['manifest']['inode']
                or before != expected or before_inode != record['before']['inode']):
            raise CustodyError('native-source-job-generation-changed')
        with protected_directory(NATIVE_DEFINITIONS) as definitions:
            for label, item in record['definitions'].items():
                _path, body, _mode = native_disabled_definition(item['role'], identity=identity,
                    python=record['before']['descriptor']['hostPython'], identifier=item['identifier'])
                actual, actual_inode = _native_root_file(definitions, label + '.plist', mode=0o644, limit=16384)
                if actual != body or actual_inode != item['inode']:
                    raise CustodyError('native-source-job-definition-changed')
    finally:
        os.close(jobs)


def _native_environment_acl(selection, fd, broker_uid):
    """Only called on an inode already recorded in the Root pending journal."""
    from pixel_macos_custody import protected_directory
    _manifest, kit_sha = native_verify_kit(selection)
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_STATE.parent) as parent:
        gate = os.open(NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            identity, binding = _native_environment_context(selection, metadata, gate, kit_sha)
            record, _raw = _native_json_at(metadata, 'source-environment.json')
            _native_environment_record(record, binding)
            info, pending = os.fstat(fd), record['pending']
            if (pending is None or pending['inode'] != [info.st_dev, info.st_ino]
                    or identity['id'] != broker_uid or not stat.S_ISREG(info.st_mode)
                    or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1 or not 0 < info.st_size <= MAX_RESULT_BYTES):
                raise CustodyError('native-source-env-intent-required')
            leaf = os.stat(pending['name'], dir_fd=metadata, follow_symlinks=False)
            if (leaf.st_dev, leaf.st_ino) != (info.st_dev, info.st_ino):
                raise CustodyError('native-source-env-intent-required')
            body = os.pread(fd, MAX_RESULT_BYTES + 1, 0)
            if len(body) != info.st_size or hashlib.sha256(body).hexdigest() != pending['sha256']:
                raise CustodyError('native-source-env-candidate-changed')
        finally:
            os.close(gate)
    _native_selected(selection)
    lib = _native_acl_library()
    ctypes.set_errno(0)
    existing = lib.acl_get_fd_np(fd, _NATIVE_ACL_TYPE)
    if existing:
        try:
            if lib.acl_valid(existing) != 0:
                raise CustodyError('native-source-env-acl-invalid')
            _native_flags_zero(lib, existing)
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            result = lib.acl_get_entry(existing, 0, ctypes.byref(entry))
            empty = result == -1 and ctypes.get_errno() == errno.EINVAL
        finally:
            lib.acl_free(existing)
        if not empty:
            native_verify_env_fd(fd, broker_uid)  # Never replace an unknown ACL.
            return
    elif ctypes.get_errno() != errno.ENOENT:
        raise CustodyError('native-source-env-acl-required')
    acl = ctypes.c_void_p(lib.acl_init(1))
    if not acl.value:
        raise CustodyError('native-source-acl-create-failed')
    try:
        entry = ctypes.c_void_p()
        qualifier = (ctypes.c_ubyte * 16).from_buffer_copy(_native_uuid(lib, broker_uid))
        if (lib.acl_create_entry(ctypes.byref(acl), ctypes.byref(entry)) != 0 or not entry.value
                or lib.acl_set_tag_type(entry, 1) != 0 or lib.acl_set_qualifier(entry, qualifier) != 0
                or lib.acl_set_permset_mask_np(entry, 1 << 1) != 0):
            raise CustodyError('native-source-acl-create-failed')
        _native_flags_zero(lib, acl)
        if lib.acl_valid(acl) != 0 or lib.acl_set_fd_np(fd, acl, _NATIVE_ACL_TYPE) != 0:
            raise CustodyError('native-source-acl-write-failed')
    finally:
        lib.acl_free(acl)
    native_verify_env_fd(fd, broker_uid)


def native_publish_environment(selection, text):
    """Durable fixed-gate env replacement under the existing Root admission.

    The journal holds identities/hashes only, never credentials or env text.
    A lost rename acknowledgement resumes the SAME candidate, never a job.
    Unknown/unrecorded files and changed deployments are preserved/refused.
    """
    data, kit_sha = _native_environment_bytes(selection, text)
    wanted = hashlib.sha256(data).hexdigest()
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_STATE.parent) as parent:
        gate = os.open(NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            identity, binding = _native_environment_context(selection, metadata, gate, kit_sha)
            if os.fstat(metadata).st_dev != os.fstat(gate).st_dev:
                raise CustodyError('native-source-env-cross-filesystem')
            record, raw = _native_json_at(metadata, 'source-environment.json')
            actual = _native_environment_snapshot(gate, NATIVE_ENV.name, identity['id'])
            if raw is None:
                if actual is not None:
                    raise CustodyError('native-source-env-unmanaged')
                record = dict(binding, current=None, pending=None)
            _native_environment_record(record, binding)
            pending = record['pending']
            if pending is None:
                if actual != record['current']:
                    raise CustodyError('native-source-env-changed')
                if actual is not None and actual['sha256'] == wanted:
                    return dict(actual)
                record['pending'] = {'name': '.source-env-' + uuid.uuid4().hex, 'inode': None,
                                     'sha256': wanted, 'ready': False}
                raw = _native_save_at(selection, metadata, 'source-environment.json', record, previous=raw)
                pending = record['pending']
            if pending['sha256'] != wanted:
                raise CustodyError('native-source-environment-pending')
            # A crash after the exact rename must not restore/re-publish an old
            # env. Complete only this journal-bound new inode and hash.
            if actual is not None and pending['ready'] and actual['inode'] == pending['inode'] and actual['sha256'] == wanted:
                record.update(current=actual, pending=None)
                _native_save_at(selection, metadata, 'source-environment.json', record, previous=raw)
                return dict(actual)
            if actual != record['current']:
                raise CustodyError('native-source-env-changed')
            if pending['inode'] is None:
                _native_selected(selection)
                try:
                    fd = os.open(pending['name'], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=metadata)
                except FileExistsError:
                    raise CustodyError('native-source-env-candidate-unmanaged') from None
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size):
                        raise CustodyError('native-source-env-candidate-unsafe')
                    _native_no_acl(fd)
                    pending['inode'] = [info.st_dev, info.st_ino]
                    os.fsync(fd)
                    os.fsync(metadata)
                    raw = _native_save_at(selection, metadata, 'source-environment.json', record, previous=raw)
                finally:
                    os.close(fd)
            fd = os.open(pending['name'], os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=metadata)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or pending['inode'] != [info.st_dev, info.st_ino]
                        or info.st_uid != 0 or info.st_gid != 0 or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600 or not 0 <= info.st_size <= len(data)):
                    raise CustodyError('native-source-env-candidate-changed')
                prefix = os.read(fd, MAX_RESULT_BYTES + 1)
                if prefix != data[:len(prefix)]:
                    raise CustodyError('native-source-env-candidate-changed')
                if len(prefix) != len(data):
                    _native_no_acl(fd)  # A readable incomplete candidate is not a valid prefix.
                    _native_selected(selection)
                    os.lseek(fd, len(prefix), os.SEEK_SET)
                    remaining = memoryview(data)[len(prefix):]
                    while remaining:
                        count = os.write(fd, remaining)
                        if count <= 0:
                            raise CustodyError('native-source-env-write-failed')
                        remaining = remaining[count:]
                    os.fsync(fd)
                _native_environment_acl(selection, fd, identity['id'])
                os.fsync(fd)
                snapshot = _native_environment_snapshot(metadata, pending['name'], identity['id'])
                if snapshot != {'inode': pending['inode'], 'sha256': wanted, 'size': len(data)}:
                    raise CustodyError('native-source-env-candidate-changed')
                if not pending['ready']:
                    pending['ready'] = True
                    raw = _native_save_at(selection, metadata, 'source-environment.json', record, previous=raw)
                _native_environment_context(selection, metadata, gate, kit_sha)
                if _native_environment_snapshot(gate, NATIVE_ENV.name, identity['id']) != record['current']:
                    raise CustodyError('native-source-env-changed')
                if _native_environment_snapshot(metadata, pending['name'], identity['id']) != snapshot:
                    raise CustodyError('native-source-env-candidate-changed')
                _native_selected(selection)
                os.replace(pending['name'], NATIVE_ENV.name, src_dir_fd=metadata, dst_dir_fd=gate)
                os.fsync(gate)
                os.fsync(metadata)
                actual = _native_environment_snapshot(gate, NATIVE_ENV.name, identity['id'])
                if actual != snapshot:
                    raise CustodyError('native-source-env-changed')
                record.update(current=actual, pending=None)
                _native_save_at(selection, metadata, 'source-environment.json', record, previous=raw)
                return dict(actual)
            finally:
                os.close(fd)
        finally:
            os.close(gate)


def _native_log_names():
    return tuple(role + '.' + stream + '.log' for role in NATIVE_ROLES for stream in ('stdout', 'stderr'))


def native_verify_log_directory(fd, broker_uid, *, prefix=False):
    """Metadata-only singleton broker LIST/SEARCH; no owner/group principal."""
    if type(prefix) is not bool:
        raise CustodyError('native-source-log-specification-invalid')
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise CustodyError('native-source-log-directory-unsafe')
    wanted = (_native_uuid(_native_acl_library(), broker_uid),)
    actual = _native_acl_entries(fd, missing=prefix)
    if actual != wanted and not (prefix and actual == ()):
        raise CustodyError('native-source-log-directory-acl')
    return [info.st_dev, info.st_ino]


def _native_log_file(fd, identity, *, prefix=False):
    info = os.fstat(fd)
    owner = (info.st_uid, info.st_gid)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 <= info.st_size <= MAX_RETIRED_ACTION_BYTES
            or owner != (identity['id'], identity['id']) and not (prefix and owner == (0, 0) and info.st_size == 0)):
        raise CustodyError('native-source-log-file-unsafe')
    _native_no_acl(fd)
    return [info.st_dev, info.st_ino]


def _native_log_record(record, binding):
    if (type(record) is not dict or set(record) != {*binding, 'directory', 'files'}
            or any(type(record[key]) is not type(value) or record[key] != value for key, value in binding.items())
            or record['directory'] is not None and not _native_inode(record['directory'])
            or type(record['files']) is not dict or set(record['files']) - set(_native_log_names())
            or record['directory'] is None and record['files']):
        raise CustodyError('native-source-log-intent-changed')
    for value in record['files'].values():
        if (type(value) is not dict or set(value) != {'inode', 'ready'}
                or not _native_inode(value['inode']) or type(value['ready']) is not bool):
            raise CustodyError('native-source-log-intent-changed')


def _native_log_binding(selection, metadata, kit_sha, *, _publication=False):
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_STATE.parent) as parent:
        fd = os.open(NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            identity, context = _native_environment_context(selection, metadata, fd, kit_sha, _publication=_publication)
        finally:
            os.close(fd)
    return identity, {**context, 'kind': 'ods-native-source-log-prepare', 'path': str(NATIVE_LOGS)}


def _native_log_files(directory, record, identity):
    if set(record['files']) != set(_native_log_names()) or set(os.listdir(directory)) != set(_native_log_names()):
        raise CustodyError('native-source-logs-incomplete')
    for name in _native_log_names():
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            value = record['files'][name]
            if not value['ready'] or value['inode'] != _native_log_file(fd, identity):
                raise CustodyError('native-source-log-file-changed')
        finally:
            os.close(fd)


def _native_verify_prepared_logs(metadata, identity, environment_binding):
    """Read-only exact log publication without following an intermediate Gate."""
    record, _raw = _native_json_at(metadata, 'source-logs-prepare.json')
    _native_log_record(record, {**environment_binding, 'kind': 'ods-native-source-log-prepare', 'path': str(NATIVE_LOGS)})
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_LOGS.parent) as parent:
        fd = os.open(NATIVE_LOGS.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            inode = native_verify_log_directory(fd, identity['id'])
            if inode != record['directory'] or inode != _native_at_inode(parent, NATIVE_LOGS.name):
                raise CustodyError('native-source-log-file-changed')
            _native_log_files(fd, record, identity)
        finally:
            os.close(fd)


def _native_publish_log_acl(selection, directory, identity):
    from pixel_macos_custody import protected_directory
    _manifest, kit_sha = native_verify_kit(selection)
    with protected_directory(NATIVE_META) as metadata:
        selected, binding = _native_log_binding(selection, metadata, kit_sha)
        record, _raw = _native_json_at(metadata, 'source-logs-prepare.json')
        _native_log_record(record, binding)
        if selected != identity or record['directory'] != native_verify_log_directory(directory, identity['id'], prefix=True):
            raise CustodyError('native-source-log-intent-changed')
        _native_log_files(directory, record, identity)
    with protected_directory(NATIVE_LOGS.parent) as parent:
        fixed = os.open(NATIVE_LOGS.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            if native_verify_log_directory(fixed, identity['id'], prefix=True) != record['directory']:
                raise CustodyError('native-source-log-intent-changed')
        finally:
            os.close(fixed)
    lib = _native_acl_library()
    if _native_acl_entries(directory, missing=True) == (_native_uuid(lib, identity['id']),):
        return
    _native_selected(selection)
    acl = ctypes.c_void_p(lib.acl_init(1))
    if not acl.value:
        raise CustodyError('native-source-acl-create-failed')
    try:
        entry = ctypes.c_void_p()
        qualifier = (ctypes.c_ubyte * 16).from_buffer_copy(_native_uuid(lib, identity['id']))
        if (lib.acl_create_entry(ctypes.byref(acl), ctypes.byref(entry)) != 0 or not entry.value
                or lib.acl_set_tag_type(entry, 1) != 0 or lib.acl_set_qualifier(entry, qualifier) != 0
                or lib.acl_set_permset_mask_np(entry, _NATIVE_LIST_SEARCH) != 0):
            raise CustodyError('native-source-acl-create-failed')
        _native_flags_zero(lib, acl)
        if lib.acl_valid(acl) != 0 or lib.acl_set_fd_np(directory, acl, _NATIVE_ACL_TYPE) != 0:
            raise CustodyError('native-source-acl-write-failed')
    finally:
        lib.acl_free(acl)
    native_verify_log_directory(directory, identity['id'])


def native_prepare_logs(selection):
    """Root-only, journal-first known files; publishes traverse ACL LAST.

    The Source program prints to inherited stdout/stderr; it receives no
    directory ADD/DELETE/WRITE_SECURITY permission and no rotation authority.
    This function neither publishes a plist nor starts any process.
    """
    _manifest, kit_sha = native_verify_kit(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_META) as metadata:
        identity, binding = _native_log_binding(selection, metadata, kit_sha)
        record, raw = _native_json_at(metadata, 'source-logs-prepare.json')
        if raw is None:
            if os.path.lexists(NATIVE_LOGS):
                raise CustodyError('native-source-logs-unmanaged')
            record = dict(binding, directory=None, files={})
            raw = _native_save_at(selection, metadata, 'source-logs-prepare.json', record)
        _native_log_record(record, binding)
        with protected_directory(NATIVE_LOGS.parent) as parent:
            if record['directory'] is None:
                _native_selected(selection)
                try:
                    os.mkdir(NATIVE_LOGS.name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    raise CustodyError('native-source-logs-unmanaged') from None
                os.fsync(parent)
                fd = os.open(NATIVE_LOGS.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    inode = native_verify_log_directory(fd, identity['id'], prefix=True)
                    if os.listdir(fd) or _native_acl_entries(fd, missing=True):
                        raise CustodyError('native-source-logs-unmanaged')
                    record['directory'] = inode
                    raw = _native_save_at(selection, metadata, 'source-logs-prepare.json', record, previous=raw)
                finally:
                    os.close(fd)
            directory = os.open(NATIVE_LOGS.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if native_verify_log_directory(directory, identity['id'], prefix=True) != record['directory']:
                    raise CustodyError('native-source-log-directory-changed')
                if set(os.listdir(directory)) - set(_native_log_names()):
                    raise CustodyError('native-source-logs-unmanaged')
                if _native_acl_entries(directory, missing=True):
                    _native_log_files(directory, record, identity)  # No opening an unfinished prefix.
                for name in _native_log_names():
                    current = record['files'].get(name)
                    if current is None:
                        _native_selected(selection)
                        try:
                            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600, dir_fd=directory)
                        except FileExistsError:
                            raise CustodyError('native-source-log-file-unmanaged') from None
                        try:
                            inode = _native_log_file(fd, identity, prefix=True)
                            if os.fstat(fd).st_size:
                                raise CustodyError('native-source-log-file-unmanaged')
                            record['files'][name] = {'inode': inode, 'ready': False}
                            os.fsync(fd)
                            os.fsync(directory)
                            raw = _native_save_at(selection, metadata, 'source-logs-prepare.json', record, previous=raw)
                        finally:
                            os.close(fd)
                        current = record['files'][name]
                    flags = os.O_RDONLY if current['ready'] else os.O_RDWR
                    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    try:
                        if _native_log_file(fd, identity, prefix=not current['ready']) != current['inode']:
                            raise CustodyError('native-source-log-file-changed')
                        if not current['ready']:
                            if os.fstat(fd).st_size:
                                raise CustodyError('native-source-log-file-unmanaged')
                            _native_selected(selection)
                            if (os.fstat(fd).st_uid, os.fstat(fd).st_gid) != (identity['id'], identity['id']):
                                os.fchown(fd, identity['id'], identity['id'])
                            os.fsync(fd)
                            current['ready'] = True
                            raw = _native_save_at(selection, metadata, 'source-logs-prepare.json', record, previous=raw)
                        _native_log_file(fd, identity)
                    finally:
                        os.close(fd)
                _native_log_files(directory, record, identity)
                _native_log_binding(selection, metadata, kit_sha)
                _native_publish_log_acl(selection, directory, identity)
                os.fsync(directory)
                native_verify_log_directory(directory, identity['id'])
                return {'directory': list(record['directory']),
                        'files': {name: list(value['inode']) for name, value in record['files'].items()}}
            finally:
                os.close(directory)


@dataclass(frozen=True)
class NativeChildOutcome:
    """Process mechanics only. An exit code is never a provider result."""
    disposition: str
    returncode: int | None
    reaped: bool
    known_children_gone: bool
    reason: str


def _native_clock_ns():
    """One Darwin boot-wide raw clock; never Python3.9's local monotonic origin."""
    import time
    if sys.platform != 'darwin' or not hasattr(time, 'CLOCK_MONOTONIC_RAW'):
        raise CustodyError('native-source-clock-unavailable')
    try:
        value = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    except (OSError, ValueError):
        raise CustodyError('native-source-clock-unavailable') from None
    if type(value) is not int or not 0 < value < 2**63:
        raise CustodyError('native-source-clock-unavailable')
    return value


@dataclass(frozen=True)
class _NativeInvocation:
    """Immutable mechanics only. Parsing is NOT Root/generation authority."""
    encoded: bytes
    seal: Any

    @property
    def document(self):
        return json.loads(self.encoded)


def _native_invocation(value):
    keys = {'role', 'identifier', 'nonce', 'boot', 'clock', 'generation', 'issuedNs',
        'admissionDeadlineNs', 'workerDeadlineNs'}
    if (type(value) is not dict or set(value) != keys or type(value['generation']) is not str
            or not FLOW_ID.fullmatch(value['generation']) or type(value['nonce']) is not str
            or not FLOW_ID.fullmatch(value['nonce']) or type(value['clock']) is not str
            or value['clock'] != 'darwin-clock-monotonic-raw-ns-v1'
            or type(value['boot']) is not str or not re.fullmatch(r'[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', value['boot'])):
        raise CustodyError('native-source-invocation-invalid')
    native_child_arguments(value['role'], value['identifier'])
    for name in ('issuedNs', 'admissionDeadlineNs', 'workerDeadlineNs'):
        item = value[name]
        if type(item) is not str or not re.fullmatch('[1-9][0-9]{0,18}', item) or int(item) >= 2**63:
            raise CustodyError('native-source-invocation-invalid')
    issued, admission, worker = (int(value[key]) for key in ('issuedNs', 'admissionDeadlineNs', 'workerDeadlineNs'))
    budget = NATIVE_CHILD_BUDGETS[value['role']] * 10**9
    if (not issued < admission or not issued < worker or worker - issued > budget
            or admission - issued > (30 * 10**9 if value['role'] == 'sync' else budget)
            or value['role'] != 'sync' and worker > admission):
        raise CustodyError('native-source-invocation-invalid')
    return _NativeInvocation(_native_encoded(value), _NATIVE_SEAL)


def _native_invocation_document(invocation):
    if (type(invocation) is not _NativeInvocation or invocation.seal is not _NATIVE_SEAL
            or type(invocation.encoded) is not bytes or len(invocation.encoded) > 1024):
        raise CustodyError('native-source-invocation-invalid')
    try:
        value = json.loads(invocation.encoded, object_pairs_hook=_native_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-invocation-invalid') from None
    if _native_invocation(value).encoded != invocation.encoded:
        raise CustodyError('native-source-invocation-invalid')
    return value


def _native_slot_matches(snapshot, invocation, *, running=False):
    """Private captured-view comparison, never a new approval/deployment reader.

    The future fixed Gate reader must obtain this view after Root admission.
    Sibling additions do not change the expected invocation's immutable bytes.
    """
    expected = _native_invocation_document(invocation)
    if (type(snapshot) is not dict or set(snapshot) != {'generation', 'boot', 'phase', 'invocations'}
            or type(snapshot['phase']) is not str or snapshot['phase'] != 'active'
            or type(snapshot['generation']) is not str or type(snapshot['boot']) is not str
            or snapshot['generation'] != expected['generation'] or snapshot['boot'] != expected['boot']
            or type(snapshot['invocations']) is not dict or not 1 <= len(snapshot['invocations']) <= 64):
        return False
    wanted = NATIVE_LABEL + expected['role'] + ('.' + expected['identifier'] if expected['identifier'] is not None else '')
    found = False
    for label, row in snapshot['invocations'].items():
        if (type(label) is not str or type(row) is not dict or set(row) != {'phase', 'invocation'}
                or type(row['phase']) is not str or row['phase'] not in ('prepared', 'running', 'quiescing', 'completed', 'unknown', 'retired')):
            return False
        try:
            current = _native_invocation(row['invocation'])
            doc = current.document
        except CustodyError:
            return False
        if (doc['generation'] != snapshot['generation'] or doc['boot'] != snapshot['boot']
                or label != NATIVE_LABEL + doc['role'] + ('.' + doc['identifier'] if doc['identifier'] is not None else '')):
            return False
        if label == wanted:
            found = current.encoded == invocation.encoded and row['phase'] in (('prepared', 'running') if running else ('prepared',))
    return found


def _native_pipe_binding(fd, *, writing):
    import fcntl
    if type(fd) is not int or fd < 3:
        raise CustodyError('native-source-barrier-pipe-invalid')
    info = os.fstat(fd)
    if not stat.S_ISFIFO(info.st_mode) or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != (os.O_WRONLY if writing else os.O_RDONLY):
        raise CustodyError('native-source-barrier-pipe-invalid')
    os.set_blocking(fd, False)
    return info.st_dev, info.st_ino, info.st_mode


def _native_pipe_close(fd, binding):
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_mode) == binding:
            os.close(fd)
    except OSError:
        pass


def _native_barrier_frame(invocation, kind):
    return _native_encoded({'version': 1, 'kind': kind, 'invocationSha256': hashlib.sha256(invocation.encoded).hexdigest()})


def _native_pipe_transfer(fd, binding, *, deadline, outgoing=None, cancel=None, _guard=None):
    import select
    data, offset = b'', 0
    if outgoing is not None and len(outgoing) > 256:
        raise CustodyError('native-source-barrier-frame-invalid')
    while _native_clock_ns() < deadline:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_mode) != binding:
            raise CustodyError('native-source-barrier-pipe-changed')
        if cancel is not None and cancel.is_set():
            raise CustodyError('native-source-barrier-cancelled')
        timeout = min(.02, max(0, deadline - _native_clock_ns()) / 10**9)
        read, write, _errors = select.select([fd] if outgoing is None else [], [fd] if outgoing is not None else [], [], timeout)
        # select and the fixed-view guard may yield/block. Revalidate AFTER
        # both, immediately before I/O; no late GO or reused-FD write.
        if outgoing is not None and write and _guard is not None and not _guard():
            raise CustodyError('native-source-barrier-scope-changed')
        if _native_clock_ns() >= deadline:
            raise CustodyError('native-source-barrier-admission-expired')
        if cancel is not None and cancel.is_set():
            raise CustodyError('native-source-barrier-cancelled')
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_mode) != binding:
            raise CustodyError('native-source-barrier-pipe-changed')
        if outgoing is not None and write:
            try:
                count = os.write(fd, outgoing[offset:])
            except BlockingIOError:
                continue
            if count <= 0:
                raise CustodyError('native-source-barrier-eof')
            offset += count
            if offset == len(outgoing):
                return None
        elif read:
            chunk = os.read(fd, 257 - len(data))
            if not chunk:
                raise CustodyError('native-source-barrier-eof')
            data += chunk
            if len(data) > 256:
                raise CustodyError('native-source-barrier-frame-invalid')
            if b'\n' in data:
                if not data.endswith(b'\n') or data.count(b'\n') != 1:
                    raise CustodyError('native-source-barrier-frame-invalid')
                return data
    raise CustodyError('native-source-barrier-admission-expired')


def _native_child_ready_barrier(invocation, *, ready_fd, go_fd, current):
    """Protected bootstrap primitive only; does NOT import/run Source.

    The future post-Seatbelt entrypoint must call before Source import. EOF,
    invalid frame or expired admission never returns permission to proceed.
    Only these anonymous pipe ends are consumed/closed, never unrelated FDs.
    """
    doc = _native_invocation_document(invocation)
    if not callable(current):
        raise CustodyError('native-source-barrier-scope-changed')
    ready = _native_pipe_binding(ready_fd, writing=True)
    go = None
    try:
        go = _native_pipe_binding(go_fd, writing=False)
        if ready[:2] == go[:2]:
            raise CustodyError('native-source-barrier-pipe-invalid')
        deadline = min(int(doc['admissionDeadlineNs']), int(doc['workerDeadlineNs']))
        if not _native_slot_matches(current(), invocation):
            raise CustodyError('native-source-barrier-scope-changed')
        _native_pipe_transfer(ready_fd, ready, deadline=deadline, outgoing=_native_barrier_frame(invocation, 'ready'),
            _guard=lambda: _native_slot_matches(current(), invocation))
        answer = _native_pipe_transfer(go_fd, go, deadline=deadline)
        # GO may race the same immutable slot's prepared-to-running record.
        # This does not admit startup from an already-running slot: READY and
        # the parent's GO checks above still require prepared.
        if (answer != _native_barrier_frame(invocation, 'go') or not _native_slot_matches(current(), invocation, running=True)
                or _native_clock_ns() >= deadline):
            raise CustodyError('native-source-barrier-frame-invalid')
    finally:
        _native_pipe_close(ready_fd, ready)
        if go is not None:
            _native_pipe_close(go_fd, go)


@dataclass(frozen=True)
class _NativeChildWitness:
    process: Any
    started: float
    root: tuple
    identities: tuple
    inspect: Any
    tree: Any
    seal: Any


def _native_observe_child(process, *, started, uid, gid, executable, inspect=None, tree=None):
    """Bind an OWNED Popen handle, not a PID supplied over a request.

    This does not launch a Source program or grant Root admission. The future
    authenticated dispatcher must capture started BEFORE Popen and qualify the
    sealed Root selection before dispatch. Alternate observers are private
    test seams; Linux tests use real proc metadata, not a claimed Darwin proof.
    """
    import math
    import time
    if (type(process) is not subprocess.Popen or type(started) not in (int, float)
            or not math.isfinite(started) or not 0 < started <= time.monotonic()
            or type(uid) is not int or uid < 0 or type(gid) is not int or gid < 0
            or type(executable) is not str or not executable.startswith('/')
            or type(process.pid) is not int or process.pid <= 0 or process.poll() is not None):
        raise CustodyError('native-source-child-unbound')
    if inspect is None or tree is None:
        if inspect is not None or tree is not None:
            raise CustodyError('native-source-child-observer-invalid')
        from pixel_macos_process import process_identity, process_tree_snapshot
        inspect = lambda pid: process_identity(pid, uid=uid, gid=gid, executable=executable, allow_root=uid == 0)
        tree = process_tree_snapshot
    if not callable(inspect) or not callable(tree):
        raise CustodyError('native-source-child-observer-invalid')
    try:
        root = inspect(process.pid)
        rows = tree(process.pid)
        if (type(root) is not tuple or len(root) != 10 or root[0] != process.pid
                or root[3:9] != (uid, gid, uid, gid, uid, gid) or root[9] != executable
                or type(rows) not in (list, tuple) or not 1 <= len(rows) <= 64
                or len({row[0] for row in rows}) != len(rows)
                or root[:3] not in rows):
            raise ValueError()
        identities = []
        for row in rows:
            if (type(row) is not tuple or len(row) != 3
                    or any(type(value) is not int or value < 0 for value in row) or row[0] <= 0):
                raise ValueError()
            identity = inspect(row[0])
            if (identity[:3] != row or identity[3:9] != (uid, gid, uid, gid, uid, gid)
                    or identity[9] != executable):
                raise ValueError()
            identities.append(identity)
        if inspect(process.pid) != root or process.poll() is not None:
            raise ValueError()
    except Exception:
        raise CustodyError('native-source-child-identity-unproven') from None
    return _NativeChildWitness(process, started, root, tuple(identities), inspect, tree, _NATIVE_SEAL)


def _native_supervise_child(witness, *, role, timeout, cancel=None, _invocation=None, _current=None):
    """Bounded wait/TERM/KILL/reap for observed, identity-matched children.

    No spawn, retry, Root Run binding or provider-result interpretation occurs
    here. known_children_gone covers the recorded tree, NOT an unobserved or
    escaped descendant. launchd/process-group and Seatbelt proof remains a
    physical gate before this primitive may confer completed actor cleanup.
    """
    import math
    import signal
    import threading
    import time
    if (type(witness) is not _NativeChildWitness or witness.seal is not _NATIVE_SEAL
            or type(role) is not str or role not in NATIVE_CHILD_BUDGETS
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= NATIVE_CHILD_BUDGETS[role]
            or cancel is not None and type(cancel) is not threading.Event):
        raise CustodyError('native-source-child-control-invalid')
    process = witness.process
    root = witness.root
    if process.pid != root[0]:
        return NativeChildOutcome('unknown', None, False, False, 'owned-child-handle-changed')
    known = {identity[0]: identity for identity in witness.identities}
    now, scale = time.monotonic, 1
    deadline = witness.started + timeout
    if _invocation is not None:
        doc = _native_invocation_document(_invocation)
        if doc['role'] != role or not callable(_current):
            raise CustodyError('native-source-child-control-invalid')
        now, scale = _native_clock_ns, 10**9
        deadline = int(doc['workerDeadlineNs'])
        timeout = (deadline - int(doc['issuedNs'])) / scale
    elif _current is not None:
        raise CustodyError('native-source-child-control-invalid')
    cleanup = min(5.0, timeout / 2) * scale
    work_end = deadline - cleanup
    uncertain = False
    reason = 'exited'
    def probe(pid):
        nonlocal uncertain
        if pid == root[0] and process.poll() is not None:
            return False
        try:
            current = witness.inspect(pid)
        except (ProcessLookupError, FileNotFoundError):
            return False
        except Exception as error:
            if getattr(error, 'errno', None) == errno.ESRCH:
                return False
            uncertain = True
            return None
        if current != known[pid]:
            uncertain = True
            # A replacement is not permission to signal the reused PID.
            return None
        return True
    def observe():
        nonlocal uncertain
        if process.pid != root[0]:
            uncertain = True
            return
        if process.poll() is not None:
            return
        try:
            rows = witness.tree(root[0])
            if (type(rows) not in (tuple, list) or not 1 <= len(rows) <= 64
                    or len({row[0] for row in rows}) != len(rows) or root[:3] not in rows):
                raise ValueError()
            for row in rows:
                if type(row) is not tuple or len(row) != 3:
                    raise ValueError()
                if row[0] in known:
                    if known[row[0]][:3] != row:
                        raise ValueError()
                    continue
                current = witness.inspect(row[0])
                if (type(current) is not tuple or len(current) != 10 or current[:3] != row
                        or current[3:] != root[3:] or len(known) >= 64):
                    raise ValueError()
                known[row[0]] = current
        except Exception:
            uncertain = True
    def live():
        return {pid for pid in known if probe(pid) is not False}
    def send(which):
        nonlocal uncertain
        # Descendants first; never kill a UID/name/process group wholesale.
        for pid in sorted(known, key=lambda value: value == root[0]):
            if now() >= deadline:
                return
            if probe(pid) is not True:
                continue
            try:
                os.kill(pid, which)
            except ProcessLookupError:
                pass
            except OSError:
                uncertain = True
    while now() < work_end:
        observe()
        if not live():
            break
        if uncertain:
            reason = 'identity-unproven'
            break
        if cancel is not None and cancel.is_set():
            reason = 'cancelled'
            break
        if _invocation is not None:
            try:
                current = _native_slot_matches(_current(), _invocation, running=True)
            except Exception:
                current = False
            if not current:
                uncertain, reason = True, 'scope-changed'
                break
        time.sleep(min(0.02, max(0, work_end - now()) / scale))
    else:
        reason = 'timed-out'
    if live():
        if reason == 'exited':
            reason = 'descendants-remained'
        term_end = min(deadline, now() + cleanup / 2)
        send(signal.SIGTERM)
        while now() < deadline and live():
            observe()
            if now() >= term_end:
                send(signal.SIGKILL)
            time.sleep(min(0.02, max(0, deadline - now()) / scale))
    # A process may already be a non-running zombie while its exit is not yet
    # returned by poll(). Reap only this owned handle, within the SAME deadline.
    if process.poll() is None and now() < deadline:
        try:
            process.wait(timeout=max(0, deadline - now()) / scale)
        except subprocess.TimeoutExpired:
            pass
    remaining_children = live()
    reaped = process.poll() is not None
    if uncertain or remaining_children or not reaped:
        return NativeChildOutcome('unknown', process.returncode, reaped, not remaining_children,
                                  'identity-or-cleanup-unproven')
    disposition = reason if reason in ('cancelled', 'timed-out') else 'exited'
    return NativeChildOutcome(disposition, process.returncode, True, True, reason)


@dataclass(frozen=True)
class NativeStartupOutcome:
    """Barrier mechanics, not Root admission or a Source effect receipt."""
    go_sent: bool
    reason: str
    witness: Any
    cleanup: NativeChildOutcome | None


def _native_release_child(process, *, started, invocation, ready_fd, go_fd, current,
                          uid, gid, executable, cancel=None, inspect=None, tree=None):
    """No spawn/Source import. Consume READY, observe stable OWNED Python, GO.

    Future Root wiring must supply a requalified fixed view, not owner/model
    data. A typed invocation/slot match alone is never that authority. Failed
    startup closes GO and cleans only this owned child with its original worker
    ceiling; a changed/unobservable identity remains unknown, never replayed.
    """
    import threading
    if type(process) is not subprocess.Popen or not callable(current) or cancel is not None and type(cancel) is not threading.Event:
        raise CustodyError('native-source-child-control-invalid')
    doc = _native_invocation_document(invocation)
    ready, go, witness = None, None, None
    failure = None
    try:
        ready = _native_pipe_binding(ready_fd, writing=False)
        go = _native_pipe_binding(go_fd, writing=True)
        if ready[:2] == go[:2]:
            raise CustodyError('native-source-barrier-pipe-invalid')
        deadline = min(int(doc['admissionDeadlineNs']), int(doc['workerDeadlineNs']))
        if _native_clock_ns() < int(doc['issuedNs']) or not _native_slot_matches(current(), invocation):
            raise CustodyError('native-source-barrier-scope-changed')
        frame = _native_pipe_transfer(ready_fd, ready, deadline=deadline, cancel=cancel)
        if frame != _native_barrier_frame(invocation, 'ready'):
            raise CustodyError('native-source-barrier-frame-invalid')
        witness = _native_observe_child(process, started=started, uid=uid, gid=gid,
            executable=executable, inspect=inspect, tree=tree)
        if (cancel is not None and cancel.is_set() or not _native_slot_matches(current(), invocation)
                or _native_clock_ns() >= deadline):
            raise CustodyError('native-source-barrier-scope-changed')
        # Scope callback and observation may block; the transfer rechecks the
        # absolute ceiling/cancellation and never starts a fresh timeout.
        _native_pipe_transfer(go_fd, go, deadline=deadline, outgoing=_native_barrier_frame(invocation, 'go'), cancel=cancel,
            _guard=lambda: _native_slot_matches(current(), invocation))
    except Exception as error:
        failure = error.code if isinstance(error, CustodyError) else 'native-source-barrier-io-failed'
    finally:
        if go is not None:
            _native_pipe_close(go_fd, go)
        if ready is not None:
            _native_pipe_close(ready_fd, ready)
    if failure is None:
        return NativeStartupOutcome(True, 'released', witness, None)
    if witness is None and process.poll() is None:
        try:
            witness = _native_observe_child(process, started=started, uid=uid, gid=gid,
                executable=executable, inspect=inspect, tree=tree)
        except CustodyError:
            pass
    if witness is not None:
        stopped = threading.Event()
        stopped.set()
        cleanup = _native_supervise_child(witness, role=doc['role'], timeout=NATIVE_CHILD_BUDGETS[doc['role']],
            cancel=stopped, _invocation=invocation, _current=current)
    else:
        remaining = max(0, int(doc['workerDeadlineNs']) - _native_clock_ns()) / 10**9
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass
        cleanup = NativeChildOutcome('unknown', process.returncode, process.poll() is not None, False, 'startup-identity-unproven')
    return NativeStartupOutcome(False, failure, witness, cleanup)


def _native_boot_uuid():
    """Fixed read-only Apple sysctl ABI, no subprocess/owner environment."""
    if sys.platform != 'darwin':
        raise CustodyError('native-source-boot-unavailable')
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    lib.sysctlbyname.restype = ctypes.c_int
    value, size = ctypes.create_string_buffer(37), ctypes.c_size_t(37)
    if (lib.sysctlbyname(b'kern.bootsessionuuid', value, ctypes.byref(size), None, 0) != 0
            or size.value != 37 or value.raw[36:] != b'\0'):
        raise CustodyError('native-source-boot-unavailable')
    try:
        result = str(uuid.UUID(value.raw[:36].decode('ascii'))).upper()
    except (ValueError, UnicodeError):
        raise CustodyError('native-source-boot-unavailable') from None
    return result


def _native_label_parts(label):
    if type(label) is not str or not label.startswith(NATIVE_LABEL):
        raise CustodyError('native-source-broker-definition-invalid')
    role, separator, identifier = label[len(NATIVE_LABEL):].partition('.')
    native_child_arguments(role, identifier if separator else None)
    return role, identifier if separator else None


def _native_public_json(body):
    try:
        return json.loads(body, object_pairs_hook=_native_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-broker-metadata-invalid') from None


def _native_broker_projection(value):
    """Only a FUTURE v3 Root projection; existing v1/v2 never confer dispatch."""
    keys = {'schemaVersion', 'kind', 'phase', 'deploymentSha256', 'kitSha256', 'ownerUid',
        'ownerGid', 'ownerGuid', 'broker', 'state', 'env', 'kit', 'hostPython', 'runtime', 'launch'}
    if type(value) is not dict or value.get('schemaVersion') != 3 or type(value.get('schemaVersion')) is not int:
        raise CustodyError('native-source-broker-inactive')
    if (set(value) != keys or value['kind'] != 'ods-native-source-custody' or value['phase'] != 'active'
            or type(value['ownerUid']) is not int or value['ownerUid'] <= 0
            or type(value['ownerGid']) is not int or value['ownerGid'] < 0
            or type(value['ownerGuid']) is not str or (value['state'], value['env'], value['kit']) != (
                str(NATIVE_STATE), str(NATIVE_ENV), str(NATIVE_KIT))):
        raise CustodyError('native-source-broker-projection-invalid')
    identity = _native_identity_intent(value['broker'])
    if value['ownerUid'] == identity['id']:
        raise CustodyError('native-source-broker-identity-invalid')
    try:
        if str(uuid.UUID(value['ownerGuid'])).upper() != value['ownerGuid']:
            raise ValueError()
    except ValueError:
        raise CustodyError('native-source-broker-projection-invalid') from None
    for key in ('deploymentSha256', 'kitSha256'):
        if type(value[key]) is not str or not _NATIVE_SHA.fullmatch(value[key]):
            raise CustodyError('native-source-broker-projection-invalid')
    runtime, launch = value['runtime'], value['launch']
    if (type(runtime) is not dict or set(runtime) != {'custodySha256', 'bundleSha256', 'sourceRef'}
            or any(type(runtime[key]) is not str or not _NATIVE_SHA.fullmatch(runtime[key]) for key in ('custodySha256', 'bundleSha256'))
            or type(runtime['sourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', runtime['sourceRef'])
            or type(launch) is not dict or set(launch) != {'generation', 'phase', 'boot', 'environment', 'definitions', 'invocations'}
            or type(launch['generation']) is not str or not FLOW_ID.fullmatch(launch['generation'])
            or launch['phase'] != 'active' or type(launch['boot']) is not str
            or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', launch['boot'])
            or type(launch['environment']) is not dict or set(launch['environment']) != {'inode', 'sha256'}
            or not _native_inode(launch['environment']['inode']) or type(launch['environment']['sha256']) is not str
            or not _NATIVE_SHA.fullmatch(launch['environment']['sha256'])
            or type(launch['invocations']) is not dict or not 1 <= len(launch['invocations']) <= 64
            or type(launch['definitions']) is not dict):
        raise CustodyError('native-source-broker-projection-invalid')
    snapshots = {}
    for label, row in launch['invocations'].items():
        role, identifier = _native_label_parts(label)
        if type(row) is not dict or set(row) != {'phase', 'invocation'} or type(row['phase']) is not str:
            raise CustodyError('native-source-broker-projection-invalid')
        if row['phase'] == 'inactive' and row['invocation'] is None and label not in launch['definitions']:
            continue  # A sibling's inactive/absent slot never authorizes I/O.
        invocation = _native_invocation(row['invocation'])
        doc = invocation.document
        if (row['phase'] not in ('prepared', 'running', 'quiescing', 'completed', 'unknown', 'retired')
                or (doc['role'], doc['identifier'], doc['generation'], doc['boot']) != (role, identifier, launch['generation'], launch['boot'])):
            raise CustodyError('native-source-broker-projection-invalid')
        definition = launch['definitions'].get(label)
        if (type(definition) is not dict or set(definition) != {'inode', 'sha256', 'profileSha256'}
                or not _native_inode(definition['inode']) or any(type(definition[key]) is not str
                    or not _NATIVE_SHA.fullmatch(definition[key]) for key in ('sha256', 'profileSha256'))):
            raise CustodyError('native-source-broker-definition-invalid')
        snapshots[label] = row
    if set(launch['definitions']) != set(snapshots):
        raise CustodyError('native-source-broker-definition-invalid')
    return {'generation': launch['generation'], 'boot': launch['boot'], 'phase': 'active', 'invocations': snapshots}


def _native_broker_binding(projection, label):
    """Common inputs and this slot only; sibling publication is independent."""
    launch = projection['launch']
    return hashlib.sha256(_native_encoded({
        **{key: projection[key] for key in projection if key != 'launch'},
        'generation': launch['generation'], 'boot': launch['boot'],
        'environment': launch['environment'], 'definition': launch['definitions'][label],
    })).hexdigest()


def _native_broker_runtime(projection, qualifier):
    """Actual public RuntimeCustody + complete public bundle; no Owner reader.

    Do NOT call require_native_deployment: it reopens owner-private sidefiles.
    The Root Gate attests that private owner/deployment association. Here we
    independently reopen only runtime/source/code metadata that Broker may read.
    """
    from types import SimpleNamespace
    from pixel_provider.runtime_custody import RuntimeCustody, tree_manifest
    from pixel_macos_custody import protected_directory
    if (type(qualifier) is not RuntimeCustody or type(qualifier.bridge) is not SimpleNamespace
            or vars(qualifier.bridge) != {'binary': str(NATIVE_PROGRAM / 'openclaw-gateway-launcher')}):
        raise CustodyError('native-source-broker-runtime-changed')
    runtime, checksum = RuntimeCustody.inspect(qualifier)
    root = Path(runtime['node']).parent
    if (root.parent != NATIVE_RUNTIMES or root.name != projection['runtime']['bundleSha256']
            or runtime['runtimeRoot'] != str(root / 'runtime') or runtime['sourceRoot'] != str(root / 'ods-source')
            or runtime['hostPython'] != projection['hostPython'] or checksum != projection['runtime']['custodySha256']):
        raise CustodyError('native-source-broker-runtime-changed')
    with protected_directory(root) as directory:
        body, _inode = _native_root_file(directory, 'bundle.json', mode=0o644, limit=32*1024*1024)
        bundle = _native_public_json(body)
        if (hashlib.sha256(body).hexdigest() != root.name or type(bundle) is not dict
                or set(bundle) != {'schemaVersion', 'openclawVersion', 'plugins', 'entries'}
                or bundle['schemaVersion'] != 1 or type(bundle['entries']) is not dict):
            raise CustodyError('native-source-broker-bundle-changed')
        # Same acquisition only. The existing cache still checks every entry,
        # inode/ctime/mode and (on Darwin) its current ACL before reusing a hash.
        actual = tree_manifest(root, qualifier._cache)
        actual.pop('bundle.json', None)
        if actual != bundle['entries']:
            raise CustodyError('native-source-broker-bundle-changed')
        selection, _inode = _native_root_file(directory, 'ods-release-selection.json', mode=0o644, limit=2*1024*1024)
        selected = _native_public_json(selection)
        if type(selected) is not dict or selected.get('pixelSourceRevision') != projection['runtime']['sourceRef']:
            raise CustodyError('native-source-broker-source-changed')
    # The fixed installed program must be the actual qualified Source copy,
    # not a same-named owner module. No dynamic helper execution/sys.path edit.
    relative = 'bin/pixel_connections/custody.py'
    expected = actual.get('ods-source/' + relative)
    with protected_directory(NATIVE_PROGRAM / 'pixel_connections') as directory:
        installed, _inode = _native_root_file(directory, 'custody.py', mode=0o644, limit=2*1024*1024)
    if (Path(__file__) != NATIVE_PROGRAM / 'pixel_connections/custody.py'
            or expected != ['file', 0o644, len(installed), hashlib.sha256(installed).hexdigest()]):
        raise CustodyError('native-source-broker-bootstrap-changed')
    # The independently reviewed table lives in the qualified code closure.
    # Read only its literal data; never execute installer/Git/Node in Broker.
    import ast
    helper_path = root / 'ods-source/installers/macos/lib'
    with protected_directory(helper_path) as directory:
        code, _inode = _native_root_file(directory, 'pixel-native-source-broker.py', mode=0o644, limit=2*1024*1024)
    try:
        table = next(ast.literal_eval(node.value) for node in ast.parse(code).body
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == 'SOURCE_AUTHORITIES')
        authority = table[projection['runtime']['sourceRef']]
    except (ValueError, SyntaxError, TypeError, KeyError, StopIteration):
        raise CustodyError('native-source-broker-source-unqualified') from None
    with protected_directory(root / 'native-source') as directory:
        component, _inode = _native_root_file(directory, 'component.json', mode=0o644, limit=2*1024*1024)
    component = _native_public_json(component)
    if (type(component) is not dict or component.get('sourceRef') != projection['runtime']['sourceRef']
            or component.get('inputs') != authority['inputs'] or component.get('hostPython') != runtime['hostPython']
            or actual.get('native-source/broker.py', [None])[-1] != authority['inputs']['deploy/source-broker/broker.py']
            or actual.get('native-source/source-broker.env', [None])[-1] != authority['templateSha256']):
        raise CustodyError('native-source-broker-source-unqualified')
    return runtime, root, actual


def _native_broker_credentials(projection):
    identity = projection['broker']
    wanted = identity['id']
    if ((os.getuid(), os.geteuid(), os.getgid(), os.getegid()) != (wanted,)*4
            or any(group != wanted for group in os.getgroups())):
        raise CustodyError('native-source-broker-credentials-invalid')
    from pixel_macos_process import process_identity
    process_identity(os.getpid(), uid=wanted, gid=wanted, executable=projection['hostPython'])


def _native_broker_platform():
    if sys.platform != 'darwin':
        raise CustodyError('native-source-broker-platform-required')


from contextlib import contextmanager as _native_contextmanager


@_native_contextmanager
def _native_public_directory(path):
    """Standalone pre-import walk; no unqualified application import needed."""
    text = os.fspath(path)
    if (type(text) is not str or not text.startswith('/') or text == '/'
            or any(ord(char) < 32 for char in text)
            or any(part in ('', '.', '..') for part in text.split('/')[1:])):
        raise CustodyError('native-source-bootstrap-path-invalid')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in ('', *text.split('/')[1:]):
            if part:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise CustodyError('native-source-bootstrap-parent-unsafe')
            _native_no_acl(fd)
        yield fd
    finally:
        os.close(fd)


def _native_broker_application_imports(role, identifier=None):
    """Gate-bound public code proof BEFORE importing any application peer.

    Only used by the isolated CLI. This bootstrap does not replace the complete
    real Broker reader before Popen/READY/GO, or read a Root/owner private file.
    """
    _native_broker_platform()
    native_child_arguments(role, identifier)
    entry = NATIVE_PROGRAM / 'pixel_connections/custody.py'
    if (Path(__file__) != entry or not sys.flags.isolated or not sys.flags.no_site
            or not sys.dont_write_bytecode):
        raise CustodyError('native-source-bootstrap-isolation-required')
    with _native_public_directory(NATIVE_STATE.parent) as parent:
        gate = os.open(NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(gate)
            if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)):
                raise CustodyError('native-source-gate-unsafe')
            raw, _inode = _native_root_file(gate, 'custody.json', gid=os.getegid(), mode=0o640, limit=16384)
            projection = _native_public_json(raw)
            snapshot = _native_broker_projection(projection)
            identity = projection['broker']
            if (_native_acl_entries(gate) != (uuid.UUID(projection['ownerGuid']).bytes, uuid.UUID(identity['userGuid']).bytes)
                    or (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) != (identity['id'],)*4
                    or any(group != identity['id'] for group in os.getgroups())
                    or _native_boot_uuid() != snapshot['boot']):
                raise CustodyError('native-source-bootstrap-identity-changed')
            label = NATIVE_LABEL + role + ('.' + identifier if identifier is not None else '')
            row = snapshot['invocations'].get(label)
            if row is None or not _native_slot_matches(snapshot, _native_invocation(row['invocation'])):
                raise CustodyError('native-source-broker-slot-inactive')
            deadline = int(row['invocation']['admissionDeadlineNs'])
            if not int(row['invocation']['issuedNs']) <= _native_clock_ns() < deadline:
                raise CustodyError('native-source-broker-invocation-expired')
            with _native_public_directory(NATIVE_RUNTIME_DESCRIPTOR.parent) as directory:
                desc_body, _inode = _native_root_file(directory, NATIVE_RUNTIME_DESCRIPTOR.name, mode=0o644, limit=16384)
                manifest_body, _inode = _native_root_file(directory, NATIVE_RUNTIME_MANIFEST.name, mode=0o644, limit=32*1024*1024)
            descriptor, manifest = _native_public_json(desc_body), _native_public_json(manifest_body)
            keys = {'schemaVersion', 'runtimeRoot', 'sourceRoot', 'node', 'launcher', 'hostPython', 'manifest', 'manifestSha256', 'policy'}
            root = NATIVE_RUNTIMES / projection['runtime']['bundleSha256']
            source = root / 'ods-source'
            if (type(descriptor) is not dict or set(descriptor) != keys or descriptor['schemaVersion'] != 1
                    or type(descriptor['schemaVersion']) is not int or type(manifest) is not dict
                    or descriptor['runtimeRoot'] != str(root / 'runtime') or descriptor['sourceRoot'] != str(source)
                    or descriptor['node'] != str(root / 'node') or descriptor['launcher'] != str(NATIVE_PROGRAM / 'openclaw-gateway-launcher')
                    or descriptor['hostPython'] != projection['hostPython'] or descriptor['hostPython'] != sys.executable
                    or descriptor['manifest'] != str(NATIVE_RUNTIME_MANIFEST)
                    or descriptor['manifestSha256'] != hashlib.sha256(manifest_body).hexdigest()
                    or hashlib.sha256(json.dumps([descriptor, manifest], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                        != projection['runtime']['custodySha256']):
                raise CustodyError('native-source-bootstrap-runtime-changed')
            with _native_public_directory(root) as directory:
                bundle_body, _inode = _native_root_file(directory, 'bundle.json', mode=0o644, limit=32*1024*1024)
            bundle = _native_public_json(bundle_body)
            entries = manifest.get('source')
            if (hashlib.sha256(bundle_body).hexdigest() != root.name or type(bundle) is not dict
                    or set(bundle) != {'schemaVersion', 'openclawVersion', 'plugins', 'entries'}
                    or bundle['schemaVersion'] != 1 or type(bundle['entries']) is not dict or type(entries) is not dict
                    or entries != {name[len('ods-source/'):]: value for name, value in bundle['entries'].items() if name.startswith('ods-source/')}):
                raise CustodyError('native-source-bootstrap-code-changed')
            for relative, record in entries.items():
                if (type(relative) is not str or relative.startswith('/')
                        or any(part in ('', '.', '..') for part in relative.split('/'))
                        or type(record) is not list or not record or record[0] not in ('file', 'directory')):
                    raise CustodyError('native-source-bootstrap-code-changed')
            # No app code executes to learn the already reviewed literal list.
            helper_relative = 'installers/macos/lib/pixel-native-source-broker.py'
            with _native_public_directory(source / Path(helper_relative).parent) as directory:
                helper_body, _inode = _native_root_file(directory, Path(helper_relative).name, mode=0o644, limit=2*1024*1024)
            if entries.get(helper_relative) != ['file', 0o644, len(helper_body), hashlib.sha256(helper_body).hexdigest()]:
                raise CustodyError('native-source-bootstrap-code-changed')
            import ast
            try:
                files = next(ast.literal_eval(node.value) for node in ast.parse(helper_body).body
                    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == 'ROOT_SOURCE_FILES')
            except (SyntaxError, ValueError, TypeError, StopIteration):
                raise CustodyError('native-source-bootstrap-code-changed') from None
            if type(files) is not dict or len(files) != 107 or set(files) != {name for name, value in entries.items() if value[0] == 'file'}:
                raise CustodyError('native-source-bootstrap-code-changed')
            def namespace(directory, prefix=''):
                children = {name[len(prefix):].split('/')[0] for name in entries if name.startswith(prefix)}
                if set(os.listdir(directory)) != children:
                    raise CustodyError('native-source-bootstrap-code-changed')
                for name in children:
                    relative = prefix + name
                    record = entries[relative]
                    if record[0] != 'directory':
                        continue
                    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        info = os.fstat(fd)
                        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                                or record != ['directory', stat.S_IMODE(info.st_mode)]
                                or _native_at_inode(directory, name) != [info.st_dev, info.st_ino]):
                            raise CustodyError('native-source-bootstrap-code-changed')
                        _native_no_acl(fd)
                        namespace(fd, relative + '/')
                    finally:
                        os.close(fd)
            with _native_public_directory(source) as directory:
                source_info = os.fstat(directory)
                source_inode = [source_info.st_dev, source_info.st_ino]
                namespace(directory)
            admitted = {}
            for relative in files:
                if (type(relative) is not str or relative.startswith('/') or any(part in ('', '.', '..') for part in relative.split('/'))):
                    raise CustodyError('native-source-bootstrap-code-changed')
                expected = entries[relative]
                mode = 0o755 if relative == 'bin/ods-pixel-route-lease' else 0o644
                with _native_public_directory(source / Path(relative).parent) as directory:
                    body, _inode = _native_root_file(directory, Path(relative).name, mode=mode, limit=2*1024*1024)
                if expected != ['file', mode, len(body), hashlib.sha256(body).hexdigest()]:
                    raise CustodyError('native-source-bootstrap-code-changed')
                admitted[str(source / relative)] = expected
            with _native_public_directory(entry.parent) as directory:
                executing, _inode = _native_root_file(directory, entry.name, mode=0o644, limit=2*1024*1024)
            if admitted.get(str(source / 'bin/pixel_connections/custody.py')) != ['file', 0o644, len(executing), hashlib.sha256(executing).hexdigest()]:
                raise CustodyError('native-source-bootstrap-code-changed')
            binary = str(source / 'bin')
            # Every already-loaded app alias must be the executing module or
            # the same qualified closure. Never replace an owner-loaded peer.
            for name, module in tuple(sys.modules.items()):
                if name.startswith(('pixel_', 'access_mode_', 'scheduled_tasks_', 'settings_transaction', 'provider_transaction', 'model_transaction')):
                    filename = getattr(module, '__file__', None)
                    if filename not in admitted and not (name == 'pixel_connections.custody' and module is sys.modules[__name__]):
                        raise CustodyError('native-source-bootstrap-peer-alias')
            final = _native_public_json(_native_root_file(gate, 'custody.json', gid=identity['id'], mode=0o640, limit=16384)[0])
            final_snapshot = _native_broker_projection(final)
            with _native_public_directory(root) as directory:
                if _native_at_inode(directory, 'ods-source') != source_inode:
                    raise CustodyError('native-source-bootstrap-code-changed')
            with _native_public_directory(NATIVE_RUNTIME_DESCRIPTOR.parent) as directory:
                if (_native_root_file(directory, NATIVE_RUNTIME_DESCRIPTOR.name, mode=0o644, limit=16384)[0] != desc_body
                        or _native_root_file(directory, NATIVE_RUNTIME_MANIFEST.name, mode=0o644, limit=32*1024*1024)[0] != manifest_body):
                    raise CustodyError('native-source-bootstrap-runtime-changed')
            if (_native_at_inode(parent, NATIVE_STATE.name) != [info.st_dev, info.st_ino]
                    or _native_broker_binding(projection, label) != _native_broker_binding(final, label)
                    or not _native_slot_matches(final_snapshot, _native_invocation(row['invocation']))
                    or _native_boot_uuid() != snapshot['boot'] or _native_clock_ns() >= deadline):
                raise CustodyError('native-source-bootstrap-scope-changed')
            sys.path[:] = [binary, *(path for path in sys.path if path != binary)]
            import importlib
            package = importlib.import_module('pixel_connections')
            old = sys.modules.get('pixel_connections.custody')
            if old is not None and old is not sys.modules[__name__]:
                raise CustodyError('native-source-bootstrap-peer-alias')
            sys.modules['pixel_connections.custody'] = sys.modules[__name__]
            package.custody = sys.modules[__name__]
            return binary
        finally:
            os.close(gate)


@dataclass(frozen=True, repr=False)
class _NativeBrokerView:
    """A held private env FD; neither repr nor snapshot contains env contents."""
    public: bytes
    invocation: _NativeInvocation
    binding: str
    python: str
    environment_fd: int
    environment_stamp: tuple
    broker_guid: bytes
    broker_sha256: str
    seal: Any


@dataclass(frozen=True, repr=False)
class _NativeBrokerScope:
    role: str
    identifier: str | None
    qualifier: Any
    seal: Any


def _native_broker_scope(role, identifier):
    native_child_arguments(role, identifier)
    from types import SimpleNamespace
    from pixel_provider.runtime_custody import RuntimeCustody
    return _NativeBrokerScope(role, identifier, RuntimeCustody(SimpleNamespace(
        binary=str(NATIVE_PROGRAM / 'openclaw-gateway-launcher'))), _NATIVE_SEAL)


@_native_contextmanager
def _native_broker_read(role, identifier=None, *, running=False, _scope=None):
    """Fixed Broker reader only. No caller dictionary/path conveys authority."""
    _native_broker_platform()
    if type(running) is not bool:
        raise CustodyError('native-source-broker-platform-required')
    native_child_arguments(role, identifier)
    if _scope is None:
        _scope = _native_broker_scope(role, identifier)
    if (type(_scope) is not _NativeBrokerScope or _scope.seal is not _NATIVE_SEAL
            or (_scope.role, _scope.identifier) != (role, identifier)):
        raise CustodyError('native-source-broker-scope-invalid')
    label = NATIVE_LABEL + role + ('.' + identifier if identifier is not None else '')
    from pixel_macos_custody import protected_directory
    environment_fd, environment_identity, view = None, None, None
    with protected_directory(NATIVE_STATE.parent) as parent:
        gate = os.open(NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(gate)
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
                raise CustodyError('native-source-gate-unsafe')
            raw, _gate_inode = _native_root_file(gate, 'custody.json', gid=os.getegid(), mode=0o640, limit=16384)
            projection = _native_public_json(raw)
            snapshot = _native_broker_projection(projection)
            identity = projection['broker']
            if (_native_acl_entries(gate) != (uuid.UUID(projection['ownerGuid']).bytes, uuid.UUID(identity['userGuid']).bytes)
                    or _native_at_inode(parent, NATIVE_STATE.name) != [info.st_dev, info.st_ino]):
                raise CustodyError('native-source-gate-acl-mismatch')
            _native_broker_credentials(projection)
            if _native_boot_uuid() != snapshot['boot']:
                raise CustodyError('native-source-broker-boot-changed')
            row = snapshot['invocations'].get(label)
            if row is None:
                raise CustodyError('native-source-broker-slot-inactive')
            invocation = _native_invocation(row['invocation'])
            if not _native_slot_matches(snapshot, invocation, running=running):
                raise CustodyError('native-source-broker-slot-inactive')
            times = invocation.document
            ceiling = int(times['workerDeadlineNs'] if running else times['admissionDeadlineNs'])
            if not int(times['issuedNs']) <= _native_clock_ns() < ceiling:
                raise CustodyError('native-source-broker-invocation-expired')
            binding = _native_broker_binding(projection, label)
            from pixel_access_bridge import AccessError
            try:
                runtime, root, entries = _native_broker_runtime(projection, _scope.qualifier)
            except AccessError:
                raise CustodyError('native-source-broker-runtime-changed') from None
            with protected_directory(NATIVE_KIT) as kit:
                kit_info = os.fstat(kit)
                kit_inode = [kit_info.st_dev, kit_info.st_ino]
                kit_raw, _inode = _native_root_file(kit, 'source-kit.json', mode=0o644)
                kit_doc = _native_public_json(kit_raw)
                if (hashlib.sha256(kit_raw).hexdigest() != projection['kitSha256'] or type(kit_doc) is not dict
                        or set(kit_doc) != {'schemaVersion', 'kind', 'deploymentSha256', 'sourceRef', 'files'}
                        or kit_doc['schemaVersion'] != 2 or kit_doc['kind'] != 'ods-native-source-kit'
                        or kit_doc['deploymentSha256'] != projection['deploymentSha256']
                        or kit_doc['sourceRef'] != projection['runtime']['sourceRef']
                        or set(os.listdir(kit)) != {'source-kit.json', 'broker.py', 'source-broker.env', *NATIVE_PROFILES}
                        or type(kit_doc['files']) is not dict or set(kit_doc['files']) != {'broker.py', 'source-broker.env', *NATIVE_PROFILES}):
                    raise CustodyError('native-source-broker-kit-changed')
                payloads = {}
                for name in kit_doc['files']:
                    mode = 0o755 if name == 'broker.py' else 0o644
                    data, _inode = _native_root_file(kit, name, mode=mode, limit=2*1024*1024)
                    if (hashlib.sha256(data).hexdigest() != kit_doc['files'][name]
                            or entries.get('native-source/' + name) != ['file', mode, len(data), kit_doc['files'][name]]):
                        raise CustodyError('native-source-broker-kit-changed')
                    payloads[name] = data
            definition = projection['launch']['definitions'][label]
            if kit_doc['files']['source-' + role + '.sb'] != definition['profileSha256']:
                raise CustodyError('native-source-broker-profile-changed')
            with protected_directory(NATIVE_DEFINITIONS) as definitions:
                data, inode = _native_root_file(definitions, label + '.plist', mode=0o644, limit=16384)
            _path, expected, _mode = native_disabled_definition(role, identity=identity, python=runtime['hostPython'], identifier=identifier)
            expected_doc = plistlib.loads(expected)
            expected_doc['Disabled'] = False
            if (inode != definition['inode'] or hashlib.sha256(data).hexdigest() != definition['sha256']
                    or data != plistlib.dumps(expected_doc, sort_keys=True)):
                raise CustodyError('native-source-broker-definition-changed')
            environment_fd = os.open(NATIVE_ENV.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=gate)
            environment_info = os.fstat(environment_fd)
            environment_identity = (environment_info.st_dev, environment_info.st_ino, environment_info.st_mode)
            stamp = _native_env_fd_guid(environment_fd, uuid.UUID(identity['userGuid']).bytes)
            body = os.pread(environment_fd, MAX_RESULT_BYTES + 1, 0)
            env = projection['launch']['environment']
            if (list(stamp[:2]) != env['inode'] or len(body) != stamp[6]
                    or hashlib.sha256(body).hexdigest() != env['sha256']
                    or _native_env_fd_guid(environment_fd, uuid.UUID(identity['userGuid']).bytes) != stamp
                    or _native_at_inode(gate, NATIVE_ENV.name) != env['inode']):
                raise CustodyError('native-source-broker-env-changed')
            values, template = dict(parse_env(body.decode('utf-8'))), dict(parse_env(payloads['source-broker.env'].decode('utf-8')))
            mutable = {*PREAUTH_FLAGS, 'PIXEL_SOURCE_CALENDAR_ID', 'PIXEL_SOURCE_TIME_ZONE', 'PIXEL_SOURCE_GMAIL_QUERY', 'PIXEL_SOURCE_GMAIL_SENT_QUERY'}
            if (list(values) != list(template) or any(values.get(key) != value for key, value in _native_environment_paths().items())
                    or any(values[key] != value for key, value in template.items() if key not in mutable and key not in _native_environment_paths())
                    or any(values[key] not in ('0', '1') for key in PREAUTH_FLAGS if key in values)):
                raise CustodyError('native-source-broker-env-changed')
            final_raw, _final_inode = _native_root_file(gate, 'custody.json', gid=identity['id'], mode=0o640, limit=16384)
            final_projection = _native_public_json(final_raw)
            final_snapshot = _native_broker_projection(final_projection)
            if (not _native_slot_matches(final_snapshot, invocation, running=running)
                    or _native_broker_binding(final_projection, label) != binding
                    or _native_at_inode(parent, NATIVE_STATE.name) != [info.st_dev, info.st_ino]
                    or _native_at_inode(gate, NATIVE_ENV.name) != env['inode']
                    or _native_env_fd_guid(environment_fd, uuid.UUID(identity['userGuid']).bytes) != stamp):
                raise CustodyError('native-source-broker-gate-changed')
            with protected_directory(NATIVE_KIT.parent) as kit_parent:
                if _native_at_inode(kit_parent, NATIVE_KIT.name) != kit_inode:
                    raise CustodyError('native-source-broker-kit-changed')
            with protected_directory(NATIVE_DEFINITIONS) as definitions:
                if _native_root_file(definitions, label + '.plist', mode=0o644, limit=16384) != (data, inode):
                    raise CustodyError('native-source-broker-definition-changed')
            if _native_boot_uuid() != snapshot['boot'] or _native_clock_ns() >= ceiling:
                raise CustodyError('native-source-broker-invocation-expired')
            # The final Root snapshot may add siblings during this acquisition.
            # Only unchanged common inputs, own definition/invocation and the
            # original deadline survive; no new nonce or authority is adopted.
            view = _NativeBrokerView(_native_encoded(final_snapshot), invocation, binding, runtime['hostPython'], environment_fd, stamp,
                uuid.UUID(identity['userGuid']).bytes, hashlib.sha256(payloads['broker.py']).hexdigest(), _NATIVE_SEAL)
            yield view
        finally:
            if environment_fd is not None:
                try:
                    current_fd = os.fstat(environment_fd)
                    if (current_fd.st_dev, current_fd.st_ino, current_fd.st_mode) == environment_identity:
                        os.close(environment_fd)
                except OSError:
                    pass
                if view is not None:
                    object.__setattr__(view, 'environment_fd', -1)
            os.close(gate)


def _native_broker_sandbox_prefix(view):
    if type(view) is not _NativeBrokerView or view.seal is not _NATIVE_SEAL or view.environment_fd < 0:
        raise CustodyError('native-source-broker-view-required')
    doc = view.invocation.document
    label = NATIVE_LABEL + doc['role'] + ('.' + doc['identifier'] if doc['identifier'] is not None else '')
    # Never accepts a caller-selected SBPL parameter/path. Root definition
    # role/ID was already re-rendered and compared by the fixed reader.
    return ('/usr/bin/sandbox-exec', '-D', 'ODS_SOURCE_JOB_PLIST=' + str(NATIVE_DEFINITIONS / (label + '.plist')),
        '-f', str(NATIVE_KIT / ('source-' + doc['role'] + '.sb')), view.python, '-I', '-S', '-B')


def _native_post_seatbelt_bootstrap(role, identifier, *, ready_fd, go_fd):
    """Inert protected bootstrap: validate, READY/GO, revalidate; no Source import."""
    ready = _native_pipe_binding(ready_fd, writing=True)
    go = None
    try:
        go = _native_pipe_binding(go_fd, writing=False)
        scope = _native_broker_scope(role, identifier)
        with _native_broker_read(role, identifier, _scope=scope) as original:
            def current():
                with _native_broker_read(role, identifier, running=True, _scope=scope) as other:
                    if original.binding != other.binding or original.invocation.encoded != other.invocation.encoded:
                        raise CustodyError('native-source-broker-scope-changed')
                    return _native_public_json(other.public)
            _native_child_ready_barrier(original.invocation, ready_fd=ready_fd, go_fd=go_fd, current=current)
            current()  # Same closed reader after GO, before any future Source import.
            if _native_clock_ns() >= int(original.invocation.document['workerDeadlineNs']):
                raise CustodyError('native-source-broker-invocation-expired')
            return {'kind': 'ods-native-source-bootstrap-inert', 'label': NATIVE_LABEL + role + ('.' + identifier if identifier is not None else ''),
                'generation': original.invocation.document['generation']}
    finally:
        _native_pipe_close(ready_fd, ready)
        if go is not None:
            _native_pipe_close(go_fd, go)


def _native_consumer_environment():
    return {'HOME': NATIVE_HOME, 'USER': NATIVE_BROKER, 'LOGNAME': NATIVE_BROKER,
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}


def _native_post_seatbelt_source(role, identifier, *, ready_fd, go_fd):
    """Source's existing CLI begins ONLY after the authenticated READY/GO."""
    ready = _native_pipe_binding(ready_fd, writing=True)
    go = None
    try:
        go = _native_pipe_binding(go_fd, writing=False)
        scope = _native_broker_scope(role, identifier)
        with _native_broker_read(role, identifier, _scope=scope) as original:
            def current():
                with _native_broker_read(role, identifier, running=True, _scope=scope) as other:
                    if original.binding != other.binding or original.invocation.encoded != other.invocation.encoded:
                        raise CustodyError('native-source-broker-scope-changed')
                    return _native_public_json(other.public)
            _native_child_ready_barrier(original.invocation, ready_fd=ready_fd, go_fd=go_fd, current=current)
            with _native_broker_read(role, identifier, running=True, _scope=scope) as admitted:
                if original.binding != admitted.binding or original.invocation.encoded != admitted.invocation.encoded:
                    raise CustodyError('native-source-broker-scope-changed')
                stamp = _native_env_fd_guid(admitted.environment_fd, admitted.broker_guid)
                env_body = os.pread(admitted.environment_fd, MAX_RESULT_BYTES + 1, 0)
                if (stamp != admitted.environment_stamp or len(env_body) != stamp[6]
                        or _native_env_fd_guid(admitted.environment_fd, admitted.broker_guid) != stamp):
                    raise CustodyError('native-source-broker-env-changed')
                from pixel_macos_custody import protected_directory
                with protected_directory(NATIVE_KIT) as directory:
                    source_body, _inode = _native_root_file(directory, 'broker.py', mode=0o755, limit=2*1024*1024)
                if hashlib.sha256(source_body).hexdigest() != admitted.broker_sha256:
                    raise CustodyError('native-source-broker-source-changed')
                # Compile before the last deadline check. No Source code or
                # Source import has run before authenticated GO.
                code = compile(source_body, str(NATIVE_KIT / 'broker.py'), 'exec')
                values = dict(parse_env(env_body.decode('utf-8')))
                if any('\0' in key + value for key, value in values.items()):
                    raise CustodyError('native-source-broker-env-changed')
                current()
                doc = admitted.invocation.document
                if _native_boot_uuid() != doc['boot'] or _native_clock_ns() >= int(doc['workerDeadlineNs']):
                    raise CustodyError('native-source-broker-invocation-expired')
                os.environ.clear()
                os.environ.update(_native_consumer_environment())
                os.environ.update(values)
                sys.argv[:] = [str(NATIVE_KIT / 'broker.py'), *native_child_arguments(role, identifier)]
                try:
                    exec(code, {'__name__': '__main__', '__file__': str(NATIVE_KIT / 'broker.py'), '__package__': None})
                except SystemExit as exit:
                    return exit.code if type(exit.code) is int and 0 <= exit.code <= 255 else (0 if exit.code is None else 1)
                return 0
    finally:
        _native_pipe_close(ready_fd, ready)
        if go is not None:
            _native_pipe_close(go_fd, go)


def _native_consume_source(role, identifier=None, *, cancel=None):
    """Broker wrapper owns the Popen; never borrow launchd's numeric PID."""
    import time
    import threading
    if cancel is None:
        cancel = threading.Event()
    if type(cancel) is not threading.Event:
        raise CustodyError('native-source-child-control-invalid')
    scope = _native_broker_scope(role, identifier)
    with _native_broker_read(role, identifier, _scope=scope) as original:
        def current():
            with _native_broker_read(role, identifier, running=True, _scope=scope) as other:
                if original.binding != other.binding or original.invocation.encoded != other.invocation.encoded:
                    raise CustodyError('native-source-broker-scope-changed')
                return _native_public_json(other.public)
        ready_r, ready_w = os.pipe()
        go_r, go_w = os.pipe()
        pipe_bindings = {fd: _native_pipe_binding(fd, writing=writing)
            for fd, writing in ((ready_r, False), (ready_w, True), (go_r, False), (go_w, True))}
        started = time.monotonic()
        try:
            args = [*_native_broker_sandbox_prefix(original), str(NATIVE_PROGRAM / 'pixel_connections/custody.py'),
                '--native-broker-post-seatbelt', role, identifier if identifier is not None else '-', str(ready_w), str(go_r)]
            doc = original.invocation.document
            if (cancel.is_set() or _native_boot_uuid() != doc['boot']
                    or _native_clock_ns() >= int(doc['admissionDeadlineNs'])):
                raise CustodyError('native-source-broker-invocation-expired')
            process = subprocess.Popen(args, shell=False, close_fds=True, pass_fds=(ready_w, go_r),
                stdin=subprocess.DEVNULL, cwd=str(NATIVE_STATE), env=_native_consumer_environment())
            os.close(ready_w)
            ready_w = None
            os.close(go_r)
            go_r = None
            startup = _native_release_child(process, started=started, invocation=original.invocation,
                ready_fd=ready_r, go_fd=go_w, current=current,
                uid=os.geteuid(), gid=os.getegid(), executable=original.python, cancel=cancel)
            ready_r = go_w = None  # Existing barrier owns/closes these handles.
            if not startup.go_sent:
                return 2
            result = _native_supervise_child(startup.witness, role=role, timeout=NATIVE_CHILD_BUDGETS[role],
                cancel=cancel, _invocation=original.invocation, _current=current)
            # Process completion ONLY, never a provider/business receipt.
            if not result.reaped or not result.known_children_gone or result.disposition != 'exited':
                return 2
            return process.returncode if type(process.returncode) is int and 0 <= process.returncode <= 255 else 1
        finally:
            for fd in (ready_r, ready_w, go_r, go_w):
                if fd is not None:
                    _native_pipe_close(fd, pipe_bindings[fd])


def _native_child_main(argv):
    """Private fixed consumer; no RootRun/trigger/availability wiring."""
    if type(argv) is not list or not argv:
        raise CustodyError('native-source-command-invalid')
    if argv[0] == '--native-broker-child' and len(argv) in (2, 3):
        role, identifier = argv[1], argv[2] if len(argv) == 3 else None
        native_child_arguments(role, identifier)
        _native_broker_application_imports(role, identifier)
        import signal
        import threading
        cancel = threading.Event()
        old = {name: signal.signal(name, lambda _signum, _frame: cancel.set()) for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        try:
            return _native_consume_source(role, identifier, cancel=cancel)
        finally:
            for name, handler in old.items():
                signal.signal(name, handler)
    if (argv[0] == '--native-broker-post-seatbelt' and len(argv) == 5
            and all(type(value) is str and re.fullmatch('[0-9]{1,8}', value) for value in argv[3:])):
        role, identifier = argv[1], None if argv[2] == '-' else argv[2]
        native_child_arguments(role, identifier)
        _native_broker_application_imports(role, identifier)
        return _native_post_seatbelt_source(role, identifier, ready_fd=int(argv[3]), go_fd=int(argv[4]))
    raise CustodyError('native-source-command-invalid')


if __name__ == '__main__' and sys.argv[1:2] in (['--native-broker-child'], ['--native-broker-post-seatbelt']):
    try:
        raise SystemExit(_native_child_main(sys.argv[1:]))
    except CustodyError as error:
        print('error: ' + error.code, file=sys.stderr)
        raise SystemExit(2)
