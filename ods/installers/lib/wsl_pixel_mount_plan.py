#!/usr/bin/env python3
"""Read-only admission for retiring the former WSL Pixel socket binds.

This reports whether the mount graph is the ordinary two-bind layout. It never
stops services or unmounts anything. A mutating caller must repeat admission
under its host lock immediately before acting. CLI exit codes are 0 for clear
or ordinary, 3 when a verified ODS Edge stop is still required, and 1 for
refusal or an unreadable graph.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

INGRESS = "/mnt/wsl/ods-portal-runtime/ingress"
PREVIEW = "/mnt/wsl/ods-portal-runtime/preview"
EXPECTED = {
    INGRESS: ("/ods-pixel", "/run/ods-pixel"),
    PREVIEW: ("/ods-pixel-preview", "/run/ods-pixel-preview"),
}
PROXY_PREFIX = "/mnt/wsl/docker-desktop-bind-mounts/"
PROXY_ROOTS = {
    "/ods-portal-runtime/ingress",
    "/ods-portal-runtime/preview",
    "/ods-pixel",
    "/ods-pixel-preview",
    "/run/ods-pixel",
    "/run/ods-pixel-preview",
}
ACTIVE_PROXY_ROOTS = {
    "/ods-pixel": "/run/ods-pixel",
    "/ods-pixel-preview": "/run/ods-pixel-preview",
}
_ESCAPE = re.compile(r"\\([0-7]{3})")


@dataclass(frozen=True)
class Mount:
    mount_id: int
    parent_id: int
    device: str
    root: str
    target: str
    fstype: str
    source: str
    optional: tuple[str, ...]


def _decode(token: str) -> str:
    # Linux mountinfo only escapes space, tab, newline, and backslash.
    if re.search(r"\\(?!040|011|012|134)", token):
        raise ValueError("invalid mountinfo escape")
    decoded = _ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), token)
    if "\x00" in decoded:
        raise ValueError("invalid mountinfo escape")
    return decoded


def parse_mountinfo(contents: str) -> list[Mount]:
    rows: list[Mount] = []
    ids: set[int] = set()
    for number, line in enumerate(contents.splitlines(), 1):
        try:
            left, right = line.split(" - ", 1)
            fields, tail = left.split(), right.split()
            if len(fields) < 6 or len(tail) < 3:
                raise ValueError("missing fields")
            mount_id, parent_id = int(fields[0]), int(fields[1])
            if mount_id < 1 or parent_id < 0 or mount_id in ids:
                raise ValueError("invalid or repeated mount ID")
            ids.add(mount_id)
            device, root, target = fields[2], _decode(fields[3]), _decode(fields[4])
            if (
                not re.fullmatch(r"\d+:\d+", device)
                or not root.startswith("/")
                or not target.startswith("/")
            ):
                raise ValueError("invalid mount path or device")
            rows.append(
                Mount(
                    mount_id,
                    parent_id,
                    device,
                    root,
                    target,
                    tail[0],
                    _decode(tail[1]),
                    tuple(fields[6:]),
                )
            )
        except (ValueError, IndexError) as exc:
            raise ValueError(f"mountinfo line {number}: {exc}") from exc
    if not rows:
        raise ValueError("empty mountinfo")
    return rows


def _shared_id(row: Mount) -> str | None:
    matches = [
        field for field in row.optional if re.fullmatch(r"shared:[1-9][0-9]*", field)
    ]
    return matches[0] if len(matches) == 1 else None


def classify(
    contents: str,
    identities: dict[str, tuple[int, int, str]] | None,
    namespace_equal: bool,
) -> dict:
    """Return clear, ordinary, or refuse; identities map path to (dev, ino, type)."""
    try:
        rows = parse_mountinfo(contents)
    except ValueError as exc:
        return {"status": "refuse", "reasons": [str(exc)], "rows": []}

    selected = [row for row in rows if row.target in EXPECTED]
    proxies = [
        row
        for row in rows
        if row.target.startswith(PROXY_PREFIX) and row.root in PROXY_ROOTS
    ]
    protected = (
        *EXPECTED,
        "/run/ods-pixel",
        "/run/ods-pixel-preview",
        *(row.target for row in proxies),
    )
    nested = [
        row
        for row in rows
        if any(row.target.startswith(target + "/") for target in protected)
    ]
    reasons: list[str] = []
    if not namespace_equal:
        reasons.append("installer and PID 1 mount namespaces differ")
    if nested:
        reasons.append(f"nested mounts under former socket targets ({len(nested)})")

    for target, (root, source) in EXPECTED.items():
        at_target = [row for row in selected if row.target == target]
        if selected and not at_target:
            reasons.append(f"missing former socket mount at {target}")
        if len(at_target) > 1:
            reasons.append(f"stacked mounts at {target} ({len(at_target)})")
        elif at_target:
            row = at_target[0]
            if (
                row.root != root
                or row.fstype != "tmpfs"
                or row.source != "none"
                or _shared_id(row) is None
            ):
                reasons.append(f"unexpected mount identity at {target}")
            source_identity = (identities or {}).get(source)
            target_identity = (identities or {}).get(target)
            if (
                source_identity is None
                or target_identity is None
                or source_identity != target_identity
            ):
                reasons.append(f"source and target inode proof unavailable at {target}")
            elif (
                row.device
                != f"{os.major(source_identity[0])}:{os.minor(source_identity[0])}"
            ):
                reasons.append(f"mount device differs from socket source at {target}")

    if selected and len(selected) != 2:
        reasons.append("partial former socket layout")
    if proxies:
        counts = {
            root: sum(row.root == root for row in proxies)
            for root in ACTIVE_PROXY_ROOTS
        }
        if (
            not selected
            or len(proxies) != 2
            or any(count != 1 for count in counts.values())
        ):
            reasons.append(
                f"unexpected Docker Desktop projection graph ({len(proxies)})"
            )
        for row in proxies:
            source = ACTIVE_PROXY_ROOTS.get(row.root)
            source_identity = (identities or {}).get(source) if source else None
            proxy_identity = (identities or {}).get(row.target)
            target = next(
                (
                    target
                    for target, (_, expected_source) in EXPECTED.items()
                    if expected_source == source
                ),
                None,
            )
            target_row = next(
                (candidate for candidate in selected if candidate.target == target),
                None,
            )
            if (
                source_identity is None
                or proxy_identity is None
                or source_identity != proxy_identity
                or row.fstype != "tmpfs"
                or row.source != "none"
                or row.device
                != f"{os.major(source_identity[0])}:{os.minor(source_identity[0])}"
            ):
                reasons.append(
                    f"Docker Desktop projection identity unavailable at {row.target}"
                )
            if (
                target_row is None
                or _shared_id(row) is None
                or _shared_id(row) != _shared_id(target_row)
            ):
                reasons.append(
                    f"Docker Desktop projection propagation differs at {row.target}"
                )
    if reasons:
        status = "refuse"
    elif selected:
        # The caller must prove the exact ODS Edge container before stopping it.
        status = "needs-owned-edge-stop" if proxies else "ordinary"
    else:
        status = "clear"
    return {
        "status": status,
        "reasons": reasons,
        "counts": {
            "old_targets": len(selected),
            "desktop_projections": len(proxies),
            "nested": len(nested),
        },
        "rows": [
            {
                "id": row.mount_id,
                "root": row.root,
                "target": row.target,
                "device": row.device,
            }
            for row in (selected + proxies)[:8]
        ],
    }


def _identities(rows: list[Mount]) -> dict[str, tuple[int, int, str]]:
    result: dict[str, tuple[int, int, str]] = {}
    for target, (_, source) in EXPECTED.items():
        for path in (source, target):
            entry = os.lstat(path)
            if not stat.S_ISDIR(entry.st_mode):
                raise ValueError(f"socket directory is not a real directory: {path}")
            result[path] = (entry.st_dev, entry.st_ino, "directory")
    for row in rows:
        if row.target.startswith(PROXY_PREFIX) and row.root in PROXY_ROOTS:
            entry = os.lstat(row.target)
            if not stat.S_ISDIR(entry.st_mode):
                raise ValueError(
                    f"Docker Desktop projection is not a real directory: {row.target}"
                )
            result[row.target] = (entry.st_dev, entry.st_ino, "directory")
    return result


def main() -> int:
    try:
        contents = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        self_namespace = os.readlink("/proc/self/ns/mnt")
        pid1_namespace = os.readlink("/proc/1/ns/mnt")
        same_namespace = self_namespace == pid1_namespace
        # Plain absent old targets need no inode proof. Existing targets must be
        # checked through lstat before an ordinary pair can be admitted.
        rows = parse_mountinfo(contents)
        identities = (
            _identities(rows) if any(row.target in EXPECTED for row in rows) else {}
        )
        if contents != Path("/proc/self/mountinfo").read_text(encoding="utf-8") or (
            self_namespace != os.readlink("/proc/self/ns/mnt")
            or pid1_namespace != os.readlink("/proc/1/ns/mnt")
        ):
            raise ValueError("mount graph or namespace changed during admission")
        result = classify(contents, identities, same_namespace)
    except (OSError, ValueError) as exc:
        result = {
            "status": "refuse",
            "reasons": [f"mount admission unavailable: {exc}"],
            "rows": [],
        }
    print(json.dumps(result, sort_keys=True))
    if result["status"] == "needs-owned-edge-stop":
        return 3
    return 0 if result["status"] in ("clear", "ordinary") else 1


if __name__ == "__main__":
    raise SystemExit(main())
