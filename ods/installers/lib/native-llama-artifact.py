#!/usr/bin/env python3
"""Resolve committed native llama.cpp pins and verify local bytes; no network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


def resolve(manifest, platform, tag):
    document = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or not isinstance(document.get("artifacts"), list):
        raise ValueError("Unsupported native llama artifact manifest")
    suffixes = {"macos-arm64": "macos-arm64.tar.gz", "windows-vulkan-x64": "win-vulkan-x64.zip"}
    entries = {}
    for entry in document["artifacts"]:
        target, version = entry["platform"], entry["tag"]
        if target not in suffixes or not re.fullmatch(r"b[0-9]+", version):
            raise ValueError("Unsupported native llama artifact selection")
        asset = "llama-{}-bin-{}".format(version, suffixes[target])
        url = "https://github.com/ggml-org/llama.cpp/releases/download/{}/{}".format(version, asset)
        if entry.get("asset") != asset or entry.get("url") != url:
            raise ValueError("Native llama artifact URL/asset does not match its selection")
        if not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
            raise ValueError("Native llama artifact has no valid reviewed SHA-256")
        if (target, version) in entries:
            raise ValueError("Duplicate native llama artifact selection")
        entries[target, version] = entry
    if (platform, tag) not in entries:
        raise ValueError("No reviewed native llama artifact for {}/{}".format(platform, tag))
    return entries[platform, tag]


def verify(path, expected):
    # The caller owns a private staging directory; never accept a symlink or
    # special/empty file, even if its contents would have the expected digest.
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_nlink != 1:
        raise ValueError("Native llama archive is not a nonempty regular staged file")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise ValueError("Native llama archive is not owned by the installer user")
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError("Native llama archive SHA-256 does not match the reviewed artifact")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--verify")
    args = parser.parse_args()
    try:
        artifact = resolve(args.manifest, args.platform, args.tag)
        if args.verify:
            verify(args.verify, artifact["sha256"])
        print("\t".join(artifact[key] for key in ("asset", "url", "sha256")))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("[ERROR] {}".format(error), file=sys.stderr)
        return 20
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
