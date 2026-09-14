#!/usr/bin/env python3
"""Capture a bounded, read-only ODS installation baseline receipt."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit


MAX_COMMAND_OUTPUT = 16 * 1024 * 1024
MAX_FLAGS_BYTES = 64 * 1024
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,63}")
HEX_REVISION_RE = re.compile(r"[0-9a-f]{40,64}")
MEMORY_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b)$", re.I)
MEMORY_FACTORS = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
}


class MeasurementError(RuntimeError):
    """A bounded measurement could not be completed safely."""


def _label(value: str, field: str) -> str:
    if not LABEL_RE.fullmatch(value):
        raise MeasurementError(f"{field} must be a short printable label")
    return value


def _tool(env_name: str, default: str) -> str:
    value = os.environ.get(env_name, default)
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise MeasurementError(f"{env_name} is invalid")
    return value


def _run(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MeasurementError(f"read-only command unavailable: {Path(argv[0]).name}") from exc
    if len(result.stdout) > MAX_COMMAND_OUTPUT or len(result.stderr) > MAX_COMMAND_OUTPUT:
        raise MeasurementError("read-only command output exceeded its limit")
    return result


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _compose_file(root: Path, value: str) -> tuple[Path, str]:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file() or not _within(root, resolved):
        raise MeasurementError("compose files must be regular files inside --root")
    return resolved, resolved.relative_to(root).as_posix()


def _compose_files(root: Path, explicit: list[str]) -> list[tuple[Path, str]]:
    values = list(explicit)
    if not values:
        flags_path = root / ".compose-flags"
        if flags_path.is_file():
            raw = flags_path.read_bytes()
            if len(raw) > MAX_FLAGS_BYTES:
                raise MeasurementError(".compose-flags exceeds its size limit")
            try:
                tokens = shlex.split(raw.decode("utf-8", errors="strict"), posix=True)
            except (UnicodeDecodeError, ValueError) as exc:
                raise MeasurementError(".compose-flags is invalid") from exc
            index = 0
            while index < len(tokens):
                token = tokens[index]
                if token in {"-f", "--file"}:
                    index += 1
                    if index >= len(tokens):
                        raise MeasurementError(".compose-flags ends before a file value")
                    values.append(tokens[index])
                elif token.startswith("--file=") and token[7:]:
                    values.append(token[7:])
                else:
                    raise MeasurementError(".compose-flags contains an unsupported argument")
                index += 1
        else:
            for fallback in ("docker-compose.yml", "docker-compose.base.yml"):
                if (root / fallback).is_file():
                    values.append(fallback)
                    break
    if not values:
        raise MeasurementError("no Compose files were supplied or discovered")

    files: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for value in values:
        resolved, relative = _compose_file(root, value)
        if resolved not in seen:
            files.append((resolved, relative))
            seen.add(resolved)
    return files


def _compose_prefix(docker: str, files: list[tuple[Path, str]]) -> list[str]:
    argv = [docker, "compose"]
    for path, _ in files:
        argv.extend(("-f", str(path)))
    return argv


def _resolved_graph(prefix: list[str]) -> tuple[list[str], list[str]]:
    result = _run([*prefix, "config", "--format", "json"])
    if result.returncode != 0:
        raise MeasurementError("docker compose config could not render the resolved graph")
    try:
        value = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeasurementError("docker compose config returned invalid JSON") from exc
    services_value = value.get("services") if isinstance(value, dict) else None
    if not isinstance(services_value, dict):
        raise MeasurementError("docker compose config omitted the services object")

    services = sorted({name for name in services_value if isinstance(name, str) and name})
    images = sorted(
        {
            image.strip()
            for service in services_value.values()
            if isinstance(service, dict)
            for image in [service.get("image")]
            if isinstance(image, str) and image.strip()
        }
    )
    if len(services) > 256 or len(images) > 256:
        raise MeasurementError("resolved Compose graph exceeds its bounded size")
    return services, images


def _git_head(git: str, root: Path) -> str | None:
    try:
        result = _run([git, "-C", str(root), "rev-parse", "HEAD"], timeout=5)
    except MeasurementError:
        return None
    value = result.stdout.decode("ascii", errors="ignore").strip()
    return value if result.returncode == 0 and HEX_REVISION_RE.fullmatch(value) else None


def _install_bytes(du: str, root: Path) -> int | None:
    try:
        result = _run([du, "-sb", "--", str(root)], timeout=60)
    except MeasurementError:
        return None
    token = result.stdout.split(maxsplit=1)[0] if result.stdout else b""
    return int(token) if result.returncode == 0 and token.isdigit() else None


def _container_ids(prefix: list[str]) -> list[str] | None:
    try:
        result = _run([*prefix, "ps", "-q"])
    except MeasurementError:
        return None
    if result.returncode != 0:
        return None
    values = {
        line.strip()
        for line in result.stdout.decode("ascii", errors="ignore").splitlines()
        if re.fullmatch(r"[0-9a-f]{12,64}", line.strip())
    }
    return sorted(values)


def _image_bytes(docker: str, images: list[str]) -> tuple[int, int, int]:
    total = 0
    present = 0
    for image in images:
        try:
            result = _run([docker, "image", "inspect", "--format", "{{.Size}}", image])
        except MeasurementError:
            continue
        value = result.stdout.decode("ascii", errors="ignore").strip()
        if result.returncode == 0 and value.isdigit():
            total += int(value)
            present += 1
    return total, present, len(images) - present


def _memory_bytes(value: str) -> int:
    match = MEMORY_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("invalid memory value")
    return int(Decimal(match.group(1)) * MEMORY_FACTORS[match.group(2).lower()])


def _idle_sample(docker: str, container_ids: list[str] | None) -> dict[str, object]:
    if container_ids is None:
        return {"available": False, "containerCount": None, "cpuPercent": None, "memoryBytes": None}
    if not container_ids:
        return {"available": True, "containerCount": 0, "cpuPercent": 0.0, "memoryBytes": 0}
    try:
        result = _run(
            [docker, "stats", "--no-stream", "--format", "{{json .}}", *container_ids],
            timeout=60,
        )
    except MeasurementError:
        return {"available": False, "containerCount": len(container_ids), "cpuPercent": None, "memoryBytes": None}
    if result.returncode != 0:
        return {"available": False, "containerCount": len(container_ids), "cpuPercent": None, "memoryBytes": None}

    cpu = Decimal(0)
    memory = 0
    try:
        for line in result.stdout.decode("utf-8", errors="strict").splitlines():
            row = json.loads(line)
            cpu += Decimal(str(row["CPUPerc"]).strip().removesuffix("%"))
            used = str(row["MemUsage"]).split("/", 1)[0].strip()
            memory += _memory_bytes(used)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, InvalidOperation, ValueError):
        return {"available": False, "containerCount": len(container_ids), "cpuPercent": None, "memoryBytes": None}
    return {
        "available": True,
        "containerCount": len(container_ids),
        "cpuPercent": float(cpu),
        "memoryBytes": memory,
    }


def _readiness(curl: str, raw_url: str | None) -> dict[str, object]:
    if raw_url is None:
        return {"attempted": False, "ready": None, "statusCode": None}
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise MeasurementError("assistant readiness URL must be credential-free loopback HTTP")
    try:
        result = _run(
            [curl, "--silent", "--show-error", "--max-time", "5", "--output", os.devnull, "--write-out", "%{http_code}", raw_url],
            timeout=10,
        )
    except MeasurementError:
        return {"attempted": True, "ready": False, "statusCode": None}
    text = result.stdout.decode("ascii", errors="ignore").strip()
    status = int(text) if text.isdigit() and len(text) == 3 else None
    return {"attempted": True, "ready": result.returncode == 0 and status is not None and 200 <= status < 300, "statusCode": status}


def _nonnegative_decimal(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise MeasurementError("startup-to-ready must be a non-negative number") from exc
    if not value.is_finite() or value < 0:
        raise MeasurementError("startup-to-ready must be a non-negative number")
    return float(value)


def _write_receipt(path: Path, value: dict[str, object]) -> None:
    parent = path.parent.resolve(strict=True)
    if path.exists() and path.is_symlink():
        raise MeasurementError("output path must not be a symlink")
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > 256 * 1024:
        raise MeasurementError("measurement receipt exceeds its size limit")
    handle = tempfile.NamedTemporaryFile(prefix=".ods-baseline-", dir=parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--inference-route", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--compose-file", action="append", default=[])
    parser.add_argument("--startup-to-ready-seconds")
    parser.add_argument("--assistant-ready-url")
    args = parser.parse_args(argv)

    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise MeasurementError("--root must be a directory")
        profile = _label(args.profile, "profile")
        route = _label(args.inference_route, "inference route")
        files = _compose_files(root, args.compose_file)
        docker = _tool("ODS_MEASURE_DOCKER", "docker")
        git = _tool("ODS_MEASURE_GIT", "git")
        du = _tool("ODS_MEASURE_DU", "du")
        curl = _tool("ODS_MEASURE_CURL", "curl")
        prefix = _compose_prefix(docker, files)
        services, images = _resolved_graph(prefix)
        ids = _container_ids(prefix)
        image_bytes, image_present, image_missing = _image_bytes(docker, images)
        receipt: dict[str, object] = {
            "schemaVersion": 1,
            "kind": "ods-assistant-first-baseline",
            "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": {"gitHead": _git_head(git, root)},
            "selection": {
                "profile": profile,
                "inferenceRoute": route,
                "composeFiles": [relative for _, relative in files],
            },
            "resolved": {"services": services, "images": images},
            "footprint": {
                "installBytes": _install_bytes(du, root),
                "localImageBytes": image_bytes,
                "localImageCount": image_present,
                "missingImageCount": image_missing,
                "runningContainerCount": None if ids is None else len(ids),
            },
            "idleSample": _idle_sample(docker, ids),
            "startupToReadySeconds": _nonnegative_decimal(args.startup_to_ready_seconds),
            "assistantReadiness": _readiness(curl, args.assistant_ready_url),
        }
        _write_receipt(args.output.resolve(), receipt)
    except (MeasurementError, OSError) as exc:
        print(f"capture-assistant-first-baseline: {exc}", file=sys.stderr)
        return 2
    print(str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
