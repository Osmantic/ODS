"""Read-only MTP qualification; capability is not a performance recommendation."""
from __future__ import annotations

import math
import hashlib
import json
import re
import statistics
import subprocess
from pathlib import Path
from typing import Any


def mtp_metadata(model: dict[str, Any], inspection: dict[str, Any]) -> dict[str, Any] | None:
    metadata = inspection.get("metadata") or {}
    layers = next((value for key, value in metadata.items()
                   if key.endswith(".nextn_predict_layers") and type(value) is int and value > 0), None)
    declared = model.get("mtp") if isinstance(model.get("mtp"), dict) else {}
    if not layers and not declared:
        return None
    return {
        "modelSupport": "embedded" if layers else "publisher-declared",
        "source": "gguf-header" if layers else "catalog",
        "predictionLayers": layers,
        "defaultEnabled": False,
        "runtimeCheckRequired": True,
        "recommendation": "benchmark-required",
        "startingDraftTokens": 2,
        "sourceUrl": declared.get("source_url"),
    }


def parse_runtime_capability(help_text: str) -> dict[str, Any]:
    # Older builds expose --spec-type for ngrams, but cannot load MTP. Require
    # the enumerated mode and the current token-cap flag, not just "draft".
    section = re.search(r"(?m)^\s*--spec-type\s+([^\n]*(?:\n(?!\s*-)[^\n]*)*)", help_text[:262144])
    modes = set(re.findall(r"[a-z]+(?:-[a-z0-9]+)+", section.group(1) if section else ""))
    supported = "draft-mtp" in modes and bool(re.search(r"(?m)^\s*--spec-draft-n-max\b", help_text))
    return {"mtp": supported, "reason": None if supported else "runtime-mtp-unavailable"}


def probe_runtime(executable: Path | str) -> dict[str, Any]:
    executable = Path(executable).resolve(strict=True)
    if not executable.is_file():
        raise ValueError("runtime must be an executable file")
    result = subprocess.run([str(executable), "--help"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", timeout=20,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode:
        return {"mtp": False, "reason": "runtime-probe-failed"}
    flags = set(re.findall(r"(?<!\w)--[a-z][a-z0-9-]*", '\n'.join(
        line for line in result.stdout.splitlines() if line.lstrip().startswith('-'))))
    return {**parse_runtime_capability(result.stdout),
        "loadModeArguments": ["--load-mode", "mmap"] if "--load-mode" in flags else ["--mmap"] if "--mmap" in flags else [],
        "lemonade10MmapCompatible": {"--mmap", "--no-mmap"}.issubset(flags)}


def validate_runtime_command(command: list[str]) -> None:
    """Parse the actual launch arguments without loading a model or opening ports."""
    if not command or any(not isinstance(value, str) or '\x00' in value for value in command):
        raise ValueError("Invalid native runtime command")
    result = subprocess.run([*command, "--help"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode or '--ctx-size' not in result.stdout or '--model' not in result.stdout:
        # Do not echo potentially sensitive launch values or arbitrary output.
        raise ValueError("The selected llama.cpp executable rejected its launch arguments; requalify a compatible runtime before activating it")


def recommend_mtp(evidence: Any, signature: str) -> dict[str, Any]:
    """Only compare complete paired measurements for this exact run profile."""
    fallback = {"recommendation": "benchmark-required", "speedup": None}
    if not isinstance(evidence, dict) or evidence.get("signature") != signature:
        return fallback
    rates = {}
    for mode in ("baseline", "mtp"):
        samples = evidence.get(mode)
        if not isinstance(samples, list) or len(samples) < 3:
            return fallback
        values = []
        for sample in samples:
            if not isinstance(sample, dict) or sample.get("valid") is not True:
                return fallback
            tokens, milliseconds = sample.get("tokens"), sample.get("milliseconds")
            if type(tokens) is not int or tokens < 128 or type(milliseconds) not in (int, float) or not math.isfinite(milliseconds) or milliseconds <= 0:
                return fallback
            if mode == "mtp" and (type(sample.get("acceptedDraftTokens")) is not int or sample["acceptedDraftTokens"] < 1):
                return fallback
            values.append(tokens * 1000 / milliseconds)
        rates[mode] = statistics.median(values)
    ratio = rates["mtp"] / rates["baseline"]
    return {"recommendation": "mtp" if ratio >= 1.05 else "baseline", "speedup": round(ratio, 3)}


def qualify_memory_fit(evidence: Any, qualification: dict[str, Any]) -> dict[str, Any]:
    """Accept a completed isolated native run, never infer fit from file size."""
    profile = qualification.get("profile", {})
    signature = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
    if (not isinstance(evidence, dict) or evidence.get("status") != "completed"
            or signature != qualification.get("signature") or evidence.get("signature") != signature
            or evidence.get("profile") != profile):
        raise ValueError("Memory evidence does not match the qualified artifacts and runtime")
    if recommend_mtp(evidence, signature)["recommendation"] == "benchmark-required":
        raise ValueError("Memory evidence needs three valid baseline and MTP samples with real accepted drafts")
    execution = evidence.get("execution", {})
    if not isinstance(execution, dict):
        raise ValueError("Missing benchmark execution contract")
    execution_body = {key:value for key,value in execution.items() if key != "signature"}
    if (execution.get("signature") != hashlib.sha256(json.dumps(execution_body, sort_keys=True).encode()).hexdigest()
            or execution.get("qualificationSignature") != signature
            or execution.get("runtimeMode") != "lemonade" or execution.get("gpuLayers") != "99"
            or profile.get("launchMode") != "lemonade" or profile.get("gpuLayers") != "99"
            or execution.get("backend") not in {"vulkan", "rocm", "metal", "cpu", "cuda"}
            or execution.get("context") != profile.get("context") or execution.get("cacheType") != "q4_0"
            or execution.get("draftTokens") != profile.get("draftTokens")
            or execution.get("visionProjectorSha256") != profile.get("visionProjectorSha256")
            or not re.fullmatch(r"[0-9a-f]{64}", str(execution.get("visionProjectorSha256", "")))
            or not re.fullmatch(r"[\w.-]+\.gguf", str(execution.get("visionProjectorFile", "")))):
        raise ValueError("Memory evidence was not collected with the actual Lemonade launch parameters")
    conditions = evidence.get("conditions", {})
    if not isinstance(conditions, dict) or conditions.get("isolatedFromLemonade") is not True or conditions.get("visionProjectorLoaded") is not True:
        raise ValueError("Memory evidence does not prove an isolated multimodal model load")
    hardware = evidence.get("hardware", {})
    if (not isinstance(hardware, dict) or not isinstance(hardware.get("name"), str) or not hardware["name"]
            or hardware.get("backend") not in {"amd", "nvidia", "apple"}
            or type(hardware.get("memoryTotalMB")) is not int or hardware["memoryTotalMB"] <= 0
            or type(hardware.get("systemRamGB")) is not int or hardware["systemRamGB"] <= 0):
        raise ValueError("Memory evidence has no measured hardware identity")
    compact = lambda value: re.sub(r"[^a-z0-9]", "", str(value).lower())
    if compact(hardware["name"]) not in compact(profile.get("hardware")):
        raise ValueError("Memory evidence GPU differs from the qualified runtime hardware")
    snapshots = evidence.get("memorySnapshots", [])
    if not isinstance(snapshots, list) or any(not any(row.get("mode") == mode for row in snapshots if isinstance(row,dict)) for mode in ("baseline","mtp")):
        raise ValueError("Memory evidence lacks GPU observations during both runs")
    for row in snapshots:
        if (not isinstance(row, dict) or any(row.get(key) != hardware.get(key) for key in ("name","backend","memoryTotalMB","systemRamGB"))
                or type(row.get("memoryUsedMB")) is not int or not 0 < row["memoryUsedMB"] <= hardware["memoryTotalMB"] + 256):
            raise ValueError("Memory telemetry is missing or inconsistent")
    return {"schemaVersion":1, "source":"measured-native", "qualificationSignature":signature,
        "executionSignature":execution["signature"], "runtimeMode":"lemonade", "runtimeBackend":execution["backend"], "gpuLayers":"99",
        "modelSha256":profile["modelSha256"], "runtimeSha256":profile["runtimeSha256"],
        "contextLength":profile["context"], "draftTokens":profile["draftTokens"],
        "visionProjectorFile":execution["visionProjectorFile"], "visionProjectorSha256":execution["visionProjectorSha256"],
        "hardware":{key:hardware[key] for key in ("name","backend","memoryTotalMB","systemRamGB")},
        "observedGpuMemoryMB":max(row["memoryUsedMB"] for row in snapshots), "measuredAt":evidence.get("finishedAt")}
