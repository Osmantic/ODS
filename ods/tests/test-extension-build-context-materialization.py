#!/usr/bin/env python3
"""Extension build-context materialization contract.

A one-click extension install builds its image from the recipe directory an
installer materialized on the host, not from this checkout. A file that a
Dockerfile reads from its build context but that an installer copy filter
leaves out fails the install with, for example::

    failed to compute cache key: ... "/README.md": not found

(mapshaper on a bootstrap install, 2026-09-25: get-ods.sh excluded ``*.md``
at every depth, so the recipe's own README never reached the library). For a
directory source, the same gap silently ships an incomplete image instead.

This contract enumerates every locally built image under extensions/ (curated
library recipes and core services), resolves the exact set of context files
each build reads (COPY/ADD sources and RUN bind mounts, honouring .dockerignore
and Dockerfile-specific ignore files), and replays every installer copy filter
against them:

* ``linux-bootstrap``: the get-ods.sh rsync into the install directory. For a
  bootstrap install this tree is also the extension library source.
* ``linux-source-copy``: the installers/phases/06-directories.sh rsync.
* ``macos-source-copy``: the installers/macos/install-macos.sh rsync.
* ``windows-source-copy``: the installers/windows/phases/06-directories.ps1
  robocopy.
* ``dashboard-library-copy``: dashboard-api's staged library copy, which skips
  symlinks and special files and refuses recipes over its size bound.

Filters are read from the installer sources, so a new exclude pattern is held
to this contract as soon as it lands. ``--report`` prints the full per-build
inventory. Stdlib plus PyYAML.

Run: python3 tests/test-extension-build-context-materialization.py [--report]
"""

from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = ROOT / "extensions"
LIBRARY = EXTENSIONS / "library" / "services"
LINUX_BOOTSTRAP = ROOT / "get-ods.sh"
LINUX_PHASE = ROOT / "installers" / "phases" / "06-directories.sh"
MACOS_INSTALLER = ROOT / "installers" / "macos" / "install-macos.sh"
WINDOWS_PHASE = ROOT / "installers" / "windows" / "phases" / "06-directories.ps1"
DASHBOARD_EXTENSIONS = (
    EXTENSIONS / "services" / "dashboard-api" / "routers" / "extensions.py"
)
REMOTE_CONTEXT = ("https://", "http://", "git://", "ssh://", "git@")
UNTRACKED_CACHES = {"__pycache__", ".pytest_cache", "node_modules"}


# ---------------------------------------------------------------- git view --

def tracked_files() -> set[str] | None:
    """Tracked files relative to ROOT, or None outside a Git checkout.

    A developer checkout can hold untracked caches (``__pycache__``) inside a
    build context. Installers copy a clean clone, so only tracked files are
    part of the contract when Git is available. ``git archive`` trees have no
    untracked content and fall back to the filesystem.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--", "."],
            capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    prefix = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--show-prefix"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    files = set()
    for item in result.stdout.decode("utf-8").split("\0"):
        if item:
            files.add(item[len(prefix):] if prefix and item.startswith(prefix) else item)
    return files


TRACKED = tracked_files()


def files_under(directory: Path) -> list[str]:
    """Regular files and links below directory, relative to it (POSIX)."""
    found = []
    for current, dirs, names in os.walk(directory, followlinks=False):
        base = Path(current)
        for name in dirs[:]:
            if (base / name).is_symlink():
                found.append((base / name).relative_to(directory).as_posix())
                dirs.remove(name)
        for name in names:
            found.append((base / name).relative_to(directory).as_posix())
    if TRACKED is not None:
        prefix = directory.relative_to(ROOT).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        found = [item for item in found if prefix + item in TRACKED]
    else:
        # Without Git, leave out the local caches running tests creates;
        # they are never product content.
        found = [item for item in found
                 if not UNTRACKED_CACHES & set(item.split("/")) and not item.endswith(".pyc")]
    return sorted(found)


# ----------------------------------------------------- .dockerignore rules --

def _docker_pattern_regex(pattern: str) -> re.Pattern[str]:
    """moby/patternmatcher's pattern compiler (Unix separator)."""
    out = "^"
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                i += 1
                if i + 1 < len(pattern) and pattern[i + 1] == "/":
                    i += 1
                if i + 1 >= len(pattern):
                    out += ".*"
                else:
                    out += "(.*/)?"
            else:
                out += "[^/]*"
        elif ch == "?":
            out += "[^/]"
        elif ch == "\\" and i + 1 < len(pattern):
            i += 1
            out += re.escape(pattern[i])
        elif ch in "[]":
            out += ch
        else:
            out += re.escape(ch)
        i += 1
    return re.compile(out + "$")


def parse_dockerignore(text: str) -> list[tuple[bool, re.Pattern[str]]]:
    rules = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        exclusion = line.startswith("!")
        if exclusion:
            line = line[1:].strip()
            if not line:
                continue
        line = posixpath.normpath(line)
        if len(line) > 1 and line.startswith("/"):
            line = line.lstrip("/")
        rules.append((exclusion, _docker_pattern_regex(line)))
    return rules


def dockerignored(path: str, rules: list[tuple[bool, re.Pattern[str]]]) -> bool:
    """patternmatcher.MatchesOrParentMatches: last matching rule wins."""
    matched = False
    parent = posixpath.dirname(path)
    parents = parent.split("/") if parent else []
    for exclusion, regex in rules:
        if exclusion and not matched:
            continue
        hit = bool(regex.match(path))
        if not hit:
            for index in range(len(parents)):
                if regex.match("/".join(parents[: index + 1])):
                    hit = True
                    break
        if hit:
            matched = not exclusion
    return matched


# ------------------------------------------------------- Dockerfile reader --

@dataclass
class ContextRead:
    instruction: str
    source: str


def _instructions(text: str) -> list[str]:
    """Logical Dockerfile instructions with heredoc bodies removed."""
    escape = "\\"
    lines = text.splitlines()
    for line in lines:
        directive = re.match(r"#\s*escape\s*=\s*(\S)", line)
        if directive:
            escape = directive.group(1)
            break
        if not re.match(r"#\s*[a-z]+\s*=", line):
            break
    result, buffer, index = [], "", 0
    while index < len(lines):
        line = lines[index]
        index += 1
        stripped = line.strip()
        if stripped.startswith("#") or (not buffer and not stripped):
            continue
        if line.rstrip().endswith(escape):
            buffer += line.rstrip()[:-1] + " "
            continue
        instruction = (buffer + line).strip()
        buffer = ""
        for dash, _quote, word in re.findall(
                r"(?<!<)<<(-?)\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\2", instruction):
            for end in range(index, len(lines)):
                candidate = lines[end].lstrip("\t") if dash else lines[end]
                if candidate == word:
                    index = end + 1
                    break
        result.append(instruction)
    return result


def _expand(value: str, variables: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(3)
        default = match.group(2)
        if name in variables:
            return variables[name]
        if default is not None:
            return default
        return match.group(0)
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)",
                  replace, value)


def context_reads(dockerfile_text: str, build_args: dict[str, str]) -> list[ContextRead]:
    """Every read from the primary build context, in instruction order."""
    variables: dict[str, str] = {}
    reads = []
    for instruction in _instructions(dockerfile_text):
        keyword, _, rest = instruction.partition(" ")
        keyword = keyword.upper()
        rest = rest.strip()
        if keyword == "ARG":
            for item in rest.split():
                name, sep, default = item.partition("=")
                if name in build_args:
                    variables[name] = build_args[name]
                elif sep:
                    variables[name] = default.strip("\"'")
            continue
        if keyword == "ENV":
            if "=" in rest.split(" ", 1)[0]:
                try:
                    items = shlex.split(rest)
                except ValueError:
                    items = rest.split()
                for item in items:
                    name, _, value = item.partition("=")
                    variables[name] = value
            else:
                name, _, value = rest.partition(" ")
                variables[name] = value.strip()
            continue
        if keyword in ("COPY", "ADD"):
            flags = []
            while rest.startswith("--"):
                flag, _, rest = rest.partition(" ")
                flags.append(flag)
                rest = rest.lstrip()
            if any(flag.startswith("--from=") for flag in flags):
                continue  # Another stage or image, not the build context.
            args = json.loads(rest) if rest.startswith("[") else rest.split()
            for source in args[:-1]:
                if source.startswith("<<") or source.startswith(REMOTE_CONTEXT):
                    continue
                reads.append(ContextRead(keyword, _expand(source, variables)))
            continue
        if keyword == "RUN":
            for mount in re.findall(r"--mount=(\S+)", rest):
                options = dict(
                    (item.split("=", 1) + [""])[:2] for item in mount.split(",")
                )
                if options.get("type", "bind") != "bind" or "from" in options:
                    continue
                source = options.get("source", options.get("src", "."))
                reads.append(ContextRead("RUN --mount", _expand(source, variables)))
    return reads


def _go_match(pattern: str, path: str) -> bool:
    regex = ""
    for ch in pattern:
        if ch == "*":
            regex += "[^/]*"
        elif ch == "?":
            regex += "[^/]"
        elif ch in "[]":
            regex += ch
        else:
            regex += re.escape(ch)
    return re.fullmatch(regex, path) is not None


def resolve_reads(reads: Iterable[ContextRead], context_files: list[str]) -> tuple[set[str], list[str]]:
    """Map context reads to the files they need. Returns (files, missing)."""
    needed: set[str] = set()
    missing: list[str] = []
    directories = {posixpath.dirname(item) for item in context_files}
    for item in list(directories):
        while item:
            item = posixpath.dirname(item)
            directories.add(item)
    for read in reads:
        source = read.source
        if "$" in source:
            missing.append(f"{read.instruction} {source}: unresolved variable in a context path")
            continue
        source = posixpath.normpath(source.lstrip("/")) if source.strip("/") else "."
        if source == ".":
            needed.update(context_files)
            continue
        if any(ch in source for ch in "*?["):
            roots = [path for path in list(context_files) + sorted(directories)
                     if path and _go_match(source, path)]
        else:
            roots = [source] if source in context_files or source in directories else []
        matched = set()
        for root in roots:
            if root in context_files:
                matched.add(root)
            else:
                matched.update(path for path in context_files if path.startswith(root + "/"))
        if not matched:
            missing.append(f"{read.instruction} {read.source}: not in the build context")
        needed.update(matched)
    return needed, missing


# ------------------------------------------------------------ builds found --

@dataclass
class Build:
    compose: Path
    service: str
    context: Path
    dockerfile: Path
    library: bool
    needed: set[str] = field(default_factory=set)  # relative to ROOT
    missing: list[str] = field(default_factory=list)
    reads: list[ContextRead] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.compose.relative_to(ROOT).as_posix()}:{self.service}"


def compose_files() -> list[Path]:
    found = []
    for path in sorted(EXTENSIONS.rglob("*")):
        name = path.name
        if not path.is_file() or "/tests/" in path.as_posix():
            continue
        if re.fullmatch(r"compose(?:[.-][A-Za-z0-9-]+)*\.ya?ml(?:\.disabled)?", name):
            found.append(path)
    found.extend(sorted(ROOT.glob("docker-compose*.yml")))
    return found


def discover_builds() -> list[Build]:
    builds = []
    for compose in compose_files():
        data = yaml.safe_load(compose.read_text(encoding="utf-8"))
        services = data.get("services") if isinstance(data, dict) else None
        if not isinstance(services, dict):
            continue
        library = LIBRARY in compose.parents
        # Library recipes are rewritten to an absolute context under the
        # installed recipe directory; every other Compose file is resolved
        # against the project directory (the install root).
        base = compose.parent if library else ROOT
        for name, service in services.items():
            build = service.get("build") if isinstance(service, dict) else None
            if build is None:
                continue
            if isinstance(build, str):
                build = {"context": build}
            context_value = str(build.get("context", "."))
            if context_value.startswith(REMOTE_CONTEXT):
                continue
            assert not build.get("additional_contexts"), (
                f"{compose}:{name}: additional_contexts need a materialization rule")
            context = (base / context_value).resolve()
            assert ROOT in (context, *context.parents), (
                f"{compose}:{name}: build context leaves the product tree")
            dockerfile = (context / str(build.get("dockerfile", "Dockerfile"))).resolve()
            unusable = None
            if library and compose.parent.resolve() not in (context, *context.parents):
                unusable = f"build context {context_value!r} leaves the recipe (install refuses it)"
            elif not context.is_dir():
                unusable = f"build context {context.relative_to(ROOT).as_posix()} does not exist"
            elif "dockerfile_inline" not in build and not dockerfile.is_file():
                unusable = f"Dockerfile {dockerfile.relative_to(ROOT).as_posix()} does not exist"
            if unusable:
                builds.append(Build(compose, name, context, dockerfile, library, missing=[unusable]))
                continue
            if "dockerfile_inline" in build:
                text = str(build["dockerfile_inline"])
                dockerfile = compose
            else:
                text = dockerfile.read_text(encoding="utf-8")
            args = build.get("args") or {}
            if isinstance(args, list):
                args = dict((item.split("=", 1) + [""])[:2] for item in args)
            args = {str(k): _expand(str(v), {}) for k, v in args.items() if v is not None}
            specific = dockerfile.with_name(dockerfile.name + ".dockerignore")
            ignore_file = specific if specific.is_file() else context / ".dockerignore"
            rules = parse_dockerignore(ignore_file.read_text(encoding="utf-8")) if ignore_file.is_file() else []
            files = [item for item in files_under(context) if not dockerignored(item, rules)]
            reads = context_reads(text, args)
            needed, missing = resolve_reads(reads, files)
            prefix = context.relative_to(ROOT.resolve()).as_posix()
            prefix = "" if prefix == "." else prefix + "/"
            rooted = {prefix + item for item in needed}
            # The build also needs its Dockerfile and ignore file on disk.
            rooted.add(dockerfile.resolve().relative_to(ROOT.resolve()).as_posix())
            if ignore_file.is_file():
                rooted.add(ignore_file.resolve().relative_to(ROOT.resolve()).as_posix())
            builds.append(Build(compose, name, context, dockerfile, library,
                                rooted, missing, reads))
    return builds


# ------------------------------------------------------ installer filters --

def _rsync_glob(pattern: str, path: str) -> bool:
    regex = ""
    index = 0
    while index < len(pattern):
        ch = pattern[index]
        if pattern.startswith("**", index):
            regex += ".*"
            index += 2
            continue
        if ch == "*":
            regex += "[^/]*"
        elif ch == "?":
            regex += "[^/]"
        elif ch in "[]":
            regex += ch
        else:
            regex += re.escape(ch)
        index += 1
    return re.fullmatch(regex, path) is not None


def _rsync_rule_matches(pattern: str, path: str, is_dir: bool) -> bool:
    """rsync(1) FILTER RULES pattern semantics for one path level."""
    if pattern.endswith("/"):
        if not is_dir:
            return False
        pattern = pattern[:-1]
    if pattern.startswith("/"):
        return _rsync_glob(pattern[1:], path)
    parts = path.split("/")
    if "/" in pattern or "**" in pattern:
        return any(_rsync_glob(pattern, "/".join(parts[start:])) for start in range(len(parts)))
    return _rsync_glob(pattern, parts[-1])


def rsync_filter(rules: list[tuple[str, str]]) -> Callable[[str], bool]:
    """Return drops(path): True when rsync would not transfer path.

    rsync applies the first matching rule at each directory level while it
    walks the tree, and an excluded directory is never descended.
    """
    def drops(path: str) -> bool:
        parts = path.split("/")
        for depth in range(1, len(parts) + 1):
            sub = "/".join(parts[:depth])
            is_dir = depth < len(parts)
            for kind, pattern in rules:
                if _rsync_rule_matches(pattern, sub, is_dir):
                    if kind == "exclude":
                        return True
                    break
        return False
    return drops


def _shell_filters(block: str) -> list[tuple[str, str]]:
    rules = []
    for kind, single, double, bare in re.findall(
            r"--(exclude|include)=(?:'([^']*)'|\"([^\"]*)\"|([^\s\\]+))", block):
        rules.append((kind, single or double or bare))
    return rules


def _bash_array(source: str, name: str) -> list[str]:
    match = re.search(rf"(?s)\b{re.escape(name)}=\(\s*(.*?)\)", source)
    assert match, f"missing Bash array {name}"
    return shlex.split(match.group(1), comments=True, posix=True)


def linux_bootstrap_rules() -> list[tuple[str, str]]:
    source = LINUX_BOOTSTRAP.read_text(encoding="utf-8")
    items = _bash_array(source, "_ods_bootstrap_copy_filters")
    rules = []
    for item in items:
        kind, _, pattern = item.partition("=")
        assert kind in ("--exclude", "--include") and pattern, f"unsupported rsync filter {item}"
        rules.append((kind[2:], pattern))
    assert re.search(r'rsync -a\s+"\$\{_ods_bootstrap_copy_filters\[@\]\}"\s*\\?\s*'
                     r'"\$TEMP_DIR/repo/ods/" "\$INSTALL_DIR/"', source), (
        "get-ods.sh must copy the product tree with _ods_bootstrap_copy_filters")
    return rules


def linux_phase_rules() -> list[tuple[str, str]]:
    source = LINUX_PHASE.read_text(encoding="utf-8")
    match = re.search(r'(?s)rsync -a --no-owner --no-group \\\r?\n(.*?)"\$SCRIPT_DIR/" "\$INSTALL_DIR/"', source)
    assert match, "installers/phases/06-directories.sh source rsync not found"
    rules = _shell_filters(match.group(1))
    assert rules, "no rsync filters parsed from phase 06"
    return rules


def macos_rules() -> list[tuple[str, str]]:
    source = MACOS_INSTALLER.read_text(encoding="utf-8")
    match = re.search(r'(?s)rsync -a --quiet \\\r?\n(.*?)"\$SOURCE_ROOT/" "\$INSTALL_DIR/"', source)
    assert match, "install-macos.sh source rsync not found"
    block = match.group(1)
    rules = _shell_filters(block)
    assert rules, "no rsync filters parsed from install-macos.sh"
    assert '"${_ods_dev_rsync_excludes[@]}"' in block
    assert '_ods_dev_rsync_excludes+=(--exclude="/${_ods_dev_path}/")' in source
    assert '_ods_dev_rsync_excludes+=(--exclude="/${_ods_dev_path}")' in source
    rules += [("exclude", f"/{item}/") for item in _bash_array(source, "_ods_dev_only_dirs")]
    rules += [("exclude", f"/{item}") for item in _bash_array(source, "_ods_dev_only_files")]
    return rules


def _powershell_array(source: str, name: str) -> list[str]:
    match = re.search(rf"(?s)\${re.escape(name)}\s*=\s*@\((.*?)\)", source)
    assert match, f"missing PowerShell array ${name}"
    return re.findall(r'"([^"]+)"', match.group(1))


def windows_filter() -> Callable[[str], bool]:
    """robocopy /XD and /XF: bare names match at any depth, paths are exact."""
    source = WINDOWS_PHASE.read_text(encoding="utf-8")
    xd = re.search(r'"/XD"((?:\s*,\s*"[^"]*")+)', source)
    xf = re.search(r'"/XF"((?:\s*,\s*"[^"]*")+)', source)
    assert xd and xf, "robocopy /XD and /XF lists not found"
    dir_names = re.findall(r'"([^"]*)"', xd.group(1))
    file_names = re.findall(r'"([^"]*)"', xf.group(1))
    assert "$robocopyArgs += @($devOnlyDirectories | ForEach-Object" in source
    assert "$robocopyArgs += @($devOnlyFiles | ForEach-Object" in source
    assert "Join-Path $sourceRoot $_" in source
    dir_paths = {item.lower() for item in _powershell_array(source, "devOnlyDirectories")}
    file_paths = {item.lower() for item in _powershell_array(source, "devOnlyFiles")}

    def drops(path: str) -> bool:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            name = parts[depth - 1].lower()
            if "/".join(parts[:depth]).lower() in dir_paths:
                return True
            if any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in dir_names):
                return True
        if path.lower() in file_paths:
            return True
        return any(fnmatch.fnmatchcase(parts[-1].lower(), pattern.lower()) for pattern in file_names)
    return drops


def dashboard_library_filter() -> Callable[[str], bool]:
    """dashboard-api _copytree_safe skips symlinks and special files."""
    source = DASHBOARD_EXTENSIONS.read_text(encoding="utf-8")
    assert "shutil.copytree(src, dst, ignore=_ignore_special)" in source
    for flag in ("S_ISLNK", "S_ISFIFO", "S_ISBLK", "S_ISCHR", "S_ISSOCK"):
        assert flag in source

    def drops(path: str) -> bool:
        current = ROOT
        for part in path.split("/"):
            current = current / part
            mode = os.lstat(current).st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                return True
        return False
    return drops


def library_size_limit() -> int:
    source = DASHBOARD_EXTENSIONS.read_text(encoding="utf-8")
    match = re.search(r"^_MAX_EXTENSION_BYTES\s*=\s*([0-9 *]+)", source, re.M)
    assert match, "_MAX_EXTENSION_BYTES not found"
    value = 1
    for factor in match.group(1).split("*"):
        value *= int(factor.strip())
    return value


def rsync_materializers() -> dict[str, list[tuple[str, str]]]:
    return {
        "linux-bootstrap": linux_bootstrap_rules(),
        "linux-source-copy": linux_phase_rules(),
        "macos-source-copy": macos_rules(),
    }


def materializers() -> dict[str, Callable[[str], bool]]:
    filters = {name: rsync_filter(rules) for name, rules in rsync_materializers().items()}
    filters["windows-source-copy"] = windows_filter()
    return filters


# --------------------------------------------------------------- contract --

def audit() -> tuple[list[Build], dict[str, dict[str, list[str]]], list[str]]:
    builds = discover_builds()
    filters = materializers()
    library_copy = dashboard_library_filter()
    dropped: dict[str, dict[str, list[str]]] = {}
    problems: list[str] = []
    for build in builds:
        per_build: dict[str, list[str]] = {}
        for name, drops in filters.items():
            lost = sorted(path for path in build.needed if drops(path))
            if lost:
                per_build[name] = lost
        if build.library:
            lost = sorted(path for path in build.needed if library_copy(path))
            if lost:
                per_build["dashboard-library-copy"] = lost
        if per_build:
            dropped[build.label] = per_build
        for item in build.missing:
            problems.append(f"{build.label}: {item}")
    limit = library_size_limit()
    for recipe in sorted(path for path in LIBRARY.iterdir() if path.is_dir()):
        size = sum((recipe / item).lstat().st_size for item in files_under(recipe))
        if size > limit:
            problems.append(f"{recipe.name}: recipe is {size} bytes; dashboard-api refuses more than {limit}")
    for label, per_build in dropped.items():
        for name, lost in per_build.items():
            problems.append(f"{label}: {name} drops {', '.join(lost)}")
    return builds, dropped, problems


def check_filter_emulation_against_rsync() -> str:
    """Execute the real rsync with each installer's filters and compare.

    The contract replays rsync's pattern rules in Python. On hosts with rsync
    this proves the replay against the tool itself on the full product tree.
    """
    rsync = shutil.which("rsync")
    if rsync is None:
        if os.environ.get("CI"):
            raise AssertionError("rsync is required in CI to verify the rsync filter replay")
        return "[SKIP] rsync not installed; rsync filter replay not cross-checked"
    candidates = sorted(TRACKED) if TRACKED is not None else files_under(ROOT)
    with tempfile.TemporaryDirectory() as empty:
        for name, rules in rsync_materializers().items():
            result = subprocess.run(
                [rsync, "-a", "--dry-run", "--out-format=%n",
                 *[f"--{kind}={pattern}" for kind, pattern in rules],
                 f"{ROOT}/", f"{empty}/target/"],
                capture_output=True, text=True, check=True,
            )
            transferred = {line for line in result.stdout.splitlines()
                           if line and not line.endswith("/")}
            drops = rsync_filter(rules)
            disagreements = [path for path in candidates
                             if (not drops(path)) != (path in transferred)]
            assert not disagreements, (
                f"{name} filter replay disagrees with rsync for: " + ", ".join(disagreements[:20]))
    return f"[PASS] rsync filter replay matches rsync on {len(candidates)} files for each installer"


def check_bootstrap_filters_on_fixture() -> str:
    """Materializer unit test: run get-ods.sh's filters over a fixture tree."""
    rules = linux_bootstrap_rules()
    drops = rsync_filter(rules)
    kept = [
        "extensions/library/services/demo/README.md",
        "extensions/library/services/demo/docs/index.html",
        "extensions/library/services/demo/examples/config.yaml",
        "extensions/library/services/demo/tests/fixture.json",
        "extensions/library/services/demo/.gitignore",
        "extensions/services/demo/README.md",
        "memory-shepherd/baselines/ods-agent-MEMORY.md",
        "LICENSE",
        "install.sh",
    ]
    removed = [
        "README.md", "CHANGELOG.md", "LICENSING.md", "tests/test-x.sh",
        "docs/guide.md", "examples/x.yaml", ".github/workflows/x.yml",
        ".shellcheckrc", "PSScriptAnalyzerSettings.psd1", "test-stack.sh",
        ".gitignore", "extensions/services/demo/__pycache__/x.cpython-312.pyc",
        "extensions/services/demo/x.pyc", "extensions/services/demo/node_modules/a/index.js",
        "extensions/services/demo/.pytest_cache/v/cache",
    ]
    replay_wrong = [path for path in kept if drops(path)] + [path for path in removed if not drops(path)]
    assert not replay_wrong, f"bootstrap filter policy wrong for: {replay_wrong}"
    rsync = shutil.which("rsync")
    if rsync is None:
        if os.environ.get("CI"):
            raise AssertionError("rsync is required in CI to execute the bootstrap filters")
        return "[PASS] bootstrap filter policy (replay only; rsync not installed)"
    with tempfile.TemporaryDirectory() as temp:
        source, target = Path(temp) / "src", Path(temp) / "dst"
        for path in kept + removed:
            (source / path).parent.mkdir(parents=True, exist_ok=True)
            (source / path).write_text(path, encoding="utf-8")
        subprocess.run([rsync, "-a", *[f"--{kind}={pattern}" for kind, pattern in rules],
                        f"{source}/", f"{target}/"], check=True)
        absent = [path for path in kept if not (target / path).is_file()]
        present = [path for path in removed if (target / path).exists()]
        assert not absent and not present, f"rsync kept {present}, dropped {absent}"
    return "[PASS] bootstrap rsync keeps nested recipe files and drops root development files"


def check_readers() -> str:
    """The contract's Dockerfile and .dockerignore readers on known inputs."""
    rules = parse_dockerignore("*\n!Dockerfile\n!README.md\n# comment\n")
    assert not dockerignored("README.md", rules) and dockerignored("notes.txt", rules)
    assert dockerignored("sub/README.md", rules)
    rules = parse_dockerignore("**/__pycache__\nsecrets/\n!secrets/public.pem\n")
    assert dockerignored("a/b/__pycache__/x.pyc", rules)
    assert dockerignored("secrets/key", rules) and not dockerignored("secrets/public.pem", rules)
    reads = context_reads(
        "# syntax=docker/dockerfile:1\n"
        "FROM alpine AS build\n"
        "ARG CONF=nginx.conf\n"
        "COPY --chown=1:1 --chmod=0644 a.txt b/ \\\n    /dst/\n"
        "COPY --from=build /x /y\n"
        'COPY --link ["with space.txt", "/dst/"]\n'
        "ADD https://example.com/x.tgz /tmp/\n"
        "COPY <<EOF /etc/inline\nCOPY not-an-instruction /x\nEOF\n"
        "RUN --mount=type=bind,source=scripts/build.sh,target=/b.sh sh /b.sh\n"
        "RUN --mount=type=cache,target=/root/.cache true\n"
        "COPY ${CONF} /etc/nginx/\n", {})
    assert [read.source for read in reads] == [
        "a.txt", "b/", "with space.txt", "scripts/build.sh", "nginx.conf"], reads
    needed, missing = resolve_reads(
        reads, ["a.txt", "b/c.txt", "b/d/e.txt", "scripts/build.sh", "nginx.conf"])
    assert needed == {"a.txt", "b/c.txt", "b/d/e.txt", "scripts/build.sh", "nginx.conf"}
    assert missing == ["COPY with space.txt: not in the build context"], missing
    needed, _ = resolve_reads([ContextRead("COPY", "*.conf")], ["x.conf", "sub/y.conf"])
    assert needed == {"x.conf"}
    return "[PASS] Dockerfile and .dockerignore readers"


def report(builds: list[Build], dropped: dict[str, dict[str, list[str]]]) -> None:
    for build in builds:
        kind = "library" if build.library else "core"
        state = "DROPPED" if build.label in dropped else ("MISSING" if build.missing else "ok")
        sources = " ".join(read.source for read in build.reads) or "-"
        print(f"{state:8} {kind:8} {build.label}  context-reads: {sources}")
        for name, lost in dropped.get(build.label, {}).items():
            print(f"         {name}: {', '.join(lost)}")
        for item in build.missing:
            print(f"         missing: {item}")


def main() -> int:
    print(check_readers())
    builds, dropped, problems = audit()
    if "--report" in sys.argv[1:]:
        report(builds, dropped)
    library = sum(1 for build in builds if build.library)
    assert library >= 90, f"expected the curated library builds, found {library}"
    # Discovery must see the regression's own build input.
    mapshaper = [build for build in builds
                 if build.label.endswith("mapshaper/compose.yaml:mapshaper")]
    assert mapshaper and "extensions/library/services/mapshaper/README.md" in mapshaper[0].needed
    print(check_bootstrap_filters_on_fixture())
    print(check_filter_emulation_against_rsync())
    if problems:
        print("[FAIL] extension build contexts lose files during install materialization:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"[PASS] {len(builds)} extension image builds ({library} library) keep every "
          "build-context file through all installer materializations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
