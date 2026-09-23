#!/usr/bin/env python3
"""Content-free, read-only host guidance for Pixel local control."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import sys
from typing import Any, Callable


DOCTOR_BOUNDARY = (
    "Rounded local readiness only; no hostname, serial number, device name, model identifier, path, "
    "process output, credential, prompt, network probe, or provider call is projected."
)
SUPPORTED_HOSTS = {("ubuntu", "24.04"), ("debian", "12")}
MAX_FACT_VALUE = 1 << 70


def iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _fixed_read(path: Path, maximum: int) -> str | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None
        payload = os.read(descriptor, maximum + 1)
        if len(payload) > maximum:
            return None
        current = path.lstat()
        if current.st_dev != info.st_dev or current.st_ino != info.st_ino or not stat.S_ISREG(current.st_mode):
            return None
        return payload.decode("utf-8", errors="strict")
    except (OSError, UnicodeError):
        return None
    finally:
        os.close(descriptor)


def _fixed_child_read(directory: Path, name: str, maximum: int) -> tuple[str | None, str]:
    """Read one fixed child without following either the directory or file symlink."""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None, "unavailable"
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    if os.open in os.supports_dir_fd and getattr(os, "O_DIRECTORY", 0) and getattr(os, "O_NOFOLLOW", 0):
        try:
            directory_descriptor = os.open(directory, directory_flags)
        except FileNotFoundError:
            return None, "absent"
        except OSError:
            return None, "unavailable"
        try:
            directory_info = os.fstat(directory_descriptor)
            if not stat.S_ISDIR(directory_info.st_mode):
                return None, "unavailable"
            try:
                descriptor = os.open(name, file_flags, dir_fd=directory_descriptor)
            except FileNotFoundError:
                return None, "absent"
            except OSError:
                return None, "unavailable"
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    return None, "unavailable"
                payload = os.read(descriptor, maximum + 1)
                if len(payload) > maximum:
                    return None, "unavailable"
                current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
                if current.st_dev != info.st_dev or current.st_ino != info.st_ino or not stat.S_ISREG(current.st_mode):
                    return None, "unavailable"
                return payload.decode("utf-8", errors="strict"), "read"
            except (OSError, UnicodeError):
                return None, "unavailable"
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_descriptor)

    try:
        before = directory.lstat()
    except FileNotFoundError:
        return None, "absent"
    except OSError:
        return None, "unavailable"
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        return None, "unavailable"
    child = directory / name
    try:
        child.lstat()
    except FileNotFoundError:
        return None, "absent"
    except OSError:
        return None, "unavailable"
    payload = _fixed_read(child, maximum)
    try:
        after = directory.lstat()
    except OSError:
        return None, "unavailable"
    if after.st_dev != before.st_dev or after.st_ino != before.st_ino or not stat.S_ISDIR(after.st_mode):
        return None, "unavailable"
    return (payload, "read") if payload is not None else (None, "unavailable")


def _os_release() -> tuple[str | None, str | None]:
    payload = _fixed_read(Path("/etc/os-release"), 32 * 1024)
    if payload is None:
        payload = _fixed_read(Path("/usr/lib/os-release"), 32 * 1024)
    values: dict[str, str] = {}
    for line in (payload or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key not in {"ID", "VERSION_ID"}:
            continue
        value = value.strip().strip('"\'')
        if value and len(value) <= 32 and all(character.isalnum() or character in ".-_" for character in value):
            values[key] = value.lower()
    return values.get("ID"), values.get("VERSION_ID")


def _memory_bytes() -> int | None:
    payload = _fixed_read(Path("/proc/meminfo"), 64 * 1024)
    for line in (payload or "").splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "MemTotal:" and fields[1].isdigit() and fields[2] == "kB":
            value = int(fields[1]) * 1024
            return value if 0 < value <= MAX_FACT_VALUE else None
    return None


def _accelerators() -> list[str]:
    found: set[str] = set()
    vendor_names = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
    try:
        candidates = sorted(Path("/sys/class/drm").glob("card*/device/vendor"))[:32]
    except OSError:
        candidates = []
    for path in candidates:
        try:
            value = path.read_text(encoding="ascii")[:16].strip().lower()
        except (OSError, UnicodeError):
            continue
        if value in vendor_names:
            found.add(vendor_names[value])
    if Path("/dev/nvidia0").exists():
        found.add("nvidia")
    if Path("/dev/kfd").exists():
        found.add("amd")
    return sorted(found)


def collect_facts(root: Path) -> dict[str, Any]:
    system = platform.system().lower()
    os_id, os_version = _os_release() if system == "linux" else (None, None)
    try:
        storage_free = shutil.disk_usage(root).free
    except OSError:
        storage_free = None
    socket_detected = False
    try:
        socket_info = Path("/var/run/docker.sock").lstat()
        socket_detected = stat.S_ISSOCK(socket_info.st_mode)
    except OSError:
        pass
    return {
        "system": system,
        "osId": os_id,
        "osVersion": os_version,
        "cpuCount": os.cpu_count(),
        "memoryBytes": _memory_bytes(),
        "storageFreeBytes": storage_free,
        "accelerators": _accelerators(),
        "containerSocketDetected": socket_detected,
        "pythonMajor": sys.version_info.major,
        "pythonMinor": sys.version_info.minor,
    }


def collect_model_configuration(root: Path) -> dict[str, Any]:
    payload, read_state = _fixed_child_read(root / ".generated", "deployment.json", 2 * 1024 * 1024)
    if payload is None:
        return {
            "configured": False, "unavailable": read_state != "absent",
            "contextWindow": None, "reasoning": None,
        }
    try:
        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, child in pairs:
                if key in value:
                    raise ValueError("duplicate model configuration field")
                value[key] = child
            return value

        value = json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite model configuration")),
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"configured": False, "unavailable": True, "contextWindow": None, "reasoning": None}
    if not isinstance(value, dict):
        return {"configured": False, "unavailable": True, "contextWindow": None, "reasoning": None}
    text_fields = (("modelProvider", 64), ("modelId", 256), ("modelBaseUrl", 2048))
    text_valid = all(
        isinstance(value.get(key), str) and 0 < len(value[key].strip()) <= maximum
        for key, maximum in text_fields
    )
    context = value.get("modelContextWindow")
    reasoning = value.get("modelReasoning")
    configured = (
        text_valid
        and type(context) is int and 4_096 <= context <= 10_000_000
        and type(reasoning) is bool
    )
    if not configured:
        return {"configured": False, "unavailable": True, "contextWindow": None, "reasoning": None}
    return {
        "configured": True,
        "unavailable": False,
        "contextWindow": context,
        "reasoning": reasoning,
    }


def _bounded_int(value: Any, *, minimum: int = 0) -> int | None:
    return value if type(value) is int and minimum <= value <= MAX_FACT_VALUE else None


def _capacity_bucket(value: int | None, thresholds: tuple[int, ...], labels: tuple[str, ...]) -> str:
    if value is None:
        return "unavailable"
    for threshold, label in zip(thresholds, labels):
        if value < threshold:
            return label
    return labels[-1]


def _family(system: Any, os_id: Any) -> str:
    if system == "linux" and os_id in {"ubuntu", "debian"}:
        return os_id
    return {
        "linux": "linux", "windows": "windows", "darwin": "macos",
    }.get(system, "other")


def build_report(
    facts: dict[str, Any],
    deployment_profile: str = "prepared",
    *,
    model_configuration: dict[str, Any] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    system = facts.get("system") if facts.get("system") in {"linux", "windows", "darwin", "other"} else "other"
    os_id = facts.get("osId") if isinstance(facts.get("osId"), str) else None
    os_version = facts.get("osVersion") if isinstance(facts.get("osVersion"), str) else None
    host_contract = "supported" if (os_id, os_version) in SUPPORTED_HOSTS and system == "linux" else (
        "unavailable" if system == "linux" and (os_id is None or os_version is None) else "unsupported"
    )
    cpu_count = _bounded_int(facts.get("cpuCount"), minimum=1)
    memory_bytes = _bounded_int(facts.get("memoryBytes"), minimum=1)
    storage_bytes = _bounded_int(facts.get("storageFreeBytes"), minimum=0)
    gib = 1024 ** 3
    cpu_capacity = _capacity_bucket(cpu_count, (4, 8, 16, MAX_FACT_VALUE), ("1-3", "4-7", "8-15", "16-plus"))
    memory_capacity = _capacity_bucket(
        memory_bytes, (8 * gib, 16 * gib, 32 * gib, 64 * gib, MAX_FACT_VALUE),
        ("under-8", "8-15", "16-31", "32-63", "64-plus"),
    )
    storage_capacity = _capacity_bucket(
        storage_bytes, (10 * gib, 25 * gib, 50 * gib, 100 * gib, MAX_FACT_VALUE),
        ("under-10", "10-24", "25-49", "50-99", "100-plus"),
    )
    accelerators = facts.get("accelerators")
    if not isinstance(accelerators, list) or any(item not in {"nvidia", "amd", "intel"} for item in accelerators):
        accelerator = "unavailable"
    else:
        unique = sorted(set(accelerators))
        accelerator = unique[0] if len(unique) == 1 else "multiple" if unique else "not-detected"
    python_major = _bounded_int(facts.get("pythonMajor"), minimum=0)
    python_minor = _bounded_int(facts.get("pythonMinor"), minimum=0)
    python_ready = python_major is not None and python_minor is not None and (python_major, python_minor) >= (3, 11)
    container_detected = facts.get("containerSocketDetected") if type(facts.get("containerSocketDetected")) is bool else None
    profile = deployment_profile if deployment_profile in {"prepared", "reference"} else "prepared"
    configuration = model_configuration if isinstance(model_configuration, dict) else {}
    model_unavailable = configuration.get("unavailable") is True
    model_claimed = configuration.get("configured") is True
    configured_context = _bounded_int(configuration.get("contextWindow"), minimum=4_096) if model_claimed else None
    reasoning_configured = configuration.get("reasoning") if model_claimed and type(configuration.get("reasoning")) is bool else None
    model_configured = (
        not model_unavailable and model_claimed
        and configured_context is not None and configured_context <= 10_000_000
        and reasoning_configured is not None
    )
    model_unavailable = model_unavailable or (model_claimed and not model_configured)
    if not model_configured:
        configured_context = None
        reasoning_configured = None
    if not model_configured:
        context_capacity = "not-configured"
    elif configured_context is None:
        context_capacity = "unavailable"
    elif configured_context <= 16_384:
        context_capacity = "4k-16k"
    elif configured_context <= 65_536:
        context_capacity = "16k-64k"
    elif configured_context <= 262_144:
        context_capacity = "64k-256k"
    else:
        context_capacity = "256k-plus"

    checks = [
        {
            "id": "supported-host",
            "state": "pass" if host_contract == "supported" else "unavailable" if host_contract == "unavailable" else "review",
            "guidanceCode": "host-supported" if host_contract == "supported" else "confirm-host-release" if host_contract == "unavailable" else "use-supported-host",
        },
        {
            "id": "python-runtime",
            "state": "pass" if python_ready else "unavailable" if python_major is None or python_minor is None else "review",
            "guidanceCode": "python-ready" if python_ready else "confirm-python-runtime" if python_major is None or python_minor is None else "install-python-311",
        },
        {
            "id": "memory-headroom",
            "state": "unavailable" if memory_bytes is None else "pass" if memory_bytes >= 8 * gib else "review",
            "guidanceCode": "confirm-memory" if memory_bytes is None else "memory-ready" if memory_bytes >= 8 * gib else "reduce-local-model-load",
        },
        {
            "id": "storage-headroom",
            "state": "unavailable" if storage_bytes is None else "pass" if storage_bytes >= 25 * gib else "review",
            "guidanceCode": "confirm-storage" if storage_bytes is None else "storage-ready" if storage_bytes >= 25 * gib else "free-local-storage",
        },
        {
            "id": "reference-container-runtime",
            "state": "not-required" if profile != "reference" else "unavailable" if container_detected is None else "pass" if container_detected else "review",
            "guidanceCode": "prepared-profile-selected" if profile != "reference" else "confirm-container-runtime" if container_detected is None else "container-detected" if container_detected else "start-container-runtime",
        },
        {
            "id": "generated-model-configuration",
            "state": "unavailable" if model_unavailable else "pass" if model_configured else "not-required",
            "guidanceCode": "confirm-generated-model" if model_unavailable else "model-configuration-ready" if model_configured else "model-not-configured",
        },
    ]
    unavailable_checks = sum(item["state"] == "unavailable" for item in checks)
    review_checks = sum(item["state"] == "review" for item in checks)
    state = "unavailable" if unavailable_checks else "attention" if review_checks else "ready"

    if memory_bytes is None:
        model_class, context_guidance = "unavailable", "measure-first"
    elif memory_bytes < 8 * gib:
        model_class, context_guidance = "remote-or-compact", "start-small"
    elif memory_bytes < 16 * gib:
        model_class, context_guidance = "compact-local", "start-small"
    elif memory_bytes < 32 * gib:
        model_class, context_guidance = "balanced-local", "moderate"
    elif memory_bytes < 64 * gib:
        model_class, context_guidance = "larger-local", "expanded-after-measurement"
    else:
        model_class, context_guidance = "large-memory-local", "expanded-after-measurement"

    return {
        "schemaVersion": 1,
        "generatedAt": iso((now or (lambda: datetime.now(timezone.utc)))()),
        "summary": {
            "state": state,
            "attentionChecks": review_checks,
            "unavailableChecks": unavailable_checks,
            "supportedHost": True if host_contract == "supported" else None if host_contract == "unavailable" else False,
        },
        "host": {
            "contract": host_contract,
            "family": _family(system, os_id),
            "cpuCapacity": cpu_capacity,
            "memoryCapacityGiB": memory_capacity,
            "storageFreeCapacityGiB": storage_capacity,
            "accelerator": accelerator,
            "containerRuntime": "unavailable" if container_detected is None else "socket-detected" if container_detected else "not-detected",
        },
        "recommendation": {
            "localModelClass": model_class,
            "contextGuidance": context_guidance,
            "acceleratorGuidance": "verify-memory-before-use" if accelerator not in {"not-detected", "unavailable"} else "cpu-or-remote-first",
            "fitIsGuaranteed": False,
        },
        "model": {
            "configured": model_configured,
            "discoveryState": "unavailable" if model_unavailable else "configured" if model_configured else "not-configured",
            "contextCapacity": "unavailable" if model_unavailable else context_capacity,
            "reasoningConfigured": reasoning_configured,
            "fitAssessment": "unavailable" if model_unavailable else "manual-validation-required" if model_configured else "not-configured",
        },
        "checks": checks,
        "privacy": {
            "exactHardwareProjected": False,
            "hostIdentityProjected": False,
            "deviceIdentityProjected": False,
            "modelIdentityProjected": False,
            "localPathsProjected": False,
            "processOutputProjected": False,
            "networkProbesPerformed": False,
            "providerCallsPerformed": False,
        },
        "boundary": DOCTOR_BOUNDARY,
    }


def doctor_report(
    root: Path,
    deployment_profile: str = "prepared",
    *,
    facts: dict[str, Any] | None = None,
    model_configuration: dict[str, Any] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    return build_report(
        facts if facts is not None else collect_facts(root),
        deployment_profile,
        model_configuration=(
            model_configuration if model_configuration is not None
            else collect_model_configuration(root) if facts is None
            else {}
        ),
        now=now,
    )


def format_human(report: dict[str, Any]) -> str:
    state_labels = {"ready": "Ready", "attention": "Needs attention", "unavailable": "Incomplete"}
    check_labels = {
        "supported-host": "Supported host", "python-runtime": "Python runtime",
        "memory-headroom": "Memory headroom", "storage-headroom": "Storage headroom",
        "reference-container-runtime": "Reference container runtime",
        "generated-model-configuration": "Generated model configuration",
    }
    check_states = {"pass": "ready", "review": "review", "unavailable": "could not verify", "not-required": "not required"}
    lines = [f"Pixel Doctor: {state_labels[report['summary']['state']]}"]
    for item in report["checks"]:
        state = check_states[item["state"]]
        if item["id"] == "reference-container-runtime" and item["state"] == "not-required":
            state = "not required for the prepared profile"
        elif item["id"] == "generated-model-configuration" and item["state"] == "not-required":
            state = "not configured"
        lines.append(f"- {check_labels[item['id']]}: {state}")
    lines.extend([
        f"- Configured context tier: {report['model']['contextCapacity'].replace('-', ' ')}",
        f"- Local model starting point: {report['recommendation']['localModelClass'].replace('-', ' ')}",
        "- Model fit is advisory; confirm the exact model, quantization, context, and accelerator memory locally.",
        "No changes were made. No network probe or provider call was performed.",
    ])
    return "\n".join(lines)
