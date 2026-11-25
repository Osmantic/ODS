"""Keep the retired Lemonade runtime out of the Linux runtime and installer.

ODS no longer installs or launches Lemonade: AMD runs the upstream llama.cpp
server like every other backend. This guard fails when a retired file comes
back, when the upgrade manifest stops covering one, or when a new Lemonade
reference appears in the installer, compose or script surfaces outside the
compatibility code listed below.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "installers/lib/retired-lemonade-files.sha256"

# Files ODS shipped for Lemonade. installers/phases/06-directories.sh deletes
# an upgraded install's copy when it is byte-identical to a shipped version.
RETIRED_FILES = {
    "docker-compose.lemonade-external.yml",
    "extensions/services/llama-server/Dockerfile.amd",
    "extensions/services/llama-server/lemonade-entrypoint.sh",
    "scripts/select-external-lemonade-model.py",
    "config/litellm/strix-halo-config.yaml",
    "config/litellm/lemonade.yaml",
}

# Surfaces this guard scans (relative to ods/). The host agent's renderer and
# model preservation scripts are retired by their own package.
SCANNED_ROOTS = (
    "installers/lib", "installers/phases", "installers/macos", "lib", "scripts",
    "migrations", "extensions/services/llama-server", "config/backends", "config/litellm",
)
SCANNED_FILES = (
    ".env.example", ".env.schema.json", "Makefile", "ods-cli", "install-core.sh",
    "install.sh", "ods-uninstall.sh", "config/dependency-lock.json",
)
NOT_SCANNED = {"scripts/render-runtime-configs.py", "scripts/preserve-active-model.py"}

# Every file here may mention Lemonade, for the reason given. Remove an entry
# when its reference goes (most are one-release compatibility reads).
ALLOWED = {
    ".env.example": "names Lemonade as a server an owner may run behind --external-llm-url",
    ".env.schema.json": "keeps the retired keys valid (deprecated) for one release",
    "Makefile": "runs the WSL bridge and Windows tests other packages own",
    "docker-compose.amd-rocm.yml": "documents parity with the pre-Lemonade ROCm setup",
    "install-core.sh": "maps the retired --lemonade-* flags for one release",
    "install.sh": "documents that mapping",
    "installers/lib/install-mode.sh": "reads ODS_MODE=lemonade as local for one release",
    "installers/lib/lemonade-migration.sh": "runs the Lemonade-era settings migration",
    "installers/lib/native-llm.sh": "accepts the retired flags' /api/v1 suffix",
    "installers/lib/retired-lemonade-files.sha256": "lists the files upgrades delete",
    "installers/macos/lib/pixel-native-continuation.py": "refuses a retained legacy external route before resuming a native model download",
    "installers/phases/02-detection.sh": "resolves a retired --lemonade-model id",
    "installers/phases/06-directories.sh": "deletes unchanged retired files on upgrade",
    "migrations/migrate-v3.1.0.sh": "the settings migration for source updates",
    "ods-cli": "re-resolves a cached stack that still names the retired overlay",
    "ods-uninstall.sh": "removes the retired Lemonade image",
    "scripts/bootstrap-upgrade.sh": "Windows (is_windows_bash) blocks the Windows package owns",
    "scripts/build-installation-context.py": "keeps the local-lemonade prompt profile the host agent passes",
    "scripts/configure-wsl-model-store.py": "uses the WSL bridge's Lemonade-era module and keys",
    "scripts/retire-wsl-runtime.py": "uses the WSL bridge's Lemonade-era module and keys",
    "scripts/migrate-lemonade-install.py": "the settings migration",
    "scripts/ods-doctor.sh": "diagnoses an unmigrated Lemonade-era .env",
    "scripts/resolve-compose-stack.sh": "reads ODS_MODE=lemonade as local; GAIA's own Lemonade Server",
    "scripts/uninstall-compose-volumes.py": "proves custody of the retired Lemonade volumes",
}

# Never again in any compose file or dependency pin.
RETIRED_ARTIFACTS = re.compile(r"lemonade-sdk|ods-lemonade-server|lemonade-entrypoint|Dockerfile\.amd")


def manifest_paths() -> set[str]:
    paths = set()
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, path = line.split()
        assert re.fullmatch(r"[0-9a-f]{64}", digest), line
        assert ".." not in path and not path.startswith("/"), line
        paths.add(path)
    return paths


def scanned_files() -> list[Path]:
    files = [ROOT / name for name in SCANNED_FILES]
    files += sorted(ROOT.glob("docker-compose*.yml"))
    for root in SCANNED_ROOTS:
        files += sorted(path for path in (ROOT / root).rglob("*")
                        if path.is_file() and "__pycache__" not in path.parts)
    return [path for path in files
            if path.relative_to(ROOT).as_posix() not in NOT_SCANNED]


def test_retired_files_are_not_shipped() -> None:
    shipped = sorted(path for path in RETIRED_FILES if (ROOT / path).exists())
    assert shipped == [], f"retired Lemonade files are back: {shipped}"


def test_upgrade_manifest_covers_every_retired_file() -> None:
    assert manifest_paths() == RETIRED_FILES
    phase06 = (ROOT / "installers/phases/06-directories.sh").read_text(encoding="utf-8")
    for path in RETIRED_FILES:
        assert path in phase06, f"Phase 06 does not retire an upgraded install's {path}"


def test_no_new_lemonade_references() -> None:
    unexpected = []
    for path in scanned_files():
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if "lemonade" in text.lower() and relative not in ALLOWED:
            unexpected.append(relative)
    assert unexpected == [], (
        "New Lemonade references; ODS no longer ships Lemonade. Remove them, or add the "
        f"file to ALLOWED with the reason it needs one: {unexpected}"
    )


def test_compose_files_and_pins_name_no_lemonade_artifact() -> None:
    files = sorted(ROOT.glob("docker-compose*.yml"))
    files += sorted(ROOT.glob("extensions/services/*/compose*.y*ml"))
    files += [ROOT / "config/dependency-lock.json", ROOT / "config/backends/amd.json"]
    found = [path.relative_to(ROOT).as_posix() for path in files
             if RETIRED_ARTIFACTS.search(path.read_text(encoding="utf-8"))]
    assert found == [], f"compose files or pins still name Lemonade artifacts: {found}"
