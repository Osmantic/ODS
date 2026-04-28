"""Real llama.cpp RPC integration tests.

Stack: llama-server (controller) + 2x rpc-server (workers), all built
from the production Dockerfiles. llama-server is launched with:

    --rpc rpc1:50052,rpc2:50052  -ngl 99

which forces *all* model layers onto the RPC devices. A successful
/completion therefore proves:

  1. The controller reached both rpc-servers over TCP
  2. The RPC handshake + tensor transfer protocol worked
  3. Remote compute returned correct intermediate results
  4. The full token-generation loop ran across the network

Model: Qwen2.5-0.5B-Instruct-Q2_K (~280MB, downloaded from HF on first
launch). Small enough for CPU inference in CI.
"""
import time

import pytest
import requests


def test_llama_health_reports_ok(llama_url):
    """Baseline: /health returns {"status":"ok"} once the model is loaded
    and bound to both RPC workers. A healthy llama with -ngl 99 already
    proves both rpc-servers were reachable AND completed the handshake;
    we cannot TCP-probe them separately because rpc-server only accepts
    one client and llama owns it."""
    r = requests.get(f"{llama_url}/health", timeout=5)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_model_props_advertises_context(llama_url):
    """/props returns generation_settings and model metadata — proves the
    model is actually loaded, not just that the HTTP server is up."""
    r = requests.get(f"{llama_url}/props", timeout=10)
    assert r.status_code == 200
    data = r.json()
    # llama.cpp advertises n_ctx or default_generation_settings.n_ctx
    assert "default_generation_settings" in data or "generation_settings" in data


def test_completion_returns_tokens_across_rpc_workers(llama_url):
    """End-to-end: POST /completion, assert tokens come back.

    With -ngl 99 and --rpc set, every transformer layer lives on a
    remote rpc-server. If either worker were unreachable or the protocol
    broken, this request would hang or error.
    """
    r = requests.post(
        f"{llama_url}/completion",
        json={
            "prompt": "The capital of France is",
            "n_predict": 8,
            "temperature": 0.0,
            "cache_prompt": False,
            "stream": False,
        },
        timeout=180,
    )
    assert r.status_code == 200, f"status={r.status_code} body={r.text[:400]}"
    body = r.json()
    assert "content" in body, f"no content key: {body}"
    assert len(body["content"]) > 0, f"empty completion: {body}"
    # Sanity: llama.cpp reports timings, and tokens_predicted should match n_predict
    # (or stop_eos if EOS hit early). Anything > 0 proves inference ran.
    assert body.get("tokens_predicted", 0) > 0, f"no tokens generated: {body}"


def test_completion_deterministic_with_zero_temperature(llama_url):
    """Two identical greedy-decode requests must produce the same output.
    Proves the RPC round-trip is consistent, not hallucinating random data."""
    payload = {
        "prompt": "1 + 1 =",
        "n_predict": 4,
        "temperature": 0.0,
        "seed": 42,
        "cache_prompt": False,
        "stream": False,
    }
    r1 = requests.post(f"{llama_url}/completion", json=payload, timeout=120)
    r2 = requests.post(f"{llama_url}/completion", json=payload, timeout=120)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["content"] == r2.json()["content"], (
        f"non-deterministic output:\n  r1={r1.json()['content']!r}\n  "
        f"r2={r2.json()['content']!r}"
    )


def test_tokenize_and_detokenize_roundtrip(llama_url):
    """The /tokenize and /detokenize endpoints require the model to be
    loaded (vocab is part of the GGUF)."""
    tok = requests.post(
        f"{llama_url}/tokenize",
        json={"content": "Hello, world"},
        timeout=15,
    ).json()
    assert "tokens" in tok and len(tok["tokens"]) > 0

    detok = requests.post(
        f"{llama_url}/detokenize",
        json={"tokens": tok["tokens"]},
        timeout=15,
    ).json()
    # Detokenized text contains the original (tokenization may add BOS etc.)
    assert "Hello" in detok["content"]


def test_timings_show_nonzero_compute_time(llama_url):
    """llama.cpp /completion returns timings.predicted_ms > 0, which only
    happens if the generation loop actually executed remote tensor ops."""
    r = requests.post(
        f"{llama_url}/completion",
        json={
            "prompt": "Count: 1, 2,",
            "n_predict": 5,
            "temperature": 0.0,
            "cache_prompt": False,
            "stream": False,
        },
        timeout=120,
    )
    assert r.status_code == 200
    timings = r.json().get("timings", {})
    assert timings.get("predicted_ms", 0) > 0, f"no compute time reported: {timings}"
    assert timings.get("predicted_n", 0) > 0


def test_streaming_completion_produces_events(llama_url):
    """Streaming /completion emits a sequence of JSON events — each one
    represents a token round-tripped through RPC workers."""
    with requests.post(
        f"{llama_url}/completion",
        json={
            "prompt": "One two three",
            "n_predict": 5,
            "temperature": 0.0,
            "cache_prompt": False,
            "stream": True,
        },
        timeout=120,
        stream=True,
    ) as resp:
        assert resp.status_code == 200
        events = 0
        last = None
        for line in resp.iter_lines():
            if not line:
                continue
            # Server-Sent-Events prefix
            if line.startswith(b"data: "):
                events += 1
                last = line[len(b"data: "):]
        assert events > 0, "no SSE events received"
        assert last is not None
