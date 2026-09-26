"""The shared compose policy is one rule set, run by both extension validators.

dashboard-api (routers/extensions.py:_scan_compose_content) decides at install
and enable time; scripts/resolve-compose-stack.sh (_scan_user_compose_content)
decides on every `ods` command. The resolver is a self-contained Bash script
(tests and installers copy it alone), so the policy is a delimited block kept
byte-identical in both files instead of an import. The rule fixtures live in
test_accelerator_policy_fixtures.py; this file guards the sharing itself and
ODS's own compose files.
"""

from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import extensions


ODS = Path(__file__).resolve().parents[4]
RESOLVER = ODS / "scripts/resolve-compose-stack.sh"
DASHBOARD = ODS / "extensions/services/dashboard-api/routers/extensions.py"
BEGIN = "# >>> shared compose policy >>>"
END = "# <<< shared compose policy <<<"


def _block(path):
    text = path.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1, path
    return text[text.index(BEGIN):text.index(END) + len(END)]


def test_shared_policy_block_is_identical_in_both_validators():
    assert _block(RESOLVER) == _block(DASHBOARD)


def test_resolver_scan_region_holds_the_shared_policy():
    """Tests exec this region of the resolver; the policy must be inside it."""
    source = RESOLVER.read_text(encoding="utf-8")
    start = source.index("_LOOPBACK_VAR_DEFAULT_RE = re.compile(")
    end = source.index("def _extension_base_path(", start)
    assert start < source.index(BEGIN) < source.index(END) < end


def test_both_validators_call_the_shared_rules():
    for path in (RESOLVER, DASHBOARD):
        text = path.read_text(encoding="utf-8")
        outside = text.replace(_block(path), "")
        for name in ("_compose_policy_load(", "_compose_policy_document_problems(",
                     "_compose_policy_service_problems(", "_compose_policy_library_origin("):
            assert name in outside, (path.name, name)


# Built-in extensions (EXTENSIONS_DIR) whose compose file passed the
# enable/activate scan before the shared policy. Each must keep passing; the
# others were already refused for reasons this policy does not touch (local
# builds, extra_hosts, a 0.0.0.0 port, tailscale's NET_ADMIN).
BUILTIN_ENABLE_SCAN_PASSES = [
    "comfyui/compose.yaml",
    "embeddings/compose.yaml",
    "fooocus/compose.yaml.disabled",
    "hermes-proxy/compose.yaml",
    "langfuse/compose.yaml.disabled",
    "litellm/compose.yaml",
    "n8n/compose.yaml",
    "qdrant/compose.yaml",
    "searxng/compose.yaml",
    "tts/compose.yaml",
    "whisper/compose.yaml",
]


@pytest.mark.parametrize("relative", BUILTIN_ENABLE_SCAN_PASSES)
def test_builtin_extension_still_passes_the_enable_scan(relative, monkeypatch):
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", ODS / "extensions/services")
    extensions._scan_compose_content(
        ODS / "extensions/services" / relative,
        skip_name_collision=True, skip_gpu_passthrough_check=True,
        skip_root_user_check=True, builtin=True)


def test_interpolated_volume_source_is_a_builtin_only_allowance():
    """litellm picks its config file from the owner's ODS_MODE. The same shape
    in a user or library extension chooses a host path at render time."""
    litellm = ODS / "extensions/services/litellm/compose.yaml"
    with pytest.raises(HTTPException) as rejected:
        extensions._scan_compose_content(litellm, skip_name_collision=True,
                                         skip_gpu_passthrough_check=True, skip_root_user_check=True)
    assert "interpolation" in rejected.value.detail
