#!/usr/bin/env python3
"""Adjust nvidia-smi GPU rows for a vLLM rerun's own engine memory.

Input: one nvidia-smi row per line on stdin, ``UUID, CAP, TOTAL_MIB, FREE_MIB``.
Output: same rows with FREE_MIB credited by provably-owned engine memory,
clamped to TOTAL_MIB. Fail closed on malformed rows.

Credit rules (all must hold):
* Exactly one running engine container (llama-server/vllm-server) whose
  compose working_dir resolves to --install-dir and whose full 64-hex ID is
  known and running.
* For each numeric process usage on a GPU, /proc/PID/cgroup names exactly
  that container ID (docker/<64hex> or docker-<64hex>.scope).
* Container identity (ID, StartedAt, running) unchanged after all cgroup
  reads and credit computation.

Ambiguous/malformed/unreadable entries grant no credit. Docker metadata
failure => no credit (numeric free only). Unknown GPU free still fails.
Preflight estimate only; actual engine must still enforce start check.

Standard library only. Boundaries are narrow functions with None defaults
resolved at call time so tests can monkeypatch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Iterable


ENGINE_SERVICES = ("llama-server", "vllm-server")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
CGROUP_DOCKER = re.compile(r"(?:^|/)docker/([0-9a-f]{64})(?:$|/)")
CGROUP_SCOPE = re.compile(r"(?:^|/)docker-([0-9a-f]{64})\.scope(?:$|/)")
GPU_ROW = re.compile(r"\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([0-9]+)\s*,\s*([^,]+?)\s*$")


class AdmissionError(Exception):
    """The helper cannot safely decide; caller must fail closed."""


# ── narrow boundaries (monkeypatched in tests) ──────────────────────────────

def run_docker(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["docker", *args], check=False, capture_output=True,
            text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdmissionError(f"docker {args[0]} failed: {exc}") from exc
    if result.returncode != 0:
        raise AdmissionError(f"docker {args[0]} exited {result.returncode}")
    return result.stdout


def run_nvidia_smi() -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_gpu_memory",
             "--format=csv,noheader,nounits"],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdmissionError(f"nvidia-smi compute apps failed: {exc}") from exc
    if result.returncode != 0:
        raise AdmissionError(f"nvidia-smi compute apps exited {result.returncode}")
    return result.stdout


def read_cgroup(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError as exc:
        raise AdmissionError(f"cannot read /proc/{pid}/cgroup: {exc}") from exc


# ── pure parsing helpers ────────────────────────────────────────────────────

def parse_gpu_row(line: str) -> tuple[str, str, int, int]:
    match = GPU_ROW.match(line)
    if not match:
        raise AdmissionError(f"malformed GPU row: {line!r}")
    uuid, cap, total, free = match.groups()
    try:
        total_mib = int(total)
        free_mib = int(free)
    except ValueError as exc:
        raise AdmissionError(f"non-numeric memory in GPU row: {line!r}") from exc
    if total_mib <= 0:
        raise AdmissionError(f"non-positive total in GPU row: {line!r}")
    if free_mib < 0 or free_mib > total_mib:
        raise AdmissionError(f"invalid free in GPU row: {line!r}")
    return uuid, cap, total_mib, free_mib


def parse_compute_apps(text: str) -> list[tuple[str, int, int]]:
    """Parse ``gpu_uuid, pid, used_mib`` rows; skip non-numeric/invalid usage."""
    apps: list[tuple[str, int, int]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            continue
        uuid, pid_text, used_text = parts
        try:
            pid = int(pid_text)
            used = int(used_text)
        except ValueError:
            continue
        if pid <= 0 or used < 0:
            continue
        apps.append((uuid, pid, used))
    return apps


def _cgroup_path(line: str) -> str | None:
    """Extract path from a cgroup line ``hierarchy:controllers:path``."""
    parts = line.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[2]


def container_id_from_cgroup(text: str) -> str | None:
    """The single full 64-hex container ID named across all cgroup lines, or None.

    Collects all matches across all lines; requires exactly one distinct ID.
    """
    found: set[str] = set()
    for line in text.splitlines():
        path = _cgroup_path(line)
        if path is None:
            continue
        for pattern in (CGROUP_DOCKER, CGROUP_SCOPE):
            for match in pattern.finditer(path):
                found.add(match.group(1))
    if len(found) == 1:
        return next(iter(found))
    return None


def inspect_engine(install_dir: str, run_docker_fn=None) -> dict | None:
    """The installation's own running engine container, or None.

    Returns ``{"id", "service", "started_at", "pid"}`` for exactly one
    running container whose compose working_dir resolves to ``install_dir``
    and whose service is an engine service. Ambiguity raises AdmissionError.
    """
    if run_docker_fn is None:
        run_docker_fn = run_docker
    try:
        listing = run_docker_fn([
            "ps", "--filter", "status=running",
            "--filter", "name=^/ods-(llama|vllm)-server$", "--format", "{{.ID}}",
        ])
    except AdmissionError:
        raise
    ids = [line.strip() for line in listing.splitlines() if line.strip()]
    if not ids:
        return None
    target = os.path.realpath(install_dir)
    matches: list[dict] = []
    for cid in ids:
        try:
            raw = run_docker_fn(["inspect", "--type", "container", cid])
        except AdmissionError:
            continue
        try:
            inspected = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(inspected, list):
            continue
        for entry in inspected:
            if not isinstance(entry, dict):
                continue
            container_id = entry.get("Id")
            if not isinstance(container_id, str) or not CONTAINER_ID.fullmatch(container_id):
                continue
            state = entry.get("State") or {}
            if not isinstance(state, dict) or state.get("Running") is not True:
                continue
            pid = state.get("Pid")
            if type(pid) is not int or pid <= 0:
                continue
            config = entry.get("Config") or {}
            labels = config.get("Labels") if isinstance(config, dict) else None
            if not isinstance(labels, dict):
                continue
            service = labels.get("com.docker.compose.service")
            if service not in ENGINE_SERVICES:
                continue
            working_dir = labels.get("com.docker.compose.project.working_dir")
            if not isinstance(working_dir, str) or not working_dir:
                continue
            if os.path.realpath(working_dir) != target:
                continue
            started_at = state.get("StartedAt")
            if not isinstance(started_at, str) or not started_at:
                continue
            matches.append({
                "id": container_id, "service": service,
                "started_at": started_at, "pid": pid,
            })
    if len(matches) > 1:
        raise AdmissionError(
            "more than one running engine container belongs to this installation"
        )
    return matches[0] if matches else None


def verify_engine(install_dir: str, expected: dict, inspect_fn=None) -> bool:
    if inspect_fn is None:
        inspect_fn = inspect_engine
    try:
        current = inspect_fn(install_dir)
    except AdmissionError:
        return False
    return current == expected


def credit_for_gpu(
    gpu_uuid: str,
    apps: Iterable[tuple[str, int, int]],
    engine: dict | None,
    read_cgroup_fn=None,
) -> int:
    """Memory (MiB) this installation's own engine holds on ``gpu_uuid``.

    Duplicate (gpu,pid) rows with conflicting sizes grant no credit to that
    pid; identical duplicates count once. Invalid/negative usage never
    credited. Unreadable/ambiguous cgroup grants no credit for that process.
    """
    if engine is None or engine.get("service") not in ENGINE_SERVICES:
        return 0
    if read_cgroup_fn is None:
        read_cgroup_fn = read_cgroup
    by_pid: dict[int, set[int]] = {}
    for uuid, pid, used in apps:
        if uuid != gpu_uuid:
            continue
        if pid <= 0 or used < 0:
            continue
        by_pid.setdefault(pid, set()).add(used)
    total = 0
    for pid, sizes in by_pid.items():
        if len(sizes) != 1:
            continue
        used = next(iter(sizes))
        try:
            cgroup = read_cgroup_fn(pid)
        except AdmissionError:
            continue
        if container_id_from_cgroup(cgroup) != engine["id"]:
            continue
        total += used
    return total


def adjust_rows(
    rows: list[str],
    install_dir: str,
    *,
    inspect_fn=None,
    verify_fn=None,
    apps_fn=None,
    read_cgroup_fn=None,
) -> list[str]:
    """Return ``rows`` with each row's free memory credited by the own engine."""
    if inspect_fn is None:
        inspect_fn = inspect_engine
    if verify_fn is None:
        verify_fn = verify_engine
    if apps_fn is None:
        apps_fn = run_nvidia_smi
    if read_cgroup_fn is None:
        read_cgroup_fn = read_cgroup

    parsed = [parse_gpu_row(line) for line in rows]
    try:
        engine = inspect_fn(install_dir)
    except AdmissionError:
        engine = None
    apps: list[tuple[str, int, int]] = []
    if engine is not None:
        try:
            apps = parse_compute_apps(apps_fn())
        except AdmissionError:
            engine = None
            apps = []

    credits: dict[str, int] = {}
    for uuid, _cap, _total, _free in parsed:
        credits[uuid] = credit_for_gpu(uuid, apps, engine, read_cgroup_fn=read_cgroup_fn)

    if engine is not None:
        if not verify_fn(install_dir, engine):
            engine = None
            credits = {uuid: 0 for uuid in credits}

    adjusted: list[str] = []
    for uuid, cap, total_mib, free_mib in parsed:
        credit = credits.get(uuid, 0)
        if not isinstance(credit, int) or credit < 0:
            raise AdmissionError(f"invalid credit {credit!r} for GPU {uuid}")
        new_free = min(total_mib, free_mib + credit)
        adjusted.append(f"{uuid}, {cap}, {total_mib}, {new_free}")
    return adjusted


def _load_model_vllm():
    """Import the dashboard's model_vllm by path (no package install needed)."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "extensions", "services", "dashboard-api", "model_vllm.py")
    spec = importlib.util.spec_from_file_location("ods_model_vllm", path)
    if spec is None or spec.loader is None:
        raise AdmissionError(f"cannot load model_vllm from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_contract(
    rows: list[str],
    install_dir: str,
    *,
    model_id: str,
    gpu_uuid: str,
    memory_share: str | None,
    catalog_path: str,
    inspect_fn=None,
    verify_fn=None,
    apps_fn=None,
    read_cgroup_fn=None,
    model_vllm=None,
) -> None:
    """Refuse unless the preserved vLLM contract fits the actual free memory.

    Reuses the same own-engine credit as adjust_rows, then asks model_vllm
    whether the named catalog entry fits the named GPU at the configured
    share. Any missing/malformed/ambiguous input raises AdmissionError.
    """
    if model_vllm is None:
        model_vllm = _load_model_vllm()
    if not model_id or not gpu_uuid:
        raise AdmissionError("preserved vLLM contract names no model or GPU")
    try:
        with open(catalog_path, encoding="utf-8") as handle:
            catalog = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise AdmissionError(f"cannot read model catalog {catalog_path}: {exc}") from exc
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        raise AdmissionError(f"model catalog {catalog_path} has no models list")
    matches = [
        item for item in models
        if isinstance(item, dict) and model_vllm.is_vllm_entry(item)
        and model_id in (item.get("llm_model_name"), item.get("id"))
    ]
    if len(matches) != 1:
        raise AdmissionError(
            f"preserved model {model_id!r} names {len(matches)} vLLM catalog entries (need exactly one)"
        )
    model = matches[0]
    problems = model_vllm.entry_problems(model)
    if problems:
        raise AdmissionError("preserved vLLM catalog entry is invalid: " + "; ".join(problems))
    memory_share = memory_share or str(model_vllm.DEFAULT_MEMORY_SHARE)
    if memory_share:
        try:
            share = float(memory_share)
        except ValueError as exc:
            raise AdmissionError(f"configured memory share {memory_share!r} is not a number") from exc
        if not (model_vllm.MIN_MEMORY_SHARE <= share <= model_vllm.MAX_MEMORY_SHARE):
            raise AdmissionError(
                f"configured memory share {share:g} is outside "
                f"{model_vllm.MIN_MEMORY_SHARE}..{model_vllm.MAX_MEMORY_SHARE}"
            )
        model = {**model, "gpu_memory_utilization": share}

    adjusted = adjust_rows(
        rows, install_dir,
        inspect_fn=inspect_fn, verify_fn=verify_fn,
        apps_fn=apps_fn, read_cgroup_fn=read_cgroup_fn,
    )
    gpus = []
    for line in adjusted:
        try:
            gpu = model_vllm.parse_gpu(line)
        except ValueError as exc:
            raise AdmissionError(f"adjusted GPU row is unusable: {exc}") from exc
        gpus.append(gpu)
    target = [gpu for gpu in gpus if gpu.uuid == gpu_uuid]
    if len(target) != 1:
        raise AdmissionError(
            f"preserved GPU {gpu_uuid!r} appears {len(target)} times in nvidia-smi output (need exactly one)"
        )
    gpu = target[0]
    if gpu.free_mib is None:
        raise AdmissionError(f"GPU {gpu_uuid} reports no numeric free memory")
    if not model_vllm.gpu_hardware_fits(model, gpu):
        raise AdmissionError(
            f"preserved model {model_id} does not fit GPU {gpu_uuid} hardware "
            f"({model_vllm.requirement_text(model)})"
        )
    needed = model_vllm.free_needed_mib(model, gpu)
    if gpu.free_mib < needed:
        raise AdmissionError(
            f"preserved model {model_id} needs {needed} MiB free on GPU {gpu_uuid} "
            f"({model_vllm.memory_share(model):.2f} of the GPU plus "
            f"{model_vllm.START_MARGIN_MIB} MiB for its CUDA context), "
            f"but only {gpu.free_mib} MiB is free"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--model-id", default="",
                        help="verify the preserved vLLM contract for this catalog model")
    parser.add_argument("--gpu-uuid", default="",
                        help="with --model-id: the GPU the preserved contract names")
    parser.add_argument("--memory-share", default=None,
                        help="with --model-id: the configured VLLM_GPU_MEMORY_UTILIZATION")
    parser.add_argument("--catalog", default="",
                        help="with --model-id: config/model-library.json")
    args = parser.parse_args(argv)
    rows = [line for line in sys.stdin.read().splitlines() if line.strip()]
    if not rows:
        print("vllm-gpu-admission: no GPU rows on stdin", file=sys.stderr)
        return 1
    try:
        if args.model_id or args.gpu_uuid or args.memory_share is not None or args.catalog:
            if not (args.model_id and args.gpu_uuid and args.catalog):
                raise AdmissionError(
                    "--model-id, --gpu-uuid and --catalog must be given together"
                )
            verify_contract(
                rows, args.install_dir,
                model_id=args.model_id,
                gpu_uuid=args.gpu_uuid,
                memory_share=args.memory_share,
                catalog_path=args.catalog,
            )
            return 0
        adjusted = adjust_rows(rows, args.install_dir)
    except AdmissionError as exc:
        print(f"vllm-gpu-admission: {exc}", file=sys.stderr)
        return 1
    for line in adjusted:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
