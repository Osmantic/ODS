"""Owned Compose engine control; readiness and transaction completion are separate.

The fixed engine names and Compose-declared engine instances are inspected.
Mutations require its process lease, and the runner must pass that lease to bounded child
processes. Runtime proof, GPU admission, config writes and rollback policy remain
with the host transaction. Nothing here operates on an external model service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

import model_lifecycle_lock

SERVICES = ("llama-server", "vllm-server")


def engine_record(entry, install_dir, service):
    if service not in SERVICES or not isinstance(entry, dict):
        raise RuntimeError("Invalid engine inspection")
    if entry.get("Name") != "/ods-" + service:
        raise RuntimeError("Engine container name changed")
    cid = entry.get("Id")
    if not isinstance(cid, str) or not re.fullmatch(r"[0-9a-f]{64}", cid):
        raise RuntimeError("Invalid engine container identity")
    config = entry.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict) or labels.get("com.docker.compose.service") != service:
        raise RuntimeError("Engine Compose service ownership is unproven")
    directory = labels.get("com.docker.compose.project.working_dir")
    if (not isinstance(directory, str) or not os.path.isabs(directory)
            or os.path.realpath(directory) != os.path.realpath(install_dir)):
        raise RuntimeError("Engine belongs to another installation")
    project = labels.get("com.docker.compose.project")
    if not isinstance(project, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise RuntimeError("Invalid engine Compose project")
    state = entry.get("State")
    if not isinstance(state, dict) or any(type(state.get(k)) is not bool for k in ("Running", "Paused", "Restarting", "Dead")):
        raise RuntimeError("Invalid engine state")
    if state["Paused"] or state["Restarting"] or state["Dead"]:
        raise RuntimeError("Engine is paused or unstable; preserve it for explicit recovery")
    if state.get("Status") not in ("running", "exited", "created") or state["Running"] != (state["Status"] == "running"):
        raise RuntimeError("Inconsistent engine state")
    pid = state.get("Pid")
    if type(pid) is not int or pid < 0 or (pid > 0) != state["Running"]:
        raise RuntimeError("Invalid engine process identity")
    started = state.get("StartedAt")
    if not isinstance(started, str) or not started:
        raise RuntimeError("Invalid engine start identity")
    image_id, image_ref = entry.get("Image"), config.get("Image")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise RuntimeError("Invalid engine image identity")
    if not isinstance(image_ref, str) or not image_ref:
        raise RuntimeError("Invalid engine image reference")
    restarts = entry.get("RestartCount")
    if type(restarts) is not int or restarts < 0:
        raise RuntimeError("Invalid engine restart count")
    return {"id": cid, "service": service, "project": project, "running": state["Running"],
            "pid": pid, "started_at": started, "image_id": image_id, "image_ref": image_ref,
            "restart_count": restarts}


def _json_result(result, operation):
    if result.returncode != 0:
        raise RuntimeError(f"{operation} failed")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError):
        raise RuntimeError(f"{operation} returned invalid JSON") from None


def capture(install_dir: Path, run, *, project: str | None = None) -> dict:
    """Read sanitized ownership and generation evidence; never mutate Docker."""
    records = {}
    for service in SERVICES:
        name = "ods-" + service
        result = run(["docker", "inspect", "--type", "container", name], 15)
        missing = re.search(r"(?im)^Error(?: response from daemon)?: No such (?:object|container): "
                            + re.escape(name) + r"\s*$", result.stderr or "")
        if result.returncode == 1 and missing:
            records[service] = None
            continue
        rows = _json_result(result, "Engine inspection")
        if not isinstance(rows, list) or len(rows) != 1:
            raise RuntimeError("Engine inspection returned ambiguous containers")
        records[service] = engine_record(rows[0], install_dir, service)
    existing = [row for row in records.values() if row is not None]
    projects = {row["project"] for row in existing}
    if len(projects) > 1:
        raise RuntimeError("Engine containers belong to different Compose projects")
    if sum(row["running"] for row in existing) > 1:
        raise RuntimeError("More than one owned engine is running; explicit recovery is required")
    known = {row["id"] for row in existing}
    if project is not None:
        projects.add(project)
    # A renamed source still belongs to its Compose service. Name-only lookup
    # could otherwise start a second engine beside that preserved workload.
    for service in SERVICES:
        result = run(["docker", "ps", "-a", "--no-trunc", "--filter",
                      "label=com.docker.compose.service=" + service, "--format", "{{.ID}}"], 15)
        if result.returncode != 0 or not isinstance(result.stdout, str):
            raise RuntimeError("Could not enumerate Compose engine ownership")
        ids = set(result.stdout.split())
        if any(not re.fullmatch(r"[0-9a-f]{64}", cid) for cid in ids):
            raise RuntimeError("Compose engine enumeration returned invalid identities")
        for cid in ids - known:
            rows = _json_result(run(["docker", "inspect", "--type", "container", cid], 15), "Engine alias inspection")
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("Id") != cid:
                raise RuntimeError("Compose engine alias identity changed")
            config = rows[0].get("Config")
            labels = config.get("Labels") if isinstance(config, dict) else None
            directory = labels.get("com.docker.compose.project.working_dir") if isinstance(labels, dict) else None
            if not isinstance(labels, dict) or labels.get("com.docker.compose.service") != service:
                raise RuntimeError("Compose engine alias service identity changed")
            if isinstance(directory, str) and os.path.isabs(directory) and os.path.realpath(directory) == os.path.realpath(install_dir):
                raise RuntimeError("A renamed or additional engine belongs to this installation; preserve it for explicit recovery")
            if labels.get("com.docker.compose.project") in projects:
                raise RuntimeError("Compose project also owns a foreign engine; preserve it for explicit recovery")
    return records


def stage(install_dir: Path, flags: list[str], service: str, *, lease_fd: int, run, expected: dict | None = None) -> dict:
    """Stage exactly one owned engine, returning evidence, not readiness.

    A failure may leave the source stopped or the target created. The durable
    transaction must retain its hold and handle restoration; never claim a
    failed command had no effect or automatically adopt a replacement source.
    """
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Managed Compose engine transitions require Linux")
    if service not in SERVICES or not isinstance(flags, list) or not flags or any(not isinstance(v, str) or not v for v in flags):
        raise RuntimeError("Invalid Compose engine selection")
    model_lifecycle_lock.validate_fd(install_dir, lease_fd)
    import fcntl
    fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    model_lifecycle_lock.validate_fd(install_dir, lease_fd)
    before = capture(install_dir, run)
    if expected is not None and before != expected:
        raise RuntimeError("Captured engine identity changed before staging")
    base = ["docker", "compose", *flags]
    config = _json_result(run(base + ["config", "--format", "json"], 30), "Compose engine plan")
    services = config.get("services") if isinstance(config, dict) else None
    selected = services.get(service) if isinstance(services, dict) else None
    project = config.get("name") if isinstance(config, dict) else None
    if (not isinstance(project, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project)
            or not isinstance(selected, dict) or selected.get("container_name") != "ods-" + service
            or not isinstance(selected.get("image"), str) or not selected["image"]):
        raise RuntimeError("Compose engine plan has an unexpected owner or container")
    if any(row is not None and row["project"] != project for row in before.values()):
        raise RuntimeError("Compose plan would change engine project ownership")
    if capture(install_dir, run, project=project) != before:
        raise RuntimeError("Engine identity changed while resolving the Compose plan")
    stopped = dict(before)
    for other, row in before.items():
        if other == service or row is None or not row["running"]:
            continue
        result = run(["docker", "stop", "--time", "30", row["id"]], 60)
        if result.returncode != 0:
            raise RuntimeError("Could not stop the captured source engine; target was not started")
        stopped[other] = {**row, "running": False, "pid": 0}
    if capture(install_dir, run, project=project) != stopped:
        raise RuntimeError("Engine state changed before target creation")
    result = run(base + ["up", "-d", "--force-recreate", "--no-deps", service], 300)
    if result.returncode != 0:
        raise RuntimeError("Compose engine creation failed; recovery is required")
    after = capture(install_dir, run, project=project)
    target = after[service]
    if (target is None or not target["running"] or target["project"] != project
            or target["image_ref"] != selected["image"]
            or (before[service] is not None and target["id"] == before[service]["id"])
            or any(after[other] != stopped[other] for other in SERVICES if other != service)):
        raise RuntimeError("Compose did not prove the selected engine generation; recovery is required")
    return {"previous": before, "current": after}
