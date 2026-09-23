#!/usr/bin/env python3
"""Generate, validate, sign, verify, and manage constrained Pixel limb packs."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from typing import Any

try:
    import pwd
except ImportError:  # pragma: no cover - Windows development host
    pwd = None

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows development host
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - Linux deployment host
    msvcrt = None


SCHEMA_URL = "https://osmantic.com/pixel/schemas/limb-pack-v1.schema.json"
LOCAL_CAPABILITY_SCHEMA_URL = "https://osmantic.com/pixel/schemas/local-capability-pack-v1.schema.json"
OPERATIONS_ACTION_SCHEMA_URL = "https://osmantic.com/pixel/schemas/operations-action-pack-v1.schema.json"
FRONTIER_TASK_SCHEMA_URL = "https://osmantic.com/pixel/schemas/frontier-task-pack-v1.schema.json"
LOCK_NAME = "pixel-pack.lock.json"
SIGNATURE_NAME = f"{LOCK_NAME}.sig"
SIGNATURE_NAMESPACE = "pixel-limb-pack"
MINIMUM_OPENCLAW_PLUGIN_API = "2026.5.17"
DEFAULT_INSTALL_ROOT = Path("/opt/pixel-limb-packs")
DEFAULT_REGISTRY = Path("/etc/pixel-limb-packs/registry.json")
DEFAULT_PROJECTION_ROOT = Path("/var/lib/pixel-limb-packs")
DEFAULT_SYSTEMD_ROOT = Path("/etc/systemd/system")
DEFAULT_SYSTEMCTL = Path("/usr/bin/systemctl")
DEFAULT_PYTHON = Path("/usr/bin/python3")
DEFAULT_USERADD = Path("/usr/sbin/useradd")
DEFAULT_USERDEL = Path("/usr/sbin/userdel")
DEFAULT_SETFACL = Path("/usr/bin/setfacl")
MAX_FILES = 256
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
PACK_ID = re.compile(r"^[a-z][a-z0-9-]{1,17}$")
SEMVER = re.compile(r"^(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})$")
TOOL_NAME = re.compile(r"^pixel_[a-z0-9_]{2,63}$")
PROJECTION_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}\.json$")
POLICY_PACK_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
ACTION_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
TARGET_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
PARAMETER_NAME = re.compile(r"^[a-z][A-Za-z0-9_]{0,63}$")
PIXEL_HELPER = re.compile(r"^/usr/local/libexec/pixel-[a-z0-9][a-z0-9._-]{0,127}$")
IDENTITY = re.compile(r"^[A-Za-z0-9._@+-]{3,128}$")
RESERVED_PACK_IDS = {"limb", "gmail", "calendar", "social", "ops", "frontier"}
HTTPS_DESTINATION = re.compile(r"^https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.I),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b", re.I),
    re.compile(rb"\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*['\"]?[^\s'\"]{12,}", re.I),
)


class PackError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PackError(f"{label} has missing or unknown fields")
    return value


def bounded_object(value: Any, required: set[str], allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not required.issubset(value) or not set(value).issubset(allowed):
        raise PackError(f"{label} has missing or unknown fields")
    return value


def bounded_text(value: Any, label: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or "\x00" in value:
        raise PackError(f"{label} must be {minimum}..{maximum} characters")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise PackError(f"{label} contains control characters")
    return value


def unique_strings(value: Any, label: str, maximum: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(item, str) for item in value) or len(value) != len(set(value)):
        raise PackError(f"{label} must be a unique bounded string list")
    return value


def validate_authority(value: Any, label: str, *, gateway: bool) -> dict[str, Any]:
    authority = exact(value, {"network", "networkDestinations", "filesystemRead", "filesystemWrite", "credentials"}, label)
    network = authority["network"]
    destinations = unique_strings(authority["networkDestinations"], f"{label}.networkDestinations", 16)
    if network not in {"none", "declared-destinations"}:
        raise PackError(f"{label}.network is invalid")
    if network == "none" and destinations:
        raise PackError(f"{label} declares destinations while networking is disabled")
    if network == "declared-destinations" and (not destinations or any(not HTTPS_DESTINATION.fullmatch(item) for item in destinations)):
        raise PackError(f"{label} must enumerate bounded HTTPS destinations")
    reads = unique_strings(authority["filesystemRead"], f"{label}.filesystemRead", 32)
    writes = unique_strings(authority["filesystemWrite"], f"{label}.filesystemWrite", 8)
    credentials = unique_strings(authority["credentials"], f"{label}.credentials", 16)
    if not set(reads).issubset({"projection", "pack"}) or not set(writes).issubset({"projection"}):
        raise PackError(f"{label} filesystem authority is outside the pack boundary")
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", item) for item in credentials):
        raise PackError(f"{label} credential declarations are invalid")
    if gateway and (network != "none" or destinations or writes or credentials or set(reads) != {"projection", "pack"}):
        raise PackError("gateway authority must be fixed to read-only pack/projection access with no network or credentials")
    if not gateway and (network != "none" or destinations or credentials or set(reads) != {"pack", "projection"} or writes != ["projection"]):
        raise PackError("v1 service authority must be offline and credential-free with pack/projection reads and projection-only writes")
    return authority


def policy_identity(value: dict[str, Any], *, schema: str, kind: str, manifest: dict[str, Any], label: str) -> None:
    if value["$schema"] != schema or value["schemaVersion"] != 1 or value["kind"] != kind:
        raise PackError(f"{label} schema identity is unsupported")
    if not isinstance(value["id"], str) or not POLICY_PACK_ID.fullmatch(value["id"]):
        raise PackError(f"{label} id is invalid")
    if value["version"] != manifest["version"]:
        raise PackError(f"{label} version must match the containing limb pack")
    bounded_text(value["name"], f"{label} name", 1, 100)
    bounded_text(value["description"], f"{label} description", 1, 500)


def validate_local_capability_pack(value: Any, manifest: dict[str, Any], label: str) -> dict[str, Any]:
    value = exact(value, {
        "$schema", "schemaVersion", "kind", "id", "version", "name", "description",
        "tools", "classifications", "trust", "authority", "rawContentStored", "retentionDays",
    }, label)
    policy_identity(value, schema=LOCAL_CAPABILITY_SCHEMA_URL, kind="local-capability", manifest=manifest, label=label)
    tools = unique_strings(value["tools"], f"{label}.tools", 16)
    declared_tools = {tool["name"] for tool in manifest["tools"]}
    if not tools or not set(tools).issubset(declared_tools):
        raise PackError(f"{label}.tools must be a non-empty subset of the limb's signed tools")
    classifications = unique_strings(value["classifications"], f"{label}.classifications", 3)
    if not classifications or not set(classifications).issubset({"public", "internal-derived", "confidential"}):
        raise PackError(f"{label}.classifications are invalid")
    if value["trust"] != "untrusted-projection" or value["authority"] != "observe-only" or value["rawContentStored"] is not False:
        raise PackError(f"{label} must remain an untrusted observe-only projection with no raw content")
    if type(value["retentionDays"]) is not int or not 1 <= value["retentionDays"] <= manifest["retention"]["projectionDays"]:
        raise PackError(f"{label}.retentionDays exceeds the containing limb's retention boundary")
    return value


def validate_parameter_rules(value: Any, label: str) -> dict[str, Any]:
    value = exact(value, {"pattern", "maxLength"}, label)
    pattern = value["pattern"]
    if not isinstance(pattern, str) or not 3 <= len(pattern) <= 256 or not pattern.startswith("^") or not pattern.endswith("$") or any(ord(char) < 32 for char in pattern):
        raise PackError(f"{label}.pattern must be a bounded anchored linear-time expression")
    body = pattern[1:-1]
    index = 0
    previous_atom = ""
    group_depth = 0
    while index < len(body):
        character = body[index]
        if character == "\\":
            index += 1
            if index >= len(body) or body[index].isdigit():
                raise PackError(f"{label}.pattern contains an unsafe escape")
            previous_atom = "class" if body[index] in "dDsSwW" else "literal"
        elif character == "[":
            end = index + 1
            escaped = False
            while end < len(body):
                if not escaped and body[end] == "]":
                    break
                escaped = not escaped and body[end] == "\\"
                if body[end] != "\\":
                    escaped = False
                end += 1
            if end >= len(body) or end == index + 1:
                raise PackError(f"{label}.pattern contains an invalid character class")
            index = end
            previous_atom = "class"
        elif character == "(":
            if index + 1 < len(body) and body[index + 1] == "?":
                raise PackError(f"{label}.pattern contains an unsafe regex extension")
            group_depth += 1
            previous_atom = ""
        elif character == ")":
            if group_depth == 0:
                raise PackError(f"{label}.pattern contains an unmatched group")
            group_depth -= 1
            previous_atom = "group"
        elif character == "|":
            if group_depth == 0:
                raise PackError(f"{label}.pattern may only use alternatives inside a group")
            previous_atom = ""
        elif character in "*+?":
            if previous_atom != "class":
                raise PackError(f"{label}.pattern may only repeat a character class")
            previous_atom = "quantifier"
        elif character == "{":
            end = body.find("}", index + 1)
            if previous_atom != "class" or end < 0 or not re.fullmatch(r"[0-9]+(?:,[0-9]*)?", body[index + 1:end]):
                raise PackError(f"{label}.pattern contains an unsafe repetition")
            limits = body[index + 1:end].split(",", 1)
            minimum = int(limits[0])
            maximum = int(limits[1]) if len(limits) == 2 and limits[1] else 4096
            if minimum > maximum or maximum > 4096:
                raise PackError(f"{label}.pattern repetition is outside its safe range")
            index = end
            previous_atom = "quantifier"
        elif character in ".^$":
            raise PackError(f"{label}.pattern contains an unsafe metacharacter")
        else:
            previous_atom = "literal"
        index += 1
    if group_depth:
        raise PackError(f"{label}.pattern contains an unmatched group")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise PackError(f"{label}.pattern is invalid") from exc
    if type(value["maxLength"]) is not int or not 1 <= value["maxLength"] <= 4096:
        raise PackError(f"{label}.maxLength is invalid")
    return value


def validate_fixed_helper_argv(argv: Any, label: str) -> list[str]:
    if not isinstance(argv, list) or not 1 <= len(argv) <= 128 or any(not isinstance(item, str) or not item or "\x00" in item or len(item) > 16_384 for item in argv):
        raise PackError(f"{label} must be a bounded fixed argv array")
    executable = argv[0]
    if executable == "/usr/bin/sudo":
        helper = argv[2] if len(argv) >= 3 else ""
        if len(argv) < 3 or argv[1] != "--non-interactive" or not PIXEL_HELPER.fullmatch(helper):
            raise PackError(f"{label} sudo may invoke only a fixed Pixel helper non-interactively")
    elif not PIXEL_HELPER.fullmatch(executable):
        raise PackError(f"{label} executable must be a fixed /usr/local/libexec/pixel-* helper")
    if any(argument in {"-c", "--eval", "--exec"} for argument in argv[1:]):
        raise PackError(f"{label} contains a shell/evaluator escape")
    return argv


def validate_operations_action_pack(value: Any, manifest: dict[str, Any], label: str) -> dict[str, Any]:
    value = exact(value, {
        "$schema", "schemaVersion", "kind", "id", "version", "name", "description",
        "targetPlaceholder", "actions", "authorityGrants",
    }, label)
    policy_identity(value, schema=OPERATIONS_ACTION_SCHEMA_URL, kind="operations-action-pack", manifest=manifest, label=label)
    placeholder = value["targetPlaceholder"]
    if not isinstance(placeholder, str) or not TARGET_NAME.fullmatch(placeholder):
        raise PackError(f"{label}.targetPlaceholder is invalid")
    actions = value["actions"]
    if not isinstance(actions, dict) or not 1 <= len(actions) <= 64:
        raise PackError(f"{label}.actions must contain 1..64 actions")
    namespace = f"{manifest['id']}."
    effect_for_tier = {"read": "observe", "staging": "stage", "managed": "manage", "change": "change"}
    required = {"description", "tier", "effect", "defaultAuthority", "idempotent", "reversible", "targets", "argv"}
    allowed = required | {"rollbackAction", "verificationAction", "parameters", "cwd", "timeoutSeconds", "exclusiveTarget", "isolation"}
    for name, action in actions.items():
        if not isinstance(name, str) or not ACTION_NAME.fullmatch(name) or not name.startswith(namespace):
            raise PackError(f"{label} action names must stay inside the {namespace} namespace")
        action = bounded_object(action, required, allowed, f"{label}.actions.{name}")
        bounded_text(action["description"], f"{label}.actions.{name}.description", 1, 500)
        tier = action["tier"]
        if tier not in effect_for_tier or action["effect"] != effect_for_tier[tier]:
            raise PackError(f"{label}.actions.{name} has an invalid tier/effect pair")
        if action["defaultAuthority"] not in {"disabled", "observe", "propose"} or (tier != "read" and action["defaultAuthority"] == "observe"):
            raise PackError(f"{label}.actions.{name} has invalid default authority")
        if type(action["idempotent"]) is not bool or type(action["reversible"]) is not bool:
            raise PackError(f"{label}.actions.{name} requires explicit boolean safety properties")
        if action["targets"] != [placeholder]:
            raise PackError(f"{label}.actions.{name} must target only the private mapping placeholder")
        argv = validate_fixed_helper_argv(action["argv"], f"{label}.actions.{name}.argv")
        parameters = action.get("parameters", {})
        if not isinstance(parameters, dict) or len(parameters) > 32 or any(not isinstance(item, str) or not PARAMETER_NAME.fullmatch(item) for item in parameters):
            raise PackError(f"{label}.actions.{name}.parameters are invalid")
        for parameter, rules in parameters.items():
            validate_parameter_rules(rules, f"{label}.actions.{name}.parameters.{parameter}")
        cwd = action.get("cwd", "/var/lib/pixel-runner/jobs")
        if not isinstance(cwd, str) or not (cwd == "/var/lib/pixel-runner/jobs" or cwd.startswith("/var/lib/pixel-runner/jobs/")) or ".." in PurePosixPath(cwd).parts or "\x00" in cwd:
            raise PackError(f"{label}.actions.{name}.cwd must stay below the runner job root")
        placeholders = set()
        for argument in [*argv, cwd]:
            placeholders.update(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", argument))
        if placeholders != set(parameters):
            raise PackError(f"{label}.actions.{name} parameters and placeholders must match exactly")
        if type(action.get("timeoutSeconds", 60)) is not int or not 1 <= action.get("timeoutSeconds", 60) <= 86_400:
            raise PackError(f"{label}.actions.{name}.timeoutSeconds is invalid")
        if type(action.get("exclusiveTarget", False)) is not bool or action.get("isolation", "none") not in {"none", "dedicated-runner", "ephemeral"}:
            raise PackError(f"{label}.actions.{name} isolation metadata is invalid")
        if tier != "read" and action.get("isolation", "none") == "none":
            raise PackError(f"{label}.actions.{name} state-changing work requires declared runner isolation")
        for field in ("rollbackAction", "verificationAction"):
            if field in action and (not isinstance(action[field], str) or action[field] not in actions):
                raise PackError(f"{label}.actions.{name} references an unknown {field}")
        if tier in {"managed", "change"} and (
            not action["reversible"] or "verificationAction" not in action
            or ("rollbackAction" not in action and not name.endswith(".rollback"))
        ):
            raise PackError(f"{label}.actions.{name} managed/change actions require reversal and verification")
    for name, action in actions.items():
        verification = action.get("verificationAction")
        rollback = action.get("rollbackAction")
        if verification and actions[verification]["tier"] not in {"read", "staging"}:
            raise PackError(f"{label}.actions.{name} verification must be read or staging")
        if rollback and actions[rollback]["tier"] not in {"managed", "change"}:
            raise PackError(f"{label}.actions.{name} rollback must be managed or change")
        for reference in (verification, rollback):
            if reference and not set(actions[reference].get("parameters", {})).issubset(action.get("parameters", {})):
                raise PackError(f"{label}.actions.{name} rollback/verification parameters are incompatible")
    grants = value["authorityGrants"]
    if not isinstance(grants, list) or len(grants) > 32:
        raise PackError(f"{label}.authorityGrants must be a bounded list")
    grant_ids: set[str] = set()
    grant_required = {"id", "level", "actions", "targets", "tiers", "environments", "maxExecutions", "windowSeconds", "maxConcurrent", "maxRuntimeSeconds", "maxFailures"}
    grant_allowed = grant_required | {"targetLabels", "parameterConstraints", "maxOutputBytes", "maxArtifactBytes"}
    for index, grant in enumerate(grants):
        grant = bounded_object(grant, grant_required, grant_allowed, f"{label}.authorityGrants[{index}]")
        grant_id = grant["id"]
        if not isinstance(grant_id, str) or not ACTION_NAME.fullmatch(grant_id) or not grant_id.startswith(namespace) or grant_id in grant_ids or grant["level"] != "bounded-auto":
            raise PackError(f"{label}.authorityGrants[{index}] identity or level is invalid")
        grant_ids.add(grant_id)
        grant_actions = unique_strings(grant["actions"], f"{label}.authorityGrants[{index}].actions", 64)
        if not grant_actions or not set(grant_actions).issubset(actions):
            raise PackError(f"{label}.authorityGrants[{index}] references unknown actions")
        if grant["targets"] != [placeholder]:
            raise PackError(f"{label}.authorityGrants[{index}] must use only the private target placeholder")
        tiers = unique_strings(grant["tiers"], f"{label}.authorityGrants[{index}].tiers", 3)
        if not tiers or not set(tiers).issubset({"read", "staging", "managed"}):
            raise PackError(f"{label}.authorityGrants[{index}] cannot grant change, break-glass, or unknown tiers")
        environments = unique_strings(grant["environments"], f"{label}.authorityGrants[{index}].environments", 4)
        if not environments or not set(environments).issubset({"development", "test", "staging", "lab"}):
            raise PackError(f"{label}.authorityGrants[{index}] cannot grant production or unclassified authority")
        if not any(actions[action_name]["tier"] in tiers for action_name in grant_actions):
            raise PackError(f"{label}.authorityGrants[{index}] has no action matching its granted tiers")
        for field, low, high in (("maxExecutions", 1, 100000), ("windowSeconds", 60, 31536000), ("maxConcurrent", 1, 32), ("maxRuntimeSeconds", 1, 86400), ("maxFailures", 1, 10000)):
            if type(grant[field]) is not int or not low <= grant[field] <= high:
                raise PackError(f"{label}.authorityGrants[{index}].{field} is invalid")
        for field, low, high in (("maxOutputBytes", 1024, 16 * 1024 * 1024), ("maxArtifactBytes", 1, 2 * 1024 * 1024 * 1024)):
            if field in grant and (type(grant[field]) is not int or not low <= grant[field] <= high):
                raise PackError(f"{label}.authorityGrants[{index}].{field} is invalid")
        labels = unique_strings(grant.get("targetLabels", []), f"{label}.authorityGrants[{index}].targetLabels", 32)
        if any(not TARGET_NAME.fullmatch(item) for item in labels):
            raise PackError(f"{label}.authorityGrants[{index}].targetLabels are invalid")
        constraints = grant.get("parameterConstraints", {})
        if not isinstance(constraints, dict) or any(not PARAMETER_NAME.fullmatch(str(name)) for name in constraints):
            raise PackError(f"{label}.authorityGrants[{index}].parameterConstraints are invalid")
        known_parameters = set().union(*(set(actions[action_name].get("parameters", {})) for action_name in grant_actions))
        if not set(constraints).issubset(known_parameters):
            raise PackError(f"{label}.authorityGrants[{index}].parameterConstraints reference unknown parameters")
        for parameter, rules in constraints.items():
            if not isinstance(rules, dict) or not rules or not set(rules).issubset({"values", "pattern"}):
                raise PackError(f"{label}.authorityGrants[{index}] constraint {parameter} is invalid")
            if "values" in rules:
                values = rules["values"]
                if not isinstance(values, list) or not 1 <= len(values) <= 100 or len(values) != len({str(item) for item in values}) or any(isinstance(item, bool) or not isinstance(item, (str, int)) or "\x00" in str(item) or len(str(item)) > 4096 for item in values):
                    raise PackError(f"{label}.authorityGrants[{index}] constraint {parameter} values are invalid")
            if "pattern" in rules:
                validate_parameter_rules({"pattern": rules["pattern"], "maxLength": 4096}, f"{label}.authorityGrants[{index}].parameterConstraints.{parameter}")
        if "managed" in tiers:
            for action_name in grant_actions:
                if set(constraints) != set(actions[action_name].get("parameters", {})):
                    raise PackError(f"{label}.authorityGrants[{index}] must constrain every managed-action parameter")
    return value


def validate_frontier_task_pack(value: Any, manifest: dict[str, Any], label: str) -> dict[str, Any]:
    value = exact(value, {
        "$schema", "schemaVersion", "kind", "id", "version", "name", "description",
        "mode", "taskClass", "localTools", "policy",
    }, label)
    policy_identity(value, schema=FRONTIER_TASK_SCHEMA_URL, kind="frontier-task-pack", manifest=manifest, label=label)
    if value["mode"] != "restrict" or value["taskClass"] not in {"plan_review", "failure_triage"}:
        raise PackError(f"{label} must restrict one supported typed Frontier task")
    local_tools = unique_strings(value["localTools"], f"{label}.localTools", 16)
    declared_tools = {tool["name"] for tool in manifest["tools"]}
    if not local_tools or not set(local_tools).issubset(declared_tools):
        raise PackError(f"{label}.localTools must be a non-empty subset of the limb's signed tools")
    policy = exact(value["policy"], {"enabled", "allowedClassifications", "maxInputTokens", "maxOutputTokens", "rehydrate"}, f"{label}.policy")
    if type(policy["enabled"]) is not bool or type(policy["rehydrate"]) is not bool:
        raise PackError(f"{label}.policy booleans are invalid")
    classifications = unique_strings(policy["allowedClassifications"], f"{label}.policy.allowedClassifications", 3)
    if not classifications or not set(classifications).issubset({"public", "internal-derived", "confidential"}):
        raise PackError(f"{label}.policy.allowedClassifications are invalid")
    if type(policy["maxInputTokens"]) is not int or not 128 <= policy["maxInputTokens"] <= 262_144:
        raise PackError(f"{label}.policy.maxInputTokens is invalid")
    if type(policy["maxOutputTokens"]) is not int or not 64 <= policy["maxOutputTokens"] <= 8192:
        raise PackError(f"{label}.policy.maxOutputTokens is invalid")
    return value


def validate_policy_packs(root: Path, manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    validators = {
        "localCapabilities": validate_local_capability_pack,
        "operationsActionPacks": validate_operations_action_pack,
        "frontierTaskPacks": validate_frontier_task_pack,
    }
    result: dict[str, list[dict[str, Any]]] = {name: [] for name in validators}
    seen_paths: set[str] = set()
    seen_ids: set[tuple[str, str]] = set()
    frontier_tasks: set[str] = set()
    for category, validator in validators.items():
        for relative in manifest["extensions"][category]:
            if relative in seen_paths:
                raise PackError(f"policy pack path is declared more than once: {relative}")
            seen_paths.add(relative)
            value, payload = read_json(root / Path(*PurePosixPath(relative).parts), 256 * 1024, f"{category} policy pack {relative}")
            validated = validator(value, manifest, f"{category} policy pack {relative}")
            if payload != (json.dumps(validated, indent=2, ensure_ascii=False) + "\n").encode("utf-8"):
                raise PackError(f"{category} policy pack {relative} must use canonical two-space generated JSON formatting")
            if relative != f"packs/{validated['id']}.json":
                raise PackError(f"{category} policy pack path must match its declared id")
            identity = (category, validated["id"])
            if identity in seen_ids:
                raise PackError(f"{category} contains a duplicate policy pack id")
            seen_ids.add(identity)
            if category == "frontierTaskPacks":
                if validated["taskClass"] in frontier_tasks:
                    raise PackError("Frontier task restrictions may be declared only once per task class")
                frontier_tasks.add(validated["taskClass"])
            result[category].append(validated)
    return result


def policy_pack_counts(manifest: dict[str, Any]) -> dict[str, int]:
    return {
        "local": len(manifest["extensions"]["localCapabilities"]),
        "operations": len(manifest["extensions"]["operationsActionPacks"]),
        "frontier": len(manifest["extensions"]["frontierTaskPacks"]),
    }


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = exact(value, {
        "$schema", "schemaVersion", "kind", "id", "version", "name", "description",
        "compatibility", "tools", "authority", "service", "retention", "extensions", "migrations",
    }, "limb manifest")
    if manifest["$schema"] != SCHEMA_URL or manifest["schemaVersion"] != 1 or manifest["kind"] != "projection-limb":
        raise PackError("limb manifest schema identity is unsupported")
    pack_id = manifest["id"]
    if not isinstance(pack_id, str) or not PACK_ID.fullmatch(pack_id):
        raise PackError("pack id must be 2..18 lowercase letters, digits, or hyphens")
    if pack_id.split("-", 1)[0] in RESERVED_PACK_IDS:
        raise PackError("pack id collides with a Pixel-managed tool namespace")
    if not isinstance(manifest["version"], str) or not SEMVER.fullmatch(manifest["version"]):
        raise PackError("pack version must be semantic x.y.z")
    bounded_text(manifest["name"], "pack name", 1, 100)
    bounded_text(manifest["description"], "pack description", 1, 500)
    compatibility = exact(manifest["compatibility"], {"pixelMajor", "minimumPixel", "minimumOpenClawPluginApi"}, "compatibility")
    if compatibility["pixelMajor"] != 3 or not isinstance(compatibility["minimumPixel"], str) or not re.fullmatch(r"3\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})", compatibility["minimumPixel"]):
        raise PackError("pack compatibility must target Pixel 3 with an exact minimum version")
    if compatibility["minimumOpenClawPluginApi"] != MINIMUM_OPENCLAW_PLUGIN_API:
        raise PackError("pack compatibility uses an unsupported OpenClaw plugin API floor")
    tools = manifest["tools"]
    if not isinstance(tools, list) or not 1 <= len(tools) <= 16:
        raise PackError("pack must declare 1..16 projection tools")
    tool_names: set[str] = set()
    projections: set[str] = set()
    prefix = f"pixel_{pack_id.replace('-', '_')}_"
    for index, item in enumerate(tools):
        tool = exact(item, {"name", "description", "projection", "maxBytes"}, f"tools[{index}]")
        if not isinstance(tool["name"], str) or not TOOL_NAME.fullmatch(tool["name"]) or not tool["name"].startswith(prefix) or tool["name"] in tool_names:
            raise PackError(f"tools[{index}].name is invalid, duplicated, or outside the pack namespace")
        tool_names.add(tool["name"])
        bounded_text(tool["description"], f"tools[{index}].description", 1, 500)
        if not isinstance(tool["projection"], str) or not PROJECTION_NAME.fullmatch(tool["projection"]) or tool["projection"] in projections:
            raise PackError(f"tools[{index}].projection is invalid or duplicated")
        projections.add(tool["projection"])
        if type(tool["maxBytes"]) is not int or not 1024 <= tool["maxBytes"] <= 1024 * 1024:
            raise PackError(f"tools[{index}].maxBytes is outside 1 KiB..1 MiB")
    authority = exact(manifest["authority"], {"gateway", "service"}, "authority")
    validate_authority(authority["gateway"], "authority.gateway", gateway=True)
    validate_authority(authority["service"], "authority.service", gateway=False)
    service = exact(manifest["service"], {"identity", "entrypoint", "stateDirectory", "scheduleSeconds"}, "service")
    expected_identity = f"pixel-limb-{pack_id}"
    if service["identity"] != expected_identity or service["stateDirectory"] != expected_identity or service["entrypoint"] != "service/service.py":
        raise PackError("service identity, state directory, or entrypoint escaped the generated pack boundary")
    if type(service["scheduleSeconds"]) is not int or not 60 <= service["scheduleSeconds"] <= 86400:
        raise PackError("service schedule must be 60..86400 seconds")
    retention = exact(manifest["retention"], {"projectionDays", "rawContentStored"}, "retention")
    if type(retention["projectionDays"]) is not int or not 1 <= retention["projectionDays"] <= 365 or retention["rawContentStored"] is not False:
        raise PackError("retention must be 1..365 days with rawContentStored=false")
    extensions = exact(manifest["extensions"], {"localCapabilities", "operationsActionPacks", "frontierTaskPacks"}, "extensions")
    for name, entries in extensions.items():
        values = unique_strings(entries, f"extensions.{name}", 16)
        if any(not re.fullmatch(r"packs/[a-z][a-z0-9-]{0,62}\.json", item) for item in values):
            raise PackError(f"extensions.{name} contains an unsafe pack path")
    migrations = exact(manifest["migrations"], {"fromVersions", "stateSchemaVersion"}, "migrations")
    versions = unique_strings(migrations["fromVersions"], "migrations.fromVersions", 32)
    if any(not SEMVER.fullmatch(item) for item in versions) or type(migrations["stateSchemaVersion"]) is not int or not 1 <= migrations["stateSchemaVersion"] <= 1_000_000:
        raise PackError("migration contract is invalid")
    return manifest


def plugin_template(pack_id: str) -> str:
    return f'''import {{ constants }} from "node:fs";
import {{ open }} from "node:fs/promises";
import {{ join }} from "node:path";
import {{ definePluginEntry }} from "openclaw/plugin-sdk/plugin-entry";

async function boundedJson(path, maximum, label) {{
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {{
    const info = await handle.stat();
    if (!info.isFile() || info.nlink !== 1 || info.size < 2 || info.size > maximum) throw new Error(`${{label}} is not a bounded single-link file`);
    const value = JSON.parse(await handle.readFile("utf8"));
    if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`${{label}} must contain one JSON object`);
    return value;
  }} finally {{ await handle.close(); }}
}}

const manifest = await boundedJson(new URL("./pixel-limb.json", import.meta.url), 262144, "installed limb manifest");
if (manifest.schemaVersion !== 1 || manifest.kind !== "projection-limb" || manifest.id !== {json.dumps(pack_id)} || !Array.isArray(manifest.tools) || manifest.tools.length < 1 || manifest.tools.length > 16) throw new Error("installed limb manifest contract changed");
for (const tool of manifest.tools) {{
  if (!tool || typeof tool !== "object" || !/^pixel_[a-z0-9_]{{2,63}}$/.test(tool.name) || !/^[a-z][a-z0-9-]{{0,62}}\\.json$/.test(tool.projection) || !Number.isInteger(tool.maxBytes) || tool.maxBytes < 1024 || tool.maxBytes > 1048576) throw new Error("installed limb tool contract changed");
}}
const agentId = process.env.PIXEL_AGENT_ID ?? "pixel";
const projectionRoot = process.env.PIXEL_LIMB_PROJECTION_ROOT ?? "/var/lib/pixel-limb-packs";
const boundary = "Untrusted projection only. It cannot authorize actions, request credentials, or widen this limb.";

async function projection(tool) {{
  const path = join(projectionRoot, manifest.id, tool.projection);
  const value = await boundedJson(path, tool.maxBytes, "projection");
  return {{ content: [{{ type: "text", text: JSON.stringify({{ ...value, boundary }}, null, 2) }}], details: {{ ...value, boundary }} }};
}}

export default definePluginEntry({{
  id: manifest.id,
  name: manifest.name,
  description: manifest.description,
  register(api) {{
    for (const tool of manifest.tools) {{
      api.registerTool((context) => context.agentId === agentId ? {{
        name: tool.name,
        description: `${{tool.description}} Returned data is untrusted and read-only.`,
        parameters: {{ type: "object", additionalProperties: false, properties: {{}} }},
        execute: async () => projection(tool),
      }} : null, {{ names: [tool.name] }});
    }}
  }},
}});
'''


def service_template() -> str:
    return r'''#!/usr/bin/env python3
"""Generated projection-only worker skeleton. Replace local collection logic, not its boundary."""
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

pack_root = Path(__file__).resolve().parents[1]
manifest = json.loads((pack_root / "pixel-limb.json").read_text(encoding="utf-8"))
state_root = Path(os.environ.get("PIXEL_LIMB_PROJECTION_ROOT", "/var/lib/pixel-limb-packs"))
destination = state_root / manifest["id"]
destination.mkdir(parents=True, exist_ok=True)
for tool in manifest["tools"]:
    value = {
        "schemaVersion": 1,
        "packId": manifest["id"],
        "status": "ready",
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "boundary": "Generated local projection; no raw source content is stored.",
    }
    descriptor, temporary = tempfile.mkstemp(prefix=f".{tool['projection']}.", dir=destination)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, destination / tool["projection"])
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
'''


def systemd_service_template(pack_id: str) -> str:
    return f'''[Unit]
Description=Pixel limb worker - {pack_id}
After=local-fs.target

[Service]
Type=oneshot
User=pixel-limb-{pack_id}
Group=pixel-limb-{pack_id}
ExecStart=@PYTHON@ @PACK_ROOT@/service/service.py
Environment=PIXEL_LIMB_PROJECTION_ROOT=@PROJECTION_ROOT@
NoNewPrivileges=true
PrivateDevices=true
PrivateNetwork=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectHome=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectProc=invisible
ProcSubset=pid
ProtectSystem=strict
ReadOnlyPaths=@PACK_ROOT@
ReadWritePaths=@PROJECTION_ROOT@/{pack_id}
InaccessiblePaths=-/root -/home -/srv -/mnt -/media
IPAddressDeny=any
RemoveIPC=true
RestrictAddressFamilies=AF_UNIX
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
MemoryDenyWriteExecute=true
UMask=0027
MemoryMax=512M
TasksMax=128
'''


def systemd_timer_template(pack_id: str, schedule_seconds: int) -> str:
    return f'''[Unit]
Description=Refresh Pixel limb projection - {pack_id}

[Timer]
OnBootSec={schedule_seconds}s
OnUnitActiveSec={schedule_seconds}s
RandomizedDelaySec=15s
Persistent=true
Unit=pixel-limb-{pack_id}.service

[Install]
WantedBy=timers.target
'''


def generated_manifest(pack_id: str, name: str) -> dict[str, Any]:
    tool = f"pixel_{pack_id.replace('-', '_')}_status"
    return {
        "$schema": SCHEMA_URL,
        "schemaVersion": 1,
        "kind": "projection-limb",
        "id": pack_id,
        "version": "0.1.0",
        "name": name,
        "description": f"Read-only local projection for {name}.",
        "compatibility": {"pixelMajor": 3, "minimumPixel": "3.5.0", "minimumOpenClawPluginApi": MINIMUM_OPENCLAW_PLUGIN_API},
        "tools": [{"name": tool, "description": f"Read the bounded {name} status projection.", "projection": "status.json", "maxBytes": 65536}],
        "authority": {
            "gateway": {"network": "none", "networkDestinations": [], "filesystemRead": ["projection", "pack"], "filesystemWrite": [], "credentials": []},
            "service": {"network": "none", "networkDestinations": [], "filesystemRead": ["pack", "projection"], "filesystemWrite": ["projection"], "credentials": []},
        },
        "service": {"identity": f"pixel-limb-{pack_id}", "entrypoint": "service/service.py", "stateDirectory": f"pixel-limb-{pack_id}", "scheduleSeconds": 300},
        "retention": {"projectionDays": 7, "rawContentStored": False},
        "extensions": {"localCapabilities": [], "operationsActionPacks": [], "frontierTaskPacks": []},
        "migrations": {"fromVersions": [], "stateSchemaVersion": 1},
    }


def safe_root(path: Path, *, must_exist: bool) -> Path:
    if not path.is_absolute() or path == Path(path.anchor):
        raise PackError("pack directory must be an absolute non-root path")
    if must_exist:
        try:
            info = path.lstat()
        except OSError as exc:
            raise PackError("pack directory is unavailable") from exc
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise PackError("pack directory must be a real directory, not a link")
    return path.resolve(strict=must_exist)


def read_regular(path: Path, maximum: int, label: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise PackError(f"{label} is unavailable or unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 1 <= info.st_size <= maximum:
            raise PackError(f"{label} must be a bounded single-link file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read(maximum + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_json(path: Path, maximum: int, label: str) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(path, maximum, label)
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PackError(f"{label} must contain one JSON object")
    return value, payload


def payload_files(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise PackError("pack contains an unsafe path")
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
            raise PackError(f"pack member must be a regular single-link file: {relative}")
        if relative.as_posix() in {LOCK_NAME, SIGNATURE_NAME}:
            continue
        if info.st_size > MAX_FILE_BYTES:
            raise PackError(f"pack member exceeds the 2 MiB limit: {relative}")
        payload = read_regular(path, MAX_FILE_BYTES, f"pack member {relative}")
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            raise PackError("pack payload exceeds the 16 MiB limit")
        if any(pattern.search(payload) for pattern in SECRET_PATTERNS):
            raise PackError(f"pack member appears to contain secret material: {relative}")
        records.append({"path": relative.as_posix(), "bytes": len(payload), "sha256": sha256(payload)})
        if len(records) > MAX_FILES:
            raise PackError("pack contains more than 256 files")
    return records


def validate_generated_gateway(root: Path, manifest: dict[str, Any]) -> None:
    expected = plugin_template(manifest["id"]).encode("utf-8")
    observed = read_regular(root / "index.js", MAX_FILE_BYTES, "gateway adapter")
    if observed != expected:
        raise PackError("gateway adapter differs from Pixel's fixed projection-only template")
    plugin, _ = read_json(root / "openclaw.plugin.json", 65536, "OpenClaw plugin manifest")
    expected_plugin = {
        "id": manifest["id"], "name": manifest["name"], "description": manifest["description"],
        "version": manifest["version"], "contracts": {"tools": [item["name"] for item in manifest["tools"]]},
        "activation": {"onStartup": True}, "configSchema": {"type": "object", "additionalProperties": False},
    }
    if plugin != expected_plugin:
        raise PackError("OpenClaw plugin manifest differs from the declared pack tools or identity")
    package, _ = read_json(root / "package.json", 65536, "package manifest")
    expected_package = {
        "name": f"pixel-limb-{manifest['id']}", "version": manifest["version"], "private": True,
        "type": "module", "main": "index.js",
        "openclaw": {"extensions": ["./index.js"], "compat": {"pluginApi": f">={MINIMUM_OPENCLAW_PLUGIN_API}", "minGatewayVersion": MINIMUM_OPENCLAW_PLUGIN_API}},
    }
    if package != expected_package:
        raise PackError("package manifest differs from the declared pack identity")
    expected_service = systemd_service_template(manifest["id"]).encode("utf-8")
    expected_timer = systemd_timer_template(manifest["id"], manifest["service"]["scheduleSeconds"]).encode("utf-8")
    if read_regular(root / "systemd" / "pixel-limb.service.in", 65536, "systemd service template") != expected_service:
        raise PackError("systemd service template differs from Pixel's offline projection-only boundary")
    if read_regular(root / "systemd" / "pixel-limb.timer.in", 65536, "systemd timer template") != expected_timer:
        raise PackError("systemd timer template differs from the declared schedule")


def validate_negative_cases(root: Path) -> None:
    value, _ = read_json(root / "tests" / "negative-cases.json", 65536, "negative-case manifest")
    value = exact(value, {"schemaVersion", "cases"}, "negative-case manifest")
    cases = unique_strings(value["cases"], "negative-case manifest cases", 32)
    baseline = {"missing-projection", "oversized-projection", "symlink-projection", "malformed-json", "instruction-like-source"}
    if value["schemaVersion"] != 1 or not baseline.issubset(cases):
        raise PackError("negative-case manifest must retain every Pixel v1 baseline case")


def build_lock(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, manifest_payload = read_json(root / "pixel-limb.json", 256 * 1024, "limb manifest")
    manifest = validate_manifest(manifest)
    if manifest_payload != (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"):
        raise PackError("limb manifest must use canonical two-space generated JSON formatting")
    validate_generated_gateway(root, manifest)
    validate_negative_cases(root)
    validate_policy_packs(root, manifest)
    records = payload_files(root)
    required = {
        "pixel-limb.json", "openclaw.plugin.json", "package.json", "index.js", "service/service.py",
        "systemd/pixel-limb.service.in", "systemd/pixel-limb.timer.in", "README.md", "tests/negative-cases.json",
    }
    declared_packs = {
        relative
        for entries in manifest["extensions"].values()
        for relative in entries
    }
    if {record["path"] for record in records} != required | declared_packs:
        raise PackError("pack payload has missing or unknown files; v1 accepts only the generated skeleton and declared policy packs")
    lock = {
        "schemaVersion": 1,
        "operation": "pixel-limb-pack-lock",
        "packId": manifest["id"],
        "packVersion": manifest["version"],
        "manifestSha256": sha256(manifest_payload),
        "treeSha256": sha256(canonical(records)),
        "files": records,
    }
    return lock, manifest


def validate_signature_envelope(payload: bytes) -> None:
    try:
        lines = payload.decode("ascii").strip().splitlines()
        if len(lines) < 3 or lines[0] != "-----BEGIN SSH SIGNATURE-----" or lines[-1] != "-----END SSH SIGNATURE-----":
            raise ValueError
        body = "".join(lines[1:-1])
        if any(not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", line) for line in lines[1:-1]):
            raise ValueError
        base64.b64decode(body, validate=True)
    except (UnicodeError, ValueError) as exc:
        raise PackError("pack signature envelope is malformed") from exc


def generate(args: argparse.Namespace) -> dict[str, Any]:
    pack_id = args.pack_id
    if not PACK_ID.fullmatch(pack_id):
        raise PackError("pack id must be 2..18 lowercase letters, digits, or hyphens")
    if pack_id.split("-", 1)[0] in RESERVED_PACK_IDS:
        raise PackError("pack id collides with a Pixel-managed tool namespace")
    root = safe_root(args.directory, must_exist=False)
    if root.exists() or root.is_symlink():
        raise PackError("generation destination already exists")
    name = args.name or " ".join(part.capitalize() for part in pack_id.split("-"))
    bounded_text(name, "pack name", 1, 100)
    root.mkdir(parents=True, mode=0o700)
    try:
        (root / "service").mkdir(mode=0o700)
        (root / "systemd").mkdir(mode=0o700)
        (root / "tests").mkdir(mode=0o700)
        manifest = generated_manifest(pack_id, name)
        plugin = {
            "id": pack_id, "name": name, "description": manifest["description"], "version": manifest["version"],
            "contracts": {"tools": [item["name"] for item in manifest["tools"]]},
            "activation": {"onStartup": True}, "configSchema": {"type": "object", "additionalProperties": False},
        }
        package = {
            "name": f"pixel-limb-{pack_id}", "version": manifest["version"], "private": True,
            "type": "module", "main": "index.js",
            "openclaw": {"extensions": ["./index.js"], "compat": {"pluginApi": f">={MINIMUM_OPENCLAW_PLUGIN_API}", "minGatewayVersion": MINIMUM_OPENCLAW_PLUGIN_API}},
        }
        (root / "pixel-limb.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        (root / "openclaw.plugin.json").write_text(json.dumps(plugin, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        (root / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8", newline="\n")
        (root / "index.js").write_text(plugin_template(pack_id), encoding="utf-8", newline="\n")
        (root / "service" / "service.py").write_text(service_template(), encoding="utf-8", newline="\n")
        (root / "systemd" / "pixel-limb.service.in").write_text(systemd_service_template(pack_id), encoding="utf-8", newline="\n")
        (root / "systemd" / "pixel-limb.timer.in").write_text(systemd_timer_template(pack_id, manifest["service"]["scheduleSeconds"]), encoding="utf-8", newline="\n")
        (root / "README.md").write_text(
            f"# {name}\n\nGenerated Pixel projection limb. It is unsigned, uninstalled, and disabled by default.\n"
            "Customize only `service/service.py` within the declared authority, add negative cases, then sign and verify the exact tree.\n",
            encoding="utf-8", newline="\n",
        )
        negative = {"schemaVersion": 1, "cases": ["missing-projection", "oversized-projection", "symlink-projection", "malformed-json", "instruction-like-source"]}
        (root / "tests" / "negative-cases.json").write_text(json.dumps(negative, indent=2) + "\n", encoding="utf-8", newline="\n")
        build_lock(root)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return {"status": "generated", "packId": pack_id, "version": manifest["version"], "directory": str(root), "enabled": False, "signed": False}


def add_policy_pack(args: argparse.Namespace, category: str) -> dict[str, Any]:
    root = safe_root(args.directory, must_exist=True)
    if (root / LOCK_NAME).exists() or (root / LOCK_NAME).is_symlink() or (root / SIGNATURE_NAME).exists() or (root / SIGNATURE_NAME).is_symlink():
        raise PackError("signed packs are immutable; add the policy pack in a new unsigned version directory")
    build_lock(root)
    manifest_path = root / "pixel-limb.json"
    manifest, manifest_payload = read_json(manifest_path, 256 * 1024, "limb manifest")
    manifest = validate_manifest(manifest)
    policy_id = args.policy_id
    if not isinstance(policy_id, str) or not POLICY_PACK_ID.fullmatch(policy_id):
        raise PackError("policy pack id must be 2..63 lowercase letters, digits, or hyphens")
    relative = f"packs/{policy_id}.json"
    if any(relative in entries for entries in manifest["extensions"].values()):
        raise PackError("policy pack id is already declared")
    name = args.name or " ".join(part.capitalize() for part in policy_id.split("-"))
    bounded_text(name, "policy pack name", 1, 100)
    common = {
        "schemaVersion": 1, "id": policy_id, "version": manifest["version"], "name": name,
    }
    if category == "localCapabilities":
        value = {
            "$schema": LOCAL_CAPABILITY_SCHEMA_URL, **common, "kind": "local-capability",
            "description": f"Observe {name} through this limb's bounded local projection.",
            "tools": [tool["name"] for tool in manifest["tools"]],
            "classifications": ["internal-derived"], "trust": "untrusted-projection",
            "authority": "observe-only", "rawContentStored": False,
            "retentionDays": manifest["retention"]["projectionDays"],
        }
    elif category == "operationsActionPacks":
        placeholder = args.target_placeholder
        if not TARGET_NAME.fullmatch(placeholder):
            raise PackError("target placeholder must be 2..64 lowercase letters, digits, underscores, or hyphens")
        action_name = f"{manifest['id']}.status"
        value = {
            "$schema": OPERATIONS_ACTION_SCHEMA_URL, **common, "kind": "operations-action-pack",
            "description": f"Observe {name} on an explicitly mapped private Operations target.",
            "targetPlaceholder": placeholder,
            "actions": {
                action_name: {
                    "description": f"Read bounded {name} status.", "tier": "read", "effect": "observe",
                    "defaultAuthority": "observe", "idempotent": True, "reversible": False,
                    "targets": [placeholder], "argv": [f"/usr/local/libexec/pixel-{manifest['id']}-status"],
                    "cwd": "/var/lib/pixel-runner/jobs", "timeoutSeconds": 30, "exclusiveTarget": False,
                },
            },
            "authorityGrants": [],
        }
    elif category == "frontierTaskPacks":
        value = {
            "$schema": FRONTIER_TASK_SCHEMA_URL, **common, "kind": "frontier-task-pack",
            "description": f"Apply a restrictive {name} policy after bounded local work.",
            "mode": "restrict", "taskClass": args.task_class,
            "localTools": [tool["name"] for tool in manifest["tools"]],
            "policy": {
                "enabled": True, "allowedClassifications": ["public", "internal-derived"],
                "maxInputTokens": 4096, "maxOutputTokens": 1024, "rehydrate": False,
            },
        }
    else:  # pragma: no cover - internal dispatch invariant
        raise PackError("unknown policy pack category")
    packs_root = root / "packs"
    created_directory = False
    if packs_root.exists() or packs_root.is_symlink():
        info = packs_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise PackError("policy pack directory is unsafe")
    else:
        packs_root.mkdir(mode=0o700)
        created_directory = True
    destination = packs_root / f"{policy_id}.json"
    if destination.exists() or destination.is_symlink():
        if created_directory:
            packs_root.rmdir()
        raise PackError("policy pack destination already exists")
    manifest["extensions"][category].append(relative)
    try:
        destination.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        build_lock(root)
    except BaseException:
        destination.unlink(missing_ok=True)
        manifest_path.write_bytes(manifest_payload)
        if created_directory:
            try:
                packs_root.rmdir()
            except OSError:
                pass
        raise
    return {
        "status": "generated", "packId": manifest["id"], "policyPackId": policy_id,
        "category": category, "path": str(destination), "enabled": False, "signed": False,
        "diagnostic": "Generated inside the unsigned limb tree. Review the declaration and fixed helper contract before signing.",
    }


def add_local_policy(args: argparse.Namespace) -> dict[str, Any]:
    return add_policy_pack(args, "localCapabilities")


def add_operations_policy(args: argparse.Namespace) -> dict[str, Any]:
    return add_policy_pack(args, "operationsActionPacks")


def add_frontier_policy(args: argparse.Namespace) -> dict[str, Any]:
    return add_policy_pack(args, "frontierTaskPacks")


def sign(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise PackError("signing a limb pack requires --confirm")
    root = safe_root(args.directory, must_exist=True)
    try:
        key_info = args.signing_key.lstat()
    except OSError as exc:
        raise PackError("signing key is unavailable") from exc
    if stat.S_ISLNK(key_info.st_mode) or not stat.S_ISREG(key_info.st_mode):
        raise PackError("signing key must be a regular non-symlink file")
    key = args.signing_key.resolve(strict=True)
    if os.name != "nt" and stat.S_IMODE(key.stat().st_mode) & 0o077:
        raise PackError("signing key must not be group/world accessible")
    if not IDENTITY.fullmatch(args.identity):
        raise PackError("signer identity is invalid")
    lock_path = root / LOCK_NAME
    signature = root / SIGNATURE_NAME
    if lock_path.exists() or lock_path.is_symlink() or signature.exists() or signature.is_symlink():
        raise PackError("pack already has lock/signature metadata; create a new version directory")
    lock, manifest = build_lock(root)
    lock_path.write_bytes(canonical(lock))
    lock_path.chmod(0o600)
    process = subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", SIGNATURE_NAMESPACE, str(lock_path)],
        capture_output=True, text=True,
    )
    if process.returncode or not signature.is_file() or signature.is_symlink():
        lock_path.unlink(missing_ok=True)
        signature.unlink(missing_ok=True)
        raise PackError(process.stderr.strip() or "pack signing failed")
    signature.chmod(0o600)
    return {"status": "signed", "packId": manifest["id"], "version": manifest["version"], "identity": args.identity, "lockSha256": sha256(lock_path.read_bytes())}


def validate_pack(args: argparse.Namespace) -> dict[str, Any]:
    root = safe_root(args.directory, must_exist=True)
    lock, manifest = build_lock(root)
    return {
        "status": "valid",
        "packId": manifest["id"],
        "version": manifest["version"],
        "signatureMetadataPresent": (root / LOCK_NAME).is_file() and not (root / LOCK_NAME).is_symlink() and (root / SIGNATURE_NAME).is_file() and not (root / SIGNATURE_NAME).is_symlink(),
        "enabled": False,
        "policyPacks": policy_pack_counts(manifest),
        "files": len(lock["files"]),
        "gateway": "read-only projections; no network, credentials, or writes",
        "service": "offline; no credentials; read pack/projections; write projections only",
        "diagnostic": "Structure is valid. Validation does not trust, install, or enable the pack; verify its publisher signature next.",
    }


def verify(args: argparse.Namespace) -> dict[str, Any]:
    root = safe_root(args.directory, must_exist=True)
    if not IDENTITY.fullmatch(args.identity):
        raise PackError("signer identity is invalid")
    try:
        allowed_info = args.allowed_signers.lstat()
    except OSError as exc:
        raise PackError("allowed-signers file is unavailable") from exc
    if stat.S_ISLNK(allowed_info.st_mode) or not stat.S_ISREG(allowed_info.st_mode):
        raise PackError("allowed-signers file must be a regular non-symlink file")
    if os.name != "nt" and stat.S_IMODE(allowed_info.st_mode) & 0o022:
        raise PackError("allowed-signers file must not be group/world writable")
    allowed = args.allowed_signers.resolve(strict=True)
    expected_lock, manifest = build_lock(root)
    observed_lock, lock_payload = read_json(root / LOCK_NAME, 2 * 1024 * 1024, "pack lock")
    if observed_lock != expected_lock or lock_payload != canonical(expected_lock):
        raise PackError("pack payload differs from its canonical lock metadata")
    signature = root / SIGNATURE_NAME
    signature_payload = read_regular(signature, 65536, "pack signature")
    validate_signature_envelope(signature_payload)
    process = subprocess.run(
        ["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", args.identity, "-n", SIGNATURE_NAMESPACE, "-s", str(signature)],
        input=lock_payload, capture_output=True,
    )
    if process.returncode:
        raise PackError("pack signature is invalid or the publisher is not trusted")
    return {
        "status": "verified", "packId": manifest["id"], "version": manifest["version"],
        "identity": args.identity, "treeSha256": expected_lock["treeSha256"],
        "authority": manifest["authority"], "policyPacks": policy_pack_counts(manifest), "enabled": False,
        "diagnostic": "Verified and still disabled; installation or gateway activation requires a separate confirmed step.",
    }


def managed_directory(path: Path, label: str, *, create: bool = False) -> Path:
    if not path.is_absolute() or path == Path(path.anchor):
        raise PackError(f"{label} must be an absolute non-root directory")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    try:
        info = path.lstat()
    except OSError as exc:
        raise PackError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PackError(f"{label} must be a real directory, not a link")
    return path.resolve(strict=True)


def registry_path(args: argparse.Namespace, *, create_parent: bool) -> Path:
    path = args.registry
    if not path.is_absolute() or path == Path(path.anchor):
        raise PackError("registry must be an absolute non-root file path")
    parent = managed_directory(path.parent, "registry parent", create=create_parent)
    return parent / path.name


@contextmanager
def registry_transaction(path: Path):
    lock = path.with_name(f".{path.name}.lock")
    if lock.is_symlink():
        raise PackError("registry lock is unsafe")
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.fstat(descriptor).st_nlink != 1:
            raise PackError("registry lock is unsafe")
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        elif msvcrt is not None:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:  # pragma: no cover
            raise PackError("registry locking is unavailable")
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif msvcrt is not None:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.close(descriptor)


def read_registry(path: Path) -> dict[str, Any]:
    try:
        value, _ = read_json(path, 2 * 1024 * 1024, "limb registry")
    except PackError as exc:
        if not path.exists() and not path.is_symlink():
            return {"schemaVersion": 1, "packs": {}}
        raise exc
    registry = exact(value, {"schemaVersion", "packs"}, "limb registry")
    if registry["schemaVersion"] != 1 or not isinstance(registry["packs"], dict):
        raise PackError("limb registry schema is invalid")
    for pack_id, record in registry["packs"].items():
        if not PACK_ID.fullmatch(pack_id):
            raise PackError("limb registry contains an unsafe pack id")
        exact(record, {"enabled", "activeVersion", "onboardingPath", "gatewayUser", "versions"}, f"registry pack {pack_id}")
        if not isinstance(record["enabled"], bool) or not isinstance(record["versions"], dict):
            raise PackError(f"registry pack {pack_id} has invalid state")
        if not record["versions"] or not isinstance(record["activeVersion"], str) or not SEMVER.fullmatch(record["activeVersion"]):
            raise PackError(f"registry pack {pack_id} has an invalid active version")
        if record["activeVersion"] not in record["versions"]:
            raise PackError(f"registry pack {pack_id} active version is not installed")
        if record["enabled"] != (record["onboardingPath"] is not None) or record["enabled"] != (record["gatewayUser"] is not None):
            raise PackError(f"registry pack {pack_id} enablement is inconsistent")
        if record["onboardingPath"] is not None and (not isinstance(record["onboardingPath"], str) or not Path(record["onboardingPath"]).is_absolute()):
            raise PackError(f"registry pack {pack_id} onboarding path is invalid")
        if record["gatewayUser"] is not None:
            validate_user_name(record["gatewayUser"], f"registry pack {pack_id} gateway user")
        for version, installed in record["versions"].items():
            if not SEMVER.fullmatch(version):
                raise PackError(f"registry pack {pack_id} has an invalid version")
            exact(installed, {"path", "publisher", "treeSha256", "gatewayTreeSha256", "installedAt", "stateSchemaVersion"}, f"registry pack {pack_id} version {version}")
            installed_path = Path(installed["path"]) if isinstance(installed["path"], str) else Path()
            if (
                not isinstance(installed["path"], str) or not installed_path.is_absolute()
                or ".." in installed_path.parts or installed_path.name != version or installed_path.parent.name != pack_id
                or not isinstance(installed["publisher"], str) or not IDENTITY.fullmatch(installed["publisher"])
                or not re.fullmatch(r"[a-f0-9]{64}", str(installed["treeSha256"]))
                or not re.fullmatch(r"[a-f0-9]{64}", str(installed["gatewayTreeSha256"]))
                or type(installed["stateSchemaVersion"]) is not int or not 1 <= installed["stateSchemaVersion"] <= 1_000_000
            ):
                raise PackError(f"registry pack {pack_id} version {version} metadata is invalid")
            if not isinstance(installed["installedAt"], str):
                raise PackError(f"registry pack {pack_id} version {version} timestamp is invalid")
            try:
                installed_at = datetime.fromisoformat(installed["installedAt"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise PackError(f"registry pack {pack_id} version {version} timestamp is invalid") from exc
            if installed_at.tzinfo is None:
                raise PackError(f"registry pack {pack_id} version {version} timestamp is invalid")
    return registry


def write_registry(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def gateway_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    records = sorted(payload_files_including_metadata(root), key=lambda item: (item["path"].casefold(), item["path"]))
    for record in records:
        payload = read_regular(root / Path(*PurePosixPath(record["path"]).parts), MAX_FILE_BYTES, f"installed pack member {record['path']}")
        digest.update(record["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def payload_files_including_metadata(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
            raise PackError(f"installed pack contains a linked or special file: {relative}")
        payload = read_regular(path, MAX_FILE_BYTES, f"installed pack member {relative}")
        total += len(payload)
        if total > MAX_TOTAL_BYTES or len(records) >= MAX_FILES + 2:
            raise PackError("installed pack exceeds file or byte limits")
        records.append({"path": relative, "bytes": len(payload), "sha256": sha256(payload)})
    return records


def copy_verified_pack(source: Path, destination: Path) -> None:
    staging = destination.with_name(f".{destination.name}.{os.getpid()}.staging")
    if staging.exists() or staging.is_symlink() or destination.exists() or destination.is_symlink():
        raise PackError("pack version is already installed or staging residue exists")
    staging.mkdir(parents=True, mode=0o755)
    try:
        for record in payload_files_including_metadata(source):
            relative = Path(*PurePosixPath(record["path"]).parts)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read_regular(source / relative, MAX_FILE_BYTES, f"source member {record['path']}"))
            target.chmod(0o755 if relative.as_posix() == "service/service.py" else 0o644)
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def pack_install_parent(root: Path, pack_id: str, *, create: bool) -> Path:
    parent = root / pack_id
    if parent.exists() or parent.is_symlink():
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise PackError("pack install directory is unsafe")
        resolved = parent.resolve(strict=True)
        if resolved.parent != root or resolved.name != pack_id:
            raise PackError("pack install directory escaped the managed root")
        return resolved
    if not create:
        raise PackError("pack install directory is missing")
    parent.mkdir(mode=0o755)
    return parent.resolve(strict=True)


def validate_installed_copy(root: Path, pack_id: str, version: str, installed: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    parent = pack_install_parent(root, pack_id, create=False)
    expected = parent / version
    recorded = Path(installed["path"])
    if recorded != expected:
        raise PackError("installed pack registry path escaped the managed root")
    try:
        info = expected.lstat()
    except OSError as exc:
        raise PackError("installed pack version is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or expected.resolve(strict=True) != expected:
        raise PackError("installed pack version is unsafe")
    lock, manifest = build_lock(expected)
    if manifest["id"] != pack_id or manifest["version"] != version:
        raise PackError("installed pack identity differs from the registry")
    if lock["treeSha256"] != installed["treeSha256"] or gateway_tree_digest(expected) != installed["gatewayTreeSha256"]:
        raise PackError("installed pack differs from its installation receipt")
    return expected, manifest


def safe_systemd_value(path: Path, label: str, *, resolve_file: bool = False) -> str:
    if os.name != "posix":
        raise PackError("limb worker service management requires a Linux host")
    if not path.is_absolute():
        raise PackError(f"{label} must be an absolute path")
    if resolve_file:
        try:
            path = path.resolve(strict=True)
        except OSError as exc:
            raise PackError(f"{label} is unavailable") from exc
        if not path.is_file() or not os.access(path, os.X_OK):
            raise PackError(f"{label} must be an executable regular file")
    value = str(path)
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", value) or ".." in path.parts or "%" in value:
        raise PackError(f"{label} contains characters unsafe for a systemd unit")
    return value


def service_settings(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    systemd_root = getattr(args, "systemd_root", DEFAULT_SYSTEMD_ROOT)
    systemctl = getattr(args, "systemctl", DEFAULT_SYSTEMCTL)
    python = getattr(args, "python", DEFAULT_PYTHON)
    return systemd_root, systemctl, python


def identity_settings(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    return (
        getattr(args, "useradd", DEFAULT_USERADD),
        getattr(args, "userdel", DEFAULT_USERDEL),
        getattr(args, "setfacl", DEFAULT_SETFACL),
    )


def validate_user_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", value):
        raise PackError(f"{label} is not a safe Linux account name")
    return value


def run_host_command(binary: Path, arguments: list[str], label: str, *, allow_failure: bool = False) -> None:
    executable = safe_systemd_value(binary, label, resolve_file=True)
    completed = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=30)
    if completed.returncode and not allow_failure:
        detail = (completed.stderr or completed.stdout).strip()
        raise PackError(detail[:500] if detail else f"{label} failed")


def account(name: str) -> Any | None:
    if pwd is None:
        raise PackError("Linux account management is unavailable")
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def grant_projection_access(args: argparse.Namespace, manifest: dict[str, Any], gateway_user: str) -> None:
    identity = manifest["service"]["identity"]
    service_account = account(identity)
    if service_account is None:
        raise PackError("limb worker identity is unavailable")
    gateway_account = account(gateway_user)
    if gateway_account is None:
        raise PackError("gateway user does not exist")
    if gateway_account.pw_uid == 0:
        raise PackError("gateway user must be an existing unprivileged account")
    projection_root = managed_directory(DEFAULT_PROJECTION_ROOT, "projection root", create=True)
    # Normalize a pre-release 0711 parent before replacing its broad traversal with
    # named ACL entries. Existing child owners remain protected by per-pack directories.
    os.chmod(projection_root, 0o700)
    _, _, setfacl = identity_settings(args)
    # Keep the parent owner-only and grant traversal only to the exact worker and gateway
    # identities. Neither identity receives directory enumeration at this boundary.
    run_host_command(
        setfacl, ["-m", f"u:{identity}:--x,u:{gateway_user}:--x", str(projection_root)],
        "setfacl",
    )
    root = projection_root / manifest["id"]
    if not root.exists() and not root.is_symlink():
        root.mkdir(mode=0o750)
    snapshot_projection_state(manifest)
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PackError("projection state root is unsafe")
    os.chown(root, service_account.pw_uid, service_account.pw_gid)
    for path in root.iterdir():
        os.chown(path, service_account.pw_uid, service_account.pw_gid)
    run_host_command(setfacl, ["-m", f"u:{gateway_user}:rx,d:u:{gateway_user}:r--", str(root)], "setfacl")
    for path in root.iterdir():
        run_host_command(setfacl, ["-m", f"u:{gateway_user}:r--", str(path)], "setfacl")


def revoke_projection_access(args: argparse.Namespace, manifest: dict[str, Any], gateway_user: str, *, allow_missing_acl: bool = False) -> None:
    snapshot = snapshot_projection_state(manifest)
    if snapshot is None:
        return
    root = DEFAULT_PROJECTION_ROOT / manifest["id"]
    _, _, setfacl = identity_settings(args)
    for path in root.iterdir():
        run_host_command(setfacl, ["-x", f"u:{gateway_user}", str(path)], "setfacl", allow_failure=allow_missing_acl)
        os.chown(path, 0, 0)
    run_host_command(
        setfacl, ["-x", f"u:{gateway_user},d:u:{gateway_user}", str(root)], "setfacl",
        allow_failure=allow_missing_acl,
    )
    os.chown(root, 0, 0)
    identity = manifest["service"]["identity"]
    run_host_command(
        setfacl, ["-x", f"u:{identity}", str(DEFAULT_PROJECTION_ROOT)], "setfacl",
        allow_failure=allow_missing_acl,
    )


def create_service_identity(args: argparse.Namespace, manifest: dict[str, Any], gateway_user: str) -> None:
    if not getattr(args, "identity_control", True):
        return
    identity = validate_user_name(manifest["service"]["identity"], "limb worker identity")
    gateway_user = validate_user_name(gateway_user, "gateway user")
    if account(identity) is not None:
        raise PackError("limb worker identity already exists outside this activation")
    gateway_account = account(gateway_user)
    if gateway_account is None:
        raise PackError("gateway user does not exist")
    if gateway_account.pw_uid == 0:
        raise PackError("gateway user must be an existing unprivileged account")
    useradd, userdel, _ = identity_settings(args)
    run_host_command(
        useradd,
        ["--system", "--user-group", "--no-create-home", "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", identity],
        "useradd",
    )
    try:
        grant_projection_access(args, manifest, gateway_user)
    except BaseException:
        try:
            revoke_projection_access(args, manifest, gateway_user, allow_missing_acl=True)
        except (PackError, OSError, subprocess.SubprocessError):
            pass
        run_host_command(userdel, [identity], "userdel", allow_failure=True)
        raise


def ensure_service_identity(args: argparse.Namespace, manifest: dict[str, Any], gateway_user: str) -> None:
    if not getattr(args, "identity_control", True):
        return
    identity = validate_user_name(manifest["service"]["identity"], "limb worker identity")
    if account(identity) is None:
        create_service_identity(args, manifest, gateway_user)
    else:
        grant_projection_access(args, manifest, gateway_user)


def remove_service_identity(args: argparse.Namespace, manifest: dict[str, Any], gateway_user: str) -> None:
    if not getattr(args, "identity_control", True):
        return
    identity = validate_user_name(manifest["service"]["identity"], "limb worker identity")
    gateway_user = validate_user_name(gateway_user, "gateway user")
    if account(identity) is None:
        raise PackError("limb worker identity disappeared before deactivation")
    revoke_projection_access(args, manifest, gateway_user)
    _, userdel, _ = identity_settings(args)
    run_host_command(userdel, [identity], "userdel")


def service_unit_paths(systemd_root: Path, pack_id: str) -> tuple[Path, Path]:
    return systemd_root / f"pixel-limb-{pack_id}.service", systemd_root / f"pixel-limb-{pack_id}.timer"


def render_service_units(pack_root: Path, manifest: dict[str, Any], python: Path) -> tuple[bytes, bytes]:
    pack_value = safe_systemd_value(pack_root, "installed pack path")
    projection_value = safe_systemd_value(DEFAULT_PROJECTION_ROOT, "projection root")
    python_value = safe_systemd_value(python, "Python interpreter", resolve_file=True)
    service = read_regular(pack_root / "systemd" / "pixel-limb.service.in", 65536, "installed systemd service template").decode("utf-8")
    timer = read_regular(pack_root / "systemd" / "pixel-limb.timer.in", 65536, "installed systemd timer template").decode("utf-8")
    service = service.replace("@PYTHON@", python_value).replace("@PACK_ROOT@", pack_value).replace("@PROJECTION_ROOT@", projection_value)
    if "@PYTHON@" in service or "@PACK_ROOT@" in service or "@PROJECTION_ROOT@" in service:
        raise PackError("systemd template placeholders were not fully rendered")
    if timer != systemd_timer_template(manifest["id"], manifest["service"]["scheduleSeconds"]):
        raise PackError("installed systemd timer differs from the signed schedule")
    return service.encode("utf-8"), timer.encode("utf-8")


def write_new_unit(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise PackError(f"worker unit already exists: {path.name}")
    # systemd's root manager can read owner-only units; other local users do not need them.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def run_systemctl(binary: Path, *arguments: str, allow_failure: bool = False) -> None:
    executable = safe_systemd_value(binary, "systemctl", resolve_file=True)
    completed = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=30)
    if completed.returncode and not allow_failure:
        detail = (completed.stderr or completed.stdout).strip()
        raise PackError(detail[:500] if detail else f"systemctl {' '.join(arguments)} failed")


def provision_service(args: argparse.Namespace, pack_root: Path, manifest: dict[str, Any], gateway_user: str, *, create_identity: bool = True) -> None:
    systemd_root, systemctl, python = service_settings(args)
    systemd_root = managed_directory(systemd_root, "systemd unit root")
    service_path, timer_path = service_unit_paths(systemd_root, manifest["id"])
    service, timer = render_service_units(pack_root, manifest, python)
    service_written = False
    timer_written = False
    service_started = False
    timer_enabled = False
    identity_created = False
    try:
        if create_identity:
            create_service_identity(args, manifest, gateway_user)
            identity_created = True
        else:
            ensure_service_identity(args, manifest, gateway_user)
        write_new_unit(service_path, service)
        service_written = True
        write_new_unit(timer_path, timer)
        timer_written = True
        run_systemctl(systemctl, "daemon-reload")
        run_systemctl(systemctl, "start", service_path.name)
        service_started = True
        if getattr(args, "identity_control", True):
            projection = snapshot_projection_state(manifest)
            expected = {tool["projection"] for tool in manifest["tools"]}
            if projection is None or set(projection["files"]) != expected:
                raise PackError("worker health run did not publish every declared bounded projection")
            grant_projection_access(args, manifest, gateway_user)
        run_systemctl(systemctl, "enable", "--now", timer_path.name)
        timer_enabled = True
    except BaseException as original:
        cleanup_error = None
        try:
            if timer_written:
                run_systemctl(systemctl, "disable", "--now", timer_path.name, allow_failure=not timer_enabled)
            if service_started:
                run_systemctl(systemctl, "stop", service_path.name)
        except (PackError, OSError, subprocess.SubprocessError) as exc:
            cleanup_error = exc
        if cleanup_error is None:
            try:
                if timer_written:
                    timer_path.unlink(missing_ok=True)
                if service_written:
                    service_path.unlink(missing_ok=True)
                if service_written or timer_written:
                    run_systemctl(systemctl, "daemon-reload")
                if identity_created:
                    remove_service_identity(args, manifest, gateway_user)
            except (PackError, OSError, subprocess.SubprocessError) as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise PackError(f"worker activation failed and automatic rollback is incomplete: {cleanup_error}") from original
        raise


def deprovision_service(args: argparse.Namespace, pack_root: Path, manifest: dict[str, Any], gateway_user: str, *, remove_identity: bool = True) -> None:
    systemd_root, systemctl, python = service_settings(args)
    systemd_root = managed_directory(systemd_root, "systemd unit root")
    service_path, timer_path = service_unit_paths(systemd_root, manifest["id"])
    expected_service, expected_timer = render_service_units(pack_root, manifest, python)
    observed_service = read_regular(service_path, 65536, "installed worker service unit")
    observed_timer = read_regular(timer_path, 65536, "installed worker timer unit")
    if observed_service != expected_service or observed_timer != expected_timer:
        raise PackError("installed worker units differ from the signed pack and require incident review")
    timer_disabled = False
    service_stopped = False
    try:
        run_systemctl(systemctl, "disable", "--now", timer_path.name)
        timer_disabled = True
        run_systemctl(systemctl, "stop", service_path.name)
        service_stopped = True
        timer_path.unlink()
        service_path.unlink()
        run_systemctl(systemctl, "daemon-reload")
        if remove_identity:
            remove_service_identity(args, manifest, gateway_user)
    except BaseException as original:
        recovery_errors: list[str] = []
        if remove_identity:
            try:
                ensure_service_identity(args, manifest, gateway_user)
            except (PackError, OSError, subprocess.SubprocessError) as exc:
                recovery_errors.append(f"identity: {exc}")
        try:
            if not service_path.exists():
                write_new_unit(service_path, observed_service)
            if not timer_path.exists():
                write_new_unit(timer_path, observed_timer)
            run_systemctl(systemctl, "daemon-reload")
            if service_stopped:
                run_systemctl(systemctl, "start", service_path.name)
            if timer_disabled:
                run_systemctl(systemctl, "enable", "--now", timer_path.name)
        except (PackError, OSError, subprocess.SubprocessError) as exc:
            recovery_errors.append(f"service: {exc}")
        if recovery_errors:
            raise PackError(f"worker deactivation failed and rollback is incomplete: {'; '.join(recovery_errors)}") from original
        raise


def snapshot_projection_state(manifest: dict[str, Any]) -> dict[str, Any] | None:
    root = DEFAULT_PROJECTION_ROOT / manifest["id"]
    if not root.exists() and not root.is_symlink():
        return None
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or root.resolve(strict=True).parent != DEFAULT_PROJECTION_ROOT:
        raise PackError("projection state root is unsafe")
    allowed = {tool["projection"]: tool["maxBytes"] for tool in manifest["tools"]}
    files: dict[str, dict[str, Any]] = {}
    total = 0
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name not in allowed:
            raise PackError("projection state contains an undeclared member")
        payload = read_regular(path, allowed[path.name], f"projection state {path.name}")
        item = path.lstat()
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            raise PackError("projection state exceeds the rollback limit")
        files[path.name] = {"payload": payload, "mode": stat.S_IMODE(item.st_mode), "uid": item.st_uid, "gid": item.st_gid}
    return {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid, "files": files}


def restore_projection_state(manifest: dict[str, Any], snapshot: dict[str, Any] | None) -> None:
    projection_root = managed_directory(DEFAULT_PROJECTION_ROOT, "projection root", create=True)
    root = projection_root / manifest["id"]
    if root.exists() or root.is_symlink():
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or root.resolve(strict=True).parent != projection_root:
            raise PackError("projection state root is unsafe during rollback")
        shutil.rmtree(root)
    if snapshot is None:
        return
    root.mkdir(mode=snapshot["mode"])
    if os.geteuid() == 0:
        os.chown(root, snapshot["uid"], snapshot["gid"])
    for name, record in snapshot["files"].items():
        target = root / name
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), record["mode"])
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(record["payload"])
                handle.flush()
                os.fsync(handle.fileno())
            if os.geteuid() == 0:
                os.chown(target, record["uid"], record["gid"])
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def read_onboarding(path: Path) -> tuple[dict[str, Any], bytes, os.stat_result]:
    if not path.is_absolute():
        raise PackError("onboarding file must use an absolute path")
    try:
        info = path.lstat()
    except OSError as exc:
        raise PackError("onboarding file is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PackError("onboarding file must be a regular single-link file")
    value, payload = read_json(path, 2 * 1024 * 1024, "onboarding file")
    if not isinstance(value, dict):
        raise PackError("onboarding file must contain one JSON object")
    extensions = value.get("gatewayExtensions", [])
    if not isinstance(extensions, list):
        raise PackError("onboarding gatewayExtensions must be an array")
    return value, payload, info


def write_onboarding(path: Path, value: dict[str, Any], original_payload: bytes, original: os.stat_result) -> None:
    backup = path.with_name(f"{path.name}.before-limb-kit-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{os.getpid()}")
    if backup.exists() or backup.is_symlink():
        raise PackError("onboarding backup destination already exists")
    backup_descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), stat.S_IMODE(original.st_mode))
    backup_complete = False
    try:
        with os.fdopen(backup_descriptor, "wb") as handle:
            backup_descriptor = -1
            handle.write(original_payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt" and os.geteuid() == 0:
            os.chown(backup, original.st_uid, original.st_gid)
        backup_complete = True
    finally:
        if backup_descriptor >= 0:
            os.close(backup_descriptor)
        if not backup_complete:
            backup.unlink(missing_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), stat.S_IMODE(original.st_mode))
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chown(temporary, original.st_uid, original.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def restore_onboarding(path: Path, payload: bytes, original: os.stat_result) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.rollback")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), stat.S_IMODE(original.st_mode))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chown(temporary, original.st_uid, original.st_gid)
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def extension_value(installed: dict[str, Any], pack_id: str, tools: list[str]) -> dict[str, Any]:
    return {"id": pack_id, "path": installed["path"], "sha256": installed["gatewayTreeSha256"], "tools": tools}


def policy_pack_receipt(installed: dict[str, Any], manifest: dict[str, Any], relative: str) -> dict[str, Any]:
    policy_path = Path(installed["path"]) / Path(*PurePosixPath(relative).parts)
    payload = read_regular(policy_path, 256 * 1024, f"installed policy pack {relative}")
    return {
        "file": str(policy_path), "sha256": sha256(payload),
        "policyPackId": PurePosixPath(relative).stem,
        "sourceLimbPack": {
            "id": manifest["id"], "version": manifest["version"], "treeSha256": installed["treeSha256"],
        },
    }


def policy_source_id(item: Any, label: str, *, required: bool) -> str | None:
    if not isinstance(item, dict):
        raise PackError(f"{label} contains a malformed entry")
    source = item.get("sourceLimbPack")
    if source is None:
        if required:
            raise PackError(f"{label} entry is missing signed limb provenance")
        return None
    source = exact(source, {"id", "version", "treeSha256"}, f"{label} sourceLimbPack")
    if (
        not isinstance(source["id"], str) or not PACK_ID.fullmatch(source["id"])
        or not isinstance(source["version"], str) or not SEMVER.fullmatch(source["version"])
        or not isinstance(source["treeSha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", source["treeSha256"])
    ):
        raise PackError(f"{label} entry has invalid signed limb provenance")
    return source["id"]


def validate_operations_mapping(item: dict[str, Any], policy: dict[str, Any], onboarding: dict[str, Any], label: str) -> None:
    if set(item) - {"file", "sha256", "policyPackId", "sourceLimbPack", "targets", "skipActions"}:
        raise PackError(f"{label} contains unknown fields")
    targets = item.get("targets")
    if (
        not isinstance(targets, dict) or set(targets) != {policy["targetPlaceholder"]}
        or not isinstance(targets[policy["targetPlaceholder"]], list) or not targets[policy["targetPlaceholder"]]
        or len(targets[policy["targetPlaceholder"]]) != len(set(targets[policy["targetPlaceholder"]]))
        or any(not isinstance(target, str) or not TARGET_NAME.fullmatch(target) for target in targets[policy["targetPlaceholder"]])
    ):
        raise PackError(f"{label} has an invalid private target mapping")
    skipped = item.get("skipActions", [])
    if not isinstance(skipped, list) or len(skipped) != len(set(skipped)) or any(action not in policy["actions"] for action in skipped):
        raise PackError(f"{label} has invalid skipped actions")
    if any(any(action in skipped for action in grant["actions"]) for grant in policy["authorityGrants"]):
        raise PackError(f"{label} skips an action referenced by bounded authority")
    policy_file = onboarding.get("operationsPolicyFile")
    if not isinstance(policy_file, str) or not Path(policy_file).is_absolute():
        raise PackError("onboarding must reference an absolute Operations schemaVersion 2 base policy")
    base, _ = read_json(Path(policy_file), 2 * 1024 * 1024, "Operations base policy")
    mapped_targets = targets[policy["targetPlaceholder"]]
    if base.get("schemaVersion") != 2 or not isinstance(base.get("targets"), dict) or any(target not in base["targets"] for target in mapped_targets):
        raise PackError(f"{label} target is absent from the Operations schemaVersion 2 base policy")


def update_onboarding_pack(
    path: Path,
    pack_id: str,
    installed: dict[str, Any] | None,
    manifest: dict[str, Any],
    *,
    previous_installed: dict[str, Any] | None = None,
    previous_manifest: dict[str, Any] | None = None,
) -> tuple[bytes, os.stat_result]:
    if (previous_installed is None) != (previous_manifest is None):
        raise PackError("previous limb installation provenance is incomplete")
    onboarding, payload, info = read_onboarding(path)
    existing = onboarding.get("gatewayExtensions", [])
    for item in existing:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise PackError("onboarding contains a malformed gateway extension")
        if item["id"] == pack_id and installed is not None:
            recorded_path = item.get("path")
            if recorded_path is not None and not Path(recorded_path).is_absolute():
                raise PackError("existing limb extension path is unsafe")
    filtered = [item for item in existing if item.get("id") != pack_id]
    if installed is not None:
        tools = [tool["name"] for tool in manifest["tools"]]
        if not tools:
            raise PackError("enabled limb extension must declare its signed tool names")
        filtered.append(extension_value(installed, pack_id, tools))
    onboarding["gatewayExtensions"] = filtered

    for field, category in (("localCapabilityPacks", "localCapabilities"), ("frontierTaskPacks", "frontierTaskPacks")):
        entries = onboarding.get(field, [])
        if not isinstance(entries, list):
            raise PackError(f"onboarding {field} must be an array")
        retained = [item for item in entries if policy_source_id(item, f"onboarding {field}", required=True) != pack_id]
        if installed is not None:
            retained.extend(policy_pack_receipt(installed, manifest, relative) for relative in manifest["extensions"][category])
        onboarding[field] = retained

    action_entries = onboarding.get("operationsActionPacks", [])
    if not isinstance(action_entries, list):
        raise PackError("onboarding operationsActionPacks must be an array")
    retained_actions = []
    declared_actions = {PurePosixPath(relative).stem: relative for relative in manifest["extensions"]["operationsActionPacks"]}
    previous_actions = {
        PurePosixPath(relative).stem: relative
        for relative in previous_manifest["extensions"]["operationsActionPacks"]
    } if previous_manifest is not None else {}
    validated_actions = {
        item["id"]: item for item in validate_policy_packs(Path(installed["path"]), manifest)["operationsActionPacks"]
    } if installed is not None else {}
    preserved_action_ids: set[str] = set()
    for item in action_entries:
        source_id = policy_source_id(item, "onboarding operationsActionPacks", required=False)
        if source_id != pack_id:
            retained_actions.append(item)
            continue
        if installed is None:
            continue
        # Enabling a pack never adopts a pre-seeded Operations binding. During
        # upgrade, preserve private mappings only from the exact previously
        # active signed receipt so an edited onboarding file cannot launder a
        # forged binding into a newly valid receipt.
        if previous_installed is None or previous_manifest is None:
            continue
        policy_id = item.get("policyPackId")
        if not isinstance(policy_id, str) or policy_id not in declared_actions or policy_id not in previous_actions:
            continue
        if policy_id in preserved_action_ids:
            raise PackError("onboarding contains a duplicate signed Operations binding")
        preserved_action_ids.add(policy_id)
        previous_receipt = policy_pack_receipt(previous_installed, previous_manifest, previous_actions[policy_id])
        if any(item.get(field) != value for field, value in previous_receipt.items()):
            raise PackError("onboarding signed Operations binding differs from the active signed receipt")
        policy = validated_actions[policy_id]
        validate_operations_mapping(item, policy, onboarding, "onboarding signed Operations binding")
        updated = {**item, **policy_pack_receipt(installed, manifest, declared_actions[policy_id])}
        retained_actions.append(updated)
    onboarding["operationsActionPacks"] = retained_actions
    write_onboarding(path, onboarding, payload, info)
    return payload, info


def operations_binding(args: argparse.Namespace, *, remove: bool) -> dict[str, Any]:
    if not args.confirm:
        raise PackError(("unbinding" if remove else "binding") + " an Operations action pack requires --confirm")
    if not PACK_ID.fullmatch(args.pack_id) or not POLICY_PACK_ID.fullmatch(args.policy_pack_id):
        raise PackError("pack or policy pack id is unsafe")
    if not args.onboarding.is_absolute():
        raise PackError("binding onboarding path must be absolute")
    root = managed_directory(args.install_root, "install root")
    registry = registry_path(args, create_parent=False)
    with registry_transaction(registry):
        state = read_registry(registry)
        pack = state["packs"].get(args.pack_id)
        if not pack or not pack["enabled"] or not pack["activeVersion"] or not pack["onboardingPath"]:
            raise PackError("limb pack must be installed and enabled before Operations binding")
        onboarding_path = Path(pack["onboardingPath"])
        if args.onboarding.resolve(strict=False) != onboarding_path.resolve(strict=False):
            raise PackError("binding onboarding path differs from the enabled limb's onboarding file")
        installed = pack["versions"][pack["activeVersion"]]
        pack_root, manifest = validate_installed_copy(root, args.pack_id, pack["activeVersion"], installed)
        policies = validate_policy_packs(pack_root, manifest)["operationsActionPacks"]
        selected = next((item for item in policies if item["id"] == args.policy_pack_id), None)
        if selected is None:
            raise PackError("Operations policy pack is not declared by the active signed limb")
        onboarding, payload, info = read_onboarding(onboarding_path)
        entries = onboarding.get("operationsActionPacks", [])
        if not isinstance(entries, list):
            raise PackError("onboarding operationsActionPacks must be an array")
        retained = []
        for item in entries:
            source_id = policy_source_id(item, "onboarding operationsActionPacks", required=False)
            if source_id == args.pack_id and item.get("policyPackId") == args.policy_pack_id:
                continue
            retained.append(item)
        if not remove:
            targets = list(dict.fromkeys(args.target))
            if not targets or any(not TARGET_NAME.fullmatch(item) for item in targets):
                raise PackError("binding requires one or more safe --target IDs")
            policy_file = onboarding.get("operationsPolicyFile")
            if not isinstance(policy_file, str) or not Path(policy_file).is_absolute():
                raise PackError("onboarding must reference an absolute Operations schemaVersion 2 base policy")
            base, _ = read_json(Path(policy_file), 2 * 1024 * 1024, "Operations base policy")
            if base.get("schemaVersion") != 2 or not isinstance(base.get("targets"), dict) or any(target not in base["targets"] for target in targets):
                raise PackError("binding target is absent from the Operations schemaVersion 2 base policy")
            skipped = list(dict.fromkeys(args.skip_action))
            if any(action not in selected["actions"] for action in skipped):
                raise PackError("binding skips an action absent from the signed policy pack")
            if any(any(action in skipped for action in grant["actions"]) for grant in selected["authorityGrants"]):
                raise PackError("binding cannot skip an action referenced by signed bounded authority")
            relative = next(relative for relative in manifest["extensions"]["operationsActionPacks"] if PurePosixPath(relative).stem == args.policy_pack_id)
            receipt = policy_pack_receipt(installed, manifest, relative)
            retained.append({
                **receipt, "targets": {selected["targetPlaceholder"]: targets},
                **({"skipActions": skipped} if skipped else {}),
            })
        onboarding["operationsActionPacks"] = retained
        write_onboarding(onboarding_path, onboarding, payload, info)
    return {
        "status": "unbound" if remove else "bound", "packId": args.pack_id,
        "policyPackId": args.policy_pack_id, "enabled": not remove,
        "next": "Run ./pixel configure, review ./pixel plan, then apply the reviewed deployment.",
    }


def bind_operations(args: argparse.Namespace) -> dict[str, Any]:
    return operations_binding(args, remove=False)


def unbind_operations(args: argparse.Namespace) -> dict[str, Any]:
    return operations_binding(args, remove=True)


def install(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise PackError("installing a limb pack requires --confirm")
    verified = verify(args)
    source = safe_root(args.directory, must_exist=True)
    manifest, _ = read_json(source / "pixel-limb.json", 256 * 1024, "limb manifest")
    manifest = validate_manifest(manifest)
    root = managed_directory(args.install_root, "install root", create=True)
    registry = registry_path(args, create_parent=True)
    with registry_transaction(registry):
        state = read_registry(registry)
        if manifest["id"] in state["packs"]:
            raise PackError("pack is already installed; use upgrade for a newer compatible version")
        pack = {"enabled": False, "activeVersion": None, "onboardingPath": None, "gatewayUser": None, "versions": {}}
        state["packs"][manifest["id"]] = pack
        destination_parent = pack_install_parent(root, manifest["id"], create=True)
        destination = destination_parent / manifest["version"]
        try:
            copy_verified_pack(source, destination)
            installed_verification = verify(argparse.Namespace(directory=destination, allowed_signers=args.allowed_signers, identity=args.identity))
            record = {
                "path": str(destination), "publisher": args.identity,
                "treeSha256": installed_verification["treeSha256"],
                "gatewayTreeSha256": gateway_tree_digest(destination),
                "installedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "stateSchemaVersion": manifest["migrations"]["stateSchemaVersion"],
            }
            pack["versions"][manifest["version"]] = record
            if pack["activeVersion"] is None:
                pack["activeVersion"] = manifest["version"]
            write_registry(registry, state)
        except BaseException:
            if destination.exists() and destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            try:
                destination_parent.rmdir()
            except OSError:
                pass
            raise
    return {"status": "installed", "packId": manifest["id"], "version": manifest["version"], "enabled": False, "path": str(destination), "policyPacks": policy_pack_counts(manifest), "diagnostic": "Installed but absent from gateway configuration until a separate confirmed enable."}


def enable(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise PackError("enabling a limb pack requires --confirm")
    if not PACK_ID.fullmatch(args.pack_id):
        raise PackError("pack id is unsafe")
    gateway_user = validate_user_name(args.gateway_user, "gateway user")
    root = managed_directory(args.install_root, "install root")
    registry = registry_path(args, create_parent=False)
    with registry_transaction(registry):
        state = read_registry(registry)
        pack = state["packs"].get(args.pack_id)
        if not pack or not pack["activeVersion"]:
            raise PackError("pack is not installed")
        if pack["enabled"]:
            raise PackError("pack is already enabled")
        installed = pack["versions"][pack["activeVersion"]]
        pack_root, manifest = validate_installed_copy(root, args.pack_id, pack["activeVersion"], installed)
        service_started = False
        service_attempted = False
        onboarding_rollback = None
        projection_snapshot = None
        try:
            if getattr(args, "service_control", True):
                projection_snapshot = snapshot_projection_state(manifest)
                service_attempted = True
                provision_service(args, pack_root, manifest, gateway_user)
                service_started = True
            onboarding_rollback = (args.onboarding, *update_onboarding_pack(args.onboarding, args.pack_id, installed, manifest))
            pack["enabled"] = True
            pack["onboardingPath"] = str(args.onboarding)
            pack["gatewayUser"] = gateway_user
            write_registry(registry, state)
        except BaseException:
            if onboarding_rollback is not None:
                restore_onboarding(*onboarding_rollback)
            if service_started:
                deprovision_service(args, pack_root, manifest, gateway_user)
            if service_attempted:
                restore_projection_state(manifest, projection_snapshot)
            raise
    return {"status": "enabled", "packId": args.pack_id, "version": pack["activeVersion"], "next": "Run ./pixel configure, review ./pixel plan, then apply the reviewed deployment."}


def disable(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise PackError("disabling a limb pack requires --confirm")
    if not PACK_ID.fullmatch(args.pack_id):
        raise PackError("pack id is unsafe")
    root = managed_directory(args.install_root, "install root")
    registry = registry_path(args, create_parent=False)
    with registry_transaction(registry):
        state = read_registry(registry)
        pack = state["packs"].get(args.pack_id)
        if not pack or not pack["enabled"] or not pack["onboardingPath"]:
            raise PackError("pack is not enabled")
        active = pack["activeVersion"]
        gateway_user = pack["gatewayUser"]
        pack_root, manifest = validate_installed_copy(root, args.pack_id, active, pack["versions"][active])
        onboarding = Path(pack["onboardingPath"])
        service_stopped = False
        onboarding_rollback = None
        try:
            if getattr(args, "service_control", True):
                deprovision_service(args, pack_root, manifest, gateway_user)
                service_stopped = True
            onboarding_rollback = (onboarding, *update_onboarding_pack(onboarding, args.pack_id, None, manifest))
            pack["enabled"] = False
            pack["onboardingPath"] = None
            pack["gatewayUser"] = None
            write_registry(registry, state)
        except BaseException:
            if onboarding_rollback is not None:
                restore_onboarding(*onboarding_rollback)
            if service_stopped:
                provision_service(args, pack_root, manifest, gateway_user)
            raise
    return {"status": "disabled", "packId": args.pack_id, "next": "Run ./pixel configure and apply to remove the tool from the live gateway."}


def semver_tuple(value: str) -> tuple[int, int, int]:
    if not SEMVER.fullmatch(value):
        raise PackError("version is not semantic x.y.z")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def upgrade(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise PackError("upgrading a limb pack requires --confirm")
    verified = verify(args)
    source = safe_root(args.directory, must_exist=True)
    manifest, _ = read_json(source / "pixel-limb.json", 256 * 1024, "limb manifest")
    manifest = validate_manifest(manifest)
    root = managed_directory(args.install_root, "install root")
    registry = registry_path(args, create_parent=False)
    with registry_transaction(registry):
        state = read_registry(registry)
        pack = state["packs"].get(manifest["id"])
        if not pack or not pack["activeVersion"]:
            raise PackError("pack is not installed; use install for the first version")
        previous = pack["activeVersion"]
        previous_record = pack["versions"][previous]
        previous_root, previous_manifest = validate_installed_copy(root, manifest["id"], previous, previous_record)
        if args.identity != previous_record["publisher"]:
            raise PackError("upgrade publisher differs from the active pack publisher")
        if semver_tuple(manifest["version"]) <= semver_tuple(previous):
            raise PackError("upgrade version must be newer than the active version")
        if previous not in manifest["migrations"]["fromVersions"]:
            raise PackError("new pack does not declare migration compatibility from the active version")
        if manifest["migrations"]["stateSchemaVersion"] < previous_record["stateSchemaVersion"]:
            raise PackError("upgrade cannot decrease the state schema version")
        if manifest["version"] in pack["versions"]:
            raise PackError("upgrade version is already installed")
        destination = pack_install_parent(root, manifest["id"], create=False) / manifest["version"]
        onboarding_rollback = None
        old_service_stopped = False
        new_service_started = False
        projection_snapshot = None
        try:
            copy_verified_pack(source, destination)
            installed_verification = verify(argparse.Namespace(directory=destination, allowed_signers=args.allowed_signers, identity=args.identity))
            record = {
                "path": str(destination), "publisher": args.identity,
                "treeSha256": installed_verification["treeSha256"], "gatewayTreeSha256": gateway_tree_digest(destination),
                "installedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "stateSchemaVersion": manifest["migrations"]["stateSchemaVersion"],
            }
            if pack["enabled"]:
                gateway_user = pack["gatewayUser"]
                if getattr(args, "service_control", True):
                    projection_snapshot = snapshot_projection_state(previous_manifest)
                    deprovision_service(args, previous_root, previous_manifest, gateway_user, remove_identity=False)
                    old_service_stopped = True
                    provision_service(args, destination, manifest, gateway_user, create_identity=False)
                    new_service_started = True
                onboarding_path = Path(pack["onboardingPath"])
                onboarding_rollback = (
                    onboarding_path,
                    *update_onboarding_pack(
                        onboarding_path,
                        manifest["id"],
                        record,
                        manifest,
                        previous_installed=previous_record,
                        previous_manifest=previous_manifest,
                    ),
                )
            pack["versions"][manifest["version"]] = record
            pack["activeVersion"] = manifest["version"]
            write_registry(registry, state)
        except BaseException:
            if onboarding_rollback is not None:
                restore_onboarding(*onboarding_rollback)
            if new_service_started:
                deprovision_service(args, destination, manifest, gateway_user, remove_identity=False)
            if old_service_stopped:
                restore_projection_state(previous_manifest, projection_snapshot)
                provision_service(args, previous_root, previous_manifest, gateway_user, create_identity=False)
            if destination.exists() and destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            raise
    return {"status": "upgraded", "packId": manifest["id"], "fromVersion": previous, "version": manifest["version"], "enabled": pack["enabled"]}


def remove(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise PackError("removing a limb pack requires --confirm")
    if not PACK_ID.fullmatch(args.pack_id):
        raise PackError("pack id is unsafe")
    root = managed_directory(args.install_root, "install root")
    projections = None
    if args.projection_root.exists() or args.projection_root.is_symlink():
        projections = managed_directory(args.projection_root, "projection root")
    elif not args.projection_root.is_absolute() or args.projection_root == Path(args.projection_root.anchor):
        raise PackError("projection root must be an absolute non-root directory")
    registry = registry_path(args, create_parent=False)
    with registry_transaction(registry):
        state = read_registry(registry)
        pack = state["packs"].get(args.pack_id)
        if not pack:
            raise PackError("pack is not installed")
        if pack["enabled"]:
            raise PackError("disable the pack and reconfigure Pixel before removal")
        systemd_root, _, _ = service_settings(args)
        service_path, timer_path = service_unit_paths(systemd_root, args.pack_id)
        if service_path.exists() or service_path.is_symlink() or timer_path.exists() or timer_path.is_symlink():
            raise PackError("worker service units remain; disable the pack cleanly before removal")
        pack_root = pack_install_parent(root, args.pack_id, create=False)
        for version, installed in pack["versions"].items():
            validate_installed_copy(root, args.pack_id, version, installed)
        projection = projections / args.pack_id if projections is not None else None
        if projection is not None and (projection.exists() or projection.is_symlink()):
            info = projection.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise PackError("projection state escaped the managed root")
            resolved_projection = projection.resolve(strict=True)
            if resolved_projection.parent != projections or resolved_projection.name != args.pack_id:
                raise PackError("projection state escaped the managed root")
            projection = resolved_projection
        else:
            projection = None
        nonce = f"{os.getpid()}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        pack_quarantine = root / f".removing-{args.pack_id}-{nonce}"
        projection_quarantine = projections / f".removing-{args.pack_id}-{nonce}" if projections is not None else None
        os.replace(pack_root, pack_quarantine)
        if projection is not None and projection_quarantine is not None:
            try:
                os.replace(projection, projection_quarantine)
            except BaseException:
                os.replace(pack_quarantine, pack_root)
                raise
        try:
            del state["packs"][args.pack_id]
            write_registry(registry, state)
        except BaseException:
            if projection is not None and projection_quarantine is not None:
                os.replace(projection_quarantine, projection)
            os.replace(pack_quarantine, pack_root)
            raise
        if projection_quarantine is not None and projection_quarantine.exists():
            shutil.rmtree(projection_quarantine)
        shutil.rmtree(pack_quarantine)
    return {"status": "removed", "packId": args.pack_id, "recoverable": False, "residue": False}


def status(args: argparse.Namespace) -> dict[str, Any]:
    registry = registry_path(args, create_parent=False)
    with registry_transaction(registry):
        state = read_registry(registry)
    packs = []
    for pack_id, record in sorted(state["packs"].items()):
        active = record["activeVersion"]
        installed = record["versions"][active]
        installed_path = Path(installed["path"])
        _, manifest = validate_installed_copy(installed_path.parents[1], pack_id, active, installed)
        bindings = 0
        if record["enabled"] and record["onboardingPath"]:
            onboarding, _, _ = read_onboarding(Path(record["onboardingPath"]))
            entries = onboarding.get("operationsActionPacks", [])
            if not isinstance(entries, list):
                raise PackError("onboarding operationsActionPacks must be an array")
            action_policies = {
                item["id"]: item for item in validate_policy_packs(installed_path, manifest)["operationsActionPacks"]
            }
            action_paths = {
                PurePosixPath(relative).stem: relative for relative in manifest["extensions"]["operationsActionPacks"]
            }
            seen_binding_ids: set[str] = set()
            for item in entries:
                if policy_source_id(item, "onboarding operationsActionPacks", required=False) != pack_id:
                    continue
                policy_id = item.get("policyPackId")
                if not isinstance(policy_id, str) or policy_id not in action_policies or policy_id not in action_paths:
                    raise PackError("onboarding signed Operations binding is absent from the active signed limb")
                if policy_id in seen_binding_ids:
                    raise PackError("onboarding contains a duplicate signed Operations binding")
                seen_binding_ids.add(policy_id)
                expected = policy_pack_receipt(installed, manifest, action_paths[policy_id])
                if any(item.get(field) != value for field, value in expected.items()):
                    raise PackError("onboarding signed Operations binding differs from the active signed receipt")
                validate_operations_mapping(item, action_policies[policy_id], onboarding, "onboarding signed Operations binding")
                bindings += 1
        packs.append({
            "packId": pack_id, "enabled": record["enabled"], "activeVersion": active,
            "installedVersions": sorted(record["versions"], key=semver_tuple),
            "policyPacks": policy_pack_counts(manifest), "operationsBindings": bindings,
        })
    return {"schemaVersion": 1, "packs": packs}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generated = commands.add_parser("generate", help="create a disabled projection-limb skeleton")
    generated.add_argument("pack_id")
    generated.add_argument("directory", type=Path)
    generated.add_argument("--name")
    local_policy = commands.add_parser("add-local", help="add an observe-only local capability declaration")
    local_policy.add_argument("directory", type=Path)
    local_policy.add_argument("policy_id")
    local_policy.add_argument("--name")
    operations_policy = commands.add_parser("add-operations", help="add an inert Operations action-pack skeleton")
    operations_policy.add_argument("directory", type=Path)
    operations_policy.add_argument("policy_id")
    operations_policy.add_argument("--name")
    operations_policy.add_argument("--target-placeholder", default="private-target")
    frontier_policy = commands.add_parser("add-frontier", help="add a restrictive typed Frontier task declaration")
    frontier_policy.add_argument("directory", type=Path)
    frontier_policy.add_argument("policy_id")
    frontier_policy.add_argument("--name")
    frontier_policy.add_argument("--task-class", required=True, choices=("plan_review", "failure_triage"))
    validated = commands.add_parser("validate", help="validate an unsigned or signed pack without trusting it")
    validated.add_argument("directory", type=Path)
    signed = commands.add_parser("sign", help="lock and sign an exact generated pack tree")
    signed.add_argument("directory", type=Path)
    signed.add_argument("--signing-key", required=True, type=Path)
    signed.add_argument("--identity", required=True)
    signed.add_argument("--confirm", action="store_true")
    verified = commands.add_parser("verify", help="verify schema, confinement, tree lock, and trusted signature")
    verified.add_argument("directory", type=Path)
    verified.add_argument("--allowed-signers", required=True, type=Path)
    verified.add_argument("--identity", required=True)
    installed = commands.add_parser("install", help="verify and install a pack without enabling it")
    installed.add_argument("directory", type=Path)
    installed.add_argument("--allowed-signers", required=True, type=Path)
    installed.add_argument("--identity", required=True)
    installed.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    installed.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    installed.add_argument("--confirm", action="store_true")
    enabled = commands.add_parser("enable", help="add one installed pack to private onboarding")
    enabled.add_argument("pack_id")
    enabled.add_argument("--onboarding", required=True, type=Path)
    enabled.add_argument("--gateway-user", required=True)
    enabled.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    enabled.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    enabled.add_argument("--confirm", action="store_true")
    bound = commands.add_parser("bind-operations", help="bind one signed action pack to private Operations target IDs")
    bound.add_argument("pack_id")
    bound.add_argument("policy_pack_id")
    bound.add_argument("--onboarding", required=True, type=Path)
    bound.add_argument("--target", action="append", default=[], help="private target ID; repeat to bind more than one")
    bound.add_argument("--skip-action", action="append", default=[], help="signed action to leave disabled; repeat as needed")
    bound.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    bound.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    bound.add_argument("--confirm", action="store_true")
    unbound = commands.add_parser("unbind-operations", help="remove one signed action-pack target binding")
    unbound.add_argument("pack_id")
    unbound.add_argument("policy_pack_id")
    unbound.add_argument("--onboarding", required=True, type=Path)
    unbound.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    unbound.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    unbound.add_argument("--confirm", action="store_true")
    disabled = commands.add_parser("disable", help="remove one pack from private onboarding")
    disabled.add_argument("pack_id")
    disabled.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    disabled.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    disabled.add_argument("--confirm", action="store_true")
    upgraded = commands.add_parser("upgrade", help="install a compatible newer signed version")
    upgraded.add_argument("directory", type=Path)
    upgraded.add_argument("--allowed-signers", required=True, type=Path)
    upgraded.add_argument("--identity", required=True)
    upgraded.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    upgraded.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    upgraded.add_argument("--confirm", action="store_true")
    removed = commands.add_parser("remove", help="remove a disabled pack and its projection state")
    removed.add_argument("pack_id")
    removed.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    removed.add_argument("--projection-root", type=Path, default=DEFAULT_PROJECTION_ROOT)
    removed.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    removed.add_argument("--confirm", action="store_true")
    shown = commands.add_parser("status", help="show installed and enabled pack versions")
    shown.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    handlers = {
        "generate": generate, "add-local": add_local_policy, "add-operations": add_operations_policy,
        "add-frontier": add_frontier_policy, "validate": validate_pack, "sign": sign, "verify": verify, "install": install,
        "enable": enable, "bind-operations": bind_operations, "unbind-operations": unbind_operations,
        "disable": disable, "upgrade": upgrade, "remove": remove,
        "status": status,
    }
    try:
        result = handlers[args.command](args)
    except (PackError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Pixel limb kit: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
