#!/usr/bin/env python3
"""Windows local installs must not ship the hybrid LiteLLM switchboard.

config/litellm/switchboard.yaml is the hybrid render (tests/test-cloud-model-ids.py
keeps its cloud route). Linux and macOS replace it with the renderer's output
for the install's mode. The Windows installer has no Python when it writes the
config, so env-generator.ps1 strips the hybrid-only routes with a regex. These
checks run that regex against the checked-in template and require the result
to match the renderer's local output.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render-runtime-configs.py"
ENV_GENERATOR = ROOT / "installers" / "windows" / "lib" / "env-generator.ps1"
TEMPLATE = ROOT / "config" / "litellm" / "switchboard.yaml"


def render_switchboard(ods_mode: str) -> str:
    proc = subprocess.run(
        [
            sys.executable, str(RENDERER),
            "--surface", "litellm-switchboard",
            "--switchboard-mode", "enabled",
            "--ods-mode", ods_mode,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    files = json.loads(proc.stdout)["files"]
    assert len(files) == 1, [f["surface"] for f in files]
    return files[0]["content"]


def windows_route_pattern() -> str:
    text = ENV_GENERATOR.read_text(encoding="utf-8")
    match = re.search(r"\$hybridOnlyRoutePattern = '([^']+)'", text)
    assert match, "env-generator.ps1 no longer defines $hybridOnlyRoutePattern"
    return match.group(1)


def strip_hybrid_routes(content: str) -> str:
    # The pattern is a single-quoted PowerShell literal; .NET and Python agree
    # on every construct it uses.
    return re.sub(windows_route_pattern(), "", content)


def non_blank_lines(content: str) -> list[str]:
    return [line for line in content.splitlines() if line.strip()]


def test_checked_in_template_is_still_the_hybrid_render() -> None:
    # If this fails, the template changed shape and the Windows filter needs
    # another look before the comparison below means anything.
    assert non_blank_lines(TEMPLATE.read_text(encoding="utf-8")) == non_blank_lines(
        render_switchboard("hybrid")
    )


def test_windows_filter_yields_the_local_render() -> None:
    filtered = strip_hybrid_routes(TEMPLATE.read_text(encoding="utf-8"))
    assert non_blank_lines(filtered) == non_blank_lines(render_switchboard("local"))
    for provider_route in ("cloud", "minimax", "minimax-fast"):
        assert f"model_name: {provider_route}\n" not in filtered, provider_route


def test_windows_filter_handles_crlf_checkouts() -> None:
    template = TEMPLATE.read_text(encoding="utf-8").replace("\n", "\r\n")
    filtered = strip_hybrid_routes(template)
    assert non_blank_lines(filtered) == non_blank_lines(render_switchboard("local"))


def test_new_ods_env_filters_only_local_installs() -> None:
    text = ENV_GENERATOR.read_text(encoding="utf-8")
    body = text.split("function New-ODSEnv {", 1)[1].split("\nfunction ", 1)[0]
    assert '$effectiveODSMode -notin @("cloud", "hybrid")' in body
    assert "Remove-SwitchboardHybridRoutes -Content $switchboard" in body
    assert '"switchboard.yaml"' in body


def main() -> int:
    for test in (
        test_checked_in_template_is_still_the_hybrid_render,
        test_windows_filter_yields_the_local_render,
        test_windows_filter_handles_crlf_checkouts,
        test_new_ods_env_filters_only_local_installs,
    ):
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
