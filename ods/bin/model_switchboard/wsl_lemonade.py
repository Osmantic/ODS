"""Bounded access to this WSL installation's owned Windows Lemonade task.

This is a finite PowerShell command, not a server. The controller is the
authority for task/process ownership; a flag or reachable port never is.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import shutil
import stat
import subprocess
import threading
import time
from typing import NamedTuple
from urllib.parse import urlsplit


_LIMIT = 65536
_WSLPATH = "/usr/bin/wslpath"
_CONTROLLER = "installers/windows/portal-model-control.ps1"
_SOURCE = Path(__file__).resolve().parents[2]
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PLAN_KEYS = {"ExecutablePath", "Port", "ModelsDir", "ContextSize", "GgufFile",
              "WslDistro", "WslInstallDir"}


class BridgeError(OSError):
    """A rejected or uncertain controller operation; never retry blindly."""

    def __init__(self, message: str, *, code: str = "wsl_lemonade_unavailable", response=None):
        super().__init__(message)
        self.code = code
        self.response = response or {}
        digest = self.response.get("newPlanDigest")
        self.new_plan_digest = digest if isinstance(digest, str) and _DIGEST.fullmatch(digest) else None


class _WindowsTools(NamedTuple):
    shell: str
    probe: str
    module_path: str


class _Context(NamedTuple):
    distro: str
    install_dir: str
    controller: str
    windows: _WindowsTools


def _run(command: list[str], *, data: bytes | None = None, timeout: float = 10,
         environ: dict | None = None) -> subprocess.CompletedProcess:
    """Bound memory as well as wall time, including a noisy/crashed controller."""
    if not math.isfinite(timeout) or not 0 < timeout <= 1200:
        raise ValueError("Invalid Windows controller deadline")
    if data is not None and len(data) > _LIMIT:
        raise ValueError("Windows controller request exceeds 64 KiB")
    process = subprocess.Popen(command, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environ)
    output = {}
    oversized = threading.Event()

    def read(name, stream):
        content = stream.read(_LIMIT + 1)
        output[name] = content
        if len(content) > _LIMIT:
            oversized.set()
            try:
                process.kill()
            except ProcessLookupError:
                pass
        stream.close()

    def write():
        try:
            process.stdin.write(data)
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # The bounded process result below is authoritative.
        finally:
            process.stdin.close()

    threads = [threading.Thread(target=read, args=(name, stream), daemon=True)
               for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    if data is not None:
        threads.append(threading.Thread(target=write, daemon=True))
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise
    finally:
        for thread in threads:
            thread.join(timeout=1)
    if oversized.is_set() or any(thread.is_alive() for thread in threads):
        raise BridgeError("Windows controller output exceeds its bounded contract")
    return subprocess.CompletedProcess(command, process.returncode, output["stdout"], output["stderr"])


def _remaining_timeout(limit: float, deadline: float | None) -> float:
    """Cap a read-only proof step by the caller's remaining deadline."""
    if deadline is None:
        return limit
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BridgeError("Windows ownership verification exceeded its deadline")
    return min(limit, remaining)


def _socket_identity(value: str):
    """Only root-owned WSL sockets in root-protected directories are eligible."""
    if not isinstance(value, str) or not re.fullmatch(r"/run/WSL/[1-9][0-9]*_interop", value):
        return None
    try:
        for parent in (Path("/run"), Path("/run/WSL")):
            row = parent.lstat()
            if not stat.S_ISDIR(row.st_mode) or row.st_uid != 0 or row.st_mode & 0o022:
                return None
        row = Path(value).lstat()
        if not stat.S_ISSOCK(row.st_mode) or row.st_uid != 0:
            return None
        return row.st_dev, row.st_ino
    except OSError:
        return None


def _sockets() -> list[tuple[str, tuple]]:
    inherited = os.environ.get("WSL_INTEROP", "")
    candidates = {inherited: 0}
    try:
        with os.scandir("/run/WSL") as entries:
            for index, entry in enumerate(entries):
                if index >= 64:
                    break
                try:
                    candidates[entry.path] = entry.stat(follow_symlinks=False).st_mtime_ns
                except OSError:
                    continue
    except OSError as exc:
        if not _socket_identity(inherited):
            raise BridgeError("No trusted WSL interop session is available") from exc
    result = []
    # Prefer the caller's session, then recent sessions. Do not silently drop
    # a responsive session because scandir happened to list stale ones first.
    for candidate in sorted(candidates, key=lambda item: (item == inherited, candidates[item]), reverse=True):
        identity = _socket_identity(candidate)
        if identity:
            result.append((candidate, identity))
    return result


def candidate(env: dict) -> bool:
    return (platform.system() == "Linux" and "microsoft" in platform.release().casefold()
            and env.get("LEMONADE_HOST_TRANSPORT") == "model-router")


def _text(value, maximum=4096) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value)


def _windows_path(value) -> bool:
    return (_text(value) and bool(re.match(r"^[A-Za-z]:[\\/]", value))
            and ".." not in PureWindowsPath(value).parts
            and not any(character in value[2:] for character in ':*?"<>|'))


def _gguf(value) -> bool:
    return (_text(value, 240) and value.lower().endswith(".gguf")
            and not any(character in value for character in '/\\:*?"<>|')
            and value == value.strip() and not value.startswith("."))


def _path(path: str, direction: str, *, deadline: float | None = None) -> str:
    if not _text(path):
        raise ValueError("Invalid path for WSL translation")
    result = _run([_WSLPATH, direction, "-a", path],
                  timeout=_remaining_timeout(5, deadline))
    if result.returncode:
        raise BridgeError("WSL path translation failed")
    value = result.stdout.decode("utf-8-sig").strip()
    if not _text(value):
        raise BridgeError("WSL path translation returned an invalid path")
    return value


def _windows_tools(env: dict, *, deadline: float | None = None) -> _WindowsTools:
    system = env.get('ODS_WINDOWS_SYSTEM_DIRECTORY', '')
    if not system:
        # Compatibility for an older interactive installation only. A Linux
        # executable or UNC path cannot become a Windows control executable.
        inherited = shutil.which('powershell.exe')
        if not inherited:
            raise BridgeError('Windows system directory is unknown; rerun the Windows installer')
        windows_shell = _path(inherited, '-w', deadline=deadline)
        if (not _windows_path(windows_shell)
                or tuple(part.casefold() for part in PureWindowsPath(windows_shell).parts[-3:])
                != ('windowspowershell', 'v1.0', 'powershell.exe')):
            raise BridgeError('The inherited Windows PowerShell path is not a system executable')
        system = str(PureWindowsPath(windows_shell).parents[2])
    if not _windows_path(system) or PureWindowsPath(system).name.casefold() != 'system32':
        raise ValueError('ODS_WINDOWS_SYSTEM_DIRECTORY must name a local Windows System32 directory')
    windows = PureWindowsPath(system)
    drive = Path(_path(windows.anchor, '-u', deadline=deadline))
    directory = drive.joinpath(*windows.parts[1:])
    if (not directory.is_absolute() or directory.resolve() != directory
            or PureWindowsPath(_path(str(directory), '-w', deadline=deadline)) != windows):
        raise BridgeError('The Windows system directory did not survive canonical WSL translation')
    shell = directory / 'WindowsPowerShell/v1.0/powershell.exe'
    probe = directory / 'whoami.exe'
    if any(not path.is_file() or path.is_symlink() or path.resolve() != path for path in (shell, probe)):
        raise BridgeError('Windows system PowerShell interop is unavailable; rerun the Windows installer')
    return _WindowsTools(str(shell), str(probe), str(windows / 'WindowsPowerShell/v1.0/Modules'))


def _context(install_dir: Path, env: dict, *, deadline: float | None = None) -> _Context:
    root = Path(install_dir).resolve(strict=True)
    if not root.is_dir() or not root.as_posix().startswith("/"):
        raise ValueError("A canonical WSL installation directory is required")
    # Execute the controller shipped with this trusted module. A candidate
    # uninstaller can retire an older target whose tree lacks these scripts;
    # the target root remains the exact Windows task/plan binding below.
    controller = _SOURCE / _CONTROLLER
    if controller.is_symlink() or not controller.is_file() or controller.resolve().parent != controller.parent:
        raise BridgeError("The installed Windows model controller is unavailable")
    windows = _windows_tools(env, deadline=deadline)
    windows_root = _path("/", "-w", deadline=deadline)
    match = re.fullmatch(r"\\\\(?:wsl\.localhost|wsl\$)\\([^\\/]+)\\?", windows_root, re.IGNORECASE)
    if not match or not _text(match[1], 128) or match[1] in {".", ".."}:
        raise BridgeError("Cannot identify this WSL distribution")
    return _Context(match[1], root.as_posix(), _path(str(controller), "-w", deadline=deadline), windows)


def _plan(plan, context: _Context) -> None:
    if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS:
        raise ValueError("Invalid Windows Lemonade plan schema")
    if (not isinstance(plan["WslDistro"], str) or plan["WslDistro"].casefold() != context.distro.casefold()
            or plan["WslInstallDir"] != context.install_dir
            or not _windows_path(plan["ExecutablePath"]) or not _windows_path(plan["ModelsDir"])
            or not _gguf(plan["GgufFile"]) or type(plan["Port"]) is not int
            or not 1 <= plan["Port"] <= 65535 or type(plan["ContextSize"]) is not int
            or not 4096 <= plan["ContextSize"] <= 262144):
        raise ValueError("Windows Lemonade plan does not match this installation")


def _response(value, context: _Context) -> dict:
    if not isinstance(value, dict) or value.get("ok") is not True or type(value.get("managed")) is not bool:
        raise BridgeError("Windows controller did not return an ownership result")
    if type(value.get("running")) is not bool:
        raise BridgeError("Windows controller returned an invalid process status")
    if not value["managed"]:
        return value
    _plan(value.get("plan"), context)
    if (not isinstance(value.get("planDigest"), str) or not _DIGEST.fullmatch(value["planDigest"])
            or value.get("modelStoreWindowsPath") != value["plan"]["ModelsDir"]):
        raise BridgeError("Windows controller returned an invalid plan identity")
    expected_path = PureWindowsPath(value["plan"]["ModelsDir"]).parent / "portal-runtime" / "runtime.json"
    if not _windows_path(value.get("planPathWindows")) or PureWindowsPath(value["planPathWindows"]) != expected_path:
        raise BridgeError("Windows controller returned an unexpected durable plan path")
    observed = value.get("observation")
    if observed is not None and (not isinstance(observed, dict) or observed.get("status") != "verified"
            or not _text(observed.get("modelId"), 512) or type(observed.get("contextLength")) is not int
            or not 4096 <= observed["contextLength"] <= 262144 or not value["running"]):
        raise BridgeError("Windows controller returned an invalid runtime observation")
    return value


def _environment(socket: str, windows: _WindowsTools) -> dict:
    environ = os.environ.copy()
    environ["WSL_INTEROP"] = socket
    # A WSL session started from PS7 otherwise gives Windows PowerShell 5.1
    # the PS7 module search path, breaking even Get-Acl. Scope this override
    # to this child; /w explicitly exports it from WSL to Windows.
    environ["PSModulePath"] = windows.module_path
    exports = [entry for entry in environ.get("WSLENV", "").split(":")
               if entry and entry.split("/", 1)[0].casefold() != "psmodulepath"]
    environ["WSLENV"] = ":".join([*exports, "PSModulePath/w"])
    return environ


def _probe(socket_info: tuple, timeout: float, windows: _WindowsTools) -> bool:
    socket, identity = socket_info
    if _socket_identity(socket) != identity:
        return False
    # Fixed, read-only native executable: liveness only, never ownership.
    result = _run([windows.probe, "/user", "/fo", "csv", "/nh"], timeout=timeout,
                  environ=_environment(socket, windows))
    return result.returncode == 0 and bool(result.stdout.strip()) and _socket_identity(socket) == identity


def _select_socket(windows: _WindowsTools, *, excluded: tuple = (), timeout: float = 3) -> tuple:
    sockets = [socket for socket in _sockets() if socket not in excluded]
    if not sockets:
        raise BridgeError("No trusted WSL interop session is available")
    deadline = time.monotonic() + timeout
    for socket in sockets:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            # A cold Windows executable can take over 500 ms (antivirus and
            # image loading); keep the global bound, not a warm-start cutoff.
            if _probe(socket, min(remaining, 2), windows):
                return socket
        except subprocess.TimeoutExpired:
            continue  # Only this native read-only liveness probe is retried.
    raise BridgeError("No responsive trusted WSL interop session is available")


def _call(context: _Context, socket_info: tuple, request: dict, timeout: float) -> dict:
    socket, identity = socket_info
    if _socket_identity(socket) != identity:
        raise BridgeError("The trusted WSL interop session changed before dispatch", code="interop_changed")
    message = {"action": "status", "distro": context.distro, "installDir": context.install_dir, **request}
    data = json.dumps(message, allow_nan=False, separators=(",", ":")).encode("utf-8")
    result = _run([context.windows.shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", context.controller], data=data, timeout=timeout, environ=_environment(socket, context.windows))
    if _socket_identity(socket) != identity:
        raise BridgeError("The WSL session changed during the operation; inspect status before retrying",
                          code="interop_changed")
    if result.returncode and not result.stdout.strip() and b"invalid argument" in result.stderr.lower():
        raise BridgeError("The selected WSL interop session is unavailable", code="interop_unavailable")
    try:
        value = json.loads(result.stdout.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        # The fixed controller accepts no credentials and emits only its
        # ownership result/errors. Keep diagnostics bounded; never log env/stdin.
        logging.getLogger(__name__).warning(
            "Windows Lemonade controller returned invalid JSON (exit=%s, stdout=%r, stderr=%r)",
            result.returncode, result.stdout[:500], result.stderr[:500])
        raise BridgeError("Windows controller returned invalid JSON") from exc
    if result.returncode != 0 or (isinstance(value, dict) and value.get("ok") is False):
        failure = {}
        if isinstance(value, dict):
            for key in ("code", "error", "newPlanDigest"):
                if _text(value.get(key), 2000):
                    failure[key] = value[key]
        raise BridgeError(failure.get("error", "Windows controller operation failed"),
                          code=failure.get("code", "windows_controller_failed"), response=failure)
    return _response(value, context)


def _endpoint_matches_plan(env: dict, value: dict) -> None:
    if not value["managed"]:
        return
    port = value["plan"]["Port"]
    for key, hosts in (("LEMONADE_BASE_URL", {"localhost", "127.0.0.1", "::1"}),
                       ("LEMONADE_CONTAINER_BASE_URL", {"host.docker.internal"})):
        if key not in env:
            continue
        try:
            raw = env[key]
            url = urlsplit(raw) if _text(raw) else None
            valid = (url is not None and url.scheme == "http" and url.hostname in hosts
                     and url.username is None and url.password is None and not url.query and not url.fragment
                     and url.path.rstrip("/") in {"", "/api", "/api/v1"}
                     and (url.port or 80) == port)
        except ValueError:
            valid = False
        if not valid:
            raise BridgeError("The configured Lemonade endpoint does not match the owned Windows task", code="endpoint_mismatch")
    if "AMD_INFERENCE_PORT" in env and str(env["AMD_INFERENCE_PORT"]) != str(port):
        raise BridgeError("The configured Lemonade port does not match the owned Windows task", code="endpoint_mismatch")


def _connected_status(install_dir: Path, env: dict, *, deadline: float | None = None):
    if not candidate(env):
        raise BridgeError("This installation does not use managed WSL Lemonade", code="unsupported_runtime")
    if deadline is not None:
        deadline = min(deadline, time.monotonic() + 18)
    context = _context(install_dir, env, deadline=deadline)
    if deadline is None:
        deadline = time.monotonic() + 18
    excluded = ()
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BridgeError("Windows ownership verification exceeded its deadline")
        socket = _select_socket(context.windows, excluded=excluded, timeout=min(3, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BridgeError("Windows ownership verification exceeded its deadline")
        try:
            # Ownership needs its full budget; the former 5-second discovery
            # attempts killed healthy controllers before checks completed.
            value = _call(context, socket, {}, min(15, remaining))
        except BridgeError as exc:
            if exc.code != "interop_changed" or attempt:
                raise
            # A short-lived interactive session can disappear after its probe.
            # Retry only read-only status after proven inode loss/replacement.
            excluded = (socket,)
            continue
        _endpoint_matches_plan(env, value)
        return context, socket, value


def status(install_dir: Path, env: dict, *, deadline: float | None = None) -> dict:
    if not candidate(env):
        return {"ok": True, "managed": False, "running": False}
    return _connected_status(install_dir, env, deadline=deadline)[2]


def _mutate(install_dir: Path, env: dict, action: str, expected_plan_digest: str, **values) -> dict:
    if not isinstance(expected_plan_digest, str) or not _DIGEST.fullmatch(expected_plan_digest):
        raise ValueError("A valid expected plan digest is required")
    context, socket, current = _connected_status(install_dir, env)
    if not current["managed"]:
        raise BridgeError("The Windows Lemonade task is not managed by this installation", code="unmanaged_runtime")
    if current["planDigest"] != expected_plan_digest:
        raise BridgeError("The Windows Lemonade plan changed before activation", code="plan_changed")
    if action == "restore":
        _plan(values.get("plan"), context)
        immutable = _PLAN_KEYS - {"GgufFile", "ContextSize", "WslDistro"}
        if any(values["plan"][key] != current["plan"][key] for key in immutable):
            raise ValueError("Restore cannot change runtime ownership or location")
    result = _call(context, socket, {"action": action, "expectedPlanDigest": expected_plan_digest, **values},
                   1200 if action in {"activate", "restore", "start"} else 90)
    if not result["managed"] or result["running"] != (action != "stop"):
        raise BridgeError("Windows controller did not prove the requested process state")
    target = values.get("plan") if action == "restore" else current["plan"]
    gguf = values.get("gguf", target["GgufFile"])
    size = values.get("contextSize", target["ContextSize"])
    if result["plan"]["GgufFile"] != gguf or result["plan"]["ContextSize"] != size:
        raise BridgeError("Windows controller did not persist the requested model plan")
    if action != "stop" and (not result.get("observation") or result["observation"]["contextLength"] != size):
        raise BridgeError("Windows controller did not prove the requested model context")
    return result


def activate(install_dir: Path, env: dict, gguf: str, context_size: int, expected_plan_digest: str) -> dict:
    if not _gguf(gguf) or type(context_size) is not int or not 4096 <= context_size <= 262144:
        raise ValueError("A safe GGUF filename and supported context size are required")
    return _mutate(install_dir, env, "activate", expected_plan_digest, gguf=gguf, contextSize=context_size)


def restore(install_dir: Path, env: dict, plan: dict, expected_plan_digest: str) -> dict:
    return _mutate(install_dir, env, "restore", expected_plan_digest, plan=plan)


def stop(install_dir: Path, env: dict, expected_plan_digest: str) -> dict:
    return _mutate(install_dir, env, "stop", expected_plan_digest)


def start(install_dir: Path, env: dict, expected_plan_digest: str) -> dict:
    return _mutate(install_dir, env, "start", expected_plan_digest)


def disable_startup(install_dir: Path, env: dict, *, validate_only: bool = False,
                    retire_relay: bool = False) -> dict:
    """Retire bound Windows login startup and, for uninstall, its owned relay."""
    state_root = env.get('ODS_WSL_STATE_ROOT')
    if 'ODS_WSL_STATE_ROOT' in env:
        if not _text(state_root) or any(character in state_root for character in '\"*?<>|'):
            raise ValueError('ODS_WSL_STATE_ROOT must name an absolute Windows state directory')
        path = PureWindowsPath(state_root)
        local = bool(re.match(r'^[A-Za-z]:[\\/]', state_root))
        unc = state_root.startswith('\\\\') and len(path.drive.split('\\')) == 4
        if (not path.is_absolute() or not (local or unc) or path == PureWindowsPath(path.anchor)
                or re.search(r'(^|[\\/])\.{1,2}([\\/]|$)', state_root)
                or ':' in (state_root[2:] if local else state_root)):
            raise ValueError('ODS_WSL_STATE_ROOT must not be a filesystem root, device or traversal path')
    context = _context(install_dir, env)
    program = _SOURCE / 'installers/wsl-lifecycle.ps1'
    if not program.is_file() or program.is_symlink() or program.resolve() != program:
        raise BridgeError('The installed WSL startup controller is unavailable')
    controller = _path(str(program), '-w')
    socket, identity = _select_socket(context.windows)
    if _socket_identity(socket) != identity:
        raise BridgeError('The WSL session changed before startup retirement')
    command = [context.windows.shell, '-NoLogo', '-NoProfile', '-NonInteractive',
               '-ExecutionPolicy', 'Bypass', '-File', controller,
               '-Action', 'disable-startup', '-Distro', context.distro,
               '-InstallRoot', context.install_dir]
    if state_root is not None:
        command.extend(['-StateRoot', state_root])
    if validate_only:
        command.append('-ValidateOnly')
    if retire_relay:
        command.append('-RetireRelay')
    # Mutations are issued once, including an uncertain/expired interop call.
    # Relay retirement adds bounded scheduler/child shutdown after command.lock.
    result = _run(command, timeout=90 if retire_relay and not validate_only else 45,
                  environ=_environment(socket, context.windows))
    if _socket_identity(socket) != identity:
        raise BridgeError('The WSL session changed during startup retirement; inspect before retrying')
    if result.returncode:
        raise BridgeError('Windows startup ownership could not be verified; installation retained')
    try:
        value = json.loads(result.stdout.decode('utf-8-sig'))
    except (ValueError, UnicodeError) as exc:
        raise BridgeError('Windows startup controller returned invalid JSON') from exc
    expected = {'unmanaged', 'validated' if validate_only else 'disabled'}
    owner = value.get('identity') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('scope') != 'wsl-startup'
            or value.get('state') not in expected or not isinstance(owner, dict)
            or not isinstance(owner.get('distro'), str)
            or owner['distro'].casefold() != context.distro.casefold()
            or owner.get('installRoot') != context.install_dir):
        raise BridgeError('Windows startup controller did not prove this installation was retired')
    if retire_relay:
        expected_relay = {'unmanaged': 'unmanaged', 'validated': 'validated', 'disabled': 'stopped'}[value['state']]
        if value.get('relayRetirement') != expected_relay:
            raise BridgeError('Windows startup controller did not prove owned relay retirement')
    return value


def _owned_path(install_dir: Path, env: dict, status_info: dict | None, field: str,
                *, deadline: float | None = None) -> Path:
    if not candidate(env):
        raise BridgeError("This installation does not use managed WSL Lemonade", code="unsupported_runtime")
    context = _context(install_dir, env, deadline=deadline)
    proof = _response(status_info, context) if status_info is not None else status(install_dir, env, deadline=deadline)
    if not proof["managed"]:
        raise BridgeError("The Windows model store is not owned by this installation")
    windows = proof[field]
    # Docker Desktop registers bind aliases for individual Windows folders.
    # wslpath then prefers that temporary alias for the complete path. Resolve
    # only the drive's mount and append validated components, so registration
    # remains stable across Compose recreations and custom DrvFS mount roots.
    windows_path = PureWindowsPath(windows)
    drive = Path(_path(windows_path.anchor, "-u", deadline=deadline))
    root = drive.joinpath(*windows_path.parts[1:])
    exists = root.is_dir() if field == "modelStoreWindowsPath" else root.is_file()
    if not root.is_absolute() or root.is_symlink() or not exists or root.resolve() != root:
        raise BridgeError("The owned Windows path is not available as a canonical WSL path")
    if PureWindowsPath(_path(str(root), "-w", deadline=deadline)) != PureWindowsPath(windows):
        raise BridgeError("The owned Windows path did not survive translation")
    return root


def model_store(install_dir: Path, env: dict, status_info: dict | None = None,
                *, deadline: float | None = None) -> Path:
    return _owned_path(install_dir, env, status_info, "modelStoreWindowsPath", deadline=deadline)


def plan_path(install_dir: Path, env: dict, status_info: dict | None = None,
              *, deadline: float | None = None) -> Path:
    return _owned_path(install_dir, env, status_info, "planPathWindows", deadline=deadline)


# ---------------------------------------------------------------------------
# Catalog staging into the managed Windows runtime store.
#
# The observed failure: a catalog model advertised as installed in a WSL
# registered store (e.g. /home/odsfresh/ods/data/models) is refused by the
# host agent because the Windows controller only accepts a GGUF that already
# lives in the managed Windows runtime store. Rather than weaken ownership or
# hide the button, we stage a *verified* copy of the catalog artifact into the
# exact managed store, then let the existing activate transaction run.
#
# Guarantees:
#   * Only catalog models with a complete manifest (size + sha256 for every
#     artifact) are eligible. Size-only manifests are rejected.
#   * Source bytes are never mutated or deleted; the source cache is preserved.
#   * Promotion uses an atomic hard link and never overwrites an existing file.
#   * Free space is checked before any bytes are written.
#   * Ambiguous duplicate basenames with differing bytes are rejected.
#   * Corrupt targets are rejected; cleanup removes only this operation's temp file.
#   * No symlinks, non-regular files, or cross-root paths are accepted.
#   * Bytes are streamed; no large buffers are held in memory.
# ---------------------------------------------------------------------------

_STAGE_CHUNK = 8 * 1024 * 1024
_STAGE_TEMP_PREFIX = ".ods-stage-"


class StageError(BridgeError):
    """A rejected or failed catalog staging operation."""

    def __init__(self, message: str, *, code: str = "stage_failed"):
        super().__init__(message, code=code)


def _is_regular_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def _sha256_file(path: Path, cancel_event: threading.Event | None = None) -> str:
    digest = __import__("hashlib").sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_STAGE_CHUNK), b""):
            if cancel_event is not None and cancel_event.is_set():
                raise StageError("staging cancelled", code="stage_cancelled")
            digest.update(chunk)
    if cancel_event is not None and cancel_event.is_set():
        raise StageError("staging cancelled", code="stage_cancelled")
    return digest.hexdigest()


def _free_bytes(path: Path) -> int:
    usage = shutil.disk_usage(str(path))
    return usage.free


def _resolve_catalog_source(
    source_root: Path,
    filename: str,
    expected_sha: str,
    expected_size: int | None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Return the unique regular file in source_root matching filename+sha.

    Rejects symlinks, non-regular files, and any path that escapes source_root.
    """
    if not isinstance(filename, str) or not filename or any(c in filename for c in "/\\\x00\n\r"):
        raise StageError("catalog filename is unsafe", code="stage_unsafe_name")
    if filename in {".", ".."}:
        raise StageError("catalog filename is unsafe", code="stage_unsafe_name")
    source_root = Path(source_root)
    if not source_root.is_absolute() or any(p.is_symlink() for p in (source_root, *source_root.parents)):
        raise StageError("catalog source root is redirected", code="stage_source_escape")
    try:
        root = source_root.resolve(strict=True)
    except OSError as exc:
        raise StageError(f"catalog source root is unavailable: {exc}", code="stage_source_missing") from exc
    if not root.is_dir():
        raise StageError("catalog source root is not a directory", code="stage_source_missing")
    candidate = root / filename
    if not _is_regular_file(candidate):
        raise StageError(f"catalog source {filename!r} is not a regular file", code="stage_source_missing")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise StageError(f"catalog source {filename!r} could not be resolved: {exc}", code="stage_source_missing") from exc
    if resolved.parent != root:
        raise StageError(f"catalog source {filename!r} escapes its store", code="stage_source_escape")
    if expected_size is not None:
        try:
            actual_size = resolved.stat().st_size
        except OSError as exc:
            raise StageError(f"catalog source {filename!r} could not be inspected: {exc}", code="stage_source_missing") from exc
        if actual_size != expected_size:
            raise StageError(
                f"catalog source {filename!r} size mismatch: expected {expected_size}, got {actual_size}",
                code="stage_source_mismatch",
            )
    actual_sha = _sha256_file(resolved, cancel_event)
    if actual_sha != expected_sha:
        raise StageError(
            f"catalog source {filename!r} SHA256 mismatch",
            code="stage_source_mismatch",
        )
    return resolved


def _existing_target_matches(
    target: Path,
    expected_sha: str,
    expected_size: int | None,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Return True iff target is a regular file with the expected bytes."""
    if not _is_regular_file(target):
        return False
    try:
        if expected_size is not None and target.stat().st_size != expected_size:
            return False
    except OSError:
        return False
    try:
        return _sha256_file(target, cancel_event) == expected_sha
    except OSError:
        return False


def _validate_artifact_entry(artifact: dict) -> tuple[str, str, int | None]:
    """Validate one manifest entry and return (filename, sha, size)."""
    if not isinstance(artifact, dict):
        raise StageError("catalog artifact is not an object", code="stage_manifest_invalid")
    filename = artifact.get("file")
    expected_sha = str(artifact.get("sha256") or "").strip().lower()
    expected_size = artifact.get("size_bytes")
    if not isinstance(filename, str) or not filename:
        raise StageError("catalog artifact is missing a filename", code="stage_manifest_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise StageError(
            f"catalog artifact {filename!r} has no valid SHA256; refusing size-only trust",
            code="stage_manifest_untrusted",
        )
    if expected_size is not None and (type(expected_size) is not int or expected_size <= 0):
        raise StageError(f"catalog artifact {filename!r} has an invalid size", code="stage_manifest_invalid")
    return filename, expected_sha, expected_size


def _stage_one_artifact(
    source_root: Path,
    target_root: Path,
    artifact: dict,
    cancel_event: threading.Event | None = None,
    prepared_source: Path | None = None,
) -> Path:
    """Stage one verified catalog artifact into target_root atomically.

    Returns the final path. Never overwrites an existing file. Cleans up its
    own temporary file on any failure. When ``prepared_source`` is supplied
    the caller has already validated the source bytes; the copy still
    re-verifies the temp file before promotion so a changed source is caught.
    """
    filename, expected_sha, expected_size = _validate_artifact_entry(artifact)

    if prepared_source is not None:
        source = prepared_source
    else:
        source = _resolve_catalog_source(source_root, filename, expected_sha, expected_size, cancel_event)
    target = target_root / filename

    # Reject symlinks or non-regular files at the destination.
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not _is_regular_file(target):
            raise StageError(
                f"managed store entry {filename!r} is not a regular file",
                code="stage_target_unsafe",
            )
        if _existing_target_matches(target, expected_sha, expected_size, cancel_event):
            # Same verified bytes already present: reuse, do not rewrite.
            return target
        raise StageError(
            f"managed store already contains a different {filename!r}; refusing to overwrite",
            code="stage_target_conflict",
        )

    # Free-space check: need source size plus a small margin for the temp file.
    try:
        source_size = source.stat().st_size
    except OSError as exc:
        raise StageError(f"catalog source {filename!r} could not be sized: {exc}", code="stage_source_missing") from exc
    try:
        free = _free_bytes(target_root)
    except OSError as exc:
        raise StageError(f"managed store is not writable: {exc}", code="stage_target_unwritable") from exc
    if free < source_size + (16 * 1024 * 1024):
        raise StageError(
            f"insufficient free space in managed store for {filename!r}: need {source_size}, have {free}",
            code="stage_no_space",
        )

    # Stream copy into a private temp file in the same directory, then fsync
    # and atomically promote via os.link (no-overwrite).
    temp_path: Path | None = None
    try:
        fd, temp_name = __import__("tempfile").mkstemp(
            prefix=_STAGE_TEMP_PREFIX, suffix=".part", dir=str(target_root),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as destination, source.open("rb") as src:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise StageError("staging cancelled", code="stage_cancelled")
                    chunk = src.read(_STAGE_CHUNK)
                    if not chunk:
                        break
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        # Verify the temp file before promotion. This catches a source that
        # changed after preflight even when the caller pre-verified it.
        if _sha256_file(temp_path, cancel_event) != expected_sha:
            raise StageError(
                f"staged copy of {filename!r} failed verification",
                code="stage_verify_failed",
            )
        if expected_size is not None and temp_path.stat().st_size != expected_size:
            raise StageError(
                f"staged copy of {filename!r} has wrong size",
                code="stage_verify_failed",
            )
        if cancel_event is not None and cancel_event.is_set():
            raise StageError("staging cancelled", code="stage_cancelled")
        # Atomic no-overwrite promotion. If the target appeared concurrently,
        # os.link + unlink gives us no-overwrite semantics; os.replace would
        # clobber another writer's file. Fail closed if link is unsupported.
        try:
            os.link(str(temp_path), str(target))
        except FileExistsError:
            # Someone else created the target. Accept only if bytes match.
            if _existing_target_matches(target, expected_sha, expected_size, cancel_event):
                return target
            raise StageError(
                f"managed store already contains a different {filename!r}; refusing to overwrite",
                code="stage_target_conflict",
            )
        except OSError as exc:
            raise StageError(
                f"could not promote {filename!r} into the managed store: {exc}",
                code="stage_promote_failed",
            ) from exc
        # Remove the temp link; the target now owns the inode.
        try:
            temp_path.unlink()
        except OSError:
            pass
        temp_path = None
        return target
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def stage_catalog_artifact(
    source_root: Path,
    target_root: Path,
    manifest: dict,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Stage every artifact in a complete catalog manifest into target_root.

    The manifest must be a dict with an ``artifacts`` list; each entry must
    carry ``file``, ``sha256`` and (optionally) ``size_bytes``. Size-only
    entries are rejected: the caller must supply full catalog hashes.

    Returns the list of final paths in the managed store. On any failure the
    function raises StageError and leaves the source cache untouched.
    """
    if not isinstance(manifest, dict):
        raise StageError("catalog manifest is not an object", code="stage_manifest_invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise StageError("catalog manifest has no artifacts", code="stage_manifest_invalid")
    target_root = Path(target_root)
    if not target_root.is_absolute() or any(p.is_symlink() for p in (target_root, *target_root.parents)):
        raise StageError("managed store is redirected", code="stage_target_unsafe")
    try:
        target_root = target_root.resolve(strict=True)
    except OSError as exc:
        raise StageError(f"managed store is unavailable: {exc}", code="stage_target_missing") from exc
    if not target_root.is_dir() or target_root.is_symlink():
        raise StageError("managed store is not a regular directory", code="stage_target_unsafe")

    # ---- Preflight: validate every artifact before any copy. ----
    # Casefold collisions are rejected even when the hashes match, because
    # the managed store may be case-insensitive on the Windows side.
    seen_casefold: dict[str, str] = {}
    validated: list[tuple[dict, str, str, int | None]] = []
    for artifact in artifacts:
        filename, expected_sha, expected_size = _validate_artifact_entry(artifact)
        folded = filename.casefold()
        if folded in seen_casefold:
            raise StageError(
                f"catalog manifest lists {filename!r} colliding with {seen_casefold[folded]!r}",
                code="stage_manifest_ambiguous",
            )
        seen_casefold[folded] = filename
        validated.append((artifact, filename, expected_sha, expected_size))

    # Resolve and verify every source, and check every existing target, before
    # publishing the first artifact. This makes a later malformed/missing/bad
    # artifact fail with zero new published targets.
    prepared: list[tuple[dict, str, str, int | None, Path]] = []
    for artifact, filename, expected_sha, expected_size in validated:
        if cancel_event is not None and cancel_event.is_set():
            raise StageError("staging cancelled", code="stage_cancelled")
        source = _resolve_catalog_source(source_root, filename, expected_sha, expected_size, cancel_event)
        target = target_root / filename
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not _is_regular_file(target):
                raise StageError(
                    f"managed store entry {filename!r} is not a regular file",
                    code="stage_target_unsafe",
                )
            if not _existing_target_matches(target, expected_sha, expected_size, cancel_event):
                raise StageError(
                    f"managed store already contains a different {filename!r}; refusing to overwrite",
                    code="stage_target_conflict",
                )
        prepared.append((artifact, filename, expected_sha, expected_size, source))

    # Total missing-space budget: only bytes that will actually be written
    # (targets not already present with matching bytes) count, plus a small
    # margin per new artifact. Hardlinks do not require two copies of disk
    # space, so existing target bytes are not counted against the budget.
    missing_bytes = 0
    new_count = 0
    for _artifact, filename, _sha, expected_size, source in prepared:
        target = target_root / filename
        if target.exists() and _is_regular_file(target) and not target.is_symlink():
            # Already verified above; reuse, no additional bytes needed.
            continue
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise StageError(
                f"catalog source {filename!r} could not be sized: {exc}",
                code="stage_source_missing",
            ) from exc
        missing_bytes += size
        new_count += 1
    if new_count:
        try:
            free = _free_bytes(target_root)
        except OSError as exc:
            raise StageError(f"managed store is not writable: {exc}", code="stage_target_unwritable") from exc
        margin = 16 * 1024 * 1024 * new_count
        if free < missing_bytes + margin:
            raise StageError(
                f"insufficient free space in managed store: need {missing_bytes}, have {free}",
                code="stage_no_space",
            )

    # ---- Publish. Cancellation is checked immediately before each artifact
    # and again inside _stage_one_artifact before promotion. If a later I/O
    # race fails, previously verified completed artifacts are preserved; we
    # never unlink a target we did not create in this call.
    staged: list[Path] = []
    for artifact, filename, expected_sha, expected_size, source in prepared:
        if cancel_event is not None and cancel_event.is_set():
            raise StageError("staging cancelled", code="stage_cancelled")
        staged.append(
            _stage_one_artifact(
                source_root, target_root, artifact, cancel_event, prepared_source=source,
            )
        )
    return staged


def stage_catalog_model(
    install_dir: Path,
    env: dict,
    status_info: dict | None,
    source_root: Path,
    manifest: dict,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Stage a verified catalog model into the managed Windows runtime store.

    Proves Windows ownership first, then stages every artifact. The caller is
    expected to run the existing activate transaction afterwards; this helper
    never mutates the plan, task, or process.
    """
    target_root = model_store(install_dir, env, status_info)
    return stage_catalog_artifact(source_root, target_root, manifest, cancel_event)
