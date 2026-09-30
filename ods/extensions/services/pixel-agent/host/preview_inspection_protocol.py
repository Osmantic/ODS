"""Strict, bounded protocol shared by the trusted broker and isolated capsule."""

import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import unicodedata

KIND = "ods-pixel-preview-inspection"
MAX_REQUEST = 8192
MAX_BUNDLE = 24 * 1024 * 1024
MAX_RESULT = 32768
MAX_FILES = 128
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_STEPS = 12
CSP = (
    "default-src 'self' data: blob:; connect-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self'; font-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors http://localhost:* http://127.0.0.1:*"
)
SANDBOX = "allow-scripts allow-forms allow-downloads"
SCOPE = "Only the listed CSS layout visibility, normalized visible-text assertions and click dispatches were tested; not pixel paint, occlusion, clipping, a full accessibility audit, or overall functionality."
SELECT_SCOPE = SCOPE + " Native single-select values were observed only for explicit select-option steps."
SELECT_CAPABILITY = "native-single-select-v1"
DOWNLOAD_CAPABILITY = "snapshot-download-v1"
DOWNLOAD_SCOPE = " A single final download was captured only inside the capsule and matched the declared snapshot file bytes; not a download to the user's computer, PDF quality, or ZIP content validation."


def inspection_scope(request):
    scope = SELECT_SCOPE if any(s['action'] == 'select-option' for s in request['steps']) else SCOPE
    return scope + (DOWNLOAD_SCOPE if request['steps'][-1]['action'] == 'download' else '')


class Invalid(ValueError):
    pass


def read_only_wsl_docker(binary, info):
    """Desktop's ISO reports 0775, but cannot be written by its root group."""
    binary = Path(binary)
    if (
        str(binary) != "/mnt/wsl/docker-desktop/cli-tools/usr/bin/docker"
        or info.st_uid != 0
        or info.st_gid != 0
        or info.st_nlink != 1
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o775
    ):
        return False
    try:
        if (
            not os.statvfs(binary).f_flag & os.ST_RDONLY
            or "microsoft" not in platform.release().lower()
        ):
            return False
        mounts = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields = line.split()
            separator = fields.index("-")
            target = fields[4]
            if str(binary) == target or str(binary).startswith(
                target.rstrip("/") + "/"
            ):
                mounts.append(
                    (target, fields[5], fields[separator + 1], fields[separator + 3])
                )
        target, options, filesystem, super_options = max(
            mounts, key=lambda item: len(item[0])
        )
        if (
            target != "/mnt/wsl/docker-desktop/cli-tools"
            or filesystem != "iso9660"
            or "ro" not in options.split(",")
            or "ro" not in super_options.split(",")
        ):
            return False
        for parent in binary.parents:
            parent_info = parent.lstat()
            if (
                not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != 0
                or parent_info.st_gid != 0
                or (
                    parent_info.st_mode & 0o022
                    and not (
                        parent == Path("/mnt/wsl")
                        and parent_info.st_mode & stat.S_ISVTX
                    )
                )
            ):
                return False
    except (OSError, ValueError, IndexError):
        return False
    return True


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise Invalid("duplicate key")
            result[key] = value
        return result

    return json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(Invalid("nonfinite")),
    )


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise Invalid("invalid fields")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def plan_hash(request):
    return hashlib.sha256(canonical(request)).hexdigest()


def printable(value, chars, size):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= chars
        and not any(
            unicodedata.category(c).startswith("C")
            or unicodedata.category(c) in ("Zl", "Zp")
            for c in value
        )
        and len(value.encode("utf-8")) <= size
    )


def validate_request(value):
    exact(value, ("schemaVersion", "action", "siteId", "sha256", "viewport", "steps"))
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or value["action"] != "inspect"
    ):
        raise Invalid("invalid request")
    digest = value["sha256"]
    if (
        not isinstance(digest, str)
        or not re.fullmatch("[a-f0-9]{64}", digest)
        or value["siteId"] != "site-" + digest[:24]
    ):
        raise Invalid("invalid identity")
    exact(value["viewport"], ("width", "height"))
    if any(
        type(n) is not int or not 240 <= n <= 1920 for n in value["viewport"].values()
    ):
        raise Invalid("invalid viewport")
    steps = value["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise Invalid("invalid steps")
    for index, step in enumerate(steps):
        text_step = isinstance(step, dict) and step.get("action") == "assert-text"
        select_step = isinstance(step, dict) and step.get("action") == "select-option"
        download_step = isinstance(step, dict) and step.get("action") == "download"
        extra = ("expectedText",) if text_step else ("value",) if select_step else ("path", "expectedBytes", "expectedSha256") if download_step else ()
        exact(step, ("action", "locator", *extra))
        if step["action"] not in ("assert-visible", "assert-hidden", "assert-text", "click", "select-option", "download"):
            raise Invalid("invalid step")
        if download_step and (
            index != len(steps) - 1 or not isinstance(step['path'], str)
            or len(step['path']) > 512 or len(step['path'].split('/')) > 12
            or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', p) or p in ('.', '..') for p in step['path'].split('/'))
            or not step['path'].lower().endswith(('.pdf', '.zip'))
            or type(step['expectedBytes']) is not int or not 1 <= step['expectedBytes'] <= MAX_FILE
            or not isinstance(step['expectedSha256'], str) or not re.fullmatch('[a-f0-9]{64}', step['expectedSha256'])
        ):
            raise Invalid('invalid download step')
        if select_step and step['value'] != '' and not printable(step['value'], 256, 1024):
            raise Invalid('invalid option value')
        if text_step and (not printable(step['expectedText'], 256, 1024)
                          or ' '.join(step['expectedText'].split()) != step['expectedText']):
            raise Invalid('invalid expected text')
        locator = step["locator"]
        if not isinstance(locator, dict):
            raise Invalid("invalid locator")
        if set(locator) == {"selector"}:
            selector = locator["selector"]
            if (
                not printable(selector, 256, 1024)
                or ">>" in selector
                or re.match(r"^[A-Za-z_-]+=", selector)
            ):
                raise Invalid("invalid selector")
        else:
            exact(locator, ("role", "name", "exact"))
            if (
                locator["role"]
                not in (
                    "button",
                    "link",
                    "checkbox",
                    "radio",
                    "textbox",
                    "combobox",
                    "heading",
                    "tab",
                    "switch",
                )
                or locator["exact"] is not True
                or not printable(locator["name"], 120, 480)
            ):
                raise Invalid("invalid semantic locator")
    if len(canonical(value)) > MAX_REQUEST:
        raise Invalid("request too large")
    return value


def validate_bundle(bundle):
    exact(bundle, ("schemaVersion", "request", "files"))
    if type(bundle["schemaVersion"]) is not int or bundle["schemaVersion"] != 1:
        raise Invalid("invalid bundle")
    request = validate_request(bundle["request"])
    files = bundle["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise Invalid("invalid files")
    result, digest, total, previous = {}, hashlib.sha256(), 0, ""
    for entry in files:
        exact(entry, ("path", "base64"))
        name = entry["path"]
        if (
            not isinstance(name, str)
            or len(name) > 512
            or len(name.split("/")) > 12
            or name <= previous
            or any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", p)
                or p in (".", "..")
                for p in name.split("/")
            )
        ):
            raise Invalid("invalid file path")
        if (
            not isinstance(entry["base64"], str)
            or len(entry["base64"]) > (MAX_FILE + 2) // 3 * 4
        ):
            raise Invalid("invalid file bytes")
        try:
            data = base64.b64decode(entry["base64"], validate=True)
        except (ValueError, TypeError):
            raise Invalid("invalid file bytes") from None
        if not 1 <= len(data) <= MAX_FILE:
            raise Invalid("invalid file bytes")
        total += len(data)
        if total > MAX_TOTAL:
            raise Invalid("snapshot too large")
        path = name.encode()
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        result[name], previous = data, name
    if "index.html" not in result or digest.hexdigest() != request["sha256"]:
        raise Invalid("snapshot mismatch")
    download = request['steps'][-1]
    if download['action'] == 'download':
        data = result.get(download['path'])
        if data is None or len(data) != download['expectedBytes'] or hashlib.sha256(data).hexdigest() != download['expectedSha256']:
            raise Invalid('download snapshot mismatch')
    return request, result


def failure(code, request=None):
    return {
        "schemaVersion": 1,
        "kind": KIND,
        "status": "failed",
        "errorCode": code,
        **(
            {
                "siteId": request["siteId"],
                "sha256": request["sha256"],
                "planSha256": plan_hash(request),
            }
            if request
            else {}
        ),
        "scope": inspection_scope(request) if request else SCOPE,
    }
