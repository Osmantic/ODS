"""Bounded Markdown operations through a pinned, no-symlink vault descriptor."""
from contextlib import contextmanager
import os
import pathlib
import re
import stat

from integrations_common import IntegrationError, clip

MAX_NOTE_BYTES = 1024 * 1024
MAX_NOTE_FILES = 5000
MAX_NOTE_READ_CHARS = 20000
MAX_WRITE_CHARS = 20000
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def relative_name(relative):
    if not isinstance(relative, str) or not relative.strip() or len(relative) > 512 or "\0" in relative:
        raise IntegrationError("invalid-note", "Give the note's path inside the vault, such as Projects/Plan.md.")
    relative = relative.strip()
    if relative.startswith("/") or relative.endswith("/") or any(part.startswith(".") for part in relative.split("/")):
        raise IntegrationError("invalid-note", "Notes outside the vault or in hidden folders cannot be used.")
    if not relative.lower().endswith(".md"):
        relative += ".md"
    return pathlib.PurePosixPath(relative).as_posix()


@contextmanager
def open_vault(config):
    notes = config["notes"]
    if not notes["enabled"]:
        raise IntegrationError("notes-off", "Notes are not set up. The owner can connect an Obsidian vault in Settings → Portal integrations.")
    path = pathlib.Path(notes["vaultPath"])
    if not path.is_absolute() or ".." in path.parts:
        raise IntegrationError("notes-unavailable", "The notes folder must be an absolute folder without symbolic links.")
    fd = None
    try:
        fd = os.open("/", DIRECTORY_FLAGS)
        for part in path.parts[1:]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
    except OSError:
        if fd is not None:
            os.close(fd)
        raise IntegrationError("notes-unavailable", "The notes folder is unavailable or contains a symbolic link.") from None
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _parent(root, relative, *, create=False):
    fd = os.dup(root)
    try:
        for part in pathlib.PurePosixPath(relative).parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, pathlib.PurePosixPath(relative).name
    finally:
        os.close(fd)


def _regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise IntegrationError("invalid-note", "Only regular notes without links can be used.")
    if info.st_size > MAX_NOTE_BYTES:
        raise IntegrationError("note-too-large", "That note is too large to read or change.")
    return info


def _read_bytes(fd):
    _regular(fd)
    data = bytearray()
    while len(data) <= MAX_NOTE_BYTES:
        piece = os.read(fd, min(65536, MAX_NOTE_BYTES + 1 - len(data)))
        if not piece:
            return bytes(data)
        data.extend(piece)
    raise IntegrationError("note-too-large", "That note is too large to read or change.")


def _open_note(parent, name, flags):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise IntegrationError("invalid-note", "Only regular notes without links can be used.")
    fd = os.open(name, flags | FILE_FLAGS, dir_fd=parent)
    try:
        _regular(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _hit(relative, text, terms):
    lowered, title = text.lower(), pathlib.PurePosixPath(relative).stem.lower()
    matched = [term for term in terms if term in lowered or term in title]
    if not matched:
        return None
    score = len(matched) * 100 + sum(lowered.count(term) for term in matched) + 20 * sum(term in title for term in matched)
    lines = text.splitlines() or [""]
    best = max(range(len(lines)), key=lambda i: (sum(term in lines[i].lower() for term in terms),
                                                not lines[i].lstrip().startswith("#")))
    return score, {"path": relative, "title": pathlib.PurePosixPath(relative).stem, "line": best + 1,
                   "snippet": lines[best].strip()[:240], "matched": len(matched), "of": len(terms)}


def search(config, query):
    terms = [term for term in re.findall(r"\w+", query.lower()) if len(term) > 1][:12]
    if not terms:
        raise IntegrationError("invalid-query", "Give one or more words to search for.")
    hits, count = [], 0
    def walk(directory, prefix="", depth=0):
        nonlocal count
        if depth > 64 or count >= MAX_NOTE_FILES:
            return
        for name in sorted(os.listdir(directory)):
            if name.startswith(".") or count >= MAX_NOTE_FILES:
                continue
            relative = prefix + name
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, DIRECTORY_FLAGS, dir_fd=directory)
                    try:
                        walk(child, relative + "/", depth + 1)
                    finally:
                        os.close(child)
                elif name.lower().endswith(".md") and stat.S_ISREG(info.st_mode):
                    count += 1
                    fd = _open_note(directory, name, os.O_RDONLY)
                    try:
                        text = _read_bytes(fd).decode("utf-8", "replace")
                    finally:
                        os.close(fd)
                    hit = _hit(relative, text, terms)
                    if hit:
                        hits.append(hit)
            except (OSError, IntegrationError):
                continue
    with open_vault(config) as root:
        walk(root)
    hits.sort(key=lambda item: (-item[0], item[1]["path"]))
    return [item for _score, item in hits[:8]]


def read(config, relative):
    relative = relative_name(relative)
    try:
        with open_vault(config) as root, _parent(root, relative) as (parent, name):
            fd = _open_note(parent, name, os.O_RDONLY)
            try:
                text = _read_bytes(fd).decode("utf-8", "replace")
            finally:
                os.close(fd)
    except FileNotFoundError:
        raise IntegrationError("note-missing", f"There is no note {relative}.") from None
    except OSError:
        raise IntegrationError("invalid-note", "The note is unavailable or contains a symbolic link.") from None
    return relative, clip(text, MAX_NOTE_READ_CHARS)


def write(config, relative, text, mode, *, root_fd=None):
    if mode not in ("append", "create") or not isinstance(text, str) or not text.strip() or len(text) > MAX_WRITE_CHARS:
        raise IntegrationError("invalid-note", "Give the text to add, up to 20,000 characters.")
    relative = relative_name(relative)
    if root_fd is None:
        with open_vault(config) as root:
            return write(config, relative, text, mode, root_fd=root)
    try:
        with _parent(root_fd, relative, create=mode == "create") as (parent, name):
            if mode == "create":
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | FILE_FLAGS, 0o644, dir_fd=parent)
            else:
                fd = _open_note(parent, name, os.O_RDWR | os.O_APPEND)
            try:
                _regular(fd)
                existing = _read_bytes(fd) if mode == "append" else b""
                separator = b"" if existing.endswith(b"\n\n") or not existing else b"\n" if existing.endswith(b"\n") else b"\n\n"
                data = separator + (text.rstrip() + "\n").encode("utf-8")
                if len(existing) + len(data) > MAX_NOTE_BYTES:
                    raise IntegrationError("note-too-large", "That note would be too large to change.")
                remaining = memoryview(data)
                while remaining:
                    remaining = remaining[os.write(fd, remaining):]
                os.fsync(fd)
            finally:
                os.close(fd)
    except FileExistsError:
        raise IntegrationError("note-exists", f"{relative} already exists; add to it instead.") from None
    except FileNotFoundError:
        raise IntegrationError("note-missing", f"There is no note {relative}.") from None
    except OSError:
        raise IntegrationError("invalid-note", "The note is unavailable or contains a symbolic link.") from None
    return relative
