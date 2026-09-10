"""ODS compatibility repair for OpenClaw 2026.6.33 unknown-tool recovery.

The reviewed transformation is bound to exact original and replacement bytes.
OpenClaw's other detectors and limits are unchanged. An owner-private backup
and receipt retain provenance; --restore refuses to overwrite later changes.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

MANIFEST = Path(__file__).with_name("openclaw-tool-recovery.json")
MODULE = "tool-loop-detection-C0oQKkXZ.js"
COMPLETION_MODULE = "agent-command-DeS125kF.js"
IMAGE_MODULE = "tool-search-BInRpkE3.js"
COMPACTION_MODULE = "embedded-agent-subscribe.handlers.compaction.runtime.js"
COMPACTION_CHUNK = "embedded-agent-subscribe.handlers.compaction.runtime-BcFOW95l.js"
VERSION = "2026.6.33"


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


def repair(runtime_root, state_dir, *, restore=False, manifest_path=MANIFEST,
           module_name=MODULE):
    if module_name not in {MODULE, COMPLETION_MODULE, IMAGE_MODULE, COMPACTION_MODULE}:
        raise ValueError("unsupported runtime repair module")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = json.loads((runtime_root / "package.json").read_text(encoding="utf-8"))
    if package.get("name") != "openclaw":
        raise ValueError("runtime repair target is not OpenClaw")
    if package.get("version") != VERSION:
        return {"status": "not-applicable", "version": package.get("version")}
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
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
        if current_hash != before and reverse is None:
            raise ValueError("OpenClaw recovery module differs from reviewed bytes")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openclaw-bin", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--restore", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--completion-recovery", action="store_true")
    selection.add_argument("--image-envelope", action="store_true")
    selection.add_argument("--compaction-export", action="store_true")
    args = parser.parse_args()
    runtime_root = args.openclaw_bin.resolve(strict=True).parent
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
    print(json.dumps(repair(runtime_root, args.state_dir, restore=args.restore, **options)))


if __name__ == "__main__":
    main()
