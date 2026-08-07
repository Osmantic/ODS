#!/usr/bin/env python3
"""Validate the host-side model artifacts needed for offline ODS operation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


DEFAULT_STT_MODEL = "Systran/faster-whisper-base"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"

WHISPER_COMPOSE = "extensions/services/whisper/compose.yaml"
EMBEDDINGS_COMPOSE = "extensions/services/embeddings/compose.yaml"
TTS_COMPOSE = "extensions/services/tts/compose.yaml"
MODEL_ARTIFACT_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".model",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
}
HF_REPO_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$"
)


def ods_root() -> Path:
    """Return the install root, allowing an explicit override for tooling/tests."""
    override = os.environ.get("ODS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def env_value(root: Path, key: str, default: str = "") -> str:
    """Read one KEY=value from the install's .env, or return ``default``."""
    env_path = root / ".env"
    if not env_path.is_file():
        return default

    prefix = f"{key}="
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value or default
    return default


def model_candidates(cache_root: Path, repo_id: str) -> list[Path]:
    """Return supported installer and manual layouts for a Hugging Face repo."""
    org_name = repo_id.replace("/", "--")
    return [
        cache_root / f"models--{org_name}",
        cache_root / repo_id,
        cache_root / repo_id.split("/")[-1],
    ]


def _usable_file(path: Path) -> bool:
    """A finalized model artifact must resolve to a non-empty regular file."""
    if path.name.endswith((".incomplete", ".lock", ".part")):
        return False
    if path.suffix.lower() not in MODEL_ARTIFACT_SUFFIXES:
        return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _tree_has_usable_file(path: Path) -> bool:
    try:
        return any(_usable_file(item) for item in path.rglob("*"))
    except OSError:
        return False


def hf_model_path(cache_root: Path, repo_id: str) -> Path | None:
    """Locate a usable snapshot for ``repo_id`` below ``cache_root``.

    Hugging Face cache directories are not complete merely because the
    ``models--org--repo`` directory exists. A finalized snapshot must contain
    at least one non-empty file (or a symlink resolving to one). Manually
    populated bare layouts use the same file-level readiness rule.
    """
    for candidate in model_candidates(cache_root, repo_id):
        if not candidate.is_dir():
            continue
        content_root = candidate / "snapshots" if candidate.name.startswith("models--") else candidate
        if content_root.is_dir() and _tree_has_usable_file(content_root):
            return candidate
    return None


def _compose_flags(root: Path) -> set[str] | None:
    flags_path = root / ".compose-flags"
    if not flags_path.is_file():
        return None
    value = flags_path.read_text(encoding="utf-8", errors="replace").strip()
    if not value:
        return None
    return {
        token.strip("\"'").replace("\\", "/").lower()
        for token in value.split()
    }


def _bool_value(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def service_enabled(
    root: Path,
    compose_fragment: str,
    env_keys: tuple[str, ...],
) -> bool:
    """Return whether a model-backed service belongs to the active stack.

    Once installation has produced ``.compose-flags``, that file is the
    runtime source of truth. Before then, explicit feature flags are honored;
    absent feature state preserves the historical preflight behavior and
    validates the model.
    """
    flags = _compose_flags(root)
    if flags is not None:
        expected = compose_fragment.lower()
        return any(
            token == expected or token.endswith(f"/{expected}")
            for token in flags
        )

    explicit: list[bool] = []
    for key in env_keys:
        raw = env_value(root, key)
        parsed = _bool_value(raw) if raw else None
        if parsed is not None:
            explicit.append(parsed)
    if explicit:
        return any(explicit)
    return True


def local_llm_required(root: Path) -> tuple[bool, str]:
    """Describe whether ODS owns a GGUF file for the configured LLM backend."""
    mode = env_value(root, "ODS_MODE", "local").lower()
    backend = env_value(root, "LLM_BACKEND", "llama-server").lower()
    if mode == "cloud" or backend in {"cloud", "litellm", "remote"}:
        return False, "cloud/remote backend"
    if mode == "lemonade" or backend == "lemonade":
        return False, "model storage is managed by Lemonade"
    return True, ""


def check_llm(root: Path) -> tuple[bool, str]:
    models_dir = root / "data" / "models"
    configured = env_value(root, "GGUF_FILE")
    if configured:
        if Path(configured).name != configured:
            return False, f"Invalid GGUF_FILE (expected a filename): {configured}"
        candidate = models_dir / configured
        if _usable_file(candidate):
            return True, f"OK: data/models/{configured}"
        return False, f"Not found or empty: data/models/{configured}"

    if models_dir.is_dir():
        candidates = sorted(
            path for path in models_dir.glob("*.gguf") if _usable_file(path)
        )
        if candidates:
            return True, f"OK: data/models/{candidates[0].name}"
    return False, "Not found: data/models/*.gguf"


def check_hf_model(
    root: Path,
    cache_dir: str,
    model_env: str,
    default_model: str,
) -> tuple[bool, str]:
    repo_id = env_value(root, model_env, default_model)
    if not HF_REPO_ID_RE.fullmatch(repo_id):
        return False, f"Invalid {model_env}: {repo_id or '<empty>'}"
    found = hf_model_path(root / cache_dir, repo_id)
    if found is None:
        return False, f"Not found or incomplete: {cache_dir}/ ({repo_id})"
    return True, f"OK: {found.relative_to(root)}"


def installer_command(platform_name: str | None = None) -> str:
    """Return the in-place installer command for the current platform."""
    return r".\install.ps1" if (platform_name or os.name) == "nt" else "./install.sh"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable readiness report")
    args = parser.parse_args(argv)
    root = ods_root()
    results: list[tuple[str, bool | None, str]] = []

    needs_llm, reason = local_llm_required(root)
    if needs_llm:
        ok, detail = check_llm(root)
        results.append(("Primary LLM (GGUF model)", ok, detail))
    else:
        results.append(("Primary LLM", None, f"Skipped: {reason}"))

    voice_enabled = service_enabled(root, WHISPER_COMPOSE, ("ENABLE_VOICE",))
    if voice_enabled:
        ok, detail = check_hf_model(
            root, "data/whisper", "AUDIO_STT_MODEL", DEFAULT_STT_MODEL
        )
        results.append(("Whisper STT model", ok, detail))
    else:
        results.append(("Whisper STT model", None, "Skipped: service is not active"))

    if service_enabled(root, TTS_COMPOSE, ("ENABLE_VOICE",)):
        results.append(
            ("Kokoro TTS voice", None, "Bundled with the pinned TTS image")
        )
    else:
        results.append(("Kokoro TTS voice", None, "Skipped: service is not active"))

    if service_enabled(
        root,
        EMBEDDINGS_COMPOSE,
        ("ENABLE_EMBEDDINGS", "ENABLE_RAG"),
    ):
        ok, detail = check_hf_model(
            root,
            "data/embeddings",
            "EMBEDDING_MODEL",
            DEFAULT_EMBEDDING_MODEL,
        )
        results.append(("Embedding model", ok, detail))
    else:
        results.append(("Embedding model", None, "Skipped: service is not active"))

    missing: list[str] = []
    report: list[dict[str, object]] = []
    for description, ok, detail in results:
        status = "OK" if ok is True else "MISSING" if ok is False else "SKIP"
        report.append({"name": description, "status": status.lower(), "detail": detail})
        if ok is False:
            missing.append(description)

    if args.json:
        print(json.dumps({"ready": not missing, "models": report}, sort_keys=True))
        return 0 if not missing else 1

    print("=" * 72)
    print("ODS Offline Mode - Model Validation")
    print("=" * 72)
    for item in report:
        print(f"[{str(item['status']).upper():7s}] {str(item['name']):30s} {item['detail']}")
    print("=" * 72)

    if not missing:
        print("All active host-side model artifacts are ready for offline mode.")
        return 0

    print(f"\nMISSING OR INCOMPLETE: {', '.join(missing)}")
    print("\nReconnect this host and rerun the installer in place:")
    print(f"  {installer_command()}")
    print("The installer preserves configuration/data and populates active model caches.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
