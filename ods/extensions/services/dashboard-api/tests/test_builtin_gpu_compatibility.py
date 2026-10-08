"""A Library built-in this host's GPU backend cannot run stays incompatible.

ComfyUI is Library-manageable but needs an AMD or NVIDIA backend. On a CPU
backend (Strixy's WSL side runs its model on Windows, so GPU_BACKEND=cpu) the
Compose resolver leaves ComfyUI out of the stack. The catalog offered Add anyway
and the start failed with "Host agent failed to start extension" (fleet run
2026-10-04). The status and the enable route must both say why instead.
"""

from unittest.mock import Mock

import pytest

from routers import extensions

COMFYUI = {"id": "comfyui", "name": "ComfyUI", "gpu_backends": ["amd", "nvidia"],
           "catalog_source": "builtin"}


@pytest.fixture
def comfyui(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled"
    (bundled / "comfyui").mkdir(parents=True)
    (bundled / "comfyui" / "compose.yaml.disabled").write_text(
        "services:\n  comfyui:\n    image: alpine:3.22\n")
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", bundled)
    monkeypatch.setattr(extensions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(extensions, "LIBRARY_MANAGEABLE_BUILTINS", frozenset({"comfyui"}))
    monkeypatch.setattr(extensions, "EXTENSION_CATALOG", [COMFYUI])
    monkeypatch.setattr(extensions, "_select_extensions_on_host",
                        Mock(side_effect=AssertionError("must not select an incompatible built-in")))
    return bundled


def test_cpu_backend_reports_comfyui_incompatible(monkeypatch, comfyui):
    monkeypatch.setattr(extensions, "GPU_BACKEND", "cpu")
    assert extensions._compute_extension_status(dict(COMFYUI), {}) == "incompatible"


def test_a_refused_attempt_does_not_leave_a_retry_card(monkeypatch, comfyui, tmp_path):
    monkeypatch.setattr(extensions, "GPU_BACKEND", "cpu")
    monkeypatch.setattr(extensions, "_read_progress",
                        lambda ext_id: {"status": "error", "error": "Host agent failed to start extension."})
    assert extensions._compute_extension_status(dict(COMFYUI), {}) == "incompatible"


def test_supported_backend_keeps_the_library_selection(monkeypatch, comfyui):
    monkeypatch.setattr(extensions, "GPU_BACKEND", "nvidia")
    assert extensions._compute_extension_status(dict(COMFYUI), {}) == "disabled"


def test_enable_refuses_an_incompatible_builtin_with_the_reason(test_client, monkeypatch, comfyui):
    monkeypatch.setattr(extensions, "GPU_BACKEND", "cpu")
    response = test_client.post("/api/extensions/comfyui/enable?auto_enable_deps=true",
                                headers=test_client.auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"] == ("ComfyUI needs one of these GPU backends: AMD, NVIDIA. "
                                         "It is not available on this hardware.")
    assert (comfyui / "comfyui" / "compose.yaml.disabled").exists()
