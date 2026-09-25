"""Explicit additional model stores; no traversal or implicit recursive discovery.

The owner-maintained data/model-stores.json maps host directories to read-only
container mounts. Default data/models remains available. Ambiguous basenames
are excluded instead of silently selecting a different checkpoint.
"""
from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import re
import tempfile
from pathlib import Path, PureWindowsPath

MAX_REGISTRY_BYTES = 65536


def validate_profile_command(profile: dict, model_path: Path, *, lemonade: bool = False) -> None:
    # The host agent imports this module by filename, so resolve its adjacent
    # validator explicitly instead of depending on the host's sys.path.
    spec = importlib.util.spec_from_file_location('_ods_native_runtime_validation', Path(__file__).with_name('model_mtp.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    command = [profile['executable'], '--model', str(model_path), '--ctx-size', str(profile['contextLength']), '--jinja']
    if lemonade:
        # Lemonade 10 can add this on any machine with an iGPU, independently
        # of the selected accelerator. Check it before a running model stops.
        command += ['--no-mmap', '--gpu-layers', '99', '--context-shift', '--keep', '16', '--reasoning-format', 'auto', '--no-webui']
    command += profile['args']
    fit = profile.get('memoryQualification')
    if isinstance(fit, dict):
        projector = safe_artifact(Path(model_path).parent, fit.get('visionProjectorFile'))
        if projector is None:
            raise ValueError('The memory-qualified projector is unavailable')
        command += ['--mmproj', str(projector)]
    module.validate_runtime_command(command)


def resolve_runtime_selection(install_dir: Path, *, verify_hashes: bool = True, allow_missing_model: bool = False) -> dict:
    from env_values import parse_env_value
    install_dir = Path(install_dir)
    env = {}
    for line in (install_dir / ".env").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in {"ODS_ACTIVE_MODEL_STORE", "GGUF_FILE", "CTX_SIZE", "MAX_CONTEXT", "LLM_BACKEND", "AMD_INFERENCE_RUNTIME"}:
            env[key.strip()] = parse_env_value(value)
    if verify_hashes and allow_missing_model:
        raise ValueError("Artifact verification cannot use missing-model metadata")
    store = active_store(install_dir / "data", env.get("ODS_ACTIVE_MODEL_STORE", "default"), allow_missing=allow_missing_model)
    filename = env.get("GGUF_FILE", "")
    if not filename or any(c in filename for c in "/\\\x00\n\r") or filename in {".", ".."}:
        raise ValueError("Invalid active model filename")
    checkpoint = safe_artifact(store["path"], filename)
    available = checkpoint is not None
    if checkpoint is None and not allow_missing_model:
        raise ValueError("The active model is missing or outside its registered store")
    checkpoint = checkpoint or store["path"] / filename
    profile = native_profile(store, filename, require_executable=not allow_missing_model)
    if profile and verify_hashes:
        artifacts = [(checkpoint, profile.get("modelSha256")), (Path(profile["executable"]), profile.get("runtimeSha256"))]
        fit = profile.get("memoryQualification")
        if isinstance(fit, dict):
            projector = safe_artifact(store["path"], fit.get("visionProjectorFile"))
            if projector is None:
                raise ValueError("The qualified vision projector is missing")
            artifacts.append((projector, fit.get("visionProjectorSha256")))
        for path, expected in artifacts:
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("The active native profile has no valid artifact qualification")
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(8*1024*1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError("The active model/runtime changed since qualification")
    if profile:
        context = env.get("CTX_SIZE") or env.get("MAX_CONTEXT")
        if context and context.isdigit() and 4096 <= int(context) <= 262144:
            fit = profile.get("memoryQualification")
            if isinstance(fit, dict) and int(context) != fit.get("contextLength"):
                if verify_hashes:
                    raise ValueError("The selected context differs from the memory-qualified profile; requalify before restarting")
                # Status/Stop still need the qualified executable identity when
                # a changed setting is rejected by the next start preflight.
            else:
                profile["contextLength"] = int(context)
    if profile and verify_hashes:
        validate_profile_command(profile, checkpoint, lemonade=env.get('LLM_BACKEND') == 'lemonade' or env.get('AMD_INFERENCE_RUNTIME') == 'lemonade')
    return {"schemaVersion":1,"storeId":store["id"],"modelsDirectory":str(store["path"]),
            "modelPath":str(checkpoint),"available":available,"profile":profile}


def active_compose_overlay(install_dir: Path, identifier: str) -> Path | None:
    """Regenerate the active /models mount for both activation and cold start."""
    if not identifier or identifier == "default":
        return None
    store = active_store(Path(install_dir)/"data", identifier)
    target = Path(install_dir)/"data/.active-model-store.compose.json"
    if target.is_symlink():
        raise ValueError("Refusing a symlinked active model mount")
    content = {"services":{"llama-server":{"volumes":[{"type":"bind", "source":str(store["hostPath"]),
        "target":"/models", "read_only":True, "bind":{"create_host_path":False}}]}}}
    serialized = json.dumps(content, indent=2) + "\n"
    if target.is_file() and target.read_text(encoding="utf-8") == serialized:
        return target
    descriptor, temporary = tempfile.mkstemp(prefix=target.name+'.', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as destination:
            destination.write(serialized)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def validated_compose_overlay(install_dir: Path) -> Path | None:
    """Accept only the exact read-only mounts generated from the owner registry."""
    overlay = Path(install_dir) / ".model-stores.compose.json"
    registry = Path(install_dir) / "data/model-stores.json"
    if not overlay.exists():
        return None
    try:
        if any(path.is_symlink() or path.stat().st_size > MAX_REGISTRY_BYTES for path in (overlay, registry)):
            raise ValueError("Unsafe registered model mounts")
        document = json.loads(registry.read_text(encoding="utf-8"))
        entries = document.get("stores")
        if document.get("schemaVersion") != 1 or not isinstance(entries, list) or len(entries) > 16:
            raise ValueError("Invalid registered model stores")
        volumes, seen = [], set()
        for entry in entries:
            identifier, source = entry.get("id"), entry.get("hostPath")
            if not isinstance(identifier,str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", identifier) or identifier in seen or identifier == "default":
                raise ValueError("Invalid model store id")
            if not isinstance(source,str) or any(c in source for c in "\x00\n\r$") or not (Path(source).is_absolute() or PureWindowsPath(source).is_absolute()):
                raise ValueError("Model store requires an explicit host directory")
            seen.add(identifier)
            target = f"/model-stores/{identifier}"
            if entry.get("containerPath") != target:
                raise ValueError("Invalid container model directory")
            volumes.append({"type":"bind","source":source,"target":target,"read_only":True,"bind":{"create_host_path":False}})
        expected = {"services":{"dashboard-api":{"volumes":volumes}}}
        if json.loads(overlay.read_text(encoding="utf-8")) != expected:
            raise ValueError("Registered model mount overlay does not match its registry")
        return overlay
    except (OSError, TypeError, AttributeError) as exc:
        raise ValueError("Registered model mount configuration is unavailable") from exc


def registered_stores(data_dir: Path, *, container: bool = False, allow_missing: bool = False) -> list[dict]:
    data_dir = Path(data_dir)
    stores = [{"id": "default", "path": data_dir / "models", "hostPath": str(data_dir / "models"), "profiles": {}}]
    registry = data_dir / "model-stores.json"
    try:
        if registry.is_symlink() or registry.stat().st_size > MAX_REGISTRY_BYTES:
            return stores
        document = json.loads(registry.read_text(encoding="utf-8"))
        if document.get("schemaVersion") != 1 or not isinstance(document.get("stores"), list):
            return stores
        for entry in document["stores"][:16]:
            if not isinstance(entry, dict):
                continue
            identifier = entry.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", identifier) or identifier == "default":
                continue
            value = entry.get("containerPath" if container else "hostPath")
            if not isinstance(value, str) or any(c in value for c in "\x00\n\r"):
                continue
            root = Path(value)
            if not root.is_absolute() or (not allow_missing and not root.is_dir()):
                continue
            if container and value != f"/model-stores/{identifier}":
                continue
            if any(item["id"] == identifier or item["path"].resolve() == root.resolve() for item in stores):
                continue
            stores.append({"id": identifier, "path": root.resolve(), "hostPath": entry.get("hostPath"), "containerPath": entry.get("containerPath"), "profiles": entry.get("profiles", {})})
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return stores


def safe_artifact(root: Path, filename: str, *, allow_empty: bool = False) -> Path | None:
    if not isinstance(filename, str) or not filename or any(c in filename for c in "/\\\x00\n\r") or filename in {".", ".."}:
        return None
    try:
        root = root.resolve()
        target = root / filename
        if target.is_symlink() or not target.is_file() or (not allow_empty and target.stat().st_size <= 0):
            return None
        resolved = target.resolve()
        return resolved if resolved.parent == root else None
    except OSError:
        return None


def scan_model_files(data_dir: Path, *, container: bool = False, default_dir: Path | None = None) -> dict[str, Path]:
    stores = registered_stores(data_dir, container=container)
    if default_dir is not None:
        stores[0]["path"] = default_dir
    matches: dict[str, list[Path]] = {}
    for store in stores:
        try:
            for child in store["path"].iterdir():
                if not child.name.lower().endswith(".gguf") or child.name.lower().startswith(("mmproj", "mtp-")):
                    continue
                target = safe_artifact(store["path"], child.name)
                if target is not None:
                    group = matches.setdefault(child.name.lower(), [])
                    if target not in group:
                        group.append(target)
        except OSError:
            continue
    return {paths[0].name: paths[0] for paths in matches.values() if len(paths) == 1}


def resolve_model_file(data_dir: Path, filename: str, *, container: bool = False, default_dir: Path | None = None) -> Path | None:
    if not isinstance(filename, str) or any(c in filename for c in "/\\\x00\n\r"):
        return None
    return next((path for name, path in scan_model_files(data_dir, container=container, default_dir=default_dir).items()
                 if name.casefold() == filename.casefold()), None)


def store_for_model(data_dir: Path, filename: str, *, container: bool = False) -> dict | None:
    target = resolve_model_file(data_dir, filename, container=container)
    if target is None:
        return None
    return next((store for store in registered_stores(data_dir, container=container)
                 if store["path"].resolve() == target.parent), None)


def active_store(data_dir: Path, identifier: str = "default", *, container: bool = False, allow_missing: bool = False) -> dict:
    for store in registered_stores(data_dir, container=container, allow_missing=allow_missing):
        if store["id"] == (identifier or "default"):
            return store
    raise ValueError("The configured model store is unavailable; remount or register it before activating a model")


def lemonade_profile(data_dir: Path, filename: str, *, container: bool = False) -> dict | None:
    """Read a narrowly typed local runtime profile, never arbitrary shell text."""
    store = store_for_model(data_dir, filename, container=container)
    return native_profile(store, filename)


def native_profile(store: dict | None, filename: str, *, require_executable: bool = True) -> dict | None:
    profiles = store.get("profiles", {}) if store else {}
    if not isinstance(profiles, dict):
        raise ValueError("Invalid registered model profiles")
    row = profiles.get(filename) if isinstance(profiles, dict) else None
    if row is None:
        return None
    if not isinstance(row, dict) or row.get("backend") not in {"vulkan", "rocm", "cpu", "metal", "cuda"}:
        raise ValueError("Invalid registered model runtime backend")
    executable_value = row.get("executable")
    if not isinstance(executable_value, str) or any(c in executable_value for c in "\x00\n\r"):
        raise ValueError("Invalid registered runtime executable")
    executable = Path(executable_value)
    if not executable.is_absolute() or (require_executable and not executable.is_file()):
        raise ValueError("Registered model runtime executable is missing")
    context = row.get("contextLength")
    if type(context) is not int or not 4096 <= context <= 262144:
        raise ValueError("Invalid registered model context window")
    draft = row.get("draftTokens", 2)
    if type(draft) is not int or not 1 <= draft <= 6 or type(row.get("mtp")) is not bool:
        raise ValueError("Invalid registered MTP settings")
    load_args = row.get("loadModeArguments", [])
    if load_args not in ([], ["--mmap"], ["--load-mode", "mmap"]):
        raise ValueError("Invalid qualified model loading flags")
    args = ["--parallel", "1", "--flash-attn", "on", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", *load_args]
    if row["mtp"]:
        args += ["--spec-type", "draft-mtp", "--spec-draft-n-max", str(draft), "--spec-draft-type-k", "q4_0", "--spec-draft-type-v", "q4_0"]
    return {"backend": row["backend"], "executable": str(executable), "contextLength": context,
            "mtp": row["mtp"], "args": args, "storeId": store["id"],
            "runtimeSha256":row.get("runtimeSha256"), "modelSha256":row.get("modelSha256"),
            "memoryQualification":row.get("memoryQualification")}
