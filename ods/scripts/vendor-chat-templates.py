#!/usr/bin/env python3
"""Vendor fixed chat templates from upstream llama.cpp into config/chat-templates.

Downloads exactly the named templates from llama.cpp's models/templates/ at a
release tag into config/chat-templates/upstream-<tag>/, adds llama.cpp's
LICENSE (MIT) when that directory has none, and rewrites the directory's
SHA256SUMS manifest. Bytes are kept exactly as upstream publishes them.

It never edits index.json. Add the entry that uses each template by hand:
embeddedTemplateSha256 is the broken template's hash from fleet evidence (the
model profile's templateSha256), fileSha256 is printed below, and builds
lists the llama.cpp builds the fix was qualified on. Vendor only templates an
entry uses; scripts/validate-chat-templates.py fails otherwise.

Run from ods/ on a development machine (an installation never fetches
templates):

    python3 scripts/vendor-chat-templates.py --tag b9014 Qwen-Qwen3-0.6B.jinja
    python3 scripts/vendor-chat-templates.py --tag b9014 --root /tmp/chat-templates NAME.jinja
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path
from typing import Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "bin"))

from model_profile import templates  # noqa: E402  (stdlib-only package in ods/bin)

RAW_URL = "https://raw.githubusercontent.com/ggml-org/llama.cpp/{tag}/{path}"
USER_AGENT = "ODS-chat-template-vendor"


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def vendor(root: Path, tag: str, names: list[str], fetch: Callable[[str], bytes] = _fetch) -> dict[str, str]:
    """Download ``names`` at ``tag`` into ``root``/upstream-<tag>/; returns {name: sha256}."""
    if not templates.valid_build(tag):
        raise ValueError(f"{tag!r} is not a llama.cpp release tag such as b9014")
    for name in names:
        if not templates.valid_template_name(name):
            raise ValueError(f"{name!r} is not a template file name such as Qwen-Qwen3-0.6B.jinja")
    directory = Path(root) / f"upstream-{tag}"
    directory.mkdir(parents=True, exist_ok=True)
    license_path = directory / templates.LICENSE_NAME
    if not license_path.exists():
        license_path.write_bytes(fetch(RAW_URL.format(tag=tag, path="LICENSE")))
    manifest_path = directory / templates.MANIFEST_NAME
    manifest = (templates.parse_manifest(manifest_path.read_bytes().decode("utf-8"))
                if manifest_path.exists() else {})
    fetched: dict[str, str] = {}
    for name in names:
        content = fetch(RAW_URL.format(tag=tag, path=f"models/templates/{name}"))
        (directory / name).write_bytes(content)
        fetched[name] = hashlib.sha256(content).hexdigest()
    manifest.update(fetched)
    manifest_path.write_bytes(templates.render_manifest(manifest).encode("utf-8"))
    return fetched


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, help="llama.cpp release tag, for example b9014")
    parser.add_argument("--root", type=Path, default=templates.root_for(ROOT_DIR),
                        help="chat-templates directory to write (default: the repository's)")
    parser.add_argument("names", nargs="+", metavar="NAME.jinja", help="file names under models/templates/")
    args = parser.parse_args(argv)
    fetched = vendor(args.root, args.tag, args.names)
    for name, digest in fetched.items():
        print(f"{digest}  upstream-{args.tag}/{name}")
    print("Add the index.json entry that uses each file (fileSha256 above), "
          "then run scripts/validate-chat-templates.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
