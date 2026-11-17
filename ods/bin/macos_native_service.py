"""Ownership-bound native launchd startup and rollback.

The launch marker distinguishes operations; it is not a credential. Native
process identity and the loaded job's deployment paths are checked separately.
Callers must retain their model-routing transaction until health/contract proof.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import tempfile
import time
import uuid

from pixel_macos_process import ProcessIdentityError, process_identity


class NativeServiceError(RuntimeError):
    pass


class NativeServiceUncertain(NativeServiceError):
    native_cleanup_uncertain = True


class NativeService:
    """Serialize one installation's producer mutations; never adopt a PID file."""

    def __init__(self, install, executable, pid_file, *, home=None,
                 label="com.ods.llama-server"):
        self.root = Path(install).resolve()
        self.executable = Path(executable).resolve()
        self.pid_file = Path(pid_file).resolve()
        self.home = Path(home).resolve() if home is not None else Path.home().resolve()
        self.label = label
        self.uid, self.gid = os.getuid(), os.getgid()
        if self.uid == 0 or not re.fullmatch(r"[A-Za-z0-9.-]+", label):
            raise NativeServiceError("Native service requires an owner and a valid label")
        if not self.pid_file.is_relative_to(self.root):
            raise NativeServiceError("Native PID file must belong to the installation")
        if any(c in str(path) for path in (self.root, self.executable, self.pid_file, self.home)
               for c in "\0\n\r"):
            raise NativeServiceError("Native deployment paths contain unsupported characters")
        self.plist = self.home / "Library/LaunchAgents" / (label + ".plist")
        self.journal = self.pid_file.with_suffix(".launch.json")

    @contextmanager
    def locked(self):
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        path = self.pid_file.with_suffix(".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid:
                raise NativeServiceError("Native service lock ownership is invalid")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise NativeServiceError("Native service operation is already active") from exc
            yield
        finally:
            os.close(fd)

    def run(self, *args):
        return subprocess.run(["launchctl", *args], capture_output=True, text=True,
                              timeout=10, check=False, env={**os.environ, "LC_ALL": "C"})

    def domain_available(self, domain):
        probe = self.run("print", domain)
        if probe.returncode == 0:
            return True
        messages = {f"Could not find domain for {domain}"}
        if domain.startswith("gui/"):
            messages.add(f"Could not find domain for user gui: {self.uid}")
        if (probe.returncode in {1, 3, 5, 112}
                and messages.intersection(probe.stderr.splitlines())):
            return False
        raise NativeServiceError("Could not inspect native launchd domain")

    def domains(self):
        result = [domain for kind in ("gui", "user")
                  if self.domain_available(domain := f"{kind}/{self.uid}")]
        if not result:
            raise NativeServiceError("No native launchd domain is available")
        return result

    def job(self, domain):
        result = self.run("print", f"{domain}/{self.label}")
        if result.returncode == 113:
            return None
        if result.returncode:
            if not self.domain_available(domain):
                return None
            raise NativeServiceError("Could not inspect native launchd job")
        fields = {}
        for line in result.stdout.splitlines():
            match = re.fullmatch(r"\t(path|program|working directory|pid|state|runs|last exit code) = (.+)", line)
            if match:
                key, value = match.groups()
                if key in fields:
                    raise NativeServiceError("Ambiguous native launchd job")
                fields[key] = value
            marker = re.fullmatch(r"\t\tODS_NATIVE_LAUNCH_TOKEN => ([0-9a-f]{32})", line)
            if marker:
                if "token" in fields:
                    raise NativeServiceError("Ambiguous native launch marker")
                fields["token"] = marker.group(1)
        if not all(key in fields for key in ("path", "program", "working directory", "state")):
            raise NativeServiceError("Native launchd job format is unavailable")
        fields["domain"] = domain
        return fields

    def check_job(self, job, *, token=None):
        if (Path(job["path"]).resolve() != self.plist
                or Path(job["program"]).resolve() != self.executable
                or Path(job["working directory"]).resolve() != self.root
                or (token is not None and job.get("token") != token)):
            raise NativeServiceError("Native launchd job belongs to a different deployment or operation")

    def identity(self, pid):
        return process_identity(pid, uid=self.uid, gid=self.gid,
                                executable=str(self.executable))

    def live(self, identity):
        try:
            current = self.identity(identity[0])
        except ProcessIdentityError as exc:
            if exc.errno == errno.ESRCH or str(exc) in {
                "gateway-process-mismatch", "gateway-process-executable-mismatch",
                "gateway-process-owner-mismatch", "gateway-process-changed",
            }:
                return False
            raise NativeServiceError("Could not verify native process liveness") from exc
        return current == tuple(identity)

    def processes(self):
        result = subprocess.run(["ps", "-axo", "pid=,uid="], capture_output=True,
                                text=True, timeout=10, check=False)
        lines = result.stdout.splitlines()
        if result.returncode or not lines or len(lines) > 262144:
            raise NativeServiceError("Could not enumerate native processes")
        found = []
        for line in lines:
            if not re.fullmatch(r"\s*[0-9]+\s+-?[0-9]+\s*", line):
                raise NativeServiceError("Invalid native process enumeration")
            pid, uid = map(int, line.split())
            if uid != self.uid:
                continue
            binaries = dict.fromkeys((self.executable, (self.root / "bin/llama-server").resolve()))
            for binary in binaries:
                try:
                    identity = (self.identity(pid) if binary == self.executable else
                                process_identity(pid, uid=self.uid, gid=self.gid, executable=str(binary)))
                    found.append(identity)
                    break
                except ProcessIdentityError as exc:
                    if exc.errno == errno.ESRCH:
                        break
                    if str(exc) == "gateway-process-executable-mismatch":
                        continue
                    raise NativeServiceError("Could not prove native process ownership") from exc
        return found

    @staticmethod
    def atomic(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".native-stage-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def save(self, receipt):
        document = {**receipt, "install": str(self.root),
                    "executable": str(self.executable), "label": self.label}
        self.atomic(self.journal, json.dumps(document).encode())

    def read_journal(self):
        info = self.journal.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid
                or info.st_mode & 0o077 or info.st_size > 1048576):
            raise NativeServiceError("Native launch journal custody is invalid")
        saved = json.loads(self.journal.read_text())
        if (saved.get("install") != str(self.root)
                or saved.get("executable") != str(self.executable)
                or saved.get("label") != self.label):
            raise NativeServiceError("Native launch journal belongs to a different deployment")
        return saved

    def prior_plist(self):
        if not self.plist.exists():
            return None
        info = self.plist.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_mode & 0o022:
            raise NativeServiceError("Native service plist custody is invalid")
        raw = self.plist.read_bytes()
        payload = plistlib.loads(raw)
        args = payload.get("ProgramArguments", [])
        if (payload.get("Label") != self.label or not args
                or Path(args[0]).resolve() != self.executable
                or Path(payload.get("WorkingDirectory", "")).resolve() != self.root):
            raise NativeServiceError("Existing native plist belongs to a different deployment")
        return base64.b64encode(raw).decode()

    def capture(self, domain, *, token=None, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            job = self.job(domain)
            if job is not None:
                self.check_job(job, token=token)
                if ("pid" not in job and job["state"] in {"not running", "exited"}
                        and job.get("runs", "0").isdigit() and int(job.get("runs", "0")) > 0
                        and job.get("last exit code") not in {None, "(never exited)"}):
                    return None
                if "pid" in job and job["pid"].isdigit():
                    pid = int(job["pid"])
                    try:
                        identity = self.identity(pid)
                        again = self.job(domain)
                        if again is None:
                            raise NativeServiceError("Native job disappeared during startup")
                        self.check_job(again, token=token)
                        if (again.get("pid") == str(pid) and again["state"] == "running"
                                and self.identity(pid) == identity):
                            return identity
                    except ProcessIdentityError as exc:
                        # A job may publish its PID before exec completes. Retry
                        # inspection only; never interpret permission errors as absence.
                        if exc.errno not in {errno.ESRCH, errno.EPERM}:
                            raise NativeServiceError("Native startup identity did not match") from exc
            if time.monotonic() >= deadline:
                raise NativeServiceError("Native job did not produce a verified process")
            time.sleep(.1)

    def remove_job(self, domain, identity, *, token=None):
        job = self.job(domain)
        if job is not None:
            self.check_job(job, token=token)
            if identity is not None and job.get("pid") not in {None, str(identity[0])}:
                raise NativeServiceError("Native job process changed before shutdown")
            if identity is not None and self.live(identity) is False and job.get("pid"):
                # A different birth under the same numeric PID must not be booted out.
                try:
                    if self.identity(int(job["pid"])) != tuple(identity):
                        raise NativeServiceError("Native process was replaced before shutdown")
                except ProcessIdentityError as exc:
                    if exc.errno != errno.ESRCH and str(exc) != "gateway-process-mismatch":
                        raise NativeServiceError("Native shutdown identity is uncertain") from exc
            if identity is None and job.get("pid"):
                raise NativeServiceError("Native producer appeared before shutdown")
            result = self.run("bootout", f"{domain}/{self.label}")
            if result.returncode and self.job(domain) is not None:
                raise NativeServiceError("Native launchd shutdown failed")
            # A concurrent removal may make bootout fail. Even after a fresh
            # absent-job proof, the native process must still be confirmed gone.
        deadline = time.monotonic() + 30
        while True:
            after = self.job(domain)
            if after is not None:
                self.check_job(after, token=token)
            if after is None and (identity is None or not self.live(identity)):
                return
            if time.monotonic() >= deadline:
                raise NativeServiceError("Native shutdown is not confirmed")
            time.sleep(.1)

    def ensure(self, arguments):
        """Preserve a live producer or create a launch with a rollback witness."""
        with self.locked():
            domains = self.domains()
            jobs = [job for domain in domains if (job := self.job(domain)) is not None]
            if len(jobs) > 1:
                raise NativeServiceError("Multiple native jobs require recovery")
            if jobs:
                job = jobs[0]
                self.check_job(job)
                identity = self.capture(job["domain"])
                if identity is not None:
                    return {"created": False, "identity": identity}
                if self.processes():
                    raise NativeServiceError("Native job exited but an owned producer remains")
                if self.journal.exists():
                    self._rollback(self.read_journal())
                else:
                    self.prior_plist()  # Dead legacy jobs still require deployment custody.
                    self.remove_job(job["domain"], None, token=job.get("token"))
            processes = self.processes()
            if processes:
                if len(processes) != 1:
                    raise NativeServiceError("Multiple native producers require recovery")
                return {"created": False, "identity": processes[0]}
            if self.journal.exists():
                # Never discard recovery provenance from an interrupted launch.
                self._rollback(self.read_journal())
            domain = domains[0]
            token = uuid.uuid4().hex
            log = self.home / "Library/Logs/ODS/llama-server.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "Label": self.label, "ProgramArguments": [str(self.executable), *arguments],
                "WorkingDirectory": str(self.root), "RunAtLoad": True,
                "LimitLoadToSessionType": "Aqua" if domain.startswith("gui/") else "Background",
                "EnvironmentVariables": {"ODS_NATIVE_LAUNCH_TOKEN": token},
                "StandardOutPath": str(log), "StandardErrorPath": str(log),
            }
            raw = plistlib.dumps(payload)
            receipt = {"created": True, "token": token, "domain": domain, "identity": None,
                       "plist_sha256": hashlib.sha256(raw).hexdigest(),
                       "prior_plist": self.prior_plist()}
            self.save(receipt)  # Recovery provenance precedes the first launchd mutation.
            try:
                self.atomic(self.plist, raw)
                boot = self.run("bootstrap", domain, str(self.plist))
                if boot.returncode:
                    raise NativeServiceError("Native launchd bootstrap failed")
                identity = self.capture(domain, token=token)
                if identity is None:
                    raise NativeServiceError("Native producer exited during startup")
                receipt["identity"] = identity
                self.save(receipt)
                self.atomic(self.pid_file, str(identity[0]).encode())
                return {key: value for key, value in receipt.items() if key != "prior_plist"}
            except Exception as original:
                try:
                    self._rollback(receipt)
                except Exception as cleanup:
                    raise NativeServiceUncertain(
                        "Native startup failed and owned cleanup is incomplete: " + str(cleanup)
                    ) from original
                raise

    def rollback(self, expected):
        with self.locked():
            self._rollback(expected)

    def _rollback(self, expected):
        if not expected.get("created"):
            return
        if not re.fullmatch(r"[0-9a-f]{32}", expected.get("token", "")):
            raise NativeServiceError("Invalid native rollback witness")
        if expected.get("domain") not in {f"gui/{self.uid}", f"user/{self.uid}"}:
            raise NativeServiceError("Invalid native rollback domain")
        saved = self.read_journal()
        if saved.get("token") != expected["token"] or saved.get("domain") != expected["domain"]:
            raise NativeServiceError("Native launch journal belongs to another operation")
        if (saved.get("identity") and expected.get("identity")
                and tuple(saved["identity"]) != tuple(expected["identity"])):
            raise NativeServiceError("Native rollback process witness changed")
        identity = saved.get("identity") or expected.get("identity")
        # A launch can fail after bootstrap but before the first identity capture.
        if identity is None:
            job = self.job(saved["domain"])
            if job is not None:
                self.check_job(job, token=saved["token"])
                identity = self.capture(saved["domain"], token=saved["token"])
        self.remove_job(saved["domain"], identity, token=saved["token"])
        if self.plist.exists():
            if hashlib.sha256(self.plist.read_bytes()).hexdigest() != saved["plist_sha256"]:
                raise NativeServiceError("Native plist changed before rollback")
            if saved["prior_plist"] is None:
                self.plist.unlink()
            else:
                self.atomic(self.plist, base64.b64decode(saved["prior_plist"], validate=True))
        elif saved["prior_plist"] is not None:
            raise NativeServiceError("Native plist was removed before rollback")
        if self.pid_file.exists():
            if identity is not None and self.pid_file.read_text().strip() == str(identity[0]):
                self.pid_file.unlink()
        self.journal.unlink()
