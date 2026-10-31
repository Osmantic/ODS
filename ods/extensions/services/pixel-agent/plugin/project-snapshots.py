"""Portal's project copies and restores, done through folder descriptors.

plugin/project-snapshots.mjs runs this file as a child process of the gateway
(`python3 -I -S -B project-snapshots.py`), writes one JSON request to its
stdin and reads one JSON answer from its stdout.

The project folder and every folder below it are opened relative to an open
folder descriptor with O_NOFOLLOW|O_DIRECTORY, every file with O_NOFOLLOW, and
every change uses the *at() calls on those descriptors (mkdirat, unlinkat,
renameat). A folder swapped for a link while a copy or a restore runs is
never followed, so nothing outside the project folder is read, written or
removed; a process that moves the project folder itself only moves where the
files land, still inside the workspace. This needs POSIX: elsewhere every
request answers `unsupported`.

Copies live in the gateway's private state folder, which the agent's sandbox
never mounts and no preview publishes. One store per workspace and project:

    <root>/<sha256 of workspace and project>/
        project.json            the workspace and project the store belongs to
        objects/<sha256>        each file content, stored once
        lists/<sha256>          each listing of the project, by content
        copies/<ms>-<id>.json   one per kept copy (a turn's, or a restore's undo)
        restore.json            the latest restore, for its undo and retries (pending
                                until it finished changing the project)
        pruned.json             ids of copies no longer kept
        missing.json            when a collection first found the project missing

The gateway names the runs it still runs (live): their copies are never
dropped, and any other copy without an end (a run of a gateway that
restarted) is ended at the next request, as the project is then.

A copy lists every entry first, then copies code and small files first within
the caps; what it leaves out (links, hard-linked and special files, bulk
folders such as node_modules and every dot-folder, large files, anything past
the caps) is listed with its stat, never followed or copied.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import time
import uuid

try:
    import fcntl
except ImportError:  # Not POSIX: main() answers unsupported before any lock.
    fcntl = None

LIMITS = {
    "fileBytes": 8 * 1024 * 1024,     # a larger file is listed, not copied
    "totalBytes": 32 * 1024 * 1024,   # bytes one copy holds
    "files": 2048,                    # files one copy holds
    "entries": 20000,                 # entries one listing visits
    "depth": 16,                      # folder levels below the project
    "turns": 5,                       # turn copies kept per project
    "storeBytes": 512 * 1024 * 1024,  # file contents kept for all projects
    "projects": 64,                   # projects fingerprinted per response
    "fingerprintEntries": 50000,      # entries a response's fingerprint visits
    "missingDays": 14,                # a missing project's copies are kept this long
}
# Folders a copy lists but never enters, as project_snapshot.py skips them;
# every dot-folder (.git, .venv, .cache ...) is skipped too.
BULK = frozenset({"node_modules", "dist", "out", "ods-builds", "venv", "__pycache__"})
# Copied first when a project holds more than one copy can.
CODE = frozenset({"js", "mjs", "cjs", "jsx", "ts", "tsx", "html", "htm", "css", "scss", "json", "md", "txt",
                  "py", "sh", "svg", "xml", "yml", "yaml", "toml", "csv", "vue", "svelte", "rb", "go", "rs",
                  "java", "kt", "c", "h", "cc", "cpp", "hpp", "cs", "php", "lua", "sql", "ini", "cfg"})
LEFTOVER = ".ods-restore-"
ID = re.compile(r"(?:chatcmpl|restore)_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", re.IGNORECASE)
SHA = re.compile(r"[a-f0-9]{64}")
RECORD = re.compile(r"(\d{15})-((?:chatcmpl|restore)_[0-9a-f-]{36})\.json")
LISTED = 8
DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
FILE = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


class HelperError(Exception):
    """A refusal with a code the plugin turns into the owner's words."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def supported():
    return (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and fcntl is not None
            and all(call in os.supports_dir_fd for call in (os.open, os.stat, os.unlink, os.rmdir, os.mkdir, os.rename))
            and os.listdir in os.supports_fd)


def identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def visible(name):
    """A name a listing holds: UTF-8, no backslash and no control character (C0, DEL or C1, as every relay
    of a receipt refuses them), and not a restore's temporary."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return (bool(name) and not name.startswith(LEFTOVER) and "\\" not in name
            and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name))


def stored_path(path):
    """A path read back from the store: relative, with no empty, '.' or '..' part, every part a visible name."""
    return (isinstance(path, str) and 0 < len(path) <= 4096
            and all(part not in (".", "..") and visible(part) for part in path.split("/")))


def valid_list(doc):
    """A listing read back from the store holds only paths below its project and content hashes."""
    try:
        return (isinstance(doc, dict) and isinstance(doc["complete"], bool)
                and all(stored_path(path) for path in doc["dirs"])
                and all(stored_path(item[0]) and isinstance(item[1], str) and SHA.fullmatch(item[1]) is not None
                        and type(item[2]) is int and type(item[3]) is int for item in doc["files"])
                and all(stored_path(item[0]) for item in doc["skipped"]))
    except (KeyError, TypeError, IndexError):
        return False


def now_ms():
    return int(time.time() * 1000)


def record_ms(name):
    """When a kept copy was made, from its record's file name."""
    return int(name[:15])


def project_name(value):
    return (isinstance(value, str) and PROJECT.fullmatch(value) is not None and not value.endswith(".")
            and RESERVED.fullmatch(value) is None)


def depth(path):
    return path.count("/") + 1


def ancestors(path):
    parts = path.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------ folders --

class Folder:
    """One open folder. Every name is looked up in it with *at() calls, never through a link."""

    def __init__(self, fd):
        self.fd = fd

    def close(self):
        os.close(self.fd)

    def info(self):
        return os.fstat(self.fd)

    def names(self):
        return os.listdir(self.fd)

    def lstat(self, name):
        try:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def child(self, name):
        return Folder(os.open(name, DIRECTORY, dir_fd=self.fd))

    def make(self, name, mode=0o777):
        os.mkdir(name, mode, dir_fd=self.fd)

    def read(self, name, expected, limit):
        """The bytes of the one-link regular file `expected` describes, or None when it is no longer that file."""
        fd = os.open(name, FILE, dir_fd=self.fd)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > limit
                    or identity(opened) != identity(expected)):
                return None
            chunks, size = [], 0
            while size <= opened.st_size:
                chunk = os.read(fd, min(1 << 20, opened.st_size + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size != opened.st_size or identity(os.fstat(fd)) != identity(opened):
                return None
            return b"".join(chunks)
        finally:
            os.close(fd)

    def write(self, name, data, mode, durable=True, prefix=LEFTOVER):
        """data written beside `name` and renamed over it, so the name holds the old file or the new one.

        Not durable: no fsync, for store contents whose hash is checked before
        use. The temporary's name starts with prefix (a restore's own).
        """
        temporary = prefix + secrets.token_hex(8)
        fd = os.open(temporary, CREATE, 0o600, dir_fd=self.fd)
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fchmod(fd, mode)
                if durable:
                    os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass
            raise

    def unlink(self, name):
        os.unlink(name, dir_fd=self.fd)

    def rmdir(self, name):
        os.rmdir(name, dir_fd=self.fd)

    def sync(self):
        os.fsync(self.fd)


def open_root(path):
    """A trusted absolute folder (the canonical workspace or the state folder), itself never a link."""
    return Folder(os.open(path, DIRECTORY))


def folder_at(top, relative, expected=None, create=False):
    """The folder at `relative` below `top`, opened one name at a time without following a link.

    With expected [dev, ino], None when it is no longer that folder. With
    create, missing folders are made. The caller closes it unless it is top.
    """
    folder = top
    try:
        for name in relative.split("/") if relative else []:
            try:
                child = folder.child(name)
            except FileNotFoundError:
                if not create:
                    raise
                folder.make(name)
                child = folder.child(name)
            if folder is not top:
                folder.close()
            folder = child
        if expected is not None and folder is not top:
            info = folder.info()
            if [info.st_dev, info.st_ino] != list(expected):
                folder.close()
                return None
        return folder
    except BaseException:
        if folder is not top:
            folder.close()
        raise


# ----------------------------------------------------------------- listings --

class Listing:
    """Every visible entry below a project folder: folders, files and what a copy leaves out."""

    def __init__(self):
        self.dirs = {}      # relative path -> [dev, ino]
        self.files = {}     # relative path -> stat of a one-link regular file
        self.skipped = {}   # relative path -> (reason, identity or None)
        self.complete = True
        self.newest = 0     # the latest modification or change time of any entry, in ns


def walk(project, limits):
    """Lists the open project folder, breadth first and by name. Links and bulk folders are listed, never entered."""
    listing, count, queue = Listing(), 0, [""]
    while queue:
        prefix = queue.pop(0)
        try:
            folder = folder_at(project, prefix, listing.dirs[prefix]) if prefix else project
        except OSError:
            folder = None
        try:
            names = sorted(filter(visible, folder.names())) if folder is not None else None
        except OSError:  # a folder this process may not list
            names = None
        if names is None:
            if folder is not None and folder is not project:
                folder.close()
            if not prefix:
                raise HelperError("folder-unusable")
            del listing.dirs[prefix]  # no longer the folder that was listed, or unreadable: left as it is
            listing.skipped[prefix] = ("other", None)
            continue
        try:
            for name in names:
                count += 1
                if count > limits["entries"]:
                    listing.complete = False
                    return listing
                path = f"{prefix}/{name}" if prefix else name
                info = folder.lstat(name)
                if info is None:
                    continue
                listing.newest = max(listing.newest, info.st_mtime_ns, info.st_ctime_ns)
                if stat.S_ISLNK(info.st_mode):
                    listing.skipped[path] = ("link", None)
                elif stat.S_ISDIR(info.st_mode):
                    if name.startswith(".") or name in BULK or depth(path) >= limits["depth"]:
                        listing.skipped[path] = ("bulk", None)
                    else:
                        listing.dirs[path] = [info.st_dev, info.st_ino]
                        queue.append(path)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    listing.files[path] = info
                else:
                    listing.skipped[path] = ("other", identity(info) if stat.S_ISREG(info.st_mode) else None)
        finally:
            if folder is not project:
                folder.close()
    return listing


def fingerprint(listing):
    """A project's state at the level of stat: any entry's kind, size, times or inode changes it.

    Taken from a fresh walk, before admission, wherever it is compared.
    """
    files = [[path, info.st_mode & 0o170777] + identity(info) for path, info in sorted(listing.files.items())]
    return sha256(canonical({"complete": listing.complete, "dirs": sorted(listing.dirs.items()), "files": files,
                             "skipped": sorted([path, item[0], item[1]] for path, item in listing.skipped.items())}))


def extension(path):
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def admit(listing, limits):
    """The files a copy holds: code and small files first, within the caps. The rest are listed as left out."""
    chosen, total = [], 0
    for path, info in sorted(listing.files.items(),
                             key=lambda item: (extension(item[0]) not in CODE, item[1].st_size, item[0])):
        if info.st_size > limits["fileBytes"]:
            listing.skipped[path] = ("large", identity(info))
        elif len(chosen) >= limits["files"] or total + info.st_size > limits["totalBytes"]:
            listing.skipped[path] = ("limit", identity(info))
        else:
            chosen.append(path)
            total += info.st_size
    return chosen


def capture(project, store, listing, chosen, limits, reuse, keep=True):
    """Each chosen file's content hash, reusing a known hash while the file's stat is unchanged.

    With keep, contents are stored once in the store's objects. A file that
    changed or cannot be read meanwhile is listed as left out instead.
    """
    files, by_folder = {}, {}
    for path in chosen:
        by_folder.setdefault(path.rsplit("/", 1)[0] if "/" in path else "", []).append(path)
    for parent, paths in sorted(by_folder.items()):
        try:
            folder = folder_at(project, parent, listing.dirs[parent]) if parent else project
        except OSError:
            folder = None
        try:
            for path in paths:
                info = listing.files[path]
                known = reuse.get(path)
                if known and known[2] == identity(info) and (not keep or store.has_object(known[0])):
                    files[path] = [known[0], info.st_size, known[1], identity(info)]
                    continue
                try:
                    data = folder.read(path.rsplit("/", 1)[-1], info, limits["fileBytes"]) if folder else None
                except OSError:
                    data = None
                if data is None:
                    listing.skipped[path] = ("other", identity(info))
                    continue
                digest = sha256(data)
                if keep:
                    store.put_object(digest, data)
                files[path] = [digest, len(data), info.st_mode & 0o777, identity(info)]
        finally:
            if folder is not None and folder is not project:
                folder.close()
    return files


def document(listing, files):
    return {"schemaVersion": 2, "complete": listing.complete, "dirs": sorted(listing.dirs),
            "files": [[path] + record for path, record in sorted(files.items())],
            "skipped": sorted([path, item[0], item[1]] for path, item in listing.skipped.items())}


def content(doc):
    """What a listing says about the project's content, without the stat of copied files."""
    return (list(doc["dirs"]), [[item[0], item[1], item[3]] for item in doc["files"]],
            [[item[0], item[1], item[2] if item[1] in ("large", "limit", "other") else None] for item in doc["skipped"]])


def token(doc):
    return sha256(canonical(content(doc)))


# ----------------------------------------------------------------- the plan --

def coverage(skipped):
    paths = {item[0] for item in skipped}
    return lambda path: path in paths or any(parent in paths for parent in ancestors(path))


def current_identity(doc, path):
    for item in doc["files"]:
        if item[0] == path:
            return item[4]
    for item in doc["skipped"]:
        if item[0] == path:
            return "link" if item[1] == "link" else item[2]
    return None


def plan(saved, current):
    """What a restore changes so the project matches the saved copy.

    Files that differ or are gone are written back and, when the saved copy
    listed everything, files and folders it did not have are removed. A path
    either copy left out is never written or removed, and when the copy just
    taken of the project as it is stopped early, only files that copy holds
    are written: a restore changes nothing its undo cannot put back. `left`
    names what may differ from the saved copy and stays as it is.
    """
    saved_files = {item[0]: item for item in saved["files"]}
    now_files = {item[0]: item for item in current["files"]}
    kept_before, kept_now = coverage(saved["skipped"]), coverage(current["skipped"])
    write, left, remove, remove_dirs, create_dirs = [], [], [], [], []
    for path, item in saved_files.items():
        now = now_files.get(path)
        if now and now[1] == item[1] and now[3] == item[3]:
            continue
        if kept_now(path) or not (current["complete"] or now):
            if current_identity(current, path) != item[4]:  # unchanged when its stat is the saved one
                left.append(path)
        else:
            write.append(item)
    for path, reason, recorded in saved["skipped"]:
        unknown = reason == "other" and recorded is None  # a folder or special file no copy could read
        if reason != "bulk" and (unknown or current_identity(current, path) != ("link" if reason == "link" else recorded)):
            left.append(path)
    if saved["complete"]:
        for path in now_files:
            if path not in saved_files and not kept_before(path):
                remove.append(path)
        for path, reason, _ in current["skipped"]:
            if reason != "bulk" and path not in saved_files and not kept_before(path):
                left.append(path)
        saved_dirs = set(saved["dirs"])
        remove_dirs = sorted((path for path in current["dirs"] if path not in saved_dirs and not kept_before(path)),
                             key=lambda path: (-depth(path), path))
    if current["complete"]:
        now_dirs = set(current["dirs"])
        create_dirs = sorted((path for path in saved["dirs"] if path not in now_dirs and not kept_now(path)),
                             key=lambda path: (depth(path), path))
    return {"write": write, "remove": remove, "removeDirs": remove_dirs, "createDirs": create_dirs,
            "left": sorted(set(left))}


def changed_paths(current, base):
    """Paths whose content or kind differs between two listings."""
    def view(doc):
        entries = {item[0]: ("file", item[1], item[3]) for item in doc["files"]}
        entries.update({item[0]: (item[1], item[2]) for item in doc["skipped"] if item[1] != "bulk"})
        return entries
    now, then = view(current), view(base)
    return {path for path in set(now) | set(then) if now.get(path) != then.get(path)}


def classify(error):
    if isinstance(error, HelperError):
        return "changed"
    if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "permission"
    if error.errno in (errno.EBUSY, getattr(errno, "ETXTBSY", errno.EBUSY)):
        return "in-use"
    if error.errno in (errno.ELOOP, errno.ENOTDIR, errno.ENOENT, errno.EEXIST, errno.ENOTEMPTY, errno.EISDIR):
        return "changed"
    return "other"


def temporaries(undo):
    """The start of the temporary names one restore writes in the project, the same on its retry."""
    return f"{LEFTOVER}{undo[len('restore_'):][:8]}-"


def apply_plan(project, steps, objects, undo):
    """Applies a plan below the open project folder, through descriptors; returns what it did.

    Its temporaries are named for the restore (see temporaries()), so one a
    killed attempt left behind is removed by the retry, and nothing else is.
    """
    done = {"written": 0, "removed": 0, "failed": [], "failure": None}
    touched, prefix = {}, temporaries(undo)

    def fail(path, error):
        done["failed"].append(path)
        done["failure"] = done["failure"] or classify(error)

    def parent_of(path, create=False):
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if parent not in touched:
            touched[parent] = folder_at(project, parent, create=create)
        return touched[parent], path.rsplit("/", 1)[-1]

    try:
        for path in steps["remove"]:
            try:
                folder, name = parent_of(path)
                info = folder.lstat(name)
                if info is not None and not stat.S_ISREG(info.st_mode):
                    raise HelperError("changed")
                if info is not None:
                    folder.unlink(name)
                    done["removed"] += 1
            except (OSError, HelperError) as error:
                fail(path, error)
        for path in steps["removeDirs"]:
            try:
                folder, name = parent_of(path)
                info = folder.lstat(name)
                if info is not None and stat.S_ISDIR(info.st_mode):
                    folder.rmdir(name)
            except OSError:
                pass  # It still holds what the restore leaves as it is.
        for path in steps["createDirs"]:
            try:
                parent_of(path + "/-", create=True)
            except OSError as error:
                fail(path, error)
        for item in steps["write"]:
            try:
                folder, name = parent_of(item[0], create=True)
                info = folder.lstat(name)
                if info is not None and stat.S_ISDIR(info.st_mode):
                    folder.rmdir(name)
                elif info is not None and not stat.S_ISREG(info.st_mode):
                    raise HelperError("changed")
                folder.write(name, objects[item[1]], item[3] & 0o777, prefix=prefix)
                done["written"] += 1
            except (OSError, HelperError) as error:
                fail(item[0], error)
        for folder in touched.values():
            try:
                for name in folder.names():
                    info = folder.lstat(name) if name.startswith(prefix) else None
                    if info is not None and stat.S_ISREG(info.st_mode):
                        folder.unlink(name)  # left by this restore's attempt that was killed midway
                folder.sync()
            except OSError:
                pass  # A folder this restore removed has nothing left to tidy.
    finally:
        for folder in touched.values():
            if folder is not project:
                folder.close()
    return done


# -------------------------------------------------------------------- store --

def private(folder):
    if os.name == "posix":
        info = folder.info()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise HelperError("copies-unusable")
    return folder


def open_child(parent, name, create, mode=0o700):
    try:
        return parent.child(name)
    except FileNotFoundError:
        if not create:
            return None
        parent.make(name, mode)
        return parent.child(name)


def write_json(folder, name, value):
    folder.write(name, canonical(value), 0o600)
    folder.sync()


def read_json(folder, name, limit=8 * 1024 * 1024):
    info = folder.lstat(name)
    if info is None:
        return None
    data = folder.read(name, info, limit) if stat.S_ISREG(info.st_mode) else None
    if data is None:
        raise HelperError("damaged")
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        raise HelperError("damaged") from None


def remove_tree(parent, name):
    """Removes a store folder and everything in it, never following a link."""
    folder = parent.child(name)
    try:
        for entry in folder.names():
            info = folder.lstat(entry)
            if info is not None and stat.S_ISDIR(info.st_mode):
                remove_tree(folder, entry)
            elif info is not None:
                folder.unlink(entry)
    finally:
        folder.close()
    parent.rmdir(name)


def store_key(workspace, name):
    return sha256(f"{workspace}\n{name}".encode("utf-8"))


class Store:
    """One workspace project's copies in the private state folder; folder is None when there are none."""

    def __init__(self, root, workspace, name, create):
        self.folder = open_child(root, store_key(workspace, name), create) if root is not None else None
        if self.folder is None:
            return
        try:
            private(self.folder)
            self.objects = open_child(self.folder, "objects", True)
            self.lists = open_child(self.folder, "lists", True)
            self.copies = open_child(self.folder, "copies", True)
        except BaseException:
            self.folder.close()
            raise
        if create and self.folder.lstat("project.json") is None:
            write_json(self.folder, "project.json", {"schemaVersion": 1, "workspace": workspace,
                                                     "project": f"Playground/{name}"})

    def close(self):
        if self.folder is not None:
            for folder in (self.objects, self.lists, self.copies, self.folder):
                folder.close()

    def has_object(self, digest):
        return isinstance(digest, str) and SHA.fullmatch(digest) is not None and self.objects.lstat(digest) is not None

    def put_object(self, digest, data):
        if not self.has_object(digest):
            self.objects.write(digest, data, 0o600, durable=False)  # get_object checks the hash

    def get_object(self, digest, size):
        info = self.objects.lstat(digest) if self.has_object(digest) else None
        data = self.objects.read(digest, info, size) if info is not None and info.st_size == size else None
        if data is None or sha256(data) != digest:
            raise HelperError("damaged")
        return data

    def put_list(self, doc):
        data = canonical(doc)
        digest = sha256(data)
        if self.lists.lstat(digest) is None:
            self.lists.write(digest, data, 0o600, durable=False)  # get_list checks the hash
        return digest

    def get_list(self, digest):
        info = self.lists.lstat(digest) if isinstance(digest, str) and SHA.fullmatch(digest) else None
        data = self.lists.read(digest, info, 64 * 1024 * 1024) if info is not None else None
        if data is None or sha256(data) != digest:
            raise HelperError("damaged")
        try:
            doc = json.loads(data.decode("utf-8"))
        except ValueError:
            raise HelperError("damaged") from None
        if not valid_list(doc):
            raise HelperError("damaged")
        return doc

    def records(self):
        """The kept copies, newest first, as (file name, record). An unreadable record is no copy."""
        found = []
        for name in self.copies.names():
            match = RECORD.fullmatch(name)
            try:
                record = read_json(self.copies, name) if match else None
            except HelperError:
                record = None
            if isinstance(record, dict) and record.get("id") == match.group(2):
                found.append((name, record))
        return sorted(found, key=lambda item: item[0], reverse=True)

    def record(self, snapshot):
        return next((item for item in self.records() if item[1]["id"] == snapshot), None)

    def put_record(self, record, name=None):
        write_json(self.copies, name or f"{int(time.time() * 1000):015d}-{record['id']}.json", record)

    def drop(self, name, record):
        self.copies.unlink(name)
        pruned = (read_json(self.folder, "pruned.json") or {}).get("ids", [])
        write_json(self.folder, "pruned.json", {"ids": (pruned + [record["id"]])[-256:]})

    def pruned(self, snapshot):
        return snapshot in (read_json(self.folder, "pruned.json") or {}).get("ids", [])

    def state(self):
        return read_json(self.folder, "restore.json")

    def object_bytes(self):
        total = 0
        for name in self.objects.names():
            info = self.objects.lstat(name)
            total += info.st_size if info is not None else 0
        return total

    def collect(self):
        """Removes listings and contents no kept copy needs."""
        records, state = self.records(), self.state()
        lists = {record["list"] for _, record in records}
        lists |= {record["after"]["list"] for _, record in records if record.get("after")}
        if state and SHA.fullmatch(str(state.get("result"))):
            lists.add(state["result"])
        objects = set()
        for _, record in records:
            try:
                objects |= {item[1] for item in self.get_list(record["list"])["files"]}
            except HelperError:
                pass  # A damaged copy cannot be put back; it keeps no contents alive.
        for folder, keep in ((self.lists, lists), (self.objects, objects)):
            for name in folder.names():
                if name not in keep:
                    folder.unlink(name)

    def prune(self, limits, live=frozenset()):
        """Keeps the newest turn copies that changed their project, and the latest restore's copies.

        Of restore copies, the latest restore's undo and the copy it put back
        (for a retry) are kept. A turn that changed nothing keeps its copy
        only while it is the newest. The copy of a live run is never dropped:
        its response may still change the project.
        """
        state, turns = self.state(), 0
        keep = {state.get("undo"), state.get("from")} if state else set()
        for index, (name, record) in enumerate(self.records()):
            after = record.get("after")
            if record["kind"] == "restore":
                if record["id"] not in keep:
                    self.drop(name, record)
            elif after and not after["changed"]:
                if index and record["id"] not in live:
                    self.drop(name, record)
            else:
                turns += 1
                if turns > limits["turns"] and record["id"] not in live:
                    self.drop(name, record)
        self.collect()

    def latest(self):
        """The newest turn copy that changed its project (or may still), or None."""
        return next((record for _, record in self.records() if record["kind"] == "turn"
                     and (not record.get("after") or record["after"]["changed"])), None)

    def protected(self, live):
        """The copies the size cap never drops: the latest turn's that offers Put back, the latest
        restore's, and those of live runs."""
        state, latest = self.state(), self.latest()
        keep = {state.get("undo"), state.get("from")} if state else set()
        return keep | ({latest["id"]} if latest else set()) | set(live)

    def settle(self, current, live):
        """Records the end of each turn copy whose run is not live in the gateway (it restarted, or the
        run ended without its end being recorded), as the project is now.

        current() lists the project now. A copy settled so is never put back
        without the owner's yes: changes since its run may be anyone's.
        """
        if live is None:
            return
        dead = [(name, record) for name, record in self.records()
                if record["kind"] == "turn" and not record.get("after") and record["id"] not in live]
        if not dead:
            return
        try:
            doc = current()
        except (OSError, HelperError):
            return  # A project that cannot be listed now: its copies wait for the next request.
        for name, record in dead:
            try:
                changed = bool(record["late"]) or content(doc) != content(self.get_list(record["list"]))
            except HelperError:
                changed = True  # A damaged copy: putting it back is refused anyway.
            self.put_record({**record, "after": {"list": self.put_list(doc), "changed": changed, "recordedAt": now(),
                                                 "endedMs": now_ms(), "settled": True}}, name)

    def shared(self, found):
        """Whether another turn that changed the project, or still may, ran while this turn did.

        A turn's window runs from its copy to its recorded end (to now while
        it has none). Putting back a copy whose window overlapped another
        changing turn would undo that turn's work too.
        """
        name, record = found
        start, after, moment = record_ms(name), record.get("after"), now_ms()
        end = after.get("endedMs", start) if after else moment
        for other_name, other in self.records():
            other_after = other.get("after")
            if other_name == name or other["kind"] != "turn" or other_after and not other_after["changed"]:
                continue
            other_start = record_ms(other_name)
            other_end = other_after.get("endedMs", other_start) if other_after else moment
            if other_start < end and start < other_end:
                return True
        return False


def known_hashes(store):
    """Known content hashes by path from the store's newest listings, for files whose stat is unchanged."""
    known = {}
    for _, record in store.records()[:2]:
        for digest in ([record["after"]["list"]] if record.get("after") else []) + [record["list"]]:
            for item in store.get_list(digest)["files"]:
                known.setdefault(item[0], (item[1], item[3], item[4]))
    return known


def listed_now(project, store, limits):
    """A listing document of the project as it is now (an empty one when it is missing), without storing contents."""
    listing = walk(project, limits) if project is not None else Listing()
    chosen = admit(listing, limits)
    return document(listing, capture(project, store, listing, chosen, limits, known_hashes(store), keep=False))


class Session:
    """The open workspace, Playground, project and store of one request, under the store lock."""

    def __init__(self, request, opener, create):
        self.limits, self.workspace_path, self.opened = request["limits"], request["workspace"], []
        self.name, self.request_id, self.root, self.store = request.get("project"), request.get("id"), None, None
        # The runs the gateway still runs: their copies may still change and are never dropped.
        # None when the request does not say (then no copy's end is recorded for it).
        self.live = frozenset(request["live"]) if "live" in request else None
        # A session refused while opening releases what it opened, the store
        # lock with it: the request's next project takes that lock.
        try:
            self.open(request, opener, create)
        except BaseException:
            self.close()
            raise

    def open(self, request, opener, create):
        try:
            self.workspace = self.keep(opener(request["workspace"]))
        except OSError:
            raise HelperError("unavailable") from None
        try:
            try:
                self.root = self.keep(opener(request["root"]))
            except FileNotFoundError:
                if create:
                    os.mkdir(request["root"], 0o700)
                    self.root = self.keep(opener(request["root"]))
            if self.root is not None:
                private(self.root)
        except OSError:
            raise HelperError("copies-unusable") from None
        if self.root is not None and fcntl is not None:
            lock = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.root.fd)
            self.opened.append(Folder(lock))
            fcntl.flock(lock, fcntl.LOCK_EX)
        self.playground = self.project = None
        try:
            self.playground = self.keep(self.workspace.child("Playground"))
            if self.name is not None:
                self.project = self.keep(self.playground.child(self.name))
        except FileNotFoundError:
            pass
        except OSError:  # a link, a file, or a folder this process may not open, where the folder belongs
            raise HelperError("folder-unusable") from None
        if self.name is not None:
            self.store = Store(self.root, self.workspace_path, self.name, create)

    def keep(self, folder):
        self.opened.append(folder)
        return folder

    def close(self):
        if self.store is not None:
            self.store.close()
        for folder in reversed(self.opened):
            folder.close()

    def listing(self):
        return walk(self.project, self.limits) if self.project is not None else Listing()

    def protected_live(self):
        return self.live or frozenset()

    def ensure_project(self):
        """Playground and the project folder, made again when a turn removed them."""
        if self.playground is None:
            self.workspace.make("Playground", 0o755)
            self.playground = self.keep(self.workspace.child("Playground"))
        if self.project is None:
            self.playground.make(self.name, 0o755)
            self.project = self.keep(self.playground.child(self.name))


# --------------------------------------------------------------- operations --

def op_fingerprint(request, opener):
    """Stat-level fingerprints of the Playground projects, before a response's first change.

    With each, when anything in the project last changed (newest, in ms):
    a Playground-wide mention copies the projects changed most recently.
    """
    session = Session({**request, "project": None}, opener, True)
    try:
        found, newest, budget, complete = {}, {}, request["limits"]["fingerprintEntries"], True
        names = sorted(name for name in (session.playground.names() if session.playground else []) if project_name(name))
        for name in names:
            info = session.playground.lstat(name)
            if info is None or not stat.S_ISDIR(info.st_mode):
                continue
            if len(found) >= request["limits"]["projects"] or budget <= 0:
                complete = False
                break
            try:
                folder = session.playground.child(name)
            except OSError:
                continue  # swapped for a link or removed meanwhile: no project
            try:
                listing = walk(folder, request["limits"])
            except HelperError:  # a project folder this process may not list: no fingerprint
                complete = False
                continue
            finally:
                folder.close()
            budget -= len(listing.files) + len(listing.dirs) + len(listing.skipped)
            found[name] = fingerprint(listing)
            newest[name] = max(listing.newest, info.st_mtime_ns, info.st_ctime_ns) // 1_000_000
        return {"projects": found, "newest": newest, "complete": complete}
    finally:
        session.close()


def each_project(request, opener, create, work):
    """work(session, entry) for each requested project, one session (and lock) at a time.

    A project's refusal is its own answer; it does not stop the others.
    """
    answers = {}
    for entry in request["projects"]:
        try:
            session = Session({**request, "project": entry["name"]}, opener, create)
            try:
                answers[entry["name"]] = work(session, entry)
            finally:
                session.close()
        except HelperError as error:
            answers[entry["name"]] = {"error": error.code}
        except OSError:
            answers[entry["name"]] = {"error": "unavailable"}
    return {"projects": answers}


def copy_one(session, entry):
    store = session.store
    if store.record(session.request_id) is not None:
        return {"status": "exists"}
    listing = session.listing()
    stamp, start = fingerprint(listing), entry.get("start")
    # The project changed after the response's first change, before this copy.
    late = start is not None and start != stamp and not (start == "absent" and not listing.files and not listing.dirs)
    chosen = admit(listing, session.limits)
    doc = document(listing, capture(session.project, store, listing, chosen, session.limits, known_hashes(store)))
    store.settle(lambda: doc, session.live)
    store.put_record({"schemaVersion": 2, "id": session.request_id, "kind": "turn", "createdAt": now(),
                      "list": store.put_list(doc), "fingerprint": stamp, "late": late, "after": None})
    store.prune(session.limits, session.protected_live())
    return {"status": "kept", "late": late}


def op_copy(request, opener):
    """Keeps one turn's copy of each project, once."""
    answer = each_project(request, opener, True, copy_one)
    if request.get("collect"):
        session = Session({**request, "project": None}, opener, True)
        try:
            collect_all(session)
        except (HelperError, OSError):
            pass  # A collection that fails leaves the copies for the next one; the copies above stand.
        finally:
            session.close()
    return answer


def after_one(session, entry):
    """Records the project as the turn left it, and whether the turn changed it."""
    store = session.store
    found = store.record(session.request_id) if store.folder is not None else None
    if found is None:
        return {"status": "none"}
    name, record = found
    doc = listed_now(session.project, store, session.limits)
    changed = bool(record["late"]) or content(doc) != content(store.get_list(record["list"]))
    store.put_record({**record, "after": {"list": store.put_list(doc), "changed": changed, "recordedAt": now(),
                                          "endedMs": now_ms()}}, name)
    store.settle(lambda: doc, session.live)
    store.prune(session.limits, session.protected_live())
    return {"status": "changed" if changed else "unchanged"}


def op_after(request, opener):
    return each_project(request, opener, False, after_one)


def listed(paths):
    paths = sorted(paths)
    return [path for path in paths if len(path) <= 512][:LISTED], len(paths)


NOTHING = {"written": 0, "removed": 0, "failed": [], "failure": None}


def receipt(request, status, done, steps, late, undo, confirm=None):
    left, left_count = listed(steps["left"])
    failed, failed_count = listed(done["failed"])
    return {"schemaVersion": 2, "status": status, "project": f"Playground/{request['project']}",
            "snapshot": request["snapshot"], "written": done["written"], "removed": done["removed"],
            "left": left, "leftCount": left_count, "failed": failed, "failedCount": failed_count,
            "failure": done["failure"], "late": late, "undo": undo, "confirm": confirm}


def later_changes(store, state, found, current, saved, pending, finishing):
    """The paths changed since the copy's turn ended (or since the restore an undo reverses), or None when
    that is unknown and every overwrite or removal needs the owner's yes."""
    record = found[1]
    if pending:
        # A restore that stopped midway: what it had put back and what it had
        # not reached yet are its own; anything else changed since.
        undo = store.record(state["undo"])
        before = store.get_list(undo[1]["list"]) if undo else None
        return changed_paths(current, before) & changed_paths(current, saved) if before is not None else None
    if finishing:
        return changed_paths(current, store.get_list(state["result"])) if state.get("result") else None
    if record["kind"] == "turn":
        after = record.get("after")
        # Unknown when the turn's end was recorded late, or another changing turn ran meanwhile.
        if after and not after.get("settled") and not store.shared(found):
            return changed_paths(current, store.get_list(after["list"]))
        return None
    if state and state.get("undo") == record["id"] and state.get("result"):
        return changed_paths(current, store.get_list(state["result"]))
    return None


def op_restore(request, opener):
    """Puts one project back to a kept copy (see plan()); asks first before overwriting later changes.

    The undo copy and a pending restore.json are written before anything in
    the project changes: a restore that is killed midway is finished by its
    retry under the same undo, which still puts back the files as they were
    before it.
    """
    session = Session(request, opener, False)
    try:
        store, snapshot = session.store, request["snapshot"]
        if store.folder is None:
            raise HelperError("unavailable-copy")
        state = store.state()
        listing = session.listing()
        stamp = fingerprint(listing)
        # The latest restore from this copy: a retry gets the same answer
        # while nothing changed since; one that stopped midway, could not
        # change some files or could not list its result is finished under
        # the same undo.
        same = bool(state) and state.get("from") == snapshot
        pending = same and bool(state.get("pending"))
        unchanged_since = same and not pending and state.get("fingerprint") is not None and state["fingerprint"] == stamp
        if unchanged_since and not state["receipt"]["failedCount"]:
            return state["receipt"]
        finishing = pending or unchanged_since or same and state.get("fingerprint") is None
        found = store.record(snapshot)
        if found is None:
            raise HelperError("pruned" if store.pruned(snapshot) else "unavailable-copy")
        record = found[1]
        saved = store.get_list(record["list"])
        chosen = admit(listing, session.limits)
        current = document(listing, capture(session.project, store, listing, chosen, session.limits, known_hashes(store)))
        store.settle(lambda: current, session.live)
        steps = plan(saved, current)
        late = bool(record.get("late"))
        # Changes made after the copy's turn ended (or after the restore this
        # undoes) that the restore would overwrite or remove need the owner's yes.
        changed = later_changes(store, state, found, current, saved, pending, finishing)
        present = {item[0] for item in current["files"]}
        overwrite = [item[0] for item in steps["write"] if item[0] in present and (changed is None or item[0] in changed)]
        removing = [path for path in steps["remove"] if changed is None or path in changed]
        if (overwrite or removing) and request.get("confirm") != token(current):
            shown, shown_count = listed(overwrite)
            gone, gone_count = listed(removing)
            return receipt(request, "confirm", NOTHING, steps, late, None,
                           {"token": token(current), "overwrite": shown, "overwriteCount": shown_count,
                            "remove": gone, "removeCount": gone_count})
        keep_undo = finishing and isinstance(state.get("undo"), str) and store.record(state["undo"]) is not None
        work = steps["write"] or steps["remove"] or steps["removeDirs"] or steps["createDirs"]
        # Nothing left to change: a restore being finished answers as restored, with its undo.
        if not work and not keep_undo:
            return receipt(request, "unchanged", NOTHING, steps, late, None)
        undo = state["undo"] if keep_undo else f"restore_{uuid.uuid4()}"
        # Which version the project holds afterwards: the files from before
        # the turn, or the turn's own (after an Undo). Each restore's undo
        # flips it, so the owner can go back and forth. turn: the turn whose
        # Put back started this chain of restores.
        if keep_undo:
            side, turn = state.get("side", "before"), state.get("turn")
        else:
            side = "before" if record["kind"] == "turn" or (state or {}).get("side") == "after" else "after"
            turn = (snapshot if record["kind"] == "turn"
                    else (state or {}).get("turn") if (state or {}).get("undo") == snapshot else None)
        done = NOTHING
        if work:
            objects = {item[1]: store.get_object(item[1], item[2]) for item in steps["write"]}
            try:
                session.ensure_project()
            except OSError:
                raise HelperError("folder-unusable") from None
            if not keep_undo:
                store.put_record({"schemaVersion": 2, "id": undo, "kind": "restore", "createdAt": now(),
                                  "list": store.put_list(current), "fingerprint": stamp, "late": False, "after": None})
            write_json(store.folder, "restore.json", {"schemaVersion": 2, "from": snapshot, "undo": undo, "side": side,
                                                      "turn": turn, "pending": True, "fingerprint": None,
                                                      "result": None, "receipt": None})
            done = apply_plan(session.project, steps, objects, undo)
        return finish_restore(session, request, steps, current, done, late, undo, side, turn)
    finally:
        session.close()


def finish_restore(session, request, steps, current, done, late, undo, side, turn):
    """Records a restore that changed the project, and answers its receipt.

    Never a refusal: the project has changed. When the project cannot be
    listed now, its result is unknown, so a retry finishes under the same
    undo and the undo asks before overwriting anything.
    """
    store, answer, result, result_stamp = session.store, receipt(request, "restored", done, steps, late, undo), None, None
    try:
        listing = session.listing()
        result_stamp = fingerprint(listing)
        # Files this restore wrote are read again: their stat does not prove what they hold.
        written = {item[0] for item in steps["write"]}
        reuse = {item[0]: (item[1], item[3], item[4]) for item in current["files"] if item[0] not in written}
        files = capture(session.project, store, listing, admit(listing, session.limits), session.limits, reuse, keep=False)
        result = store.put_list(document(listing, files))
    except (OSError, HelperError):
        result_stamp = None
    write_json(store.folder, "restore.json", {"schemaVersion": 2, "from": request["snapshot"], "undo": undo, "side": side,
                                              "turn": turn, "pending": False, "fingerprint": result_stamp,
                                              "result": result, "receipt": answer})
    try:
        store.prune(session.limits, session.protected_live())
    except (OSError, HelperError):
        pass  # Old copies are dropped by the next copy instead.
    return answer


def project_now(session, store, name):
    """A function listing the project `name` of this session's workspace as it is now (empty when missing)."""
    def current():
        project = None
        try:
            if session.playground is not None and session.playground.lstat(name) is not None:
                project = session.playground.child(name)
            return listed_now(project, store, session.limits)
        finally:
            if project is not None:
                project.close()
    return current


def op_options(request, opener):
    """For each project whose latest kept turn copy is one of `runs`: that copy, and the latest restore's undo.

    The undo is offered while the latest restore belongs to that turn (it put
    back the turn's copy, or undid that, and so on), also when files changed
    since: putting files back then asks the owner first.
    """
    session = Session({**request, "project": None}, opener, False)
    try:
        entries, runs = [], set(request["runs"])
        for key in sorted(session.root.names() if session.root is not None else []):
            if not SHA.fullmatch(key) or len(entries) >= 32:
                continue
            folder = private(session.root.child(key))
            try:
                about = read_json(folder, "project.json") or {}
            finally:
                folder.close()
            name = str(about.get("project", "")).split("/", 1)[-1]
            if about.get("workspace") != request["workspace"] or not project_name(name):
                continue
            store = Store(session.root, request["workspace"], name, False)
            try:
                store.settle(project_now(session, store, name), session.live)
                latest = store.latest()
                if latest is None or latest["id"] not in runs:
                    continue
                entry = {"project": f"Playground/{name}", "snapshot": latest["id"], "late": bool(latest["late"]),
                         "restore": None}
                state = store.state()
                if state and state.get("turn") == latest["id"]:
                    if (not state.get("pending") and isinstance(state.get("undo"), str)
                            and store.record(state["undo"]) is not None):
                        entry["restore"] = {"undo": state["undo"], "side": state.get("side", "before")}
                    elif (state.get("pending") and state.get("from") != latest["id"]
                          and isinstance(state.get("from"), str) and store.record(state["from"]) is not None):
                        # An Undo (or a Put back again) that stopped midway: the next step finishes it.
                        entry["restore"] = {"undo": state["from"], "side": "before" if state.get("side") == "after" else "after"}
                entries.append(entry)
            finally:
                store.close()
        return {"projects": entries}
    finally:
        session.close()


def missing_too_long(store, present, limits):
    """Whether a store's project has been missing for limits["missingDays"], counted from when a collection
    first found it missing; missing.json keeps that time until the project is back."""
    missing = read_json(store.folder, "missing.json")
    if present:
        if missing is not None:
            store.folder.unlink("missing.json")
        return False
    since = missing.get("since") if isinstance(missing, dict) else None
    if not isinstance(since, (int, float)) or isinstance(since, bool):
        write_json(store.folder, "missing.json", {"schemaVersion": 1, "since": time.time()})
        return False
    return since < time.time() - limits["missingDays"] * 86400


def collect_all(session):
    """Across all stores: drops those of projects missing for a while, then the oldest copies over the size cap.

    The size cap never drops a project's latest copy that offers Put back,
    the latest restore's copies, or a live run's copy; when only those are
    left, they stay.
    """
    limits, kept = session.limits, []
    for key in sorted(session.root.names()):
        if not SHA.fullmatch(key):
            continue
        folder = private(session.root.child(key))
        try:
            about = read_json(folder, "project.json") or {}
        finally:
            folder.close()
        workspace, name = about.get("workspace"), str(about.get("project", "")).split("/", 1)[-1]
        if not isinstance(workspace, str) or not project_name(name) or store_key(workspace, name) != key:
            continue
        present = (workspace == session.workspace_path and session.playground is not None
                   and session.playground.lstat(name) is not None)
        store = Store(session.root, workspace, name, False)
        try:
            gone = missing_too_long(store, present, limits)
            if not gone:
                protected = store.protected(session.protected_live())
                # Oldest first: the copies the size cap may drop.
                spare = [entry for entry in reversed(store.records()) if entry[1]["id"] not in protected]
                kept.append([workspace, name, store.object_bytes(), spare])
        finally:
            store.close()
        if gone:
            remove_tree(session.root, key)
    total = sum(item[2] for item in kept)
    while total > limits["storeBytes"]:
        candidates = [item for item in kept if item[3]]
        if not candidates:
            break
        item = min(candidates, key=lambda entry: entry[3][0][0])
        store = Store(session.root, item[0], item[1], False)
        try:
            store.drop(*item[3].pop(0))
            store.collect()
            size = store.object_bytes()
        finally:
            store.close()
        total -= item[2] - size
        item[2] = size


OPERATIONS = {"fingerprint": op_fingerprint, "copy": op_copy, "after": op_after, "restore": op_restore,
              "options": op_options}


def validate(request):
    """The request with checked fields, and limits no larger than the defaults."""
    if not isinstance(request, dict) or request.get("operation") not in OPERATIONS:
        raise HelperError("invalid")
    for key in ("workspace", "root"):
        if not isinstance(request.get(key), str) or not os.path.isabs(request[key]) or "\0" in request[key]:
            raise HelperError("invalid")
    if "project" in request and not project_name(request["project"]):
        raise HelperError("invalid")
    for key in ("id", "snapshot"):
        if key in request and not (isinstance(request[key], str) and ID.fullmatch(request[key])):
            raise HelperError("invalid")
    confirm = request.get("confirm")
    if confirm is not None and not (isinstance(confirm, str) and SHA.fullmatch(confirm)):
        raise HelperError("invalid")
    projects = request.get("projects", [])
    if not isinstance(projects, list) or len(projects) > 64 or not all(
            isinstance(entry, dict) and set(entry) <= {"name", "start"} and project_name(entry.get("name"))
            and (entry.get("start") is None or isinstance(entry["start"], str)
                 and (SHA.fullmatch(entry["start"]) or entry["start"] == "absent")) for entry in projects):
        raise HelperError("invalid")
    if (request["operation"] in ("copy", "after") and ("id" not in request or not projects)
            or request["operation"] == "restore" and ("project" not in request or "snapshot" not in request)):
        raise HelperError("invalid")
    runs = request.get("runs", [])
    if not isinstance(runs, list) or len(runs) > 128 or not all(isinstance(run, str) and ID.fullmatch(run) for run in runs):
        raise HelperError("invalid")
    live = request.get("live", [])
    if not isinstance(live, list) or len(live) > 1024 or not all(isinstance(run, str) and ID.fullmatch(run) for run in live):
        raise HelperError("invalid")
    supplied = request.get("limits") or {}
    if not isinstance(supplied, dict) or any(key not in LIMITS or type(value) is not int or not 0 < value <= LIMITS[key]
                                             for key, value in supplied.items()):
        raise HelperError("invalid")
    return {**request, "limits": {**LIMITS, **supplied}}


def handle(request, opener=open_root):
    """One request's answer: the operation's result, or {"error": code}."""
    try:
        request = validate(request)
        return OPERATIONS[request["operation"]](request, opener)
    except HelperError as error:
        return {"error": error.code}


def main():
    if not supported():
        answer = {"error": "unsupported"}
    else:
        try:
            request = json.loads(sys.stdin.buffer.read(1 << 20).decode("utf-8"))
        except ValueError:
            request = None
        answer = handle(request)
    sys.stdout.write(json.dumps(answer, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
