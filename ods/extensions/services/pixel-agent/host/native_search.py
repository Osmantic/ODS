"""Provision the pinned OpenClaw search plugin; Pixel owns its extension digest."""

import argparse
import base64
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
import urllib.request

PACKAGE = "@openclaw/parallel-plugin"
VERSION = "2026.6.33"
URL = "https://registry.npmjs.org/@openclaw/parallel-plugin/-/parallel-plugin-2026.6.33.tgz"
INTEGRITY = "nxbBR2YnHTW/vR57MxrSxncnOBmzzH7ejMyT8l96Qi5Gk2xbTZKonlZFAZ0ZAjBNdjNuOvzN/xqsne0Qw3tRLw=="
MAX_ARCHIVE = 512 * 1024
MAX_CONTENT = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("unexpected redirect from the pinned search package URL")


def checked(path, *, directory=False, private=False):
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not valid_type or info.st_uid != os.getuid()
            or info.st_mode & (0o077 if private else 0o022)
            or (not directory and info.st_nlink != 1)):
        raise ValueError(f"unsafe search plugin path: {path}")
    return info


def read_private(path):
    # Open without waiting for a FIFO peer, then enforce the regular-file
    # contract on the descriptor before reading. O_NONBLOCK is inert on files.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > MAX_ARCHIVE):
            raise ValueError("search archive must be a bounded owner-private regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(MAX_ARCHIVE + 1)
        if len(data) > MAX_ARCHIVE:
            raise ValueError("search archive exceeds its size limit")
        return data
    finally:
        os.close(fd)


def verify_archive(data):
    actual = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    if len(data) > MAX_ARCHIVE or actual != INTEGRITY:
        raise ValueError("search plugin archive differs from the pinned release; inspect the cache before retrying")


def archive_files(data):
    if len(data) > MAX_ARCHIVE:
        raise ValueError("search archive exceeds its size limit")
    files, seen, total = {}, set(), 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for count, entry in enumerate(archive, 1):
            name = entry.name.rstrip("/") if entry.isdir() else entry.name
            parts = name.split("/")
            if (count > 64 or not parts or parts[0] != "package"
                    or any(p in {"", ".", ".."} for p in parts)
                    or any(ord(c) < 32 or ord(c) == 127 for c in name)
                    or "\\" in name or name in seen
                    or not (entry.isfile() or entry.isdir())):
                raise ValueError("invalid search plugin archive entry")
            seen.add(name)
            if len(parts) == 1:
                if not entry.isdir():
                    raise ValueError("package root must be a directory")
                continue
            relative = "/".join(parts[1:])
            if "node_modules" in parts:
                raise ValueError("search plugin must not contain node_modules")
            if entry.isdir():
                continue
            total += entry.size
            if not 0 <= entry.size <= MAX_ARCHIVE or total > MAX_CONTENT:
                raise ValueError("search plugin content exceeds its size limit")
            with archive.extractfile(entry) as handle:
                content = handle.read(entry.size + 1)
            if len(content) != entry.size:
                raise ValueError("incomplete search plugin archive entry")
            files[relative] = content
    package = json.loads(files.get("package.json", b"null"))
    manifest = json.loads(files.get("openclaw.plugin.json", b"null"))
    providers = manifest.get("contracts", {}).get("webSearchProviders") if (
        isinstance(manifest, dict) and isinstance(manifest.get("contracts"), dict)) else None
    if (not isinstance(package, dict) or package.get("name") != PACKAGE
            or package.get("version") != VERSION or not isinstance(manifest, dict)
            or manifest.get("id") != "parallel"
            or not isinstance(providers, list) or "parallel-free" not in providers):
        raise ValueError("search plugin identity or keyless provider contract differs")
    return files


def expected_directories(files):
    return {str(p) for name in files for p in PurePosixPath(name).parents if str(p) != "."}


def verify_tree(target, files):
    checked(target, directory=True)
    actual_files, actual_dirs = set(), set()
    for parent, directories, names in os.walk(target, followlinks=False):
        for name in directories:
            path = Path(parent) / name
            checked(path, directory=True)
            actual_dirs.add(path.relative_to(target).as_posix())
        for name in names:
            path = Path(parent) / name
            info = checked(path)
            relative = path.relative_to(target).as_posix()
            if relative not in files or info.st_size != len(files[relative]) or path.read_bytes() != files[relative]:
                raise ValueError("installed search plugin bytes differ; immutable directory was retained")
            actual_files.add(relative)
    if actual_files != set(files) or actual_dirs != expected_directories(files):
        raise ValueError("installed search plugin tree differs; immutable directory was retained")


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def select_provider(answers_file, explicit=None):
    if explicit:
        provider = explicit
    else:
        path = Path(answers_file)
        if not path.exists() and not path.is_symlink():
            return "parallel-free"
        answers = json.loads(read_private(path))
        if not isinstance(answers, dict):
            raise ValueError("invalid existing search onboarding contract")
        # Old installers always wrote SearXNG. Preserve that installation until
        # its owner explicitly selects the native provider.
        provider = answers.get("webSearchProvider", "searxng")
    if not isinstance(provider, str) or provider not in {"searxng", "parallel-free"}:
        raise ValueError("search provider must be searxng or parallel-free")
    return provider


def prepare(base_dir):
    raw = os.fspath(base_dir)
    base = Path(raw)
    if (not base.is_absolute() or base == Path("/") or ".." in base.parts
            or any(ord(c) < 32 or ord(c) == 127 for c in raw)):
        raise ValueError("search plugin base must be a specific absolute directory")
    for parent in [base, *base.parents]:
        if parent.is_symlink():
            raise ValueError("search plugin base cannot traverse symbolic links")
    base.mkdir(mode=0o755, parents=True, exist_ok=True)
    checked(base, directory=True)
    lock = base / ".install.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = checked(lock, private=True)
        opened = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("search plugin lock changed while opening")
        fcntl.flock(fd, fcntl.LOCK_EX)
        cache = base / f"parallel-{VERSION}.tgz"
        if cache.exists() or cache.is_symlink():
            data = read_private(cache)
            verify_archive(data)
        else:
            with urllib.request.build_opener(NoRedirect()).open(URL, timeout=30) as response:
                data = response.read(MAX_ARCHIVE + 1)
            verify_archive(data)
            archive_fd, temporary_name = tempfile.mkstemp(dir=base, prefix=".archive-")
            temporary = Path(temporary_name)
            try:
                with os.fdopen(archive_fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, cache)
                sync_directory(base)
            finally:
                temporary.unlink(missing_ok=True)
        files = archive_files(data)
        target = base / f"parallel-{VERSION}"
        changed = not (target.exists() or target.is_symlink())
        if changed:
            stage = Path(tempfile.mkdtemp(dir=base, prefix=".parallel-stage-"))
            try:
                for directory in sorted(expected_directories(files)):
                    (stage / directory).mkdir(mode=0o755, parents=True, exist_ok=True)
                for name, data in files.items():
                    with (stage / name).open("xb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fchmod(handle.fileno(), 0o644)
                        os.fsync(handle.fileno())
                for directory in sorted(expected_directories(files), reverse=True):
                    sync_directory(stage / directory)
                stage.chmod(0o755)
                verify_tree(stage, files)
                sync_directory(stage)
                os.rename(stage, target)
                sync_directory(base)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        verify_tree(target, files)
        return {"provider": "parallel-free", "id": "parallel", "path": str(target),
                "packageVersion": VERSION, "archiveSha512": INTEGRITY, "changed": changed}
    finally:
        os.close(fd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base-dir")
    mode.add_argument("--answers-file")
    parser.add_argument("--provider")
    args = parser.parse_args()
    try:
        if args.answers_file:
            print(select_provider(args.answers_file, args.provider))
        elif args.provider:
            parser.error("--provider requires --answers-file")
        else:
            print(json.dumps(prepare(args.base_dir)))
    except (ValueError, OSError, tarfile.TarError) as error:
        parser.exit(1, f"Native search setup failed: {error}\n")
