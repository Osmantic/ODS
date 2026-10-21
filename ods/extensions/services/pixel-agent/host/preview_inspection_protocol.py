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
SELECT_OPTION_SCOPE = " Native single-select values were observed only for explicit select-option steps."
SELECT_SCOPE = SCOPE + SELECT_OPTION_SCOPE
SELECT_CAPABILITY = "native-single-select-v1"
FILL_CAPABILITY = "native-text-number-fill-v2"
FILL_SCOPE = " Native non-sensitive text and number fields were filled only with explicit synthetic values; number constraint flags are observations, not a claim of form validity. This does not test keyboard behavior or submit forms."
DOWNLOAD_CAPABILITY = "snapshot-download-v1"
DOWNLOAD_SCOPE = " Download verification is limited to one final step whose own click must produce matching snapshot bytes inside the capsule. A failed or unavailable receipt does not verify a download; no delivery to the user's computer, PDF quality, or ZIP content validation is established."
PRESS_CAPABILITY = "trusted-key-press-v1"
PRESS_SCOPE = " Trusted key presses were sent to the focused preview frame only for explicit press steps; behavior beyond the listed assertions, such as gameplay, is not verified."
# One unmodified key per press: no modifiers, chords, function keys or text.
# workspace-preview-inspect.mjs mirrors this list exactly as a schema enum.
PRESS_KEYS = (
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space", "Enter", "Escape", "Tab",
    *"abcdefghijklmnopqrstuvwxyz", *"0123456789",
)
PRESS_HOLD_MS = 100
MAX_PRESS_HOLD_MS = 1000
PAINT_CAPABILITY = "element-paint-v1"
PAINT_SCOPE = " Pixel paint was checked only for explicit assert-painted steps, as aggregate color statistics of each element's on-screen box; this detects a blank or single-color render, not image recognition or whether the intended content was drawn."
# Press and paint plans run only on a capsule labelled for them, so their scope
# need not match older capsules: its base does not deny the key presses and
# paint checks described after it, and beside presses the fill sentence speaks
# only of fill. workspace-preview-inspect.mjs mirrors both exactly.
STEP_SCOPE = "Only the listed steps were tested, each only as stated here. CSS layout visibility, normalized visible-text assertions and click dispatches do not test pixel paint; no step tests occlusion, clipping, a full accessibility audit, or overall functionality."
PRESS_FILL_SCOPE = " Native non-sensitive text and number fields were filled only with explicit synthetic values; number constraint flags are observations, not a claim of form validity. Fill sends no key events and does not submit forms."
# One viewport screenshot after the steps, only when the plugin asks for it
# (a model that accepts images). The directive sits beside the plan, never in
# it: plan_hash, scope and every receipt field stay those of the plan alone.
# workspace-preview-inspect.mjs and preview-screenshot.mjs mirror these bounds.
SCREENSHOT_CAPABILITY = "viewport-screenshot-v1"
MAX_SCREENSHOT_SIDE = 1200  # OpenClaw re-encodes larger tool images (DEFAULT_IMAGE_MAX_DIMENSION_PX)
MIN_SCREENSHOT_SIDE = 64
MAX_SCREENSHOT_PIXELS = 1024 * 1024
MIN_SCREENSHOT_PIXELS = 4096
# The plugin's Tool Search envelope must stay within its run guard's 256 KiB bound.
MAX_SCREENSHOT_BYTES = 128 * 1024
MIN_SCREENSHOT_BYTES = 16 * 1024
SCREENSHOT_OMISSIONS = ("capability_missing", "time_budget", "page_unavailable", "capture_failed", "too_large")
# The request line may carry the directive beside a plan of MAX_REQUEST bytes.
MAX_REQUEST_FRAME = MAX_REQUEST + 128
MAX_SCREENSHOT_RESULT = MAX_RESULT + 4 * ((MAX_SCREENSHOT_BYTES + 2) // 3) + 1024


def inspection_scope(request):
    pressed = any(s['action'] == 'press' for s in request['steps'])
    scope = STEP_SCOPE if pressed or any(s['action'] == 'assert-painted' for s in request['steps']) else SCOPE
    scope += SELECT_OPTION_SCOPE if any(s['action'] == 'select-option' for s in request['steps']) else ''
    scope += (PRESS_FILL_SCOPE if pressed else FILL_SCOPE) if any(s['action'] == 'fill' for s in request['steps']) else ''
    scope += PRESS_SCOPE if pressed else ''
    scope += PAINT_SCOPE if any(s['action'] == 'assert-painted' for s in request['steps']) else ''
    return scope + (DOWNLOAD_SCOPE if request['steps'][-1]['action'] == 'download' else '')


class Invalid(ValueError):
    pass


def read_only_wsl_docker(binary, info):
    """Verify Desktop's immutable ISO despite its CLI/directory 0775 modes."""
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
        mount_root = Path(target)
        for parent in binary.parents:
            parent_info = parent.lstat()
            # Desktop also ships directories such as usr/bin with mode 0775.
            # Accept that exact mode only within the verified ISO, where the
            # directory itself is read-only. Ancestors outside it still control
            # the mounted path and must retain their ordinary custody checks.
            read_only_image_parent = (
                (parent == mount_root or mount_root in parent.parents)
                and stat.S_IMODE(parent_info.st_mode) == 0o775
                and os.statvfs(parent).f_flag & os.ST_RDONLY
            )
            if (
                not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != 0
                or parent_info.st_gid != 0
                or (
                    parent_info.st_mode & 0o022
                    and not (
                        read_only_image_parent
                        or (
                            parent == Path("/mnt/wsl")
                            and parent_info.st_mode & stat.S_ISVTX
                        )
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


def plan_request(request):
    """The request without its screenshot directive: what plan_hash binds."""
    return {key: value for key, value in request.items() if key != "screenshot"}


def plan_hash(request):
    return hashlib.sha256(canonical(plan_request(request))).hexdigest()


def validate_screenshot_directive(value):
    exact(value, ("maxSide", "maxPixels", "maxBytes"))
    for key, low, high in (("maxSide", MIN_SCREENSHOT_SIDE, MAX_SCREENSHOT_SIDE),
                           ("maxPixels", MIN_SCREENSHOT_PIXELS, MAX_SCREENSHOT_PIXELS),
                           ("maxBytes", MIN_SCREENSHOT_BYTES, MAX_SCREENSHOT_BYTES)):
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise Invalid("invalid screenshot directive")
    return value


def jpeg_size(data):
    """Width and height from a baseline or progressive JPEG frame header."""
    if not isinstance(data, (bytes, bytearray)) or data[:3] != b"\xff\xd8\xff" or data[-2:] != b"\xff\xd9":
        raise Invalid("invalid jpeg")
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            raise Invalid("invalid jpeg")
        marker = data[position + 1]
        if marker == 0xFF:  # fill byte before a marker
            position += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # markers without a length
            position += 2
            continue
        if marker in (0xD9, 0xDA):  # end of image or scan data before any frame header
            break
        length = int.from_bytes(data[position + 2:position + 4], "big")
        if length < 2:
            raise Invalid("invalid jpeg")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if length < 7 or position + 9 > len(data):
                raise Invalid("invalid jpeg")
            height = int.from_bytes(data[position + 5:position + 7], "big")
            width = int.from_bytes(data[position + 7:position + 9], "big")
            if not width or not height:
                raise Invalid("invalid jpeg")
            return width, height
        position += 2 + length
    raise Invalid("invalid jpeg")


def validate_screenshot(value, directive):
    """The capsule's screenshot member: one bounded JPEG, or why it is omitted."""
    if isinstance(value, dict) and set(value) == {"status", "reason"}:
        if value["status"] != "omitted" or value["reason"] not in SCREENSHOT_OMISSIONS:
            raise Invalid("invalid screenshot")
        return value
    exact(value, ("status", "mimeType", "width", "height", "bytes", "sha256", "data"))
    if (
        value["status"] != "captured"
        or value["mimeType"] != "image/jpeg"
        or any(type(value[key]) is not int for key in ("width", "height", "bytes"))
        or not 1 <= value["width"] <= directive["maxSide"]
        or not 1 <= value["height"] <= directive["maxSide"]
        or value["width"] * value["height"] > directive["maxPixels"]
        or not 1 <= value["bytes"] <= directive["maxBytes"]
        or not isinstance(value["sha256"], str)
        or not re.fullmatch("[a-f0-9]{64}", value["sha256"])
        or not isinstance(value["data"], str)
        or len(value["data"]) != 4 * ((value["bytes"] + 2) // 3)
    ):
        raise Invalid("invalid screenshot")
    try:
        data = base64.b64decode(value["data"], validate=True)
    except ValueError:
        raise Invalid("invalid screenshot") from None
    if (
        len(data) != value["bytes"]
        or base64.b64encode(data).decode() != value["data"]
        or hashlib.sha256(data).hexdigest() != value["sha256"]
        or jpeg_size(data) != (value["width"], value["height"])
    ):
        raise Invalid("invalid screenshot")
    return value


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
    keys = ("schemaVersion", "action", "siteId", "sha256", "viewport", "steps")
    exact(value, (*keys, "screenshot") if isinstance(value, dict) and "screenshot" in value else keys)
    if "screenshot" in value:
        validate_screenshot_directive(value["screenshot"])
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
        select_step = isinstance(step, dict) and step.get("action") in ("select-option", "fill")
        download_step = isinstance(step, dict) and step.get("action") == "download"
        press_step = isinstance(step, dict) and step.get("action") == "press"
        extra = ("expectedText",) if text_step else ("value",) if select_step else ("path", "expectedBytes", "expectedSha256") if download_step else ("key", *(("holdMs",) if "holdMs" in step else ())) if press_step else ()
        exact(step, ("action", "locator", *extra))
        if step["action"] not in ("assert-visible", "assert-hidden", "assert-text", "click", "select-option", "fill", "download", "press", "assert-painted"):
            raise Invalid("invalid step")
        if press_step and (
            not isinstance(step["key"], str) or step["key"] not in PRESS_KEYS
            or ("holdMs" in step and (type(step["holdMs"]) is not int or not 0 <= step["holdMs"] <= MAX_PRESS_HOLD_MS))
        ):
            raise Invalid("invalid press step")
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
    if len(canonical(plan_request(value))) > MAX_REQUEST:
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
                not re.fullmatch(r"(?!__ods_)[A-Za-z0-9_\[][A-Za-z0-9._\[\]-]{0,127}", p)
                or p in (".", "..", "__pycache__")
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
