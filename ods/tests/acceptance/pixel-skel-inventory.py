#!/usr/bin/env python3
"""Compare only metadata and bytes of known broker-home residue to /etc/skel."""

import hashlib
import os
from pathlib import Path
import stat

STATE = Path("/var/lib/pixel-ops-broker")
SKEL = Path("/etc/skel")
NAMES = (
    ".bash_profile", ".cargo", ".composer", ".config", ".dotnet",
    ".ghcup", ".nvm", ".rustup",
)
MAX_ENTRIES = 4096
MAX_BYTES = 64 * 1024 * 1024


def tree(path):
    if not os.path.lexists(path):
        return None
    rows = {}
    pending = [(path, "")]
    total_bytes = 0
    while pending:
        current, relative = pending.pop()
        info = current.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            rows[relative] = ("dir", mode, None)
            with os.scandir(current) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
            for child in children:
                pending.append((Path(child.path), f"{relative}/{child.name}"))
        elif stat.S_ISLNK(info.st_mode):
            rows[relative] = ("link", mode, os.readlink(current))
        elif stat.S_ISREG(info.st_mode):
            total_bytes += info.st_size
            if total_bytes > MAX_BYTES:
                raise ValueError("entry byte bound exceeded")
            digest = hashlib.sha256()
            with current.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            rows[relative] = ("file", mode, digest.digest())
        else:
            rows[relative] = ("special", mode, None)
        if len(rows) > MAX_ENTRIES:
            raise ValueError("entry count bound exceeded")
    return rows


for name in NAMES:
    state_path, skel_path = STATE / name, SKEL / name
    try:
        state_tree, skel_tree = tree(state_path), tree(skel_path)
    except ValueError as error:
        print(f"Pixel skel candidate {name}: comparison={error}")
        continue
    if state_tree is None:
        print(f"Pixel skel candidate {name}: state=absent")
        continue
    top = state_path.lstat()
    if skel_tree is None:
        print(f"Pixel skel candidate {name}: skel=absent state_entries={len(state_tree)}")
        continue
    same_shape = {key: value[0] for key, value in state_tree.items()} == {
        key: value[0] for key, value in skel_tree.items()
    }
    same_modes = same_shape and all(
        state_tree[key][1] == skel_tree[key][1] for key in state_tree
    )
    same_bytes = same_shape and all(
        state_tree[key][2] == skel_tree[key][2] for key in state_tree
    )
    print(
        f"Pixel skel candidate {name}: state_entries={len(state_tree)} "
        f"skel_entries={len(skel_tree)} shape={same_shape} modes={same_modes} "
        f"bytes={same_bytes} top_uid={top.st_uid} top_gid={top.st_gid}"
    )
