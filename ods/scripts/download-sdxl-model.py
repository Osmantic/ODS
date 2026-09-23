#!/usr/bin/env python3
"""Publish the pinned SDXL checkpoint only after checking its size and SHA-256."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile


REVISION = "c9a24f48e1c025556787b0c58dd67a091ece2e44"
FILENAME = "sdxl_lightning_4step.safetensors"
URL = f"https://huggingface.co/ByteDance/SDXL-Lightning/resolve/{REVISION}/{FILENAME}"
SHA256 = "e0d996ee0013e79d9d3561f50fcafb9a17e3ff07b780358e3b66d67932c4d490"
SIZE = 6938040682


def verified(path):
    """Do not follow links or accept a same-size substituted checkpoint."""
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != SIZE:
            return False
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return False
            digest = hashlib.sha256()
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
            after = os.fstat(handle.fileno())
        current = path.lstat()
        def identity(value):
            return (value.st_dev, value.st_ino, value.st_size,
                    value.st_mtime_ns, value.st_ctime_ns)

        return (stat.S_ISREG(current.st_mode) and identity(before) == identity(after)
                == identity(current) and digest.hexdigest() == SHA256)
    except OSError:
        return False


def download(directory):
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("Checkpoint directory must be a real directory")
    destination = directory / FILENAME
    if os.path.lexists(destination):
        if verified(destination):
            return destination
        raise RuntimeError("Existing checkpoint failed verification; preserved for inspection")
    # A private, same-filesystem staging directory avoids predictable .part
    # symlinks. Publish with a hard link so a concurrent owner file is never
    # overwritten. NTFS, APFS and the supported Linux filesystems support it.
    with tempfile.TemporaryDirectory(prefix=".ods-sdxl-", dir=directory) as staging:
        candidate = Path(staging) / FILENAME
        subprocess.run([
            "curl", "--fail", "--silent", "--show-error", "--location",
            "--proto", "=https", "--proto-redir", "=https",
            "--connect-timeout", "30", "--max-time", "3600",
            "--retry", "3", "--retry-delay", "5", "--retry-max-time", "3600",
            "--max-filesize", str(SIZE), "--output", str(candidate), URL,
        ], check=True, timeout=3700)
        if not verified(candidate):
            raise RuntimeError("Downloaded checkpoint size or SHA-256 did not match")
        candidate.chmod(0o644)
        try:
            os.link(candidate, destination)
        except FileExistsError:
            if not verified(destination):
                raise RuntimeError("Concurrent checkpoint failed verification; preserved") from None
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        download(args.directory)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"[SDXL] ERROR: {error}", flush=True)
        return 1
    print(f"[SDXL] Verified {FILENAME}: sha256={SHA256}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
