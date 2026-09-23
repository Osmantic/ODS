#!/usr/bin/python3
"""Root-only managed helper for the narrow Pixel release operator.

This helper is root-owned and callable only through a sudoers entry held by the dedicated
release transport identity. It can install/remove only the fixed, reviewed gateway and
courier unit templates, run a fixed allowlisted set of systemctl verbs against only the
configured units, run fixed permission probes as configured broker reader identities, and
report read-only status. It never accepts arbitrary bytes, paths, units, users, commands,
environment, shells, package installs, reboots, or Docker.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from pixel_release_grammar import (
    READERS,
    SHA256,
    SYSTEMCTL_UNIT_VERBS,
    UNITS,
    GrammarError,
    validate_operation,
)

TESTING = os.environ.get("PIXEL_RELEASE_MANAGED_TESTING") == "1" and getattr(os, "geteuid", lambda: 1)() != 0
CONFIG = Path(os.environ.get("PIXEL_RELEASE_MANAGED_CONFIG", "/etc/pixel-release-operator/config.json")) if TESTING else Path("/etc/pixel-release-operator/config.json")
MAX_CONFIG = 1024 * 1024
MAX_TEMPLATE = 1024 * 1024
MAX_OUTPUT = 128 * 1024
ACCESS_FLAG = {"read": "-r", "write": "-w", "execute": "-x"}
SERVICE_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]*\.service")
# The systemctl binary is always the fixed system path in production. Test configuration
# may only select an executable while TESTING (non-root) so it can never steer production.
SYSTEMCTL = os.environ.get("PIXEL_RELEASE_MANAGED_SYSTEMCTL", "/usr/bin/systemctl") if TESTING else "/usr/bin/systemctl"


class ManagedError(RuntimeError):
    pass


def stable_identity(info) -> tuple:
    """Return the full stable identity used to detect a file swapped/changed on open.

    Covers device, inode, mode/type, link count, size, and mtime/ctime nanoseconds so a
    path replaced between lstat and read, or a descriptor whose backing file changes while
    being read, is rejected.
    """
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def secure_read(path: Path, label: str, limit: int, *, allow_missing: bool = False):
    """Open and read a bounded regular file without following symlinks.

    Returns (payload, stat_info) or None when allow_missing and the path is absent. The
    full stable identity is captured before reading and re-verified against both the open
    descriptor and the path after reading, closing the changed-on-open race boundary.
    """
    try:
        raw = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ManagedError(f"{label} is missing")
    if stat.S_ISLNK(raw.st_mode):
        raise ManagedError(f"{label} must not be a symlink")
    if not stat.S_ISREG(raw.st_mode):
        raise ManagedError(f"{label} must be a regular file")
    if raw.st_size > limit:
        raise ManagedError(f"{label} exceeds its size limit")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > limit
            or stable_identity(opened) != stable_identity(raw)
        ):
            raise ManagedError(f"{label} changed during secure open")
        chunks, total = [], 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ManagedError(f"{label} exceeds its size limit")
        payload = b"".join(chunks)
        if len(payload) > limit:
            raise ManagedError(f"{label} changed during secure open")
        final_fd = os.fstat(descriptor)
        try:
            final_path = path.lstat()
        except FileNotFoundError as error:
            raise ManagedError(f"{label} changed during secure open") from error
        if stable_identity(final_fd) != stable_identity(opened) or stable_identity(final_path) != stable_identity(raw):
            raise ManagedError(f"{label} changed during secure open")
        return payload, opened
    finally:
        os.close(descriptor)


def release_file_metadata_ok(info) -> bool:
    """Return whether installed/reviewed file ownership/mode are safe.

    A safe file is never group/world writable and never executable. In production the
    reviewed layout is exactly root:root:0644; in non-root testing the ownership half
    cannot be asserted, so only the unsafe-mode half is exercised there.
    """
    if info.st_mode & 0o022 or info.st_mode & 0o111:
        return False
    if not TESTING and (info.st_uid != 0 or info.st_gid != 0 or (info.st_mode & 0o777) != 0o644):
        return False
    return True


def require_release_file_metadata(info, label: str) -> None:
    if not release_file_metadata_ok(info):
        raise ManagedError(f"{label} must be root:root:0644")


def require_root_owned(info, label: str) -> None:
    if TESTING:
        return
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ManagedError(f"{label} must be root-owned and not group/world writable")


def require_root_dir(path: Path, label: str) -> None:
    if TESTING:
        return
    try:
        raw = path.lstat()
    except FileNotFoundError as error:
        raise ManagedError(f"{label} is missing") from error
    if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
        raise ManagedError(f"{label} must be a real directory")
    require_root_owned(raw, label)


def load_config():
    payload, info = secure_read(CONFIG, "release-operator config", MAX_CONFIG)
    require_root_owned(info, "release-operator config")
    require_root_dir(CONFIG.parent, "release-operator config directory")
    try:
        value = json.loads(payload)
    except (ValueError, json.JSONDecodeError) as error:
        raise ManagedError("release-operator config is not valid JSON") from error
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ManagedError("release-operator config must use schemaVersion 1")
    validate_config(value)
    return value


def validate_unit(value, unit_id: str) -> None:
    if not isinstance(value, dict):
        raise ManagedError(f"unit {unit_id} is not configured")
    for key in ("name", "destination", "template", "templateSha256"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ManagedError(f"unit {unit_id} requires {key}")
    name = value["name"]
    if not SERVICE_NAME.fullmatch(name):
        raise ManagedError(f"unit {unit_id} name is not a safe .service name")
    destination = Path(value["destination"])
    if not destination.is_absolute() or destination == Path("/"):
        raise ManagedError(f"unit {unit_id} destination must be absolute and non-root")
    if destination.name != name:
        raise ManagedError(f"unit {unit_id} destination basename must match its service name")
    template = Path(value["template"])
    if not template.is_absolute() or template == Path("/"):
        raise ManagedError(f"unit {unit_id} template must be absolute and non-root")
    if not SHA256.fullmatch(value["templateSha256"]):
        raise ManagedError(f"unit {unit_id} templateSha256 is not a lowercase SHA-256")
    if not TESTING:
        expected_dest = Path("/etc/systemd/system") / name
        if destination != expected_dest:
            raise ManagedError(f"unit {unit_id} destination must be exactly /etc/systemd/system/{name}")
        if template.parent != Path("/etc/pixel-release-operator/templates"):
            raise ManagedError(f"unit {unit_id} template must live exactly beneath /etc/pixel-release-operator/templates")
    prior = value.get("priorTemplate")
    if prior is not None:
        if not isinstance(prior, str) or not prior or not SHA256.fullmatch(value.get("priorTemplateSha256", "")):
            raise ManagedError(f"unit {unit_id} prior template must carry a SHA-256")
        prior_path = Path(prior)
        if not prior_path.is_absolute() or prior_path == Path("/"):
            raise ManagedError(f"unit {unit_id} prior template must be absolute and non-root")
        if not TESTING and prior_path.parent != Path("/etc/pixel-release-operator/templates"):
            raise ManagedError(f"unit {unit_id} prior template must live exactly beneath /etc/pixel-release-operator/templates")
    else:
        if value.get("priorTemplateSha256") is not None:
            raise ManagedError(f"unit {unit_id} priorTemplateSha256 requires priorTemplate")


def validate_probe(value) -> None:
    if not isinstance(value, dict):
        raise ManagedError("permission probe is not an object")
    path = Path(str(value.get("path", "")))
    if not path.is_absolute() or path == Path("/"):
        raise ManagedError("permission probe path must be absolute and non-root")
    if value.get("access") not in ACCESS_FLAG:
        raise ManagedError("permission probe access must be read, write, or execute")
    if not isinstance(value.get("expect"), bool):
        raise ManagedError("permission probe expect must be boolean")


def validate_reader(value, reader_id: str) -> None:
    if not isinstance(value, dict):
        raise ManagedError(f"reader {reader_id} is not configured")
    user = str(value.get("user", ""))
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise ManagedError(f"reader {reader_id} user is unsafe")
    probes = value.get("probes")
    if not isinstance(probes, list) or not probes:
        raise ManagedError(f"reader {reader_id} requires a non-empty probes array")
    for probe in probes:
        validate_probe(probe)


def validate_config(value: dict) -> None:
    units = value.get("units")
    if not isinstance(units, dict):
        raise ManagedError("units must be an object")
    for unit_id in UNITS:
        if unit_id not in units:
            raise ManagedError(f"unit {unit_id} is not configured")
        validate_unit(units.get(unit_id), unit_id)
    readers = value.get("readers")
    if not isinstance(readers, dict):
        raise ManagedError("readers must be an object")
    # A reader may be absent for a disabled limb, but an explicitly configured reader must
    # validate. probe <id> still fails closed when its reader is unconfigured.
    for reader_id in READERS:
        reader = readers.get(reader_id)
        if reader is not None:
            validate_reader(reader, reader_id)
    receipt_root = Path(str(value.get("receiptRoot", "/var/lib/pixel-release-operator/receipts")))
    if not receipt_root.is_absolute() or receipt_root == Path("/"):
        raise ManagedError("receiptRoot must be an absolute non-root path")


def unit_config(value, unit_id: str) -> dict:
    if unit_id not in UNITS:
        raise ManagedError("unknown unit")
    item = value.get("units", {}).get(unit_id)
    if not isinstance(item, dict):
        raise ManagedError("unit is not configured")
    return item


def template_for_digest(item: dict, requested: str):
    if requested == item["templateSha256"]:
        return Path(item["template"]), "template"
    prior = item.get("priorTemplate")
    if prior is not None and requested == item.get("priorTemplateSha256"):
        return Path(prior), "priorTemplate"
    raise ManagedError("install digest does not match any reviewed unit template")


def template_candidates(item: dict):
    candidates = [(Path(item["template"]), item["templateSha256"])]
    if item.get("priorTemplate") is not None:
        candidates.append((Path(item["priorTemplate"]), item.get("priorTemplateSha256")))
    return candidates


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class BoundedRun:
    def __init__(self, argv, timeout=120):
        self.argv = list(argv)
        self.timeout = min(max(int(timeout), 1), 3600)

    def run(self, check=True):
        deadline = time.monotonic() + self.timeout
        process = subprocess.Popen(
            self.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
            env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "HOME": "/"},
        )
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        truncated = {"stdout": False, "stderr": False}

        def drain(name, stream):
            while True:
                try:
                    chunk = stream.read(8192)
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                remaining = MAX_OUTPUT - len(captured[name])
                if remaining > 0:
                    captured[name].extend(chunk[:remaining])
                if len(chunk) > max(remaining, 0):
                    truncated[name] = True

        assert process.stdout is not None and process.stderr is not None
        readers = [threading.Thread(target=drain, args=(name, stream), daemon=True) for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
        for reader in readers:
            reader.start()
        timed_out = False
        while process.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                break
            time.sleep(0.05)
        if timed_out:
            for stream in (process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
            for reader in readers:
                reader.join(timeout=2)
            raise ManagedError(f"command {Path(self.argv[0]).name} exceeded its hard timeout")
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        for reader in readers:
            reader.join(timeout=2)
        result = {
            "argv0": Path(self.argv[0]).name,
            "exitCode": process.returncode,
            "stdout": captured["stdout"].decode("utf-8", "replace"),
            "stderr": captured["stderr"].decode("utf-8", "replace"),
            "outputTruncated": truncated["stdout"] or truncated["stderr"],
        }
        if check and process.returncode != 0:
            raise ManagedError(f"command {Path(self.argv[0]).name} exited with {process.returncode}")
        return result


def write_all(descriptor, payload):
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ManagedError("unit write made no progress")
        view = view[written:]


def checked_parent(destination: Path) -> Path:
    parent = destination.parent
    if not parent.is_absolute() or parent == Path("/"):
        raise ManagedError("unit destination parent must be absolute and non-root")
    try:
        raw = parent.lstat()
    except FileNotFoundError as error:
        raise ManagedError("unit destination parent is missing") from error
    if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
        raise ManagedError("unit destination parent must be a real directory")
    require_root_owned(raw, "unit destination parent")
    return parent


def read_regular(path: Path, label: str, limit: int):
    """Strict reader shared by mutations and status: real regular file, no links/hardlinks."""
    try:
        raw = path.lstat()
    except FileNotFoundError as error:
        raise ManagedError(f"{label} is missing") from error
    if stat.S_ISLNK(raw.st_mode):
        raise ManagedError(f"{label} must not be a symlink")
    if not stat.S_ISREG(raw.st_mode):
        raise ManagedError(f"{label} must be a regular file")
    if raw.st_nlink > 1:
        raise ManagedError(f"{label} must not be a hardlink")
    return secure_read(path, label, limit)


def read_destination(destination: Path, *, allow_missing: bool):
    try:
        destination.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ManagedError("unit destination is missing")
    return read_regular(destination, "unit destination", MAX_TEMPLATE)


def read_template(template: Path):
    return read_regular(template, "reviewed unit template", MAX_TEMPLATE)


def install_unit(value: dict, requested: str) -> dict:
    template_path, kind = template_for_digest(value, requested)
    payload, info = read_template(template_path)
    require_release_file_metadata(info, "reviewed unit template")
    if digest(payload) != requested:
        raise ManagedError("reviewed unit template digest does not match the request")
    destination = Path(value["destination"])
    checked_parent(destination)
    existing = read_destination(destination, allow_missing=True)
    if existing is not None:
        existing_payload, existing_info = existing
        if digest(existing_payload) == requested:
            if release_file_metadata_ok(existing_info):
                return {"operation": "unit.install", "unit": value["name"], "sha256": requested, "source": kind, "idempotent": True}
            # Bytes already match but production ownership/mode is not root:root:0644;
            # fall through to repair the destination with the fixed metadata.
        else:
            known = {candidate_digest for _, candidate_digest in template_candidates(value)}
            if digest(existing_payload) not in known:
                raise ManagedError("unit destination already exists with unexpected bytes")
    parent = checked_parent(destination)
    descriptor, temporary = tempfile.mkstemp(prefix=".pixel-op-", dir=str(parent))
    try:
        try:
            os.fchmod(descriptor, 0o644)
            if not TESTING:
                os.fchown(descriptor, 0, 0)
            write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    fsync_dir(parent)
    return {"operation": "unit.install", "unit": value["name"], "sha256": requested, "source": kind, "idempotent": False}


def remove_unit(value: dict) -> dict:
    destination = Path(value["destination"])
    checked_parent(destination)
    existing = read_destination(destination, allow_missing=True)
    if existing is None:
        return {"operation": "unit.remove", "unit": value["name"], "idempotent": True}
    payload, _ = existing
    current = digest(payload)
    known = {candidate_digest for _, candidate_digest in template_candidates(value)}
    if current not in known:
        raise ManagedError("unit destination does not match any reviewed template")
    parent = checked_parent(destination)
    destination.unlink()
    fsync_dir(parent)
    return {"operation": "unit.remove", "unit": value["name"], "sha256": current, "idempotent": False}


def systemctl_action(value: dict, operation: list) -> dict:
    verb = operation[0]
    if verb == "daemon-reload":
        unit_id = None
        argv = [SYSTEMCTL, "daemon-reload"]
    else:
        unit_id = operation[1]
        unit_name = value["units"][unit_id]["name"]
        argv = [SYSTEMCTL, verb]
        # operation is [verb, unit, (--now)]; preserve --now exactly for enable/disable.
        if len(operation) == 3:
            argv.append(operation[2])
        argv.append(unit_name)
    result = BoundedRun(argv).run(check=False)
    return {"operation": f"systemctl.{verb}", "unit": unit_id, **result}


def probe_action(value: dict, reader_id: str) -> dict:
    reader = value.get("readers", {}).get(reader_id)
    if not isinstance(reader, dict):
        raise ManagedError("reader is not configured")
    user = str(reader["user"])
    results = []
    failed = []
    for probe in reader["probes"]:
        path = str(probe["path"])
        flag = ACCESS_FLAG[probe["access"]]
        expect = bool(probe["expect"])
        if TESTING:
            argv = ["/usr/bin/test", flag, path]
        else:
            argv = ["/usr/bin/sudo", "-u", user, "--", "/usr/bin/test", flag, path]
        result = BoundedRun(argv).run(check=False)
        observed = result["exitCode"] == 0
        results.append({"path": path, "access": probe["access"], "expect": expect, "observed": observed, "exitCode": result["exitCode"]})
        if observed != expect:
            failed.append({"path": path, "access": probe["access"], "expect": expect, "observed": observed})
    if failed:
        raise ManagedError(json.dumps({"operation": "probe", "reader": reader_id, "failed": failed}, sort_keys=True))
    return {"operation": "probe", "reader": reader_id, "user": user, "probes": results}


def status_action(value: dict) -> dict:
    units = {}
    for unit_id in UNITS:
        item = unit_config(value, unit_id)
        destination = Path(item["destination"])
        entry = {"name": item["name"], "destination": str(destination)}
        existing = read_destination(destination, allow_missing=True)
        entry["installedSha256"] = None if existing is None else digest(existing[0])
        if existing is not None:
            require_release_file_metadata(existing[1], f"installed unit {item['name']}")
        templates = {}
        for path, expected in template_candidates(item):
            try:
                payload, info = read_template(path)
            except FileNotFoundError:
                templates[str(path)] = {"present": False, "sha256": None, "expected": expected}
            else:
                observed = digest(payload)
                if observed != expected:
                    raise ManagedError("reviewed unit template is tampered")
                require_release_file_metadata(info, f"reviewed unit template {path}")
                templates[str(path)] = {"present": True, "sha256": observed, "expected": expected}
        entry["templates"] = templates
        units[unit_id] = entry
    return {"operation": "status", "units": units}


def _receipt_root(value: dict) -> Path:
    receipt_root = Path(str(value.get("receiptRoot", "/var/lib/pixel-release-operator/receipts")))
    receipt_root.mkdir(parents=True, exist_ok=True)
    raw = receipt_root.lstat()
    if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
        raise ManagedError("receipt root must be a real directory")
    require_root_owned(raw, "receipt root")
    require_root_dir(receipt_root.parent, "receipt root parent")
    return receipt_root


def receipt(value: dict, label: str, result=None, exit_code: int = 0, error=None):
    """Write a canonical, content-free JSON receipt and return (name, sha256).

    The receipt records schema/timestamp/operation/result identity and digests of evidence
    or errors only. It never contains paths, stdout/stderr, user content, or credentials.
    It is created with O_EXCL so an existing name is never replaced, fsync'd along with its
    directory, and root-owned mode 0600 in production.
    """
    receipt_root = _receipt_root(value)
    nonce = hashlib.sha256(os.urandom(32)).hexdigest()[:12]
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"{timestamp}-{label}.{nonce}"
    data = {
        "schemaVersion": 1,
        "timestamp": timestamp,
        "operation": label,
        "exitCode": exit_code,
    }
    if result is not None:
        data["resultIdentity"] = result.get("operation", label)
        evidence = json.dumps(result, sort_keys=True, separators=(",", ":"))
        data["evidenceSha256"] = digest(evidence.encode())
    if error is not None:
        data["errorSha256"] = digest(str(error).encode())
    payload = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path = receipt_root / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not TESTING:
            os.fchown(descriptor, 0, 0)
        write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_dir(receipt_root)
    return name, digest(payload)


def operation_label(operation: list) -> str:
    if operation == ["status"]:
        return "status"
    if operation[0] == "unit" and operation[1] == "install":
        return "unit-install"
    if operation[0] == "unit" and operation[1] == "remove":
        return "unit-remove"
    if operation[0] == "systemctl":
        return "systemctl"
    if operation[0] == "probe":
        return "probe"
    raise ManagedError("unsupported operation")


def validate_staging_config(config_path: str, templates_dir: str) -> int:
    """Root-only staging validation used during provisioning.

    Deliberately not part of the transport grammar or sudoers surface, so the transport can
    never pass an arbitrary config path. Validates the staged config schema and cross-checks
    the actual staged template bytes against the configured SHA-256 values.
    """
    if getattr(os, "geteuid", lambda: 1)() != 0:
        raise ManagedError("staging config validation must run as root")
    path = Path(config_path)
    if not path.is_absolute() or path == Path("/"):
        raise ManagedError("staging config path must be absolute and non-root")
    payload, info = secure_read(path, "staging release-operator config", MAX_CONFIG)
    if info.st_nlink > 1:
        raise ManagedError("staging release-operator config must not be a hardlink")
    try:
        value = json.loads(payload)
    except (ValueError, json.JSONDecodeError) as error:
        raise ManagedError("staging release-operator config is not valid JSON") from error
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ManagedError("staging release-operator config must use schemaVersion 1")
    validate_config(value)
    templates = Path(templates_dir)
    raw = templates.lstat()
    if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
        raise ManagedError("staging templates must be a real directory")
    for unit_id in UNITS:
        item = value["units"][unit_id]
        for key, digest_key in (("template", "templateSha256"), ("priorTemplate", "priorTemplateSha256")):
            configured = item.get(key)
            if configured is None:
                continue
            staged = templates / Path(str(configured)).name
            try:
                staged_payload, staged_info = secure_read(staged, f"staging template {staged.name}", MAX_TEMPLATE)
            except FileNotFoundError:
                raise ManagedError(f"staging is missing template {staged.name} referenced by config") from None
            if staged_info.st_nlink > 1:
                raise ManagedError(f"staging template {staged.name} must not be a hardlink")
            if digest(staged_payload) != item[digest_key]:
                raise ManagedError(f"staging template {staged.name} does not match its configured SHA-256")
    print(json.dumps({"schemaVersion": 1, "stagingValid": True}, sort_keys=True))
    return 0


def main() -> int:
    if getattr(os, "geteuid", lambda: 1)() != 0 and not TESTING:
        raise ManagedError("managed actions must run as root")
    if sys.argv[1:] == ["--validate-config"]:
        load_config()
        print(json.dumps({"schemaVersion": 1, "valid": True}, sort_keys=True))
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "--validate-staging-config":
        return validate_staging_config(sys.argv[2], sys.argv[3])
    operation = validate_operation(sys.argv[1:])
    # The thin bundle/service/reboot/broker-bytes operator is a separate fixed surface bound to its own
    # root-owned config and the repository lifecycle CLIs. It serializes under the same
    # single-operation flock as the gateway/courier helper so concurrent operations can
    # never interleave.
    if operation[0] in ("bundle", "service", "reboot", "broker-bytes"):
        import pixel_operator as pop
        pixel_value = pop.load_config()
        pixel_receipt_root = Path(str(pixel_value["receiptRoot"]))
        lock_path = pixel_receipt_root.parent / "operator.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        require_root_dir(lock_path.parent, "operator lock directory")
        lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = pop.run_operation(operation)
        finally:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            finally:
                os.close(lock)
        print(json.dumps({"schemaVersion": 1, **result}, sort_keys=True))
        return 0
    value = load_config()
    label = operation_label(operation)
    receipt_root = Path(str(value.get("receiptRoot", "/var/lib/pixel-release-operator/receipts")))
    lock_path = receipt_root.parent / "operator.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    require_root_dir(lock_path.parent, "operator lock directory")
    lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    result = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if operation == ["status"]:
            result = status_action(value)
        elif operation[0] == "unit" and operation[1] == "install":
            result = install_unit(unit_config(value, operation[2]), operation[3])
        elif operation[0] == "unit" and operation[1] == "remove":
            result = remove_unit(unit_config(value, operation[2]))
        elif operation[0] == "systemctl":
            result = systemctl_action(value, operation[1:])
            if result.get("exitCode") != 0:
                raise ManagedError(f"systemctl {operation[1]} failed with exit code {result.get('exitCode')}")
        elif operation[0] == "probe":
            result = probe_action(value, operation[1])
        else:
            raise ManagedError("unsupported operation")
    except (ManagedError, GrammarError, OSError, ValueError, json.JSONDecodeError) as error:
        try:
            failure_name, failure_sha = receipt(value, label, exit_code=1, error=error)
            print(f"failure receipt: {failure_name} {failure_sha}", file=sys.stderr)
        except Exception:
            pass
        raise
    else:
        receipt_name, receipt_sha = receipt(value, label, result, exit_code=0)
        print(json.dumps({"schemaVersion": 1, **result, "receipt": receipt_name, "receiptSha256": receipt_sha}, sort_keys=True))
        return 0
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            os.close(lock)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ManagedError, GrammarError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"pixel-release-managed: {error}", file=sys.stderr)
        raise SystemExit(1)
