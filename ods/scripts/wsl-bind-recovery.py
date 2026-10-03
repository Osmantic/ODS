#!/usr/bin/env python3
"""Detect and repair stale Docker Desktop bind views for ODS services.

Runs only under real WSL + Docker Desktop. Compares fresh host bind metadata
against the ORIGINAL RUNNING container's bind view via `docker exec stat`.
Recreates only services whose bind view is provably stale, using the exact
compose flags the caller already resolved. Never touches volumes, auth, or
unrelated projects. Never prints secrets.
"""
import argparse
import importlib.util
import json
import os
import re
import selectors
import shutil
import stat as statmod
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

PROBE_TIMEOUT = 30
COMPOSE_TIMEOUT = 60
BACKUP_ROOT_REL = "data/wsl-bind-recovery"
BACKUP_MAX_BYTES = 1024 * 1024 * 1024
BACKUP_MAX_ENTRIES = 10000
RECOVERY_TIMEOUT = 120
_deadline = None
_install_dir = None


def log(msg):
    print(f"[wsl-bind-recovery] {msg}", file=sys.stderr)


def fail(msg):
    print(f"[wsl-bind-recovery] ERROR: {msg}", file=sys.stderr)
    return 1


def run(argv, timeout, check=True, capture=True, binary=False):
    if argv[:2] == ["docker", "compose"]:
        # A recovery may run long after CLI resolution. Recheck the current
        # recipe bytes before rendering or recreating any part of the stack.
        policy_path = Path(__file__).with_name("compose-cache-policy.py")
        try:
            spec = importlib.util.spec_from_file_location("recovery_compose_policy", policy_path)
            policy = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(policy)
            policy.validate_flags(_install_dir or Path.cwd(), argv[2:])
        except (OSError, ValueError, ImportError) as exc:
            raise RuntimeError(f"saved Compose policy rejected recovery: {exc}") from exc
    if _deadline is not None:
        remaining = _deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("total recovery time budget exceeded")
        timeout = min(timeout, remaining)
    return subprocess.run(
        argv, timeout=timeout, check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=not binary,
    )


def is_wsl_docker_desktop():
    try:
        rel = Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        return False
    if "microsoft" not in rel:
        return False
    try:
        info = run(["docker", "info", "--format", "{{.OperatingSystem}}"], 15)
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    os_name = (info.stdout or "").strip().lower()
    return "docker desktop" in os_name or "dockerdesktop" in os_name


def compose_config_json(flags):
    argv = ["docker", "compose", *flags, "config", "--format", "json"]
    try:
        out = run(argv, 30)
    except subprocess.SubprocessError as exc:
        raise RuntimeError(f"compose config failed: {exc}")
    try:
        config = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"compose config returned invalid JSON: {exc}")
    if not isinstance(config, dict) or not isinstance(config.get("services"), dict):
        raise RuntimeError("compose config returned an invalid manifest")
    return config


def compose_ps(flags):
    argv = ["docker", "compose", *flags, "ps", "-a", "--format", "json"]
    try:
        out = run(argv, 30)
    except subprocess.SubprocessError as exc:
        raise RuntimeError(f"compose ps failed: {exc}")
    text = (out.stdout or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            if any(not isinstance(row, dict) for row in parsed):
                raise RuntimeError("compose ps returned an invalid row")
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            raise RuntimeError("compose ps returned invalid JSON")
        if not isinstance(rows[-1], dict):
            raise RuntimeError("compose ps returned an invalid row")
    return rows


def inspect_container(name):
    try:
        out = run(["docker", "inspect", name], 15)
    except subprocess.SubprocessError:
        return None
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None


def active_profiles(flags):
    profiles = set()
    wildcard = False
    env = os.environ.get("COMPOSE_PROFILES", "")
    for p in env.split(","):
        p = p.strip()
        if not p:
            continue
        if p == "*":
            wildcard = True
        else:
            profiles.add(p)
    i = 0
    while i < len(flags):
        f = flags[i]
        if f == "--profile" and i + 1 < len(flags):
            val = flags[i + 1]
            if val == "*":
                wildcard = True
            else:
                profiles.add(val)
            i += 2
            continue
        if f.startswith("--profile="):
            val = f.split("=", 1)[1]
            if val == "*":
                wildcard = True
            else:
                profiles.add(val)
        i += 1
    return profiles, wildcard


def service_enabled(svc_cfg, profiles, wildcard=False):
    svc_profiles = svc_cfg.get("profiles") or []
    if not svc_profiles:
        return True
    if wildcard:
        return True
    return any(p in profiles for p in svc_profiles)


def resolve_bind_targets(config, service, profiles, wildcard=False):
    services = config.get("services") or {}
    svc = services.get(service)
    if not svc:
        raise RuntimeError(f"service {service} not in compose config")
    if profiles is not None and not service_enabled(svc, profiles, wildcard):
        return []
    binds = []
    for vol in svc.get("volumes") or []:
        if not isinstance(vol, dict):
            continue
        vtype = vol.get("type")
        if vtype != "bind":
            continue
        src = vol.get("source")
        dst = vol.get("target")
        if not src or not dst:
            continue
        binds.append((src, dst, bool(vol.get("read_only"))))
    return binds


def host_stat(source):
    try:
        s = os.stat(source)
    except OSError as exc:
        return {"error": exc.errno}
    if statmod.S_ISDIR(s.st_mode):
        filetype = "dir"
    elif statmod.S_ISREG(s.st_mode):
        filetype = "file"
    else:
        filetype = "other"
    return {
        "device": s.st_dev,
        "inode": s.st_ino,
        "filetype": filetype,
    }


def exec_stat(container_name, target):
    """Return {device,inode,filetype} or {error} for target inside container."""
    argv = [
        "docker", "exec", container_name,
        "stat", "-Lc", "%d:%i:%f", "--", target,
    ]
    try:
        out = run(argv, PROBE_TIMEOUT)
    except subprocess.SubprocessError as exc:
        raise RuntimeError(f"docker exec stat failed for {container_name}:{target}: {exc}")
    line = (out.stdout or "").strip()
    if not line:
        raise RuntimeError(f"empty stat output for {container_name}:{target}")
    parts = line.split(":")
    if len(parts) != 3:
        raise RuntimeError(f"unexpected stat output for {container_name}:{target}")
    try:
        device = int(parts[0], 10)
        inode = int(parts[1], 10)
        mode = int(parts[2], 16)
    except ValueError as exc:
        raise RuntimeError(f"unparseable stat output for {container_name}:{target}: {exc}")
    if statmod.S_ISDIR(mode):
        filetype = "dir"
    elif statmod.S_ISREG(mode):
        filetype = "file"
    else:
        filetype = "other"
    return {"device": device, "inode": inode, "filetype": filetype}


def prevalidate_binds(binds):
    for src, _dst, _ro in binds:
        host = host_stat(src)
        if "error" in host:
            raise RuntimeError(f"missing host source {src}")
        if host["filetype"] not in ("dir", "file"):
            raise RuntimeError(f"unsupported host filetype for {src}")


def bind_view_matches(container_name, src, dst, read_only, host=None, seen=None):
    host = host if host is not None else host_stat(src)
    seen = seen if seen is not None else exec_stat(container_name, dst)
    if (host["device"], host["inode"], host["filetype"]) == (
            seen["device"], seen["inode"], seen["filetype"]):
        return True
    if (not read_only or host["inode"] != seen["inode"] or
            host["filetype"] != seen["filetype"]):
        return False

    # Docker Desktop exposes a Windows v9fs bind through a different mount
    # namespace. Its st_dev may change while the object inode stays the same.
    # Keep the strict identity check for writable and native Linux binds.
    try:
        host_fs = run(["stat", "-f", "-c", "%T", "--", src], PROBE_TIMEOUT).stdout.strip()
        seen_fs = run(["docker", "exec", container_name, "stat", "-f", "-c",
                       "%T", "--", dst], PROBE_TIMEOUT).stdout.strip()
    except subprocess.SubprocessError as exc:
        raise RuntimeError(f"filesystem probe failed for {container_name}:{dst}: {exc}")
    if host_fs != "v9fs" or seen_fs != "v9fs":
        return False

    container = inspect_container(container_name)
    if not container or not isinstance(container.get("Mounts"), list):
        raise RuntimeError(f"mount inspection failed for {container_name}:{dst}")
    mounts = [m for m in container["Mounts"] if m.get("Destination") == dst]
    if (len(mounts) != 1 or mounts[0].get("Type") != "bind" or
            mounts[0].get("Source") != src or mounts[0].get("RW") is not False):
        raise RuntimeError(f"unexpected read-only bind declaration for {container_name}:{dst}")
    return True


def classify_running(container_name, binds):
    prevalidate_binds(binds)
    stale_target = ""
    for src, dst, ro in binds:
        host = host_stat(src)
        seen = exec_stat(container_name, dst)
        if not bind_view_matches(container_name, src, dst, ro, host, seen):
            stale_target = stale_target or dst
    return (True, f"stale-bind:{stale_target}") if stale_target else (False, "healthy")


def classify_stopped(container, binds):
    prevalidate_binds(binds)
    err = (container.get("State") or {}).get("Error") or ""
    if not err:
        return False, "stopped-no-evidence"
    low = err.lower()
    if "no such file or directory" in low:
        match = re.search(r'error mounting "([^"]+)" to rootfs at "([^"]+)"', err)
        if match:
            proxy, target = match.groups()
            declared = [item for item in binds if item[1] == target and item[2]]
            mounted = [item for item in container.get("Mounts", [])
                       if item.get("Destination") == target]
            if (len(declared) == len(mounted) == 1
                    and mounted[0].get("Type") == "bind"
                    and mounted[0].get("RW") is False
                    and mounted[0].get("Source") in (proxy, declared[0][0])
                    and deleted_desktop_file_bind(declared[0][0], proxy)):
                return True, "stopped-deleted-desktop-file-bind"
    if "not a directory" not in low and "is a directory" not in low:
        return False, "stopped-no-evidence"
    # Require a declared target as a complete path token. A named-volume,
    # executable or working-directory error elsewhere is not stale-bind proof.
    for _src, dst, _ro in binds:
        if re.search(r"(?<![\w/.-])" + re.escape(dst) + r"(?![\w/.-])", err):
            return True, "stopped-oci-mount-error"
    return False, "stopped-no-evidence"


def deleted_desktop_file_bind(source, proxy):
    """Prove that Desktop still pins a deleted, replaced read-only source file."""
    prefix = "/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/"
    if not proxy.startswith(prefix):
        return False
    suffix = proxy[len(prefix):]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[0-9a-f]{64}", suffix):
        return False
    local = Path("/mnt/wsl/docker-desktop-bind-mounts") / suffix
    descriptor = None
    try:
        original = Path(source)
        if original.resolve() != original or local.resolve() != local:
            return False
        current = original.lstat()
        descriptor = os.open(local, os.O_PATH | os.O_NOFOLLOW)
        old = os.fstat(descriptor)
        if (not statmod.S_ISREG(current.st_mode) or not statmod.S_ISREG(old.st_mode)
                or current.st_nlink < 1 or old.st_nlink != 0
                or current.st_dev != old.st_dev or current.st_ino == old.st_ino):
            return False
        fields = dict(line.split(":", 1) for line in
                      Path(f"/proc/self/fdinfo/{descriptor}").read_text().splitlines())
        mount_id = fields["mnt_id"].strip()
        rows = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()
                if line.split()[0] == mount_id]
        if len(rows) != 1:
            return False
        row = rows[0]
        def decode(value):
            return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)
        return (row[2] == f"{os.major(old.st_dev)}:{os.minor(old.st_dev)}"
                and decode(row[3]) == source + "//deleted"
                and decode(row[4]) == str(local))
    except (OSError, ValueError, KeyError, AttributeError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _bounded_tar_stream(container_name, argv, archive_path, deadline):
    """Stream tar stdout from docker exec into archive_path with byte/time caps.

    Returns (bytes_written, member_count). Raises RuntimeError on any failure.
    """
    capacity = os.statvfs(Path(archive_path).parent)
    if capacity.f_bavail * capacity.f_frsize < BACKUP_MAX_BYTES + 16 * 1024 * 1024:
        raise RuntimeError("private backup capacity insufficient; no service recreated")
    sel = selectors.DefaultSelector()
    proc = None
    total = 0
    members = 0
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        sel.register(proc.stdout, selectors.EVENT_READ)
        fd = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as fh:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"tar stream timed out for {container_name}")
                events = sel.select(timeout=min(remaining, 1.0))
                if not events:
                    if proc.poll() is not None:
                        break
                    continue
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > BACKUP_MAX_BYTES:
                    raise RuntimeError(
                        f"tar stream exceeded byte budget for {container_name}")
                fh.write(chunk)
        rc = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        if rc != 0:
            raise RuntimeError(
                f"tar stream exited {rc} for {container_name}")
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"tar stream failed for {container_name}: {exc}")
    finally:
        try:
            sel.close()
        except Exception:
            pass
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
    # Validate member count without reading content.
    try:
        with tarfile.open(archive_path, "r:") as tf:
            for _ in tf:
                members += 1
                if members > BACKUP_MAX_ENTRIES:
                    raise RuntimeError(
                        f"tar archive exceeded entry budget for {container_name}")
    except tarfile.TarError as exc:
        raise RuntimeError(
            f"tar archive unreadable for {container_name}: {exc}")
    return total, members


def backup_phantom(install_dir, container_name, binds):
    """Preserve RW phantom data from the CURRENT container view.

    Only backs up binds whose container view is provably stale relative to
    the host. Archives are streamed from the container's own view, never
    copied from the host filesystem.
    """
    parent = install_dir
    for component in Path(BACKUP_ROOT_REL).parts:
        parent = parent / component
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
        metadata = parent.lstat()
        if not statmod.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("unsafe backup directory ownership or type")
    root = parent
    if statmod.S_IMODE(root.stat().st_mode) & 0o077:
        raise RuntimeError("backup directory must be owner-private")
    txn = root / uuid.uuid4().hex
    txn.mkdir(mode=0o700)
    os.chmod(txn, 0o700)
    total = 0
    entries = 0
    deadline = time.monotonic() + PROBE_TIMEOUT
    if _deadline is not None:
        deadline = min(deadline, _deadline)
    for idx, (src, dst, ro) in enumerate(binds):
        if ro:
            continue
        host = host_stat(src)
        if "error" in host:
            raise RuntimeError(f"missing host source {src}")
        if host["filetype"] not in ("dir", "file"):
            raise RuntimeError(f"unsupported host filetype for {src}")
        try:
            seen = exec_stat(container_name, dst)
        except RuntimeError as exc:
            raise RuntimeError(str(exc))
        if (host["device"], host["inode"], host["filetype"]) == (
                seen["device"], seen["inode"], seen["filetype"]):
            continue
        if seen["filetype"] not in ("dir", "file"):
            raise RuntimeError(
                f"unsupported container filetype for {container_name}:{dst}")
        archive = txn / f"{idx:04d}.tar"
        if seen["filetype"] == "dir":
            argv = ["docker", "exec", container_name,
                    "tar", "-cf", "-", "-C", dst, "."]
        else:
            parent = os.path.dirname(dst.rstrip("/")) or "/"
            base = os.path.basename(dst.rstrip("/"))
            argv = ["docker", "exec", container_name,
                    "tar", "-cf", "-", "-C", parent, base]
        written, members = _bounded_tar_stream(
            container_name, argv, archive, deadline)
        total += written
        entries += members
        if total > BACKUP_MAX_BYTES:
            raise RuntimeError("phantom backup byte budget exceeded")
        if entries > BACKUP_MAX_ENTRIES:
            raise RuntimeError("phantom backup entry budget exceeded")
        log(f"preserved phantom {dst} -> {archive}")
    return total, entries


def recreate(flags, service):
    argv = [
        "docker", "compose", *flags, "up", "-d", "--no-deps",
        "--no-build", "--pull", "never", "--force-recreate", service,
    ]
    run(argv, COMPOSE_TIMEOUT, check=True)


def verify(flags, service, expected_binds):
    try:
        rows = compose_ps(flags)
    except RuntimeError as exc:
        return False, f"compose-ps-failed:{exc}"
    matched = False
    for row in rows:
        if row.get("Service") != service:
            continue
        name = row.get("Name")
        if not name:
            return False, "container-missing"
        matched = True
        container = inspect_container(name)
        if not container:
            return False, "inspect-failed"
        state = (container.get("State") or {}).get("Status", "")
        if (container.get("State") or {}).get("Paused") or state == "paused":
            return False, "paused"
        if state != "running":
            return False, f"state={state}"
        try:
            prevalidate_binds(expected_binds)
            for src, dst, ro in expected_binds:
                host = host_stat(src)
                seen = exec_stat(name, dst)
                if not bind_view_matches(name, src, dst, ro, host, seen):
                    return False, f"stale-bind:{dst}"
        except RuntimeError as exc:
            return False, f"verify-error:{exc}"
    return (True, "ok") if matched else (False, "container-missing")


def main(argv=None):
    global _deadline, _install_dir
    _deadline = None
    _install_dir = None
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--service", default="")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--repair-stopped", action="store_true",
                        help="Repair one proven stale stopped service; fail if no repair is needed")
    parser.add_argument("flags", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.repair_stopped and (not args.service or args.check):
        return fail("--repair-stopped requires one service and cannot be combined with --check")

    install_dir = Path(args.install_dir).resolve()
    if not install_dir.is_dir():
        return fail(f"install dir not found: {install_dir}")
    _install_dir = install_dir

    flags = args.flags[1:] if args.flags and args.flags[0] == "--" else args.flags
    if not flags:
        return fail("no compose flags supplied")

    if not is_wsl_docker_desktop():
        log("not WSL+DockerDesktop; skipping")
        return 3 if args.repair_stopped else 0
    _deadline = time.monotonic() + RECOVERY_TIMEOUT

    if shutil.which("docker") is None:
        return fail("docker not found")

    try:
        config = compose_config_json(flags)
    except RuntimeError as exc:
        return fail(str(exc))

    project = config.get("name") or ""
    if not project:
        return fail("compose config missing project name")

    profiles, wildcard = active_profiles(flags)
    requested = args.service.strip()

    try:
        rows = compose_ps(flags)
    except RuntimeError as exc:
        return fail(str(exc))

    candidates = []
    for row in rows:
        svc = row.get("Service") or ""
        # Compose ps includes project orphans after an extension is removed
        # or disabled. Only the caller's selected manifest authorizes recovery.
        if svc not in config["services"]:
            continue
        if requested and svc != requested:
            continue
        name = row.get("Name")
        if not name:
            continue
        container = inspect_container(name)
        if not container:
            return fail(f"inspect failed for container {name}")
        labels = (container.get("Config") or {}).get("Labels") or {}
        if labels.get("com.docker.compose.project") != project:
            continue
        if labels.get("com.docker.compose.service") != svc:
            continue
        if args.repair_stopped and labels.get("com.docker.compose.project.working_dir") != str(install_dir):
            return fail("stopped recovery container does not belong to the installed root")
        candidates.append((svc, name, container))

    if not candidates:
        log("no matching ODS containers; nothing to do")
        return 3 if args.repair_stopped else 0

    # Prevalidate ALL bind sources across ALL selected candidates before any
    # classification or mutation. Explicit --service bypasses profile gating
    # (matching Compose itself); bulk start skips disabled profiles.
    prepared = []
    for svc, name, container in candidates:
        try:
            if requested:
                binds = resolve_bind_targets(config, svc, None)
            else:
                binds = resolve_bind_targets(config, svc, profiles, wildcard)
        except RuntimeError as exc:
            return fail(str(exc))
        if not binds:
            continue
        if args.repair_stopped and any(not read_only for _src, _dst, read_only in binds):
            return fail("stopped recovery cannot preserve an unobserved writable bind view")
        try:
            prevalidate_binds(binds)
        except RuntimeError as exc:
            return fail(str(exc))
        prepared.append((svc, name, container, binds))

    stale_services = []
    for svc, name, container, binds in prepared:
        state = (container.get("State") or {}).get("Status", "")
        paused = bool((container.get("State") or {}).get("Paused"))
        if paused or state == "paused":
            return fail(
                f"service {svc} ({name}) is paused; refusing to mutate")
        try:
            if args.repair_stopped and state not in ("exited", "created", "dead"):
                return fail("stopped recovery refuses an active container")
            if state == "running":
                stale, reason = classify_running(name, binds)
            elif state in ("exited", "created", "dead"):
                stale, reason = classify_stopped(container, binds)
            else:
                stale, reason = False, f"state={state}"
        except RuntimeError as exc:
            return fail(str(exc))
        if stale:
            labels = (container.get("Config") or {}).get("Labels") or {}
            if labels.get("com.docker.compose.project.working_dir") != str(install_dir):
                return fail("stale recovery container does not belong to the installed root")
            if state in ("exited", "created", "dead"):
                # A stopped container has no readable phantom view to back up.
                # Apply the stopped-repair custody rules in both check and
                # repair modes, including the ordinary post-Compose retry.
                if any(not read_only for _src, _dst, read_only in binds):
                    return fail("stopped recovery cannot preserve an unobserved writable bind view")
            log(f"service {svc} ({name}) stale: {reason}")
            stale_services.append((svc, name, binds, state))
        else:
            log(f"service {svc} ({name}) {reason}")

    if not stale_services:
        log("all bind views healthy")
        return 3 if args.repair_stopped else 0

    if args.check:
        log(f"check mode: {len(stale_services)} stale service(s)")
        return 2

    backup_bytes = backup_entries = 0
    for svc, name, binds, state in stale_services:
        if state != "running":
            continue
        try:
            size, count = backup_phantom(install_dir, name, binds)
            backup_bytes += size
            backup_entries += count
            if backup_bytes > BACKUP_MAX_BYTES or backup_entries > BACKUP_MAX_ENTRIES:
                raise RuntimeError("combined backup budget exceeded")
        except (RuntimeError, OSError) as exc:
            return fail(str(exc))

    # Compose recreates the entire service, including its replicas. Preserve
    # every original stale view above, then recreate and verify each service once.
    service_binds = {svc: binds for svc, _name, binds, _state in stale_services}
    for svc in service_binds:
        log(f"recreating {svc}")
        try:
            recreate(flags, svc)
        except (subprocess.SubprocessError, RuntimeError) as exc:
            return fail(f"recreate failed for {svc}: {exc}")

    for svc, binds in service_binds.items():
        ok, reason = verify(flags, svc, binds)
        if not ok:
            return fail(f"post-recreate verification failed for {svc}: {reason}")
        log(f"verified {svc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
