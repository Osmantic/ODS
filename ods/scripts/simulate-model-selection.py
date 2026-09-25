#!/usr/bin/env python3
"""Run the installer model selector over every hardware envelope.

For each envelope in tests/fixtures/model-selection-envelopes.json this runs
``scripts/select-model.py`` from ``--root`` (any ODS tree: this checkout, or an
older one extracted with ``git archive``) exactly as the Linux and macOS
installers do, then re-checks the pick with *this* checkout's memory estimator
(model_memory.estimate_model_memory) so an older selector's picks get a real
fit column:

* ``corrected GiB``: weights + KV + recurrent state + overhead for the pick at
  its context and cache types (plus host context checkpoints on the CPU
  backend), from this checkout's catalog metadata, or the fixture's
  ``reference_architecture`` for stale picks that carry none;
* ``fits``: that estimate against the selector capacity minus the memory
  class's margin (on the CPU backend, also the llama-server container limit);
  on a discrete GPU, whether llama.cpp keeps the pick fully on the idle GPU
  as configured (model_memory.gpu_residency_fit at the pick's context, its
  runtime profile and the residency settings the selector emitted, on the
  envelope's platform; an older selector emits none);
* ``fits @64K``: the same after the Linux/Windows Hermes raise to 65,536.

Offline and read-only: it never downloads, installs or starts anything.

Usage:
  simulate-model-selection.py [--root ODS_TREE] [--format md|json] [--out FILE]
  simulate-model-selection.py --check-golden tests/fixtures/model-selection-golden.json
  simulate-model-selection.py --update-golden tests/fixtures/model-selection-golden.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

THIS_ODS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(THIS_ODS / "extensions/services/dashboard-api"))
from model_memory import (  # noqa: E402
    architecture_metadata_complete,
    estimate_model_memory,
    fit_margin_gib,
    gpu_residency_fit,
)

DEFAULT_ENVELOPES = THIS_ODS / "tests/fixtures/model-selection-envelopes.json"
HERMES_CONTEXT = 65536
GOLDEN_FIELDS = (
    "pick", "runtime_profile", "context_length", "cache_types", "policy",
    "selector_estimate_gib", "corrected_estimate_gib", "fits", "fits_at_hermes",
)


def memory_class(backend: str, memory_type: str, vram_mb: int) -> str:
    key = str(backend or "").lower()
    if key == "apple" or str(memory_type or "").lower() == "unified":
        return "unified"
    if key in {"", "cpu", "none", "unknown"} or int(vram_mb or 0) <= 0:
        return "cpu"
    return "discrete"



def tier_map_size_mb(tier_map: Path, tier: str, host_arch: str) -> int:
    """LLM_MODEL_SIZE_MB that installers/lib/tier-map.sh sets for a qwen tier.

    Parsed rather than sourced so the harness also runs where ``bash`` is not
    Git Bash (tests/test-model-selection-matrix.py checks the parse against a
    sourced tier map on Linux). Inside a tier's case arm the last assignment
    wins for arm64 (the NV_ULTRA aarch64 branch) and the first otherwise.
    """
    text = tier_map.read_text(encoding="utf-8")
    body = text[text.index("set_qwen_tier_config()"):]
    body = body[:body.index("\n}\n")]
    arms = re.split(r"\n        ([A-Z0-9_|]+)\)\n", body)
    for label, arm in zip(arms[1::2], arms[2::2]):
        if tier not in label.split("|"):
            continue
        sizes = re.findall(r"LLM_MODEL_SIZE_MB=(\d+)", arm)
        if not sizes:
            return 0
        return int(sizes[-1] if host_arch == "arm64" else sizes[0])
    return 0

def _gib(limit: str | None, default: float) -> float:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([GgMm])?[iI]?[bB]?\s*", str(limit or ""))
    if not match:
        return default
    value = float(match.group(1))
    return value / 1024.0 if (match.group(2) or "G").upper() == "M" else value


class Simulator:
    def __init__(self, root: Path, envelopes_path: Path):
        self.root = root.resolve()
        self.selector = self.root / "scripts/select-model.py"
        self.catalog_path = self.root / "config/model-library.json"
        self.tier_map = self.root / "installers/lib/tier-map.sh"
        fixture = json.loads(envelopes_path.read_text(encoding="utf-8"))
        self.envelopes = fixture["envelopes"]
        self.min_context = int(fixture.get("min_context") or HERMES_CONTEXT)
        self.cpu_limit_gib = float(fixture.get("cpu_container_limit_gib") or 6.0)
        self.reference = {
            key: value for key, value in (fixture.get("reference_architecture") or {}).items()
            if not key.startswith("_")
        }
        root_catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))["models"]
        self.root_models = {model["id"]: model for model in root_catalog}
        this_catalog = json.loads((THIS_ODS / "config/model-library.json").read_text(encoding="utf-8"))["models"]
        self.this_models = {model["id"]: model for model in this_catalog}
        help_text = subprocess.run(
            [sys.executable, str(self.selector), "--help"],
            capture_output=True, text=True, check=False,
        ).stdout
        self.supports_min_context = "--min-context" in help_text
        self.supports_platform = "--platform" in help_text

    def ceiling(self, envelope: dict[str, Any]) -> int:
        if envelope.get("ceiling") != "tier-map":
            return int(envelope.get("ceiling") or 0)
        return tier_map_size_mb(self.tier_map, str(envelope["tier"]), str(envelope["host_arch"]))

    def select(self, envelope: dict[str, Any], ceiling: int) -> dict[str, Any]:
        command = [
            sys.executable, str(self.selector),
            "--catalog", str(self.catalog_path),
            "--backend", envelope["backend"],
            "--memory-type", envelope["memory_type"],
            "--vram-mb", str(envelope["vram_mb"]),
            "--ram-gb", str(envelope["ram_gb"]),
            "--profile", "qwen",
            "--tier", str(envelope["tier"]),
            "--max-size-mb", str(ceiling),
            "--host-arch", envelope["host_arch"],
            "--installable-only",
        ]
        if self.supports_min_context:
            command += ["--min-context", str(self.min_context)]
        if self.supports_platform:
            # The envelope's OS, not the machine running the simulation.
            command += ["--platform", str(envelope.get("platform") or "linux")]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return {"error": (result.stderr.strip().splitlines() or ["selector failed"])[-1]}
        return json.loads(result.stdout)

    def architecture(self, model_id: str) -> dict[str, Any] | None:
        model = dict(self.this_models.get(model_id) or self.root_models.get(model_id) or {})
        if not model:
            return None
        if not architecture_metadata_complete(model) and model_id in self.reference:
            reference = {
                k: v for k, v in self.reference[model_id].items()
                if k not in {"source", "gguf_source"}
            }
            model = {**model, **reference}
        return model if architecture_metadata_complete(model) else None

    def run(self) -> list[dict[str, Any]]:
        rows = []
        for envelope in self.envelopes:
            rows.append(self.row(envelope))
        return rows

    def row(self, envelope: dict[str, Any]) -> dict[str, Any]:
        ceiling = self.ceiling(envelope)
        mclass = memory_class(envelope["backend"], envelope["memory_type"], envelope["vram_mb"])
        row: dict[str, Any] = {
            "envelope": envelope["id"],
            "label": envelope["label"],
            "platform": envelope["platform"],
            "host_kind": envelope["host_kind"],
            "backend": envelope["backend"],
            "memory_class": mclass,
            "vram_mb": envelope["vram_mb"],
            "ram_gb": envelope["ram_gb"],
            "tier": str(envelope["tier"]),
            "ceiling_mb": ceiling,
            "fleet_hosts": envelope.get("fleet_hosts") or [],
        }
        payload = self.select(envelope, ceiling)
        if "error" in payload:
            row.update({"pick": "ERROR", "error": payload["error"]})
            for key in GOLDEN_FIELDS:
                row.setdefault(key, None)
            return row
        selected_id = payload["selected"]["id"]
        chosen = next(
            (alt for alt in payload.get("alternatives", []) if alt["id"] == selected_id),
            payload["alternatives"][0],
        )
        root_model = self.root_models.get(selected_id, {})
        profile_id = chosen.get("runtime_profile")
        profile = next(
            (p for p in root_model.get("runtime_profiles") or [] if p.get("id") == profile_id),
            None,
        )
        env = (profile or {}).get("env") or {}
        cache_k = str(env.get("LLAMA_ARG_CACHE_TYPE_K") or "f16")
        cache_v = str(env.get("LLAMA_ARG_CACHE_TYPE_V") or "f16")
        checkpoints = env.get("LLAMA_ARG_CTX_CHECKPOINTS")
        parallel = int(env.get("LLAMA_PARALLEL") or 1)
        context = int(chosen["context_length"])
        capacity = float(payload["memory_capacity_gb"])
        container_limit = None
        if mclass == "cpu":
            container_limit = _gib(env.get("LLAMA_SERVER_MEMORY_LIMIT"), self.cpu_limit_gib)
        effective_capacity = min(capacity, container_limit) if container_limit else capacity
        platform = str(envelope.get("platform") or "linux")
        margin = fit_margin_gib(effective_capacity, mclass, gpu_platform=platform)

        def corrected(at_context: int) -> float | None:
            model = self.architecture(selected_id)
            if model is None:
                return None
            estimate = estimate_model_memory(
                model, context_length=at_context, cache_type_k=cache_k, cache_type_v=cache_v,
                parallel=parallel,
                ctx_checkpoints=int(checkpoints) if checkpoints is not None else None,
            )
            return estimate.total_gib if mclass == "cpu" else estimate.device_gib

        overrides = {
            str(key): value for key, value in (payload.get("gpu_residency_overrides") or {}).items()
        }

        def resident(at_context: int) -> bool | None:
            model = self.architecture(selected_id) or self.this_models.get(selected_id) or root_model
            if not model:
                return None
            fit = gpu_residency_fit(
                model, total_vram_mb=envelope["vram_mb"], runtime_profile=profile,
                context_length=at_context, gpu_platform=platform, overrides=overrides or None,
            )
            return bool(fit["fits"])

        def fits_at(at_context: int, estimate: float | None) -> bool | None:
            if mclass == "discrete":
                return resident(at_context)
            return None if estimate is None else estimate <= effective_capacity - margin + 1e-9

        corrected_now = corrected(context)
        fits = fits_at(context, corrected_now)
        if not envelope.get("hermes_raise"):
            fits_hermes: Any = "n/a"
            hermes_estimate = None
        elif context >= HERMES_CONTEXT:
            fits_hermes = fits
            hermes_estimate = corrected_now
        else:
            hermes_estimate = corrected(HERMES_CONTEXT)
            fits_hermes = fits_at(HERMES_CONTEXT, hermes_estimate)
        row.update({
            "pick": selected_id,
            "llm_model": payload["selected"].get("llm_model_name"),
            "runtime_profile": profile_id,
            "context_length": context,
            "cache_types": f"{cache_k}/{cache_v}",
            "selector_estimate_gib": chosen.get("estimated_required_gb"),
            "corrected_estimate_gib": corrected_now,
            "hermes_estimate_gib": hermes_estimate,
            "capacity_gib": capacity,
            "container_limit_gib": container_limit,
            "margin_gib": margin,
            "fits": fits,
            "fits_at_hermes": fits_hermes,
            "policy": payload["policy"],
        })
        return row


def _cell(value: Any) -> str:
    if value is None:
        return "?"
    if value is True:
        return "yes"
    if value is False:
        return "**NO**"
    return str(value)


def markdown(rows: list[dict[str, Any]], title: str) -> str:
    lines = [
        f"# {title}",
        "",
        "Generated by `ods/scripts/simulate-model-selection.py`. `corrected GiB` is "
        "this tree's architecture estimate for the pick (CPU: including host "
        "context checkpoints); `fits` compares it with the capacity (CPU: capped "
        "by the llama-server container limit) minus the memory-class margin, and "
        "on a discrete GPU says whether llama.cpp keeps the pick fully on the "
        "idle GPU at its settings (full GPU residency, native Linux; `margin` is "
        "then the platform reserve); `fits @64K` repeats that after the "
        "Linux/Windows Hermes raise.",
        "",
        "| envelope | host | tier | ceiling MB | class | pick | profile | ctx | KV | "
        "selector GiB | corrected GiB | capacity GiB | margin | fits | fits @64K | policy |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        capacity = _cell(row.get("capacity_gib"))
        if row.get("container_limit_gib"):
            capacity += f" (container {row['container_limit_gib']:g})"
        hosts = row.get("fleet_hosts") or []
        label = row["label"] + (f" **[{', '.join(hosts)}]**" if hosts else "")
        hermes = row.get("fits_at_hermes")
        hermes_text = _cell(hermes)
        if row.get("hermes_estimate_gib") is not None and row.get("context_length", 0) < HERMES_CONTEXT:
            hermes_text += f" ({row['hermes_estimate_gib']})"
        lines.append(
            f"| {label} | {row['host_kind']} | {row['tier']} | {row['ceiling_mb']} | "
            f"{row['memory_class']} | {row.get('pick')} | {row.get('runtime_profile') or '-'} | "
            f"{_cell(row.get('context_length'))} | {_cell(row.get('cache_types'))} | "
            f"{_cell(row.get('selector_estimate_gib'))} | {_cell(row.get('corrected_estimate_gib'))} | "
            f"{capacity} | {_cell(row.get('margin_gib'))} | {_cell(row.get('fits'))} | "
            f"{hermes_text} | {row.get('policy') or row.get('error')} |"
        )
    return "\n".join(lines) + "\n"


def golden_view(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["envelope"]: {key: row.get(key) for key in GOLDEN_FIELDS} for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=THIS_ODS, help="ODS tree whose selector and catalog to run")
    parser.add_argument("--envelopes", type=Path, default=DEFAULT_ENVELOPES)
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--title", default="Model selection simulation")
    golden = parser.add_mutually_exclusive_group()
    golden.add_argument("--check-golden", type=Path)
    golden.add_argument("--update-golden", type=Path)
    args = parser.parse_args()

    simulator = Simulator(args.root, args.envelopes)
    rows = simulator.run()

    if args.update_golden:
        args.update_golden.write_text(
            json.dumps(golden_view(rows), indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n",
        )
    status = 0
    if args.check_golden:
        expected = json.loads(args.check_golden.read_text(encoding="utf-8"))
        actual = golden_view(rows)
        for envelope in sorted(set(expected) | set(actual)):
            if expected.get(envelope) != actual.get(envelope):
                status = 1
                print(f"golden mismatch: {envelope}\n  expected {expected.get(envelope)}\n  actual   {actual.get(envelope)}",
                      file=sys.stderr)
    text = markdown(rows, args.title) if args.format == "md" else json.dumps(rows, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8", newline="\n")
    elif not args.check_golden:
        sys.stdout.write(text)
    return status


if __name__ == "__main__":
    sys.exit(main())
