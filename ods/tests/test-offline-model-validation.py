#!/usr/bin/env python3
"""Offline model readiness contracts.

Run: python3 tests/test-offline-model-validation.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALIDATE_PY = ROOT / "scripts" / "validate-models.py"
CHECK_SH = ROOT / "scripts" / "check-offline-models.sh"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")
        if detail:
            print(f"       {detail}")


@contextlib.contextmanager
def temp_root():
    path = Path(tempfile.mkdtemp())
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_models", VALIDATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def place_hf_model(root: Path, cache: str, repo_id: str, layout: str = "hf-cache") -> Path:
    if layout == "hf-cache":
        target = (
            root
            / "data"
            / cache
            / f"models--{repo_id.replace('/', '--')}"
            / "snapshots"
            / "abc"
        )
    else:
        target = root / "data" / cache / repo_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "weights.bin").write_bytes(b"ready")
    return target


def build_ready_root(
    root: Path,
    *,
    layout: str = "hf-cache",
    stt_model: str = "Systran/faster-whisper-base",
    embedding_model: str = "BAAI/bge-base-en-v1.5",
) -> Path:
    (root / "data" / "models").mkdir(parents=True)
    (root / "data" / "models" / "model.gguf").write_bytes(b"gguf")
    place_hf_model(root, "whisper", stt_model, layout)
    place_hf_model(root, "embeddings", embedding_model, layout)
    (root / ".env").write_text(
        "\n".join(
            (
                "ODS_MODE=local",
                "LLM_BACKEND=llama-server",
                "GGUF_FILE=model.gguf",
                f"AUDIO_STT_MODEL={stt_model}",
                f"EMBEDDING_MODEL={embedding_model}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return root


def run_validator(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, ODS_ROOT=str(root), PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(VALIDATE_PY), *args],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )


def run_shell_checker(root: Path) -> subprocess.CompletedProcess[str]:
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / CHECK_SH.name).write_bytes(CHECK_SH.read_bytes())
    (scripts_dir / VALIDATE_PY.name).write_bytes(VALIDATE_PY.read_bytes())
    return subprocess.run(
        ["bash", CHECK_SH.name],
        cwd=str(scripts_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def output(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def assert_both(root: Path, expected: int, label: str) -> None:
    python_result = run_validator(root)
    shell_result = run_shell_checker(root)
    check(
        f"python validator {label}",
        python_result.returncode == expected,
        output(python_result),
    )
    check(
        f"shell entry point {label}",
        shell_result.returncode == expected,
        output(shell_result),
    )
    check(
        f"validators agree {label}",
        python_result.returncode == shell_result.returncode,
        f"python:\n{output(python_result)}\nshell:\n{output(shell_result)}",
    )


def main() -> int:
    validator = load_validator()

    candidates = [
        path.name
        for path in validator.model_candidates(
            Path("/x"), "Systran/faster-whisper-base"
        )
    ]
    check(
        "Hugging Face cache directory is supported",
        "models--Systran--faster-whisper-base" in candidates,
        repr(candidates),
    )
    check(
        "manual bare directory is supported",
        "faster-whisper-base" in candidates,
        repr(candidates),
    )

    with temp_root() as root:
        (root / ".env").write_text(
            'AUDIO_STT_MODEL="org/quoted-model"\r\nEMPTY=\r\n',
            encoding="utf-8",
        )
        check(
            "matched quotes and CRLF are normalized",
            validator.env_value(root, "AUDIO_STT_MODEL", "fallback")
            == "org/quoted-model",
        )
        check(
            "empty values use the caller default",
            validator.env_value(root, "EMPTY", "fallback") == "fallback",
        )

    for layout, stt, label in (
        ("hf-cache", "Systran/faster-whisper-base", "default HF cache layout"),
        (
            "hf-cache",
            "deepdml/faster-whisper-large-v3-turbo-ct2",
            "NVIDIA STT pin",
        ),
        ("bare", "Systran/faster-whisper-base", "manual bare layout"),
    ):
        with temp_root() as root:
            build_ready_root(root, layout=layout, stt_model=stt)
            assert_both(root, 0, f"accept {label}")

    with temp_root() as root:
        build_ready_root(root)
        # A finalized small model is valid. Integrity and catalog hashes belong
        # to the downloader; this readiness check must not reject small custom
        # GGUF, STT, or embedding models using guessed size floors.
        check(
            "small finalized GGUF is accepted",
            run_validator(root).returncode == 0,
            output(run_validator(root)),
        )
        check(
            "Kokoro host voice file is not required",
            not (root / "data" / "kokoro" / "voices" / "af_heart.pt").exists()
            and run_validator(root).returncode == 0,
        )
        json_result = run_validator(root, "--json")
        json_report = json.loads(json_result.stdout)
        check(
            "JSON readiness report describes every model check",
            json_result.returncode == 0
            and json_report["ready"] is True
            and len(json_report["models"]) == 4,
            output(json_result),
        )

    with temp_root() as root:
        build_ready_root(root)
        configured = root / "data" / "models" / "model.gguf"
        configured.unlink()
        (root / "data" / "models" / "old.gguf").write_bytes(b"old")
        assert_both(root, 1, "reject missing configured GGUF despite an old model")

    with temp_root() as root:
        build_ready_root(root)
        (root / "data" / "models" / "model.gguf").write_bytes(b"")
        assert_both(root, 1, "reject empty finalized GGUF")

    with temp_root() as root:
        build_ready_root(root)
        whisper = (
            root
            / "data"
            / "whisper"
            / "models--Systran--faster-whisper-base"
            / "snapshots"
            / "abc"
            / "weights.bin"
        )
        whisper.unlink()
        (whisper.parent / "weights.bin.incomplete").write_bytes(b"partial")
        assert_both(root, 1, "reject an incomplete Hugging Face snapshot")

    with temp_root() as root:
        build_ready_root(root)
        (root / ".env").write_text(
            "ODS_MODE=local\nLLM_BACKEND=llama-server\nGGUF_FILE=model.gguf\n"
            "AUDIO_STT_MODEL=org/../../outside\n"
            "EMBEDDING_MODEL=BAAI/bge-base-en-v1.5\n",
            encoding="utf-8",
        )
        assert_both(root, 1, "reject unsafe Hugging Face repository ids")

    with temp_root() as root:
        (root / "data" / "models").mkdir(parents=True)
        (root / "data" / "models" / "model.gguf").write_bytes(b"gguf")
        (root / ".env").write_text(
            "ODS_MODE=local\nLLM_BACKEND=llama-server\nGGUF_FILE=model.gguf\n",
            encoding="utf-8",
        )
        (root / ".compose-flags").write_text(
            "-f docker-compose.base.yml "
            "-f extensions/services/whisper/compose.yaml.disabled "
            "-f docker-compose.cpu.yml\n",
            encoding="utf-8",
        )
        result = run_validator(root)
        assert_both(root, 0, "skip model caches for disabled services")
        check(
            "disabled services are reported as skipped",
            output(result).count("Skipped: service is not active") == 3,
            output(result),
        )

    for mode, backend in (("cloud", "litellm"), ("lemonade", "lemonade")):
        with temp_root() as root:
            (root / ".env").write_text(
                f"ODS_MODE={mode}\nLLM_BACKEND={backend}\n"
                "ENABLE_VOICE=false\nENABLE_EMBEDDINGS=false\nENABLE_RAG=false\n",
                encoding="utf-8",
            )
            result = run_validator(root)
            assert_both(root, 0, f"skip host GGUF for {mode}")
            check(
                f"{mode} skip reason is explicit",
                "[SKIP" in output(result),
                output(result),
            )

    with temp_root() as root:
        (root / ".env").write_text("ODS_MODE=local\n", encoding="utf-8")
        result = run_validator(root)
        check(
            "empty install reports missing artifacts",
            result.returncode == 1 and "MISSING OR INCOMPLETE" in output(result),
            output(result),
        )
        check(
            "remediation uses the resumable installer",
            validator.installer_command() in output(result)
            and "pre-download.sh" not in output(result),
            output(result),
        )

    check(
        "Windows remediation uses the PowerShell installer",
        validator.installer_command("nt") == r".\install.ps1",
        validator.installer_command("nt"),
    )
    check(
        "Unix remediation uses the shell installer",
        validator.installer_command("posix") == "./install.sh",
        validator.installer_command("posix"),
    )

    print()
    print(f"Passed: {PASS}  Failed: {FAIL}")
    if FAIL:
        return 1
    print("[PASS] offline model validation contracts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
