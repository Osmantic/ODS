#!/usr/bin/env python3
"""Canonical complete-tree digest for an exact release tree (shared helper).

Single authoritative implementation of the release-tree verification digest used by both
the ordinary apply path and the migration prepare path. Covers every relative entry:
type, mode, regular-file bytes/hash/size, and symlink target string. Rejects hardlinks,
extended attributes on opened files/directories, unsafe ownership/modes, symlink
substitution/traversal, unlisted/extra entries, and content tamper. The install-manifest
must list exactly the regular files with matching hashes.
Symlink targets are bound EXACTLY as text and never followed, so legitimate absolute and
relative-with-.. targets are accepted while a changed target still changes the digest.
Prints the digest on stdout; exits non-zero on any verification failure.
"""
import hashlib
import json
import os
import stat
import sys

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _unsafe_mode(mode):
    return bool((mode & 0o002) or (mode & (stat.S_ISUID | stat.S_ISGID)))


def _unsafe_rel(path):
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        return True
    if path == ".." or path.startswith("../") or "/../" in path or path.endswith("/.."):
        return True
    return False


def _reject_xattrs(fd, label):
    """Reject authority- or metadata-bearing xattrs on a descriptor-bound object."""
    try:
        attributes = os.listxattr(fd)
    except (AttributeError, OSError):
        sys.exit(f"release-tree-sha: release tree extended attributes could not be inspected: {label}")
    if attributes:
        sys.exit(f"release-tree-sha: release tree contains extended attributes: {label}")


def release_tree_sha(release_dir: str) -> str:
    root_fd = os.open(release_dir, os.O_RDONLY | os.O_DIRECTORY | O_NOFOLLOW)
    entries = []
    manifest_bytes = b""
    manifest_entry = None
    try:
        root_info = os.fstat(root_fd)
        root_mode = stat.S_IMODE(root_info.st_mode)
        if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid() \
                or (root_mode & 0o002) or (root_mode & (stat.S_ISUID | stat.S_ISGID)):
            sys.exit("release-tree-sha: release tree root is not a safe owner-owned directory")
        _reject_xattrs(root_fd, ".")

        def walk(fd, rel):
            nonlocal manifest_entry
            for name in sorted(os.listdir(fd)):
                if name in (".", "..") or "/" in name or "\x00" in name:
                    sys.exit(f"release-tree-sha: release tree entry is unsafe: {name!r}")
                r = name if not rel else rel + "/" + name
                st = os.lstat(name, dir_fd=fd)
                mode = stat.S_IMODE(st.st_mode)
                if stat.S_ISLNK(st.st_mode):
                    # Bind the symlink TARGET STRING exactly; never follow it.
                    target = os.readlink(name, dir_fd=fd)
                    entries.append({"path": r, "type": "symlink", "mode": mode, "target": target})
                elif stat.S_ISDIR(st.st_mode):
                    if st.st_uid != os.geteuid() or _unsafe_mode(mode):
                        sys.exit(f"release-tree-sha: release tree directory has unsafe owner/mode: {r}")
                    try:
                        sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | O_NOFOLLOW, dir_fd=fd)
                    except OSError:
                        sys.exit(f"release-tree-sha: release tree directory could not be opened safely: {r}")
                    try:
                        sub_info = os.fstat(sub)
                        sub_mode = stat.S_IMODE(sub_info.st_mode)
                        if not stat.S_ISDIR(sub_info.st_mode) \
                                or (sub_info.st_ino, sub_info.st_dev) != (st.st_ino, st.st_dev) \
                                or sub_info.st_uid != os.geteuid() \
                                or sub_mode != mode \
                                or _unsafe_mode(sub_mode) or sub_info.st_nlink != st.st_nlink:
                            sys.exit(f"release-tree-sha: release tree directory changed under the verifier: {r}")
                        _reject_xattrs(sub, r)
                        entries.append({"path": r, "type": "dir", "mode": mode})
                        walk(sub, r)
                    finally:
                        os.close(sub)
                elif stat.S_ISREG(st.st_mode):
                    if st.st_nlink != 1:
                        sys.exit(f"release-tree-sha: release tree contains a hardlink: {r}")
                    if st.st_uid != os.geteuid() or _unsafe_mode(mode):
                        sys.exit(f"release-tree-sha: release tree file has unsafe owner/mode: {r}")
                    try:
                        f = os.open(name, os.O_RDONLY | O_NOFOLLOW, dir_fd=fd)
                    except OSError:
                        sys.exit(f"release-tree-sha: release tree file could not be opened safely: {r}")
                    try:
                        finfo = os.fstat(f)
                        fmode = stat.S_IMODE(finfo.st_mode)
                        if not stat.S_ISREG(finfo.st_mode) \
                                or (finfo.st_ino, finfo.st_dev) != (st.st_ino, st.st_dev) \
                                or finfo.st_uid != os.geteuid() \
                                or fmode != mode \
                                or _unsafe_mode(fmode) or finfo.st_nlink != st.st_nlink:
                            sys.exit(f"release-tree-sha: release tree file changed under the verifier: {r}")
                        _reject_xattrs(f, r)
                        h = hashlib.sha256()
                        size = 0
                        while True:
                            chunk = os.read(f, 65536)
                            if not chunk:
                                break
                            h.update(chunk)
                            size += len(chunk)
                    finally:
                        os.close(f)
                    if r == "install-manifest.sha256":
                        manifest_entry = {"dev": st.st_dev, "ino": st.st_ino,
                                          "uid": st.st_uid, "mode": mode,
                                          "nlink": st.st_nlink, "size": size,
                                          "sha256": h.hexdigest()}
                    entries.append({"path": r, "type": "file", "mode": mode,
                                    "size": size, "sha256": h.hexdigest()})
                else:
                    sys.exit(f"release-tree-sha: release tree contains an unsupported object: {r}")

        walk(root_fd, "")

        # install-manifest.sha256 must itself be an owner-owned safe regular file opened
        # descriptor-relatively with O_NOFOLLOW (never followed, never world/gid-writable).
        try:
            mfd = os.open("install-manifest.sha256", os.O_RDONLY | O_NOFOLLOW, dir_fd=root_fd)
        except OSError:
            sys.exit("release-tree-sha: release install-manifest is not a safe regular file")
        try:
            minfo = os.fstat(mfd)
            mmode = stat.S_IMODE(minfo.st_mode)
            if manifest_entry is None \
                    or not stat.S_ISREG(minfo.st_mode) \
                    or minfo.st_dev != manifest_entry["dev"] \
                    or minfo.st_ino != manifest_entry["ino"] \
                    or minfo.st_uid != manifest_entry["uid"] \
                    or mmode != manifest_entry["mode"] \
                    or minfo.st_nlink != manifest_entry["nlink"]:
                sys.exit("release-tree-sha: release install-manifest changed under the verifier")
            if minfo.st_uid != os.geteuid() \
                    or (mmode & 0o002) or (mmode & (stat.S_ISUID | stat.S_ISGID)) \
                    or minfo.st_nlink != 1:
                sys.exit("release-tree-sha: release install-manifest is not a safe owner-owned regular file")
            _reject_xattrs(mfd, "install-manifest.sha256")
            mh = hashlib.sha256()
            msize = 0
            while True:
                chunk = os.read(mfd, 65536)
                if not chunk:
                    break
                mh.update(chunk)
                msize += len(chunk)
                manifest_bytes += chunk
            if msize != manifest_entry["size"] or mh.hexdigest() != manifest_entry["sha256"]:
                sys.exit("release-tree-sha: release install-manifest changed under the verifier")
        finally:
            os.close(mfd)
    finally:
        os.close(root_fd)

    # Manifest must list exactly the regular files with matching hashes (no extras, no
    # missing, no content tamper, no duplicate, no malformed/unsafe relative path).
    file_map = {e["path"]: e for e in entries
                if e["type"] == "file" and e["path"] != "install-manifest.sha256"}
    manifest_map = {}
    for line in manifest_bytes.decode("utf-8").splitlines():
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            sys.exit("release-tree-sha: release install-manifest is malformed")
        digest, path = parts
        rel = path[2:] if path.startswith("./") else path
        if _unsafe_rel(rel):
            sys.exit("release-tree-sha: release install-manifest contains an unsafe relative path")
        if rel in manifest_map:
            sys.exit("release-tree-sha: release install-manifest contains a duplicate entry")
        manifest_map[rel] = digest
    if manifest_map != {p: e["sha256"] for p, e in file_map.items()}:
        sys.exit("release-tree-sha: release install-manifest does not exactly match the release tree")
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: release-tree-sha.py RELEASE_DIR")
    print(release_tree_sha(sys.argv[1]))
