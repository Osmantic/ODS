"""The Desktop llama-server is loopback-only and its key stays server-side."""

import os

import pytest

import native_llm
from native_llm import NativeLlm, NativeLlmConfigError, parse


def env(tmp_path, url="http://127.0.0.1:18080"):
    return {"ODS_NATIVE_LLM_URL": url, "ODS_NATIVE_LLM_RUNTIME_DIR": str(tmp_path)}


def test_loopback_url_and_runtime_dir(tmp_path):
    llm = parse(env(tmp_path, "http://127.0.0.1:18080/"))
    assert llm.base_url == "http://127.0.0.1:18080" and llm.key_file == tmp_path / "api-key"


@pytest.mark.parametrize("url", [
    "", "http://192.168.1.20:8080", "http://llama-server:8080", "https://127.0.0.1:8080",
    "http://127.0.0.1", "http://127.0.0.1:8080/v1", "http://user:pw@127.0.0.1:8080", "http://0.0.0.0:8080",
])
def test_anything_but_a_loopback_http_endpoint_is_refused(tmp_path, url):
    with pytest.raises(NativeLlmConfigError, match="ODS_NATIVE_LLM_URL"):
        parse(env(tmp_path, url))


def test_runtime_dir_must_be_absolute(tmp_path):
    with pytest.raises(NativeLlmConfigError, match="ODS_NATIVE_LLM_RUNTIME_DIR"):
        parse({"ODS_NATIVE_LLM_URL": "http://127.0.0.1:18080", "ODS_NATIVE_LLM_RUNTIME_DIR": "portal-runtime"})


def test_key_is_read_once_and_reread_after_rotation(tmp_path):
    llm = NativeLlm("http://127.0.0.1:18080", tmp_path)
    llm.key_file.write_text("first-key\n", encoding="utf-8")
    assert native_llm.headers(llm)["Authorization"] == "Bearer first-key"
    llm.key_file.write_text("second-key-longer\n", encoding="utf-8")
    os.utime(llm.key_file, ns=(1, 1))  # a different mtime, as a rewrite produces
    assert native_llm.read_key(llm) == "second-key-longer"


def test_empty_key_file_is_refused_without_leaking(tmp_path):
    llm = NativeLlm("http://127.0.0.1:18080", tmp_path)
    llm.key_file.write_text("  \n", encoding="utf-8")
    with pytest.raises(NativeLlmConfigError, match="empty"):
        native_llm.read_key(llm)


def test_runtime_records(tmp_path):
    llm = NativeLlm("http://127.0.0.1:18080", tmp_path)
    assert native_llm.runtime_records(llm) == (None, None)
    (tmp_path / "intent.json").write_text('{"State":"stopped"}', encoding="utf-8")
    (tmp_path / "ready.json").write_text('{"ModelId":"m.gguf"', encoding="utf-8")  # being rewritten
    intent, ready = native_llm.runtime_records(llm)
    assert intent == {"State": "stopped"} and ready is None


def test_compose_profile_has_no_native_llm():
    assert native_llm.configured() is None
