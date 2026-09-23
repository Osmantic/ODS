#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[[ $# -le 1 ]] || { echo "Usage: scripts/check-no-secrets.sh [PATH]" >&2; exit 2; }
TARGET=${1:-$ROOT}
if python3 -c 'raise SystemExit(0)' >/dev/null 2>&1; then PYTHON=python3
elif python -c 'raise SystemExit(0)' >/dev/null 2>&1; then PYTHON=python
else echo "Python 3 is required." >&2; exit 1
fi
"$PYTHON" - "$TARGET" <<'PY'
import pathlib, re, subprocess, sys

root = pathlib.Path(sys.argv[1]).resolve()
if not root.is_dir():
    raise SystemExit(f"Secret policy target is not a directory: {root}")
try:
    top = pathlib.Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=root, stderr=subprocess.DEVNULL
    ).decode().strip()).resolve()
    if top != root:
        raise subprocess.CalledProcessError(1, "git rev-parse")
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        stderr=subprocess.DEVNULL,
    ).decode("utf-8", "surrogateescape").split("\0")
except (subprocess.CalledProcessError, FileNotFoundError):
    excluded_dirs = {".git", ".generated", ".runtime", ".secrets", "secrets", "dist", "node_modules", "__pycache__"}
    names = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in excluded_dirs for part in relative.parts):
            continue
        if relative.name == ".env" or relative.name.startswith(".env.") and relative.name != ".env.example":
            continue
        names.append(str(relative))
findings = []
patterns = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Google refresh token", re.compile(r'"refresh_token"\s*:\s*"(?!REDACTED|CHANGEME|example)[^"{]{8,}"', re.I)),
    ("OAuth client secret", re.compile(r'"client_secret"\s*:\s*"(?!REDACTED|CHANGEME|example)[^"{]{8,}"', re.I)),
    ("Discord token", re.compile(r"\b(?:mfa\.[\w-]{40,}|[A-Za-z0-9_-]{20,30}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,40})\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{30,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,}\b")),
]
for name in filter(None, names):
    path = root / name
    if not path.is_file() or path.stat().st_size > 2_000_000:
        continue
    try: text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError: continue
    for label, pattern in patterns:
        if pattern.search(text): findings.append(f"{name}: possible {label}")
    for match in re.finditer(r"OPENCLAW_GATEWAY_TOKEN\s*=\s*([^\s#]+)", text):
        if match.group(1).strip('"\'').lower() not in {"redacted", "changeme", "example", "${openclaw_gateway_token}"}:
            findings.append(f"{name}: non-placeholder gateway token")
tracked_runtime = [name for name in names if name and (name == ".openclaw" or name.startswith(".openclaw/"))]
if tracked_runtime: findings.append("tracked .openclaw runtime state")
if findings:
    print("Secret policy check failed:", file=sys.stderr)
    for finding in sorted(set(findings)): print(f"- {finding}", file=sys.stderr)
    raise SystemExit(1)
print("Secret policy check passed.")
PY
