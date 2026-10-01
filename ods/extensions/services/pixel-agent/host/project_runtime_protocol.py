"""Bound npm/Python dependency inputs before isolated project acquisition.

This module grants no execution authority. The controller authorizes execution
and selects its installed runtime after validating immutable source bytes.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from urllib.parse import urlsplit


class InvalidProjectDependencies(ValueError):
    pass


class ProjectManifestError(InvalidProjectDependencies):
    """Closed input guidance; never transports source text or parser errors."""
    def __init__(self, code, file):
        allowed = {
            'manifest-missing': {'ods-project.json', 'package.json', 'package-lock.json', 'requirements.lock'},
            'manifest-encoding': {'ods-project.json', 'package.json', 'package-lock.json', 'requirements.lock'},
            'manifest-json': {'ods-project.json', 'package.json', 'package-lock.json'},
            'python-profile': {'ods-project.json'},
            'python-lock-sha256': {'requirements.lock'},
            'python-lock-pin': {'requirements.lock'},
            'python-lock-format': {'requirements.lock'},
            'python-entrypoints': {'ods-project.json'},
            'npm-lock-format': {'package-lock.json'},
        }
        if file not in allowed.get(code, set()):
            raise ValueError('unsupported project input issue')
        super().__init__(code)
        self.issue = {'code': code, 'file': file}


_NAME = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*\Z")
_INTEGRITY = re.compile(r"sha512-([A-Za-z0-9+/]+={0,2})\Z")
_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "optionalDependencies")

# This deliberately supports a narrow, inspectable subset of pip's requirements
# language. The runner additionally forces wheels and hash checking: a hash alone
# does not establish whether an index artifact is a wheel or a source archive.
MAX_PYTHON_LOCK_BYTES = 256 * 1024
MAX_PYTHON_PACKAGES = 512
_PYTHON_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_PYTHON_VERSION = r"(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
_PYTHON_REQUIREMENT = re.compile(r"(" + _PYTHON_NAME + r")==(" + _PYTHON_VERSION + r")\Z")
_PYTHON_HASH = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})\Z")


def validate_python_lock(text: str) -> dict:
    """Validate exact public-index pins with hashes; never evaluate pip syntax.

    Each requirement is ``name==version --hash=sha256:<digest>``. Multiple
    hashes and backslash-newline continuations are accepted, as are standalone
    comments. Empty/comment-only locks support standard-library-only projects.
    Transitive dependencies must also be pinned by the author; pip's required
    hash mode fails execution if the closure is incomplete.
    """
    if not isinstance(text, str):
        raise InvalidProjectDependencies("Python lock must be text")
    try:
        length = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise InvalidProjectDependencies("invalid Python lock encoding") from exc
    if length > MAX_PYTHON_LOCK_BYTES or any(
            ord(char) > 126 or (ord(char) < 32 and char not in "\r\n\t") for char in text):
        raise InvalidProjectDependencies("Python lock exceeds limits or contains invalid characters")
    names, pending = set(), ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            if pending:
                raise InvalidProjectDependencies("interrupted Python lock continuation")
            continue
        continued = line.endswith("\\")
        pending += (line[:-1].rstrip() if continued else line) + " "
        if continued:
            continue
        tokens = pending.split()
        pending = ""
        match = _PYTHON_REQUIREMENT.fullmatch(tokens[0])
        if not match or len(tokens[0]) > 256:
            raise ProjectManifestError('python-lock-pin', 'requirements.lock')
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in names or len(names) >= MAX_PYTHON_PACKAGES:
            raise InvalidProjectDependencies("duplicate or excessive Python dependencies")
        if not 1 <= len(tokens) - 1 <= 64 or any(not _PYTHON_HASH.fullmatch(token) for token in tokens[1:]):
            raise ProjectManifestError('python-lock-sha256', 'requirements.lock')
        names.add(name)
    if pending:
        raise InvalidProjectDependencies("unfinished Python lock continuation")
    return {"packageCount": len(names), "registry": "https://pypi.org/simple", "lifecycleScripts": False}


def select_project_runtime(files: dict) -> str:
    """Resolve a profile from captured bytes, never from caller tool arguments."""
    if not isinstance(files, dict):
        raise InvalidProjectDependencies("project files must be a snapshot mapping")

    def decode(name, limit):
        value = files.get(name)
        if not isinstance(value, bytes) or len(value) > limit:
            raise ProjectManifestError('manifest-missing', name)
        try:
            return value.decode("utf-8")
        except UnicodeError as exc:
            raise ProjectManifestError('manifest-encoding', name) from exc

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidProjectDependencies("duplicate project manifest key")
            result[key] = value
        return result

    def manifest(name, limit, **options):
        try:
            return json.loads(decode(name, limit), **options)
        except (json.JSONDecodeError, RecursionError, InvalidProjectDependencies) as exc:
            if isinstance(exc, ProjectManifestError):
                raise
            raise ProjectManifestError('manifest-json', name) from exc

    if "ods-project.json" not in files:
        package = manifest('package.json', 8 * 1024 * 1024)
        lock = manifest('package-lock.json', 8 * 1024 * 1024)
        try:
            validate_project_lock(package, lock)
        except InvalidProjectDependencies as exc:
            raise ProjectManifestError('npm-lock-format', 'package-lock.json') from exc
        return "npm"
    profile = manifest('ods-project.json', 8192, object_pairs_hook=unique_object)
    if profile != {"runtime": "python"}:
        raise ProjectManifestError('python-profile', 'ods-project.json')
    try:
        validate_python_lock(decode("requirements.lock", MAX_PYTHON_LOCK_BYTES))
    except ProjectManifestError:
        raise
    except InvalidProjectDependencies as exc:
        raise ProjectManifestError('python-lock-format', 'requirements.lock') from exc
    if not isinstance(files.get("main.py"), bytes) or not any(
            isinstance(name, str) and re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py", name)
            and isinstance(contents, bytes) for name, contents in files.items()):
        raise ProjectManifestError('python-entrypoints', 'ods-project.json')
    return "python"


def validate_project_lock(package: dict, lock: dict) -> dict:
    """Accept registry-only npm v3 locks; never silently weaken acquisition.

    Local/workspace/Git dependencies require another explicit acquisition path.
    Lifecycle hooks must still be disabled by the caller during acquisition.
    Build execution must use an isolated container without network access.
    """
    def reject(reason):
        raise InvalidProjectDependencies(reason)

    if not isinstance(package, dict) or not isinstance(lock, dict):
        reject("package and lock must be objects")
    if type(lock.get("lockfileVersion")) is not int or lock["lockfileVersion"] != 3:
        reject("npm lockfile version 3 required")
    if any(package.get(key) for key in ("workspaces", "bundledDependencies", "bundleDependencies", "overrides")):
        reject("workspace, bundled and override dependencies need explicit support")
    packages = lock.get("packages")
    if not isinstance(packages, dict) or not 1 <= len(packages) <= 4096:
        reject("invalid or excessive locked package count")
    root = packages.get("")
    if not isinstance(root, dict):
        reject("missing locked project root")
    for field in _DEPENDENCY_FIELDS:
        requested = package.get(field, {})
        if not isinstance(requested, dict) or root.get(field, {}) != requested:
            reject("package and lock dependencies differ")
        for name, version in requested.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                reject("invalid dependency name")
            if not isinstance(version, str) or not version or len(version) > 128:
                reject("invalid dependency version")
            # npm semver ranges only; no URLs, Git shorthands, aliases, tags,
            # filesystem references or workspace protocols in this lane.
            if not re.fullmatch(r"[0-9xXvV*~^<>=|. +\-]+", version):
                reject("dependency version must be a semver range")
    for path, entry in packages.items():
        if path == "":
            continue
        if not isinstance(path, str) or len(path) > 1024 or not isinstance(entry, dict):
            reject("invalid locked package")
        segments = path.split("/node_modules/")
        if not segments[0].startswith("node_modules/"):
            reject("locked package is outside node_modules")
        segments[0] = segments[0][len("node_modules/"):]
        if any(not _NAME.fullmatch(segment) for segment in segments):
            reject("invalid locked package path")
        if entry.get("link") or entry.get("inBundle"):
            reject("linked or bundled packages unsupported")
        resolved = entry.get("resolved")
        if not isinstance(resolved, str) or len(resolved) > 2048 or any(ord(c) <= 32 or ord(c) >= 127 for c in resolved):
            reject("missing registry URL")
        try:
            url = urlsplit(resolved)
            port = url.port
        except ValueError:
            reject("invalid registry URL")
        if (url.scheme != "https" or url.netloc != "registry.npmjs.org"
                or port is not None or url.query or url.fragment
                or not re.fullmatch(r"/[A-Za-z0-9@._/\-]+\.tgz", url.path)
                or any(part in ("", ".", "..") for part in url.path[1:].split("/"))):
            reject("only canonical public npm tarball URLs are supported")
        integrity = entry.get("integrity")
        match = _INTEGRITY.fullmatch(integrity) if isinstance(integrity, str) else None
        if not match:
            reject("sha512 integrity required")
        try:
            digest = base64.b64decode(match[1], validate=True)
        except binascii.Error:
            reject("invalid integrity encoding")
        if len(digest) != 64:
            reject("invalid sha512 digest length")
    return {"packageCount": len(packages) - 1, "registry": "https://registry.npmjs.org",
            "lifecycleScripts": False}
