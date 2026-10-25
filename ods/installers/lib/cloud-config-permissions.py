#!/usr/bin/env python3
"""Normalize legacy private-group provider paths through verified descriptors."""
import errno
import grp
import os
import pwd
import stat
import sys


class Refusal(ValueError):
    pass


def _local_lines(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                or metadata.st_mode & 0o022 or metadata.st_size > 4 * 1024 * 1024):
            raise Refusal(f"{path}: local identity source is unsafe")
        return [line.split("#", 1)[0].strip() for line in stream if line.split("#", 1)[0].strip()]


def _require_private_group(uid, gid):
    if uid == 0 or uid != os.getuid() or uid != os.geteuid() or gid != os.getgid() or gid != os.getegid():
        raise Refusal("automatic repair requires the ordinary owner's primary group")
    databases = {}
    for line in _local_lines("/etc/nsswitch.conf"):
        name, separator, value = line.partition(":")
        name = name.strip()
        if name not in ("passwd", "group"):
            continue
        sources = value.split()
        if (not separator or name in databases or "files" not in sources
                or len(sources) != len(set(sources)) or any(s not in ("files", "systemd") for s in sources)):
            raise Refusal("/etc/nsswitch.conf: cannot establish a local private group")
        databases[name] = sources
    if set(databases) != {"passwd", "group"}:
        raise Refusal("/etc/nsswitch.conf: local identity databases are missing")

    accounts = []
    for line in _local_lines("/etc/passwd"):
        fields = line.split(":")
        if len(fields) != 7 or not fields[0] or not fields[2].isdigit() or not fields[3].isdigit():
            raise Refusal("/etc/passwd: malformed local identity record")
        accounts.append((fields[0], int(fields[2]), int(fields[3])))
    owners = [entry for entry in accounts if entry[1] == uid]
    if len(owners) != 1 or owners[0][2] != gid:
        raise Refusal("/etc/passwd: owner identity is ambiguous")
    owner = owners[0][0]
    if len([entry for entry in accounts if entry[0] == owner]) != 1:
        raise Refusal("/etc/passwd: owner name is ambiguous")
    groups = []
    for line in _local_lines("/etc/group"):
        fields = line.split(":")
        if len(fields) != 4 or not fields[0] or not fields[2].isdigit():
            raise Refusal("/etc/group: malformed local identity record")
        groups.append((fields[0], int(fields[2]), fields[3].split(",") if fields[3] else []))
    matches = [entry for entry in groups if entry[1] == gid]
    if (len(matches) != 1 or matches[0][0] != owner
            or len([entry for entry in groups if entry[0] == owner]) != 1
            or any(member != owner for member in matches[0][2])
            or any(account_uid != uid and account_gid == gid for _, account_uid, account_gid in accounts)):
        raise Refusal("owner primary group is shared or ambiguous")
    # Include accounts/members contributed by the permitted local NSS providers.
    actual_owner, actual_group = pwd.getpwuid(uid), grp.getgrgid(gid)
    named_owner, named_group = pwd.getpwnam(owner), grp.getgrnam(owner)
    if (actual_owner.pw_name != owner or actual_owner.pw_gid != gid
            or named_owner.pw_uid != uid or named_owner.pw_gid != gid or named_group.gr_gid != gid
            or actual_group.gr_name != owner or any(member != owner for member in actual_group.gr_mem)
            or any(member != owner for member in named_group.gr_mem)
            or any(account.pw_uid != uid and account.pw_gid == gid for account in pwd.getpwall())):
        raise Refusal("local NSS does not confirm an exclusive owner primary group")


def _check_entries(entries, targets, modes):
    for parent, name, fd, display, original in entries:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if ((current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode))
                != (original.st_dev, original.st_ino, stat.S_IFMT(original.st_mode))):
            raise Refusal(f"{display}: directory entry changed during validation")
    for _, _, fd, display, original in targets:
        current = os.fstat(fd)
        if (current.st_uid != original.st_uid or current.st_gid != original.st_gid
                or current.st_nlink != original.st_nlink or stat.S_IMODE(current.st_mode) != modes[fd]):
            raise Refusal(f"{display}: ownership or permissions changed during validation")


def _refuse_extended_acl(fd, display):
    # The group mode bits are an ACL mask when named principals are present.
    # A private owning group alone does not establish who could write then.
    for attribute in ("system.posix_acl_access", "system.posix_acl_default"):
        try:
            os.getxattr(fd, attribute)
        except OSError as error:
            if error.errno in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
                continue
            raise Refusal(f"{display}: cannot verify access ACLs") from error
        raise Refusal(f"{display}: extended ACL requires explicit owner review")


def prepare(install_dir):
    parts = [part for part in os.fspath(install_dir).split("/") if part]
    if not os.fspath(install_dir).startswith("/") or not parts or any(part in (".", "..") for part in parts):
        raise Refusal("install path must be an absolute directory without dot components")
    uid = os.geteuid()
    fds, entries, targets = [], [], []
    try:
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        fds.append(parent)
        display = ""
        components = parts + ["config", "litellm", "cloud.yaml"]
        for index, name in enumerate(components):
            display += "/" + name
            leaf = index == len(components) - 1
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if not leaf:
                flags |= os.O_DIRECTORY
            try:
                fd = os.open(name, flags, dir_fd=parent)
            except OSError as error:
                raise Refusal(f"{display}: {error.strerror}") from error
            fds.append(fd)
            metadata = os.fstat(fd)
            if leaf and not stat.S_ISREG(metadata.st_mode):
                raise Refusal(f"{display}: provider configuration is not a regular file")
            entry = (parent, name, fd, display, metadata)
            entries.append(entry)
            if index >= len(parts) - 1:
                if metadata.st_uid != uid or metadata.st_mode & 0o002:
                    raise Refusal(f"{display}: foreign owner or world-write permissions")
                targets.append(entry)
            parent = fd

        modes = {entry[2]: stat.S_IMODE(entry[4].st_mode) for entry in targets}
        repairs = [entry for entry in targets if modes[entry[2]] & 0o020]
        if repairs:
            _require_private_group(uid, os.getegid())
            for _, _, fd, display, metadata in repairs:
                _refuse_extended_acl(fd, display)
                if metadata.st_gid != os.getegid():
                    raise Refusal(f"{display}: writable group is not the owner primary group")
                if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                    raise Refusal(f"{display}: cannot repair a hardlinked provider configuration")
        _check_entries(entries, targets, modes)
        for _, _, fd, display, _ in repairs:
            _check_entries(entries, targets, modes)
            new_mode = modes[fd] & ~0o020
            os.fchmod(fd, new_mode)
            modes[fd] = new_mode
            _check_entries(entries, targets, modes)
        return len(repairs)
    finally:
        for fd in reversed(fds):
            os.close(fd)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: cloud-config-permissions.py <install-directory>", file=sys.stderr)
        return 2
    try:
        changed = prepare(args[0])
    except (OSError, ValueError, KeyError) as error:
        print(f"Existing cloud provider configuration permissions require review: {error}", file=sys.stderr)
        return 1
    if changed:
        print(f"Normalized retained cloud provider permissions on {changed} owner-private paths.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
