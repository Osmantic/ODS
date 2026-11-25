"""Keep Lemonade out of the host agent, model management, the dashboard and routing.

Round F serves every managed model through upstream llama-server. This guard
fails when a file this package retired comes back, or when a Lemonade
reference appears in these surfaces outside the one-release compatibility
code listed below (installer, compose and Windows surfaces have their own
guards).
"""

from __future__ import annotations

from pathlib import Path

ODS = Path(__file__).resolve().parents[4]

RETIRED_FILES = (
    "extensions/services/dashboard-api/lemonade_client.py",
    "extensions/services/dashboard/src/components/ExternalLemonadeAdoption.jsx",
    "config/litellm/lemonade.yaml",
    "config/litellm/strix-halo-config.yaml",
)

SCANNED_ROOTS = (
    "bin",
    "extensions/services/dashboard-api",
    "extensions/services/dashboard/src",
    "extensions/services/model-router/app",
    "extensions/services/perplexica",
    "extensions/services/hermes",
    "config/litellm",
)
SCANNED_FILES = (
    "config/model-library.json",
    "config/model-state.schema.v1.json",
    "config/generated-config-contracts.json",
    "scripts/render-runtime-configs.py",
    "scripts/preserve-active-model.py",
)
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".css", ".json", ".yaml", ".yml", ".sh", ".template", ".md"}

# Every file here may mention Lemonade, for the reason given. Remove an entry
# when its reference goes (most are one-release compatibility reads).
ALLOWED = {
    "bin/ods-host-agent.py": (
        "reads ODS_MODE=lemonade, an unmigrated external .env and the old Pixel journal names; "
        "replaces a retired route; retired endpoints answer 410; passes the local-lemonade prompt profile"
    ),
    "bin/model_switchboard/state.py": "reads the retired lemonade route kind so it can be replaced",
    "bin/model_switchboard/wsl_lemonade.py": "import alias for scripts shipped before round F",
    "bin/model_switchboard/wsl_runtime.py": "reads the LEMONADE_* bridge keys of an unmigrated .env",
    "bin/pixel_provider/sharing.py": "moves grants pinned to a retired Lemonade id",
    "extensions/services/dashboard-api/config.py": "reads ODS_MODE=lemonade and LLM_BACKEND=lemonade as llama-server",
    "extensions/services/dashboard-api/model_mtp.py": "refuses memory fits measured through Lemonade",
    "extensions/services/dashboard-api/model_stores.py": "ignores memory fits measured through Lemonade",
    "extensions/services/dashboard-api/performance_oracle.py": "reads an unmigrated .env and the retired id forms",
    "extensions/services/dashboard-api/routers/gpu.py": "reads the retired runtime name and base URL key",
    "extensions/services/dashboard-api/routers/models.py": (
        "retired adoption routes answer 410; retired id forms and an unmigrated external .env are read"
    ),
    "extensions/services/dashboard-api/settings.py": "lists the retired Lemonade .env keys so Settings can clear them",
    "extensions/services/dashboard/src/index.css": "the lemonade colour theme, unrelated to the runtime",
    "extensions/services/dashboard/src/components/dashboard-sign-in.css": "the lemonade colour theme",
    "extensions/services/model-router/app/main.py": "reads the retired lemonade state kind",
    "config/model-library.json": "fleet evidence prose recorded before round F",
    "config/model-state.schema.v1.json": "the retired lemonade kind stays readable",
    "config/generated-config-contracts.json": "the env schema keeps ODS_MODE=lemonade valid for one release",
    "scripts/render-runtime-configs.py": "accepts and ignores the retired flags and mode (R13)",
    "scripts/preserve-active-model.py": "accepts the retired helper flags and Lemonade id forms",
}


def _scanned_files() -> list[Path]:
    files = [ODS / name for name in SCANNED_FILES]
    for root in SCANNED_ROOTS:
        files += sorted(
            path for path in (ODS / root).rglob("*")
            if path.is_file() and path.suffix in SOURCE_SUFFIXES
            and not {"tests", "__tests__", "__pycache__", "node_modules"} & set(path.parts)
            and ".test." not in path.name
        )
    return files


def test_retired_files_stay_removed():
    back = [name for name in RETIRED_FILES if (ODS / name).exists()]
    assert back == [], f"retired Lemonade files are back: {back}"


def test_no_new_lemonade_references():
    unexpected = sorted(
        path.relative_to(ODS).as_posix() for path in _scanned_files()
        if "lemonade" in path.read_text(encoding="utf-8", errors="replace").lower()
        and path.relative_to(ODS).as_posix() not in ALLOWED
    )
    assert unexpected == [], (
        "New Lemonade references; ODS no longer runs Lemonade. Remove them, or add the "
        f"file to ALLOWED with the reason it needs one: {unexpected}"
    )


def test_allowed_entries_are_still_needed():
    stale = sorted(
        name for name in ALLOWED
        if not (ODS / name).exists()
        or "lemonade" not in (ODS / name).read_text(encoding="utf-8", errors="replace").lower()
    )
    assert stale == [], f"remove these entries, they no longer mention Lemonade: {stale}"
