#!/usr/bin/env python3
"""Inspect a local GGUF/runtime and emit a portable, explicit MTP launch profile.

This command never downloads, starts, stops or switches an inference server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/dashboard-api"))
from gguf_inspector import inspect_gguf
from model_mtp import mtp_metadata, probe_runtime, recommend_mtp, validate_runtime_command


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--hardware-id", required=True, help="Exact accelerator/CPU identifier used for benchmark matching")
    parser.add_argument("--context", type=int, default=16384)
    parser.add_argument("--draft-tokens", type=int, choices=range(1, 7), default=2)
    parser.add_argument("--launch-mode", choices=("native", "lemonade"), default="native")
    parser.add_argument("--vision-projector", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--benchmark-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    model, runtime = args.model.resolve(strict=True), args.runtime.resolve(strict=True)
    if model.suffix.lower() != ".gguf" or not model.is_file() or args.context < 512:
        parser.error("a completed GGUF and context >= 512 are required")
    digest = sha256(model)
    if digest != args.expected_sha256.lower():
        parser.error("model SHA-256 does not match the publisher artifact")
    inspection = inspect_gguf(model, max_metadata_bytes=32 * 1024 * 1024)
    support = mtp_metadata({}, inspection)
    if not inspection.get("readable") or not support or support["modelSupport"] != "embedded":
        parser.error("this artifact does not declare embedded MTP; external draft models need separate qualification")
    if args.context > (inspection.get("context_length") or 0):
        parser.error("requested context exceeds the artifact's declared native context")
    capability = probe_runtime(runtime)
    if args.launch_mode == "lemonade" and not capability.get("lemonade10MmapCompatible"):
        parser.error("Lemonade 10 can inject --no-mmap; this executable removed the legacy mmap flags. Qualify a compatible runtime or update the router first")
    load_args = ["--mmap"] if args.launch_mode == "lemonade" else capability.get("loadModeArguments", [])
    projector = args.vision_projector.resolve(strict=True) if args.vision_projector else None
    if args.launch_mode == "lemonade" and (projector is None or not projector.is_file()):
        parser.error("Lemonade qualification requires its actual --vision-projector")
    gpu_layers = "99" if args.launch_mode == "lemonade" else "auto"
    baseline = [str(runtime), "--model", str(model), "--jinja", "--ctx-size", str(args.context),
                "--parallel", "1", "--gpu-layers", gpu_layers, "--flash-attn", "on",
                "--cache-type-k", "q4_0", "--cache-type-v", "q4_0"]
    if projector:
        baseline += ["--mmproj", str(projector)]
    if args.launch_mode == "lemonade":
        baseline += [*load_args, "--context-shift", "--keep", "16", "--reasoning-format", "auto", "--no-webui"]
    elif load_args:
        baseline += load_args
    mtp = baseline + ["--spec-type", "draft-mtp", "--spec-draft-n-max", str(args.draft_tokens),
                      "--spec-draft-type-k", "q4_0", "--spec-draft-type-v", "q4_0"]
    for command in (baseline, mtp):
        validate_runtime_command(command)
    if args.launch_mode == "lemonade":
        validate_runtime_command([*mtp, "--no-mmap"])
    signature_data = {"modelSha256": digest, "runtimeSha256": sha256(runtime),
                      "hardware": args.hardware_id, "platform": platform.platform(),
                      "context": args.context, "draftTokens": args.draft_tokens,
                      "cacheType": "q4_0", "parallel": 1, "gpuLayers": gpu_layers, "flashAttention": "on",
                      "loadModeArguments":load_args}
    if args.launch_mode == "lemonade":
        signature_data.update(launchMode="lemonade", visionProjectorSha256=sha256(projector))
    signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True).encode()).hexdigest()
    evidence = json.loads(args.benchmark_evidence.read_text()) if args.benchmark_evidence else None
    recommendation = recommend_mtp(evidence, signature) if capability["mtp"] else {"recommendation":"baseline","speedup":None}
    result = {"schemaVersion":1, "signature":signature, "profile":signature_data,
              "model":support, "runtime":capability, **recommendation,
              "baselineCommand":baseline, "mtpCommand":mtp if capability["mtp"] else None,
              "notes":["Launch arguments were parsed with --help without loading the model. Validate memory fit before serving traffic.",
                       "MTP is recommended only after at least three valid paired samples of 128+ tokens and a measured gain of 5%."]}
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if capability["mtp"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
