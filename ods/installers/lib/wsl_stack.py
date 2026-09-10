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
import stat
import subprocess
import sys

NATIVE_UNITS = (
    "pixel-ingress.service", "openclaw-gateway.service",
    "pixel-extension-manager.service", "pixel-artifact-promoter.service",
    "pixel-workspace-preview.service",
)


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
        content = regular(path, root_uid)
        text = content.decode("utf-8")
        if name == "openclaw-gateway.service":
            if "Description=OpenClaw Gateway - Pixel" not in text or f"{root}/extensions/services/pixel-agent/plugin" not in text:
                raise RuntimeError("Gateway unit does not belong to this ODS installation")
        elif name == "pixel-ingress.service":
            program = "/usr/local/libexec/ods-pixel-ingress.mjs"
            if (f"ExecStart=/usr/bin/env node {program}" not in text
                    or "EnvironmentFile=/etc/ods/pixel-agent.env" not in text
                    or "Description=Pixel Agent host ingress" not in text):
                raise RuntimeError("Ingress unit does not match the ODS runtime")
        else:
            source = root / "data/pixel" / name.removeprefix("pixel-")
            if content != regular(source, os.getuid()):
                raise RuntimeError(f"Native unit differs from this installation: {name}")
        units.append(name)
    return units


def run(action, root):
    root = Path(root)
    if not root.is_absolute() or root == Path("/") or root.resolve() != root or root.is_symlink():
        raise RuntimeError("An exact normalized ODS installation directory is required")
    regular(root / "ods-cli", os.getuid(), maximum=1024 * 1024)
    regular(root / ".env", os.getuid(), maximum=1024 * 1024, private=True)
    units = managed_units(root, Path.home())
    if os.getuid() == 0:
        raise RuntimeError("Run the lifecycle adapter as the ordinary installation owner")
    if action in {"plan-start", "plan-stop"}:
        return {"schemaVersion": 1, "action": action.removeprefix("plan-"),
                "installRoot": str(root), "ownerUid": os.getuid(), "nativeUnits": units}
    if action not in {"compose-start", "compose-stop"}:
        raise ValueError("Expected plan-start, plan-stop, compose-start or compose-stop")
    operation = action.removeprefix("compose-")
    # Native unit control belongs to the bound Windows WSL owner, which can
    # call fixed systemctl argv as the distro's root without any sudo grant.
    # This owner-checkout Python and the Compose CLI never execute as root.
    subprocess.run(["bash", str(root / "ods-cli"), operation],
                   env=dict(os.environ, INSTALL_DIR=str(root)), cwd=root,
                   check=True, timeout=300 if operation == "start" else 180)
    return {"state": "stopped" if operation == "stop" else "started", "installRoot": str(root)}


if __name__ == "__main__":
    try:
        print(json.dumps(run(sys.argv[1], sys.argv[2])))
    except Exception as error:
        print(f"ODS WSL lifecycle failed: {error}", file=sys.stderr)
        raise SystemExit(1)
