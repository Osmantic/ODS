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
    return decode_oauth_result(raw, keys, kind)


def decode_oauth_result(raw, keys, kind):
    """Shared content validation; filesystem custody belongs to the backend."""
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _name: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError):
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
    return exchange_result(result, flow_id)


def exchange_result(result, flow_id):
    """One shared interpretation of the broker's content-free exchange result."""
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
    return env_text(lines, connection_overrides(lines, gateway=gateway, calendar_id=calendar_id,
        time_zone=time_zone, gmail_window_days=gmail_window_days))


def connection_overrides(lines, *, gateway, calendar_id, time_zone, gmail_window_days):
    """One shared capability/query decision for both fixed host backends."""
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
    return overrides


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
NATIVE_CONNECTION_RECORD = Path('/private/etc/ods/portal-connections.json')
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


def native_current_source_selection(bridge):
    """Select the current protected deployment without a caller-provided copy.

    The class method is the authority: never invoke an instance override or
    accept a deployment supplied by a request. Subsequent uses of the sealed
    selection still run its ordinary guard and detect deployment changes.
    """
    _native_root()
    from pixel_access_bridge import LaunchdAccessBridge
    from .planner import ConnectionDeployment
    if type(bridge) is not LaunchdAccessBridge:
        raise CustodyError('native-source-deployment-required')
    current = LaunchdAccessBridge.connection_deployment(bridge)
    if type(current) is not ConnectionDeployment:
        raise CustodyError('native-source-deployment-changed')
    doc = current.document
    if (doc['ownerUid'], doc['ownerGid'], doc['home']) != (
            bridge.owner.pw_uid, bridge.owner.pw_gid, str(bridge.home)):
        raise CustodyError('native-source-owner-changed')
    return _NativeSourceSelection(bridge, current, _NATIVE_SEAL)


def _native_initial_boot_pending(value):
    if value is None:
        return False
    if (type(value) is not dict or set(value) != {'schemaVersion', 'kind', 'bindingSha256', 'transactions'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['kind'] != 'ods-native-source-initial-boot' or type(value['transactions']) is not list
            or not 1 <= len(value['transactions']) <= 16 or type(value['transactions'][-1]) is not dict):
        return True  # Unknown fixed metadata never waives the lifecycle barrier.
    return value['transactions'][-1].get('phase') != 'committed'


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
            initial, _initial_raw = _native_json_at(directory, 'source-initial-generation.json')
            initial_boot, _boot_raw = _native_json_at(directory, 'source-initial-boot.json')
        if initial is not None and (type(initial) is not dict or initial.get('phase') != 'committed'):
            raise CustodyError('native-source-initial-generation-requires-lifecycle')
        if _native_initial_boot_pending(initial_boot):
            raise CustodyError('native-source-initial-boot-requires-lifecycle')
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
    if name not in ('source-identity.json', 'source-custody-prepare.json', 'source-environment.json', 'source-logs-prepare.json', 'source-publication.json', 'source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json', 'source-environment-generation.json', 'source-boot-quiesce.json', 'source-installer-rebind.json', 'source-sidefiles.json', 'source-initial-generation.json', 'source-initial-boot.json', 'source-installer-code-only.json', 'source-installer-history.json'):
        raise CustodyError('native-source-record-invalid')
    if name in ('source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json', 'source-environment-generation.json', 'source-boot-quiesce.json', 'source-installer-rebind.json', 'source-sidefiles.json', 'source-initial-generation.json', 'source-initial-boot.json', 'source-installer-code-only.json', 'source-installer-history.json'):
        try:
            raw, _inode = _native_root_file(directory, name, mode=mode,
                limit=524288 if name in ('source-execution.json', 'source-boot-quiesce.json') else 131072 if name in ('source-jobs-active.json', 'source-installer-history.json') else 65536)
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
    if len(body) > (524288 if name in ('source-execution.json', 'source-boot-quiesce.json') else 131072 if name in ('source-jobs-active.json', 'source-installer-history.json') else
            65536 if name in ('source-publication.json', 'source-jobs-prepare.json', 'source-environment-generation.json', 'source-installer-rebind.json', 'source-sidefiles.json', 'source-initial-generation.json', 'source-initial-boot.json', 'source-installer-code-only.json', 'source-installer-history.json') else 16384):
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


_INSTALLER_SOURCE_CODE = frozenset(('source-runtime-installation.json', 'source-runtime-publication.json',
    'source-sidefiles.json', 'source-publication.json'))
_INSTALLER_ABSENCE_KEYS = frozenset(('schemaVersion', 'kind', 'transaction', 'currentDigest', 'candidateDigest',
    'ownerUid', 'ownerGid', 'oldRuntimeCustodySha256', 'runtimeRoot', 'sourceRoot', 'kit', 'metadata', 'identity', 'witness'))


def _native_installer_directory_owner(name):
    """Fresh Directory Service owner data; never a live runtime capability."""
    if (type(name) is not str or not 0 < len(name) <= 256 or any(ord(char) < 32 for char in name)):
        raise CustodyError('native-source-installer-owner-invalid')
    try:
        owner = pwd.getpwnam(name)
    except KeyError:
        raise CustodyError('native-source-installer-owner-unavailable') from None
    if (owner.pw_name != name or type(owner.pw_uid) is not int or owner.pw_uid <= 0
            or type(owner.pw_gid) is not int or owner.pw_gid < 0 or type(owner.pw_dir) is not str
            or not Path(owner.pw_dir).is_absolute() or Path(owner.pw_dir).resolve() != Path(owner.pw_dir)):
        raise CustodyError('native-source-installer-owner-invalid')
    return owner


def _native_installer_owner(bridge, *, context_owner=None):
    """Request-local DS identity for stopped/mixed file proofs only.

    This neither discovers a gateway nor sets owner/runtime fields on a bridge.
    NativeSource selection still requires ordinary live discovery separately.
    """
    from pixel_access_bridge import LaunchdAccessBridge
    _native_root()
    if (type(bridge) is not LaunchdAccessBridge or Path(bridge.state) != NATIVE_META
            or LaunchdAccessBridge._native_runtime_lease(bridge) is None):
        raise CustodyError('native-source-installer-transaction-required')
    LaunchdAccessBridge._native_source_deadline_ns(bridge)
    owner = _native_installer_directory_owner(bridge.gateway_owner)
    if context_owner is not None and (type(context_owner) is not dict
            or set(context_owner) != {'name', 'uid', 'gid', 'home'}
            or any(type(context_owner[key]) is not int for key in ('uid', 'gid'))
            or context_owner != {'name': owner.pw_name, 'uid': owner.pw_uid, 'gid': owner.pw_gid, 'home': owner.pw_dir}):
        raise CustodyError('native-source-installer-owner-changed')
    return owner


@dataclass(frozen=True)
class _NativeRetainedSourceCensus:
    """Standalone identity-only observation, never an installer bridge."""
    uid: int
    gid: int

    def command(self, arguments, timeout=10):
        if arguments not in (['/bin/launchctl', 'print', 'system'], ['/bin/ps', '-axo', 'pid=,uid=']):
            raise CustodyError('native-source-retained-command-invalid')
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=10, check=False, env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': NATIVE_HOME, 'LANG': 'C', 'LC_ALL': 'C'})
        if result.returncode or len(result.stdout) > 1024 * 1024:
            raise CustodyError('native-source-retained-census-unavailable')
        return result.stdout


def _native_installer_census_owner(bridge):
    from pixel_access_bridge import LaunchdAccessBridge
    if type(bridge) is LaunchdAccessBridge:
        return _native_installer_directory_owner(bridge.gateway_owner)
    if type(bridge) is _NativeRetainedSourceCensus:
        owner = pwd.getpwuid(bridge.uid)
        if owner.pw_uid != bridge.uid or owner.pw_gid != bridge.gid or bridge.uid <= 0:
            raise CustodyError('native-source-retained-owner-invalid')
        return owner
    raise CustodyError('native-source-installer-census-required')


def _native_installer_context(bridge, transaction, *, mode='absent'):
    from pixel_access_bridge import LaunchdAccessBridge
    from pixel_macos_custody import protected_directory
    _native_root()
    if (type(bridge) is not LaunchdAccessBridge or Path(bridge.state) != NATIVE_META
            or LaunchdAccessBridge._native_runtime_lease(bridge) is None
            or type(transaction) is not dict or set(transaction) != {'kind', 'transaction', 'currentDigest', 'candidateDigest'}
            or transaction['kind'] != 'native-installer-upgrade'
            or any(type(transaction[key]) is not str or not _NATIVE_SHA.fullmatch(transaction[key])
                for key in ('transaction', 'currentDigest', 'candidateDigest'))
            or transaction['currentDigest'] == transaction['candidateDigest']):
        raise CustodyError('native-source-installer-transaction-required')
    LaunchdAccessBridge._native_source_deadline_ns(bridge)
    name = 'runtime-upgrade-context-' + transaction['candidateDigest'] + '.json'
    with protected_directory(NATIVE_META) as metadata:
        body, _inode = _native_root_file(metadata, name, limit=32 * 1024 * 1024)
    value = _native_installer_context_bytes(body, transaction, mode=mode)
    _native_installer_owner(bridge, context_owner=value['owner'])
    return value, body


def _native_installer_context_bytes(body, transaction, *, mode='absent'):
    """Pure frozen-context decoding; grants no live bridge or mutation authority."""
    if hashlib.sha256(body).hexdigest() != transaction['transaction']:
        raise CustodyError('native-source-installer-context-changed')
    try:
        value = json.loads(body.decode(), object_pairs_hook=_native_pairs)
        intent, owner = value['source_lifecycle'], value['owner']
        if (type(value['schemaVersion']) is not int or value['schemaVersion'] != 2
                or type(intent) is not dict or set(intent) != {'schemaVersion', 'kind', 'mode', 'nonce',
                    'currentDigest', 'candidateDigest', 'ownerUid', 'ownerGid', 'familySha256'}
                or type(intent['schemaVersion']) is not int or intent['schemaVersion'] != 1
                or intent['kind'] != 'ods-native-source-installer-intent' or mode not in ('absent', 'withdrawal') or intent['mode'] != mode
                or type(intent['nonce']) is not str or not FLOW_ID.fullmatch(intent['nonce'])
                or type(intent['familySha256']) is not str or not _NATIVE_SHA.fullmatch(intent['familySha256'])
                or (intent['currentDigest'], intent['candidateDigest']) != (transaction['currentDigest'], transaction['candidateDigest'])
                or value['upgrade_qualification']['currentDigest'] != intent['currentDigest']
                or value['upgrade_qualification']['candidateDigest'] != intent['candidateDigest']
                or value['runtime_bundle']['digest'] != intent['candidateDigest']
                or (owner['uid'], owner['gid']) != (intent['ownerUid'], intent['ownerGid'])):
            raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-installer-context-invalid') from None
    return value


def _native_installer_image(item):
    return (type(item) is dict and set(item) == {'inode', 'sha256'} and _native_inode(item['inode'])
        and type(item['sha256']) is str and _NATIVE_SHA.fullmatch(item['sha256']) is not None)


def native_installer_absence_receipt(value, *, transaction, owner_uid, owner_gid):
    """Pure shape/binding validation. A receipt never replaces a fresh census."""
    if (type(value) is not dict or set(value) != _INSTALLER_ABSENCE_KEYS
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['kind'] != 'ods-native-source-installer-absence'
            or (value['transaction'], value['currentDigest'], value['candidateDigest']) != (
                transaction['transaction'], transaction['currentDigest'], transaction['candidateDigest'])
            or (value['ownerUid'], value['ownerGid']) != (owner_uid, owner_gid)
            or any(type(value[key]) is not int for key in ('ownerUid', 'ownerGid'))
            or owner_uid <= 0 or owner_gid < 0
            or type(value['oldRuntimeCustodySha256']) is not str or not _NATIVE_SHA.fullmatch(value['oldRuntimeCustodySha256'])
            or value['runtimeRoot'] != str(NATIVE_RUNTIMES / transaction['currentDigest'] / 'runtime')
            or type(value['sourceRoot']) is not str or not value['sourceRoot'].startswith('/')
            or type(value['metadata']) is not dict or set(value['metadata']) - (_INSTALLER_SOURCE_CODE | {'source-identity.json'})
            or any(not _native_installer_image(image) for image in value['metadata'].values())):
        raise CustodyError('native-source-installer-absence-invalid')
    identity, kit, witness = value['identity'], value['kit'], value['witness']
    if identity == {'kind': 'absent'}:
        if 'source-identity.json' in value['metadata']:
            raise CustodyError('native-source-installer-absence-invalid')
        uid = None
    elif type(identity) is dict and set(identity) == {'kind', 'record'} and identity['kind'] == 'retained':
        intent = _native_identity_intent(identity['record'])
        if intent['id'] == owner_uid or 'source-identity.json' not in value['metadata']:
            raise CustodyError('native-source-installer-absence-invalid')
        uid = intent['id']
    else:
        raise CustodyError('native-source-installer-absence-invalid')
    if kit is not None and (type(kit) is not dict or set(kit) != {'inode', 'files'} or not _native_inode(kit['inode'])
            or type(kit['files']) is not dict
            or set(kit['files']) not in ({'source-kit.json', 'broker.py', 'source-broker.env'},
                {'source-kit.json', 'broker.py', 'source-broker.env', *NATIVE_PROFILES})
            or any(not _native_installer_image(image) for image in kit['files'].values())):
        raise CustodyError('native-source-installer-absence-invalid')
    if (type(witness) is not dict or set(witness) != {'boot', 'loadedSourceJobs', 'brokerProcesses', 'brokerUid'}
            or type(witness['boot']) is not str
            or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', witness['boot'])
            or type(witness['loadedSourceJobs']) is not int or witness['loadedSourceJobs'] != 0
            or type(witness['brokerProcesses']) is not int or witness['brokerProcesses'] != 0
            or witness['brokerUid'] != uid):
        raise CustodyError('native-source-installer-absence-invalid')
    return value


def _native_installer_transaction_value(value):
    return (type(value) is dict and set(value) == {'kind', 'transaction', 'currentDigest', 'candidateDigest'}
        and value['kind'] == 'native-installer-upgrade'
        and all(type(value[key]) is str and _NATIVE_SHA.fullmatch(value[key])
            for key in ('transaction', 'currentDigest', 'candidateDigest'))
        and value['currentDigest'] != value['candidateDigest'])


def _native_installer_gate_image(value, uid):
    return (type(value) is dict and set(value) == {'inode', 'sha256', 'mode', 'gid'}
        and _native_installer_image({key: value[key] for key in ('inode', 'sha256')})
        and type(value['mode']) is int and value['mode'] == 0o640
        and type(value['gid']) is int and value['gid'] == uid)


def native_installer_withdrawal_intent(value, *, transaction, owner_uid, owner_gid):
    return _native_withdrawal_intent(value, transaction=transaction, owner_uid=owner_uid, owner_gid=owner_gid, purpose='upgrade')


def native_retirement_withdrawal_intent(value, *, transaction, owner_uid, owner_gid):
    return _native_withdrawal_intent(value, transaction=transaction, owner_uid=owner_uid, owner_gid=owner_gid, purpose='retirement')


def _native_retirement_transaction_value(value):
    return (type(value) is dict and set(value) == {'kind', 'transaction', 'currentDigest'}
        and value['kind'] == 'native-installer-retirement'
        and all(type(value[key]) is str and _NATIVE_SHA.fullmatch(value[key]) for key in ('transaction', 'currentDigest')))


def _native_withdrawal_intent(value, *, transaction, owner_uid, owner_gid, purpose):
    """Pure frozen before-state contract, never a stop or custody capability."""
    valid_transaction = (_native_retirement_transaction_value(transaction) if purpose == 'retirement'
        else _native_installer_transaction_value(transaction) if purpose == 'upgrade' else False)
    kind = 'ods-native-source-' + ('retirement' if purpose == 'retirement' else 'installer') + '-withdrawal-intent'
    if (not valid_transaction or type(value) is not dict
            or set(value) != {'schemaVersion', 'kind', 'transaction', 'binding', 'runtime', 'owner',
                'generation', 'boot', 'environment', 'gate', 'metadata', 'token'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['kind'] != kind or value['transaction'] != transaction
            or type(value['owner']) is not dict or set(value['owner']) != {'uid', 'gid'}
            or any(type(value['owner'][key]) is not int for key in ('uid', 'gid'))
            or value['owner'] != {'uid': owner_uid, 'gid': owner_gid} or owner_uid <= 0 or owner_gid < 0
            or type(value['generation']) is not str or not FLOW_ID.fullmatch(value['generation'])
            or type(value['boot']) is not str or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', value['boot'])):
        raise CustodyError('native-source-installer-withdrawal-intent-invalid')
    binding, runtime, environment = value['binding'], value['runtime'], value['environment']
    fields = {'schemaVersion', 'kind', 'deploymentSha256', 'kitSha256', 'gate', 'ownerUid', 'brokerUid',
        'brokerGuid', 'runtimeCustodySha256', 'bundleSha256', 'sourceRef'}
    if (type(binding) is not dict or set(binding) != fields
            or type(binding['schemaVersion']) is not int or binding['schemaVersion'] != 1
            or binding['kind'] != 'ods-native-source-environment' or binding['ownerUid'] != owner_uid
            or type(binding['ownerUid']) is not int or type(binding['brokerUid']) is not int
            or binding['brokerUid'] in (0, owner_uid) or not 60000 <= binding['brokerUid'] < 65000
            or not _native_inode(binding['gate'])
            or any(type(binding[key]) is not str or not _NATIVE_SHA.fullmatch(binding[key])
                for key in ('deploymentSha256', 'kitSha256', 'runtimeCustodySha256', 'bundleSha256'))
            or type(binding['sourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', binding['sourceRef'])
            or type(runtime) is not dict or set(runtime) != {'custodySha256', 'bundleSha256', 'sourceRef'}
            or runtime != {'custodySha256': binding['runtimeCustodySha256'],
                'bundleSha256': transaction['currentDigest'], 'sourceRef': binding['sourceRef']}
            or binding['bundleSha256'] != transaction['currentDigest']
            or type(environment) is not dict or set(environment) != {'inode', 'sha256', 'size'}
            or not _native_installer_image({key: environment[key] for key in ('inode', 'sha256')})
            or type(environment['size']) is not int or not 0 < environment['size'] <= MAX_RESULT_BYTES
            or not _native_installer_gate_image(value['gate'], binding['brokerUid'])
            or type(value['metadata']) is not dict or set(value['metadata']) != {
                'identitySha256', 'prepareSha256', 'logsSha256', 'publicationSha256'}
            or any(type(item) is not str or not _NATIVE_SHA.fullmatch(item) for item in value['metadata'].values())):
        raise CustodyError('native-source-installer-withdrawal-intent-invalid')
    try:
        if str(uuid.UUID(binding['brokerGuid'])).upper() != binding['brokerGuid']:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise CustodyError('native-source-installer-withdrawal-intent-invalid') from None
    token = value['token']
    if token is not None and (type(token) is not list or len(token) != 9
            or any(type(item) is not int for item in token) or not _native_inode(token[:2])
            or token[2:5] != [binding['brokerUid'], binding['brokerUid'], 0o600]
            or not 0 < token[5] <= MAX_RESULT_BYTES or token[6] != 1 or min(token[7:]) <= 0):
        raise CustodyError('native-source-installer-withdrawal-intent-invalid')
    return value


def native_installer_withdrawal_receipt(value, *, intent, transaction, owner_uid, owner_gid):
    return _native_withdrawal_receipt(value, intent=intent, transaction=transaction,
        owner_uid=owner_uid, owner_gid=owner_gid, purpose='upgrade')


def native_retirement_withdrawal_receipt(value, *, intent, transaction, owner_uid, owner_gid):
    return _native_withdrawal_receipt(value, intent=intent, transaction=transaction,
        owner_uid=owner_uid, owner_gid=owner_gid, purpose='retirement')


def _native_withdrawal_receipt(value, *, intent, transaction, owner_uid, owner_gid, purpose):
    """Pure complete-v1 receipt contract; live consumers must re-prove it."""
    _native_withdrawal_intent(intent, transaction=transaction, owner_uid=owner_uid, owner_gid=owner_gid, purpose=purpose)
    prefix = 'retirement' if purpose == 'retirement' else 'installer'
    shared = {'transaction', 'binding', 'runtime', 'owner', 'generation', 'environment', 'metadata', 'token'}
    if (type(value) is not dict or set(value) != shared | {'schemaVersion', 'kind', 'intentSha256',
                'withdrawal', 'archive', 'descriptor', 'boot'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['kind'] != 'ods-native-source-' + prefix + '-withdrawal'
            or any(value[key] != intent[key] for key in shared)
            or value['intentSha256'] != hashlib.sha256(_native_encoded(intent)).hexdigest()
            or type(value['withdrawal']) is not str or not FLOW_ID.fullmatch(value['withdrawal'])
            or type(value['boot']) is not str or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', value['boot'])
            or not _native_installer_gate_image(value['descriptor'], intent['binding']['brokerUid'])
            or type(value['archive']) is not dict or set(value['archive']) != {'name', 'inode', 'receiptSha256'}
            or value['archive']['name'] != '.source-' + prefix + '-withdrawal-' + value['withdrawal']
            or not _native_inode(value['archive']['inode']) or type(value['archive']['receiptSha256']) is not str
            or not _NATIVE_SHA.fullmatch(value['archive']['receiptSha256'])):
        raise CustodyError('native-source-installer-withdrawal-receipt-invalid')
    return value


def native_retirement_inventory_receipt(value, *, owner_uid, owner_gid, runtime):
    """Pure fixed Source inventory shape. Never admission or file authority."""
    if type(value) is dict and value.get('kind') == 'ods-native-source-retirement-code-only':
        from . import native_retirement
        return native_retirement.validate_code_only_inventory(value, owner_uid=owner_uid, owner_gid=owner_gid, runtime=runtime)
    fields = {'schemaVersion', 'kind', 'binding', 'ownerGuid', 'identity', 'generation', 'generationBoot', 'boot', 'gate',
        'children', 'environment', 'kit', 'logs', 'metadata', 'definitions', 'jobs', 'token', 'portalRecord'}
    with_history = type(value) is dict and set(value) == fields | {'history'}
    if (type(runtime) is not dict or set(runtime) != {'deploymentSha256', 'custodySha256', 'bundleSha256', 'sourceRef'}
            or any(type(runtime[key]) is not str or not _NATIVE_SHA.fullmatch(runtime[key])
                for key in ('deploymentSha256', 'custodySha256', 'bundleSha256'))
            or type(runtime['sourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', runtime['sourceRef'])
            or type(value) is not dict or set(value) != fields and not with_history or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['kind'] != 'ods-native-source-retirement-inventory'
            or type(owner_uid) is not int or owner_uid <= 0 or type(owner_gid) is not int or owner_gid < 0):
        raise CustodyError('native-source-retirement-inventory-invalid')
    identity = _native_identity_intent(value['identity'])
    binding = value['binding']
    if (type(binding) is not dict or set(binding) != {'schemaVersion', 'kind', 'deploymentSha256', 'kitSha256',
                'gate', 'ownerUid', 'brokerUid', 'brokerGuid', 'runtimeCustodySha256', 'bundleSha256', 'sourceRef'}
            or type(binding['schemaVersion']) is not int
            or binding != {'schemaVersion': 1, 'kind': 'ods-native-source-environment',
                'deploymentSha256': runtime['deploymentSha256'], 'kitSha256': binding.get('kitSha256'),
                'gate': binding.get('gate'), 'ownerUid': owner_uid, 'brokerUid': identity['id'],
                'brokerGuid': identity['userGuid'], 'runtimeCustodySha256': runtime['custodySha256'],
                'bundleSha256': runtime['bundleSha256'], 'sourceRef': runtime['sourceRef']}
            or identity['id'] == owner_uid or not _native_inode(binding['gate'])
            or type(binding['kitSha256']) is not str or not _NATIVE_SHA.fullmatch(binding['kitSha256'])
            or type(value['generation']) is not str or not FLOW_ID.fullmatch(value['generation'])
            or any(type(value[key]) is not str or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', value[key])
                for key in ('boot', 'generationBoot'))
            or value['portalRecord'] is not None and not _native_installer_image(value['portalRecord'])
            or not _native_installer_gate_image(value['gate'], identity['id'])):
        raise CustodyError('native-source-retirement-inventory-invalid')
    try:
        if str(uuid.UUID(value['ownerGuid'])).upper() != value['ownerGuid']:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise CustodyError('native-source-retirement-inventory-invalid') from None
    environment, kit, logs = value['environment'], value['kit'], value['logs']
    if (type(environment) is not dict or set(environment) != {'inode', 'sha256', 'size'}
            or not _native_installer_image({key: environment[key] for key in ('inode', 'sha256')})
            or type(environment['size']) is not int or not 0 < environment['size'] <= MAX_RESULT_BYTES
            or type(value['children']) is not dict or set(value['children']) != {'private', 'private/oauth', 'projection', 'results', 'proposals'}
            or any(not _native_inode(item) for item in value['children'].values())
            or type(kit) is not dict or set(kit) != {'inode', 'files'} or not _native_inode(kit['inode'])
            or type(kit['files']) is not dict or set(kit['files']) != {'source-kit.json', 'broker.py', 'source-broker.env', *NATIVE_PROFILES}
            or any(not _native_installer_image(item) for item in kit['files'].values())
            or kit['files']['source-kit.json']['sha256'] != binding['kitSha256']
            or type(logs) is not dict or set(logs) != {'directory', 'files'} or not _native_inode(logs['directory'])
            or type(logs['files']) is not dict or set(logs['files']) != set(_native_log_names())
            or any(not _native_inode(item) for item in logs['files'].values())):
        raise CustodyError('native-source-retirement-inventory-invalid')
    metadata = value['metadata']
    required = {'source-identity.json', 'source-custody-prepare.json', 'source-environment.json',
        'source-logs-prepare.json', 'source-publication.json', 'source-jobs-prepare.json'}
    optional = {'source-sidefiles.json', 'source-jobs-active.json', 'source-execution.json',
        'source-environment-generation.json', 'source-installer-rebind.json', 'source-boot-quiesce.json',
        'source-runtime-installation.json', 'source-runtime-publication.json', 'native-app-enrollment.json', 'source-initial-generation.json', 'source-initial-boot.json', 'source-installer-code-only.json', 'source-installer-history.json'}
    if (type(metadata) is not dict or not required <= set(metadata) or set(metadata) - required - optional
            or any(not _native_installer_image(item) for item in metadata.values())
            or type(value['jobs']) is not dict or set(value['jobs']) != {'directory', 'sha256'}
            or not _native_inode(value['jobs']['directory']) or type(value['jobs']['sha256']) is not str
            or not _NATIVE_SHA.fullmatch(value['jobs']['sha256']) or type(value['definitions']) is not dict
            or not 1 <= len(value['definitions']) <= 64):
        raise CustodyError('native-source-retirement-inventory-invalid')
    for label, item in value['definitions'].items():
        if (type(item) is not dict or set(item) != {'role', 'identifier', 'active', 'inode', 'sha256'}
                or type(item['active']) is not bool or not _native_installer_image({key: item[key] for key in ('inode', 'sha256')})):
            raise CustodyError('native-source-retirement-inventory-invalid')
        native_child_arguments(item['role'], item['identifier'])
        if label != NATIVE_LABEL + item['role'] + ('.' + item['identifier'] if item['identifier'] is not None else ''):
            raise CustodyError('native-source-retirement-inventory-invalid')
    token = value['token']
    if token is not None and (type(token) is not list or len(token) != 9 or any(type(item) is not int for item in token)
            or not _native_inode(token[:2]) or token[2:5] != [identity['id'], identity['id'], 0o600]
            or not 0 < token[5] <= MAX_RESULT_BYTES or token[6] != 1 or min(token[7:]) <= 0):
        raise CustodyError('native-source-retirement-inventory-invalid')
    if with_history:
        from . import native_retirement
        native_retirement.validate_history(value['history'], owner_uid=owner_uid, owner_gid=owner_gid, runtime=runtime)
    return value


def native_retirement_inventory(selection):
    """Current stopped-generation inventory; archive cursors are separate."""
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    doc = _native_selected(selection, _publication=True)
    publisher = _publisher(selection)
    _module, _doc, component, ref = publisher._material(selection)
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_DEFINITIONS) as units, protected_directory(NATIVE_KIT.parent) as kit_parent:
        gate = os.open(NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _manifest, kit_sha = native_verify_kit(selection, require_profiles=True, _publication=True)
            identity, binding, expected = _native_environment_binding(selection, metadata, gate, kit_sha, _publication=True)
            origin, generation_boot, tree, _definitions = publisher._environment_origin(selection, globals_module(), doc,
                component, ref, metadata, gate, units, identity, binding, expected)
            active, _raw = _native_json_at(metadata, 'source-jobs-active.json')
            definitions = active['current']['definitions'] if active is not None else {
                label: dict(item, active=False) for label, item in origin['definitions'].items()}
            kit, _files = publisher._kit_image(globals_module(), kit_parent, NATIVE_KIT.name)
            full_binding = dict(binding, runtimeCustodySha256=doc['runtimeCustodySha256'], bundleSha256=doc['bundleSha256'], sourceRef=ref)
            runtime = {'deploymentSha256': selection.deployment.checksum, 'custodySha256': doc['runtimeCustodySha256'],
                'bundleSha256': doc['bundleSha256'], 'sourceRef': ref}
            images = {}
            names = ('source-identity.json', 'source-custody-prepare.json', 'source-environment.json', 'source-logs-prepare.json',
                'source-publication.json', 'source-jobs-prepare.json', 'source-sidefiles.json', 'source-jobs-active.json',
                'source-execution.json', 'source-environment-generation.json', 'source-installer-rebind.json',
                'source-boot-quiesce.json', 'source-runtime-installation.json', 'source-runtime-publication.json', 'native-app-enrollment.json', 'source-initial-generation.json', 'source-initial-boot.json', 'source-installer-code-only.json', 'source-installer-history.json')
            for name in names:
                if _native_at_inode(metadata, name) is not None:
                    body, inode = _native_root_file(metadata, name, limit=524288)
                    images[name] = {'inode': inode, 'sha256': hashlib.sha256(body).hexdigest()}
            prepare, _raw = _native_json_at(metadata, 'source-custody-prepare.json')
            logs, _raw = _native_json_at(metadata, 'source-logs-prepare.json')
            _native_verify_prepared_logs(metadata, identity, binding)
            witness = publisher._no_actors(selection, globals_module(), metadata, _generation=True)
            with protected_directory(NATIVE_CONNECTION_RECORD.parent) as records:
                try:
                    body, inode = _native_root_file(records, NATIVE_CONNECTION_RECORD.name, mode=0o644)
                except FileNotFoundError:
                    portal_record = None
                else:
                    from . import state
                    state.validate(json.loads(body, object_pairs_hook=_native_pairs))
                    portal_record = {'inode': inode, 'sha256': hashlib.sha256(body).hexdigest()}
            value = {'schemaVersion': 1, 'kind': 'ods-native-source-retirement-inventory',
                'binding': full_binding, 'ownerGuid': prepare['ownerGuid'], 'identity': identity,
                'generation': origin['generation'], 'generationBoot': generation_boot, 'boot': witness['boot'],
                'gate': publisher._environment_image(globals_module(), gate, 'custody.json', mode=0o640, gid=identity['id'])[0],
                'children': {name: item['inode'] for name, item in prepare['children'].items()},
                'environment': _native_environment_snapshot(gate, NATIVE_ENV.name, identity['id']),
                'kit': kit, 'logs': {'directory': logs['directory'], 'files': {name: image['inode'] for name, image in logs['files'].items()}},
                'metadata': images, 'definitions': {label: {key: item[key] for key in ('role', 'identifier', 'active', 'inode', 'sha256')}
                    for label, item in definitions.items()}, 'jobs': {'directory': origin['directory'], 'sha256': hashlib.sha256(_native_encoded(tree)).hexdigest()},
                'token': publisher._installer_token_metadata(globals_module(), gate, identity['id']), 'portalRecord': portal_record}
            native_retirement_inventory_receipt(value, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'], runtime=runtime)
            _native_selected(selection, _publication=True)
            return value
        finally:
            os.close(gate)


def _native_retirement_authority(selection, transaction):
    """Source-only authority from the actual immutable Core retirement witness."""
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    doc = _native_selected(selection, _publication=True)
    if not _native_retirement_transaction_value(transaction):
        raise CustodyError('native-source-retirement-transaction-invalid')
    _module, _doc, _component, ref = _publisher(selection)._material(selection)
    runtime = {'deploymentSha256': selection.deployment.checksum, 'custodySha256': doc['runtimeCustodySha256'],
        'bundleSha256': doc['bundleSha256'], 'sourceRef': ref}
    with protected_directory(NATIVE_META) as metadata:
        raw, inode = _native_root_file(metadata, 'retirement.json', limit=32 * 1024 * 1024)
        try:
            value = json.loads(raw, object_pairs_hook=_native_pairs)
            source = value['source']
            if (hashlib.sha256(raw).hexdigest() != transaction['transaction']
                    or type(value) is not dict or set(value) != {'schema', 'kind', 'owner', 'boot', 'hashes', 'trees', 'sandboxes', 'source'}
                    or type(value['schema']) is not int or value['schema'] != 3 or value['kind'] != 'ods-native-source-installer-retirement'
                    or type(value['owner']) is not int or value['owner'] != doc['ownerUid']
                    or str(uuid.UUID(value['boot'])).lower() != value['boot']
                    or type(value['hashes']) is not dict or not 1 <= len(value['hashes']) <= 512
                    or any(type(path) is not str or not path.startswith('/') or type(checksum) is not str
                        or not _NATIVE_SHA.fullmatch(checksum) for path, checksum in value['hashes'].items())
                    or type(value['trees']) is not dict or not value['trees'] or type(value['sandboxes']) is not list
                    or type(source) is not dict or set(source) != {'schemaVersion', 'kind', 'nonce', 'owner', 'runtime', 'inventory'}
                    or type(source['schemaVersion']) is not int or source['schemaVersion'] != 1
                    or source['kind'] != 'ods-native-source-retirement-intent' or type(source['nonce']) is not str or not FLOW_ID.fullmatch(source['nonce'])
                    or source['owner'] != {'uid': doc['ownerUid'], 'gid': doc['ownerGid'], 'name': selection.bridge.owner.pw_name, 'installDir': str(selection.bridge.install)}
                    or source['runtime'] != runtime or transaction['currentDigest'] != runtime['bundleSha256']):
                raise ValueError()
            inventory = native_retirement_inventory_receipt(source['inventory'], owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'], runtime=runtime)
            if inventory['kind'] != 'ods-native-source-retirement-inventory':
                raise CustodyError('native-source-retirement-private-generation-required')
            if inventory['boot'].lower() != value['boot']:
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, RecursionError):
            raise CustodyError('native-source-retirement-witness-invalid') from None
        proof = {'schemaVersion': 1, 'kind': 'ods-native-source-retirement-withdrawal-intent',
            'transaction': dict(transaction), 'binding': inventory['binding'],
            'runtime': {'custodySha256': runtime['custodySha256'], 'bundleSha256': runtime['bundleSha256'], 'sourceRef': ref},
            'owner': {'uid': doc['ownerUid'], 'gid': doc['ownerGid']}, 'generation': inventory['generation'],
            'boot': inventory['generationBoot'], 'environment': inventory['environment'], 'gate': inventory['gate'],
            'metadata': {key: inventory['metadata'][name]['sha256'] for key, name in (
                ('identitySha256', 'source-identity.json'), ('prepareSha256', 'source-custody-prepare.json'),
                ('logsSha256', 'source-logs-prepare.json'), ('publicationSha256', 'source-publication.json'))}, 'token': inventory['token']}
        native_retirement_withdrawal_intent(proof, transaction=transaction, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'])
        if _native_root_file(metadata, 'retirement.json', limit=32 * 1024 * 1024) != (raw, inode):
            raise CustodyError('native-source-retirement-witness-changed')
    return {'transaction': dict(transaction), 'intent': source, 'owner': source['owner'], 'phase': 'prepared',
        'files': [], 'proof': proof, 'receipt': None}


def native_retirement_withdrawal_request(selection, *, transaction):
    authority = _native_retirement_authority(selection, transaction)
    inventory = native_retirement_inventory(selection)
    if 'history' in authority['intent']['inventory']:
        from . import native_retirement
        inventory = native_retirement.add_history(selection, inventory)
    if inventory != authority['intent']['inventory']:
        raise CustodyError('native-source-retirement-inventory-changed')
    _native_retirement_inventory_state(selection, authority)
    return authority


def native_retirement_withdrawal_transaction(selection, *, transaction):
    authority = _native_retirement_authority(selection, transaction)
    _native_retirement_inventory_state(selection, authority)
    return authority


def native_retirement_archive_begin(*, transaction, archive_id):
    _native_root()
    from . import native_retirement
    return native_retirement.archive_begin(transaction=transaction, archive_id=archive_id)


def native_retirement_archive_step(*, transaction):
    _native_root()
    from . import native_retirement
    return native_retirement.archive_step(transaction=transaction)


def _native_retirement_inventory_state(selection, authority):
    """Compare immutable witness images through this fixed withdrawal prefix."""
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    publisher = _publisher(selection)
    inventory, transaction = authority['intent']['inventory'], authority['transaction']
    if 'history' in inventory:
        from . import native_retirement
        native_retirement.verify_history(selection, inventory)
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_KIT.parent) as parent:
        publisher._kit_image(globals_module(), parent, NATIVE_KIT.name, expected=inventory['kit'])
        record, _raw = _native_json_at(metadata, 'source-environment-generation.json')
        if record is not None and (type(record) is not dict or type(record.get('installer', {})) is not dict):
            raise CustodyError('native-source-retirement-origin-changed')
        archive = None
        try:
            if record is not None and record.get('installer', {}).get('transaction') == transaction:
                publisher._environment_generation_record(globals_module(), record, inventory['binding'])
                if (record['previousGeneration'] != inventory['generation'] or record['old']['gate'] != inventory['gate']
                        or record['old']['environment'] != inventory['environment']
                        or record['old']['directory'] != inventory['jobs']['directory']
                        or hashlib.sha256(_native_encoded(record['old']['jobs'])).hexdigest() != inventory['jobs']['sha256']
                        or record['old']['definitions'] != {label + '.plist': {
                            'inode': item['inode'], 'sha256': item['sha256'], 'mode': 0o644, 'gid': 0}
                            for label, item in inventory['definitions'].items()}):
                    raise CustodyError('native-source-retirement-origin-changed')
                if record['archive'] is not None:
                    name = '.source-retirement-withdrawal-' + record['generation']
                    archive = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
                    info = os.fstat(archive)
                    _native_no_acl(archive)
                    if ([info.st_dev, info.st_ino] != record['archive'] or _native_at_inode(metadata, name) != record['archive']
                            or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)):
                        raise CustodyError('native-source-retirement-archive-changed')
            else:
                record = None
            for name, image in inventory['metadata'].items():
                directory, leaf = metadata, name
                if name == 'source-environment-generation.json':
                    try:
                        body, inode = _native_root_file(metadata, name)
                    except FileNotFoundError:
                        body, inode = b'', None
                    if {'inode': inode, 'sha256': hashlib.sha256(body).hexdigest()} != image:
                        leaf = 'source-environment-generation-' + inventory['generation'] + '.json'
                elif name in ('source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json'):
                    current = _native_at_inode(metadata, name)
                    if current is None:
                        if archive is None or record['old']['files'].get(name) != {**image, 'mode': 0o600, 'gid': 0}:
                            raise CustodyError('native-source-retirement-metadata-changed')
                        directory = archive
                    elif current != image['inode']:
                        raise CustodyError('native-source-retirement-metadata-changed')
                body, inode = _native_root_file(directory, leaf, limit=524288)
                if {'inode': inode, 'sha256': hashlib.sha256(body).hexdigest()} != image:
                    raise CustodyError('native-source-retirement-metadata-changed')
            if 'native-app-enrollment.json' not in inventory['metadata'] and _native_at_inode(metadata, 'native-app-enrollment.json') is not None:
                raise CustodyError('native-source-retirement-enrollment-changed')
            base = {key: item for key, item in inventory['binding'].items() if key not in ('runtimeCustodySha256', 'bundleSha256', 'sourceRef')}
            _native_verify_prepared_logs(metadata, inventory['identity'], base)
            with protected_directory(NATIVE_CONNECTION_RECORD.parent) as records:
                image = inventory['portalRecord']
                if image is None:
                    if _native_at_inode(records, NATIVE_CONNECTION_RECORD.name) is not None:
                        raise CustodyError('native-source-retirement-record-changed')
                else:
                    body, inode = _native_root_file(records, NATIVE_CONNECTION_RECORD.name, mode=0o644)
                    if {'inode': inode, 'sha256': hashlib.sha256(body).hexdigest()} != image:
                        raise CustodyError('native-source-retirement-record-changed')
            publisher._no_actors(selection, globals_module(), metadata, _generation=True)
        finally:
            if archive is not None:
                os.close(archive)


_SIDEFILE_ARCHIVES = 'source-sidefiles-archives.json'
_SIDEFILE_ARCHIVE_PREFIX = 'source-sidefiles-archive-'
NATIVE_SIDEFILE_CONFIG_ROOT = Path('/private/var/lib/ods-pixel-native-config')


def _native_sidefile_archive_manifest(value, candidate, *, owner_uid, owner_gid):
    """Closed immutable preservation plan; never owner-file adoption authority."""
    if type(candidate) is not str or not _NATIVE_SHA.fullmatch(candidate):
        raise CustodyError('native-source-sidefile-archive-invalid')
    fields = {'schemaVersion', 'kind', 'currentDigest', 'candidateDigest', 'owner', 'directory',
        'parent', 'authority', 'files', 'absent', 'origin'}
    bound = type(value) is dict and type(value.get('schemaVersion')) is int and value['schemaVersion'] == 2
    if bound:
        fields.add('history')
    authority = (('context', 'runtime-upgrade-context-' + candidate + '.json'),
        ('journal', 'runtime-upgrade.json') if bound else ('restored', 'runtime-upgrade-' + candidate + '.completed.json'))
    files = {'source-sidefiles.json', 'gateway.env'}
    if bound:
        files |= {'onboarding.json', 'source-runtime-installation.json'}
    if (type(value) is not dict or set(value) != fields
            or type(value['schemaVersion']) is not int or value['schemaVersion'] not in (1, 2)
            or value['kind'] != 'ods-native-source-sidefiles-archive'
            or type(candidate) is not str or not _NATIVE_SHA.fullmatch(candidate)
            or value['candidateDigest'] != candidate
            or type(value['currentDigest']) is not str or not _NATIVE_SHA.fullmatch(value['currentDigest'])
            or value['currentDigest'] == candidate
            or type(owner_uid) is not int or owner_uid <= 0 or type(owner_gid) is not int or owner_gid <= 0
            or type(value['owner']) is not dict or set(value['owner']) != {'uid', 'gid'}
            or any(type(value['owner'][key]) is not int for key in ('uid', 'gid'))
            or value['owner'] != {'uid': owner_uid, 'gid': owner_gid}
            or type(value['directory']) is not dict or set(value['directory']) != {'inode'}
            or not _native_inode(value['directory']['inode'])
            or type(value['parent']) is not dict or set(value['parent']) != {'path', 'inode'}
            or value['parent']['path'] != str(NATIVE_SIDEFILE_CONFIG_ROOT / str(owner_uid))
            or not _native_inode(value['parent']['inode'])
            or value['origin'] != ('bound-ready-before-source-publication' if bound else 'unbound-empty-inherited-group')
            or value['absent'] != ([] if bound else ['onboarding.json'])
            or type(value['authority']) is not dict or set(value['authority']) != {key for key, _name in authority}
            or type(value['files']) is not dict or set(value['files']) != files):
        raise CustodyError('native-source-sidefile-archive-invalid')
    if bound:
        history = value['history']
        if (type(history) is not dict or set(history) != {'ledger', 'archives'}
                or type(history['archives']) is not dict or len(history['archives']) > 15
                or candidate in history['archives']
                or bool(history['archives']) != (history['ledger'] is not None)
                or history['ledger'] is not None and not _native_installer_image(history['ledger'])):
            raise CustodyError('native-source-sidefile-archive-invalid')
        for prior, image in history['archives'].items():
            if not _native_sidefile_archive_entry(prior, image):
                raise CustodyError('native-source-sidefile-archive-invalid')
    for key, suffix in authority:
        image = value['authority'][key]
        if (type(image) is not dict or set(image) != {'sourceName', 'sourceInode', 'inode', 'sha256', 'size'}
                or image['sourceName'] != suffix or not _native_inode(image['sourceInode'])
                or not _native_installer_image({name: image[name] for name in ('inode', 'sha256')})
                or image['sourceInode'] == image['inode']
                or type(image['size']) is not int or not 0 < image['size'] <= 32 * 1024 * 1024):
            raise CustodyError('native-source-sidefile-archive-invalid')
    for key in files:
        image = value['files'][key]
        owner_file = bound and key in ('gateway.env', 'onboarding.json')
        uid, gid = (owner_uid, owner_gid) if owner_file else (0, owner_gid if key == 'gateway.env' else 0)
        maximum = 8192 if key == 'source-sidefiles.json' else 131072 if bound else 0
        if (type(image) is not dict or set(image) != {'inode', 'uid', 'gid', 'mode', 'size', 'sha256'}
                or not _native_installer_image({name: image[name] for name in ('inode', 'sha256')})
                or any(type(image[name]) is not int for name in ('uid', 'gid', 'mode', 'size'))
                or (image['uid'], image['gid'], image['mode']) != (uid, gid, 0o600)
                or not 0 <= image['size'] <= maximum
                or (bound or key == 'source-sidefiles.json') and image['size'] == 0
                or not bound and key == 'gateway.env' and image['sha256'] != hashlib.sha256(b'').hexdigest()):
            raise CustodyError('native-source-sidefile-archive-invalid')
    return value


def _native_sidefile_archive_entry(candidate, image):
    return (type(candidate) is str and _NATIVE_SHA.fullmatch(candidate) is not None
        and type(image) is dict and set(image) == {'directory', 'manifest'}
        and type(image['directory']) is dict and set(image['directory']) == {'inode'}
        and _native_inode(image['directory']['inode']) and _native_installer_image(image['manifest']))


def _native_bound_sidefile_archive_authority(value, context_raw, journal_raw, bodies):
    """Frozen prepublication evidence, not a claim that Source finished activation."""
    if value['schemaVersion'] != 2 or type(bodies) is not dict or set(bodies) != set(value['files']):
        raise CustodyError('native-source-sidefile-archive-authority-invalid')
    for key, raw in {**bodies, 'context': context_raw, 'journal': journal_raw}.items():
        image = value['authority'][key] if key in value['authority'] else value['files'][key]
        if type(raw) is not bytes or len(raw) != image['size'] or hashlib.sha256(raw).hexdigest() != image['sha256']:
            raise CustodyError('native-source-sidefile-archive-authority-invalid')
    transaction = dict(kind='native-installer-upgrade', transaction=hashlib.sha256(context_raw).hexdigest(),
        currentDigest=value['currentDigest'], candidateDigest=value['candidateDigest'])
    context = _native_installer_context_bytes(context_raw, transaction)
    journal, records, _raw = _native_installer_journal_bytes(journal_raw, transaction, context)
    try:
        owner, selection = context['owner'], context['runtime_bundle']
        proof = journal['sourceAddition']['proof']
        if (journal['schemaVersion'] != 5 or journal['phase'] != 'active'
                or type(owner) is not dict or set(owner) != {'name', 'uid', 'gid', 'home'}
                or any(type(owner[key]) is not int for key in ('uid', 'gid'))
                or (owner['uid'], owner['gid']) != (value['owner']['uid'], value['owner']['gid'])
                or type(owner['name']) is not str or not owner['name']
                or type(owner['home']) is not str or not owner['home'].startswith('/')
                or proof['kit'] is not None or proof['identity'] != {'kind': 'absent'}):
            raise ValueError()
        supplied = selection['connection_sidefiles']
        if (type(supplied) is not dict or set(supplied) != {'gateway.env', 'onboarding.json'}
                or type(selection['config_path']) is not str
                or str(Path(selection['config_path']).parent) != value['parent']['path']
                or any(type(text) is not str or not 0 < len(text.encode('utf-8')) <= 131072
                    or bodies[name] != text.encode('utf-8') for name, text in supplied.items())):
            raise ValueError()
        receipt = json.loads(bodies['source-sidefiles.json'], object_pairs_hook=_native_pairs)
        expected = dict(schemaVersion=1, kind='ods-native-connection-sidefiles', ownerUid=owner['uid'], ownerGid=owner['gid'],
            configPath=selection['config_path'], bundleSha256=value['candidateDigest'], parent=value['parent']['inode'],
            files={key: hashlib.sha256(body.encode('utf-8')).hexdigest() for key, body in supplied.items()},
            children={key: {'inode': value['files'][key]['inode'], 'ready': True} for key in supplied})
        if (type(receipt) is not dict or receipt != expected or not _native_inode(receipt['parent'])
                or any(type(receipt[key]) is not int for key in ('schemaVersion', 'ownerUid', 'ownerGid'))
                or any(type(child['ready']) is not bool or not _native_inode(child['inode'])
                    for child in receipt['children'].values())):
            raise ValueError()
        rows = [row for row in records if row['path'] == str(NATIVE_META / 'source-runtime-installation.json')]
        if (len(rows) != 1 or rows[0]['before'] is not None or (rows[0]['mode'], rows[0]['gid']) != (0o600, 0)
                or rows[0]['after'] != bodies['source-runtime-installation.json']):
            raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-sidefile-archive-authority-invalid') from None


def _native_sidefile_archive_authority(value, context_raw, restored_raw, receipt_raw):
    """Reopen the complete frozen context/journal and the unbound receipt."""
    transaction = dict(kind='native-installer-upgrade', transaction=hashlib.sha256(context_raw).hexdigest(),
        currentDigest=value['currentDigest'], candidateDigest=value['candidateDigest'])
    context = _native_installer_context_bytes(context_raw, transaction)
    restored, _records, _raw = _native_installer_journal_bytes(restored_raw, transaction, context, _completed=True)
    try:
        owner, selection = context['owner'], context['runtime_bundle']
        proof = restored['sourceAddition']['proof']
        if (restored['phase'] != 'restored' or type(owner) is not dict
                or set(owner) != {'name', 'uid', 'gid', 'home'}
                or any(type(owner[key]) is not int for key in ('uid', 'gid'))
                or (owner['uid'], owner['gid']) != (value['owner']['uid'], value['owner']['gid'])
                or type(owner['name']) is not str or not owner['name']
                or type(owner['home']) is not str or not owner['home'].startswith('/')
                or proof['kit'] is not None or proof['identity'] != {'kind': 'absent'} or proof['metadata'] != {}):
            raise ValueError()
        supplied = selection['connection_sidefiles']
        if (type(supplied) is not dict or set(supplied) != {'gateway.env', 'onboarding.json'}
                or any(type(text) is not str or not 0 < len(text.encode('utf-8')) <= 131072 for text in supplied.values())
                or type(selection['config_path']) is not str
                or str(Path(selection['config_path']).parent) != value['parent']['path']):
            raise ValueError()
        receipt = json.loads(receipt_raw, object_pairs_hook=_native_pairs)
        expected = dict(schemaVersion=1, kind='ods-native-connection-sidefiles', ownerUid=owner['uid'], ownerGid=owner['gid'],
            configPath=selection['config_path'], bundleSha256=value['candidateDigest'], parent=value['parent']['inode'],
            files={key: hashlib.sha256(text.encode('utf-8')).hexdigest() for key, text in supplied.items()},
            children={key: {'inode': None, 'ready': False} for key in supplied})
        if (type(receipt) is not dict or receipt != expected or not _native_inode(receipt['parent'])
                or any(type(receipt[key]) is not int for key in ('schemaVersion', 'ownerUid', 'ownerGid'))
                or any(type(child['ready']) is not bool for child in receipt['children'].values())):
            raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-sidefile-archive-authority-invalid') from None


def native_sidefile_archives(*, owner_uid, owner_gid):
    """Read only terminal archives named by the Root ledger; never discover grants.

    Callers retain their complete namespace refusal checks. An unregistered
    directory is not history, even when its name resembles a known candidate.
    Private context/journal copies survive legitimate retire-runtime moves of
    the originals. Their bytes and inode bindings remain part of the archive.
    """
    from pixel_macos_custody import protected_directory
    result = {'ledger': None, 'archives': {}}
    with protected_directory(NATIVE_META) as metadata:
        try: ledger_raw, ledger_inode = _native_root_file(metadata, _SIDEFILE_ARCHIVES, limit=131072)
        except FileNotFoundError: return result
        try: ledger = json.loads(ledger_raw, object_pairs_hook=_native_pairs)
        except (ValueError, UnicodeError, RecursionError):
            raise CustodyError('native-source-sidefile-archives-invalid') from None
        if (type(ledger) is not dict or set(ledger) != {'schemaVersion', 'kind', 'archives'}
                or type(ledger['schemaVersion']) is not int or ledger['schemaVersion'] != 1
                or ledger['kind'] != 'ods-native-source-sidefiles-archives'
                or type(ledger['archives']) is not dict or not 1 <= len(ledger['archives']) <= 16):
            raise CustodyError('native-source-sidefile-archives-invalid')
        for candidate, image in ledger['archives'].items():
            if not _native_sidefile_archive_entry(candidate, image):
                raise CustodyError('native-source-sidefile-archives-invalid')
            name = _SIDEFILE_ARCHIVE_PREFIX + candidate
            directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
            try:
                info = os.fstat(directory)
                _native_no_acl(directory)
                if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)
                        or [info.st_dev, info.st_ino] != image['directory']['inode']
                        or _native_at_inode(metadata, name) != image['directory']['inode']):
                    raise CustodyError('native-source-sidefile-archive-changed')
                manifest_raw, manifest_inode = _native_root_file(directory, 'manifest.json', limit=131072)
                if {'inode': manifest_inode, 'sha256': hashlib.sha256(manifest_raw).hexdigest()} != image['manifest']:
                    raise CustodyError('native-source-sidefile-archive-changed')
                try: value = json.loads(manifest_raw, object_pairs_hook=_native_pairs)
                except (ValueError, UnicodeError, RecursionError):
                    raise CustodyError('native-source-sidefile-archive-invalid') from None
                _native_sidefile_archive_manifest(value, candidate, owner_uid=owner_uid, owner_gid=owner_gid)
                expected_names = {'manifest.json', *(key + '.json' for key in value['authority']), *value['files']}
                if value['directory'] != image['directory'] or set(os.listdir(directory)) != expected_names:
                    raise CustodyError('native-source-sidefile-archive-changed')
                bodies = {}
                for key in value['authority']:
                    wanted = value['authority'][key]
                    raw, inode = _native_root_file(directory, key + '.json', limit=32 * 1024 * 1024)
                    if (inode != wanted['inode'] or len(raw) != wanted['size']
                            or hashlib.sha256(raw).hexdigest() != wanted['sha256']):
                        raise CustodyError('native-source-sidefile-archive-changed')
                    bodies[key] = raw
                for key, wanted in value['files'].items():
                    if wanted['uid'] == 0:
                        raw, inode = _native_root_file(directory, key, gid=wanted['gid'],
                            limit=8192 if key == 'source-sidefiles.json' else 131072)
                    else:
                        observed, raw = _native_installer_owner_file(directory, key, owner_uid, owner_gid)
                        inode = observed['inode']
                    if (inode != wanted['inode'] or len(raw) != wanted['size']
                            or hashlib.sha256(raw).hexdigest() != wanted['sha256']):
                        raise CustodyError('native-source-sidefile-archive-changed')
                    bodies[key] = raw
                if value['schemaVersion'] == 1:
                    _native_sidefile_archive_authority(value, bodies['context'], bodies['restored'], bodies['source-sidefiles.json'])
                else:
                    _native_bound_sidefile_archive_authority(value, bodies['context'], bodies['journal'],
                        {key: bodies[key] for key in value['files']})
                    if any(ledger['archives'].get(prior) != item for prior, item in value['history']['archives'].items()):
                        raise CustodyError('native-source-sidefile-archive-history-changed')
                after = os.fstat(directory)
                _native_no_acl(directory)
                if ((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) != (0, 0, 0o700)
                        or (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)
                            != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
                        or set(os.listdir(directory)) != expected_names or _native_at_inode(metadata, name) != image['directory']['inode']
                        or _native_root_file(directory, 'manifest.json', limit=131072) != (manifest_raw, manifest_inode)):
                    raise CustodyError('native-source-sidefile-archive-changed')
                result['archives'][name] = {'inode': image['directory']['inode'], 'manifest': value,
                    'manifestImage': image['manifest'], 'manifestSize': len(manifest_raw)}
            finally:
                os.close(directory)
        # A terminal ledger can append later capsules, but no frozen capsule
        # may invent a predecessor, replace its images, or form a history cycle.
        visited, visiting = set(), set()
        def prior_archives(candidate):
            if candidate in visiting:
                raise CustodyError('native-source-sidefile-archive-history-changed')
            if candidate in visited:
                return
            visiting.add(candidate)
            value = result['archives'][_SIDEFILE_ARCHIVE_PREFIX + candidate]['manifest']
            for prior in value.get('history', {}).get('archives', {}):
                prior_archives(prior)
            visiting.remove(candidate)
            visited.add(candidate)
        for candidate in ledger['archives']:
            prior_archives(candidate)
        if _native_root_file(metadata, _SIDEFILE_ARCHIVES, limit=131072) != (ledger_raw, ledger_inode):
            raise CustodyError('native-source-sidefile-archives-changed')
        result['ledger'] = {'inode': ledger_inode, 'sha256': hashlib.sha256(ledger_raw).hexdigest()}
    return result


def _native_sidefile_archive_names(view):
    return {_SIDEFILE_ARCHIVES, *view['archives']} if view['ledger'] is not None else set()


def _native_installer_absence_state(bridge, *, qualified_runtime=None, _installer_publication=None, _installer_code_only=None):
    """Fixed file-only Source absence/identity inventory; no file is removed."""
    from pixel_macos_custody import protected_directory
    owner = _native_installer_census_owner(bridge)
    for path in (NATIVE_STATE, NATIVE_ENV, NATIVE_LEGACY_ENV, NATIVE_LOGS):
        if os.path.lexists(path):
            raise CustodyError('native-source-installer-private-state-present')
    images, identity = {}, None
    with protected_directory(NATIVE_META) as metadata:
        names = {name for name in os.listdir(metadata) if name.startswith(('source-', '.source-', 'native-source-', '.native-source-'))}
        archived = native_sidefile_archives(owner_uid=owner.pw_uid, owner_gid=owner.pw_gid)
        extra = _native_sidefile_archive_names(archived)
        if _installer_code_only is not None:
            extra |= _native_installer_code_only_names(_installer_code_only)['metadata']
        if 'source-installer-history.json' in names:
            retained = _native_installer_history_records(bridge)
            extra |= {'source-installer-history.json', *(item['name'] for item in retained),
                *(name for item in retained for name in item['metadata'])}
        if 'source-installer-code-only.json' in names and 'source-installer-code-only.json' not in extra:
            selected = native_source_selection(bridge, bridge.connection_deployment())
            extra |= native_installer_code_only_history(selected)['metadata']
        if names - (_INSTALLER_SOURCE_CODE | {'source-identity.json'} | extra):
            raise CustodyError('native-source-installer-metadata-preserved')
        for name in names - extra:
            body, inode = _native_root_file(metadata, name, limit=131072)
            images[name] = {'inode': inode, 'sha256': hashlib.sha256(body).hexdigest()}
            try:
                record = json.loads(body.decode(), object_pairs_hook=_native_pairs)
            except (ValueError, UnicodeError, RecursionError):
                raise CustodyError('native-source-installer-metadata-invalid') from None
            if name == 'source-identity.json':
                identity = _native_identity_intent(record)
            elif (type(record) is not dict or type(record.get('schemaVersion')) is not int or record['schemaVersion'] != 1
                    or (name == 'source-publication.json' and record != _installer_publication
                        and (record.get('phase') != 'prepared-no-jobs' or record.get('bindings') != {}))
                    or (name == 'source-runtime-publication.json' and record.get('phase') != 'published-no-jobs')
                    or (name == 'source-runtime-installation.json' and record.get('kind') != 'ods-native-source-runtime-publication')
                    or (name == 'source-sidefiles.json' and record.get('kind') != 'ods-native-connection-sidefiles')):
                raise CustodyError('native-source-installer-metadata-invalid')
    users, groups = _native_ds_record('Users'), _native_ds_record('Groups')
    if identity is None:
        if users is not None or groups is not None:
            raise CustodyError('native-source-installer-identity-unbound')
        for lookup in (pwd.getpwnam, grp.getgrnam):
            try:
                lookup(NATIVE_BROKER)
            except KeyError:
                continue
            raise CustodyError('native-source-installer-identity-unbound')
        identity_view, uid = {'kind': 'absent'}, None
    else:
        for kind, record in (('Users', users), ('Groups', groups)):
            _native_identity_record(record, _native_identity_attributes(identity, kind), complete=True)
        _native_identity_collisions(identity)
        if identity['id'] in (0, owner.pw_uid):
            raise CustodyError('native-source-installer-identity-conflict')
        identity_view, uid = {'kind': 'retained', 'record': identity}, identity['id']
    raw = bridge.command(['/bin/launchctl', 'print', 'system'], timeout=10)
    if not raw.startswith('system = {') or not raw.rstrip().endswith('}') or len(raw) > 1024 * 1024 or NATIVE_LABEL in raw:
        raise CustodyError('native-source-installer-loaded-source-preserved')
    processes = bridge.command(['/bin/ps', '-axo', 'pid=,uid='], timeout=10)
    if not processes.strip() or len(processes) > 1024 * 1024:
        raise CustodyError('native-source-installer-process-unavailable')
    seen = set()
    for line in processes.splitlines():
        match = re.fullmatch(r'\s*([1-9][0-9]*)\s+(-?[0-9]+)\s*', line)
        if match is None or int(match[1]) in seen or uid is not None and int(match[2]) == uid:
            raise CustodyError('native-source-installer-process-preserved')
        seen.add(int(match[1]))
    kit = None
    if os.path.lexists(NATIVE_KIT):
        with protected_directory(NATIVE_KIT.parent) as parent:
            kit_fd = os.open(NATIVE_KIT.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(kit_fd)
                if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o755):
                    raise CustodyError('native-source-installer-kit-unsafe')
                _native_no_acl(kit_fd)
                files = {}
                for name in os.listdir(kit_fd):
                    if name not in {'source-kit.json', 'broker.py', 'source-broker.env', *NATIVE_PROFILES}:
                        raise CustodyError('native-source-installer-kit-preserved')
                    body, inode = _native_root_file(kit_fd, name, mode=0o755 if name == 'broker.py' else 0o644, limit=2 * 1024 * 1024)
                    files[name] = {'inode': inode, 'sha256': hashlib.sha256(body).hexdigest()}
                kit = {'inode': [info.st_dev, info.st_ino], 'files': files}
            finally:
                os.close(kit_fd)
        if qualified_runtime is not None:
            from .native_executor import _publisher_current
            selection, publisher, (_module, _doc, component, _ref) = _publisher_current(bridge)
            if _doc['sourceRoot'] != qualified_runtime['sourceRoot']:
                raise CustodyError('native-source-installer-kit-unqualified')
            if any(name != 'source-kit.json' and image['sha256'] != hashlib.sha256(component[name]).hexdigest()
                    for name, image in kit['files'].items()):
                raise CustodyError('native-source-installer-kit-unqualified')
            native_verify_kit(selection)
            if 'source-publication.json' in images:
                # Code-only custody survives boot. The fresh census above is
                # independent of the historical publication witness; it grants
                # no state/account/job or current generation authority.
                files = publisher._publication_files(component, _ref, selection.deployment.checksum)
                with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_KIT.parent) as parent:
                    publication, _raw = _native_json_at(metadata, 'source-publication.json')
                    publisher._publication_record(publication, selection.deployment.checksum, files)
                    if publication['phase'] != 'prepared-no-jobs' or publication['bindings'] != {}:
                        raise CustodyError('native-source-installer-code-publication-invalid')
                    publisher._kit_image(globals_module(), parent, NATIVE_KIT.name, expected={
                        'inode': publication['candidateInode'], 'files': {name: {
                            'inode': publication['stagedFiles'][name]['inode'], 'sha256': hashlib.sha256(body).hexdigest()}
                            for name, body in files.items()}})
    if kit is None and 'source-publication.json' in images and _installer_publication is None and _installer_code_only is None:
        raise CustodyError('native-source-installer-kit-missing')
    witness = {'boot': _native_boot_uuid(), 'loadedSourceJobs': 0, 'brokerProcesses': 0, 'brokerUid': uid}
    current_owner = _native_installer_census_owner(bridge)
    if (current_owner.pw_name, current_owner.pw_uid, current_owner.pw_gid, current_owner.pw_dir) != (
            owner.pw_name, owner.pw_uid, owner.pw_gid, owner.pw_dir):
        raise CustodyError('native-source-installer-owner-changed')
    return kit, images, identity_view, witness


def _native_installer_code_only_names(record):
    nonce = record['nonce']
    return {'metadata': {'source-installer-code-only.json',
            '.source-code-only-publication-before-' + nonce + '.json',
            '.source-code-only-publication-next-' + nonce,
            '.source-code-only-sidefiles-before-' + nonce + '.json',
            '.source-code-only-sidefiles-next-' + nonce},
        'kits': {record['candidate'], record['backup']}}


def _native_installer_history_record(value):
    """Pure bounded metadata-retention ledger, never generation authority."""
    if (type(value) is not dict or set(value) != {'schemaVersion', 'kind', 'entries'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['kind'] != 'ods-native-source-installer-history'
            or type(value['entries']) is not list or not 1 <= len(value['entries']) <= 64):
        raise CustodyError('native-source-installer-history-invalid')
    seen, artifacts = set(), set()
    for index, entry in enumerate(value['entries']):
        if (type(entry) is not dict or set(entry) != {'transaction', 'runtime', 'owner', 'boot', 'phase', 'artifacts'}
                or not _native_installer_transaction_value(entry['transaction']) or entry['transaction']['transaction'] in seen
                or entry['phase'] not in ('pending', 'committed') or index != len(value['entries']) - 1 and entry['phase'] != 'committed'
                or type(entry['boot']) is not str or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', entry['boot'])
                or type(entry['owner']) is not dict or set(entry['owner']) != {'uid', 'gid'}
                or any(type(item) is not int for item in entry['owner'].values()) or entry['owner']['uid'] <= 0 or entry['owner']['gid'] < 0
                or type(entry['runtime']) is not dict or set(entry['runtime']) != {'deploymentSha256', 'custodySha256', 'bundleSha256', 'sourceRef'}
                or any(type(entry['runtime'][key]) is not str or not _NATIVE_SHA.fullmatch(entry['runtime'][key])
                    for key in ('deploymentSha256', 'custodySha256', 'bundleSha256'))
                or entry['runtime']['bundleSha256'] != entry['transaction']['currentDigest']
                or type(entry['runtime']['sourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', entry['runtime']['sourceRef'])
                or type(entry['artifacts']) is not dict or not entry['artifacts']
                or set(entry['artifacts']) - {'source-installer-rebind.json', 'source-installer-code-only.json'}):
            raise CustodyError('native-source-installer-history-invalid')
        seen.add(entry['transaction']['transaction'])
        for fixed, image in entry['artifacts'].items():
            if (type(image) is not dict or set(image) != {'transaction', 'name', 'inode', 'sha256'}
                    or not _native_installer_transaction_value(image['transaction'])
                    or entry['runtime']['bundleSha256'] not in (image['transaction']['currentDigest'], image['transaction']['candidateDigest'])
                    or image['name'] != fixed[:-5] + '-' + image['transaction']['transaction'] + '.json'
                    or image['name'] in artifacts or not _native_installer_history_image(image)):
                raise CustodyError('native-source-installer-history-invalid')
            artifacts.add(image['name'])
    return value


def _native_installer_history_image(image):
    return _native_installer_image({key: image[key] for key in ('inode', 'sha256')})


def _native_installer_history_artifact(bridge, publisher, metadata, kits, fixed, image, runtime):
    """Qualify one exact committed record and its retained before-images."""
    current, retained = _native_at_inode(metadata, fixed), _native_at_inode(metadata, image['name'])
    if current == image['inode']:
        if retained is not None:
            raise CustodyError('native-source-installer-history-conflict')
        name = fixed
    else:
        if retained != image['inode']:
            raise CustodyError('native-source-installer-history-missing')
        name = image['name']
    body, inode = _native_root_file(metadata, name, limit=65536)
    if inode != image['inode'] or hashlib.sha256(body).hexdigest() != image['sha256']:
        raise CustodyError('native-source-installer-history-changed')
    value = json.loads(body, object_pairs_hook=_native_pairs)
    transaction = image['transaction']
    mode = 'withdrawal' if fixed == 'source-installer-rebind.json' else 'absent'
    context, context_raw = _native_installer_context(bridge, transaction, mode=mode)
    journal, records, journal_raw = _native_installer_journal(metadata, transaction, context, _history=True)
    if (journal['phase'] not in ('active', 'restored') or value.get('phase') != 'committed'
            or value.get('transaction') != transaction):
        raise CustodyError('native-source-installer-history-uncommitted')
    if fixed == 'source-installer-rebind.json':
        publisher._installer_rebind_record(globals_module(), value)
        receipt = journal['sourceAddition'].get('receipt')
        if (receipt is None or value['receiptSha256'] != hashlib.sha256(_native_encoded(receipt)).hexdigest()
                or value['intentSha256'] != receipt['intentSha256']
                or value['binding']['deploymentSha256'] != runtime['deploymentSha256']
                or value['binding']['kitSha256'] != value['kitSha256']):
            raise CustodyError('native-source-installer-history-binding-changed')
        # Rebind deliberately stores the BASE environment binding. Its actual
        # target runtime is independently retained in the Core transaction;
        # do not invent full-runtime fields on that base binding.
        from pixel_provider.runtime_custody import digest, tree_manifest
        files = {item['path']: item for item in records}
        which = 'after' if journal['phase'] == 'active' else 'before'
        descriptor = json.loads(files[str(NATIVE_RUNTIME_DESCRIPTOR)][which], object_pairs_hook=_native_pairs)
        manifest_raw = files[str(NATIVE_RUNTIME_MANIFEST)][which]
        manifest = json.loads(manifest_raw, object_pairs_hook=_native_pairs)
        bundle_sha = transaction['candidateDigest'] if which == 'after' else transaction['currentDigest']
        root = NATIVE_RUNTIMES / bundle_sha
        if (runtime['bundleSha256'] != bundle_sha or digest([descriptor, manifest]) != runtime['custodySha256']
                or descriptor['runtimeRoot'] != str(root / 'runtime') or descriptor['sourceRoot'] != str(root / 'ods-source')
                or descriptor['manifest'] != str(NATIVE_RUNTIME_MANIFEST)
                or descriptor['manifestSha256'] != hashlib.sha256(manifest_raw).hexdigest()
                or tree_manifest(descriptor['sourceRoot']) != manifest['source']):
            raise CustodyError('native-source-installer-history-binding-changed')
        component = publisher.bundle_payloads(root, ref=runtime['sourceRef'], python=descriptor['hostPython'], protected=True)
        payloads = publisher._publication_files(component, runtime['sourceRef'], runtime['deploymentSha256'])
        published = value['publication']['after']
        publisher._publication_record(published, runtime['deploymentSha256'], payloads)
        if (published['sourceRef'] != runtime['sourceRef'] or published['phase'] != 'prepared-no-jobs'
                or published['old'] is not None or published['bindings']
                or hashlib.sha256(payloads['source-kit.json']).hexdigest() != value['kitSha256']):
            raise CustodyError('native-source-installer-history-binding-changed')
        pub_name = '.source-publication-before-' + value['nonce'] + '.json'
        metadata_names = {pub_name}
        for index, (_leaf, binding) in enumerate(sorted(value['bindings'].items())):
            saved = '.source-binding-before-' + value['nonce'] + '-' + str(index)
            if _native_root_file(metadata, saved, mode=binding['mode'], gid=binding['gid']) != (
                    _native_encoded(binding['before']), binding['beforeInode']):
                raise CustodyError('native-source-installer-history-before-changed')
            if _native_at_inode(metadata, '.source-binding-' + value['nonce'] + '-' + str(index)) is not None:
                raise CustodyError('native-source-installer-history-uncommitted')
            metadata_names.add(saved)
        parent = 'source-environment-generation-' + receipt['withdrawal'] + '.json'
        old, located = _native_root_file(metadata, parent, limit=65536)
        if located != value['withdrawalParent']['inode'] or hashlib.sha256(old).hexdigest() != value['withdrawalParent']['sha256']:
            raise CustodyError('native-source-installer-history-before-changed')
        metadata_names.add(parent)
    else:
        publisher._installer_code_only_record(globals_module(), value)
        proof = journal['sourceAddition']['proof']
        if (journal['phase'] != 'active' or value['proofSha256'] != hashlib.sha256(_native_encoded(proof)).hexdigest()
                or value['oldKit'] != proof['kit'] or value['deploymentSha256'] != runtime['deploymentSha256']
                or value['sourceRef'] != runtime['sourceRef']):
            raise CustodyError('native-source-installer-history-binding-changed')
        pub_name = '.source-code-only-publication-before-' + value['nonce'] + '.json'
        side_name = '.source-code-only-sidefiles-before-' + value['nonce'] + '.json'
        if _native_root_file(metadata, side_name) != (_native_encoded(value['sidefiles']['before']), value['sidefiles']['beforeInode']):
            raise CustodyError('native-source-installer-history-before-changed')
        metadata_names = {pub_name, side_name}
    if _native_root_file(metadata, pub_name) != (_native_encoded(value['publication']['before']), value['publication']['beforeInode']):
        raise CustodyError('native-source-installer-history-before-changed')
    publisher._kit_image(globals_module(), kits, value['backup'], expected=value['oldKit'])
    if _native_at_inode(kits, value['candidate']) is not None:
        raise CustodyError('native-source-installer-history-uncommitted')
    for label in ('publication', 'sidefiles') if fixed == 'source-installer-code-only.json' else ('publication',):
        stem = '.source-code-only-' + label + '-next-' if fixed == 'source-installer-code-only.json' else '.source-rebind-publication-'
        if _native_at_inode(metadata, stem + value['nonce']) is not None:
            raise CustodyError('native-source-installer-history-uncommitted')
    if (_native_root_file(metadata, name, limit=65536) != (body, inode)
            or _native_installer_context(bridge, transaction, mode=mode)[1] != context_raw
            or _native_root_file(metadata, _native_installer_journal_name(metadata, transaction, _history=True), limit=32 * 1024 * 1024)[0] != journal_raw):
        raise CustodyError('native-source-installer-history-changed')
    return {'name': name, 'fixed': fixed, 'document': value, 'metadata': metadata_names, 'kits': {value['backup']}}


def _native_installer_history_records(bridge):
    """File-only history across mixed installer images, never live authority."""
    from pixel_macos_custody import protected_directory
    owner = _native_installer_owner(bridge)
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_KIT.parent) as kits:
        ledger, raw = _native_json_at(metadata, 'source-installer-history.json')
        if ledger is None:
            return []
        _native_installer_history_record(ledger)
        result = []
        for entry in ledger['entries']:
            if entry['owner'] != {'uid': owner.pw_uid, 'gid': owner.pw_gid}:
                raise CustodyError('native-source-installer-history-owner-changed')
            try:
                context, _raw = _native_installer_context(bridge, entry['transaction'], mode='withdrawal')
            except CustodyError:
                context, _raw = _native_installer_context(bridge, entry['transaction'], mode='absent')
            journal, records, _body = _native_installer_journal(metadata, entry['transaction'], context, _history=True)
            if journal['phase'] not in ('prepared', 'replacing-files', 'active', 'restored', 'rolling-back',
                    *(action + '-' + role for action in ('stopping', 'starting') for role in (
                        'integrations', 'operations', 'promoter', 'manager', 'gateway', 'access', 'relay'))):
                raise CustodyError('native-source-installer-history-transaction-unsettled')
            original = journal['sourceAddition']['proof']
            custody_sha = (original['runtime']['custodySha256'] if context['source_lifecycle']['mode'] == 'withdrawal'
                else original['oldRuntimeCustodySha256'])
            if custody_sha != entry['runtime']['custodySha256']:
                raise CustodyError('native-source-installer-history-runtime-changed')
            publisher = _native_installer_old_publisher({'transaction': entry['transaction'], 'files': records,
                'proof': {'runtime': {'custodySha256': custody_sha}}}, _bridge=bridge)
            for fixed, image in entry['artifacts'].items():
                item = _native_installer_history_artifact(bridge, publisher, metadata, kits, fixed, image, entry['runtime'])
                if entry['phase'] == 'committed' and item['name'] != image['name']:
                    raise CustodyError('native-source-installer-history-uncommitted')
                result.append(item)
        if _native_json_at(metadata, 'source-installer-history.json')[1] != raw:
            raise CustodyError('native-source-installer-history-changed')
        return result


def native_installer_history_records(selection):
    """Actual current selection plus separately qualified retained data."""
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_META) as metadata:
        if _native_at_inode(metadata, 'source-installer-history.json') is None:
            return []  # Absence grants no names, file or runtime authority.
    _native_selected(selection, _publication=True)
    result = _native_installer_history_records(selection.bridge)
    _native_selected(selection, _publication=True)
    return result


def native_installer_history(selection):
    from pixel_macos_custody import protected_directory
    extra = set()
    with protected_directory(NATIVE_META) as metadata:
        if _native_at_inode(metadata, _SIDEFILE_ARCHIVES) is not None:
            doc = _native_selected(selection, _publication=True)
            extra = _native_sidefile_archive_names(native_sidefile_archives(
                owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid']))
    items = native_installer_history_records(selection)
    if not items:
        return {'metadata': extra, 'kits': set()}
    return {'metadata': {*extra, 'source-installer-history.json', *(item['name'] for item in items),
            *(name for item in items for name in item['metadata'])},
        'kits': {name for item in items for name in item['kits']}}


def native_installer_history_plan(selection, *, transaction):
    """Admit metadata rollover only before the actual installer replaces code."""
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    publisher = _publisher(selection)
    _module, doc, _component, ref = publisher._material(selection)
    if not _native_installer_transaction_value(transaction) or doc['bundleSha256'] != transaction['currentDigest']:
        raise CustodyError('native-source-installer-history-old-runtime-required')
    try:
        context, context_raw = _native_installer_context(selection.bridge, transaction, mode='withdrawal')
    except CustodyError:
        context, context_raw = _native_installer_context(selection.bridge, transaction, mode='absent')
    runtime = {'deploymentSha256': selection.deployment.checksum, 'custodySha256': doc['runtimeCustodySha256'],
        'bundleSha256': doc['bundleSha256'], 'sourceRef': ref}
    owner = {'uid': doc['ownerUid'], 'gid': doc['ownerGid']}
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_KIT.parent) as kits:
        journal, records, journal_raw = _native_installer_journal(metadata, transaction, context)
        if journal['phase'] != 'prepared':
            raise CustodyError('native-source-installer-history-before-code-required')
        # A mixed or already-candidate Root image is not this old-runtime
        # admission boundary. It cannot turn a prior completed record into an
        # authorization for arbitrary file replacement.
        for image in records:
            if image['path'] not in native_installer_source_family():
                continue
            path = Path(image['path'])
            with protected_directory(path.parent) as directory:
                try:
                    body, _inode = _native_root_file(directory, path.name, mode=image['mode'], gid=image['gid'], limit=8 * 1024 * 1024)
                except FileNotFoundError:
                    body = None
                if body != image['before']:
                    raise CustodyError('native-source-installer-history-before-code-required')
        ledger, raw = _native_json_at(metadata, 'source-installer-history.json')
        retained = _native_installer_history_records(selection.bridge) if ledger is not None else []
        if ledger is not None:
            _native_installer_history_record(ledger)
        if context['source_lifecycle']['mode'] == 'withdrawal':
            actual = publisher.installer_withdrawal_intent(selection, transaction=transaction)
            if _native_encoded(actual) != _native_encoded(journal['sourceAddition']['proof']):
                raise CustodyError('native-source-installer-history-origin-changed')
            native_retirement_inventory(selection)  # Fresh stopped DS/env/token metadata and literal origin.
        else:
            actual = native_absent_for_installer(selection.bridge, transaction=transaction)
            if _native_encoded(actual) != _native_encoded(journal['sourceAddition']['proof']):
                raise CustodyError('native-source-installer-history-origin-changed')
        witness = publisher._no_actors(selection, globals_module(), metadata, _generation=True)
        entry = None
        if ledger is not None and ledger['entries'][-1]['transaction'] == transaction:
            entry = ledger['entries'][-1]
            if entry['runtime'] != runtime or entry['owner'] != owner:
                raise CustodyError('native-source-installer-history-origin-changed')
        elif ledger is not None and ledger['entries'][-1]['phase'] != 'committed':
            raise CustodyError('native-source-installer-history-pending')
        artifacts = {} if entry is None else entry['artifacts']
        if entry is None:
            for fixed in ('source-installer-rebind.json', 'source-installer-code-only.json'):
                if _native_at_inode(metadata, fixed) is None:
                    continue
                body, inode = _native_root_file(metadata, fixed, limit=65536)
                value = json.loads(body, object_pairs_hook=_native_pairs)
                old = value.get('transaction') if type(value) is dict else None
                if not _native_installer_transaction_value(old) or old == transaction:
                    raise CustodyError('native-source-installer-history-origin-changed')
                image = {'transaction': old, 'name': fixed[:-5] + '-' + old['transaction'] + '.json',
                    'inode': inode, 'sha256': hashlib.sha256(body).hexdigest()}
                _native_installer_history_artifact(selection.bridge, publisher, metadata, kits, fixed, image, runtime)
                artifacts[fixed] = image
            if artifacts:
                entry = {'transaction': dict(transaction), 'runtime': runtime, 'owner': owner,
                    'boot': witness['boot'], 'phase': 'pending', 'artifacts': artifacts}
                prospective = {'schemaVersion': 1, 'kind': 'ods-native-source-installer-history',
                    'entries': ([] if ledger is None else ledger['entries']) + [entry]}
                _native_installer_history_record(prospective)
        # Every pending physical prefix is qualified from the same frozen
        # original before-image. A retained record cannot become current again.
        for fixed, image in artifacts.items():
            _native_installer_history_artifact(selection.bridge, publisher, metadata, kits, fixed, image, runtime)
        if (_native_json_at(metadata, 'source-installer-history.json')[1] != raw
                or _native_root_file(metadata, 'runtime-upgrade.json', limit=32 * 1024 * 1024)[0] != journal_raw
                or _native_installer_context(selection.bridge, transaction, mode=context['source_lifecycle']['mode'])[1] != context_raw
                or publisher._no_actors(selection, globals_module(), metadata, _generation=True) != witness):
            raise CustodyError('native-source-installer-history-changed')
        if retained and ledger is None:
            raise CustodyError('native-source-installer-history-changed')
        return {'ledger': ledger, 'entry': entry, 'records': artifacts, 'witness': witness}


def _native_installer_code_only_record(metadata, transaction, proof):
    record, raw = _native_json_at(metadata, 'source-installer-code-only.json')
    if record is None:
        return None, None
    fields = {'schemaVersion', 'kind', 'transaction', 'proofSha256', 'nonce', 'phase', 'deploymentSha256',
        'sourceRef', 'kitSha256', 'kitParent', 'oldKit', 'candidate', 'backup', 'candidateInode',
        'stagedFiles', 'publication', 'sidefiles', 'witness'}
    if (type(record) is not dict or set(record) != fields or type(record['schemaVersion']) is not int
            or record['schemaVersion'] != 1 or record['kind'] != 'ods-native-source-installer-code-only'
            or record['transaction'] != transaction or record['proofSha256'] != hashlib.sha256(_native_encoded(proof)).hexdigest()
            or type(record['nonce']) is not str or not FLOW_ID.fullmatch(record['nonce'])
            or record['phase'] not in ('staging', 'kit', 'publication', 'sidefiles', 'committed')
            or any(type(record[key]) is not str or not _NATIVE_SHA.fullmatch(record[key]) for key in ('deploymentSha256', 'kitSha256'))
            or type(record['sourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', record['sourceRef'])
            or not _native_inode(record['kitParent']) or record['oldKit'] != proof['kit']
            or record['candidate'] != '.source-kit-' + record['nonce'] or record['backup'] != '.source-kit-before-' + record['nonce']
            or record['candidateInode'] is not None and not _native_inode(record['candidateInode'])
            or type(record['stagedFiles']) is not dict or set(record['stagedFiles']) != {'source-kit.json', 'broker.py', 'source-broker.env', *NATIVE_PROFILES}):
        raise CustodyError('native-source-installer-code-only-record-invalid')
    for image in record['stagedFiles'].values():
        if (type(image) is not dict or set(image) != {'inode', 'ready'} or type(image['ready']) is not bool
                or image['inode'] is not None and not _native_inode(image['inode']) or image['ready'] and image['inode'] is None):
            raise CustodyError('native-source-installer-code-only-record-invalid')
    for key, name in (('publication', 'source-publication.json'), ('sidefiles', 'source-sidefiles.json')):
        image = record[key]
        if (type(image) is not dict or set(image) != {'before', 'beforeInode', 'after', 'afterInode', 'ready'}
                or type(image['before']) is not dict or not _native_inode(image['beforeInode'])
                or proof['metadata'].get(name) != {'inode': image['beforeInode'], 'sha256': hashlib.sha256(_native_encoded(image['before'])).hexdigest()}
                or image['after'] is not None and type(image['after']) is not dict
                or key == 'sidefiles' and image['after'] is None
                or image['afterInode'] is not None and not _native_inode(image['afterInode'])
                or type(image['ready']) is not bool or image['ready'] and (image['afterInode'] is None or image['after'] is None)):
            raise CustodyError('native-source-installer-code-only-record-invalid')
    witness = record['witness']
    uid = proof['identity'].get('record', {}).get('id')
    if (type(witness) is not dict or set(witness) != {'kind', 'boot', 'loadedSourceJobs', 'brokerProcesses', 'brokerUid'}
            or witness['kind'] != 'never-started-observed' or type(witness['boot']) is not str
            or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', witness['boot'])
            or any(type(witness[key]) is not int or witness[key] != 0 for key in ('loadedSourceJobs', 'brokerProcesses'))
            or type(witness['brokerUid']) is not type(uid) or witness['brokerUid'] != uid):
        raise CustodyError('native-source-installer-code-only-record-invalid')
    return record, raw


def native_installer_code_only_kit_authority(selection, *, transaction, proof, _completed=False, _with_publisher=False):
    """Actual installer-purpose authority, never private Source admission."""
    if type(_with_publisher) is not bool:
        raise CustodyError('native-source-installer-code-only-target-changed')
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    # _publisher qualifies the selected deployment and then independently
    # qualifies its complete material. Reuse that exact material here instead
    # of traversing both protected runtime trees a second time.
    publisher, material = _publisher(selection, with_material=True)
    _module, doc, component, ref = material
    if doc != selection.deployment.document:
        raise CustodyError('native-source-installer-code-only-target-changed')
    context, context_raw = _native_installer_context(selection.bridge, transaction)
    native_installer_absence_receipt(proof, transaction=transaction, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'])
    if (proof['kit'] is None or not {'source-publication.json', 'source-sidefiles.json'} <= set(proof['metadata'])
            or doc['bundleSha256'] != transaction['candidateDigest']):
        raise CustodyError('native-source-installer-code-only-original-state-invalid')
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_KIT.parent) as kits:
        journal, records, journal_raw = _native_installer_journal(metadata, transaction, context, _completed=_completed)
        if journal['phase'] != 'active' or journal['sourceAddition']['proof'] != proof:
            raise CustodyError('native-source-installer-code-only-target-not-active')
        _native_installer_live_images(records)
        for item in records:
            if item['path'] in native_installer_source_family():
                path = Path(item['path'])
                with protected_directory(path.parent) as directory:
                    if _native_root_file(directory, path.name, mode=item['mode'], gid=item['gid'], limit=8 * 1024 * 1024)[0] != item['after']:
                        raise CustodyError('native-source-installer-code-only-file-image-changed')
        record, record_raw = _native_installer_code_only_record(metadata, transaction, proof)
        before = {}
        for key, name in (('publication', 'source-publication.json'), ('sidefiles', 'source-sidefiles.json')):
            body, inode = (_native_root_file(metadata, name) if record is None
                else (_native_encoded(record[key]['before']), record[key]['beforeInode']))
            if proof['metadata'].get(name) != {'inode': inode, 'sha256': hashlib.sha256(body).hexdigest()}:
                raise CustodyError('native-source-installer-code-only-original-image-changed')
            before[key] = {'before': body, 'inode': inode}
        old_publication = json.loads(before['publication']['before'], object_pairs_hook=_native_pairs)
        old_location = NATIVE_KIT.name
        if record is not None and _native_at_inode(kits, old_location) != proof['kit']['inode']:
            old_location = record['backup']
        _old_kit, old_files = publisher._kit_image(globals_module(), kits, old_location, expected=proof['kit'])
        old_manifest = json.loads(old_files['source-kit.json'], object_pairs_hook=_native_pairs)
        if (type(old_manifest) is not dict or old_manifest.get('schemaVersion') != 2
                or old_manifest.get('kind') != 'ods-native-source-kit'
                or old_publication.get('phase') != 'prepared-no-jobs' or old_publication.get('old') is not None
                or old_publication.get('bindings') != {} or old_manifest.get('sourceRef') != old_publication.get('sourceRef')
                or old_manifest.get('deploymentSha256') != old_publication.get('deploymentSha256')):
            raise CustodyError('native-source-installer-code-only-publication-invalid')
        old_authority = {'transaction': transaction, 'files': records,
            'proof': {'runtime': {'custodySha256': proof['oldRuntimeCustodySha256']}}}
        old_publisher = _native_installer_old_publisher(old_authority, _bridge=selection.bridge)
        descriptor = json.loads(next(item['before'] for item in records if item['path'] == str(NATIVE_RUNTIME_DESCRIPTOR)), object_pairs_hook=_native_pairs)
        old_component = old_publisher.bundle_payloads(Path(proof['runtimeRoot']).parent,
            ref=old_publication['sourceRef'], python=descriptor['hostPython'], protected=True)
        old_publisher._publication_record(old_publication, old_publication['deploymentSha256'],
            old_publisher._publication_files(old_component, old_publication['sourceRef'], old_publication['deploymentSha256']))
        if (old_publication['candidateInode'] != proof['kit']['inode']
                or any(old_publication['stagedFiles'][name] != {'inode': image['inode'], 'ready': True}
                    for name, image in proof['kit']['files'].items())
                or any(old_publication['new'][name] != image['sha256'] for name, image in proof['kit']['files'].items())
                or old_publication['witness']['brokerUid'] is not None
                    and old_publication['witness']['brokerUid'] != proof['identity'].get('record', {}).get('id')):
            raise CustodyError('native-source-installer-code-only-publication-invalid')
        state = _native_installer_absence_state(selection.bridge, _installer_code_only=record)
        if state[2] != proof['identity'] or state[1].get('source-identity.json') != proof['metadata'].get('source-identity.json'):
            raise CustodyError('native-source-installer-code-only-identity-changed')
        if any(state[1].get(name) != image for name, image in proof['metadata'].items()
                if name not in {'source-publication.json', 'source-sidefiles.json', 'source-runtime-installation.json'}):
            raise CustodyError('native-source-installer-code-only-metadata-changed')
        witness = dict(state[3], kind='never-started-observed')
        if record is not None:
            if record['witness'] != dict(witness, boot=record['witness']['boot']):
                raise CustodyError('native-source-installer-code-only-identity-changed')
            if record['witness']['boot'] != witness['boot']:
                _native_installer_hold(metadata, transaction)
            witness = record['witness']  # Historical observation never renewed.
        old_runtime = {'deploymentSha256': old_publication['deploymentSha256'], 'custodySha256': proof['oldRuntimeCustodySha256'],
            'bundleSha256': transaction['currentDigest'], 'sourceRef': old_publication['sourceRef']}
        snapshot = context['runtime_bundle'].get('source_sidefiles_before')
        if snapshot is None:
            raise CustodyError('native-source-installer-code-only-sidefiles-snapshot-required')
        native_installer_sidefiles_receipt(snapshot, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'], runtime=old_runtime)
        sidefiles = json.loads(before['sidefiles']['before'], object_pairs_hook=_native_pairs)
        after = _native_installer_sidefiles(doc, context, sidefiles, transaction, old_runtime=old_runtime,
            before_image=proof['metadata']['source-sidefiles.json'])
        before['sidefiles']['after'] = _native_encoded(after)
        files = publisher._publication_files(component, ref, selection.deployment.checksum)
        info = os.fstat(kits)
        _native_no_acl(kits)
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o755):
            raise CustodyError('native-source-installer-code-only-kit-parent-unsafe')
        kit_parent = [info.st_dev, info.st_ino]
        if record is not None and (record['deploymentSha256'] != selection.deployment.checksum or record['sourceRef'] != ref
                or record['kitSha256'] != hashlib.sha256(files['source-kit.json']).hexdigest() or record['kitParent'] != kit_parent
                or _native_encoded(record['sidefiles']['after']) != before['sidefiles']['after']):
            raise CustodyError('native-source-installer-code-only-target-changed')
        if (_native_json_at(metadata, 'source-installer-code-only.json')[1] != record_raw
                or _native_root_file(metadata, _native_installer_journal_name(metadata, transaction, completed=_completed), limit=32 * 1024 * 1024)[0] != journal_raw
                or _native_installer_context(selection.bridge, transaction)[1] != context_raw):
            raise CustodyError('native-source-installer-code-only-authority-changed')
        result = {'authority': {'transaction': dict(transaction), 'proof': proof}, 'record': record,
            'oldKit': proof['kit'], 'kitParent': kit_parent, 'targetKit': files,
            'publication': before['publication'], 'sidefiles': before['sidefiles'], 'witness': witness,
            'history': native_installer_history(selection)}
        return (result, publisher) if _with_publisher else result


def _native_installer_code_only_prefix(publisher, metadata, kits, plan, record):
    files = plan['targetKit']
    known = {NATIVE_KIT.name, record['candidate'], record['backup'], *plan['history']['kits']}
    if any(name.startswith('.source-kit-') and name not in known for name in os.listdir(kits)):
        raise CustodyError('native-source-installer-code-only-unknown-prefix')
    live, backup = _native_at_inode(kits, NATIVE_KIT.name), _native_at_inode(kits, record['backup'])
    staged = _native_at_inode(kits, record['candidate'])
    if live == record['oldKit']['inode']:
        if backup is not None or record['phase'] not in ('staging', 'kit'):
            raise CustodyError('native-source-installer-code-only-prefix-changed')
        target, target_name = staged, record['candidate']
    else:
        if backup != record['oldKit']['inode'] or record['phase'] == 'staging':
            raise CustodyError('native-source-installer-code-only-prefix-changed')
        if live is not None and staged is not None:
            raise CustodyError('native-source-installer-code-only-prefix-changed')
        target, target_name = (live, NATIVE_KIT.name) if live is not None else (staged, record['candidate'])
    if target is None:
        if record['candidateInode'] is not None:
            raise CustodyError('native-source-installer-code-only-prefix-missing')
    else:
        if target != record['candidateInode']:
            raise CustodyError('native-source-installer-code-only-prefix-changed')
        directory = os.open(target_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=kits)
        try:
            info = os.fstat(directory)
            _native_no_acl(directory)
            if ((info.st_uid, info.st_gid) != (0, 0) or stat.S_IMODE(info.st_mode) not in (0o700, 0o755)
                    or set(os.listdir(directory)) - set(files)):
                raise CustodyError('native-source-installer-code-only-prefix-changed')
            for name, body in files.items():
                image = record['stagedFiles'][name]
                publisher._generation_prefix(globals_module(), directory, name, body, image, mode=0o755 if name == 'broker.py' else 0o644)
                if image['inode'] is not None and _native_at_inode(directory, name) != image['inode']:
                    raise CustodyError('native-source-installer-code-only-prefix-missing')
            if target_name == NATIVE_KIT.name and (stat.S_IMODE(info.st_mode) != 0o755
                    or set(os.listdir(directory)) != set(files) or any(not image['ready'] for image in record['stagedFiles'].values())):
                raise CustodyError('native-source-installer-code-only-kit-unconfirmed')
        finally:
            os.close(directory)
    if record['phase'] in ('publication', 'sidefiles', 'committed') and live != record['candidateInode']:
        raise CustodyError('native-source-installer-code-only-kit-unconfirmed')
    publication = {'schemaVersion': 1, 'kind': 'ods-native-source-publication', 'deploymentSha256': record['deploymentSha256'],
        'sourceRef': record['sourceRef'], 'nonce': record['nonce'], 'phase': 'prepared-no-jobs', 'old': None,
        'new': {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
        'candidate': record['candidate'], 'backup': record['backup'], 'candidateInode': record['candidateInode'],
        'stagedFiles': record['stagedFiles'], 'bindings': {}, 'witness': record['witness']}
    if (record['publication']['after'] is not None and _native_encoded(record['publication']['after']) != _native_encoded(publication)
            or record['phase'] != 'staging' and record['publication']['after'] is None):
        raise CustodyError('native-source-installer-code-only-publication-changed')
    phases = ('staging', 'kit', 'publication', 'sidefiles', 'committed')
    for key, leaf in (('publication', 'source-publication.json'), ('sidefiles', 'source-sidefiles.json')):
        image = record[key]
        current = _native_at_inode(metadata, leaf)
        temporary = '.source-code-only-' + key + '-next-' + record['nonce']
        retained = '.source-code-only-' + key + '-before-' + record['nonce'] + '.json'
        boundary = phases.index(key)
        phase = phases.index(record['phase'])
        after = _native_encoded(publication) if key == 'publication' else plan['sidefiles']['after']
        if current == image['beforeInode']:
            if (_native_root_file(metadata, leaf) != (plan[key]['before'], image['beforeInode'])
                    or _native_at_inode(metadata, retained) is not None or phase > boundary):
                raise CustodyError('native-source-installer-code-only-before-changed')
        else:
            if (phase < boundary or _native_root_file(metadata, retained) != (plan[key]['before'], image['beforeInode'])
                    or current is not None and (not image['ready'] or current != image['afterInode']
                        or _native_root_file(metadata, leaf) != (after, current))):
                raise CustodyError('native-source-installer-code-only-before-changed')
        if phase > boundary and current != image['afterInode']:
            raise CustodyError('native-source-installer-code-only-after-missing')
        publisher._generation_prefix(globals_module(), metadata, temporary, after,
            {'inode': image['afterInode'], 'ready': image['ready']})
        staged = _native_at_inode(metadata, temporary)
        if (current == image['afterInode'] and image['afterInode'] is not None and staged is not None
                or current != image['afterInode'] and image['afterInode'] is not None and staged != image['afterInode']):
            raise CustodyError('native-source-installer-code-only-prefix-missing')


def native_installer_code_only_kit_plan(selection, *, transaction, proof, _completed=False):
    from pixel_macos_custody import protected_directory
    plan, publisher = native_installer_code_only_kit_authority(selection, transaction=transaction, proof=proof,
        _completed=_completed, _with_publisher=True)
    # Recheck the protected selection after authority and before prefix reads.
    _native_selected(selection, _publication=True)
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_KIT.parent) as kits:
        record, raw = _native_installer_code_only_record(metadata, transaction, proof)
        if record is None:
            if any(name.startswith('.source-kit-') and name not in plan['history']['kits'] for name in os.listdir(kits)):
                raise CustodyError('native-source-installer-code-only-unknown-prefix')
        else:
            _native_installer_code_only_prefix(publisher, metadata, kits, plan, record)
        if _native_json_at(metadata, 'source-installer-code-only.json')[1] != raw:
            raise CustodyError('native-source-installer-code-only-record-changed')
        return plan


def native_verify_completed_installer_code_only_kit(selection, proof, *, transaction):
    plan = native_installer_code_only_kit_plan(selection, transaction=transaction, proof=proof, _completed=True)
    if plan['record'] is None or plan['record']['phase'] != 'committed':
        raise CustodyError('native-source-installer-code-only-unconfirmed')
    return {'status': 'prepared-no-jobs', 'transaction': dict(transaction),
        'kitSha256': hashlib.sha256(plan['targetKit']['source-kit.json']).hexdigest()}


def native_installer_code_only_history(selection):
    """Names from completed immutable installer history, never live authority."""
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    with protected_directory(NATIVE_META) as metadata:
        if _native_at_inode(metadata, 'source-installer-code-only.json') is None:
            return native_installer_history(selection)
    publisher = _publisher(selection)
    _module, doc, component, ref = publisher._material(selection)
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_KIT.parent) as kits:
        record, raw = _native_json_at(metadata, 'source-installer-code-only.json')
        if record is None:
            return native_installer_history(selection)
        if type(record) is not dict or not _native_installer_transaction_value(record.get('transaction')):
            raise CustodyError('native-source-installer-code-only-history-invalid')
        transaction = record['transaction']
        context, context_raw = _native_installer_context(selection.bridge, transaction)
        journal, records, journal_raw = _native_installer_journal(metadata, transaction, context, _history=True)
        proof = journal['sourceAddition']['proof']
        native_installer_absence_receipt(proof, transaction=transaction, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'])
        if proof['kit'] is None or journal['phase'] != 'active' or doc['bundleSha256'] != transaction['candidateDigest']:
            raise CustodyError('native-source-installer-code-only-history-invalid')
        record, _raw = _native_installer_code_only_record(metadata, transaction, proof)
        files = publisher._publication_files(component, ref, selection.deployment.checksum)
        if (record['phase'] != 'committed' or record['deploymentSha256'] != selection.deployment.checksum
                or record['sourceRef'] != ref or record['kitSha256'] != hashlib.sha256(files['source-kit.json']).hexdigest()
                or _native_at_inode(kits, '.') != record['kitParent']):
            raise CustodyError('native-source-installer-code-only-history-invalid')
        expected_pub = {'schemaVersion': 1, 'kind': 'ods-native-source-publication', 'deploymentSha256': record['deploymentSha256'],
            'sourceRef': ref, 'nonce': record['nonce'], 'phase': 'prepared-no-jobs', 'old': None,
            'new': {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
            'candidate': record['candidate'], 'backup': record['backup'], 'candidateInode': record['candidateInode'],
            'stagedFiles': record['stagedFiles'], 'bindings': {}, 'witness': record['witness']}
        publisher._publication_record(expected_pub, selection.deployment.checksum, files)
        if _native_encoded(record['publication']['after']) != _native_encoded(expected_pub):
            raise CustodyError('native-source-installer-code-only-history-invalid')
        for key in ('publication', 'sidefiles'):
            image = record[key]
            if (not image['ready'] or _native_root_file(metadata, '.source-code-only-' + key + '-before-' + record['nonce'] + '.json') != (
                    _native_encoded(image['before']), image['beforeInode'])
                    or _native_at_inode(metadata, '.source-code-only-' + key + '-next-' + record['nonce']) is not None):
                raise CustodyError('native-source-installer-code-only-history-changed')
        publisher._kit_image(globals_module(), kits, record['backup'], expected=proof['kit'])
        if _native_at_inode(kits, record['candidate']) is not None:
            raise CustodyError('native-source-installer-code-only-history-changed')
        publisher._kit_image(globals_module(), kits, NATIVE_KIT.name, expected={
            'inode': record['candidateInode'], 'files': {name: {'inode': record['stagedFiles'][name]['inode'],
                'sha256': hashlib.sha256(body).hexdigest()} for name, body in files.items()}})
        old_pub = record['publication']['before']
        old_runtime = {'deploymentSha256': old_pub['deploymentSha256'], 'custodySha256': proof['oldRuntimeCustodySha256'],
            'bundleSha256': transaction['currentDigest'], 'sourceRef': old_pub['sourceRef']}
        old_publisher = _native_installer_old_publisher({'transaction': transaction, 'files': records,
            'proof': {'runtime': {'custodySha256': proof['oldRuntimeCustodySha256']}}}, _bridge=selection.bridge)
        descriptor = json.loads(next(item['before'] for item in records if item['path'] == str(NATIVE_RUNTIME_DESCRIPTOR)), object_pairs_hook=_native_pairs)
        old_component = old_publisher.bundle_payloads(Path(proof['runtimeRoot']).parent,
            ref=old_pub['sourceRef'], python=descriptor['hostPython'], protected=True)
        old_publisher._publication_record(old_pub, old_pub['deploymentSha256'],
            old_publisher._publication_files(old_component, old_pub['sourceRef'], old_pub['deploymentSha256']))
        if (old_pub['phase'] != 'prepared-no-jobs' or old_pub['old'] is not None or old_pub['bindings']
                or old_pub['candidateInode'] != proof['kit']['inode']
                or any(old_pub['stagedFiles'][name] != {'inode': image['inode'], 'ready': True}
                    or old_pub['new'][name] != image['sha256'] for name, image in proof['kit']['files'].items())):
            raise CustodyError('native-source-installer-code-only-history-invalid')
        snapshot = context['runtime_bundle'].get('source_sidefiles_before')
        native_installer_sidefiles_receipt(snapshot, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'], runtime=old_runtime)
        before = record['sidefiles']['before']
        fields = {'schemaVersion', 'kind', 'ownerUid', 'ownerGid', 'configPath', 'bundleSha256', 'files', 'parent', 'children'}
        expected_sidefiles = dict(before, configPath=doc['configPath'], bundleSha256=doc['bundleSha256'],
            files={name: image['sha256'] for name, image in snapshot['files'].items()}, parent=snapshot['parent'],
            children={name: {'inode': image['inode'], 'ready': True} for name, image in snapshot['files'].items()})
        if (set(before) != fields or before.get('kind') != 'ods-native-connection-sidefiles'
                or type(before.get('schemaVersion')) is not int or before['schemaVersion'] != 1
                or (before['ownerUid'], before['ownerGid']) != (doc['ownerUid'], doc['ownerGid'])
                or snapshot['bootstrap'] != proof['metadata']['source-sidefiles.json']
                or _native_encoded(record['sidefiles']['after']) != _native_encoded(expected_sidefiles)
                or _native_root_file(metadata, 'source-sidefiles.json') != (_native_encoded(expected_sidefiles), record['sidefiles']['afterInode'])):
            raise CustodyError('native-source-installer-code-only-history-sidefiles-changed')
        initial_images = publisher.initial_boot_images(selection)
        initial = (initial_images['parent'] if initial_images is not None
            else _native_json_at(metadata, 'source-initial-generation.json')[0])
        if initial is None:
            if _native_root_file(metadata, 'source-publication.json') != (_native_encoded(expected_pub), record['publication']['afterInode']):
                raise CustodyError('native-source-installer-code-only-history-publication-changed')
        else:
            publisher._initial_generation_record(globals_module(), initial)
            if initial['before'] != {'document': expected_pub, 'inode': record['publication']['afterInode']}:
                raise CustodyError('native-source-installer-code-only-history-publication-changed')
            name = ('source-publication.json' if _native_at_inode(metadata, 'source-publication.json') == record['publication']['afterInode']
                else '.source-initial-publication-' + initial['nonce'] + '.json')
            if _native_root_file(metadata, name) != (_native_encoded(expected_pub), record['publication']['afterInode']):
                raise CustodyError('native-source-installer-code-only-history-publication-changed')
        if (_native_json_at(metadata, 'source-installer-code-only.json')[1] != raw
                or _native_root_file(metadata, _native_installer_journal_name(metadata, transaction, _history=True), limit=32 * 1024 * 1024)[0] != journal_raw
                or _native_installer_context(selection.bridge, transaction)[1] != context_raw):
            raise CustodyError('native-source-installer-code-only-history-changed')
        history = native_installer_history(selection)
        return {'metadata': {'source-installer-code-only.json', *history['metadata'],
                '.source-code-only-publication-before-' + record['nonce'] + '.json',
                '.source-code-only-sidefiles-before-' + record['nonce'] + '.json'}, 'kits': {record['backup'], *history['kits']}}


def native_code_only_status_absence(selection):
    """Read-only post-upgrade preconnection proof, including after boot."""
    from pixel_macos_custody import protected_directory
    native_installer_code_only_history(selection)
    with protected_directory(NATIVE_META) as metadata:
        record, raw = _native_json_at(metadata, 'source-installer-code-only.json')
        if record is None or record['phase'] != 'committed':
            raise CustodyError('native-source-installer-code-only-unconfirmed')
        first = _native_installer_absence_state(selection.bridge, _installer_code_only=record)
        if (_native_installer_absence_state(selection.bridge, _installer_code_only=record) != first
                or _native_json_at(metadata, 'source-installer-code-only.json')[1] != raw):
            raise CustodyError('native-source-installer-code-only-status-changed')
    return first


def native_installer_absent_kit_authority(selection, *, transaction, proof, _completed=False):
    """Actual first-kit purpose; creates no identity, private state or actor."""
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    doc = _native_selected(selection, _publication=True)
    publisher = _publisher(selection)
    _module, _doc, component, ref = publisher._material(selection)
    context, context_raw = _native_installer_context(selection.bridge, transaction)
    native_installer_absence_receipt(proof, transaction=transaction, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'])
    if (proof['kit'] is not None or 'source-publication.json' in proof['metadata']
            or doc['bundleSha256'] != transaction['candidateDigest']):
        raise CustodyError('native-source-installer-first-kit-original-state-invalid')
    with protected_directory(NATIVE_META) as metadata:
        journal, records, raw = _native_installer_journal(metadata, transaction, context, _completed=_completed)
        if journal['phase'] != 'active' or journal['sourceAddition']['proof'] != proof:
            raise CustodyError('native-source-installer-first-kit-target-not-active')
        _native_installer_live_images(records)
        for item in records:
            if item['path'] in native_installer_source_family():
                path = Path(item['path'])
                with protected_directory(path.parent) as directory:
                    if _native_root_file(directory, path.name, mode=item['mode'], gid=item['gid'], limit=8 * 1024 * 1024)[0] != item['after']:
                        raise CustodyError('native-source-installer-first-kit-file-image-changed')
        files = publisher._publication_files(component, ref, selection.deployment.checksum)
        publication, publication_raw = _native_json_at(metadata, 'source-publication.json')
        if publication is not None:
            publisher._publication_record(publication, selection.deployment.checksum, files)
            if publication['old'] is not None or publication['bindings'] != {}:
                raise CustodyError('native-source-installer-first-kit-publication-invalid')
        state = _native_installer_absence_state(selection.bridge, _installer_publication=publication)
        if state[2] != proof['identity']:
            raise CustodyError('native-source-installer-first-kit-identity-changed')
        if proof['identity']['kind'] == 'retained' and state[1].get('source-identity.json') != proof['metadata'].get('source-identity.json'):
            raise CustodyError('native-source-installer-first-kit-identity-changed')
        if publication is not None:
            fresh = dict(state[3], kind='never-started-observed')
            if publication['witness'] != dict(fresh, boot=publication['witness']['boot']):
                raise CustodyError('native-source-installer-first-kit-identity-changed')
            if publication['witness']['boot'] != fresh['boot']:
                _native_installer_hold(metadata, transaction)
        # Existing fixed owner-sidefile journal is independently re-proved from
        # this candidate's explicit, frozen producer bytes. No file is written.
        sidefile_raw, sidefile_inode = _native_root_file(metadata, 'source-sidefiles.json')
        sidefiles = json.loads(sidefile_raw, object_pairs_hook=_native_pairs)
        target = context['runtime_bundle'].get('connection_sidefiles')
        if (type(sidefiles) is not dict or set(sidefiles) != {'schemaVersion', 'kind', 'ownerUid', 'ownerGid', 'configPath',
                    'bundleSha256', 'files', 'parent', 'children'}
                or type(sidefiles['schemaVersion']) is not int or sidefiles['schemaVersion'] != 1
                or sidefiles['kind'] != 'ods-native-connection-sidefiles'
                or (sidefiles['ownerUid'], sidefiles['ownerGid'], sidefiles['configPath'], sidefiles['bundleSha256']) != (
                    doc['ownerUid'], doc['ownerGid'], doc['configPath'], doc['bundleSha256'])
                or type(target) is not dict or set(target) != {'gateway.env', 'onboarding.json'}
                or type(sidefiles['files']) is not dict or set(sidefiles['files']) != set(target)
                or type(sidefiles['children']) is not dict or set(sidefiles['children']) != set(target)):
            raise CustodyError('native-source-installer-first-kit-sidefiles-invalid')
        directory = os.open(Path(doc['configPath']).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            _native_no_acl(directory)
            if (sidefiles['parent'] != [info.st_dev, info.st_ino]
                    or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (doc['ownerUid'], doc['ownerGid'], 0o700)):
                raise CustodyError('native-source-installer-first-kit-sidefiles-changed')
            for name, text in target.items():
                image, body = _native_installer_owner_file(directory, name, doc['ownerUid'], doc['ownerGid'])
                if (type(text) is not str or body != text.encode('utf-8') or sidefiles['files'][name] != image['sha256']
                        or sidefiles['children'][name] != {'inode': image['inode'], 'ready': True}):
                    raise CustodyError('native-source-installer-first-kit-sidefiles-changed')
        finally:
            os.close(directory)
        if (_native_json_at(metadata, 'source-publication.json')[1] != publication_raw
                or _native_root_file(metadata, 'source-sidefiles.json') != (sidefile_raw, sidefile_inode)
                or _native_root_file(metadata, _native_installer_journal_name(metadata, transaction, completed=_completed), limit=32 * 1024 * 1024)[0] != raw
                or _native_installer_context(selection.bridge, transaction)[1] != context_raw):
            raise CustodyError('native-source-installer-first-kit-authority-changed')
        return {'transaction': dict(transaction), 'proof': proof, 'publication': publication,
            'files': files, 'kit': state[0], 'witness': state[3]}


def native_installer_absent_kit_plan(selection, *, transaction, proof, _completed=False):
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    authority = native_installer_absent_kit_authority(selection, transaction=transaction, proof=proof, _completed=_completed)
    publisher = _publisher(selection)
    record, files = authority['publication'], authority['files']
    with protected_directory(NATIVE_KIT.parent) as parent:
        known = {NATIVE_KIT.name}
        if record is not None:
            known.add(record['candidate'])
        if any(name.startswith('.source-kit-') and name not in known for name in os.listdir(parent)):
            raise CustodyError('native-source-installer-first-kit-unknown-prefix')
        if record is None:
            if _native_at_inode(parent, NATIVE_KIT.name) is not None:
                raise CustodyError('native-source-installer-first-kit-unbound')
            return authority
        live, staged = _native_at_inode(parent, NATIVE_KIT.name), _native_at_inode(parent, record['candidate'])
        if live is not None and staged is not None or live is None and staged is None and record['candidateInode'] is not None:
            raise CustodyError('native-source-installer-first-kit-prefix-changed')
        location = NATIVE_KIT.name if live is not None else record['candidate']
        inode = live if live is not None else staged
        if inode is not None:
            if inode != record['candidateInode']:
                raise CustodyError('native-source-installer-first-kit-prefix-changed')
            directory = os.open(location, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(directory)
                _native_no_acl(directory)
                if ((info.st_uid, info.st_gid) != (0, 0) or stat.S_IMODE(info.st_mode) not in (0o700, 0o755)
                        or set(os.listdir(directory)) - set(files)):
                    raise CustodyError('native-source-installer-first-kit-prefix-changed')
                for name, body in files.items():
                    item = record['stagedFiles'][name]
                    publisher._generation_prefix(globals_module(), directory, name, body, item,
                        mode=0o755 if name == 'broker.py' else 0o644)
                    if item['inode'] is not None and _native_at_inode(directory, name) != item['inode']:
                        raise CustodyError('native-source-installer-first-kit-prefix-changed')
                if live is not None and (record['phase'] not in ('publishing-kit', 'kit-promoted', 'binding', 'prepared-no-jobs')
                        or stat.S_IMODE(info.st_mode) != 0o755 or set(os.listdir(directory)) != set(files)
                        or any(not item['ready'] for item in record['stagedFiles'].values())):
                    raise CustodyError('native-source-installer-first-kit-unconfirmed')
            finally:
                os.close(directory)
        return authority


def native_verify_completed_installer_absent_kit(selection, proof, *, transaction):
    authority = native_installer_absent_kit_plan(selection, transaction=transaction, proof=proof, _completed=True)
    if authority['publication'] is None or authority['publication']['phase'] != 'prepared-no-jobs':
        raise CustodyError('native-source-installer-first-kit-unconfirmed')
    return {'status': 'prepared-no-jobs', 'transaction': dict(transaction),
        'kitSha256': hashlib.sha256(authority['files']['source-kit.json']).hexdigest()}


def _native_installer_runtime(bridge, current_digest, state):
    from pixel_access_bridge import gateway_runtime_environment
    from pixel_macos_custody import protected_bytes
    from pixel_provider.runtime_custody import RuntimeCustody, tree_manifest
    descriptor = os.path.lexists(NATIVE_RUNTIME_DESCRIPTOR)
    manifest = os.path.lexists(NATIVE_RUNTIME_MANIFEST)
    root = NATIVE_RUNTIMES / current_digest
    if descriptor != manifest:
        raise CustodyError('native-source-installer-partial-runtime-preserved')
    if descriptor:
        runtime, checksum = RuntimeCustody(bridge).inspect()
        if Path(runtime['runtimeRoot']) != root / 'runtime':
            raise CustodyError('native-source-installer-old-runtime-changed')
        return runtime, checksum
    # This is a distinct legacy contract, not an error fallback. Any Source
    # identity, code kit, metadata or private state makes it inapplicable.
    if state[:3] != (None, {}, {'kind': 'absent'}):
        raise CustodyError('native-source-installer-legacy-state-preserved')
    body = protected_bytes(root / 'bundle.json', limit=32 * 1024 * 1024)
    try:
        bundle = json.loads(body.decode(), object_pairs_hook=_native_pairs)
        if (hashlib.sha256(body).hexdigest() != current_digest or _native_encoded(bundle) != body
                or type(bundle) is not dict or set(bundle) != {'schemaVersion', 'openclawVersion', 'plugins', 'entries'}
                or type(bundle['schemaVersion']) is not int or bundle['schemaVersion'] != 1
                or type(bundle['entries']) is not dict or type(bundle['plugins']) is not list
                or bundle['plugins'] != ['plugins/' + str(i) for i in range(len(bundle['plugins']))]
                or any(name in ('ods-source', 'ods-source.json', 'native-source')
                    or name.startswith(('ods-source/', 'native-source/')) for name in bundle['entries'])):
            raise ValueError()
        actual = tree_manifest(root)
        actual.pop('bundle.json', None)
        if (actual != bundle['entries'] or actual.get('node', [])[:2] != ['file', 0o755]
                or actual.get('runtime/openclaw.mjs', [None])[0] != 'file'
                or any(actual.get(path) != ['directory', 0o755] for path in bundle['plugins'])
                or any(row[0] == 'link' and (row[1].startswith('/') or any(ord(char) < 32 for char in row[1]))
                    for row in actual.values())):
            raise ValueError()
        package = json.loads(protected_bytes(root / 'runtime/package.json', limit=1048576).decode(), object_pairs_hook=_native_pairs)
        if package.get('name') != 'openclaw' or package.get('version') != bundle['openclawVersion']:
            raise ValueError()
        launcher = NATIVE_PROGRAM / 'openclaw-gateway-launcher'
        expected = ('#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH '
            + str(root / 'node') + ' ' + str(root / 'runtime/openclaw.mjs') + ' "$@"\n').encode()
        if Path(bridge.binary) != launcher or protected_bytes(launcher, limit=16384) != expected:
            raise ValueError()
        plist_body, document = bridge._launchd_document()
        environment = gateway_runtime_environment(document)
        args = document['ProgramArguments'][2:]
        while args and '=' in args[0] and args[0].partition('=')[0].isidentifier():
            args = args[1:]
        if (args[:6] != ['/usr/bin/sandbox-exec', '-f', '/etc/ods/pixel-gateway.sb', str(launcher), 'gateway', 'run']
                or environment.get('HOME') != str(bridge.home)):
            raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-installer-legacy-runtime-unqualified') from None
    qualified = {'kind': 'ods-native-source-legacy-runtime-custody', 'bundleSha256': current_digest,
        'gatewayPlistSha256': hashlib.sha256(plist_body).hexdigest(), 'launcherSha256': hashlib.sha256(expected).hexdigest(),
        'ownerUid': bridge.owner.pw_uid, 'ownerGid': bridge.owner.pw_gid, 'runtimeRoot': str(root / 'runtime')}
    return {'runtimeRoot': str(root / 'runtime'), 'sourceRoot': str(root)}, hashlib.sha256(_native_encoded(qualified)).hexdigest()


def native_inspect_installer_absence(bridge, *, current_digest):
    """Read-only preflight; no context, journal, Source state or actor is created."""
    from pixel_access_bridge import LaunchdAccessBridge
    _native_root()
    if (type(bridge) is not LaunchdAccessBridge or Path(bridge.state) != NATIVE_META
            or LaunchdAccessBridge._native_runtime_lease(bridge) is None
            or type(current_digest) is not str or not _NATIVE_SHA.fullmatch(current_digest)):
        raise CustodyError('native-source-installer-capability-required')
    LaunchdAccessBridge._native_source_deadline_ns(bridge)
    bridge.verify_gateway_installation_binding()
    state = _native_installer_absence_state(bridge)
    runtime, checksum = _native_installer_runtime(bridge, current_digest, state)
    first = _native_installer_absence_state(bridge, qualified_runtime=runtime)
    if state != first or _native_installer_absence_state(bridge) != first:
        raise CustodyError('native-source-installer-absence-changed')
    return {'runtimeRoot': runtime['runtimeRoot'], 'sourceRoot': runtime['sourceRoot'],
        'oldRuntimeCustodySha256': checksum, 'state': first}


def native_absent_for_installer(bridge, *, transaction):
    """Initial absence proof from the actual old protected runtime and context."""
    context, context_raw = _native_installer_context(bridge, transaction)
    inspected = native_inspect_installer_absence(bridge, current_digest=transaction['currentDigest'])
    if _native_installer_context(bridge, transaction)[1] != context_raw:
        raise CustodyError('native-source-installer-context-changed')
    first = inspected['state']
    kit, metadata, identity, witness = first
    value = {'schemaVersion': 1, 'kind': 'ods-native-source-installer-absence',
        'transaction': transaction['transaction'], 'currentDigest': transaction['currentDigest'],
        'candidateDigest': transaction['candidateDigest'], 'ownerUid': context['owner']['uid'],
        'ownerGid': context['owner']['gid'], 'oldRuntimeCustodySha256': inspected['oldRuntimeCustodySha256'],
        'runtimeRoot': inspected['runtimeRoot'], 'sourceRoot': inspected['sourceRoot'],
        'kit': kit, 'metadata': metadata, 'identity': identity, 'witness': witness}
    return native_installer_absence_receipt(value, transaction=transaction,
        owner_uid=context['owner']['uid'], owner_gid=context['owner']['gid'])


def native_verify_installer_absence(bridge, proof, *, transaction):
    """Fresh absence under the exact protected mixed-image installer journal.

    The installer codec separately proves its complete fixed file family. This
    hook does not reselect a mixed runtime or turn it into the old runtime.
    """
    from pixel_macos_custody import protected_directory
    context, context_raw = _native_installer_context(bridge, transaction)
    native_installer_absence_receipt(proof, transaction=transaction,
        owner_uid=context['owner']['uid'], owner_gid=context['owner']['gid'])
    with protected_directory(NATIVE_META) as metadata:
        journal, records, raw = _native_installer_journal(metadata, transaction, context)
        if journal['sourceAddition']['proof'] != proof:
            raise CustodyError('native-source-installer-journal-changed')
        _native_installer_live_images(records)
        first = _native_installer_absence_state(bridge)
        if first[2] != proof['identity']:
            raise CustodyError('native-source-installer-identity-changed')
        if first[0] != proof['kit']:
            raise CustodyError('native-source-installer-kit-changed')
        if (_native_installer_absence_state(bridge) != first
                or _native_root_file(metadata, 'runtime-upgrade.json', limit=32 * 1024 * 1024)[0] != raw
                or _native_installer_context(bridge, transaction)[1] != context_raw):
            raise CustodyError('native-source-installer-absence-changed')
    return proof


def native_retained_identity_only(*, owner_uid, owner_gid):
    """Verify only the retained Source identity family for an installer.

    This read-only bootstrap needs no provider runtime/Source execution
    capability. Ops/other families must independently validate their own files.
    It never creates/adopts an account or restores archived Source data.
    """
    _native_root()
    if (type(owner_uid) is not int or owner_uid <= 0 or type(owner_gid) is not int or owner_gid < 0
            or pwd.getpwuid(owner_uid).pw_gid != owner_gid):
        raise CustodyError('native-source-retained-owner-invalid')
    census = _NativeRetainedSourceCensus(owner_uid, owner_gid)
    first = _native_installer_absence_state(census)
    kit, metadata, identity, witness = first
    if kit is not None or set(metadata) != {'source-identity.json'} or identity.get('kind') != 'retained':
        raise CustodyError('native-source-retained-family-required')
    if _native_installer_absence_state(census) != first:
        raise CustodyError('native-source-retained-identity-changed')
    return {'schemaVersion': 1, 'kind': 'ods-native-source-retained-identity',
        'record': identity['record'], 'image': metadata['source-identity.json'], 'witness': witness}


def native_installer_source_family():
    """The fixed code-only Source family shared with the installer codec."""
    names = ('__init__.py', 'activity.py', 'approvals.py', 'catalog.py', 'converge.py',
        'coordinator.py', 'custody.py', 'drafts.py', 'google_app.py', 'host_api.py', 'lifecycle.py',
        'native_access.py', 'native_apps.py', 'native_contract.py', 'native_enrollment.py', 'native_host_api.py',
        'native_mcp.py', 'native_public.py', 'native_executor.py', 'source_backend.py', 'native_retirement.py',
        'oauth.py', 'planner.py', 'plugins.py', 'state.py', 'status.py')
    return frozenset({str(NATIVE_PROGRAM / 'pixel_connections' / name) for name in names}
        | {str(NATIVE_PROGRAM / name) for name in ('connection_transaction.py', 'native_mcp_owner.py', 'native_mcp_oauth.mjs', 'pixel_connection_agent_status.py')}
        | {str(NATIVE_RUNTIME_DESCRIPTOR), str(NATIVE_RUNTIME_MANIFEST), str(NATIVE_META / 'source-runtime-installation.json')})


def _native_installer_legacy_additions():
    inspection = frozenset('/usr/local/libexec/ods-pixel-services/helpers/' + name for name in (
        'preview_inspection.py', 'preview_inspection_protocol.py', 'workspace_preview.py', 'unix_peer.py', 'preview-inspection.json'))
    integration = frozenset({'/usr/local/libexec/ods-pixel-services/integrations/' + name for name in (
        'integrations_common.py', 'integrations_mcp.py', 'integrations_notes.py', 'integrations_service.py',
        'unix_peer.py', 'integrations_app_service.py', 'integrations_apps.py', 'integrations_ntfy.py', 'integrations_recovery.py')}
        | {'/Library/LaunchDaemons/com.ods.pixel-native-integrations.plist'})
    return inspection, integration


def _native_installer_schedule_additions():
    return frozenset('/usr/local/libexec/ods-pixel-access/' + name for name in (
        'pixel_schedules.py', 'scheduled_tasks_owner.py', 'scheduled_tasks_gateway.mjs'))


def _native_installer_journal_name(metadata, transaction, *, completed=False, _history=False):
    if _history:
        # Only the fixed data-history reader selects this route. It grants no
        # installer mutation or admission proof after the edge was released.
        archived = 'runtime-upgrade-' + transaction['candidateDigest'] + '.completed.json'
        return archived if _native_at_inode(metadata, archived) is not None else 'runtime-upgrade.json'
    if not completed:
        return 'runtime-upgrade.json'
    if _native_at_inode(metadata, 'runtime-upgrade.json') is not None:
        raise CustodyError('native-source-installer-completed-live-conflict')
    _native_installer_hold(metadata, transaction)
    return 'runtime-upgrade-' + transaction['candidateDigest'] + '.completed.json'


def _native_installer_hold(metadata, transaction):
    """Protected hold receipt, separate from the caller's live SDK reacquire.

    The installer holds its Root lock and independently acquires/resumes the
    exact controller edge before calling these producers. This file is a
    necessary fixed custody binding; it cannot attest live controller state.
    """
    edge_raw, _inode = _native_root_file(metadata, 'runtime-upgrade-edge-' + transaction['candidateDigest'] + '.json')
    try:
        edge = json.loads(edge_raw, object_pairs_hook=_native_pairs)
        if (type(edge) is not dict or set(edge) != {'schemaVersion', 'container', 'binding', 'phase', 'status'}
                or type(edge['schemaVersion']) is not int or edge['schemaVersion'] != 1 or edge['phase'] != 'held'
                or type(edge['container']) is not str or not _NATIVE_SHA.fullmatch(edge['container'])
                or type(edge['binding']) is not dict or set(edge['binding']) != {'token', 'revision'}
                or any(type(value) is not str or not _NATIVE_SHA.fullmatch(value) for value in edge['binding'].values())
                or type(edge['status']) is not dict or edge['status'].get('capability') != 'available'
                or edge['status'].get('phase') != 'held' or edge['status'].get('streams') != 0
                or edge['status'].get('admission_blocked') is not True
                or edge['status'].get('revision') != edge['binding']['revision']):
            raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-installer-completed-hold-required') from None
    return edge


def _native_installer_journal(metadata, transaction, context, *, _completed=False, _history=False):
    """Decode actual protected Source images without importing installer code.

    The complete unrelated Core/service rollback contract remains installer
    owned. A Source producer can act only on this complete fixed Source family.
    """
    name = _native_installer_journal_name(metadata, transaction, completed=_completed, _history=_history)
    raw, _inode = _native_root_file(metadata, name, limit=32 * 1024 * 1024)
    return _native_installer_journal_bytes(raw, transaction, context, _completed=_completed)


def _native_installer_journal_bytes(raw, transaction, context, *, _completed=False):
    """Decode frozen journal bytes, independently of their retained location."""
    import base64
    try:
        value = json.loads(raw.decode(), object_pairs_hook=_native_pairs)
        if (type(value) is not dict or set(value) != {'schemaVersion', 'currentDigest', 'candidateDigest', 'phase', 'files', 'sourceAddition'}
                or type(value['schemaVersion']) is not int or value['schemaVersion'] not in (4, 5)
                or (value['currentDigest'], value['candidateDigest']) != (transaction['currentDigest'], transaction['candidateDigest'])
                or type(value['phase']) is not str or value['phase'] not in {'prepared', 'replacing-files', 'active',
                    'rolling-back', 'restored', 'recovery-required',
                    *(action + '-' + role for action in ('stopping', 'starting') for role in (
                        'integrations', 'operations', 'promoter', 'manager', 'gateway', 'access', 'relay'))}
                or _completed and value['phase'] not in ('active', 'restored')
                or type(value['files']) is not list or not 1 <= len(value['files']) <= 128):
            raise ValueError()
        records = []
        for item in value['files']:
            if (type(item) is not dict or set(item) != {'path', 'mode', 'gid', 'before', 'after', 'beforeSha256', 'afterSha256'}
                    or type(item['path']) is not str or not item['path'].startswith('/')
                    or any(part in ('', '.', '..') for part in item['path'].split('/')[1:])
                    or any(ord(char) < 32 for char in item['path'])
                    or type(item['mode']) is not int or item['mode'] not in (0o600, 0o640, 0o644, 0o755)
                    or type(item['gid']) is not int or item['gid'] < 0):
                raise ValueError()
            row = {key: item[key] for key in ('path', 'mode', 'gid')}
            for key in ('before', 'after'):
                if key == 'before' and item[key] is None and item[key + 'Sha256'] is None:
                    row[key] = None
                    continue
                body = base64.b64decode(item[key], validate=True)
                if (base64.b64encode(body).decode('ascii') != item[key]
                        or hashlib.sha256(body).hexdigest() != item[key + 'Sha256']):
                    raise ValueError()
                row[key] = body
            records.append(row)
        if len({item['path'] for item in records}) != len(records):
            raise ValueError()
        family = native_installer_source_family()
        source = {item['path']: item for item in records if item['path'] in family}
        if set(source) != family:
            raise ValueError()
        tuples = []
        for path, item in sorted(source.items()):
            mode = 0o600 if path == str(NATIVE_META / 'source-runtime-installation.json') else 0o644
            if item['mode'] != mode or item['gid'] != 0:
                raise ValueError()
            tuples.append([path, mode, 0, hashlib.sha256(item['after']).hexdigest()])
        family_sha = hashlib.sha256(json.dumps(tuples, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
        inspection, integration = _native_installer_legacy_additions()
        legacy_absent = {item['path'] for item in records if item['before'] is None and item['path'] not in family}
        schedules = _native_installer_schedule_additions()
        schedule_absent = legacy_absent & schedules
        if (value['schemaVersion'] == 5 and schedule_absent != schedules
                or value['schemaVersion'] == 4 and schedule_absent):
            raise ValueError()
        if any(item['path'] in schedule_absent and (item['mode'], item['gid']) != (0o644, 0) for item in records):
            raise ValueError()
        legacy_absent -= schedules
        if (legacy_absent and legacy_absent not in (inspection, integration, inspection | integration)
                or any(item['path'] in legacy_absent and (item['mode'], item['gid']) != (0o644, 0) for item in records)):
            raise ValueError()
        addition = value['sourceAddition']
        fields = {'schemaVersion', 'kind', 'contextSha256', 'familySha256', 'proof', 'absentPaths'}
        if context['source_lifecycle']['mode'] == 'withdrawal' and type(addition) is dict and 'receipt' in addition:
            fields.add('receipt')
        if (type(addition) is not dict or set(addition) != fields
                or type(addition['schemaVersion']) is not int or addition['schemaVersion'] != 1
                or addition['kind'] != 'ods-native-source-installer-addition'
                or addition['contextSha256'] != transaction['transaction']
                or addition['familySha256'] != family_sha or family_sha != context['source_lifecycle']['familySha256']
                or addition['absentPaths'] != sorted(path for path, item in source.items() if item['before'] is None)):
            raise ValueError()
        owner = context['owner']
        if context['source_lifecycle']['mode'] == 'withdrawal':
            native_installer_withdrawal_intent(addition['proof'], transaction=transaction, owner_uid=owner['uid'], owner_gid=owner['gid'])
            if 'receipt' in addition:
                native_installer_withdrawal_receipt(addition['receipt'], intent=addition['proof'], transaction=transaction,
                    owner_uid=owner['uid'], owner_gid=owner['gid'])
        else:
            native_installer_absence_receipt(addition['proof'], transaction=transaction, owner_uid=owner['uid'], owner_gid=owner['gid'])
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-installer-journal-invalid') from None
    return value, records, raw


def native_installer_withdrawal_request(selection, *, transaction):
    """Read-only protected context before Journal4; no mutation authority."""
    _native_selected(selection, _publication=True)
    context, raw = _native_installer_context(selection.bridge, transaction, mode='withdrawal')
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_META) as metadata:
        existing = None
        if _native_at_inode(metadata, 'runtime-upgrade.json') is not None:
            existing = native_installer_withdrawal_transaction(selection, transaction=transaction)
        if _native_installer_context(selection.bridge, transaction, mode='withdrawal')[1] != raw:
            raise CustodyError('native-source-installer-context-changed')
    return {'transaction': dict(transaction), 'intent': context['source_lifecycle'], 'owner': context['owner'],
        'candidateFiles': None, 'journal': existing}


def native_installer_withdrawal_transaction(selection, *, transaction):
    """Actual frozen Source Journal4 capability, no arbitrary module callback."""
    _native_selected(selection, _publication=True)
    context, context_raw = _native_installer_context(selection.bridge, transaction, mode='withdrawal')
    from pixel_macos_custody import protected_directory
    with protected_directory(NATIVE_META) as metadata:
        value, records, raw = _native_installer_journal(metadata, transaction, context)
        if (_native_root_file(metadata, 'runtime-upgrade.json', limit=32 * 1024 * 1024)[0] != raw
                or _native_installer_context(selection.bridge, transaction, mode='withdrawal')[1] != context_raw):
            raise CustodyError('native-source-installer-authority-changed')
    return {'transaction': dict(transaction), 'intent': context['source_lifecycle'], 'owner': context['owner'],
        'phase': value['phase'], 'files': records, 'proof': value['sourceAddition']['proof'],
        'receipt': value['sourceAddition'].get('receipt')}


def _native_installer_code_absent(path):
    """Reprove absence through every existing protected ancestor, without writes."""
    from pixel_macos_custody import protected_directory
    try:
        with protected_directory(path.parent) as directory:
            try:
                os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return True
    except FileNotFoundError:
        # protected_directory checks each opened ancestor before descending.
        # A missing code-package directory is absence; unsafe existing custody
        # and all other I/O errors remain refusals.
        return True
    return False


def _native_installer_live_images(records):
    from pixel_macos_custody import protected_bytes, CustodyError as FileCustodyError
    inspection, integration = _native_installer_legacy_additions()
    additions = native_installer_source_family() | inspection | integration | _native_installer_schedule_additions()
    for item in records:
        path = Path(item['path'])
        try:
            current = protected_bytes(path, limit=8 * 1024 * 1024)
        except (FileCustodyError, FileNotFoundError):
            # The protected reader wraps ENOENT. Never interpret its generic
            # custody failure as absence: only a declared initially absent
            # closed-family image may obtain a fresh, no-follow absence proof.
            if item['before'] is not None or item['path'] not in additions or not _native_installer_code_absent(path):
                raise
            current = None
        if current not in (item['before'], item['after']):
            raise CustodyError('native-source-installer-file-image-changed')


def _native_installer_old_publisher(authority, *, _bridge=None):
    """Qualify immutable old Source independently of mixed live host files."""
    import importlib.util
    from pixel_provider.runtime_custody import digest, tree_manifest
    from pixel_macos_custody import protected_bytes
    records = {item['path']: item for item in authority['files']}
    try:
        descriptor = json.loads(records[str(NATIVE_RUNTIME_DESCRIPTOR)]['before'], object_pairs_hook=_native_pairs)
        manifest_raw = records[str(NATIVE_RUNTIME_MANIFEST)]['before']
        manifest = json.loads(manifest_raw, object_pairs_hook=_native_pairs)
        if (type(descriptor) is not dict or set(descriptor) != {'schemaVersion', 'runtimeRoot', 'sourceRoot',
                    'node', 'launcher', 'hostPython', 'manifest', 'manifestSha256', 'policy'}
                or descriptor['schemaVersion'] != 1 or type(descriptor['schemaVersion']) is not int
                or descriptor['manifest'] != str(NATIVE_RUNTIME_MANIFEST)
                or descriptor['manifestSha256'] != hashlib.sha256(manifest_raw).hexdigest()
                or digest([descriptor, manifest]) != authority['proof']['runtime']['custodySha256']
                or descriptor['runtimeRoot'] != str(NATIVE_RUNTIMES / authority['transaction']['currentDigest'] / 'runtime')
                or type(manifest) is not dict or set(manifest) != {'runtime', 'source', 'node', 'launcher', 'hostPython'}
                or tree_manifest(descriptor['sourceRoot']) != manifest['source']):
            raise ValueError()
        path = Path(descriptor['sourceRoot']) / 'installers/macos/lib/pixel-native-source-broker.py'
        body = protected_bytes(path, limit=2 * 1024 * 1024)
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise CustodyError('native-source-installer-old-runtime-unqualified') from None
    spec = importlib.util.spec_from_file_location('qualified_native_source_installer_old_publisher', path)
    module = importlib.util.module_from_spec(spec)
    from pixel_access_bridge import LaunchdAccessBridge, _native_runtime_code
    operation = LaunchdAccessBridge._native_runtime_lease(_bridge) if _bridge is not None else None
    if operation is None:
        spec.loader.exec_module(module)
    else:
        exec(_native_runtime_code(operation, path, body), module.__dict__)
    return module


def _native_installer_rebind_record(metadata, transaction, receipt):
    record, raw = _native_json_at(metadata, 'source-installer-rebind.json')
    if record is None:
        return None
    fields = {'schemaVersion', 'kind', 'transaction', 'intentSha256', 'receiptSha256', 'nonce', 'phase',
        'oldBinding', 'binding', 'gate', 'children', 'environment', 'token', 'ownerGuid', 'kitSha256',
        'kitParent', 'oldKit', 'candidate', 'backup', 'candidateInode', 'stagedFiles', 'bindings',
        'publication', 'generation', 'witness', 'withdrawalParent'}
    if (type(record) is not dict or set(record) != fields or type(record['schemaVersion']) is not int
            or record['schemaVersion'] != 1 or record['kind'] != 'ods-native-source-installer-rebind'
            or record['transaction'] != transaction or record['intentSha256'] != receipt['intentSha256']
            or record['receiptSha256'] != hashlib.sha256(_native_encoded(receipt)).hexdigest()
            or type(record['nonce']) is not str or not FLOW_ID.fullmatch(record['nonce'])
            or record['phase'] not in ('staging', 'publishing-kit', 'binding', 'publication', 'generation', 'committed')
            or record['candidate'] != '.source-kit-' + record['nonce'] or record['backup'] != '.source-kit-before-' + record['nonce']
            or type(record['generation']) is not str or not FLOW_ID.fullmatch(record['generation'])
            or record['candidateInode'] is not None and not _native_inode(record['candidateInode'])
            or type(record['stagedFiles']) is not dict or set(record['stagedFiles']) != {
                'source-kit.json', 'broker.py', 'source-broker.env', *NATIVE_PROFILES}
            or type(record['bindings']) is not dict or set(record['bindings']) != {
                'source-custody-prepare.json', 'source-environment.json', 'source-logs-prepare.json', 'custody.json', 'source-sidefiles.json'}
            or type(record['publication']) is not dict or set(record['publication']) != {
                'before', 'beforeInode', 'after', 'afterInode', 'ready'}
            or type(record['withdrawalParent']) is not dict or set(record['withdrawalParent']) != {'inode', 'sha256', 'retained'}
            or not _native_installer_image({key: record['withdrawalParent'][key] for key in ('inode', 'sha256')})
            or type(record['withdrawalParent']['retained']) is not bool):
        raise CustodyError('native-source-installer-rebind-record-invalid')
    for value in record['stagedFiles'].values():
        if (type(value) is not dict or set(value) != {'inode', 'ready'} or type(value['ready']) is not bool
                or value['inode'] is not None and not _native_inode(value['inode'])
                or value['ready'] and value['inode'] is None):
            raise CustodyError('native-source-installer-rebind-record-invalid')
    for name, value in record['bindings'].items():
        if (type(value) is not dict or set(value) != {'before', 'beforeInode', 'after', 'afterInode', 'ready', 'mode', 'gid'}
                or type(value['before']) is not dict or type(value['after']) is not dict
                or not _native_inode(value['beforeInode']) or value['afterInode'] is not None and not _native_inode(value['afterInode'])
                or type(value['ready']) is not bool or value['ready'] and value['afterInode'] is None
                or type(value['mode']) is not int or value['mode'] != (0o640 if name == 'custody.json' else 0o600)
                or type(value['gid']) is not int or value['gid'] != (receipt['binding']['brokerUid'] if name == 'custody.json' else 0)):
            raise CustodyError('native-source-installer-rebind-record-invalid')
    value = record['publication']
    if (type(value['before']) is not dict or value['after'] is not None and type(value['after']) is not dict
            or not _native_inode(value['beforeInode']) or value['afterInode'] is not None and not _native_inode(value['afterInode'])
            or type(value['ready']) is not bool or value['ready'] and (value['afterInode'] is None or value['after'] is None)):
        raise CustodyError('native-source-installer-rebind-record-invalid')
    return record, raw


def _native_installer_withdrawal_state(bridge, receipt, *, transaction, _rebind=None, _completed=False):
    """Actual complete-v1 state; independent of a mixed live RuntimeCustody."""
    from pixel_macos_custody import protected_directory
    context, context_raw = _native_installer_context(bridge, transaction, mode='withdrawal')
    owner = _native_installer_owner(bridge, context_owner=context['owner'])
    with protected_directory(NATIVE_META) as metadata:
        journal, records, raw = _native_installer_journal(metadata, transaction, context, _completed=_completed)
        intent = journal['sourceAddition']['proof']
        native_installer_withdrawal_receipt(receipt, intent=intent, transaction=transaction,
            owner_uid=owner.pw_uid, owner_gid=owner.pw_gid)
        if journal['sourceAddition'].get('receipt', receipt) != receipt:
            raise CustodyError('native-source-installer-receipt-changed')
        authority = {'transaction': dict(transaction), 'intent': context['source_lifecycle'], 'owner': context['owner'],
            'phase': journal['phase'], 'files': records, 'proof': intent, 'receipt': journal['sourceAddition'].get('receipt')}
        _native_installer_live_images(records)
        publisher = _native_installer_old_publisher(authority, _bridge=bridge)
        parent_name = 'source-environment-generation.json'
        if _rebind is not None:
            retained = 'source-environment-generation-' + receipt['withdrawal'] + '.json'
            if _native_at_inode(metadata, parent_name) != _rebind['withdrawalParent']['inode']:
                parent_name = retained
        record_raw, parent_inode = _native_root_file(metadata, parent_name)
        record = json.loads(record_raw, object_pairs_hook=_native_pairs)
        if _rebind is not None and {'inode': parent_inode, 'sha256': hashlib.sha256(record_raw).hexdigest()} != {
                key: _rebind['withdrawalParent'][key] for key in ('inode', 'sha256')}:
            raise CustodyError('native-source-installer-rebind-parent-changed')
        publisher._environment_generation_record(globals_module(), record, receipt['binding'])
        if (record['phase'] != 'withdrawn' or record['generation'] != receipt['withdrawal']
                or record['previousGeneration'] != receipt['generation'] or record['archive'] != receipt['archive']['inode']
                or record['installer']['transaction'] != transaction or record['installer']['intent'] != intent
                or record['installer']['boot'] != receipt['boot']):
            raise CustodyError('native-source-installer-withdrawal-unconfirmed')
        publisher._environment_retained(globals_module(), metadata, record)
        archive = os.open(receipt['archive']['name'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
        try:
            audit_raw, audit_inode = _native_root_file(archive, 'withdrawal-receipt.json')
            audit = {'schemaVersion': 1, 'kind': 'ods-native-source-installer-withdrawal-archive',
                'transaction': transaction, 'intentSha256': receipt['intentSha256'], 'binding': receipt['binding'],
                'old': record['old'], 'descriptor': receipt['descriptor'], 'withdrawal': receipt['withdrawal'], 'boot': receipt['boot']}
            if (audit_raw != _native_encoded(audit) or hashlib.sha256(audit_raw).hexdigest() != receipt['archive']['receiptSha256']
                    or audit_inode != record['installer']['receipt']['inode'] or not record['installer']['receipt']['ready']):
                raise CustodyError('native-source-installer-withdrawal-archive-changed')
        finally:
            os.close(archive)
        for key, name in (('identitySha256', 'source-identity.json'), ('prepareSha256', 'source-custody-prepare.json'),
                ('logsSha256', 'source-logs-prepare.json'), ('publicationSha256', 'source-publication.json')):
            body = (_native_root_file(metadata, name)[0] if _rebind is None or name == 'source-identity.json'
                else _native_encoded((_rebind['publication'] if name == 'source-publication.json' else _rebind['bindings'][name])['before']))
            if hashlib.sha256(body).hexdigest() != receipt['metadata'][key]:
                raise CustodyError('native-source-installer-withdrawal-metadata-changed')
        identity, _body = _native_json_at(metadata, 'source-identity.json')
        identity = _native_identity_intent(identity)
        if (identity['id'], identity['userGuid']) != (receipt['binding']['brokerUid'], receipt['binding']['brokerGuid']):
            raise CustodyError('native-source-installer-withdrawal-identity-changed')
        for kind in ('Groups', 'Users'):
            _native_identity_record(_native_ds_record(kind), _native_identity_attributes(identity, kind), complete=True)
        _native_identity_collisions(identity)
        if _native_uuid(_native_acl_library(), identity['id']) != uuid.UUID(identity['userGuid']).bytes:
            raise CustodyError('native-source-installer-withdrawal-identity-changed')
        gate = os.open(NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if list(native_verify_gate(gate, owner.pw_uid, identity['id'])) != receipt['binding']['gate']:
                raise CustodyError('native-source-installer-withdrawal-gate-changed')
            descriptor_raw, inode = (_native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) if _rebind is None
                else (_native_encoded(_rebind['bindings']['custody.json']['before']), _rebind['bindings']['custody.json']['beforeInode']))
            if (inode != receipt['descriptor']['inode'] or hashlib.sha256(descriptor_raw).hexdigest() != receipt['descriptor']['sha256']
                    or _native_environment_snapshot(gate, NATIVE_ENV.name, identity['id']) != receipt['environment']
                    or publisher._installer_token_metadata(globals_module(), gate, identity['id']) != receipt['token']):
                raise CustodyError('native-source-installer-withdrawal-data-changed')
        finally:
            os.close(gate)
        with protected_directory(NATIVE_DEFINITIONS) as definitions:
            if _rebind is None or _rebind['phase'] not in ('generation', 'committed'):
                if (any(name.startswith(NATIVE_LABEL) for name in os.listdir(definitions))
                    or any(_native_at_inode(metadata, name) is not None for name in (
                        'native-source-jobs', 'source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json'))):
                    raise CustodyError('native-source-installer-withdrawal-jobs-present')
        witness = publisher._no_actors(type('InstallerObservation', (), {'bridge': bridge})(), globals_module(), metadata, _generation=True)
        journal_name = _native_installer_journal_name(metadata, transaction, completed=_completed)
        if (_native_root_file(metadata, journal_name, limit=32 * 1024 * 1024)[0] != raw
                or _native_installer_context(bridge, transaction, mode='withdrawal')[1] != context_raw):
            raise CustodyError('native-source-installer-authority-changed')
        return authority, publisher, identity, witness


def globals_module():
    # A fixed module identity, never a caller-selected implementation.
    return sys.modules[__name__]


def native_verify_installer_withdrawal(bridge, receipt, *, transaction):
    _native_installer_withdrawal_state(bridge, receipt, transaction=transaction)
    return receipt


def _native_installer_rebind_prefix(selection, publisher, metadata, kit_parent, plan, record):
    """Re-derive the protected transaction and qualify every retained prefix."""
    for key in ('oldBinding', 'binding', 'gate', 'children', 'environment', 'token', 'ownerGuid',
            'kitSha256', 'kitParent', 'oldKit'):
        if record[key] != plan[key]:
            raise CustodyError('native-source-installer-rebind-derivation-changed')
    if record['witness'] != plan['witness']:
        raise CustodyError('native-source-installer-rebind-boot-changed')
    for name, image in record['bindings'].items():
        expected = plan['images'][name]
        if (image['beforeInode'] != expected['inode'] or _native_encoded(image['before']) != expected['before']
                or _native_encoded(image['after']) != expected['after']
                or (image['mode'], image['gid']) != (expected['mode'], expected['gid'])):
            raise CustodyError('native-source-installer-rebind-derivation-changed')
    if (_native_encoded(record['publication']['before']) != plan['publication']['before']
            or record['publication']['beforeInode'] != plan['publication']['inode']):
        raise CustodyError('native-source-installer-rebind-publication-changed')
    # Exactly one old kit survives, under its live name or fixed transaction backup.
    live = _native_at_inode(kit_parent, NATIVE_KIT.name)
    backup = _native_at_inode(kit_parent, record['backup'])
    if backup is not None and backup != record['oldKit']['inode']:
        raise CustodyError('native-source-installer-rebind-old-kit-changed')
    if (live == record['oldKit']['inode']) == (backup == record['oldKit']['inode']):
        raise CustodyError('native-source-installer-rebind-old-kit-changed')
    target_name = record['candidate'] if _native_at_inode(kit_parent, record['candidate']) is not None else NATIVE_KIT.name
    target_inode = _native_at_inode(kit_parent, target_name)
    if target_inode == record['oldKit']['inode']:
        target_inode = None
    if target_inode is not None:
        if record['candidateInode'] != target_inode:
            raise CustodyError('native-source-installer-rebind-kit-changed')
        candidate = os.open(target_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=kit_parent)
        try:
            info = os.fstat(candidate)
            _native_no_acl(candidate)
            if ((info.st_uid, info.st_gid) != (0, 0) or stat.S_IMODE(info.st_mode) not in (0o700, 0o755)
                    or set(os.listdir(candidate)) - set(plan['targetKit'])):
                raise CustodyError('native-source-installer-rebind-kit-changed')
            for name, body in plan['targetKit'].items():
                publisher._generation_prefix(globals_module(), candidate, name, body, record['stagedFiles'][name],
                    mode=0o755 if name == 'broker.py' else 0o644)
            if target_name == NATIVE_KIT.name and (stat.S_IMODE(info.st_mode) != 0o755
                    or set(os.listdir(candidate)) != set(plan['targetKit'])
                    or any(not item['ready'] for item in record['stagedFiles'].values())):
                raise CustodyError('native-source-installer-rebind-kit-unconfirmed')
        finally:
            os.close(candidate)
    elif record['candidateInode'] is not None:
        raise CustodyError('native-source-installer-rebind-kit-missing')
    child_present = _native_at_inode(metadata, 'source-jobs-prepare.json') is not None
    gate = os.open(NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, (name, image) in enumerate(sorted(record['bindings'].items())):
            directory = gate if name == 'custody.json' else metadata
            staged = '.source-binding-' + record['nonce'] + '-' + str(index)
            retained = '.source-binding-before-' + record['nonce'] + '-' + str(index)
            if name == 'custody.json' and child_present and record['phase'] in ('generation', 'committed'):
                current = 'child'
            else:
                current = publisher._binding_current(globals_module(), directory, name, image)
            if current != 'before':
                if _native_root_file(metadata, retained, mode=image['mode'], gid=image['gid']) != (
                        plan['images'][name]['before'], image['beforeInode']):
                    raise CustodyError('native-source-installer-rebind-backup-changed')
                if not image['ready']:
                    raise CustodyError('native-source-installer-rebind-binding-unconfirmed')
            elif _native_at_inode(metadata, retained) is not None:
                raise CustodyError('native-source-installer-rebind-backup-conflict')
            publisher._generation_prefix(globals_module(), metadata, staged, plan['images'][name]['after'],
                {'inode': image['afterInode'], 'ready': image['ready']}, mode=image['mode'], gid=image['gid'])
            if current == 'before' and image['afterInode'] is not None and _native_at_inode(metadata, staged) is None:
                raise CustodyError('native-source-installer-rebind-binding-missing')
            if current is None and _native_at_inode(metadata, staged) != image['afterInode']:
                raise CustodyError('native-source-installer-rebind-binding-missing')
        value = record['publication']
        expected = {'schemaVersion': 1, 'kind': 'ods-native-source-publication',
            'deploymentSha256': selection.deployment.checksum,
            'sourceRef': json.loads(plan['targetKit']['source-kit.json'])['sourceRef'],
            'nonce': record['nonce'], 'phase': 'prepared-no-jobs', 'old': None,
            'new': {name: hashlib.sha256(body).hexdigest() for name, body in plan['targetKit'].items()},
            'candidate': record['candidate'], 'backup': record['backup'], 'candidateInode': record['candidateInode'],
            'stagedFiles': record['stagedFiles'], 'bindings': {}, 'witness': record['witness']}
        if value['after'] is not None and value['after'] != expected:
            raise CustodyError('native-source-installer-rebind-publication-changed')
        path = 'source-publication.json'
        present = _native_at_inode(metadata, path)
        old_name = '.source-publication-before-' + record['nonce'] + '.json'
        if present != value['beforeInode']:
            if _native_root_file(metadata, old_name) != (plan['publication']['before'], value['beforeInode']):
                raise CustodyError('native-source-installer-rebind-publication-backup-changed')
            if present is not None and (not value['ready'] or value['after'] is None
                    or _native_root_file(metadata, path) != (_native_encoded(expected), value['afterInode'])):
                raise CustodyError('native-source-installer-rebind-publication-changed')
        elif _native_root_file(metadata, path) != (plan['publication']['before'], value['beforeInode']):
            raise CustodyError('native-source-installer-rebind-publication-changed')
        if value['after'] is not None:
            publisher._generation_prefix(globals_module(), metadata, '.source-rebind-publication-' + record['nonce'],
                _native_encoded(expected), {'inode': value['afterInode'], 'ready': value['ready']})
        if child_present:
            if record['phase'] not in ('generation', 'committed'):
                raise CustodyError('native-source-installer-rebind-child-unexpected')
            # This fixed producer reader must qualify its original child prefix;
            # no caller-provided child record or callback grants authority.
            verifier = getattr(publisher, 'verify_installer_rebind_child', None)
            if verifier is None:
                raise CustodyError('native-source-installer-rebind-child-verifier-unavailable')
            verifier(selection)
    finally:
        os.close(gate)


def native_installer_sidefiles_receipt(value, *, owner_uid, owner_gid, runtime):
    fields = {'schemaVersion', 'kind', 'owner', 'runtime', 'config', 'parent', 'files',
        'record', 'enrollment', 'proof', 'bootstrap'}
    if (type(value) is not dict or set(value) != fields or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['kind'] != 'ods-native-source-installer-sidefiles'
            or type(value['owner']) is not dict or set(value['owner']) != {'uid', 'gid'}
            or value['owner'] != {'uid': owner_uid, 'gid': owner_gid}
            or any(type(item) is not int for item in value['owner'].values())
            or owner_uid <= 0 or owner_gid < 0 or value['runtime'] != runtime
            or type(runtime) is not dict or set(runtime) != {'deploymentSha256', 'custodySha256', 'bundleSha256', 'sourceRef'}
            or any(type(runtime[key]) is not str or not _NATIVE_SHA.fullmatch(runtime[key])
                for key in ('deploymentSha256', 'custodySha256', 'bundleSha256'))
            or type(runtime['sourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', runtime['sourceRef'])
            or type(value['config']) is not dict or set(value['config']) != {'path', 'inode', 'sha256', 'size'}
            or type(value['config']['path']) is not str or not Path(value['config']['path']).is_absolute()
            or not _native_installer_image({key: value['config'][key] for key in ('inode', 'sha256')})
            or type(value['config']['size']) is not int or not 0 < value['config']['size'] <= 8 * 1024 * 1024
            or not _native_inode(value['parent']) or type(value['files']) is not dict
            or set(value['files']) != {'gateway.env', 'onboarding.json'}
            or not _native_installer_image(value['bootstrap'])
            or any(value[key] is not None and not _native_installer_image(value[key]) for key in ('record', 'enrollment'))):
        raise CustodyError('native-source-installer-sidefiles-snapshot-invalid')
    for image in value['files'].values():
        if (type(image) is not dict or set(image) != {'inode', 'sha256', 'size'}
                or not _native_installer_image({key: image[key] for key in ('inode', 'sha256')})
                or type(image['size']) is not int or not 0 < image['size'] <= 131072):
            raise CustodyError('native-source-installer-sidefiles-snapshot-invalid')
    proof = value['proof']
    if (type(proof) is not dict or set(proof) != {'revision', 'configSha256', 'configuredMode', 'effectiveMode'}
            or any(type(proof[key]) is not str or not _NATIVE_SHA.fullmatch(proof[key]) for key in ('revision', 'configSha256'))
            or proof['configSha256'] != value['config']['sha256']
            or proof['configuredMode'] not in ('sandboxed', 'full-access') or proof['effectiveMode'] != proof['configuredMode']):
        raise CustodyError('native-source-installer-sidefiles-snapshot-invalid')
    return value


def _native_installer_owner_file(directory, name, uid, gid, *, limit=131072):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _native_no_acl(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) != (uid, gid, 0o600)
                or not 0 < before.st_size <= limit):
            raise CustodyError('native-source-installer-owner-file-unsafe')
        body = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        inode = [before.st_dev, before.st_ino]
        if (len(body) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns) or _native_at_inode(directory, name) != inode):
            raise CustodyError('native-source-installer-owner-file-changed')
        return {'inode': inode, 'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body)}, body


def native_installer_sidefiles_snapshot(selection):
    """Freeze currently admitted owner images; never infer new owner choices."""
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    from . import coordinator, state, native_enrollment
    from .native_mcp import _verified_runtime
    doc = _native_selected(selection, _publication=True)
    _module, _doc, _component, ref = _publisher(selection)._material(selection)
    runtime = {'deploymentSha256': selection.deployment.checksum, 'custodySha256': doc['runtimeCustodySha256'],
        'bundleSha256': doc['bundleSha256'], 'sourceRef': ref}
    record_path = NATIVE_CONNECTION_RECORD
    with protected_directory(record_path.parent) as directory:
        try:
            record_raw, record_inode = _native_root_file(directory, record_path.name, mode=0o644)
        except FileNotFoundError:
            record_raw = record_inode = None
        record = state.validate(json.loads(record_raw, object_pairs_hook=_native_pairs)) if record_raw is not None else None
    with protected_directory(NATIVE_META) as metadata:
        bootstrap_raw, bootstrap_inode = _native_root_file(metadata, 'source-sidefiles.json')
        try:
            enrollment_raw, enrollment_inode = _native_root_file(metadata, native_enrollment.JOURNAL, limit=262144)
        except FileNotFoundError:
            enrollment_raw = enrollment_inode = None
        if enrollment_raw is not None:
            enrollment = native_enrollment.validate_journal(json.loads(enrollment_raw, object_pairs_hook=_native_pairs))
            if enrollment['phase'] not in native_enrollment.TERMINAL:
                raise CustodyError('native-source-installer-enrollment-pending')
        if any(_native_at_inode(metadata, name) is not None for name in ('transition.json', 'policy-activation.json')):
            raise CustodyError('native-source-installer-transition-pending')
    config_path = Path(doc['configPath'])
    directory = os.open(config_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(directory)
        _native_no_acl(directory)
        if (parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) != (doc['ownerUid'], doc['ownerGid'], 0o700):
            raise CustodyError('native-source-installer-sidefiles-parent-changed')
        config, _config_body = _native_installer_owner_file(directory, config_path.name, doc['ownerUid'], doc['ownerGid'], limit=8 * 1024 * 1024)
        pairs = {name: _native_installer_owner_file(directory, name, doc['ownerUid'], doc['ownerGid'])
            for name in ('gateway.env', 'onboarding.json')}
        files = {name: image for name, (image, _body) in pairs.items()}
        bodies = {name: body.decode('utf-8') for name, (_image, body) in pairs.items()}
        owner = selection.bridge.worker('connection-owner-status')
        held = False
        if os.path.lexists(NATIVE_STATE):
            from .source_backend import NativeSource, _SEAL
            held = NativeSource(selection.bridge, _SEAL).holds_sign_in()
        target = coordinator.gateway_target(record, custody_held=held)
        answers = state.connection_answers(record, google_custody=held)
        observed = selection.bridge.inspect()
        revision = _verified_runtime(selection.bridge, {'configSha256': config['sha256']}, snapshot=observed)
        if (owner['pending'] or owner['configSha256'] != config['sha256'] or owner['envSha256'] != files['gateway.env']['sha256']
                or owner['answers'] != answers or not coordinator._live_matches(owner, target)):
            raise CustodyError('native-source-installer-sidefiles-not-admitted')
        # Match the owner worker's projected answers to the actual whole file.
        contract = json.loads(bodies['onboarding.json'], object_pairs_hook=_native_pairs)
        if type(contract) is not dict or {key: contract.get(key) for key in state.ANSWER_KEYS} != answers:
            raise CustodyError('native-source-installer-sidefiles-not-admitted')
        for name in pairs:
            if _native_installer_owner_file(directory, name, doc['ownerUid'], doc['ownerGid'])[0] != files[name]:
                raise CustodyError('native-source-installer-sidefiles-changed')
        if _native_installer_owner_file(directory, config_path.name, doc['ownerUid'], doc['ownerGid'], limit=8 * 1024 * 1024)[0] != config:
            raise CustodyError('native-source-installer-sidefiles-changed')
        snapshot = {'schemaVersion': 1, 'kind': 'ods-native-source-installer-sidefiles',
            'owner': {'uid': doc['ownerUid'], 'gid': doc['ownerGid']}, 'runtime': runtime,
            'config': dict(config, path=str(config_path)), 'parent': [parent.st_dev, parent.st_ino], 'files': files,
            'record': None if record_raw is None else {'inode': record_inode, 'sha256': hashlib.sha256(record_raw).hexdigest()},
            'enrollment': None if enrollment_raw is None else {'inode': enrollment_inode, 'sha256': hashlib.sha256(enrollment_raw).hexdigest()},
            'proof': {'revision': revision, 'configSha256': config['sha256'],
                'configuredMode': observed['configured_mode'], 'effectiveMode': observed['effective_mode']},
            'bootstrap': {'inode': bootstrap_inode, 'sha256': hashlib.sha256(bootstrap_raw).hexdigest()}}
    finally:
        os.close(directory)
    with protected_directory(record_path.parent) as directory, protected_directory(NATIVE_META) as metadata:
        if (record_raw is None and _native_at_inode(directory, record_path.name) is not None
                or record_raw is not None and _native_root_file(directory, record_path.name, mode=0o644) != (record_raw, record_inode)
                or _native_root_file(metadata, 'source-sidefiles.json') != (bootstrap_raw, bootstrap_inode)
                or enrollment_raw is None and _native_at_inode(metadata, native_enrollment.JOURNAL) is not None
                or enrollment_raw is not None and _native_root_file(metadata, native_enrollment.JOURNAL, limit=262144) != (enrollment_raw, enrollment_inode)
                or _verified_runtime(selection.bridge, {'configSha256': config['sha256']}) != revision
                or selection.bridge.worker('connection-owner-status') != owner):
            raise CustodyError('native-source-installer-sidefiles-changed')
    _native_selected(selection, _publication=True)
    return {'snapshot': native_installer_sidefiles_receipt(snapshot, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'], runtime=runtime),
        'sidefiles': bodies}


def _native_installer_sidefiles(doc, context, before, transaction, *, old_runtime, before_image):
    """Rebind the Root receipt only; owner files remain byte/inode identical."""
    fields = {'schemaVersion', 'kind', 'ownerUid', 'ownerGid', 'configPath', 'bundleSha256', 'files', 'parent', 'children'}
    selected = context['runtime_bundle']
    supplied = selected.get('connection_sidefiles')
    names = {'gateway.env', 'onboarding.json'}
    if (type(before) is not dict or set(before) != fields or type(before['schemaVersion']) is not int
            or before['schemaVersion'] != 1 or before['kind'] != 'ods-native-connection-sidefiles'
            or (before['ownerUid'], before['ownerGid']) != (doc['ownerUid'], doc['ownerGid'])
            or type(before['ownerUid']) is not int or type(before['ownerGid']) is not int
            or before['bundleSha256'] != transaction['currentDigest']
            or type(before['configPath']) is not str or Path(before['configPath']).parent != Path(doc['configPath']).parent
            or type(supplied) is not dict or set(supplied) != names
            or type(before['files']) is not dict or set(before['files']) != names
            or not _native_inode(before['parent']) or type(before['children']) is not dict or set(before['children']) != names):
        raise CustodyError('native-source-installer-sidefiles-unqualified')
    if doc['bundleSha256'] == transaction['candidateDigest']:
        if selected.get('config_path') != doc['configPath']:
            raise CustodyError('native-source-installer-sidefiles-target-changed')
    snapshot = selected.get('source_sidefiles_before')
    hashes, children = before['files'], before['children']
    if snapshot is not None:
        from pixel_macos_custody import protected_directory
        native_installer_sidefiles_receipt(snapshot, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'], runtime=old_runtime)
        if (snapshot['bootstrap'] != before_image or snapshot['parent'] != before['parent']
                or Path(snapshot['config']['path']).parent != Path(doc['configPath']).parent):
            raise CustodyError('native-source-installer-sidefiles-origin-changed')
        with protected_directory(NATIVE_CONNECTION_RECORD.parent) as record_directory, protected_directory(NATIVE_META) as metadata:
            for directory, name, image, mode in (
                    (record_directory, NATIVE_CONNECTION_RECORD.name, snapshot['record'], 0o644),
                    (metadata, 'native-app-enrollment.json', snapshot['enrollment'], 0o600)):
                if image is None:
                    if _native_at_inode(directory, name) is not None:
                        raise CustodyError('native-source-installer-sidefiles-authority-changed')
                else:
                    raw, inode = _native_root_file(directory, name, mode=mode, limit=262144)
                    if {'inode': inode, 'sha256': hashlib.sha256(raw).hexdigest()} != image:
                        raise CustodyError('native-source-installer-sidefiles-authority-changed')
        hashes = {name: image['sha256'] for name, image in snapshot['files'].items()}
        children = {name: {'inode': image['inode'], 'ready': True} for name, image in snapshot['files'].items()}
    directory = os.open(Path(doc['configPath']).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(directory)
        _native_no_acl(directory)
        if ([parent.st_dev, parent.st_ino] != before['parent']
                or (parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) != (doc['ownerUid'], doc['ownerGid'], 0o700)):
            raise CustodyError('native-source-installer-sidefiles-parent-changed')
        for name in sorted(names):
            if type(supplied[name]) is not str:
                raise CustodyError('native-source-installer-sidefiles-target-changed')
            target = supplied[name].encode('utf-8')
            child = children[name]
            if (not 0 < len(target) <= 131072 or hashlib.sha256(target).hexdigest() != hashes[name]
                    or type(child) is not dict or set(child) != {'inode', 'ready'} or child['ready'] is not True
                    or not _native_inode(child['inode'])):
                raise CustodyError('native-source-installer-sidefiles-target-changed')
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                _native_no_acl(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (doc['ownerUid'], doc['ownerGid'], 0o600)
                        or not 0 < info.st_size <= 131072):
                    raise CustodyError('native-source-installer-sidefiles-unsafe')
                body = stream.read(131073)
                after = os.fstat(stream.fileno())
                inode = [info.st_dev, info.st_ino]
                if ((info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or _native_at_inode(directory, name) != inode):
                    raise CustodyError('native-source-installer-sidefiles-changed')
            if body != target or inode != child['inode']:
                raise CustodyError('native-source-installer-sidefiles-changed')
    finally:
        os.close(directory)
    return dict(before, configPath=doc['configPath'], bundleSha256=doc['bundleSha256'], files=hashes, children=children)


def native_installer_rebind_plan(selection, *, transaction, receipt, _completed=False):
    """Read-only initial old-to-target derivation. The publisher owns CAS."""
    doc = _native_selected(selection, _publication=True)
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    with protected_directory(NATIVE_META) as metadata:
        frozen = _native_installer_rebind_record(metadata, transaction, receipt)
    rebind, rebind_raw = frozen if frozen is not None else (None, None)
    if _completed and (rebind is None or rebind['phase'] != 'committed'):
        raise CustodyError('native-source-installer-rebind-uncommitted')
    authority, old_publisher, identity, witness = _native_installer_withdrawal_state(selection.bridge, receipt,
        transaction=transaction, _rebind=rebind, _completed=_completed)
    target_digest = transaction['currentDigest'] if authority['phase'] == 'restored' else transaction['candidateDigest']
    if (authority['receipt'] != receipt or authority['phase'] not in ('replacing-files', 'active', 'restored')
            or doc['bundleSha256'] != target_digest):
        raise CustodyError('native-source-installer-target-not-installed')
    publisher = _publisher(selection)
    _module, target, component, ref = publisher._material(selection)
    files = publisher._publication_files(component, ref, selection.deployment.checksum)
    kit_sha = hashlib.sha256(files['source-kit.json']).hexdigest()
    owner_guid = str(uuid.UUID(bytes=_native_uuid(_native_acl_library(), doc['ownerUid']))).upper()
    context, _context_raw = _native_installer_context(selection.bridge, transaction, mode='withdrawal')
    old_binding = {key: value for key, value in receipt['binding'].items()
        if key not in ('runtimeCustodySha256', 'bundleSha256', 'sourceRef')}
    binding = dict(old_binding, deploymentSha256=selection.deployment.checksum, kitSha256=kit_sha)
    with protected_directory(NATIVE_META) as metadata, protected_directory(NATIVE_KIT.parent) as kit_parent:
        old_name = NATIVE_KIT.name
        if rebind is not None and _native_at_inode(kit_parent, old_name) != rebind['oldKit']['inode']:
            old_name = rebind['backup']
        old_kit, old_files = old_publisher._kit_image(globals_module(), kit_parent, old_name,
            expected=rebind['oldKit'] if rebind is not None else None)
        if (hashlib.sha256(old_files['source-kit.json']).hexdigest() != old_binding['kitSha256']
                or json.loads(old_files['source-kit.json'])['deploymentSha256'] != old_binding['deploymentSha256']):
            raise CustodyError('native-source-installer-old-kit-changed')
        old_publication, pub_inode = (_native_root_file(metadata, 'source-publication.json') if rebind is None
            else (_native_encoded(rebind['publication']['before']), rebind['publication']['beforeInode']))
        old_publisher._publication_record(json.loads(old_publication, object_pairs_hook=_native_pairs),
            old_binding['deploymentSha256'], old_files)
        pub = json.loads(old_publication, object_pairs_hook=_native_pairs)
        if (pub['phase'] != 'prepared-no-jobs' or pub['candidateInode'] != old_kit['inode']
                or pub['sourceRef'] != receipt['runtime']['sourceRef']
                or any(not item['ready'] or old_kit['files'][name]['inode'] != item['inode']
                    for name, item in pub['stagedFiles'].items())):
            raise CustodyError('native-source-installer-old-kit-changed')
        images = {}
        prepare = (_native_json_at(metadata, 'source-custody-prepare.json')[0] if rebind is None
            else rebind['bindings']['source-custody-prepare.json']['before'])
        old_prepare = _native_prepare_binding(doc, identity, owner_guid, old_binding['kitSha256'], old_binding['deploymentSha256'])
        _native_validate_prepare_intent(prepare, old_prepare)
        if prepare['gate'] != old_binding['gate']:
            raise CustodyError('native-source-installer-preparation-changed')
        new_prepare = dict(prepare, **_native_prepare_binding(doc, identity, owner_guid, kit_sha, selection.deployment.checksum))
        environment = (_native_json_at(metadata, 'source-environment.json')[0] if rebind is None
            else rebind['bindings']['source-environment.json']['before'])
        _native_environment_record(environment, old_binding)
        if environment['current'] != receipt['environment'] or environment['pending'] is not None:
            raise CustodyError('native-source-installer-environment-unsettled')
        logs = (_native_json_at(metadata, 'source-logs-prepare.json')[0] if rebind is None
            else rebind['bindings']['source-logs-prepare.json']['before'])
        _native_log_record(logs, {**old_binding, 'kind': 'ods-native-source-log-prepare', 'path': str(NATIVE_LOGS)})
        directory = os.open(NATIVE_LOGS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if native_verify_log_directory(directory, identity['id']) != logs['directory']:
                raise CustodyError('native-source-installer-logs-changed')
            _native_log_files(directory, logs, identity)
        finally:
            os.close(directory)
        new_logs = {**logs, **binding, 'kind': 'ods-native-source-log-prepare', 'path': str(NATIVE_LOGS)}
        for name, after in (('source-custody-prepare.json', new_prepare),
                ('source-environment.json', dict(environment, **binding)), ('source-logs-prepare.json', new_logs)):
            before, inode = (_native_root_file(metadata, name) if rebind is None
                else (_native_encoded(rebind['bindings'][name]['before']), rebind['bindings'][name]['beforeInode']))
            images[name] = {'before': before, 'inode': inode, 'after': _native_encoded(after), 'mode': 0o600, 'gid': 0}
        sidefiles_raw, sidefiles_inode = (_native_root_file(metadata, 'source-sidefiles.json') if rebind is None else
            (_native_encoded(rebind['bindings']['source-sidefiles.json']['before']), rebind['bindings']['source-sidefiles.json']['beforeInode']))
        sidefiles = json.loads(sidefiles_raw, object_pairs_hook=_native_pairs)
        images['source-sidefiles.json'] = {'before': sidefiles_raw, 'inode': sidefiles_inode,
            'after': _native_encoded(_native_installer_sidefiles(doc, context, sidefiles, transaction,
                old_runtime={'deploymentSha256': receipt['binding']['deploymentSha256'], **receipt['runtime']},
                before_image={'inode': sidefiles_inode, 'sha256': hashlib.sha256(sidefiles_raw).hexdigest()})), 'mode': 0o600, 'gid': 0}
        gate = os.open(NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            names = set(os.listdir(gate))
            allowed = {'private', 'projection', 'results', 'proposals', 'custody.json', NATIVE_ENV.name}
            if (names - allowed or names != allowed and not (rebind is not None
                    and rebind['bindings']['custody.json']['ready'] and names == allowed - {'custody.json'})):
                raise CustodyError('native-source-installer-state-unmanaged')
            for relative, uid, gid, mode in _native_layout(doc, identity):
                child = _native_directory_fd(gate, relative)
                try:
                    actual, expected = os.fstat(child), prepare['children'].get(relative)
                    if (expected != {'inode': [actual.st_dev, actual.st_ino], 'ready': True}
                            or (actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode)) != (uid, gid, mode)):
                        raise CustodyError('native-source-installer-child-changed')
                    _native_no_acl(child)
                    if relative != 'private/oauth':
                        _native_verify_data_tree(child, owners={uid}, group=gid, private=relative == 'private', remaining=[MAX_RETIRED_ACTION_FILES])
                finally:
                    os.close(child)
            fd = os.open(NATIVE_ENV.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=gate)
            try:
                native_verify_env_fd(fd, identity['id'])
                env_body = os.read(fd, MAX_RESULT_BYTES + 1)
            finally:
                os.close(fd)
            _native_environment_data(env_body.decode('utf-8'), parse_env(files['source-broker.env'].decode('utf-8')))
            before, inode = (_native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) if rebind is None
                else (_native_encoded(rebind['bindings']['custody.json']['before']), rebind['bindings']['custody.json']['beforeInode']))
            images['custody.json'] = {'before': before, 'inode': inode,
                'after': _native_encoded(_native_descriptor(target, identity, owner_guid, kit_sha)), 'mode': 0o640, 'gid': identity['id']}
        finally:
            os.close(gate)
        parent_info = os.fstat(kit_parent)
        _native_no_acl(kit_parent)
        if (parent_info.st_uid, parent_info.st_gid, stat.S_IMODE(parent_info.st_mode)) != (0, 0, 0o755):
            raise CustodyError('native-source-installer-kit-parent-unsafe')
        plan = {'authority': authority, 'intent': authority['proof'], 'receipt': receipt, 'identity': identity,
            'ownerGuid': owner_guid, 'oldBinding': old_binding, 'binding': binding, 'kitSha256': kit_sha,
            'environment': dict(receipt['environment']), 'token': receipt['token'], 'gate': prepare['gate'],
            'children': prepare['children'], 'images': images, 'publication': {'before': old_publication, 'inode': pub_inode},
            'oldKit': old_kit, 'targetKit': files, 'kitParent': [parent_info.st_dev, parent_info.st_ino],
            'witness': witness, 'generation': rebind['generation'] if rebind is not None else None}
        if rebind is not None:
            _native_installer_rebind_prefix(selection, publisher, metadata, kit_parent, plan, rebind)
            if _native_json_at(metadata, 'source-installer-rebind.json')[1] != rebind_raw:
                raise CustodyError('native-source-installer-rebind-record-changed')
        # Repeat the receipt proof after every physical read above.
        _native_installer_withdrawal_state(selection.bridge, receipt, transaction=transaction, _rebind=rebind, _completed=_completed)
        return plan


def native_rebind_withdrawn_generation(selection, receipt, *, transaction):
    from .native_executor import _publisher
    return _publisher(selection).rebind_installer_generation(selection, transaction=transaction, receipt=receipt)


def native_verify_installer_rebind_generation(selection, receipt, *, transaction):
    from .native_executor import _publisher
    return _publisher(selection).verify_installer_rebind_generation(selection, transaction=transaction, receipt=receipt)


def native_verify_completed_installer_rebind(selection, receipt, *, transaction):
    """Read-only proof while the installer freshly holds original admission."""
    plan = native_installer_rebind_plan(selection, transaction=transaction, receipt=receipt, _completed=True)
    return {'status': 'inactive', 'generation': plan['generation'],
        'environment': dict(plan['environment'])}



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
                        inherited_gid = 0
                        if relative == 'private/oauth':
                            parent_info = os.fstat(container)
                            if (intent['children'].get('private') != {
                                    'inode': [parent_info.st_dev, parent_info.st_ino], 'ready': True}
                                    or (parent_info.st_uid, parent_info.st_gid, stat.S_IMODE(parent_info.st_mode))
                                        != (uid, gid, 0o700)):
                                raise CustodyError('native-source-descendant-changed')
                            _native_no_acl(container)
                            inherited_gid = gid
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
                                if (created.st_uid != 0 or created.st_gid not in (0, inherited_gid)
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
                                inherited = (inherited_gid != 0 and (actual.st_uid, actual.st_gid) == (0, inherited_gid)
                                    and stat.S_IMODE(actual.st_mode) == 0o700)
                                if (os.listdir(child) or not (inherited or (actual.st_uid, actual.st_gid) in ((0, 0), (uid, gid)))
                                        or stat.S_IMODE(actual.st_mode) not in (0o700, mode)):
                                    raise CustodyError('native-source-descendant-unmanaged')
                                _native_selected(selection)
                                if inherited:
                                    # Darwin inherits the prepared broker parent's
                                    # group. Normalize only this empty, journal-bound
                                    # inode; an existing unbound name still refuses.
                                    os.fchown(child, 0, 0)
                                    os.fsync(child)
                                    actual = os.fstat(child)
                                    _native_no_acl(child)
                                    if (record['inode'] != [actual.st_dev, actual.st_ino]
                                            or (actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode)) != (0, 0, 0o700)
                                            or os.listdir(child)):
                                        raise CustodyError('native-source-descendant-changed')
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


def _native_environment_template(selection, *, _publication=False):
    manifest, checksum = native_verify_kit(selection, _publication=_publication)
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
    _native_selected(selection, _publication=_publication)
    return lines, checksum


def native_environment_text(selection, overrides=None, *, preauth=False, _publication=False):
    """Render only the admitted template; no shell/loader or request paths.

    The Root connection transaction computes these existing capability/query
    choices. This helper is not consent, a permission decision, or a launcher.
    """
    if type(preauth) is not bool or overrides is not None and type(overrides) is not dict:
        raise CustodyError('native-source-environment-invalid')
    lines, _checksum = _native_environment_template(selection, _publication=_publication)
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


def _native_environment_bytes(selection, text, *, _publication=False):
    template, checksum = _native_environment_template(selection, _publication=_publication)
    return _native_environment_data(text, template), checksum


def _native_environment_data(text, template):
    """Pure fixed-field check shared with a qualified installer target kit."""
    if type(text) is not str or '\0' in text:
        raise CustodyError('native-source-environment-invalid')
    try:
        data = text.encode('utf-8')
        lines = parse_env(text)
    except UnicodeError:
        raise CustodyError('native-source-environment-invalid') from None
    if not 0 < len(data) <= MAX_RESULT_BYTES:
        raise CustodyError('native-source-environment-invalid')
    values, base = dict(lines), dict(template)
    mutable = {*PREAUTH_FLAGS, 'PIXEL_SOURCE_CALENDAR_ID', 'PIXEL_SOURCE_TIME_ZONE',
               'PIXEL_SOURCE_GMAIL_QUERY', 'PIXEL_SOURCE_GMAIL_SENT_QUERY'}
    fixed = _native_environment_paths()
    if (list(values) != list(base) or any(values.get(key) != value for key, value in fixed.items())
            or any(values[key] != value for key, value in base.items() if key not in mutable and key not in fixed)
            or any(values[key] not in ('0', '1') for key in PREAUTH_FLAGS if key in values)):
        raise CustodyError('native-source-environment-invalid')
    return data


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
