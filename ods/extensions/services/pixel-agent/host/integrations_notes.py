"""The owner's Obsidian vault for Portal: search, read and add to Markdown notes.

Every path stays inside the configured vault folder. Hidden folders (.obsidian, .trash)
and symbolic links are never followed, and only .md files are read or written.
"""
import os
import pathlib
import re

from integrations_common import IntegrationError, clip

MAX_NOTE_BYTES = 1024 * 1024
MAX_NOTE_FILES = 5000
MAX_NOTE_READ_CHARS = 20000
MAX_WRITE_CHARS = 20000


def vault(config):
    notes = config["notes"]
    if not notes["enabled"]:
        raise IntegrationError("notes-off", "Notes are not set up. The owner can connect an Obsidian vault in Settings → Portal integrations.")
    root = pathlib.Path(notes["vaultPath"])
    if not root.is_dir():
        raise IntegrationError("notes-unavailable", "The notes folder is not available.")
    return root.resolve()


def note_path(root, relative, *, must_exist):
    """The note's resolved path inside the vault and its vault-relative name."""
    if not isinstance(relative, str) or not relative.strip() or len(relative) > 512 or "\0" in relative:
        raise IntegrationError("invalid-note", "Give the note's path inside the vault, such as Projects/Plan.md.")
    relative = relative.strip().lstrip("/")
    if not relative.lower().endswith(".md"):
        relative += ".md"
    parts = pathlib.PurePosixPath(relative).parts
    if any(part in ("..", ".") or part.startswith(".") for part in parts):
        raise IntegrationError("invalid-note", "Notes outside the vault or in hidden folders cannot be used.")
    resolved = root.joinpath(*parts).resolve()
    if root not in resolved.parents:
        raise IntegrationError("invalid-note", "Notes outside the vault cannot be used.")
    if must_exist and not resolved.is_file():
        raise IntegrationError("note-missing", f"There is no note {relative}.")
    return resolved, relative


def _files(root):
    count = 0
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(name for name in folders if not name.startswith("."))
        for name in sorted(files):
            path = pathlib.Path(directory, name)
            if not name.lower().endswith(".md") or name.startswith(".") or path.is_symlink():
                continue
            count += 1
            if count > MAX_NOTE_FILES:
                return
            yield path


def _hit(root, path, terms):
    text = path.read_text(errors="replace")
    lowered, title = text.lower(), path.stem.lower()
    matched = [term for term in terms if term in lowered or term in title]
    if not matched:
        return None
    score = len(matched) * 100 + sum(lowered.count(term) for term in matched) + 20 * sum(term in title for term in matched)
    lines = text.splitlines() or [""]
    # The line with most query words; on a tie a sentence says more than a heading.
    best = max(range(len(lines)), key=lambda index: (sum(term in lines[index].lower() for term in terms),
                                                     not lines[index].lstrip().startswith("#")))
    return score, {"path": path.relative_to(root).as_posix(), "title": path.stem, "line": best + 1,
                   "snippet": lines[best].strip()[:240], "matched": len(matched), "of": len(terms)}


def search(config, query):
    """Up to eight notes holding the most query words, the best line of each first."""
    root = vault(config)
    terms = [term for term in re.findall(r"\w+", query.lower()) if len(term) > 1][:12]
    if not terms:
        raise IntegrationError("invalid-query", "Give one or more words to search for.")
    hits = [hit for path in _files(root) if path.stat().st_size <= MAX_NOTE_BYTES
            for hit in [_hit(root, path, terms)] if hit]
    hits.sort(key=lambda item: (-item[0], item[1]["path"]))
    return [item for _score, item in hits[:8]]


def read(config, relative):
    root = vault(config)
    path, relative = note_path(root, relative, must_exist=True)
    if path.stat().st_size > MAX_NOTE_BYTES:
        raise IntegrationError("note-too-large", "That note is too large to read.")
    return relative, clip(path.read_text(errors="replace"), MAX_NOTE_READ_CHARS)


def write(config, relative, text, mode):
    """Append to an existing note or create a new one; never overwrite."""
    root = vault(config)
    if mode not in ("append", "create") or not isinstance(text, str) or not text.strip() or len(text) > MAX_WRITE_CHARS:
        raise IntegrationError("invalid-note", "Give the text to add, up to 20,000 characters.")
    path, relative = note_path(root, relative, must_exist=mode == "append")
    if mode == "create":
        if path.exists():
            raise IntegrationError("note-exists", f"{relative} already exists; add to it instead.")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w") as stream:
            stream.write(text.rstrip() + "\n")
        return relative
    existing = path.read_text(errors="replace")
    separator = "" if existing.endswith("\n\n") or not existing else "\n" if existing.endswith("\n") else "\n\n"
    with path.open("a") as stream:
        stream.write(separator + text.rstrip() + "\n")
    return relative
