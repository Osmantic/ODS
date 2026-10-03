#!/usr/bin/env python3
"""Create a non-overwritable, secret-free source provenance manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
MAX_TRACKED_FILE_BYTES = 50 * 1024 * 1024


class AssuranceError(RuntimeError):
    pass


def git(*args: str, root: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "git failed"
        raise AssuranceError(detail)
    return result.stdout


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def tracked_files(root: Path = ROOT) -> tuple[list[dict[str, object]], int]:
    raw = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if raw.returncode:
        raise AssuranceError(raw.stderr.decode(errors="replace").strip() or "git ls-files failed")
    records: list[dict[str, object]] = []
    total_bytes = 0
    for encoded in filter(None, raw.stdout.split(b"\0")):
        relative = encoded.decode("utf-8", errors="strict")
        path = root / relative
        if path.is_symlink():
            payload = os.readlink(path).encode("utf-8")
            kind = "symlink"
        elif path.is_file():
            size = path.stat().st_size
            if size > MAX_TRACKED_FILE_BYTES:
                raise AssuranceError(f"tracked file exceeds {MAX_TRACKED_FILE_BYTES} bytes: {relative}")
            payload = path.read_bytes()
            kind = "file"
        else:
            raise AssuranceError(f"tracked path is not a regular file or symlink: {relative}")
        total_bytes += len(payload)
        records.append({
            "path": relative.replace(os.sep, "/"),
            "kind": kind,
            "bytes": len(payload),
            "sha256": sha256_bytes(payload),
        })
    return records, total_bytes


def build_manifest(root: Path = ROOT) -> dict[str, object]:
    root = root.resolve()
    if not (root / ".git").exists():
        raise AssuranceError(f"not a Git worktree: {root}")
    dirty = git("status", "--porcelain=v1", "--untracked-files=all", root=root)
    if dirty:
        raise AssuranceError("refusing to attest a dirty worktree")

    release = json.loads((root / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
    catalog_path = root / "security-evals" / "assurance" / "cases.json"
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    files, total_bytes = tracked_files(root)
    return {
        "schemaVersion": 1,
        "source": {
            "commit": git("rev-parse", "HEAD", root=root).strip(),
            "tree": git("rev-parse", "HEAD^{tree}", root=root).strip(),
            "clean": True,
            "trackedFileCount": len(files),
            "trackedBytes": total_bytes,
            "files": files,
        },
        "release": {
            "pixel": release["pixel"],
            "openclaw": release["openclaw"],
        },
        "attackCatalog": {
            "schemaVersion": catalog["schemaVersion"],
            "caseCount": len(catalog["cases"]),
            "sha256": sha256_bytes(catalog_bytes),
        },
    }


def write_manifest(output: Path, root: Path = ROOT) -> dict[str, object]:
    if not output.is_absolute():
        raise AssuranceError("evidence output must be an absolute path")
    root = root.resolve()
    output = output.resolve(strict=False)
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise AssuranceError("evidence output must be outside the source worktree")
    if not output.parent.is_dir():
        raise AssuranceError(f"evidence directory does not exist: {output.parent}")

    manifest = build_manifest(root)
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise AssuranceError(f"refusing to overwrite evidence: {output}") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            output.unlink()
        except OSError:
            pass
        raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new absolute path outside the source tree")
    args = parser.parse_args()
    try:
        manifest = write_manifest(args.output)
    except (AssuranceError, KeyError, json.JSONDecodeError, OSError) as exc:
        print(f"assurance manifest failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "path": str(args.output.resolve()),
        "commit": manifest["source"]["commit"],
        "tree": manifest["source"]["tree"],
        "trackedFileCount": manifest["source"]["trackedFileCount"],
        "attackCaseCount": manifest["attackCatalog"]["caseCount"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
