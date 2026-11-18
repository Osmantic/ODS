#!/usr/bin/env python3
"""Surgical, dependency-free OpenCode global config updater.

OpenCode 1.18.32 loads three global JSONC files and deep-merges them in this
order (later wins):

    config.json  <  opencode.json  <  opencode.jsonc

This module validates every existing file before touching anything, then
patches only the highest-precedence existing file (or creates
``opencode.json`` when none exist). Lower-precedence files are never
rewritten, mirrored, or copied. Comments, BOMs, trailing commas, and
unrelated text in the target file survive because updates are applied as
surgical text splices using value spans from a small JSONC parser.

Public API:

    parse_jsonc(text) -> dict
    read_effective(config_dir) -> dict
    update_config(config_dir, patch) -> Path

CLI (no credentials in argv):

    python3 bin/opencode_config.py <config_dir>  < patch.json

On success prints only the basename of the written file. On failure prints
only a generic error to stderr and exits non-zero.

POSIX (macOS/Linux) only. Windows support is a separate PR.

Concurrency: cooperating writers that use this module are serialized via an
exclusive ``flock`` on the config directory. Uncooperative writers are not
protected against; the pre-commit snapshot comparison detects most such
races and aborts without mutating the target.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

FILENAMES = ("config.json", "opencode.json", "opencode.jsonc")
DEFAULT_TARGET = "opencode.json"


class ConfigError(Exception):
    """Raised for any validation or write failure. Message is generic."""


# ---------------------------------------------------------------------------
# JSONC parser with value spans
# ---------------------------------------------------------------------------

_WS = " \t\r\n"
_NUM_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.i = 0

    def error(self, msg: str) -> ConfigError:
        return ConfigError(f"invalid JSONC: {msg}")

    def skip_ws(self) -> None:
        t, n = self.text, self.n
        while self.i < n:
            c = t[self.i]
            if c in _WS:
                self.i += 1
            elif c == "/" and self.i + 1 < n and t[self.i + 1] == "/":
                self.i += 2
                while self.i < n and t[self.i] not in "\r\n":
                    self.i += 1
            elif c == "/" and self.i + 1 < n and t[self.i + 1] == "*":
                end = t.find("*/", self.i + 2)
                if end < 0:
                    raise self.error("unterminated block comment")
                self.i = end + 2
            else:
                return

    def parse(self) -> tuple[Any, int, int]:
        self.skip_ws()
        value, start, end = self.value()
        self.skip_ws()
        if self.i != self.n:
            raise self.error("trailing garbage")
        return value, start, end

    def value(self) -> tuple[Any, int, int]:
        self.skip_ws()
        if self.i >= self.n:
            raise self.error("unexpected end")
        c = self.text[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c == '"':
            return self.string()
        if c == "t" and self.text.startswith("true", self.i):
            s = self.i
            self.i += 4
            return True, s, self.i
        if c == "f" and self.text.startswith("false", self.i):
            s = self.i
            self.i += 5
            return False, s, self.i
        if c == "n" and self.text.startswith("null", self.i):
            s = self.i
            self.i += 4
            return None, s, self.i
        if c == "-" or c.isdigit():
            m = _NUM_RE.match(self.text, self.i)
            if not m:
                raise self.error("invalid number")
            s, e = m.span()
            if e < self.n and (self.text[e].isalnum() or self.text[e] in "._"):
                raise self.error("invalid number")
            self.i = e
            return json.loads(self.text[s:e]), s, e
        raise self.error("unexpected character")

    def string(self) -> tuple[str, int, int]:
        s = self.i
        self.i += 1
        t, n = self.text, self.n
        while self.i < n:
            c = t[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == '"':
                self.i += 1
                try:
                    return json.loads(t[s:self.i]), s, self.i
                except json.JSONDecodeError as exc:
                    raise self.error(str(exc)) from exc
            if c in "\r\n":
                raise self.error("newline in string")
            self.i += 1
        raise self.error("unterminated string")

    def obj(self) -> tuple[dict, int, int]:
        s = self.i
        self.i += 1
        result: dict[str, Any] = {}
        self.skip_ws()
        if self.i < self.n and self.text[self.i] == "}":
            self.i += 1
            return result, s, self.i
        while True:
            self.skip_ws()
            if self.i >= self.n or self.text[self.i] != '"':
                raise self.error("expected object key")
            key, _, _ = self.string()
            if key in result:
                raise self.error("duplicate key")
            self.skip_ws()
            if self.i >= self.n or self.text[self.i] != ":":
                raise self.error("expected colon")
            self.i += 1
            val, _, _ = self.value()
            result[key] = val
            self.skip_ws()
            if self.i >= self.n:
                raise self.error("unterminated object")
            c = self.text[self.i]
            if c == ",":
                self.i += 1
                self.skip_ws()
                if self.i < self.n and self.text[self.i] == "}":
                    self.i += 1
                    return result, s, self.i
                continue
            if c == "}":
                self.i += 1
                return result, s, self.i
            raise self.error("expected comma or brace")

    def arr(self) -> tuple[list, int, int]:
        s = self.i
        self.i += 1
        result: list[Any] = []
        self.skip_ws()
        if self.i < self.n and self.text[self.i] == "]":
            self.i += 1
            return result, s, self.i
        while True:
            val, _, _ = self.value()
            result.append(val)
            self.skip_ws()
            if self.i >= self.n:
                raise self.error("unterminated array")
            c = self.text[self.i]
            if c == ",":
                self.i += 1
                self.skip_ws()
                if self.i < self.n and self.text[self.i] == "]":
                    self.i += 1
                    return result, s, self.i
                continue
            if c == "]":
                self.i += 1
                return result, s, self.i
            raise self.error("expected comma or bracket")


def _parse_jsonc(text: str) -> tuple[Any, int, int]:
    parser = _Parser(text)
    parser.i = 1 if text.startswith("\ufeff") else 0
    return parser.parse()


def parse_jsonc(text: str) -> dict:
    """Parse a JSONC document and return its root object."""
    value, _, _ = _parse_jsonc(text)
    if not isinstance(value, dict):
        raise ConfigError("config root is not an object")
    return value


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------


def _read_regular(path: Path) -> str | None:
    """Return file text, or None if the path does not exist.

    Rejects symlinks and non-regular files. Never follows links. Preserves
    bytes exactly (no newline translation).
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError("cannot stat config") from exc
    if stat.S_ISLNK(st.st_mode):
        raise ConfigError("config path is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise ConfigError("config path is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ConfigError("cannot open config") from exc
    try:
        st2 = os.fstat(fd)
        if not stat.S_ISREG(st2.st_mode):
            raise ConfigError("config path is not a regular file")
        if (st2.st_dev, st2.st_ino) != (st.st_dev, st.st_ino):
            raise ConfigError("config path changed during read")
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError("cannot decode config") from exc


def _load_all(config_dir: Path) -> dict[str, tuple[str, Any, int, int]]:
    """Validate every existing config file. Returns name -> (text, value, start, end)."""
    loaded: dict[str, tuple[str, Any, int, int]] = {}
    for name in FILENAMES:
        text = _read_regular(config_dir / name)
        if text is None:
            continue
        value, start, end = _parse_jsonc(text)
        if not isinstance(value, dict):
            raise ConfigError("config root is not an object")
        loaded[name] = (text, value, start, end)
    return loaded


# ---------------------------------------------------------------------------
# Merge and patch
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def read_effective(config_dir: Path | str) -> dict:
    """Return the deep-merged effective config (arrays replaced)."""
    loaded = _load_all(Path(config_dir))
    result: dict = {}
    for name in FILENAMES:
        if name in loaded:
            result = _deep_merge(result, loaded[name][1])
    return result


def _apply_patch(value: Any, patch: dict, path: tuple[str, ...]) -> Any:
    """Return a new value with patch applied. Fails on wrong-typed intermediates."""
    if not isinstance(value, dict):
        raise ConfigError("wrong-typed intermediate")
    out = dict(value)
    for key, sub in patch.items():
        if isinstance(sub, dict):
            if key not in out:
                out[key] = _apply_patch({}, sub, path + (key,))
            else:
                existing = out[key]
                if not isinstance(existing, dict):
                    raise ConfigError("wrong-typed intermediate")
                out[key] = _apply_patch(existing, sub, path + (key,))
        else:
            out[key] = sub
    return out


def _render(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)


def _splice(text: str, start: int, end: int, replacement: str) -> str:
    return text[:start] + replacement + text[end:]


def _scan_object_members(
    text: str, offset: int
) -> tuple[dict[str, tuple[int, int]], int, int, int]:
    """Scan the root object of ``text`` (with BOM offset ``offset``).

    Returns (spans, open_brace_pos, close_brace_pos, last_member_end_pos).
    ``spans`` maps key -> (value_start, value_end) in absolute text offsets.
    ``last_member_end_pos`` is the position just after the last member's value
    (or just after the open brace if the object is empty).
    """
    parser = _Parser(text[offset:])
    parser.skip_ws()
    if parser.i >= parser.n or parser.text[parser.i] != "{":
        raise ConfigError("root is not an object")
    open_pos = parser.i + offset
    parser.i += 1
    spans: dict[str, tuple[int, int]] = {}
    last_end = open_pos + 1
    parser.skip_ws()
    if parser.i < parser.n and parser.text[parser.i] == "}":
        close_pos = parser.i + offset
        return spans, open_pos, close_pos, last_end
    while True:
        parser.skip_ws()
        key, _, _ = parser.string()
        parser.skip_ws()
        if parser.i >= parser.n or parser.text[parser.i] != ":":
            raise ConfigError("expected colon")
        parser.i += 1
        _, vs, ve = parser.value()
        spans[key] = (vs + offset, ve + offset)
        last_end = ve + offset
        parser.skip_ws()
        if parser.i >= parser.n:
            raise ConfigError("unterminated object")
        c = parser.text[parser.i]
        if c == ",":
            parser.i += 1
            parser.skip_ws()
            if parser.i < parser.n and parser.text[parser.i] == "}":
                close_pos = parser.i + offset
                return spans, open_pos, close_pos, last_end
            continue
        if c == "}":
            close_pos = parser.i + offset
            return spans, open_pos, close_pos, last_end
        raise ConfigError("expected comma or brace")


def _same(left, right):
    # Python considers True == 1, unlike JSON's distinct boolean/number types.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def _patch_text(text: str, value: dict, start: int, end: int, patch: dict) -> str:
    edits = []

    def collect(current, begin, delta):
        spans, _, close, last_end = _scan_object_members(text, begin)
        missing = {}
        for key, sub in delta.items():
            if key not in spans:
                missing[key] = sub
            elif isinstance(sub, dict):
                collect(current[key], spans[key][0], sub)
            elif not _same(current[key], sub):
                edits.append((*spans[key], _render(sub)))
        if missing:
            tail = _Parser(text)
            tail.i = last_end
            tail.skip_ws()
            if spans and text[tail.i] != ',':
                # Put the comma immediately after the value, before comments.
                edits.append((last_end, last_end, ','))
            newline = '\r\n' if '\r\n' in text else '\n'
            items = (json.dumps(k, ensure_ascii=False) + ': ' + _render(v) for k, v in missing.items())
            insertion = newline + '  ' + (',' + newline + '  ').join(items) + newline
            edits.append((close, close, insertion))

    _apply_patch(value, patch, ())  # Reject wrong-typed target intermediates.
    collect(value, start, patch)
    # At the same position apply later insertions first, so their final text
    # follows the earlier comma insertion rather than preceding it.
    for _, (begin, finish, replacement) in sorted(enumerate(edits), key=lambda row: (row[1][0], row[1][1], row[0]), reverse=True):
        text = _splice(text, begin, finish, replacement)
    return text


def _atomic_write(path: Path, data: str, verify) -> None:
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        verify()
        os.replace(tmp, path)
        try:
            dfd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Directory lock
# ---------------------------------------------------------------------------


class _DirLock:
    """Exclusive flock on the config directory for cooperating writers."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.fd: int | None = None

    def __enter__(self) -> "_DirLock":
        try:
            self.fd = os.open(self.directory, os.O_RDONLY)
        except OSError as exc:
            raise ConfigError("cannot open config directory") from exc
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except OSError as exc:
            os.close(self.fd)
            self.fd = None
            raise ConfigError("cannot lock config directory") from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


# ---------------------------------------------------------------------------
# Public update
# ---------------------------------------------------------------------------


def _snapshot(config_dir: Path) -> dict[str, bytes | None]:
    """Return a snapshot of raw bytes for every config file (None if absent)."""
    snap: dict[str, bytes | None] = {}
    for name in FILENAMES:
        p = config_dir / name
        try:
            st = p.lstat()
        except FileNotFoundError:
            snap[name] = None
            continue
        except OSError as exc:
            raise ConfigError("cannot stat config") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ConfigError("config path is not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(p, flags)
        except OSError as exc:
            raise ConfigError("cannot open config") from exc
        try:
            st2 = os.fstat(fd)
            if (st2.st_dev, st2.st_ino) != (st.st_dev, st.st_ino):
                raise ConfigError("config path changed during read")
            chunks = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            snap[name] = b"".join(chunks)
        finally:
            os.close(fd)
    return snap


def _check_config_dir(config_dir: Path) -> None:
    """Reject symlinked config directory or parent components."""
    # Walk parents up to root, rejecting symlinks.
    p = Path(os.path.abspath(config_dir))
    for parent in [p] + list(p.parents):
        try:
            pst = parent.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ConfigError("cannot stat config directory") from exc
        if stat.S_ISLNK(pst.st_mode):
            raise ConfigError("config directory parent is a symlink")
        if not stat.S_ISDIR(pst.st_mode):
            raise ConfigError("config directory component is not a directory")


def update_config(config_dir: Path | str, patch: dict, expected_effective=None) -> Path:
    """Patch one JSONC file; cooperative writers serialize on a directory lock.

    Rechecking all source bytes before replace catches intervening edits. An
    unrelated writer ignoring the lock can still race after that final check.
    """
    if not isinstance(patch, dict) or any(not isinstance(k, str) for k in patch):
        raise ConfigError("patch must be a JSON object")
    try:
        json.dumps(patch, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ConfigError("invalid patch") from exc
    directory = Path(config_dir)
    _check_config_dir(directory)
    directory.mkdir(parents=True, exist_ok=True)
    _check_config_dir(directory)
    with _DirLock(directory) as lock:
        before = _snapshot(directory)
        loaded = {}
        effective = {}
        for name, raw in before.items():
            if raw is None:
                continue
            text = raw.decode('utf-8')
            value, start, end = _parse_jsonc(text)
            if not isinstance(value, dict):
                raise ConfigError("config root is not an object")
            loaded[name] = (text, value, start, end)
            effective = _deep_merge(effective, value)
        if expected_effective is not None and not _same(effective, expected_effective):
            raise ConfigError("config changed before update")
        wanted_effective = _apply_patch(effective, patch, ())
        name = next((n for n in reversed(FILENAMES) if n in loaded), DEFAULT_TARGET)
        text, value, start, end = loaded.get(name, ('{}\n', {}, 0, 2))
        changed = _patch_text(text, value, start, end, patch)
        parsed = parse_jsonc(changed)
        actual_effective = {}
        for candidate in FILENAMES:
            if candidate == name:
                actual_effective = _deep_merge(actual_effective, parsed)
            elif candidate in loaded:
                actual_effective = _deep_merge(actual_effective, loaded[candidate][1])
        if not _same(actual_effective, wanted_effective):
            raise ConfigError("generated effective config mismatch")
        def verify():
            _check_config_dir(directory)
            current = directory.stat()
            locked = os.fstat(lock.fd)
            if (current.st_dev, current.st_ino) != (locked.st_dev, locked.st_ino):
                raise ConfigError("config directory changed")
            if _snapshot(directory) != before:
                raise ConfigError("concurrent config modification detected")
        target = directory / name
        if name in loaded and changed == text:
            verify()
            return target
        _atomic_write(target, changed, verify)
        return target


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: opencode_config.py <config_dir>", file=sys.stderr)
        return 2
    try:
        patch = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("invalid patch", file=sys.stderr)
        return 1
    if not isinstance(patch, dict):
        print("invalid patch", file=sys.stderr)
        return 1
    try:
        path = update_config(argv[1], patch)
    except (ConfigError, UnicodeError, ValueError, RecursionError):
        print("config update failed", file=sys.stderr)
        return 1
    except OSError:
        print("config update failed", file=sys.stderr)
        return 1
    print(path.name)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
