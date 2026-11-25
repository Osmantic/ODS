"""Ownership-bound launchd service for the ODS macOS LLM bridge."""
from __future__ import annotations

import base64
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import stat
import subprocess
import sys
import time

from macos_native_service import (
    NativeService,
    NativeServiceError,
    NativeServiceUncertain,
)
from pixel_macos_process import ProcessIdentityError, process_identity


_SCRIPT_REL = "bin/ods-macos-llm-bridge.py"
_DEFAULT_LABEL = "com.ods.llm-bridge"
_LEGACY_INTERPRETER = "/usr/bin/python3"
_PROBE_CODE = (
    "import ctypes,os,sys\n"
    "lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)\n"
    "lib.proc_pidpath.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]\n"
    "lib.proc_pidpath.restype=ctypes.c_int\n"
    "b=ctypes.create_string_buffer(4096)\n"
    "n=lib.proc_pidpath(os.getpid(),b,len(b))\n"
    "if not 0<n<len(b) or b.raw[n]!=0: sys.exit(3)\n"
    "raw=b.raw[:n]\n"
    "if b'\\0' in raw: sys.exit(4)\n"
    "sys.stdout.write(os.fsdecode(raw))\n"
)


def _safe_path(value):
    return (isinstance(value, str) and value.startswith("/")
            and not any(c in value for c in "\0\n\r")
            and not any(p in ("", ".", "..") for p in value.split("/")[1:]))


def _ipv4(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return False


class BridgeService(NativeService):
    """Launchd ownership for the fixed bridge script under a trusted interpreter."""

    def __init__(self, install, interpreter, *, home=None,
                 label=_DEFAULT_LABEL, log_path=None):
        root = Path(install).resolve()
        script = (root / _SCRIPT_REL).resolve()
        if not script.is_relative_to(root):
            raise NativeServiceError("Bridge script must belong to the installation")
        if not _safe_path(str(interpreter)):
            raise NativeServiceError("Bridge interpreter path is invalid")
        resolved = Path(interpreter).resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise NativeServiceError("Bridge interpreter is unavailable")
        super().__init__(root, resolved, root / "data" / (label + ".pid"),
                         home=home, label=label)
        self.script = script
        self._probe_cache = {}
        self._log_path = self._resolve_log(log_path)

    def _resolve_log(self, log_path):
        default = self.home / "Library/Logs/ODS" / (self.label + ".log")
        if log_path is None:
            candidate = default
        else:
            if not _safe_path(log_path):
                raise NativeServiceError("Bridge log path is invalid")
            candidate = Path(log_path)
            if not candidate.is_absolute():
                raise NativeServiceError("Bridge log path must be absolute")
        resolved = candidate.resolve()
        allowed = (self.home / "Library/Logs/ODS").resolve()
        if not resolved.is_relative_to(allowed) or resolved == allowed:
            raise NativeServiceError("Bridge log path is outside the owned log subtree")
        return resolved

    def _journal_interpreter(self):
        if not (self.journal.exists() or self.journal.is_symlink()):
            return None
        saved = self.journal_document()
        exe = saved.get("executable")
        if not _safe_path(exe):
            raise NativeServiceError("Bridge journal interpreter is invalid")
        resolved = Path(exe).resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise NativeServiceError("Bridge journal interpreter is unavailable")
        return resolved

    def trusted_binaries(self):
        binaries = [self.executable]
        legacy = Path(_LEGACY_INTERPRETER)
        if legacy.is_file():
            binaries.append(legacy.resolve())
        journal_exe = self._journal_interpreter()
        if journal_exe is not None:
            binaries.append(journal_exe)
        return list(dict.fromkeys(binaries))

    def bound(self, executable):
        executable = Path(executable).resolve()
        if executable not in self.trusted_binaries():
            raise NativeServiceError("Bridge interpreter is not trusted")
        if executable == self.executable:
            return self
        service = BridgeService(self.root, executable, home=self.home,
                                label=self.label, log_path=str(self._log_path))
        service._probe_cache = self._probe_cache
        return service

    def _probe_signature(self, interpreter):
        try:
            info = interpreter.stat()
        except OSError as exc:
            raise NativeServiceError("Bridge interpreter is unavailable") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, self.uid}:
            raise NativeServiceError("Bridge interpreter custody is invalid")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise NativeServiceError("Bridge interpreter is group/world writable")
        return (str(interpreter), info.st_dev, info.st_ino, info.st_size,
                info.st_mtime_ns, info.st_uid, info.st_mode)

    def _probe_kernel_exe(self, interpreter):
        signature = self._probe_signature(interpreter)
        cached = self._probe_cache.get(signature)
        if cached is not None:
            return cached
        try:
            result = subprocess.run(
                [str(interpreter), "-I", "-c", _PROBE_CODE],
                capture_output=True, text=True, timeout=10, check=False,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NativeServiceError("Bridge interpreter probe failed") from exc
        if result.returncode != 0:
            raise NativeServiceError("Bridge interpreter probe failed")
        path = result.stdout
        if len(path) > 4096 or not _safe_path(path):
            raise NativeServiceError("Bridge interpreter probe returned an invalid path")
        self._probe_cache[signature] = path
        return path

    def identity(self, pid):
        if type(pid) is not int or not 0 < pid <= 2147483647:
            raise ProcessIdentityError("gateway-process-specification-invalid")
        kernel_exe = self._probe_kernel_exe(self.executable)
        return process_identity(pid, uid=self.uid, gid=self.gid,
                                executable=kernel_exe)

    def startup_identity_pending(self, error):
        # Apple's Python launcher execs the framework binary after launchd
        # publishes its PID. Retry inspection; never accept the launcher as
        # the final identity or interpret it as an absent process.
        return (super().startup_identity_pending(error)
                or str(error) == "gateway-process-executable-mismatch")

    def producer_service(self, identity):
        if not isinstance(identity, (tuple, list)) or len(identity) != 10:
            raise NativeServiceError("Bridge identity shape is invalid")
        kernel_exe = identity[-1]
        if not _safe_path(kernel_exe):
            raise NativeServiceError("Bridge identity executable is invalid")
        candidates = self.trusted_binaries()
        for interpreter in candidates:
            if self._probe_kernel_exe(interpreter) == kernel_exe:
                return self.bound(interpreter)
        raise NativeServiceError("Bridge identity does not map to a trusted interpreter")

    def validate_arguments(self, arguments, interpreter=None):
        interpreter = Path(interpreter or self.executable).resolve()
        if not isinstance(arguments, list) or len(arguments) != 10:
            raise NativeServiceError("Bridge arguments must contain exactly ten values")
        if not all(isinstance(a, str) for a in arguments):
            raise NativeServiceError("Bridge arguments must be strings")
        if any(any(c in a for c in "\0\n\r") for a in arguments):
            raise NativeServiceError("Bridge arguments contain unsupported characters")
        if Path(arguments[0]).resolve() != interpreter:
            raise NativeServiceError("Bridge interpreter does not match configuration")
        if Path(arguments[1]).resolve() != self.script:
            raise NativeServiceError("Bridge script does not match configuration")
        if arguments[2] != "--listen-host" or not _ipv4(arguments[3]):
            raise NativeServiceError("Bridge listen host must be an IPv4 address")
        if arguments[4] != "--listen-port":
            raise NativeServiceError("Bridge listen port flag is invalid")
        if not re.fullmatch(r"[1-9][0-9]{0,4}", arguments[5]) or not 1 <= int(arguments[5]) <= 65535:
            raise NativeServiceError("Bridge listen port is invalid")
        if arguments[6] != "--target-port":
            raise NativeServiceError("Bridge target port flag is invalid")
        if not re.fullmatch(r"[1-9][0-9]{0,4}", arguments[7]) or not 1 <= int(arguments[7]) <= 65535:
            raise NativeServiceError("Bridge target port is invalid")
        if arguments[8] != "--allow-peer" or not _ipv4(arguments[9]):
            raise NativeServiceError("Bridge allow-peer must be an IPv4 address")
        if arguments[3] == "127.0.0.1" and arguments[5] == arguments[7]:
            raise NativeServiceError("Bridge listen and target addresses must differ")
        return list(arguments)

    def check_job(self, job, *, token=None):
        super().check_job(job, token=token)
        arguments = job.get("arguments")
        if arguments is None:
            raise NativeServiceError("Bridge launchd job arguments are unavailable")
        self.validate_arguments(list(arguments), interpreter=self.executable)

    def journal_document(self):
        saved = super().journal_document()
        arguments = saved.get("arguments")
        if not isinstance(arguments, list) or len(arguments) != 10:
            raise NativeServiceError("Bridge journal arguments are invalid")
        self.validate_arguments(list(arguments), interpreter=saved.get("executable"))
        if saved.get("created") is not True:
            raise NativeServiceError("Bridge journal created flag is invalid")
        token = saved.get("token")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            raise NativeServiceError("Bridge journal token is invalid")
        domain = saved.get("domain")
        if domain not in {f"gui/{self.uid}", f"user/{self.uid}"}:
            raise NativeServiceError("Bridge journal domain is invalid")
        identity = saved.get("identity")
        if identity is not None:
            if not isinstance(identity, (list, tuple)) or len(identity) != 10:
                raise NativeServiceError("Bridge journal identity shape is invalid")
            if (any(type(value) is not int for value in identity[:9])
                    or not 0 < identity[0] <= 2147483647
                    or identity[1] <= 0 or not 0 <= identity[2] < 1000000
                    or tuple(identity[3:9]) != (self.uid, self.gid) * 3
                    or not _safe_path(identity[9])):
                raise NativeServiceError("Bridge journal native identity is invalid")
        return saved

    def prior_plist(self):
        encoded = super().prior_plist()
        if encoded is None:
            return None
        payload = plistlib.loads(base64.b64decode(encoded, validate=True))
        arguments = payload.get("ProgramArguments")
        if not isinstance(arguments, list) or len(arguments) != 10:
            raise NativeServiceError("Existing bridge plist arguments are invalid")
        self.validate_arguments(list(arguments), interpreter=arguments[0])
        return encoded

    def launch_plist(self, arguments, domain, token):
        self.validate_arguments([str(self.executable), *arguments])
        payload = super().launch_plist(arguments, domain, token)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        payload.update(KeepAlive=True, ThrottleInterval=5,
                       StandardOutPath=str(self._log_path), StandardErrorPath=str(self._log_path))
        return payload

    def processes(self):
        found = []
        jobs = self.jobs()
        if jobs:
            job = jobs[0]
            owner = self.owned_job(job)
            identity = owner.capture(job["domain"], token=job.get("token"))
            if identity is not None:
                found.append(identity)
        if self.journal.exists() or self.journal.is_symlink():
            saved = self.journal_document()
            identity = saved.get("identity")
            if identity is not None:
                identity = tuple(identity)
                if identity not in found:
                    if self.bound(saved["executable"]).live(identity):
                        found.append(identity)
        return found

    def _refresh_owned_birth(self):
        if not (self.journal.exists() or self.journal.is_symlink()):
            return
        saved = self.journal_document()
        jobs = self.jobs()
        if not jobs:
            return
        job = jobs[0]
        owner = self.owned_job(job)
        if job["domain"] != saved.get("domain"):
            raise NativeServiceError("Bridge job domain does not match journal")
        if job.get("token") != saved.get("token"):
            raise NativeServiceError("Bridge job token does not match journal")
        if Path(job["program"]).resolve() != Path(saved["executable"]).resolve():
            raise NativeServiceError("Bridge job executable does not match journal")
        if Path(job["working directory"]).resolve() != self.root:
            raise NativeServiceError("Bridge job working directory does not match journal")
        arguments = job.get("arguments")
        if arguments is None or list(arguments) != list(saved.get("arguments") or []):
            raise NativeServiceError("Bridge job arguments do not match journal")
        identity = owner.capture(job["domain"], token=job.get("token"))
        if identity is None:
            return
        previous = saved.get("identity")
        if previous is not None and tuple(previous) != identity:
            if self.bound(saved["executable"]).live(tuple(previous)):
                raise NativeServiceError("Bridge birth overlap detected")
        if previous is None or tuple(previous) != identity:
            if saved != self.journal_document():
                raise NativeServiceError("Bridge journal changed during refresh")
            owner.save({**saved, "identity": list(identity)})

    def _stop(self):
        self._refresh_owned_birth()
        return super()._stop()

    def _rollback(self, expected):
        if not expected.get("created"):
            return
        if not re.fullmatch(r"[0-9a-f]{32}", expected.get("token", "")):
            raise NativeServiceError("Invalid bridge rollback witness")
        if expected.get("domain") not in {f"gui/{self.uid}", f"user/{self.uid}"}:
            raise NativeServiceError("Invalid bridge rollback domain")
        saved = self.read_journal()
        if saved.get("token") != expected["token"] or saved.get("domain") != expected["domain"]:
            raise NativeServiceError("Bridge launch journal belongs to another operation")
        self._refresh_owned_birth()
        refreshed = self.read_journal()
        if refreshed.get("token") != expected["token"] or refreshed.get("domain") != expected["domain"]:
            raise NativeServiceError("Bridge launch journal changed during rollback")
        witness = dict(expected)
        if refreshed.get("identity") is not None:
            witness["identity"] = refreshed["identity"]
        super()._rollback(witness)

    def listener_owned(self, identity, host, port):
        # A live Python process can precede bind(). Connecting to the requested
        # port alone could accept a different listener while ours is starting.
        result = subprocess.run(
            ["/usr/sbin/lsof", "-nP", "-a", "-p", str(identity[0]),
             "-i4TCP", "-sTCP:LISTEN", "-Fpn"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 1 and not result.stdout:
            return False
        if result.returncode or len(result.stdout) > 65536:
            raise NativeServiceError("Could not inspect the bridge listener")
        lines = result.stdout.splitlines()
        if (not lines or lines[0] != "p" + str(identity[0])
                or any(not line.startswith("n") and not re.fullmatch(r"f[0-9]+", line)
                       for line in lines[1:])):
            raise NativeServiceError("Bridge listener ownership is ambiguous")
        name = "*" if host == "0.0.0.0" else host
        return f"n{name}:{port}" in lines[1:]

    def configure(self, arguments, *, timeout=15.0):
        full = self.validate_arguments([str(self.executable), *arguments])
        info = self.script.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid
                or info.st_mode & 0o022):
            raise NativeServiceError("Bridge script custody is invalid")
        self._probe_kernel_exe(self.executable)  # Validate before stopping a working bridge.
        with self.locked():
            self._stop()
            receipt = self._ensure(arguments)
            if not receipt.get("created"):
                raise NativeServiceError("Bridge launch did not create a new operation")
            identity = receipt["identity"]
            try:
                deadline = time.monotonic() + timeout
                while True:
                    before = self.capture(receipt["domain"], token=receipt["token"])
                    if before != identity:
                        raise NativeServiceError("Bridge changed before readiness probe")
                    ready = False
                    if self.listener_owned(identity, full[3], int(full[5])):
                        try:
                            with socket.create_connection((full[3], int(full[5])), timeout=1):
                                ready = True
                        except OSError:
                            pass
                    after = self.capture(receipt["domain"], token=receipt["token"])
                    if after != identity:
                        raise NativeServiceError("Bridge changed during readiness probe")
                    if ready:
                        break
                    if time.monotonic() >= deadline:
                        raise NativeServiceError("Bridge listener did not become ready")
                    time.sleep(.2)
            except Exception as original:
                try:
                    self._rollback(receipt)
                except Exception as cleanup:
                    raise NativeServiceUncertain(
                        "Bridge readiness failed and owned cleanup is incomplete: " + str(cleanup)
                    ) from original
                raise
            receipt["listener_ready"] = True
            return receipt


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 6 or argv[0] not in {"configure", "stop"}:
        print("Usage: macos_bridge_service.py configure|stop INSTALL INTERPRETER LABEL PLIST LOG_PATH [ARGS...]",
              file=sys.stderr)
        return 2
    action, install, interpreter, label, plist, log_path, *bridgeargs = argv
    try:
        service = BridgeService(install, interpreter, label=label, log_path=log_path)
        supplied = Path(plist).resolve()
        if supplied != service.plist:
            raise NativeServiceError("Supplied plist does not match the service plist")
        if action == "configure":
            if not bridgeargs:
                raise NativeServiceError("Bridge arguments are required")
            result = service.configure(bridgeargs)
        else:
            if bridgeargs:
                raise NativeServiceError("stop does not accept bridge arguments")
            result = service.stop()
        print(json.dumps(result))
    except (NativeServiceError, ProcessIdentityError, OSError, ValueError) as exc:
        print("Bridge service: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
