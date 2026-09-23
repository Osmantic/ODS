#!/usr/bin/env python3
"""Credential-safe Discord handoff for the trusted owner mesh.

Moves the Discord-facing trusted Pixel channel from the restricted OpenClaw
profile to the rootless mesh profile. The restricted profile keeps every other
channel, plugin, tool, message, and agent customization intact; only the
Discord channel (and its plugin entry) is disabled there and enabled on the
mesh profile. Mesh profile customizations are preserved.

Safety contract
---------------
- Generalizes owner wording; contains no hardcoded personal name, host path,
  account ID, Discord ID, token, or credential.
- Every child process is an explicit argv list; no shell interpolation.
- Preflights all required files and the OpenClaw executable before creating
  any backup or mutating any state.
- Default invocation is a non-mutating dry-run inspection. Migration requires
  an explicit ``--apply``.
- Restricted config, mesh config, and the relevant workspace instructions are
  backed up with private modes before any mutation. Config contents and
  secrets are never printed.
- Candidate configs are validated with ``openclaw config validate`` before
  being written live. All writes are atomic and private. On any subsequent
  failure the exact backups are restored.
- The unprivileged user is assumed unable to restart the system
  ``openclaw-gateway.service``. Only the user mesh service is restarted; the
  restricted system gateway restart is reported as requiring separate
  authorization and never attempted via sudo.
- An explicit restore mode restores a validated backup directory under the
  configured private backup root.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VERSION = "mesh-discord-handoff-0.1.0"
BACKUP_SCHEMA = 1
MESH_SERVICE = "pixel-mesh-gateway.service"

OVERRIDE_MARKER = "# TRUSTED OWNER MESH OVERRIDE"
TRUSTED_OVERRIDE = """# TRUSTED OWNER MESH OVERRIDE — outranks conflicting rules below

The owner has explicitly authorized this Pixel to act as a trusted local
operator on the fixed three-tower fleet (Tower1, Tower2, and Tower3). The
runtime is intentionally unsandboxed and uses the real Unix user's
filesystem, processes, Docker, network, and SSH access. Use those
capabilities proactively for research, planning, implementation, testing,
troubleshooting, and bounded peer collaboration when the owner's request
calls for them.

The exact fleet peers are Tower1, Tower2, and Tower3 through SSH aliases
tower1/tower2/tower3. Before claiming a tower or LAN resource is absent or
inaccessible, test the relevant peer path and report the observation time
and failure boundary. Local or container network state is never fleet-global
evidence. Use `pixel-mesh-peer fleet-status` for fresh evidence and pipe task
text to `pixel-mesh-peer message TowerN` to collaborate with a peer Pixel.
Prefer the local-first model router and local hardware for token-heavy
work.

Direct host exec, SSH, file edits outside the workspace, Docker, and peer
messaging are permitted when they are within the owner's request. Preserve
unrelated work, keep recoverable backups for material changes, never reveal
credentials, and verify results.

"""

# Env overrides with safe per-tower defaults (relative to the resolved home).
HOME_ENV = "PIXEL_MESH_HOME"
BIN_ENV = "PIXEL_MESH_OPENCLAW_BIN"
RESTRICTED_STATE_ENV = "PIXEL_MESH_RESTRICTED_STATE"
MESH_STATE_ENV = "PIXEL_MESH_STATE"
BACKUP_ROOT_ENV = "PIXEL_MESH_BACKUP_ROOT"


@dataclass(frozen=True)
class Config:
    binary: Path
    restricted_state: Path
    mesh_state: Path
    backup_root: Path
    mesh_service: str

    @property
    def restricted_config(self) -> Path:
        return self.restricted_state / "openclaw.json"

    @property
    def mesh_config(self) -> Path:
        return self.mesh_state / "openclaw.json"

    @property
    def instructions(self) -> Path:
        return self.restricted_state / "workspace-pixel" / "AGENTS.md"

    @property
    def plugin_projects(self) -> Path:
        return self.restricted_state / "npm" / "projects"


def _base_home() -> Path:
    return Path(os.environ.get(HOME_ENV) or Path.home())


def resolve_config() -> Config:
    home = _base_home()
    restricted_state = Path(os.environ.get(RESTRICTED_STATE_ENV) or home / ".openclaw")
    mesh_state = Path(os.environ.get(MESH_STATE_ENV) or home / ".openclaw-mesh")
    binary = Path(os.environ.get(BIN_ENV) or home / ".npm-global" / "bin" / "openclaw")
    backup_root = Path(os.environ.get(BACKUP_ROOT_ENV) or home / ".local" / "state" / "pixel-mesh" / "discord-migration")
    return Config(binary=binary, restricted_state=restricted_state, mesh_state=mesh_state,
                  backup_root=backup_root, mesh_service=MESH_SERVICE)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_bytes(data)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict) -> None:
    data = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, data)


def atomic_private_copy(src: Path, dst: Path) -> None:
    _atomic_write_bytes(dst, src.read_bytes())


def preflight(cfg: Config) -> None:
    missing: list[str] = []
    if not cfg.binary.is_file():
        missing.append(f"OpenClaw binary missing: {cfg.binary}")
    elif not os.access(cfg.binary, os.X_OK):
        missing.append(f"OpenClaw binary not executable: {cfg.binary}")
    for label, path in (("restricted config", cfg.restricted_config),
                        ("mesh config", cfg.mesh_config),
                        ("workspace instructions", cfg.instructions)):
        if not path.is_file():
            missing.append(f"{label} missing: {path}")
    if missing:
        raise SystemExit("preflight failed: " + "; ".join(missing))


def create_backup(cfg: Config) -> Path:
    backup_dir = cfg.backup_root / f"{now()}-{secrets.token_hex(3)}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    copies = (
        ("restricted-openclaw.json", cfg.restricted_config),
        ("mesh-openclaw.json", cfg.mesh_config),
        ("AGENTS.md", cfg.instructions),
    )
    for name, src in copies:
        shutil.copy2(src, backup_dir / name)
        os.chmod(backup_dir / name, 0o600)
    manifest = {
        "schemaVersion": BACKUP_SCHEMA,
        "createdAt": now(),
        "tool": VERSION,
        "restrictedConfig": "restricted-openclaw.json",
        "meshConfig": "mesh-openclaw.json",
        "instructions": "AGENTS.md",
        "meshService": cfg.mesh_service,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.chmod(backup_dir / "manifest.json", 0o600)
    return backup_dir


def validate_config(binary: Path, state_dir: Path, config_file: Path) -> None:
    environment = {**os.environ,
                   "OPENCLAW_STATE_DIR": str(state_dir),
                   "OPENCLAW_CONFIG_PATH": str(config_file)}
    subprocess.run([str(binary), "config", "validate"], env=environment, check=True)


def build_candidates(live: dict, mesh: dict, cfg: Config) -> tuple[dict, dict]:
    """Return (mesh_candidate, restricted_candidate) enabling Discord only on mesh.

    Preserves unrelated channels, plugins, tools, messages, and mesh profile
    customizations. Only the Discord channel/plugin is toggled: enabled on the
    mesh profile, disabled on the restricted profile.
    """
    discord = (live.get("channels") or {}).get("discord")
    if not isinstance(discord, dict) or not discord.get("token"):
        raise SystemExit("restricted profile has no Discord channel with a token to migrate")

    mesh_cand = copy.deepcopy(mesh)
    channels = mesh_cand.setdefault("channels", {})
    channels["discord"] = copy.deepcopy(discord)
    channels["discord"]["enabled"] = True
    plugins = mesh_cand.setdefault("plugins", {})
    allow = plugins.setdefault("allow", [])
    if "discord" not in allow:
        allow.append("discord")
    plugins.setdefault("entries", {}).setdefault("discord", {})["enabled"] = True
    plugin_paths = plugins.setdefault("load", {}).setdefault("paths", [])
    for installed in sorted(cfg.plugin_projects.glob("openclaw-discord-*/node_modules/@openclaw/discord")):
        if str(installed) not in plugin_paths:
            plugin_paths.append(str(installed))
    mesh_agents = mesh_cand.get("agents", {}).get("list", [])
    if mesh_agents and isinstance(mesh_agents[0], dict):
        # Preserve the established Pixel persona/memory workspace while changing
        # only the authority-bearing runtime from restricted to owner-trusted.
        mesh_agents[0]["workspace"] = str(cfg.instructions.parent)

    live_cand = copy.deepcopy(live)
    live_cand.setdefault("channels", {}).setdefault("discord", {})["enabled"] = False
    live_cand.setdefault("plugins", {}).setdefault("entries", {}).setdefault("discord", {})["enabled"] = False
    return mesh_cand, live_cand


def update_instructions(cfg: Config) -> None:
    text = cfg.instructions.read_text(encoding="utf-8")
    if text.startswith(OVERRIDE_MARKER):
        return
    _atomic_write_bytes(cfg.instructions, (TRUSTED_OVERRIDE + text).encode("utf-8"))


def restart_user_mesh_service(cfg: Config) -> None:
    subprocess.run(["systemctl", "--user", "restart", cfg.mesh_service], check=True)


def restore(cfg: Config, backup_dir: Path) -> None:
    root = cfg.backup_root.resolve()
    target = backup_dir.resolve()
    if target != root and root not in target.parents:
        raise SystemExit(f"backup directory is not under the configured backup root: {backup_dir}")
    for name in ("restricted-openclaw.json", "mesh-openclaw.json", "AGENTS.md", "manifest.json"):
        if not (target / name).is_file():
            raise SystemExit(f"invalid backup directory, missing {name}: {target / name}")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    expected_manifest = {
        "schemaVersion": BACKUP_SCHEMA,
        "tool": VERSION,
        "restrictedConfig": "restricted-openclaw.json",
        "meshConfig": "mesh-openclaw.json",
        "instructions": "AGENTS.md",
        "meshService": cfg.mesh_service,
    }
    if any(manifest.get(key) != value for key, value in expected_manifest.items()):
        raise SystemExit(f"unsupported backup manifest schema: {manifest.get('schemaVersion')}")
    validate_config(cfg.binary, cfg.restricted_state, target / "restricted-openclaw.json")
    validate_config(cfg.binary, cfg.mesh_state, target / "mesh-openclaw.json")
    atomic_private_copy(target / "restricted-openclaw.json", cfg.restricted_config)
    atomic_private_copy(target / "mesh-openclaw.json", cfg.mesh_config)
    atomic_private_copy(target / "AGENTS.md", cfg.instructions)
    restart_user_mesh_service(cfg)
    print(json.dumps({"restored": True, "backup": str(target),
                      "meshService": cfg.mesh_service,
                      "restrictedConfig": str(cfg.restricted_config),
                      "meshConfig": str(cfg.mesh_config),
                      "instructions": str(cfg.instructions),
                      "restrictedSystemGatewayRestart": (
                          "required but NOT performed automatically; requires a "
                          "separately authorized restart of the system "
                          "openclaw-gateway.service. Verify Discord is served by "
                          "the mesh gateway before restarting it."
                      )}))


def dry_run(cfg: Config) -> int:
    preflight(cfg)
    live = json.loads(cfg.restricted_config.read_text(encoding="utf-8"))
    mesh = json.loads(cfg.mesh_config.read_text(encoding="utf-8"))
    build_candidates(live, mesh, cfg)  # raises if no Discord token to migrate
    print(json.dumps({
        "dryRun": True,
        "tool": VERSION,
        "restrictedConfig": str(cfg.restricted_config),
        "meshConfig": str(cfg.mesh_config),
        "instructions": str(cfg.instructions),
        "backupRoot": str(cfg.backup_root),
        "meshService": cfg.mesh_service,
        "actions": [
            "backup restricted config, mesh config, and workspace instructions (private modes)",
            "enable Discord channel/plugin on mesh profile",
            "disable Discord channel/plugin on restricted profile",
            "prepend trusted-owner override to restricted workspace instructions",
            "restart user mesh service",
        ],
        "restrictedSystemGatewayRestart": (
            "NOT performed automatically; requires a separately authorized "
            "restart of the system openclaw-gateway.service. Verify Discord "
            "lands on the mesh gateway before restarting the restricted gateway."
        ),
    }))
    return 0


def apply_migration(cfg: Config) -> int:
    preflight(cfg)
    live = json.loads(cfg.restricted_config.read_text(encoding="utf-8"))
    mesh = json.loads(cfg.mesh_config.read_text(encoding="utf-8"))
    mesh_cand, live_cand = build_candidates(live, mesh, cfg)
    # Validate both candidate configs before any live write.
    with tempfile.TemporaryDirectory(prefix="pixel-mesh-stage-") as tmp:
        staged = Path(tmp)
        staged_mesh = staged / "mesh.json"
        staged_live = staged / "restricted.json"
        staged_mesh.write_text(json.dumps(mesh_cand, indent=2) + "\n", encoding="utf-8")
        staged_live.write_text(json.dumps(live_cand, indent=2) + "\n", encoding="utf-8")
        validate_config(cfg.binary, cfg.mesh_state, staged_mesh)
        validate_config(cfg.binary, cfg.restricted_state, staged_live)
    backup_dir = create_backup(cfg)
    try:
        atomic_json(cfg.mesh_config, mesh_cand)
        atomic_json(cfg.restricted_config, live_cand)
        update_instructions(cfg)
        restart_user_mesh_service(cfg)
    except Exception as original:
        # Roll back to the exact backups. If the rollback itself also fails
        # (e.g. the mesh service restart is unavailable), surface BOTH failures
        # and never mask the original error.
        try:
            restore(cfg, backup_dir)
        except Exception as rollback_err:
            raise RuntimeError(
                "migration failed and automatic rollback also failed; "
                f"original error: {original!r}; rollback error: {rollback_err!r}"
            ) from original
        raise
    print(json.dumps({
        "migrated": True,
        "tool": VERSION,
        "backup": str(backup_dir),
        "meshService": cfg.mesh_service,
        "restrictedDiscordEnabled": False,
        "trustedDiscordEnabled": True,
        "restrictedSystemGatewayRestart": (
            "required but NOT performed automatically; requires a separately "
            "authorized restart of the system openclaw-gateway.service. Verify "
            "Discord is served by the mesh gateway before restarting it."
        ),
    }))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Move the Discord-facing Pixel channel to the trusted mesh profile.")
    parser.add_argument("--apply", action="store_true",
                        help="perform the migration (default is a non-mutating dry-run inspection)")
    parser.add_argument("--restore", metavar="BACKUP_DIR",
                        help="restore a validated backup directory under the configured backup root")
    args = parser.parse_args(argv)
    cfg = resolve_config()
    if args.restore:
        restore(cfg, Path(args.restore))
        return 0
    if args.apply:
        return apply_migration(cfg)
    return dry_run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
