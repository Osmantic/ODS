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


def _path(path: str, direction: str) -> str:
    if not _text(path):
        raise ValueError("Invalid path for WSL translation")
    result = _run([_WSLPATH, direction, "-a", path], timeout=5)
    if result.returncode:
        raise BridgeError("WSL path translation failed")
    value = result.stdout.decode("utf-8-sig").strip()
    if not _text(value):
        raise BridgeError("WSL path translation returned an invalid path")
    return value


def _windows_tools(env: dict) -> _WindowsTools:
    system = env.get('ODS_WINDOWS_SYSTEM_DIRECTORY', '')
    if not system:
        # Compatibility for an older interactive installation only. A Linux
        # executable or UNC path cannot become a Windows control executable.
        inherited = shutil.which('powershell.exe')
        if not inherited:
            raise BridgeError('Windows system directory is unknown; rerun the Windows installer')
        windows_shell = _path(inherited, '-w')
        if (not _windows_path(windows_shell)
                or tuple(part.casefold() for part in PureWindowsPath(windows_shell).parts[-3:])
                != ('windowspowershell', 'v1.0', 'powershell.exe')):
            raise BridgeError('The inherited Windows PowerShell path is not a system executable')
        system = str(PureWindowsPath(windows_shell).parents[2])
    if not _windows_path(system) or PureWindowsPath(system).name.casefold() != 'system32':
        raise ValueError('ODS_WINDOWS_SYSTEM_DIRECTORY must name a local Windows System32 directory')
    windows = PureWindowsPath(system)
    drive = Path(_path(windows.anchor, '-u'))
    directory = drive.joinpath(*windows.parts[1:])
    if (not directory.is_absolute() or directory.resolve() != directory
            or PureWindowsPath(_path(str(directory), '-w')) != windows):
        raise BridgeError('The Windows system directory did not survive canonical WSL translation')
    shell = directory / 'WindowsPowerShell/v1.0/powershell.exe'
    probe = directory / 'whoami.exe'
    if any(not path.is_file() or path.is_symlink() or path.resolve() != path for path in (shell, probe)):
        raise BridgeError('Windows system PowerShell interop is unavailable; rerun the Windows installer')
    return _WindowsTools(str(shell), str(probe), str(windows / 'WindowsPowerShell/v1.0/Modules'))


def _context(install_dir: Path, env: dict) -> _Context:
    root = Path(install_dir).resolve(strict=True)
    if not root.is_dir() or not root.as_posix().startswith("/"):
        raise ValueError("A canonical WSL installation directory is required")
    # Execute the controller shipped with this trusted module. A candidate
    # uninstaller can retire an older target whose tree lacks these scripts;
    # the target root remains the exact Windows task/plan binding below.
    controller = _SOURCE / _CONTROLLER
    if controller.is_symlink() or not controller.is_file() or controller.resolve().parent != controller.parent:
        raise BridgeError("The installed Windows model controller is unavailable")
    windows = _windows_tools(env)
    windows_root = _path("/", "-w")
    match = re.fullmatch(r"\\\\(?:wsl\.localhost|wsl\$)\\([^\\/]+)\\?", windows_root, re.IGNORECASE)
    if not match or not _text(match[1], 128) or match[1] in {".", ".."}:
        raise BridgeError("Cannot identify this WSL distribution")
    return _Context(match[1], root.as_posix(), _path(str(controller), "-w"), windows)


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


def _connected_status(install_dir: Path, env: dict):
    if not candidate(env):
        raise BridgeError("This installation does not use managed WSL Lemonade", code="unsupported_runtime")
    context = _context(install_dir, env)
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


def status(install_dir: Path, env: dict) -> dict:
    if not candidate(env):
        return {"ok": True, "managed": False, "running": False}
    return _connected_status(install_dir, env)[2]


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


def _owned_path(install_dir: Path, env: dict, status_info: dict | None, field: str) -> Path:
    if not candidate(env):
        raise BridgeError("This installation does not use managed WSL Lemonade", code="unsupported_runtime")
    context = _context(install_dir, env)
    proof = _response(status_info, context) if status_info is not None else status(install_dir, env)
    if not proof["managed"]:
        raise BridgeError("The Windows model store is not owned by this installation")
    windows = proof[field]
    # Docker Desktop registers bind aliases for individual Windows folders.
    # wslpath then prefers that temporary alias for the complete path. Resolve
    # only the drive's mount and append validated components, so registration
    # remains stable across Compose recreations and custom DrvFS mount roots.
    windows_path = PureWindowsPath(windows)
    drive = Path(_path(windows_path.anchor, "-u"))
    root = drive.joinpath(*windows_path.parts[1:])
    exists = root.is_dir() if field == "modelStoreWindowsPath" else root.is_file()
    if not root.is_absolute() or root.is_symlink() or not exists or root.resolve() != root:
        raise BridgeError("The owned Windows path is not available as a canonical WSL path")
    if PureWindowsPath(_path(str(root), "-w")) != PureWindowsPath(windows):
        raise BridgeError("The owned Windows path did not survive translation")
    return root


def model_store(install_dir: Path, env: dict, status_info: dict | None = None) -> Path:
    return _owned_path(install_dir, env, status_info, "modelStoreWindowsPath")


def plan_path(install_dir: Path, env: dict, status_info: dict | None = None) -> Path:
    return _owned_path(install_dir, env, status_info, "planPathWindows")
