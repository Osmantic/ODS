#!/usr/bin/env python3
"""Check tracked Markdown file links without network access or runtime mutation.

This deliberately checks file existence, not HTTP reachability or heading anchors.
Known missing links are accepted only for the exact normalized document content
and target list recorded in the reviewed baseline. New or changed debt fails.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote


INLINE_LINK = re.compile(r"\[[^\]]*\]\((<[^>]+>|[^)]+)\)")
REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)")
SCHEME = re.compile(r"^[a-zA-Z][\w+.-]*:")


def targets(text):
    """Yield line and target, excluding fenced and indented code examples."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
            continue
        if fence is not None or line.startswith(("    ", "\t")):
            continue
        for match in INLINE_LINK.finditer(line):
            yield number, match.group(1)
        reference = REFERENCE_LINK.match(line)
        if reference:
            yield number, reference.group(1)


def local_target(raw):
    value = raw.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    else:
        value = value.split(' "', 1)[0].split(" '", 1)[0]
    if not value or value.startswith(("#", "/")) or SCHEME.match(value):
        return None
    return unquote(value.split("#", 1)[0].split("?", 1)[0]) or None


def document_missing(root, name, text):
    missing = []
    source = root / name
    for number, raw in targets(text):
        target = local_target(raw)
        if target is None:
            continue
        resolved = (source.parent / target).resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
            missing.append((number, target))
    return missing


def audit(root, names, baseline):
    failures = []
    known = []
    documents = 0
    for name in sorted(set(names)):
        if not name.endswith(".md"):
            continue
        documents += 1
        text = (root / name).read_text(encoding="utf-8")
        missing = document_missing(root, name, text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        entry = baseline.get(name, {})
        pinned = entry.get("normalized_sha256") == digest
        allowed = set(entry.get("targets", [])) if pinned else set()
        for line, target in missing:
            item = f"{name}:{line} -> {target}"
            if target in allowed:
                known.append(item)
            else:
                failures.append(item)
    return documents, failures, known


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    baseline_path = args.baseline or root / ".github/doc-link-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["documents"]
    raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    names = [name.decode("utf-8") for name in raw.split(b"\0") if name]
    count, failures, known = audit(root, names, baseline)
    print(f"Markdown documents: {count}; new/changed broken links: {len(failures)}; known missing links: {len(known)}")
    if known:
        print("Known debt is not a clean repository-wide link result; see .github/doc-link-baseline.json and ods/docs/DOCUMENTATION-HYGIENE.md.")
    for item in failures:
        print(f"ERROR {item}")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
