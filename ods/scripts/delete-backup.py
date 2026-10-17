#!/usr/bin/env python3
"""Delete the inspected backup, never a replacement at its public name.

An open descriptor pins the selected inode through confirmation. Retirement is
an atomic rename into a new private directory, followed by an identity check.
Anything unexpected is preserved there. This protects the public-name handoff;
it is not a lock against the owner editing the contents of the selected backup.
"""

import argparse
import contextlib
import os
import re
import secrets
import stat
import subprocess
import sys
import tarfile


class Refusal(Exception):
    pass


def identity(info):
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def entry_identity(parent, name):
    return identity(os.stat(name, dir_fd=parent, follow_symlinks=False))


def open_selected(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise Refusal("Backup is not a regular archive or directory")
        if identity(info) != entry_identity(parent, name):
            raise Refusal("Backup changed while it was selected")
        return fd
    except BaseException:
        os.close(fd)
        raise


def validate_manifest(fd, name):
    archive_id = name.removesuffix(".tar.gz")
    limit = 1024 * 1024
    if stat.S_ISDIR(os.fstat(fd).st_mode):
        metadata = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(metadata, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise Refusal("Backup manifest is not a regular file")
            manifest = stream.read(limit + 1)
    elif name.endswith(".tar.gz"):
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), "rb") as stream, tarfile.open(fileobj=stream, mode="r|gz") as archive:
            manifest = None
            for member in archive:
                if member.name != archive_id + "/manifest.json":
                    continue
                if manifest is not None or not member.isfile() or member.size > limit:
                    raise Refusal("Archive manifest is linked, duplicated, or too large")
                contents = archive.extractfile(member)
                if contents is None:
                    raise Refusal("Archive manifest is unavailable")
                with contents:
                    manifest = contents.read(limit + 1)
            if manifest is None:
                raise Refusal("Backup manifest is unavailable")
    else:
        raise Refusal("Backup manifest is unavailable")
    if len(manifest) > limit:
        raise Refusal("Backup manifest is too large")
    # Preserve the public CLI's exact ownership contract and fail closed when
    # the existing metadata validator cannot run.
    checked = subprocess.run(
        ["jq", "-e", "-s", "--arg", "id", archive_id,
         'length == 1 and (.[0] | type == "object" and .manifest_version == "1.0"'
         ' and .backup_id == $id and (.backup_type == "config" or .backup_type == "user-data" or .backup_type == "full"))'],
        input=manifest, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False,
    )
    if checked.returncode:
        raise Refusal("Backup manifest does not identify the selected backup")


def remove_contents(directory):
    """Walk held directories without following links or resolving public paths."""
    for name in os.listdir(directory):
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                if identity(os.fstat(child)) != identity(info):
                    raise Refusal("Backup contents changed during deletion")
                remove_contents(child)
                if entry_identity(directory, name) != identity(info):
                    raise Refusal("Backup contents changed during deletion")
                os.rmdir(name, dir_fd=directory)
            finally:
                os.close(child)
        else:
            os.unlink(name, dir_fd=directory)


def delete_backup(root, backup_id, confirm=input):
    if not re.fullmatch(r"(?:[A-Za-z0-9_][A-Za-z0-9_-]*-)?[0-9]{8}-[0-9]{6}(?:\.tar\.gz)?", backup_id):
        raise Refusal("Not an ODS backup ID")
    with contextlib.ExitStack() as stack:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, root_fd)
        name = backup_id
        try:
            os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            if not name.endswith(".tar.gz"):
                name += ".tar.gz"
        selected = open_selected(root_fd, name)
        stack.callback(os.close, selected)
        expected = identity(os.fstat(selected))
        validate_manifest(selected, name)
        try:
            answer = confirm("Are you sure you want to delete backup " + name + "? [y/N] ")
        except EOFError:
            answer = ""
        if answer not in ("y", "Y"):
            print("[INFO] Deletion cancelled")
            return
        if entry_identity(root_fd, name) != expected:
            raise Refusal("Backup changed during confirmation; no files deleted")
        validate_manifest(selected, name)
        retired = ".ods-delete-" + secrets.token_hex(16)
        os.mkdir(retired, 0o700, dir_fd=root_fd)
        private = os.open(retired, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        stack.callback(os.close, private)
        moved = False
        try:
            # A replacement can still race the preceding check. Never remove
            # the retired object until it is proved to be the held selection.
            os.rename(name, "artifact", src_dir_fd=root_fd, dst_dir_fd=private)
            moved = True
            if entry_identity(private, "artifact") != expected:
                raise Refusal("Backup changed during retirement")
            if stat.S_ISDIR(os.fstat(selected).st_mode):
                remove_contents(selected)
                if entry_identity(private, "artifact") != expected:
                    raise Refusal("Retired backup changed during deletion")
                os.rmdir("artifact", dir_fd=private)
            else:
                os.unlink("artifact", dir_fd=private)
            moved = False
            os.rmdir(retired, dir_fd=root_fd)
        except BaseException:
            if moved:
                print("[ERROR] Deletion stopped; retained data at " + os.path.join(root, retired, "artifact"), file=sys.stderr)
            else:
                # Remove only our empty retirement directory. A cleanup error
                # must not turn a failed deletion into success.
                with contextlib.suppress(OSError):
                    os.rmdir(retired, dir_fd=root_fd)
            raise
        print("[SUCCESS] Deleted backup: " + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_root")
    parser.add_argument("backup_id")
    args = parser.parse_args()
    try:
        delete_backup(args.backup_root, args.backup_id)
    except (OSError, Refusal, tarfile.TarError) as exc:
        print("[ERROR] Failed to delete backup: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[INFO] Deletion cancelled", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
