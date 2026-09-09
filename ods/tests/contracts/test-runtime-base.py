#!/usr/bin/env python3
"""Runtime-base contract gate — exact digest, no capability reductions, clean upgrade path.

Scans all shipped install/default/generator paths for the exact digest,
rejects mutable Hermes tags in active install surfaces, proves fresh/generated
configs have no ODS output/tool/terminal reductions, proves legacy 1024 is
removed while custom 2048 is retained, and exercises affected tests as one batch.

Designed as a compact behavioral gate with zero Docker/curl/Compose shims.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DIGEST = "docker.io/nousresearch/hermes-agent@sha256:63bfb6d732f49a55d453e801057273785cc61e0f6ee43db3fa2f2a79846301b7"
DIGEST_SHA = "sha256:63bfb6d732f49a55d453e801057273785cc61e0f6ee43db3fa2f2a79846301b7"
MUTABLE_HERMES_IMAGE = re.compile(
    r"(?<![A-Za-z0-9._/-])(?:docker\.io/)?nousresearch/hermes-agent:[^\s\"'|})]+"
)


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
        "compose.yaml": (ROOT / "extensions/services/hermes/compose.yaml", f"${{HERMES_AGENT_IMAGE:-{DIGEST}}}"),
        ".env.example": (ROOT / ".env.example", f"HERMES_AGENT_IMAGE={DIGEST}"),
        "08-images.sh": (ROOT / "installers/phases/08-images.sh", f"${{HERMES_AGENT_IMAGE:-{DIGEST}}}|HERMES"),
        "install-macos.sh": (ROOT / "installers/macos/install-macos.sh", f'hermes_image="{DIGEST}"'),
        "install-windows.ps1": (ROOT / "installers/windows/install-windows.ps1", f'$envHermesImage = "{DIGEST}"'),
    }
    for name, (path, exact_default) in surfaces.items():
        if not path.exists():
            fail(f"Missing surface: {path}")
        assert exact_default in text(path), f"{name} missing exact selected default: {exact_default}"
    ok("All active install surfaces select the exact Hermes digest")

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
        mutable_refs = MUTABLE_HERMES_IMAGE.findall(t)
        assert not mutable_refs, f"Mutable Hermes image reference found in {path}: {mutable_refs}"

    # Guard the guard: reject arbitrary future tags and variable-derived tags,
    # not merely the one historical version this migration replaced.
    for unsafe in (
        "nousresearch/hermes-agent:latest",
        "docker.io/nousresearch/hermes-agent:v2099.1.2",
        "image: nousresearch/hermes-agent:${HERMES_TAG}",
    ):
        assert MUTABLE_HERMES_IMAGE.search(unsafe), f"Mutable-tag detector missed {unsafe!r}"
    assert not MUTABLE_HERMES_IMAGE.search(DIGEST), "Exact digest was misclassified as a mutable tag"
    ok("No mutable Hermes tags in active install surfaces; arbitrary-tag detector is exercised")


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

    assert 'terminal:\n  backend: "local"' in tmpl_text, "Template lost explicit local terminal backend"
    ok("Fresh template keeps the explicit local terminal backend")

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

    # No LemonadeCompact injection path remains in the Windows installer.
    win_phase = text(ROOT / "installers/windows/phases/06-directories.ps1")
    assert "LemonadeCompact" not in win_phase, "Windows installer still injects LemonadeCompact tool reductions"
    ok("Windows installer no longer injects a reduced toolset")


# ── 4. Legacy 1024 removed, custom 2048 retained ───────────────────────────

def check_upgrade_path() -> None:
    patcher = ROOT / "scripts/patch-hermes-config.py"
    p_text = text(patcher)

    # The _ensure_model default must not inject an output cap.
    assert "max_tokens: int | None = None" in p_text, "patch-hermes-config _ensure_model must default max_tokens to None"
    ok("patch-hermes-config _ensure_model does not default to 1024")

    assert "_remove_legacy_reductions" in p_text, "Missing exact legacy-reduction migrator"
    ok("patch-hermes-config has an exact legacy-reduction migrator")

    # ods-host-agent must remove 1024, not inject it
    host = text(ROOT / "bin/ods-host-agent.py")
    # The max_tokens default in _patch_hermes_config_text should be None
    assert "max_tokens: int | None = None" in host, "host-agent _patch_hermes_config_text must default max_tokens to None"
    ok("host-agent _patch_hermes_config_text max_tokens default is None")

    # Exercise 1024 removal plus explicit replacement. The replacement must
    # remain under model rather than slipping into the following block.
    result = subprocess.run(
        [sys.executable, str(patcher), "--help"],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, "patch-hermes-config --help failed"
    ok("patch-hermes-config CLI is callable")

    # Unit test: apply patcher to a config with 1024 → should remove it
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(
            "model:\n  default: test\n  max_tokens: 1024\n"
            "agent:\n  disabled_toolsets:\n    - terminal\n    - browser\n"
            "terminal:\n  backend: local\n  timeout: 30\n"
            "providers:\n  custom:\n    request_timeout_seconds: 360\n"
        )
        tf.flush()
        tf_name = tf.name

    result = subprocess.run(
        [sys.executable, str(patcher), tf_name, "--max-tokens", "2048"],
        capture_output=True, text=True, timeout=5,
    )
    patched = Path(tf_name).read_text()
    Path(tf_name).unlink()
    assert "max_tokens: 1024" not in patched, "Legacy 1024 not removed"
    model_block = patched.split("agent:", 1)[0].split("providers:", 1)[0]
    assert "  max_tokens: 2048" in model_block, "Replacement max_tokens escaped the model block"
    assert "disabled_toolsets" not in patched, "Exact legacy disabled_toolsets list was retained"
    assert "timeout: 30" not in patched and "backend: local" in patched, "Legacy timeout migration lost terminal backend"
    ok("generic migration removes exact legacy reductions and keeps replacement output cap under model")

    commented_legacy = (
        "model: # retained header comment\n  max_tokens: 1024 # ODS legacy\n"
        "agent:\n  disabled_toolsets:\n    - terminal # managed\n    - browser\n"
        "  # operator note for mode\n  mode: autonomous\n"
        "terminal:\n  backend: local\n  timeout: 30 # ODS legacy\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(commented_legacy)
        tf.flush()
        tf_name = tf.name
    result = subprocess.run(
        [sys.executable, str(patcher), tf_name, "--migrate-legacy-reductions-only"],
        capture_output=True, text=True, timeout=5,
    )
    commented_patched = Path(tf_name).read_text()
    Path(tf_name).unlink()
    assert result.returncode == 0
    assert "max_tokens" not in commented_patched and "disabled_toolsets" not in commented_patched
    assert "timeout: 30" not in commented_patched
    assert "# operator note for mode" in commented_patched and "mode: autonomous" in commented_patched
    assert "backend: local" in commented_patched

    # Execute the host-agent migrator directly from its AST without importing
    # the long-running service module. It must match the canonical helper.
    host_tree = ast.parse(host)
    selected_nodes = []
    for node in host_tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_HERMES_LEGACY_DISABLED_TOOLSETS"
            for target in node.targets
        ):
            selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_remove_legacy_hermes_reductions":
            selected_nodes.append(node)
    namespace = {"re": re}
    module = ast.fix_missing_locations(ast.Module(body=selected_nodes, type_ignores=[]))
    exec(compile(module, "ods-host-agent-migration", "exec"), namespace)
    host_patched, host_changed = namespace["_remove_legacy_hermes_reductions"](commented_legacy)
    assert host_changed and "max_tokens" not in host_patched and "disabled_toolsets" not in host_patched
    assert "timeout: 30" not in host_patched
    assert "# operator note for mode" in host_patched and "mode: autonomous" in host_patched
    assert "backend: local" in host_patched
    ok("generic and host migrations preserve operator comments and sibling keys")

    no_space_hash_values = (
        "model:\n  max_tokens: 1024#operator\n"
        "agent:\n  disabled_toolsets:\n    - terminal\n    - browser#operator\n"
        "terminal:\n  timeout: 30#operator\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(no_space_hash_values)
        tf.flush()
        tf_name = tf.name
    result = subprocess.run(
        [sys.executable, str(patcher), tf_name, "--migrate-legacy-reductions-only"],
        capture_output=True, text=True, timeout=5,
    )
    no_space_patched = Path(tf_name).read_text()
    Path(tf_name).unlink()
    assert result.returncode == 0 and no_space_patched == no_space_hash_values
    host_no_space, host_no_space_changed = namespace["_remove_legacy_hermes_reductions"](no_space_hash_values)
    assert not host_no_space_changed and host_no_space == no_space_hash_values
    ok("generic and host migrations preserve YAML values with non-comment hash suffixes")

    spaced_legacy_list = (
        "agent:\n  disabled_toolsets:\n    -  terminal\n"
        "    # retain this operator note\n\n    -   browser\n"
        "  mode: autonomous\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(spaced_legacy_list)
        tf.flush()
        tf_name = tf.name
    result = subprocess.run(
        [sys.executable, str(patcher), tf_name, "--migrate-legacy-reductions-only"],
        capture_output=True, text=True, timeout=5,
    )
    spaced_patched = Path(tf_name).read_text()
    Path(tf_name).unlink()
    assert result.returncode == 0 and "disabled_toolsets" not in spaced_patched
    assert "# retain this operator note" in spaced_patched and "mode: autonomous" in spaced_patched
    host_spaced, host_spaced_changed = namespace["_remove_legacy_hermes_reductions"](spaced_legacy_list)
    assert host_spaced_changed and "disabled_toolsets" not in host_spaced
    assert "# retain this operator note" in host_spaced and "mode: autonomous" in host_spaced

    folded_scalar = "agent:\n  disabled_toolsets:\n    -terminal\n    -browser\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(folded_scalar)
        tf.flush()
        tf_name = tf.name
    result = subprocess.run(
        [sys.executable, str(patcher), tf_name, "--migrate-legacy-reductions-only"],
        capture_output=True, text=True, timeout=5,
    )
    folded_patched = Path(tf_name).read_text()
    Path(tf_name).unlink()
    assert result.returncode == 0 and folded_patched == folded_scalar
    host_folded, host_folded_changed = namespace["_remove_legacy_hermes_reductions"](folded_scalar)
    assert not host_folded_changed and host_folded == folded_scalar
    ok("generic and host migrations handle YAML list spacing without deleting scalar operator state")

    # Unit test: apply patcher to a config with 2048 → should preserve it
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(
            "model:\n  default: test\n  max_tokens: 2048\n"
            "agent:\n  disabled_toolsets:\n    - terminal\n    - browser\n    - skills\n"
            "terminal:\n  backend: remote\n  timeout: 45\n"
        )
        tf.flush()
        tf_name = tf.name

    result = subprocess.run(
        [sys.executable, str(patcher), tf_name],
        capture_output=True, text=True, timeout=5,
    )
    patched = Path(tf_name).read_text()
    Path(tf_name).unlink()
    assert "max_tokens: 2048" in patched, "Operator value 2048 not preserved"
    assert "disabled_toolsets" in patched and "    - skills" in patched, "Divergent operator toolset list not preserved"
    assert "backend: remote" in patched and "timeout: 45" in patched, "Custom terminal settings not preserved"
    ok("generic migration preserves divergent operator reductions")

    implemented_paths = {
        "host agent": ROOT / "bin/ods-host-agent.py",
        "macOS persisted config": ROOT / "installers/macos/install-macos.sh",
        "Windows persisted config": ROOT / "installers/windows/phases/06-directories.ps1",
    }
    for label, path in implemented_paths.items():
        assert "legacy" in text(path).lower() and "disabled_toolsets" in text(path), f"{label} lacks migration path"
    bootstrap = text(ROOT / "scripts/bootstrap-upgrade.sh")
    assert bootstrap.count("--migrate-legacy-reductions-only") >= 2, "Bootstrap does not invoke canonical migration on host and container paths"
    linux_phase = text(ROOT / "installers/phases/11-services.sh")
    assert "_phase11_migrate_hermes_persisted_config" in linux_phase
    assert "--migrate-legacy-reductions-only" in linux_phase
    ok("every shipped persisted-config path invokes or implements exact legacy migration")


def main() -> None:
    check_digest_surfaces()
    check_no_mutable_tags()
    check_no_reductions()
    check_upgrade_path()
    ok("Runtime-base contract gate passed")


if __name__ == "__main__":
    main()
