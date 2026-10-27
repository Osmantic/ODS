"""ods doctor's inference contract on a vLLM install (LLM_BACKEND=vllm).

The route keeps llama-server off and sends clients through LiteLLM to
model-router and vllm-server, so the doctor expects the LiteLLM gateway and
the docker-compose.vllm.yml overlay, flags an LLM_API_URL that still names
the in-stack llama-server, and no longer warns that local mode routes through
LiteLLM. The contract functions are compiled from scripts/ods-doctor.sh's
embedded Python without running the doctor.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import shlex
import sys
from urllib import error, parse, request

import pytest

DOCTOR = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ods-doctor.sh"
NAMES = {
    "_clean_env", "_read_text", "_parse_kv_lines", "_truthy", "_env_file_values", "_compose_files_from_flags",
    "_has_compose_file", "_looks_like_litellm_route", "_looks_like_local_llama_route",
    "_url_hostname", "_source", "_evidence", "_diagnosis", "_inference_issue",
    "_collect_inference_contract",
}


def _contract(root: pathlib.Path):
    text = DOCTOR.read_text(encoding="utf-8")
    start = text.index("<<'PY'\n") + len("<<'PY'\n")
    source = text[start:text.index("\nPY\n", start)]
    tree = ast.parse(source)
    module = ast.Module(body=[node for node in tree.body
                              if isinstance(node, ast.FunctionDef) and node.name in NAMES], type_ignores=[])
    namespace = {"root_dir": root, "os": os, "re": re, "json": json, "shlex": shlex, "sys": sys,
                 "parse": parse, "error": error, "request": request, "pathlib": pathlib}
    exec(compile(module, str(DOCTOR), "exec"), namespace)
    assert NAMES <= set(namespace), NAMES - set(namespace)
    return namespace["_collect_inference_contract"]


VLLM_FLAGS = "-f docker-compose.base.yml -f docker-compose.nvidia.yml -f docker-compose.vllm.yml"


@pytest.fixture()
def doctor(tmp_path, monkeypatch):
    for key in ("ODS_MODE", "LLM_BACKEND", "LLM_API_URL", "EXTERNAL_LLM_URL", "NATIVE_LLM_BASE_URL",
                "HERMES_LLM_BASE_URL", "GPU_BACKEND"):
        monkeypatch.delenv(key, raising=False)

    def run(env: dict[str, str], flags: str | None = VLLM_FLAGS):
        (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
        if flags is None:
            (tmp_path / ".compose-flags").unlink(missing_ok=True)
        else:
            (tmp_path / ".compose-flags").write_text(flags + "\n", encoding="utf-8")
        return _contract(tmp_path)()
    return run


VLLM_ENV = {"ODS_MODE": "local", "GPU_BACKEND": "nvidia", "LLM_BACKEND": "vllm",
            "LLM_API_URL": "http://litellm:4000", "HERMES_LLM_BASE_URL": "http://model-router:9099/v1"}


def test_a_vllm_install_expects_the_gateway_and_has_no_issue(doctor):
    report = doctor(VLLM_ENV)
    assert report["expected_gateway"] == "litellm"
    assert report["expected_inference_owner"] == "ods"
    assert report["signals"]["vllm_engine"] is True and report["signals"]["vllm_overlay"] is True
    assert report["issues"] == []


def test_a_missing_overlay_or_a_llama_server_url_is_a_blocker(doctor):
    report = doctor(VLLM_ENV, flags="-f docker-compose.base.yml -f docker-compose.nvidia.yml")
    assert [issue["id"] for issue in report["issues"]] == ["ODS-RUNTIME-VLLM-OVERLAY-MISSING"]
    for url in ("http://llama-server:8080", "http://ods-llama-server:8080/v1"):
        report = doctor({**VLLM_ENV, "LLM_API_URL": url})
        assert [issue["id"] for issue in report["issues"]] == ["ODS-RUNTIME-VLLM-LLAMA-ROUTE"], url
        assert report["diagnoses"][0]["severity"] == "blocker"
        assert "ods mode local" in report["diagnoses"][0]["next_steps"][0]


def test_llama_server_installs_keep_their_contract(doctor):
    llama = {**VLLM_ENV, "LLM_BACKEND": "llama-server"}
    report = doctor(llama, flags="-f docker-compose.base.yml -f docker-compose.nvidia.yml")
    assert report["expected_gateway"] == "llama-server"
    assert [issue["id"] for issue in report["issues"]] == ["ODS-RUNTIME-LOCAL-LITELLM-ROUTE"]
    assert report["signals"]["vllm_engine"] is False
