"""Built-in Perplexica's host bridge must pass the installed enable scan."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import extensions


ODS = Path(__file__).resolve().parents[4]
PERPLEXICA = ODS / "extensions/services/perplexica/compose.yaml"


def test_shipped_perplexica_enable_scan_accepts_host_gateway(monkeypatch):
    """Regress the HTTP 400 seen in an installed gateway-only add-back."""
    ext_dir = PERPLEXICA.parent
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", ext_dir.parent)
    extensions._scan_installed_compose(
        "perplexica", ext_dir, PERPLEXICA, is_builtin=True,
    )


@pytest.mark.parametrize("extra_hosts", [
    '["host.docker.internal:host-gateway", "metadata:169.254.169.254"]',
    '["host.docker.internal:host-gateway", "host.docker.internal:host-gateway"]',
    '["metadata:host-gateway"]',
])
def test_builtin_enable_scan_rejects_other_host_mappings(tmp_path, monkeypatch, extra_hosts):
    root = tmp_path / "builtins"
    ext_dir = root / "perplexica"
    ext_dir.mkdir(parents=True)
    compose = ext_dir / "compose.yaml"
    compose.write_text(
        f"services:\n  perplexica:\n    image: test:latest\n    extra_hosts: {extra_hosts}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", root)
    with pytest.raises(HTTPException, match="extra_hosts"):
        extensions._scan_installed_compose("perplexica", ext_dir, compose, is_builtin=True)


def test_untrusted_extension_still_rejects_host_gateway(tmp_path):
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        'services:\n  custom:\n    image: test:latest\n'
        '    extra_hosts: ["host.docker.internal:host-gateway"]\n',
        encoding="utf-8",
    )
    with pytest.raises(HTTPException, match="extra_hosts"):
        extensions._scan_compose_content(compose)
