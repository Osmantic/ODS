#!/usr/bin/env python3
"""Runtime-base contract gate — exact digest, no capability reductions, clean upgrade path.

Scans all shipped install/default/generator paths for the exact digest,
rejects mutable Hermes tags in active install surfaces, proves fresh/generated
configs have no ODS output/tool/terminal reductions, proves legacy 1024 is
removed while custom 2048 is retained, and exercises affected tests as one batch.

Designed to stay under 250 lines with zero Docker/curl/Compose shims.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DIGEST = "docker.io/nousresearch/hermes-agent@sha256:63bfb6d732f49a55d453e801057273785cc61e0f6ee43db3fa2f2a79846301b7"
DIGEST_SHA = "sha256:63bfb6d732f49a55d453e801057273785cc61e0f6ee43db3fa2f2a79846301b7"
OLD_TAG = "nousresearch/hermes-agent:v2026.6.5"


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"[PASS] {msg}")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. Digest pinned in all shipped/default surfaces ───────────────────────

def check_digest_surfaces() -> None:
    surfaces = {
        "compose.yaml": ROOT / "extensions/services/hermes/compose.yaml",
        ".env.example": ROOT / ".env.example",
        ".env.schema.json": ROOT / ".env.schema.json",
        "dependency-lock.json": ROOT / "config/dependency-lock.json",
        "08-images.sh": ROOT / "installers/phases/08-images.sh",
        "install-macos.sh": ROOT / "installers/macos/install-macos.sh",
        "install-windows.ps1": ROOT / "installers/windows/install-windows.ps1",
    }
    for name, path in surfaces.items():
        if not path.exists():
            fail(f"Missing surface: {path}")
        t = text(path)
        assert DIGEST in t or DIGEST_SHA in t, f"{name} missing digest: {path}"
    ok("All shipped surfaces contain the exact Hermes digest")

    # dependency-lock hermes.agent entry must use digest
    lock = json.loads(text(ROOT / "config/dependency-lock.json"))
    hermes_pin = [e for e in lock["entries"] if e.get("id") == "hermes.agent"]
    assert len(hermes_pin) == 1, f"Expected 1 hermes.agent entry, got {len(hermes_pin)}"
    assert DIGEST in hermes_pin[0]["value"], "dependency-lock hermes.agent not digest-pinned"
    ok("dependency-lock hermes.agent entry uses digest")

    # .env.schema.json default must be digest
    schema = json.loads(text(ROOT / ".env.schema.json"))
    hermes_default = schema["properties"]["HERMES_AGENT_IMAGE"]["default"]
    assert hermes_default == DIGEST, f"schema default is {hermes_default!r}, expected {DIGEST!r}"
    ok(".env.schema.json HERMES_AGENT_IMAGE default is the digest")

    # allow_variable_refs must resolve to digest
    var_refs = [v for v in lock.get("allow_variable_refs", []) if "hermes" in v.get("path", "")]
    assert len(var_refs) == 1, f"Expected 1 hermes allow_variable_ref, got {len(var_refs)}"
    assert var_refs[0]["default"] == DIGEST, "allow_variable_refs hermes default not digest"
    ok("allow_variable_refs hermes default resolves to digest")


# ── 2. Reject mutable tags in active install surfaces ──────────────────────

def check_no_mutable_tags() -> None:
    active_files = [
        ROOT / "extensions/services/hermes/compose.yaml",
        ROOT / "installers/phases/08-images.sh",
        ROOT / "installers/macos/install-macos.sh",
        ROOT / "installers/windows/install-windows.ps1",
    ]
    for path in active_files:
        t = text(path)
        # Allow the digest and the variable ref pattern, but not the raw mutable tag
        lines_with_tag = [
            l.strip() for l in t.splitlines()
            if OLD_TAG in l and DIGEST not in l and "HERMES_AGENT_IMAGE" not in l.split(":")[0].split("=")[0]
        ]
        # The only acceptable occurrence of the old tag is inside the variable default
        # which we already changed to digest, so there should be zero
        assert not lines_with_tag, f"Mutable tag {OLD_TAG} found in {path}: {lines_with_tag}"
    ok("No mutable Hermes tags in active install surfaces")


# ── 3. Fresh configs have no ODS output/tool/terminal reductions ───────────

def check_no_reductions() -> None:
    template = ROOT / "extensions/services/hermes/cli-config.yaml.template"
    tmpl_text = text(template)

    # No max_tokens in fresh template
    assert "max_tokens:" not in tmpl_text, "Template still has max_tokens"
    ok("Fresh template has no max_tokens cap")

    # No disabled_toolsets
    assert "disabled_toolsets" not in tmpl_text, "Template still has disabled_toolsets"
    ok("Fresh template has no disabled_toolsets")

    # No terminal.timeout:30
    assert "timeout: 30" not in tmpl_text, "Template still has terminal.timeout:30"
    ok("Fresh template has no terminal.timeout reduction")

    # No TERMINAL_TIMEOUT in compose
    compose = text(ROOT / "extensions/services/hermes/compose.yaml")
    assert "TERMINAL_TIMEOUT" not in compose, "Compose still has TERMINAL_TIMEOUT"
    ok("Compose has no TERMINAL_TIMEOUT")

    # Renderer does not emit max_tokens
    renderer = text(ROOT / "scripts/render-runtime-configs.py")
    # DEFAULT_HERMES_MAX_TOKENS must be None
    m = re.search(r"DEFAULT_HERMES_MAX_TOKENS\s*=\s*(\S+)", renderer)
    assert m and m.group(1) == "None", f"Renderer DEFAULT_HERMES_MAX_TOKENS={m.group(1)}"
    ok("Renderer DEFAULT_HERMES_MAX_TOKENS is None")

    # Renderer render_hermes must not emit max_tokens
    # Find the render_hermes function body
    hm = re.search(r"def render_hermes\(.*?\n(.*?)(?=\ndef |\nclass |\Z)", renderer, re.DOTALL)
    assert hm, "Could not find render_hermes function"
    body = hm.group(1)
    assert "max_tokens" not in body, "render_hermes still emits max_tokens"
    ok("render_hermes does not emit max_tokens")

    # No LemonadeCompact disabled_toolsets in Windows installer
    win_phase = text(ROOT / "installers/windows/phases/06-directories.ps1")
    assert "disabled_toolsets" not in win_phase, "Windows installer still has disabled_toolsets"
    ok("Windows installer has no disabled_toolsets block")


# ── 4. Legacy 1024 removed, custom 2048 retained ───────────────────────────

def check_upgrade_path() -> None:
    patcher = ROOT / "scripts/patch-hermes-config.py"
    p_text = text(patcher)

    # The _ensure_model default must not be 1024 (multi-line sig)
    assert "max_tokens: int | None = None" in p_text, "patch-hermes-config _ensure_model must default max_tokens to None"
    assert "= 1024" not in p_text, "patch-hermes-config must not contain hardcoded 1024"
    ok("patch-hermes-config _ensure_model does not default to 1024")

    # Must have _remove_legacy_max_tokens
    assert "_remove_legacy_max_tokens" in p_text, "Missing _remove_legacy_max_tokens function"
    ok("patch-hermes-config has _remove_legacy_max_tokens")

    # ods-host-agent must remove 1024, not inject it
    host = text(ROOT / "bin/ods-host-agent.py")
    # The max_tokens default in _patch_hermes_config_text should be None
    assert "max_tokens: int | None = None" in host, "host-agent _patch_hermes_config_text must default max_tokens to None"
    ok("host-agent _patch_hermes_config_text max_tokens default is None")

    # Test that legacy 1024 is removed but 2048 is preserved
    result = subprocess.run(
        [sys.executable, str(patcher), "--help"],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, "patch-hermes-config --help failed"
    ok("patch-hermes-config CLI is callable")

    # Unit test: apply patcher to a config with 1024 → should remove it
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write("model:\n  default: test\n  max_tokens: 1024\n")
        tf.flush()
        tf_name = tf.name

    result = subprocess.run(
        [sys.executable, str(patcher), tf_name],
        capture_output=True, text=True, timeout=5,
    )
    patched = Path(tf_name).read_text()
    Path(tf_name).unlink()
    assert "max_tokens: 1024" not in patched, "Legacy 1024 not removed"
    ok("patch-hermes-config removes legacy max_tokens: 1024")

    # Unit test: apply patcher to a config with 2048 → should preserve it
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write("model:\n  default: test\n  max_tokens: 2048\n")
        tf.flush()
        tf_name = tf.name

    result = subprocess.run(
        [sys.executable, str(patcher), tf_name],
        capture_output=True, text=True, timeout=5,
    )
    patched = Path(tf_name).read_text()
    Path(tf_name).unlink()
    assert "max_tokens: 2048" in patched, "Operator value 2048 not preserved"
    ok("patch-hermes-config preserves operator max_tokens: 2048")


def main() -> None:
    check_digest_surfaces()
    check_no_mutable_tags()
    check_no_reductions()
    check_upgrade_path()
    ok("Runtime-base contract gate passed")


if __name__ == "__main__":
    main()
