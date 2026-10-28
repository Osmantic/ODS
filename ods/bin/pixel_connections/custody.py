"""Root custody of the Source Broker for a connected Google account.

Runs inside the access coordinator. It never runs owner-controlled code: the
installer copies the broker program, its unit files and its environment template
into a root-owned kit beside this module, and this code installs only those bytes.
The Google sign-in reaches the broker identity through a private handoff that
only that identity can read; the token never passes through here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable

BROKER_USER = "pixel-source-broker"
KIT = Path(__file__).resolve().parent / "source-broker-kit"
UNITS = ("pixel-source-broker.service", "pixel-source-broker.timer", "pixel-source-action@.service",
         "pixel-source-reconcile@.service", "pixel-source-direct.service", "pixel-source-direct.path",
         "pixel-source-exchange@.service", "pixel-source-revoke.service")
KIT_FILES = ("broker.py", "source-broker.env", *UNITS)
MAX_RESULT_BYTES = 64 * 1024
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
    for directory in (paths.projection, paths.proposals, paths.results, paths.oauth):
        if directory.is_symlink() or not directory.is_dir():
            continue
        for entry in os.scandir(directory):
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)


def remove_env(paths: Paths) -> None:
    paths.env.unlink(missing_ok=True)
