#!/usr/bin/env python3
"""Scoped service lifecycle for the Windows WSL owner; no install/uninstall.

Runs as the installation's ordinary Linux owner. Native units are admitted by
its existing private ODS marker and installed/source identity before mutation.
The shared operations broker and unrelated host services are not in the plan.
The Windows controller handles fixed native commands through WSL root authority;
this owner-checkout Python handles only validation and ordinary-owner Compose.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

NATIVE_UNITS = (
    "pixel-ingress.service", "openclaw-gateway.service",
    "pixel-extension-manager.service", "pixel-artifact-promoter.service",
    "pixel-workspace-preview.service",
    "pixel-preview-inspection.service",
)
HOST_AGENT_UNIT = "ods-host-agent.service"


def regular(path, uid, maximum=262144, private=False):
    # O_NOFOLLOW plus fstat binds the opened bytes to the checked inode.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_nlink != 1 or info.st_size > maximum
                or info.st_mode & (0o077 if private else 0o022)):
            raise RuntimeError(f"Unsafe ODS lifecycle artifact: {path}")
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise RuntimeError(f"Oversized ODS lifecycle artifact: {path}")
        return data


def managed_units(root, home, unit_dir=Path("/etc/systemd/system"), root_uid=0):
    marker_path = home / ".config/ods/pixel-managed.json"
    if not marker_path.exists() and not marker_path.is_symlink():
        # An installation without Pixel can still manage its own Compose stack.
        return []
    marker = json.loads(regular(marker_path, os.getuid(), 65536, private=True))
    if (marker.get("schema_version") != 2 or marker.get("manager") != "ods"
            or marker.get("install_dir") != str(root)
            or marker.get("initial_active_state") != "absent"
            or marker.get("state") != "ready"):
        raise RuntimeError("Pixel ownership marker is not ready for this installation")
    units = []
    for name in NATIVE_UNITS:
        path = unit_dir / name
        if name == 'pixel-preview-inspection.service' and not os.path.lexists(path):
            # Older installations have no inspection capability. A partial new
            # install is not silently admitted as an older installation.
            if os.path.lexists('/etc/ods-pixel-inspection.json') or os.path.lexists('/usr/local/libexec/ods-pixel-inspection'):
                raise RuntimeError('Incomplete preview inspection installation')
            continue
        content = regular(path, root_uid)
        text = content.decode("utf-8")
        if name == "openclaw-gateway.service":
            managed_descriptions = {"Description=OpenClaw Gateway - Pixel", "Description=OpenClaw Gateway - Portal"}
            if not managed_descriptions.intersection(text.splitlines()) or f"{root}/extensions/services/pixel-agent/plugin" not in text:
                raise RuntimeError("Gateway unit does not belong to this ODS installation")
        elif name == "pixel-ingress.service":
            program = "/usr/local/libexec/ods-pixel-ingress.mjs"
            if (f"ExecStart=/usr/bin/env node {program}" not in text
                    or "EnvironmentFile=/etc/ods/pixel-agent.env" not in text
                    or "Description=Pixel Agent host ingress" not in text):
                raise RuntimeError("Ingress unit does not match the ODS runtime")
        elif name == 'pixel-preview-inspection.service':
            source = root / 'extensions/services/pixel-agent/host' / name
            if content != regular(source, os.getuid()):
                raise RuntimeError('Inspection unit differs from this installation')
        else:
            source = root / "data/pixel" / name.removeprefix("pixel-")
            if content != regular(source, os.getuid()):
                raise RuntimeError(f"Native unit differs from this installation: {name}")
        units.append(name)
    return units


def owner_name(uid):
    # Keep the POSIX-only module out of import-time fixture loading on Windows.
    import pwd
    return pwd.getpwuid(uid).pw_name


def host_agent_restart(root):
    """Admit only the fixed, protected host-agent unit for this owner/root."""
    properties = ("LoadState", "FragmentPath", "User", "ExecStart", "DropInPaths")
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", HOST_AGENT_UNIT,
         "--property=" + ",".join(properties)],
        capture_output=True, text=True, check=False, timeout=10)
    values = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if not separator or name not in properties or name in values:
            raise RuntimeError("Invalid host-agent unit ownership response")
        values[name] = value
    if values.get("LoadState") == "not-found" and result.returncode in (0, 1, 4):
        if any(value for name, value in values.items() if name != "LoadState"):
            raise RuntimeError("Absent host-agent unit has unexpected ownership metadata")
        return False
    result.check_returncode()
    if set(values) != set(properties) or values["LoadState"] != "loaded":
        raise RuntimeError("Host-agent unit ownership is unavailable")
    unit = Path("/etc/systemd/system") / HOST_AGENT_UNIT
    if values["FragmentPath"] != str(unit) or values["DropInPaths"]:
        raise RuntimeError("Host-agent unit path or drop-ins differ from the ODS contract")
    regular(unit, 0)
    if values["User"] != owner_name(os.getuid()):
        raise RuntimeError("Host-agent unit belongs to another Linux owner")
    script = str(root / "bin/ods-host-agent.py")
    pattern = (r"\{ path=(/usr/(?:local/)?bin/python3(?:\.\d+)?) ; argv\[\]=\1 "
               + re.escape(script) + r"(?: --require-ods-network)? ; ignore_errors=no ;[^{}]* \}")
    if not re.fullmatch(pattern, values["ExecStart"]):
        raise RuntimeError("Host-agent executable does not belong to this ODS installation")
    return True


def run(action, root):
    root = Path(root)
    if not root.is_absolute() or root == Path("/") or root.resolve() != root or root.is_symlink():
        raise RuntimeError("An exact normalized ODS installation directory is required")
    regular(root / "ods-cli", os.getuid(), maximum=1024 * 1024)
    regular(root / ".env", os.getuid(), maximum=1024 * 1024, private=True)
    units = managed_units(root, Path.home())
    if os.getuid() == 0:
        raise RuntimeError("Run the lifecycle adapter as the ordinary installation owner")
    if action not in {"plan-start", "plan-stop", "compose-start", "compose-stop"}:
        raise ValueError("Expected plan-start, plan-stop, compose-start or compose-stop")
    restart_agent = host_agent_restart(root) if action.endswith("-start") else False
    if action in {"plan-start", "plan-stop"}:
        return {"schemaVersion": 1, "action": action.removeprefix("plan-"),
                "installRoot": str(root), "ownerUid": os.getuid(), "nativeUnits": units,
                "hostAgentRestart": restart_agent}
    operation = action.removeprefix("compose-")
    # Native unit control belongs to the bound Windows WSL owner, which can
    # call fixed systemctl argv as the distro's root without any sudo grant.
    # This owner-checkout Python and the Compose CLI never execute as root.
    command = ["bash", str(root / "ods-cli"), operation]
    if operation == "start":
        command.append("--defer-wsl-agent-restart")
    subprocess.run(command,
                   env=dict(os.environ, INSTALL_DIR=str(root)), cwd=root,
                   check=True, timeout=300 if operation == "start" else 180)
    return {"state": "stopped" if operation == "stop" else "started", "installRoot": str(root)}


def main():
    try:
        print(json.dumps(run(sys.argv[1], sys.argv[2])))
    except subprocess.TimeoutExpired as error:
        # Match GNU timeout so the Windows lifecycle controller cannot mistake
        # interrupted Compose descendants for a confirmed, retryable failure.
        print(f"ODS WSL lifecycle timed out: {error}", file=sys.stderr)
        return 124
    except Exception as error:
        print(f"ODS WSL lifecycle failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
