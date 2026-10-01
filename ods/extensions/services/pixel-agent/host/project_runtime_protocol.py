"""Dependency input validation for the candidate isolated project runner.

This module grants no execution authority. It constrains an npm acquisition
request before a future controller launches the networked download stage.
"""
from __future__ import annotations

import base64
import binascii
import re
from urllib.parse import urlsplit


class InvalidProjectDependencies(ValueError):
    pass


_NAME = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*\Z")
_INTEGRITY = re.compile(r"sha512-([A-Za-z0-9+/]+={0,2})\Z")
_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "optionalDependencies")


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
