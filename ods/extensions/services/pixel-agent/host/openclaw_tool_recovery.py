"""ODS compatibility repair for OpenClaw 2026.6.33 unknown-tool recovery.

The reviewed transformation is bound to exact original and replacement bytes.
OpenClaw's other detectors and limits are unchanged. An owner-private backup
and receipt retain provenance; --restore refuses to overwrite later changes.
Bytes another ODS build recorded in that receipt are rebuilt from the backup.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

MANIFEST = Path(__file__).with_name("openclaw-tool-recovery.json")
MODULE = "tool-loop-detection-C0oQKkXZ.js"
COMPLETION_MODULE = "agent-command-DeS125kF.js"
IMAGE_MODULE = "tool-search-BInRpkE3.js"
COMPACTION_MODULE = "embedded-agent-subscribe.handlers.compaction.runtime.js"
COMPACTION_CHUNK = "embedded-agent-subscribe.handlers.compaction.runtime-BcFOW95l.js"
COMPACTION_IDLE_MODULE = "sessions-KE_Xmzwf.js"
COMPACTION_RESUME_MODULE = "sessions-CZbwb3_c.js"
COMPACTION_BUDGET_MODULE = "selection-BEwSQKM-.js"
READ_RANGE_MODULE = "openclaw-tools-iHHy99PD.js"
TOOL_RESULT_PROJECTION_MODULE = "tool-result-truncation-CbxVHy2D.js"
VERSION = "2026.6.33"
SHA256 = re.compile(r"[0-9a-f]{64}")
RUNTIME_MODULE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\.js")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path, *, private=False):
    info = path.lstat()
    # npm commonly installs owner-group-writable package files (0664). Their
    # exact reviewed hash is still required; backups must remain owner-only.
    forbidden_permissions = 0o077 if private else 0o002
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & forbidden_permissions):
        raise ValueError("runtime repair requires owner-controlled regular files")
    return path.read_bytes(), stat.S_IMODE(info.st_mode)


def atomic_write(path, data, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix=".ods-repair-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify_dependencies(runtime_root, manifest, module_name):
    """A facade repair is valid only with its reviewed implementation chunk."""
    expected = manifest.get("reviewedDependencies", {})
    allowed = {COMPACTION_CHUNK} if module_name == COMPACTION_MODULE else set()
    if not isinstance(expected, dict) or set(expected) != allowed:
        raise ValueError("runtime repair dependency contract mismatch")
    for name, sha256 in expected.items():
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise ValueError("runtime repair dependency hash is invalid")
        data, _ = read_regular(runtime_root / "dist" / name)
        if digest(data) != sha256:
            raise ValueError("runtime repair dependency differs from reviewed bytes")
    return dict(expected)


def verified_custody(state_dir):
    """Return an ODS repair receipt, its recorded bytes, and verified original.

    Every ODS build records the module, the reviewed source and intended bytes
    before it changes the runtime. Accept that record only while its private
    backup still hashes to the recorded source.
    """
    payload, _ = read_regular(state_dir / "receipt.json", private=True)
    receipt = json.loads(payload)
    if not isinstance(receipt, dict):
        raise ValueError("runtime repair receipt is not an exact ODS custody record")
    recorded = [receipt.get(key) for key in ("sourceSha256", "patchedSha256", "desiredSha256")]
    if "recoveredSha256" in receipt:
        recorded.append(receipt["recoveredSha256"])
    if (receipt.get("schemaVersion") != 1 or receipt.get("version") != VERSION
            or not isinstance(receipt.get("module"), str)
            or not RUNTIME_MODULE.fullmatch(receipt["module"])
            or not all(isinstance(item, str) and SHA256.fullmatch(item) for item in recorded)
            or receipt.get("backup") != recorded[0] + ".js"):
        raise ValueError("runtime repair receipt is not an exact ODS custody record")
    original, _ = read_regular(state_dir / receipt["backup"], private=True)
    if digest(original) != receipt["sourceSha256"]:
        raise ValueError("runtime repair backup hash mismatch")
    return receipt, set(recorded), original


def recorded_original(state_dir, module_name, source_sha256, current_hash):
    """Rebuild from this set's backup when its receipt records current bytes."""
    try:
        receipt, recorded, original = verified_custody(state_dir)
    except FileNotFoundError:
        receipt, recorded, original = None, set(), None
    if (receipt is None or receipt["module"] != module_name
            or receipt["sourceSha256"] != source_sha256 or current_hash not in recorded):
        raise ValueError("OpenClaw recovery module differs from reviewed bytes")
    return original


def repair(runtime_root, state_dir, *, restore=False, manifest_path=MANIFEST,
           module_name=MODULE):
    if module_name not in {MODULE, COMPLETION_MODULE, IMAGE_MODULE, COMPACTION_MODULE, COMPACTION_IDLE_MODULE,
                           COMPACTION_RESUME_MODULE, COMPACTION_BUDGET_MODULE, READ_RANGE_MODULE,
                           TOOL_RESULT_PROJECTION_MODULE}:
        raise ValueError("unsupported runtime repair module")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = json.loads((runtime_root / "package.json").read_text(encoding="utf-8"))
    if package.get("name") != "openclaw":
        raise ValueError("runtime repair target is not OpenClaw")
    if package.get("version") != VERSION:
        return {"status": "not-applicable", "version": package.get("version")}
    # The shared state root is created here first; keep it owner-only
    # whatever the owner's umask (user-private-group systems use 002).
    state_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_dir.mkdir(mode=0o700, exist_ok=True)
    info = state_dir.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise ValueError("runtime repair state must be an owner-private directory")
    lock_fd = os.open(state_dir / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        module = runtime_root / "dist" / module_name
        current, mode = read_regular(module)
        dependencies = verify_dependencies(runtime_root, manifest, module_name)
        before = manifest["sourceSha256"]
        after = manifest["patchedSha256"]
        current_hash = digest(current)
        predecessors = manifest.get("previousReplacements", {})
        reverse = (manifest["replacements"] if current_hash == after
                   else predecessors.get(current_hash))
        recovered = None
        if current_hash != before and reverse is None:
            # Another ODS build may have applied its own reviewed recipe. Use
            # this set's verified backup only when its receipt records those
            # exact live bytes; unrelated changes still fail closed.
            original = recorded_original(state_dir, module_name, before, current_hash)
            recovered = current_hash
        else:
            original = current.decode("utf-8")
            if current_hash != before:
                # Only an exact source-bound predecessor may migrate. Reconstruct
                # and hash the same original bytes before applying the new recipe.
                for old, new in reversed(reverse):
                    if original.count(new) != 1:
                        raise ValueError("runtime repair cannot recover reviewed baseline")
                    original = original.replace(new, old)
            original = original.encode("utf-8")
        if digest(original) != before:
            raise ValueError("runtime repair baseline hash mismatch")
        backup = state_dir / (before + ".js")
        if backup.exists() or backup.is_symlink():
            saved, _ = read_regular(backup, private=True)
            if digest(saved) != before:
                raise ValueError("runtime repair backup hash mismatch")
        else:
            atomic_write(backup, original)
        candidate = original.decode("utf-8")
        for old, new in manifest["replacements"]:
            if candidate.count(old) != 1:
                raise ValueError("runtime repair transformation is not unique")
            candidate = candidate.replace(old, new)
        candidate = candidate.encode("utf-8")
        if digest(candidate) != after:
            raise ValueError("runtime repair candidate hash mismatch")
        target = original if restore else candidate
        receipt = {"schemaVersion": 1, "version": VERSION, "module": module_name,
                   "sourceSha256": before, "patchedSha256": after,
                   "backup": backup.name, "desiredSha256": digest(target)}
        if dependencies:
            receipt["reviewedDependencies"] = dependencies
        if recovered is not None:
            # Keep the replaced bytes in custody so an interrupted recovery
            # can resume from this same record.
            receipt["recoveredFrom"] = "verified-backup"
            receipt["recoveredSha256"] = recovered
        receipt_path = state_dir / "receipt.json"
        # Record custody before changing executable bytes, including interrupted
        # attempts. Reruns accept only exact reviewed source/patch byte states.
        atomic_write(receipt_path, json.dumps(receipt, sort_keys=True).encode())
        changed = current != target
        if changed:
            latest, _ = read_regular(module)
            if latest != current:
                raise ValueError("runtime changed while preparing recovery repair")
            verify_dependencies(runtime_root, manifest, module_name)
            atomic_write(module, target, mode)
        if digest(read_regular(module)[0]) != digest(target):
            raise ValueError("runtime repair readback failed")
        verify_dependencies(runtime_root, manifest, module_name)
        return {**receipt, "status": "changed" if changed else "unchanged",
                "restored": restore}


def private_directory(path, *, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise ValueError("runtime repair state must be an owner-private directory")


def fsync_directory(path):
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def tighten_directory(path):
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory_fd)
        if info.st_uid != os.getuid():
            raise ValueError("runtime repair state root must be an owner-controlled directory")
        os.fchmod(directory_fd, stat.S_IMODE(info.st_mode) & ~0o022)
        if os.fstat(directory_fd).st_mode & 0o022:
            raise ValueError("runtime repair state root must be an owner-controlled directory")
    finally:
        os.close(directory_fd)


def restore_foreign(runtime_root, patches_root, known):
    """Restore runtime patch sets another ODS build left outside this version.

    A set is restored only when its private receipt records the live module
    bytes and its backup hashes to the recorded source. All sets are verified
    before any write; each restored state is archived, never deleted.
    """
    package = json.loads((runtime_root / "package.json").read_text(encoding="utf-8"))
    if package.get("name") != "openclaw":
        raise ValueError("runtime repair target is not OpenClaw")
    if package.get("version") != VERSION:
        return {"status": "not-applicable", "version": package.get("version")}
    if not known:
        raise ValueError("foreign runtime patch restore requires this version's sets")
    if not patches_root.exists() and not patches_root.is_symlink():
        return {"status": "unchanged", "foreign": []}
    info = patches_root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("runtime repair state root must be an owner-controlled directory")
    if info.st_mode & 0o022:
        # Earlier builds created this root through mkdir(parents=True), so a
        # user-private-group umask (002) left the owner's own directory
        # group-writable. Remove those bits through a no-follow handle before
        # trusting any entry instead of refusing the owner's own state.
        tighten_directory(patches_root)
    plan, modules = [], set()
    for state_dir in sorted(patches_root.iterdir()):
        if state_dir.name in known:
            continue
        try:
            private_directory(state_dir)
            if not os.path.lexists(state_dir / "receipt.json"):
                # Custody is recorded before any module write; no receipt
                # means this set never changed the runtime.
                continue
            receipt, recorded, original = verified_custody(state_dir)
            if receipt["module"] in modules:
                raise ValueError("another foreign set records the same module")
            modules.add(receipt["module"])
            module = runtime_root / "dist" / receipt["module"]
            current, mode = read_regular(module)
            if digest(current) not in recorded:
                raise ValueError("its receipt does not record the live module bytes")
        except (OSError, ValueError) as error:
            raise ValueError(f"foreign runtime patch set {state_dir.name!r} "
                             f"cannot be restored: {error}") from error
        plan.append((state_dir, module, current, mode, original, receipt["sourceSha256"]))
    foreign = []
    if plan:
        archive_root = patches_root.with_name(patches_root.name + ".retired")
        private_directory(archive_root, create=True)
    for state_dir, module, current, mode, original, source in plan:
        if current != original:
            latest, _ = read_regular(module)
            if latest != current:
                raise ValueError("runtime changed while preparing foreign patch restore")
            atomic_write(module, original, mode)
        if digest(read_regular(module)[0]) != source:
            raise ValueError("foreign runtime patch restore readback failed")
        # A unique private container keeps every archived receipt and backup.
        archive = Path(tempfile.mkdtemp(prefix=state_dir.name + ".", dir=archive_root))
        os.rename(state_dir, archive / state_dir.name)
        for directory in (archive, archive_root, patches_root):
            fsync_directory(directory)
        foreign.append({"set": state_dir.name, "module": module.name,
                        "fromSha256": digest(current), "sourceSha256": source,
                        "status": "changed" if current != original else "unchanged",
                        "archive": str(archive / state_dir.name)})
    return {"status": "changed" if foreign else "unchanged", "foreign": foreign}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openclaw-bin", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--known", nargs="+", default=[], metavar="SET",
                        help="patch set names this ODS version manages")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--completion-recovery", action="store_true")
    selection.add_argument("--image-envelope", action="store_true")
    selection.add_argument("--compaction-export", action="store_true")
    selection.add_argument("--compaction-idle", action="store_true")
    selection.add_argument("--compaction-resume", action="store_true")
    selection.add_argument("--compaction-budget", action="store_true")
    selection.add_argument("--read-range", action="store_true")
    selection.add_argument("--tool-result-projection", action="store_true")
    selection.add_argument("--restore-foreign", type=Path, metavar="PATCHES_ROOT",
                           help="restore and archive patch sets not named by --known")
    args = parser.parse_args()
    if args.restore_foreign is not None:
        if args.state_dir is not None or args.restore or not args.known:
            parser.error("--restore-foreign requires --known and excludes --state-dir/--restore")
    elif args.state_dir is None or args.known:
        parser.error("a single repair requires --state-dir and excludes --known")
    runtime_root = args.openclaw_bin.resolve(strict=True).parent
    if args.restore_foreign is not None:
        print(json.dumps(restore_foreign(runtime_root, args.restore_foreign, set(args.known))))
        return
    options = {}
    if args.completion_recovery:
        options = {"module_name": COMPLETION_MODULE,
                   "manifest_path": MANIFEST.with_name("openclaw-completion-recovery.json")}
    elif args.image_envelope:
        options = {"module_name": IMAGE_MODULE,
                   "manifest_path": MANIFEST.with_name("openclaw-image-envelope.json")}
    elif args.compaction_export:
        options = {"module_name": COMPACTION_MODULE,
                   "manifest_path": MANIFEST.with_name("openclaw-compaction-export.json")}
    elif args.compaction_budget:
        options = {"module_name": COMPACTION_BUDGET_MODULE,
                   "manifest_path": MANIFEST.with_name("openclaw-compaction-budget.json")}
    elif args.read_range:
        options = {"module_name": READ_RANGE_MODULE,
                   "manifest_path": MANIFEST.with_name("openclaw-read-range.json")}
    elif args.tool_result_projection:
        options = {"module_name": TOOL_RESULT_PROJECTION_MODULE,
                   "manifest_path": MANIFEST.with_name("openclaw-tool-result-projection.json")}
    elif args.compaction_resume:
        options = {"module_name": COMPACTION_RESUME_MODULE,
                   "manifest_path": MANIFEST.with_name("openclaw-compaction-resume.json")}
    elif args.compaction_idle:
        options = {"module_name": COMPACTION_IDLE_MODULE,
                   "manifest_path": MANIFEST.with_name("openclaw-compaction-idle.json")}
    print(json.dumps(repair(runtime_root, args.state_dir, restore=args.restore, **options)))


if __name__ == "__main__":
    main()
