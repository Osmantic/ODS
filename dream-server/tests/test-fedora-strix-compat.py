#!/usr/bin/env python3
"""Fedora/Strix compatibility contracts.

These checks cover regressions reported by Fedora Workstation + Strix Halo users:
- Docker build args used in later FROM instructions must be globally scoped.
- Relative bind mounts need shared SELinux labels for enforcing Fedora/RHEL hosts.
- gpu_backends: [all] must be treated as a wildcard by dream-doctor.
- TOKEN_SPY_API_KEY must be wired to both Token Spy and dashboard-api.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise AssertionError(message)


def split_bind_spec(spec: str):
    if not spec.startswith("./"):
        return None
    sep = None
    for idx, ch in enumerate(spec):
        if ch == ":" and idx + 1 < len(spec) and spec[idx + 1] == "/":
            sep = idx
            break
    if sep is None:
        return None
    source = spec[:sep]
    rest = spec[sep + 1 :]
    if ":" in rest:
        target, options = rest.split(":", 1)
    else:
        target, options = rest, ""
    if not target.startswith("/"):
        return None
    return source, target, options


def compose_files() -> list[Path]:
    files = sorted(ROOT.glob("docker-compose*.yml")) + sorted(ROOT.glob("docker-compose*.yaml"))
    files += sorted((ROOT / "extensions/services").glob("*/compose*.yml"))
    files += sorted((ROOT / "extensions/services").glob("*/compose*.yaml"))
    return files


def test_amd_dockerfile_arg_scope() -> None:
    dockerfile = ROOT / "extensions/services/llama-server/Dockerfile.amd"
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    try:
        first_from = next(i for i, line in enumerate(lines, 1) if line.startswith("FROM "))
    except StopIteration as exc:
        raise AssertionError("Dockerfile.amd has no FROM instruction") from exc
    arg_lines = [i for i, line in enumerate(lines, 1) if line.startswith("ARG LEMONADE_SERVER_IMAGE")]
    if not arg_lines:
        fail("Dockerfile.amd must declare LEMONADE_SERVER_IMAGE")
    if min(arg_lines) > first_from:
        fail("LEMONADE_SERVER_IMAGE must be declared before the first FROM so Docker can use it in later FROM instructions")


def test_selinux_labels_on_relative_bind_mounts() -> None:
    missing: list[str] = []
    for path in compose_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("- ./"):
                continue
            spec = stripped[2:].split(" #", 1)[0].strip()
            parsed = split_bind_spec(spec)
            if not parsed:
                continue
            _source, _target, options = parsed
            opts = {opt.strip() for opt in options.split(",") if opt.strip()}
            if not ({"z", "Z"} & opts):
                rel = path.relative_to(ROOT)
                missing.append(f"{rel}:{lineno}: {stripped}")
    if missing:
        fail("Relative bind mounts need :z/:Z for SELinux enforcing hosts:\n" + "\n".join(missing))


def test_dream_doctor_all_backend_wildcard() -> None:
    text = (ROOT / "scripts/dream-doctor.sh").read_text(encoding="utf-8")
    if '" $gpu_backends " =~ " all "' not in text:
        fail("dream-doctor must treat gpu_backends: [all] as compatible with every non-Apple backend")


def test_token_spy_key_wiring() -> None:
    token_spy_compose = (ROOT / "extensions/services/token-spy/compose.yaml").read_text(encoding="utf-8")
    dashboard_compose = (ROOT / "docker-compose.base.yml").read_text(encoding="utf-8")
    phase06 = (ROOT / "installers/phases/06-directories.sh").read_text(encoding="utf-8")
    windows_env = (ROOT / "installers/windows/lib/env-generator.ps1").read_text(encoding="utf-8")
    macos_env = (ROOT / "installers/macos/lib/env-generator.sh").read_text(encoding="utf-8")
    schema = (ROOT / ".env.schema.json").read_text(encoding="utf-8")

    required = {
        "token-spy compose": "TOKEN_SPY_API_KEY=${TOKEN_SPY_API_KEY:-}" in token_spy_compose,
        "dashboard-api compose": "TOKEN_SPY_API_KEY=${TOKEN_SPY_API_KEY:-}" in dashboard_compose,
        "linux installer generation": "TOKEN_SPY_API_KEY=$(_env_get TOKEN_SPY_API_KEY" in phase06,
        "linux installer output": "TOKEN_SPY_API_KEY=${TOKEN_SPY_API_KEY}" in phase06,
        "windows installer generation": "Get-EnvOrNew \"TOKEN_SPY_API_KEY\"" in windows_env,
        "windows preserves token-spy key file": "token-spy-api-key.txt" in windows_env,
        "windows installer output": "TOKEN_SPY_API_KEY=$tokenSpyApiKey" in windows_env,
        "macos installer generation": "read_token_spy_api_key" in macos_env,
        "macos preserves token-spy key file": "token-spy-api-key.txt" in macos_env,
        "macos installer output": "TOKEN_SPY_API_KEY=${token_spy_api_key}" in macos_env,
        "env schema": '"TOKEN_SPY_API_KEY"' in schema,
    }
    missing = [name for name, ok in required.items() if not ok]
    if missing:
        fail("TOKEN_SPY_API_KEY is not wired through: " + ", ".join(missing))


def main() -> None:
    tests = [
        test_amd_dockerfile_arg_scope,
        test_selinux_labels_on_relative_bind_mounts,
        test_dream_doctor_all_backend_wildcard,
        test_token_spy_key_wiring,
    ]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")


if __name__ == "__main__":
    main()
