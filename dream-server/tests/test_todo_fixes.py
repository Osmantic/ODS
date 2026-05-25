#!/usr/bin/env python3
"""Regression tests for the Whisper VAD patcher embedded in the entrypoint."""

from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT_DIR / "extensions" / "services" / "whisper" / "docker-entrypoint.sh"


def load_vad_patcher() -> dict[str, object]:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    marker_start = "cat > /tmp/vad_patcher.py << 'PYTHON_EOF'"
    marker_end = "PYTHON_EOF"
    assert marker_start in source
    embedded = source.split(marker_start, 1)[1].split(marker_end, 1)[0].strip()
    patcher_source = embedded.split('if __name__ == "__main__":', 1)[0]
    namespace: dict[str, object] = {}
    exec(patcher_source, namespace)
    return namespace


def patch_source(source: str) -> tuple[str, bool]:
    patcher = load_vad_patcher()["patch_transcribe_call"]
    return patcher(source)  # type: ignore[no-any-return]


def test_vad_patch_single_line_transcribe_call() -> None:
    patched, modified = patch_source(
        """
def transcribe_audio(model, file):
    result = model.transcribe(file)
    return result
""".strip()
    )

    assert modified is True
    assert 'model.transcribe(file, vad_filter=True, vad_parameters={"threshold": 0.5})' in patched


def test_vad_patch_multiline_transcribe_call() -> None:
    patched, modified = patch_source(
        """
def transcribe_audio(model, file):
    result = model.transcribe(
        file,
        language="en"
    )
    return result
""".strip()
    )

    assert modified is True
    assert "vad_filter=True" in patched
    assert 'vad_parameters={"threshold": 0.5}' in patched


def test_vad_patch_is_idempotent_when_kwargs_already_exist() -> None:
    source = """
def transcribe_audio(model, file):
    return model.transcribe(file, vad_filter=True)
""".strip()

    patched, modified = patch_source(source)

    assert modified is False
    assert patched == source


def test_vad_patch_uses_rightmost_paren_for_nested_single_line_call() -> None:
    patched, modified = patch_source(
        """
def transcribe_audio(model, path):
    return model.transcribe(load_audio(path))
""".strip()
    )

    assert modified is True
    assert "load_audio(path))" not in patched
    assert 'load_audio(path), vad_filter=True, vad_parameters={"threshold": 0.5})' in patched


if __name__ == "__main__":
    tests = [
        test_vad_patch_single_line_transcribe_call,
        test_vad_patch_multiline_transcribe_call,
        test_vad_patch_is_idempotent_when_kwargs_already_exist,
        test_vad_patch_uses_rightmost_paren_for_nested_single_line_call,
    ]
    for test in tests:
        test()
    print("[PASS] Whisper VAD patch regressions")
