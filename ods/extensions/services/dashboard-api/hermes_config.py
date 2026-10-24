"""Migrate the legacy ODS provider timeout before Hermes starts.

The upstream entrypoint seeds config.yaml only once. Retained installations can
therefore keep the shipped 180-second override even after their template selects
900 seconds. Other owner timeout values, routing, credentials and layout remain
unchanged. An explicitly selected 180 is indistinguishable from that old default.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import uuid

import yaml


def _timeout_node(text: str):
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
        anchored_mappings = {event.start_mark.index for event in yaml.parse(text)
                             if isinstance(event, yaml.MappingStartEvent) and event.anchor}
    except yaml.YAMLError:
        raise ValueError("Invalid Hermes configuration YAML") from None
    for key in ("providers", "custom", "request_timeout_seconds"):
        if node is None:
            return None
        if isinstance(node, yaml.ScalarNode) and node.tag == 'tag:yaml.org,2002:null':
            return None
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("Hermes provider configuration must be a mapping")
        if node.start_mark.index in anchored_mappings:
            return None  # An anchored owner mapping may also serve other users.
        matches = [value for field, value in node.value
                   if isinstance(field, yaml.ScalarNode) and field.value == key]
        if len(matches) > 1:
            raise ValueError("Duplicate Hermes provider configuration key")
        node = matches[0] if matches else None
    return node


def migrated_timeout(text: str, template: str) -> str:
    """Replace only an explicit legacy scalar when the installed template is 900."""
    desired = _timeout_node(template)
    if not isinstance(desired, yaml.ScalarNode) or desired.value != "900":
        return text
    current = _timeout_node(text)
    if not isinstance(current, yaml.ScalarNode):
        return text  # Missing override already falls back to Hermes's env budget.
    start, end = current.start_mark.index, current.end_mark.index
    # Do not rewrite anchors, aliases or tagged owner expressions. A scalar's
    # source marks may point to a definition shared elsewhere in the document.
    token = text[start:end]
    if not re.fullmatch(r"(?:180(?:\.0+)?|'180(?:\.0+)?'|\"180(?:\.0+)?\")", token):
        return text
    replacement = token[0] + "900" + token[-1] if token[0] in "\"'" else "900"
    return text[:start] + replacement + text[end:]


def _open_directory(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Hermes home must be an absolute directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def migrate_provider_timeout(home: Path, template: Path) -> bool:
    """Atomically update private retained data without changing its owner/mode."""
    desired = template.read_text(encoding="utf-8")
    try:
        directory = _open_directory(home)
    except FileNotFoundError:
        return False  # Upstream will seed a new home from the template.
    temporary = None
    try:
        try:
            fd = os.open("config.yaml", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
        except FileNotFoundError:
            return False
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Hermes configuration must be a regular file")
            original = source.read()
        text = original.decode("utf-8")
        updated = migrated_timeout(text, desired).encode("utf-8")
        if updated == original:
            return False
        temporary = ".ods-timeout-" + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, "wb") as output:
            output.write(updated)
            output.flush()
            os.fchown(output.fileno(), before.st_uid, before.st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(before.st_mode))
            os.fsync(output.fileno())
        current = os.stat("config.yaml", dir_fd=directory, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns,
                current.st_ctime_ns) != (before.st_dev, before.st_ino,
                before.st_size, before.st_mtime_ns, before.st_ctime_ns):
            raise RuntimeError("Hermes configuration changed during migration")
        os.replace(temporary, "config.yaml", src_dir_fd=directory, dst_dir_fd=directory)
        temporary = None
        os.fsync(directory)
        return True
    finally:
        if temporary is not None:
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)


if __name__ == "__main__":
    try:
        migrate_provider_timeout(Path(os.environ.get("HERMES_HOME", "/opt/data")),
                                 Path("/opt/hermes/cli-config.yaml.example"))
    except (OSError, ValueError, RuntimeError):
        # YAML/parser diagnostics can quote credentials from owner configuration.
        raise SystemExit("Hermes timeout migration failed; inspect configuration before retrying startup") from None
