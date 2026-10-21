#!/usr/bin/env python3
"""Generate config/llama-cpp-architectures.json.

The file lists the GGUF architectures (`general.architecture`) each pinned
llama.cpp build can load: LLM_ARCH_NAMES from src/llama-arch.cpp at the
build's release tag. The Models page refuses a Hugging Face model whose
architecture the host's build does not list, before anything is downloaded.

`backendBuilds` (which build each backend runs) is the release policy. It is
edited by hand together with the image and archive pins, and
tests/contracts/test-llama-cpp-compat.py checks it against every pin. This
script regenerates the `builds` section for every build `backendBuilds` names.

Run from ods/ after any llama.cpp pin change:

    python3 scripts/generate-llama-architectures.py --write
    python3 scripts/generate-llama-architectures.py --write --source b9014=/path/to/llama-arch.cpp

Without --write it prints the document and exits 1 when the committed file
differs, so it can serve as a check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT = ROOT_DIR / "config" / "llama-cpp-architectures.json"
RAW_URL = "https://raw.githubusercontent.com/ggml-org/llama.cpp/{tag}/src/llama-arch.cpp"
GGML_URL = "https://raw.githubusercontent.com/ggml-org/llama.cpp/{tag}/ggml/include/ggml.h"
COMMIT_URL = "https://api.github.com/repos/ggml-org/llama.cpp/commits/{tag}"
USER_AGENT = "ODS-architecture-generator"
_ENTRY_RE = re.compile(r'\{\s*LLM_ARCH_[A-Z0-9_]+\s*,\s*"([^"]+)"\s*\}')
_TYPE_RE = re.compile(r"\bGGML_TYPE_([A-Z0-9_]+)\s*=\s*(\d+)\s*,")


def architecture_names(source: str) -> list[str]:
    """Names in the LLM_ARCH_NAMES map of a llama-arch.cpp source file."""
    start = source.find("LLM_ARCH_NAMES = {")
    if start < 0:
        raise ValueError("LLM_ARCH_NAMES not found in llama-arch.cpp")
    end = source.find("\n};", start)
    if end < 0:
        raise ValueError("LLM_ARCH_NAMES is not terminated")
    names = {name for name in _ENTRY_RE.findall(source[start:end]) if name != "(unknown)"}
    if not names:
        raise ValueError("LLM_ARCH_NAMES has no entries")
    return sorted(names)


def tensor_types(source: str) -> dict[str, int]:
    """``GGML_TYPE_<NAME> = <id>`` entries of ggml.h's ``enum ggml_type``.

    Commented-out entries are types llama.cpp removed (Q4_2, Q4_0_4_4, ...),
    so comments are stripped first; GGML_TYPE_COUNT is not a type.
    """
    start = source.find("enum ggml_type {")
    if start < 0:
        raise ValueError("enum ggml_type not found in ggml.h")
    end = source.find("};", start)
    if end < 0:
        raise ValueError("enum ggml_type is not terminated")
    body = re.sub(r"/\*.*?\*/", "", source[start:end], flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    types = {name: int(value) for name, value in _TYPE_RE.findall(body) if name != "COUNT"}
    if not types:
        raise ValueError("enum ggml_type has no entries")
    return dict(sorted(types.items(), key=lambda item: item[1]))


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def build_entry(tag: str, source_path: Path | None, ggml_path: Path | None = None) -> dict:
    if source_path is not None:
        source = source_path.read_text(encoding="utf-8")
    else:
        source = _fetch(RAW_URL.format(tag=tag)).decode("utf-8")
    if ggml_path is not None:
        ggml = ggml_path.read_text(encoding="utf-8")
    else:
        ggml = _fetch(GGML_URL.format(tag=tag)).decode("utf-8")
    commit = json.loads(_fetch(COMMIT_URL.format(tag=tag)))["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"{tag}: unexpected commit {commit!r}")
    return {"commit": commit, "architectures": architecture_names(source), "tensorTypes": tensor_types(ggml)}


def render(document: dict) -> str:
    return json.dumps(document, indent=2) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="update the committed file")
    parser.add_argument("--source", action="append", default=[], metavar="TAG=PATH",
                        help="read llama-arch.cpp for TAG from a local file")
    parser.add_argument("--ggml-source", action="append", default=[], metavar="TAG=PATH",
                        help="read ggml/include/ggml.h for TAG from a local file")
    args = parser.parse_args(argv)
    sources: dict[str, Path] = {}
    ggml_sources: dict[str, Path] = {}
    for option, items, target in (("--source", args.source, sources), ("--ggml-source", args.ggml_source, ggml_sources)):
        for item in items:
            tag, _, path = item.partition("=")
            if not tag or not path:
                parser.error(f"{option} needs TAG=PATH, got {item!r}")
            target[tag] = Path(path)

    current = json.loads(OUTPUT.read_text(encoding="utf-8"))
    backend_builds = current["backendBuilds"]
    builds = {}
    for tag in sorted(set(backend_builds.values()), key=lambda value: int(value.lstrip("b"))):
        builds[tag] = build_entry(tag, sources.get(tag), ggml_sources.get(tag))
    document = {
        "schemaVersion": 1,
        "generatedBy": "scripts/generate-llama-architectures.py",
        "backendBuilds": backend_builds,
        "builds": builds,
    }
    text = render(document)
    if args.write:
        OUTPUT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {OUTPUT.relative_to(ROOT_DIR)}: " + ", ".join(
            f"{tag} ({len(entry['architectures'])} architectures, {len(entry['tensorTypes'])} tensor types)"
            for tag, entry in builds.items()))
        return 0
    sys.stdout.write(text)
    return 0 if text == OUTPUT.read_text(encoding="utf-8") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
