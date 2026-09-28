#!/usr/bin/env python3
"""Every curated catalog download must name an immutable Hugging Face revision.

A mutable ref such as ``resolve/main`` lets an upstream rewrite change or
delete the file behind a catalog entry without any ODS change. On 2026-07-16
ggml-org deleted its Gemma 4 26B-A4B and 31B Q4_K_M files from ``main``, and
both catalog downloads failed with HTTP 404 until they were repointed.
Pinning a 40-hex commit keeps ``gguf_url``, ``gguf_sha256`` and ``size_bytes``
describing one exact file.

Network-free: this checks the catalog's shape, not Hugging Face. Verify a new
pin against the Hub's LFS metadata (``lfs.oid`` / ``lfs.size``) before adding it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "model-library.json"

PINNED_URL = re.compile(
    r"^https://huggingface\.co/"
    r"(?P<repo>[^/\s]+/[^/\s]+)/resolve/(?P<revision>[0-9a-f]{40})/(?P<path>[^\s]+\.gguf)$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _artifacts(model: dict) -> list[dict]:
    parts = model.get("gguf_parts")
    if isinstance(parts, list) and parts:
        return [
            {"file": part.get("file"), "url": part.get("url"), "sha256": part.get("sha256")}
            for part in parts
        ]
    return [{
        "file": model.get("gguf_file"),
        "url": model.get("gguf_url"),
        "sha256": model.get("gguf_sha256"),
    }]


def _models() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))["models"]


def test_every_download_url_pins_a_commit() -> None:
    offenders = []
    for model in _models():
        for artifact in _artifacts(model):
            url = str(artifact.get("url") or "")
            if not PINNED_URL.match(url):
                offenders.append(f"{model['id']}: {url or '<missing url>'}")
    assert not offenders, (
        "catalog download URLs must use resolve/<40-hex commit>, not a branch or tag:\n  "
        + "\n  ".join(offenders)
    )


def test_every_download_declares_a_sha256() -> None:
    offenders = [
        f"{model['id']}: {artifact.get('file')}"
        for model in _models()
        for artifact in _artifacts(model)
        if not SHA256.match(str(artifact.get("sha256") or ""))
    ]
    assert not offenders, "catalog downloads need a lowercase 64-hex sha256:\n  " + "\n  ".join(offenders)


def test_split_parts_share_one_revision() -> None:
    for model in _models():
        parts = model.get("gguf_parts")
        if not isinstance(parts, list) or not parts:
            continue
        sources = {
            (match.group("repo"), match.group("revision"))
            for match in (PINNED_URL.match(str(part.get("url") or "")) for part in parts)
            if match
        }
        assert len(sources) == 1, f"{model['id']}: split parts come from {sorted(sources)}"


def test_declared_source_matches_download_url() -> None:
    for model in _models():
        match = PINNED_URL.match(str(model.get("gguf_url") or ""))
        if not match:
            continue
        if model.get("source_repo"):
            assert model["source_repo"] == match.group("repo"), model["id"]
        if model.get("source_revision"):
            assert model["source_revision"] == match.group("revision"), model["id"]


def test_guard_rejects_mutable_refs() -> None:
    for url in (
        "https://huggingface.co/org/repo-GGUF/resolve/main/model-Q4_K_M.gguf",
        "https://huggingface.co/org/repo-GGUF/resolve/v1.0/model-Q4_K_M.gguf",
        "https://huggingface.co/org/repo-GGUF/resolve/c099eb48e663/model-Q4_K_M.gguf",
        "https://huggingface.co/org/repo-GGUF/resolve/"
        "C099EB48E663FD284577B04978A94FFCCB261841/model-Q4_K_M.gguf",
    ):
        assert not PINNED_URL.match(url), url
    assert PINNED_URL.match(
        "https://huggingface.co/org/repo-GGUF/resolve/"
        "c099eb48e663fd284577b04978a94ffccb261841/Q4_K_M/model-Q4_K_M-00001-of-00002.gguf"
    )


def main() -> int:
    test_guard_rejects_mutable_refs()
    test_every_download_url_pins_a_commit()
    test_every_download_declares_a_sha256()
    test_split_parts_share_one_revision()
    test_declared_source_matches_download_url()
    print("[PASS] Every catalog download pins a Hugging Face commit and declares a sha256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
