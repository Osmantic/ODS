#!/usr/bin/env python3
"""Migrate a Lemonade-era ODS installation to the upstream llama.cpp runtime.

ODS no longer installs or launches Lemonade. AMD GPUs run the llama.cpp
server image, and the Windows Portal runs llama-server.exe on Windows. This
helper moves an existing installation's settings across, and is safe to run
on every installation and on every update: it changes nothing when no
Lemonade-era setting is present.

    env           Rewrite .env in place. The installer runs this before Phase 02
                  preserves the active model and before validate-env.sh, so
                  the selected GGUF survives (Phase 02 refused
                  ODS_MODE=lemonade). Source updates run it from migrations/.
    retire-render Delete config/litellm/lemonade.yaml when it is still exactly
                  what ODS rendered or shipped; an edited file is kept.

Classification of a Lemonade-era .env:
    portal    An ODS-managed Windows runtime proven by data/wsl-lemonade-runtime.json,
              which the WSL model-store registration writes only after the Windows
              controller proved ownership (managed=true). It becomes the
              host-native llama-server route (NATIVE_LLM_*).
    external  LEMONADE_EXTERNAL=true without that proof: the owner's own Lemonade.
              It becomes the generic external OpenAI-compatible route
              (EXTERNAL_LLM_*); a user API key moves to
              config/litellm/external-upstream.key (0600).
    managed   Everything else: ODS's Linux AMD container (or a non-AMD install
              that only carries retired keys). An AMD install also gets
              data/lemonade-retired-volumes.json, which lets ods-uninstall.sh
              remove the Lemonade container's volumes and image; nothing
              mounts them after the upgrade, so no container can prove them.

Values are never printed: the summary names keys only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "extensions/services/dashboard-api"))
from env_values import parse_env_value, quote_env_value  # noqa: E402

# Retired with the Lemonade runtime. The schema keeps them as deprecated for
# one release so an unmigrated .env still validates.
RETIRED_KEYS = (
    "LEMONADE_EXTERNAL",
    "LEMONADE_HOST_TRANSPORT",
    "LEMONADE_BASE_URL",
    "LEMONADE_CONTAINER_BASE_URL",
    "LEMONADE_API_BASE_PATH",
    "LEMONADE_MODEL",
    "LEMONADE_API_KEY",
    "LITELLM_LEMONADE_API_KEY",
    "LEMONADE_SERVER_IMAGE",
    "LEMONADE_LLAMACPP",
    "LEMONADE_LLAMACPP_ROCM_BIN",
    "LLAMA_CPP_REF",
    "AMDGPU_TARGET",
    "HSA_XNACK",
)
LEGACY_RUNTIME_MODES = {"external-lemonade", "windows-legacy-lemonade"}
# The LLM_API_URL the Lemonade-era installer wrote for managed AMD. Routed
# through model-router's llama-server-default endpoint it would loop back
# into LiteLLM, so only this exact value is moved to llama-server.
OLD_AMD_LLM_API_URL = "http://litellm:4000"
NEW_AMD_LLM_API_URL = "http://llama-server:8080"
# The installer generated this LiteLLM-to-Lemonade key; a user key differs.
GENERATED_PROVIDER_KEY_RE = re.compile(r"sk-ods-lemonade-[0-9a-f]{32}")
AMD_LLAMA_IMAGE_RE = re.compile(r"ghcr\.io/ggml-org/llama\.cpp:server-(vulkan|rocm)-")
ASSIGNMENT_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
PROOF_FILE = "data/wsl-lemonade-runtime.json"
VOLUME_RECORD = "data/lemonade-retired-volumes.json"
RETIRED_VOLUME_KEYS = ("lemonade-cache", "lemonade-llama", "lemonade-recipe")
RETIRED_IMAGE = "ods-lemonade-server:latest"
KEY_FILE = "config/litellm/external-upstream.key"
RENDER_FILE = "config/litellm/lemonade.yaml"


class MigrationError(Exception):
    """The installation cannot be migrated safely; nothing was changed."""


class EnvFile:
    """Line-preserving .env editor. Lookups use the first assignment, as the
    installer's _env_get and grep readers do."""

    def __init__(self, path: Path):
        self.path = path
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise MigrationError(f"{path} must be a regular file")
        self.mode = stat.S_IMODE(info.st_mode)
        self.lines = path.read_text(encoding="utf-8").splitlines()
        self.changed: list[str] = []

    def _indexes(self, key: str) -> list[int]:
        return [index for index, line in enumerate(self.lines)
                if (match := ASSIGNMENT_RE.match(line)) and match.group(2) == key]

    def has(self, key: str) -> bool:
        return bool(self._indexes(key))

    def get(self, key: str, default: str = "") -> str:
        indexes = self._indexes(key)
        if not indexes:
            return default
        return parse_env_value(ASSIGNMENT_RE.match(self.lines[indexes[0]]).group(3))

    def set(self, key: str, value: str) -> None:
        indexes = self._indexes(key)
        if indexes and len(indexes) == 1 and self.get(key) == value:
            return
        line = f"{key}={quote_env_value(value)}"
        if indexes:
            self.lines[indexes[0]] = line
            for index in reversed(indexes[1:]):
                del self.lines[index]
        else:
            self.lines.append(line)
        self.changed.append(key)

    def remove(self, key: str) -> None:
        indexes = self._indexes(key)
        for index in reversed(indexes):
            del self.lines[index]
        if indexes:
            self.changed.append(key)

    def write(self) -> None:
        if self.path.is_symlink():
            raise MigrationError(f"{self.path} must not be a symlink")
        descriptor, temporary = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                os.chmod(temporary, self.mode)
                stream.write("\n".join(self.lines) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def normalize_origin(url: str) -> str:
    """http(s)://host:port without a trailing slash, /v1, /api/v1 or /api."""
    url = url.strip().rstrip("/")
    for suffix in ("/api/v1", "/v1", "/api"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    if not re.fullmatch(r"https?://[A-Za-z0-9.\[\]:-]+(:[0-9]{1,5})?", url):
        raise MigrationError("a Lemonade base URL is not a plain http origin; rerun the installer with explicit flags")
    return url


def container_origin(origin: str) -> str:
    return re.sub(r"^http://(localhost|127\.0\.0\.1|\[::1\])(?=:|$)", "http://host.docker.internal", origin)


def origin_port(origin: str) -> str:
    match = re.search(r":([0-9]{1,5})$", origin)
    if match:
        return match.group(1)
    return "443" if origin.startswith("https://") else "80"


def is_legacy(env: EnvFile) -> bool:
    return (
        env.get("ODS_MODE").lower() == "lemonade"
        or env.get("LLM_BACKEND").lower() == "lemonade"
        or env.get("AMD_INFERENCE_RUNTIME").lower() == "lemonade"
        or env.get("AMD_INFERENCE_RUNTIME_MODE").lower() in LEGACY_RUNTIME_MODES
        or any(env.has(key) for key in RETIRED_KEYS)
    )


def portal_proof(install_dir: Path) -> bool:
    """True for the ownership record the WSL model-store registration writes."""
    path = install_dir / PROOF_FILE
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise MigrationError(f"{PROOF_FILE} is not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{PROOF_FILE} is not valid JSON") from exc
    return (
        isinstance(value, dict)
        and set(value) == {"schemaVersion", "planPath", "modelStoreId"}
        and value["schemaVersion"] == 1
        and isinstance(value["planPath"], str) and value["planPath"].startswith("/")
        and isinstance(value["modelStoreId"], str) and bool(value["modelStoreId"])
    )


def classify(env: EnvFile, install_dir: Path) -> str:
    external = env.get("LEMONADE_EXTERNAL").lower() in {"true", "1", "yes", "on"} or (
        env.get("AMD_INFERENCE_RUNTIME").lower() == "lemonade"
        and env.get("AMD_INFERENCE_MANAGED").lower() == "false"
    )
    if not external:
        return "managed"
    # The Portal wrote the external route with the model-router transport; the
    # record proves the Windows runtime is ODS-owned rather than the owner's.
    if env.get("LEMONADE_HOST_TRANSPORT") == "model-router" and portal_proof(install_dir):
        return "portal"
    return "external"


def was_managed_amd(env: EnvFile) -> bool:
    return (
        env.get("GPU_BACKEND").lower() == "amd"
        or env.get("ODS_MODE").lower() == "lemonade"
        or env.get("AMD_INFERENCE_RUNTIME").lower() == "lemonade"
    )


def retired_volumes_note(env: EnvFile) -> str:
    project = env.get("COMPOSE_PROJECT_NAME") or "ods"
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        project = "ods"
    volumes = " ".join(f"{project}_{key}" for key in RETIRED_VOLUME_KEYS)
    return (
        f"The Lemonade Docker volumes and the {RETIRED_IMAGE} image are no longer used; "
        "uninstalling ODS removes them. To free the space now, run after this update: "
        f"docker volume rm {volumes} && docker image rm {RETIRED_IMAGE}"
    )


def record_retired_volumes(install_dir: Path) -> None:
    """Record that this installation's Compose project owned the Lemonade volumes."""
    target = install_dir / VOLUME_RECORD
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise MigrationError(f"{VOLUME_RECORD} must be a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"schemaVersion": 1, "installDir": str(install_dir.resolve()),
              "volumeKeys": list(RETIRED_VOLUME_KEYS)}
    descriptor, temporary = tempfile.mkstemp(prefix=".lemonade-retired-volumes.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def migrate_common(env: EnvFile) -> None:
    if env.get("ODS_MODE").lower() == "lemonade":
        env.set("ODS_MODE", "local")
    if env.get("LLM_BACKEND").lower() == "lemonade":
        env.set("LLM_BACKEND", "llama-server")
    if env.get("LLM_API_BASE_PATH") == "/api/v1":
        env.set("LLM_API_BASE_PATH", "/v1")


def migrate_managed(env: EnvFile, notes: list[str]) -> None:
    if not was_managed_amd(env):
        # A non-AMD install only carries the retired lines that every
        # Lemonade-era installer wrote; nothing else changes.
        for key in ("AMD_INFERENCE_RUNTIME", "AMD_INFERENCE_RUNTIME_MODE"):
            if env.get(key).lower() in {"lemonade"} | LEGACY_RUNTIME_MODES:
                env.set(key, "")
        return
    gguf = env.get("GGUF_FILE")
    lemonade_model = env.get("LEMONADE_MODEL")
    if lemonade_model and gguf and lemonade_model not in {f"extra.{gguf}", Path(gguf).stem, gguf}:
        notes.append(
            "The Lemonade model id did not name the configured GGUF file; "
            f"the llama.cpp runtime serves GGUF_FILE ({gguf}) from data/models."
        )
    env.set("AMD_INFERENCE_RUNTIME", "llama-server")
    # The Lemonade-era value described Lemonade's own backend, not an owner
    # choice of image; the llama.cpp runtime defaults to Vulkan.
    env.set("AMD_INFERENCE_BACKEND", "vulkan")
    env.set("AMD_INFERENCE_LOCATION", "container")
    env.set("AMD_INFERENCE_PORT", "8080")
    env.set("AMD_INFERENCE_SUPPORTED_BACKENDS", "vulkan,rocm")
    env.set("AMD_INFERENCE_RUNTIME_MODE", "linux-container")
    env.set("AMD_INFERENCE_MANAGED", "true")
    if env.get("LLM_API_URL") == OLD_AMD_LLM_API_URL:
        env.set("LLM_API_URL", NEW_AMD_LLM_API_URL)
    if env.get("LLAMA_ARG_SPLIT_MODE") == "row":
        # Vulkan has no row split; the HIP backend shares CUDA's split code.
        env.set("LLAMA_ARG_SPLIT_MODE", "layer")
    for key in ("LLAMA_SERVER_IMAGE", "LLAMA_SERVER_IMAGE_FALLBACK"):
        value = env.get(key)
        if value and not AMD_LLAMA_IMAGE_RE.match(value):
            # Another backend's image (the gemma4 profile wrote CUDA here).
            env.remove(key)
    if env.get("HSA_OVERRIDE_GFX_VERSION") == "11.5.1" and env.get("AMDGPU_TARGET") == "gfx1151":
        # The installer's Strix Halo workaround for Lemonade's ROCm build. The
        # ROCm image is built for gfx1151 and Vulkan never reads it.
        env.remove("HSA_OVERRIDE_GFX_VERSION")
    if env.get("ROCBLAS_USE_HIPBLASLT") == "1" and env.has("AMDGPU_TARGET"):
        # Written with AMDGPU_TARGET by the Lemonade-era installer, not by the
        # owner; the ROCm overlay's default (0) is the setup ODS ran on Strix
        # Halo before Lemonade.
        env.remove("ROCBLAS_USE_HIPBLASLT")


def migrate_portal(env: EnvFile) -> None:
    host = normalize_origin(env.get("LEMONADE_BASE_URL") or "")
    container = env.get("LEMONADE_CONTAINER_BASE_URL")
    container = normalize_origin(container) if container else container_origin(host)
    env.set("NATIVE_LLM_BASE_URL", host)
    env.set("NATIVE_LLM_CONTAINER_BASE_URL", container)
    transport = env.get("LEMONADE_HOST_TRANSPORT") or "model-router"
    if transport not in {"direct", "model-router"}:
        raise MigrationError("LEMONADE_HOST_TRANSPORT must be direct or model-router")
    env.set("ODS_HOST_LLM_TRANSPORT", transport)
    port = env.get("AMD_INFERENCE_PORT") or origin_port(host)
    env.set("AMD_INFERENCE_RUNTIME", "llama-server")
    env.set("AMD_INFERENCE_BACKEND", "vulkan")
    env.set("AMD_INFERENCE_LOCATION", "host")
    env.set("AMD_INFERENCE_PORT", port)
    env.set("AMD_INFERENCE_SUPPORTED_BACKENDS", "vulkan")
    env.set("AMD_INFERENCE_RUNTIME_MODE", "windows-portal-llama-server")
    env.set("AMD_INFERENCE_MANAGED", "true")
    env.set("LLM_BACKEND", "llama-server")
    env.set("LLM_API_BASE_PATH", "/v1")


def migrate_external(env: EnvFile, install_dir: Path, notes: list[str], *, dry_run: bool) -> None:
    host = normalize_origin(env.get("LEMONADE_BASE_URL") or "http://localhost:13305")
    container = env.get("LEMONADE_CONTAINER_BASE_URL")
    container = normalize_origin(container) if container else container_origin(host)
    model = env.get("LEMONADE_MODEL")
    if not model:
        raise MigrationError(
            "the external Lemonade route has no LEMONADE_MODEL; rerun the installer with "
            "--external-llm-url URL --external-llm-provider openai-compatible --external-llm-model ID"
        )
    env.set("EXTERNAL_LLM_URL", host)
    env.set("EXTERNAL_LLM_CONTAINER_URL", container)
    env.set("EXTERNAL_LLM_PROVIDER", "openai-compatible")
    env.set("EXTERNAL_LLM_MODEL", model)
    env.set("SKIP_MODEL_DOWNLOAD", "true")
    env.set("ODS_MODE", "local")
    env.set("LLM_BACKEND", "external")
    env.set("LLM_API_BASE_PATH", "/v1")
    env.set("LLM_API_URL", "http://litellm:4000")
    env.set("LLM_MODEL", model)
    # The generic external route reaches the model through LiteLLM only; the
    # installer sets the same values for --external-llm-url.
    env.set("ODS_MODEL_SWITCHBOARD", "observe")
    litellm_key = env.get("LITELLM_KEY")
    env.set("OPEN_WEBUI_LLM_BASE_URL", "http://litellm:4000/v1")
    env.set("OPEN_WEBUI_LLM_API_KEY", litellm_key)
    env.set("HERMES_LLM_BASE_URL", "http://litellm:4000/v1")
    env.set("HERMES_LLM_API_KEY", litellm_key)
    for key in ("AMD_INFERENCE_RUNTIME", "AMD_INFERENCE_BACKEND", "AMD_INFERENCE_LOCATION",
                "AMD_INFERENCE_PORT", "AMD_INFERENCE_SUPPORTED_BACKENDS",
                "AMD_INFERENCE_RUNTIME_MODE", "AMD_INFERENCE_MANAGED"):
        env.set(key, "")
    user_key = env.get("LITELLM_LEMONADE_API_KEY")
    if user_key and not GENERATED_PROVIDER_KEY_RE.fullmatch(user_key):
        if dry_run:
            notes.append(f"Would move the Lemonade API key to {KEY_FILE} (owner-only).")
        else:
            store_external_key(install_dir, user_key, notes)


def store_external_key(install_dir: Path, value: str, notes: list[str]) -> None:
    """Move the owner's Lemonade API key into the generic external key file."""
    if any(ord(char) < 33 or ord(char) > 126 for char in value) or len(value) > 4096:
        raise MigrationError("the saved Lemonade API key is not one printable line")
    target = install_dir / KEY_FILE
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise MigrationError(f"{KEY_FILE} must be a regular file")
    if target.exists() and target.stat().st_size:
        if target.read_text(encoding="ascii").rstrip("\n") != value:
            notes.append(f"Kept the existing {KEY_FILE}; the saved Lemonade API key was not copied over it.")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates the file owner-only (0600).
    descriptor, temporary = tempfile.mkstemp(prefix=".external-upstream.", dir=target.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    notes.append(f"Moved the Lemonade API key to {KEY_FILE} (owner-only).")


def render_external(install_dir: Path, env: EnvFile) -> None:
    """Point LiteLLM at the external server, as the installer does."""
    command = [
        sys.executable, str(SOURCE / "scripts/render-runtime-configs.py"),
        "--surface", "litellm-external",
        "--model", env.get("EXTERNAL_LLM_MODEL"),
        "--llm-base-url", env.get("EXTERNAL_LLM_CONTAINER_URL"),
        "--output-root", str(install_dir), "--write",
    ]
    key = install_dir / KEY_FILE
    if key.is_file() and key.stat().st_size:
        command.append("--external-llm-authenticated")
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)


def migrate_env(install_dir: Path, *, dry_run: bool, render: bool) -> int:
    path = install_dir / ".env"
    if not path.exists():
        print("lemonade-migration: none (no .env)")
        return 0
    env = EnvFile(path)
    if not is_legacy(env):
        print("lemonade-migration: none")
        return 0
    notes: list[str] = []
    kind = classify(env, install_dir)
    # The Lemonade container ran only on managed Linux AMD installs.
    retired_volumes = kind == "managed" and was_managed_amd(env)
    if kind == "portal":
        migrate_portal(env)
    elif kind == "external":
        migrate_external(env, install_dir, notes, dry_run=dry_run)
    else:
        migrate_managed(env, notes)
    migrate_common(env)
    for key in RETIRED_KEYS:
        env.remove(key)
    if retired_volumes:
        notes.append(retired_volumes_note(env))
    print(f"lemonade-migration: {kind}")
    for note in notes:
        print(f"lemonade-migration: {note}")
    changed = sorted(set(env.changed))
    if dry_run:
        print("lemonade-migration: would change " + ", ".join(changed))
        return 0
    if retired_volumes:
        # Before .env: a failed write leaves the installation unmigrated, and
        # the next run writes the record again.
        record_retired_volumes(install_dir)
    env.write()
    print("lemonade-migration: changed " + ", ".join(changed))
    if render and kind == "external":
        render_external(install_dir, env)
        print("lemonade-migration: LiteLLM now routes to the external server")
    return 0


def _old_render(model: str, api_base: str, api_key: str) -> str:
    """The exact litellm-lemonade file the Lemonade-era renderer wrote."""
    entry = f"""    litellm_params:
      model: openai/{model}
      api_base: {api_base}
      api_key: {api_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false
"""
    return (
        "model_list:\n"
        f"  - model_name: ods/current\n{entry}\n"
        f"  - model_name: default\n{entry}\n"
        f"  - model_name: \"*\"\n{entry}\n"
        "litellm_settings:\n  drop_params: true\n  set_verbose: false\n"
        "  request_timeout: 900\n  stream_timeout: 900\n"
    )


def retire_render(install_dir: Path, manifest: Path) -> int:
    """Delete an unchanged lemonade.yaml render or shipped template copy."""
    import hashlib

    path = install_dir / RENDER_FILE
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        print(f"lemonade-migration: kept {RENDER_FILE} (not a regular file)")
        return 0
    data = path.read_bytes()
    shipped = {
        line.split()[0] for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and line.split()[1] == RENDER_FILE
    }
    unchanged = hashlib.sha256(data).hexdigest() in shipped
    if not unchanged:
        text = data.decode("utf-8", errors="replace")
        match = re.search(r"model: openai/(\S+)\n      api_base: (\S+)\n      api_key: (\S+)\n", text)
        unchanged = bool(match) and text == _old_render(*match.groups())
    if not unchanged:
        print(f"lemonade-migration: kept {RENDER_FILE} (changed since ODS wrote it; nothing reads it now)")
        return 0
    path.unlink()
    print(f"lemonade-migration: removed the unused {RENDER_FILE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("env", "retire-render"))
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Report the .env changes without writing")
    parser.add_argument("--render", action="store_true",
                        help="Also render LiteLLM for a migrated external route (source updates)")
    parser.add_argument("--manifest", type=Path,
                        default=SOURCE / "installers/lib/retired-lemonade-files.sha256")
    args = parser.parse_args()
    try:
        if args.action == "env":
            return migrate_env(args.install_dir, dry_run=args.dry_run, render=args.render)
        return retire_render(args.install_dir, args.manifest)
    except MigrationError as exc:
        print(f"lemonade-migration: {exc}. Nothing was changed.", file=sys.stderr)
        return 1
    except OSError as exc:
        # .env is replaced atomically, so a failed read or write leaves it as it was.
        print(f"lemonade-migration: cannot update {args.install_dir}: {exc.strerror or exc}. "
              ".env was not changed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
