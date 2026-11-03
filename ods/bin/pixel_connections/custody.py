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
NATIVE_STATE = Path('/private/var/lib/pixel-source-broker')
NATIVE_ENV = Path('/private/etc/pixel-source-broker.env')
NATIVE_KIT = Path('/usr/local/libexec/ods-pixel-access/pixel_connections/source-broker-kit')
NATIVE_LABEL = 'com.ods.pixel-native-source-'
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


def _native_selected(selection):
    # A duck-typed guard/checksum is not a Root deployment capability.
    if type(selection) is not _NativeSourceSelection or selection.seal is not _NATIVE_SEAL:
        raise CustodyError('native-source-deployment-required')
    return selection.guard()


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
    if name not in ('source-identity.json', 'source-custody-prepare.json'):
        raise CustodyError('native-source-record-invalid')
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1 or info.st_size > 16384):
            raise CustodyError('native-source-record-unsafe')
        _native_no_acl(stream.fileno())
        raw = stream.read(16385)
        if len(raw) > 16384:
            raise CustodyError('native-source-record-unsafe')
    try:
        value = json.loads(raw, object_pairs_hook=_native_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-record-invalid') from None
    return value, raw


def _native_save_at(selection, directory, name, value, *, previous=None):
    _native_selected(selection)
    _current, raw = _native_json_at(directory, name)
    if raw != previous:
        raise CustodyError('native-source-record-changed')
    body = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(body) > 16384:
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
        _native_selected(selection)
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
    _native_selected(selection)
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
            occupied = {entry.pw_uid for entry in users} | {entry.pw_gid for entry in users} | {entry.gr_gid for entry in groups}
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



def native_verify_kit(selection):
    """A protected but not-yet-published kit is unavailable, never synthesized.

    The future protected native installer must publish this manifest with the
    selected Source blob and deployment. This function does not publish it.
    """
    _native_selected(selection)
    from pixel_macos_custody import protected_directory
    bodies = {}
    with protected_directory(NATIVE_KIT) as directory:
        if set(os.listdir(directory)) != {'source-kit.json', 'broker.py', 'source-broker.env'}:
            raise CustodyError('native-source-kit-invalid')
        for name, mode in (('source-kit.json', 0o644), ('broker.py', 0o755), ('source-broker.env', 0o644)):
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
            or type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != 1
            or manifest['kind'] != 'ods-native-source-kit'
            or manifest['deploymentSha256'] != selection.deployment.checksum
            or type(manifest['sourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', manifest['sourceRef'])
            or type(manifest['files']) is not dict or set(manifest['files']) != {'broker.py', 'source-broker.env'}):
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
    _native_selected(selection)
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
                wanted = (_native_uuid(lib, doc['ownerUid']), _native_uuid(lib, identity['id']))
                if _native_acl_entries(gate, missing=True) != wanted:
                    _native_write_gate_acl(selection, gate, doc['ownerUid'], identity['id'])
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
                native_verify_gate(gate, doc['ownerUid'], identity['id'])
                _native_selected(selection)
                return descriptor
            finally:
                os.close(gate)
