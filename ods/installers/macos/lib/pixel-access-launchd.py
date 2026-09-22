"""Render the native macOS Pixel LaunchDaemon contract.

This module has no installation side effects. The shell installer can use the
returned documents for a staged dry-run, while the privileged commit path
writes the exact same bytes to ``/Library/LaunchDaemons`` and ``/etc/ods``.
The gateway definition is deliberately derived from explicit installer input,
never from an arbitrary running LaunchAgent.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import re


GATEWAY_LABEL = "com.ods.pixel-native-gateway"
GATEWAY_TARGET = "system/" + GATEWAY_LABEL
GATEWAY_PLIST = Path("/Library/LaunchDaemons") / (GATEWAY_LABEL + ".plist")
ACCESS_LABEL = "com.ods.pixel-access"
ACCESS_TARGET = "system/" + ACCESS_LABEL
ACCESS_PLIST = Path("/Library/LaunchDaemons") / (ACCESS_LABEL + ".plist")
ACCESS_SOCKET = Path("/private/var/run/ods-pixel-access/control.sock")
ACCESS_STATE = Path("/private/var/lib/ods-pixel-access")
ACCESS_CONFIG = Path("/etc/ods/pixel-access.json")
ACCESS_KEY = Path("/etc/ods/pixel-access-relay.key")
ACCESS_PROGRAM = Path("/usr/local/libexec/ods-pixel-access/access_mode_server.py")
ACCESS_LOG = Path("/var/log/ods-pixel-access.log")
RELAY_LABEL = 'com.ods.pixel-access-relay'
RELAY_TARGET = 'system/' + RELAY_LABEL
RELAY_PLIST = Path('/Library/LaunchDaemons') / (RELAY_LABEL + '.plist')
RELAY_PROGRAM = ACCESS_PROGRAM.parent / 'access_mode_http.mjs'
RELAY_PROFILE = Path('/etc/ods/pixel-access-relay.sb')
MANAGED_ENV = frozenset(("OPENCLAW_REQUIRED_PLUGINS", "PIXEL_ODS_PROVIDER_DEPLOYMENT"))
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LaunchdPlanError(ValueError):
    pass


def _string(value, code="invalid-launchd-plan"):
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str) or not value or any(c in value for c in "\0\n\r\t"):
        raise LaunchdPlanError(code)
    return value


def _absolute(value, code="invalid-launchd-path"):
    value = _string(value, code)
    path = Path(value)
    if (not path.is_absolute() or path == Path("/")
            or any(part in ("", ".", "..") for part in value.split("/")[1:])):
        raise LaunchdPlanError(code)
    return value


def _environment(value):
    if value is None:
        return {}
    if type(value) is not dict:
        raise LaunchdPlanError("invalid-launchd-environment")
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _NAME.fullmatch(key) or not isinstance(item, str):
            raise LaunchdPlanError("invalid-launchd-environment")
        result[key] = _string(item, "invalid-launchd-environment")
    return result


def _arguments(value):
    if (type(value) is not list or len(value) < 4
            or any(not isinstance(item, str) or not item or any(c in item for c in "\0\n\r\t")
                   for item in value)):
        raise LaunchdPlanError("invalid-launchd-arguments")
    return list(value)


def gateway_definition(document):
    """Return the same provider-stable definition hash as LaunchdGatewayService."""
    if type(document) is not dict:
        raise LaunchdPlanError("invalid-launchd-document")
    arguments = _arguments(document.get("ProgramArguments"))
    if arguments[:2] != ["/usr/bin/env", "-i"]:
        raise LaunchdPlanError("launchd-provider-environment-unavailable")
    assignments, command = [], []
    for value in arguments[2:]:
        if not command and "=" in value and _NAME.fullmatch(value.partition("=")[0]):
            if value.partition("=")[0] not in MANAGED_ENV:
                assignments.append(value)
        else:
            command.append(value)
    if not command:
        raise LaunchdPlanError("launchd-provider-environment-unavailable")
    environment = _environment(document.get("EnvironmentVariables", {}))
    if any(key in MANAGED_ENV for key in environment):
        raise LaunchdPlanError("launchd-provider-environment-unavailable")
    normalized = dict(document)
    normalized["ProgramArguments"] = ["/usr/bin/env", "-i", *assignments, *command]
    if "EnvironmentVariables" in normalized:
        normalized["EnvironmentVariables"] = dict(environment)
    return hashlib.sha256(plistlib.dumps(normalized, fmt=plistlib.FMT_BINARY, sort_keys=True)).hexdigest()


def native_gateway_document(*, owner, group, program_arguments, working_directory,
                            environment=None, stdout_path, stderr_path,
                            label=GATEWAY_LABEL):
    """Build the fixed root LaunchDaemon for the owner-run OpenClaw gateway."""
    owner = _string(owner)
    group = _string(group)
    if label != GATEWAY_LABEL:
        raise LaunchdPlanError("invalid-launchd-label")
    arguments = _arguments(program_arguments)
    if arguments[:2] != ["/usr/bin/env", "-i"]:
        raise LaunchdPlanError("launchd-env-boundary-required")
    command = []
    for value in arguments[2:]:
        if not command and "=" in value and _NAME.fullmatch(value.partition("=")[0]):
            continue
        command.append(value)
    if (len(command) < 6 or command[:2] != ["/usr/bin/sandbox-exec", "-f"]
            or not re.fullmatch(r"/etc/ods/[A-Za-z0-9_./-]+", command[2])
            or not Path(command[3]).is_absolute() or command[4] != "gateway"):
        raise LaunchdPlanError("launchd-seatbelt-boundary-required")
    env = _environment(environment)
    return {
        "Label": label,
        "ProgramArguments": arguments,
        "UserName": owner,
        "GroupName": group,
        "WorkingDirectory": _absolute(working_directory),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        # Match OpenClaw's native gateway scheduling and private file defaults.
        "ProcessType": "Interactive",
        "Umask": 0o077,
        "StandardInPath": "/dev/null",
        "StandardOutPath": _absolute(stdout_path),
        "StandardErrorPath": _absolute(stderr_path),
    }


def access_daemon_document(*, program=ACCESS_PROGRAM, log=ACCESS_LOG):
    """Build the root-only access coordinator LaunchDaemon."""
    program = _absolute(os_fspath(program))
    log = _absolute(os_fspath(log))
    return {
        "Label": ACCESS_LABEL,
        "ProgramArguments": ["/usr/bin/python3", "-I", program],
        "UserName": "root",
        "GroupName": "wheel",
        "WorkingDirectory": "/",
        "EnvironmentVariables": {"PYTHONNOUSERSITE": "1"},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 3,
        "ProcessType": "Background",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def access_relay_document(*, owner, group, node, port, log):
    if type(port) is not int or not 1 <= port <= 65535 or owner == 'root':
        raise LaunchdPlanError('invalid-native-access-relay')
    return {
        'Label': RELAY_LABEL,
        'ProgramArguments': ['/usr/bin/env', '-i', 'PIXEL_NATIVE_ACCESS_PORT=' + str(port),
                             '/usr/bin/sandbox-exec', '-f', str(RELAY_PROFILE),
                             _absolute(node), str(RELAY_PROGRAM)],
        'UserName': _string(owner), 'GroupName': _string(group),
        'WorkingDirectory': '/', 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 5, 'ProcessType': 'Background',
        'StandardOutPath': _absolute(log), 'StandardErrorPath': _absolute(log),
    }


def os_fspath(value):
    try:
        return str(Path(value))
    except (TypeError, ValueError):
        raise LaunchdPlanError("invalid-launchd-path") from None


def binding(document, *, owner, executable, plist=GATEWAY_PLIST, target=GATEWAY_TARGET,
            uid=None, gid=None):
    """Create the root config receipt consumed by LaunchdAccessBridge."""
    if target != GATEWAY_TARGET or Path(plist) != GATEWAY_PLIST:
        raise LaunchdPlanError("system-launchdaemon-required")
    executable = _absolute(executable)
    owner = _string(owner)
    if type(uid) is not int or uid <= 0 or type(gid) is not int or gid < 0:
        raise LaunchdPlanError("invalid-launchd-process")
    if document.get("UserName") != owner:
        raise LaunchdPlanError("gateway-owner-mismatch")
    return {
        "schemaVersion": 1, "target": target, "plist": str(Path(plist)),
        "definition": gateway_definition(document), "owner": owner,
        "executable": executable,
        "process": {"uid": uid, "gid": gid, "executable": executable},
    }


def access_settings(*, install_dir, owner, openclaw_bin, gateway_port, binding_value,
                    settings_data_dir, edge_owner_key_sha256, state=ACCESS_STATE):
    """Return the fixed JSON consumed by the root access server."""
    install_dir = _absolute(install_dir)
    openclaw_bin = _absolute(openclaw_bin)
    state = _absolute(state)
    if (type(gateway_port) is not int or not 1 <= gateway_port <= 65535
            or not isinstance(binding_value, dict) or binding_value.get("target") != GATEWAY_TARGET
            or not isinstance(settings_data_dir, (str, type(None)))
            or not isinstance(edge_owner_key_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", edge_owner_key_sha256)):
        raise LaunchdPlanError("invalid-access-settings")
    if settings_data_dir is not None:
        settings_data_dir = _absolute(settings_data_dir)
    return {
        "install_dir": install_dir, "owner": _string(owner), "openclaw_bin": openclaw_bin,
        "gateway_port": gateway_port, "settings_data_dir": settings_data_dir,
        "edge_owner_key_sha256": edge_owner_key_sha256, "state_dir": state,
        "gateway_target": GATEWAY_TARGET, "gateway_plist": str(GATEWAY_PLIST),
        "gateway_process": dict(binding_value["process"]), "gateway_binding": binding_value,
    }


def encode(document):
    """Emit canonical XML accepted by the custody checker."""
    if type(document) is not dict:
        raise LaunchdPlanError("invalid-launchd-document")
    return plistlib.dumps(document, sort_keys=True)


def plan_json(*, gateway, access):
    """Stable machine-readable dry-run output for installer contract tests."""
    return json.dumps({"gateway": gateway, "access": access}, sort_keys=True, separators=(",", ":"))
