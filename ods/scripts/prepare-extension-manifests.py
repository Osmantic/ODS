#!/usr/bin/env python3
"""Prepare public extension manifest permissions for ODS runtime bind-mounts.

Root cause: with umask 077, copied extensions/services/ape directory is 0700 and
manifest.yaml is 0600. The rootless dashboard-api (UID 1000) then fails in
config.load_extension_manifests at candidate.exists()/read_text().

This script only fixes the runtime manifest bind-mount surface:
  - root extensions/services ancestors: traversal only (add x, remove g/o w)
  - services root: 0755
  - service child dirs: traversal only (add x, remove g/o w)
  - public manifest files (manifest.yaml/.yml/.json): 0644
  - config/: traversal only; the named bundled Dashboard/model metadata: 0644
  - templates/: 0755 for enumeration; named bundled templates: 0644

It validates the full scoped tree before any mutation, refuses symlinks,
special files, hardlinked manifests, and wrong path types. It does not descend
into child dirs except for the listed manifest leaves. Private files such as
private.key (0600) and unrelated dirs are left untouched.

POSIX only. CLI: prepare-extension-manifests.py <install_dir>
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

MANIFEST_NAMES = ("manifest.yaml", "manifest.yml", "manifest.json")
PUBLIC_DASHBOARD_FILES = {
    "config": (
        "core-service-ids.json", "extensions-catalog.json",
        "model-library.json", "llama-cpp-architectures.json",
    ),
    "templates": (
        "ai-coding-workspace.yaml", "chat-playground.yaml", "creative-studio.yaml",
        "developer-homelab.yaml", "llm-platform.yaml", "ml-data-workshop.yaml",
        "multi-agent-laboratory.yaml", "no-code-ai-builder.yaml", "onboarding-agents.yaml",
        "onboarding-full-stack.yaml", "personal-knowledge-base.yaml",
        "private-research-hub.yaml", "voice-assistant.yaml",
    ),
}


class ManifestPermissionError(Exception):
    """Actionable error for manifest permission preparation."""


class _Plan:
    def __init__(self) -> None:
        # path -> target mode
        self.modes: dict[Path, int] = {}
        self.identities: dict[Path, tuple[int, int]] = {}
        self.manifest_count = 0

    def add(self, path: Path, mode: int) -> None:
        self.modes[path] = mode
        entry = os.lstat(path)
        self.identities[path] = (entry.st_dev, entry.st_ino)


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except FileNotFoundError as exc:
        raise ManifestPermissionError(f"required path missing: {path}") from exc
    except OSError as exc:
        raise ManifestPermissionError(f"cannot stat {path}: {exc}") from exc


def _require_dir(path: Path, label: str) -> os.stat_result:
    st = _lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise ManifestPermissionError(f"{label} is a symlink (refused): {path}")
    if not stat.S_ISDIR(st.st_mode):
        raise ManifestPermissionError(f"{label} is not a directory: {path}")
    return st


def _require_regular_manifest(path: Path) -> os.stat_result:
    st = _lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise ManifestPermissionError(f"manifest is a symlink (refused): {path}")
    if not stat.S_ISREG(st.st_mode):
        raise ManifestPermissionError(f"manifest is not a regular file: {path}")
    if st.st_nlink != 1:
        raise ManifestPermissionError(
            f"manifest is hardlinked (st_nlink={st.st_nlink}, refused): {path}"
        )
    return st


def _dir_traversal_mode(st_mode: int) -> int:
    """Keep existing read bits, add execute, remove group/other write."""
    mode = stat.S_IMODE(st_mode)
    mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    mode &= ~(stat.S_IWGRP | stat.S_IWOTH)
    return mode


def _services_root_mode(st_mode: int) -> int:
    """Services root must be 0755."""
    del st_mode
    return 0o755


def _manifest_mode(st_mode: int) -> int:
    """Public manifest files must be 0644."""
    del st_mode
    return 0o644


def _validate_ancestor(path: Path, label: str) -> None:
    _require_dir(path, label)


def _build_plan(install_dir: Path, *, preserve_file_modes: bool = False) -> _Plan:
    plan = _Plan()

    if not install_dir.is_absolute():
        # abspath preserves a final symlink so validation can reject it.
        install_dir = Path(os.path.abspath(install_dir))

    # Only the installation and its children belong to this repair.
    _validate_ancestor(install_dir, "install_dir")

    extensions = install_dir / "extensions"
    services = extensions / "services"

    _validate_ancestor(extensions, "extensions dir")
    _validate_ancestor(services, "services dir")

    # Root and extensions: traversal only.
    plan.add(install_dir, _dir_traversal_mode(_lstat(install_dir).st_mode))
    plan.add(extensions, _dir_traversal_mode(_lstat(extensions).st_mode))

    # Services root: 0755.
    plan.add(services, _services_root_mode(_lstat(services).st_mode))

    # Enumerate service child dirs (no descent beyond this level).
    try:
        entries = list(os.scandir(services))
    except OSError as exc:
        raise ManifestPermissionError(f"cannot list services dir {services}: {exc}") from exc

    for entry in entries:
        child = Path(entry.path)
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ManifestPermissionError(f"cannot stat service entry {child}: {exc}") from exc

        if stat.S_ISLNK(st.st_mode):
            raise ManifestPermissionError(f"service child is a symlink (refused): {child}")
        if not stat.S_ISDIR(st.st_mode):
            # Match the service loader: files beside service directories are
            # not service packages and are outside this permission repair.
            continue

        plan.add(child, _dir_traversal_mode(st.st_mode))

        # Only inspect listed manifest leaves; do not descend further.
        for name in MANIFEST_NAMES:
            manifest = child / name
            try:
                mst = _require_regular_manifest(manifest)
            except ManifestPermissionError as exc:
                # Missing manifest is okay; other errors are not.
                if isinstance(exc.__cause__, FileNotFoundError):
                    continue
                raise
            mode = stat.S_IMODE(mst.st_mode)
            if preserve_file_modes:
                # Pixel's staged source inventory owns file modes during an
                # upgrade. Candidates are already normalized to 0644/0755;
                # retained custom files must not drift from that inventory.
                if mode & 0o444 != 0o444 or mode & 0o022:
                    raise ManifestPermissionError(
                        f"public manifest is unreadable under a protected source "
                        f"transaction: {manifest}; preserve the pending upgrade "
                        "and resolve its source permissions before retrying"
                    )
            else:
                mode = _manifest_mode(mst.st_mode)
            plan.add(manifest, mode)
            plan.manifest_count += 1

    # These source files are bundled public catalogs, not arbitrary config or
    # user templates. A single plan validates every scope before any chmod.
    for directory, names in PUBLIC_DASHBOARD_FILES.items():
        parent = install_dir / directory
        try:
            parent_stat = _require_dir(parent, f"public {directory} directory")
        except ManifestPermissionError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                continue
            raise
        mode = 0o755 if directory == "templates" else _dir_traversal_mode(parent_stat.st_mode)
        plan.add(parent, mode)
        for name in names:
            leaf = parent / name
            try:
                _require_regular_manifest(leaf)
            except ManifestPermissionError as exc:
                if isinstance(exc.__cause__, FileNotFoundError):
                    continue
                raise
            # config/ and templates/ are outside Pixel's protected source
            # roots. The ordinary source copy retains an owner's umask-077
            # permissions here, unlike the staged manifest modes above. Make
            # only these named public catalogs readable even during a held
            # upgrade; never change a transaction-owned manifest or private
            # configuration. The source-inventory regression checks this
            # ownership boundary against Pixel's actual ROOTS.
            plan.add(leaf, 0o644)

    return plan


def _apply_plan(plan: _Plan) -> int:
    changed = 0
    for path, target in plan.modes.items():
        try:
            current = stat.S_IMODE(os.lstat(path).st_mode)
        except OSError as exc:
            raise ManifestPermissionError(f"cannot stat {path} before chmod: {exc}") from exc
        if current == target:
            continue
        try:
            # Do not follow a leaf replaced with a symlink after validation.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino) != plan.identities[path]:
                    raise ManifestPermissionError(f"path was replaced before chmod: {path}")
                if not (stat.S_ISDIR(opened.st_mode) or stat.S_ISREG(opened.st_mode)):
                    raise ManifestPermissionError(f"path type changed before chmod: {path}")
                if stat.S_ISREG(opened.st_mode) and opened.st_nlink != 1:
                    raise ManifestPermissionError(f"manifest became hardlinked: {path}")
                os.fchmod(fd, target)
            finally:
                os.close(fd)
        except OSError as exc:
            raise ManifestPermissionError(
                f"cannot chmod {path} from {oct(current)} to {oct(target)}: {exc}"
            ) from exc
        changed += 1
    return changed


def prepare(install_dir: Path, *, preserve_file_modes: bool = False) -> tuple[int, int]:
    plan = _build_plan(install_dir, preserve_file_modes=preserve_file_modes)
    changed = _apply_plan(plan)
    return plan.manifest_count, changed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("install_dir", type=Path)
    parser.add_argument("--preserve-file-modes", action="store_true",
                        help="validate protected source modes without changing manifest files")
    args = parser.parse_args(argv[1:])
    try:
        manifest_count, changed = prepare(args.install_dir, preserve_file_modes=args.preserve_file_modes)
    except ManifestPermissionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"prepare-extension-manifests: {manifest_count} manifest(s) validated, "
        f"{changed} path(s) updated"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
