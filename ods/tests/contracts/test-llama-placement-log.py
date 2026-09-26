#!/usr/bin/env python3
"""llama.cpp placement-log contract: every pinned build's load log is readable.

ODS learns where llama.cpp put a model (all layers on the GPU, or some in
system RAM) only from llama-server's load log: "offloaded N/M layers to GPU",
the buffer sizes and the fit projection. ods doctor
(scripts/llama_gpu_residency.py), the host agent and the fleet gate parse
those lines. A llama.cpp build that changes them, or logs them at a verbosity
ODS does not set, turns the check into "unverified" on every host.
llama.cpp b9151 did exactly that: it moved them from the default verbosity (3)
to trace (4) (ggml-org/llama.cpp#23021).

So every llama.cpp build ODS pins must have a load log captured here from
that build, run the way ODS runs it, and the parsers must read placement from
it. Moving a pin fails this contract until such a log is captured under
tests/fixtures/llama-placement/, registered in CAPTURED_LOAD_LOGS and parsed.
For builds from b9151 the pinned stack must also run llama-server at
verbosity 4:
  - Docker: docker-compose.base.yml passes LLAMA_ARG_LOG_VERBOSITY (read from
    b9360; b9151-b9357 read LLAMA_LOG_VERBOSITY, which ODS does not pass).
  - Native macOS and Windows launchers pass no verbosity today, so a native
    pin from b9151 fails here until they pass -lv 4 and this contract checks
    it.

Every shipped ghcr.io/ggml-org/llama.cpp reference must name its build: a
bNNNN tag, or a version tag listed in KNOWN_VERSION_BUILDS (v0.5.0 is
b11146). A version tag not listed, a tag without a build (server-cuda,
latest) or a digest alone fails here, so a pin cannot move without this
contract seeing the build it moves to.

Lemonade's llama.cpp (LLAMA_CPP_REF, AMD) is exempt: Lemonade manages GPU
placement itself and ods doctor skips it.

Run from ods/:  python3 tests/contracts/test-llama-placement-log.py
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
FIXTURES = ROOT_DIR / "tests" / "fixtures" / "llama-placement"
RESIDENCY_SCRIPT = ROOT_DIR / "scripts" / "llama_gpu_residency.py"
MODEL_MEMORY = ROOT_DIR / "extensions" / "services" / "dashboard-api" / "model_memory.py"
COMPOSE_BASE = ROOT_DIR / "docker-compose.base.yml"

# Pinned llama.cpp build -> load logs captured from that build as ODS runs it
# (for b9151+, with LLAMA_ARG_LOG_VERBOSITY=4). Each must be a complete load:
# the start line, placement and the "model loaded" line.
CAPTURED_LOAD_LOGS = {
    9014: (
        # tower2, RTX PRO 6000, Qwen3.5-2B, -e LLAMA_ARG_LOG_VERBOSITY=4 (b9014 ignores it).
        "tower2-rtxpro6000-qwen35-2b-resident-b9014.txt",
    ),
}

# llama.cpp release versions ODS may pin, and the build each one is. A new
# version tag fails the contract until it is listed here (from the release's
# own banner: "build: NNNNN (...)").
KNOWN_VERSION_BUILDS = {
    "0.5.0": 11146,
}
# Digest-only references (no tag) whose build was checked by hand. Empty: pin
# with a bNNNN or listed version tag plus the digest instead.
KNOWN_DIGEST_BUILDS: dict[str, int] = {}

# Any reference to the llama.cpp images: an optional tag, an optional digest.
IMAGE_RE = re.compile(
    r"ghcr\.io/ggml-org/llama\.cpp(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]*))?(?:@(?P<digest>sha256:[0-9a-f]{64}))?"
)
TAG_BUILD_RE = re.compile(r"(?:^|-)b(\d{4,5})(?:-|$)")
TAG_VERSION_RE = re.compile(r"(?:^|-)v(\d+\.\d+\.\d+)(?:-|$)")
RELEASE_TAG_RE = re.compile(
    r"\b(?:LLAMA_CPP_RELEASE_TAG(?:_OVERRIDE)?|LLAMA_TAG|LlamaCppReleaseTag)\b[^\n]*?"
    r"\b(?:b(?P<build>\d{4,5})|v(?P<version>\d+\.\d+\.\d+))\b"
)
SCANNED_SUFFIXES = {".yml", ".yaml", ".json", ".sh", ".ps1", ".psm1", ".py", ".example"}
SKIPPED_DIRECTORIES = {"tests", "node_modules", "vendor", "data", "dist", "__pycache__", ".git"}
VERBOSITY_DEFAULT_RE = re.compile(r"^\s*-\s*LLAMA_ARG_LOG_VERBOSITY=\$\{LLAMA_ARG_LOG_VERBOSITY:-(\d+)\}\s*$", re.M)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def image_build(tag: str | None, digest: str | None) -> tuple[int | None, str]:
    """The llama.cpp build an image reference pins, or (None, why not)."""
    if tag:
        match = TAG_BUILD_RE.search(tag)
        if match:
            return int(match.group(1)), ""
        match = TAG_VERSION_RE.search(tag)
        if match:
            build = KNOWN_VERSION_BUILDS.get(match.group(1))
            if build is None:
                return None, (f"version tag v{match.group(1)} is not in KNOWN_VERSION_BUILDS; add the "
                              f"build its release banner reports")
            return build, ""
        if digest and digest in KNOWN_DIGEST_BUILDS:
            return KNOWN_DIGEST_BUILDS[digest], ""
        return None, f"tag {tag!r} names no llama.cpp build (bNNNN or a listed vX.Y.Z)"
    if digest:
        if digest in KNOWN_DIGEST_BUILDS:
            return KNOWN_DIGEST_BUILDS[digest], ""
        return None, "a digest alone names no llama.cpp build; pin a bNNNN or listed vX.Y.Z tag with it"
    return None, ""


def pinned_builds(unresolved: list[str] | None = None) -> dict[int, dict[str, set[str]]]:
    """build -> {"docker": files, "native": files} across the shipped tree.

    References whose build cannot be worked out are appended to
    ``unresolved`` as "file: reference: reason".
    """
    pins: dict[int, dict[str, set[str]]] = {}
    for directory, subdirectories, names in os.walk(ROOT_DIR):
        subdirectories[:] = sorted(name for name in subdirectories if name not in SKIPPED_DIRECTORIES)
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix not in SCANNED_SUFFIXES or name.startswith("test"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = path.relative_to(ROOT_DIR).as_posix()
            for match in IMAGE_RE.finditer(text):
                tag, digest = match.group("tag"), match.group("digest")
                following = text[match.end():match.end() + 1]
                if not tag and not digest:
                    continue  # the bare repository name, e.g. a digest-policy list
                if following in {"$", "{"}:
                    continue  # a templated tag; its build variable is scanned below
                build, why = image_build(tag, digest)
                if build is None:
                    if unresolved is not None:
                        unresolved.append(f"{relative}: {match.group(0)[:80]}: {why}")
                    continue
                pins.setdefault(build, {"docker": set(), "native": set()})["docker"].add(relative)
            for match in RELEASE_TAG_RE.finditer(text):
                # docker-compose.arc.yml builds the Arc image from a llama.cpp tag.
                kind = "docker" if name.startswith("docker-compose") else "native"
                if match.group("build"):
                    build = int(match.group("build"))
                else:
                    build = KNOWN_VERSION_BUILDS.get(match.group("version"))
                    if build is None:
                        if unresolved is not None:
                            unresolved.append(
                                f"{relative}: v{match.group('version')}: version tag is not in "
                                f"KNOWN_VERSION_BUILDS"
                            )
                        continue
                pins.setdefault(build, {"docker": set(), "native": set()})[kind].add(relative)
    return pins


def main() -> int:
    errors: list[str] = []
    residency = load_module("llama_gpu_residency", RESIDENCY_SCRIPT)
    model_memory = load_module("model_memory", MODEL_MEMORY) if MODEL_MEMORY.is_file() else None
    agent_parser = getattr(model_memory, "parse_llama_placement", None)

    unresolved: list[str] = []
    pins = pinned_builds(unresolved)
    if not pins:
        errors.append("found no pinned llama.cpp build; the pin scan is broken")
    for entry in unresolved:
        errors.append(f"cannot tell which llama.cpp build this pins, so no captured load log can be "
                      f"checked for it: {entry}")
    compose = COMPOSE_BASE.read_text(encoding="utf-8")
    verbosity_match = VERBOSITY_DEFAULT_RE.search(compose)
    docker_verbosity = int(verbosity_match.group(1)) if verbosity_match else None

    for build, where in sorted(pins.items()):
        files = sorted(where["docker"] | where["native"])
        logs = CAPTURED_LOAD_LOGS.get(build)
        if not logs:
            errors.append(
                f"llama.cpp b{build} is pinned ({', '.join(files[:4])}{' ...' if len(files) > 4 else ''}) but no "
                f"load log captured from b{build} is registered in CAPTURED_LOAD_LOGS. Capture one as ODS runs "
                f"it (LLAMA_ARG_LOG_VERBOSITY=4) under tests/fixtures/llama-placement/ and update the parsers "
                f"until it reads as resident"
            )
            continue
        if build >= residency.TRACE_PLACEMENT_FROM_BUILD:
            if where["docker"] and build < residency.ARG_VERBOSITY_ENV_FROM_BUILD:
                errors.append(
                    f"b{build} reads LLAMA_LOG_VERBOSITY, but docker-compose.base.yml passes only "
                    f"LLAMA_ARG_LOG_VERBOSITY: placement would not be logged ({', '.join(sorted(where['docker']))})"
                )
            elif where["docker"] and (docker_verbosity or 0) < residency.PLACEMENT_LOG_VERBOSITY:
                errors.append(
                    f"b{build} logs placement only at verbosity {residency.PLACEMENT_LOG_VERBOSITY}; "
                    f"docker-compose.base.yml must pass LLAMA_ARG_LOG_VERBOSITY=${{LLAMA_ARG_LOG_VERBOSITY:-"
                    f"{residency.PLACEMENT_LOG_VERBOSITY}}}"
                )
            if where["native"]:
                errors.append(
                    f"native llama.cpp b{build} ({', '.join(sorted(where['native']))}) logs placement only at "
                    f"verbosity {residency.PLACEMENT_LOG_VERBOSITY}; make the native launchers pass -lv "
                    f"{residency.PLACEMENT_LOG_VERBOSITY} and check that here"
                )
        for name in logs:
            path = FIXTURES / name
            if not path.is_file():
                errors.append(f"b{build}: captured load log {name} is missing")
                continue
            text = path.read_text(encoding="utf-8")
            facts = residency.log_facts(text)
            if facts["build"] != build:
                errors.append(f"{name}: its banner reports build {facts['build']}, not b{build}")
            if not (facts["load_started"] and facts["ready"]):
                errors.append(f"{name}: the doctor parser does not find b{build}'s model-load start and "
                              f"'model loaded' lines {facts}")
            if (build >= residency.TRACE_PLACEMENT_FROM_BUILD
                    and (facts["log_verbosity"] or 0) < residency.PLACEMENT_LOG_VERBOSITY):
                errors.append(f"{name}: not captured at verbosity {residency.PLACEMENT_LOG_VERBOSITY}")
            relevant = "\n".join(line for line in text.splitlines() if residency.is_relevant_line(line))
            verdict = residency.judge(relevant, [], {}, name, server_ready=True)
            if verdict["status"] not in {"pass", "fail", "intentional"} or not verdict.get("layers_total"):
                errors.append(f"{name}: ods doctor cannot read b{build}'s placement: {verdict['status']} - "
                              f"{verdict['message']}")
            if agent_parser is not None:
                # The host agent keeps only placement lines before parsing.
                keep = getattr(model_memory, "is_placement_log_line", lambda line: True)
                kept = "\n".join(line for line in text.splitlines() if keep(line))
                agent = agent_parser(kept)
                if agent.get("layersTotal") is None or agent.get("status") == "unverified":
                    errors.append(f"{name}: model_memory.parse_llama_placement cannot read b{build}'s "
                                  f"placement: {agent.get('status')} - {agent.get('reason')}")
                for probe in ("is_llama_start_line", "is_llama_ready_line"):
                    check = getattr(model_memory, probe, None)
                    if check is not None and not any(check(line) for line in text.splitlines()):
                        errors.append(f"{name}: model_memory.{probe} matches no line of b{build}'s load log")

    if errors:
        print("[FAIL] llama.cpp placement-log contract")
        for error in errors:
            print(f"  - {error}")
        return 1
    summary = ", ".join(
        f"b{build} ({len(where['docker'] | where['native'])} pins)" for build, where in sorted(pins.items())
    )
    print(f"[PASS] placement is read from captured load logs of every pinned llama.cpp build: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
