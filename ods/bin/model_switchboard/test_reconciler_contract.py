"""Focused contracts for the optional cafe-llama runtime adapter."""

from datetime import datetime, timezone

from ods.bin.model_switchboard.adapters import CafeLlamaAdapter
from ods.bin.model_switchboard.reconciler import run_runtime_activation


PROOF = {
    "identity": "/models/test.gguf",
    "contextLength": 4096,
    "contextVerified": True,
    "capabilities": {
        "chat": True,
        "tools": False,
        "vision": False,
        "agentViable": False,
    },
    "verifiedAt": datetime.now(timezone.utc).isoformat(),
}


def test_cafe_adapter_is_a_distinct_opt_in_runtime_kind():
    adapter = CafeLlamaAdapter(
        restart=lambda env: None,
        wait_ready=lambda env, gguf, ctx: dict(PROOF),
        expected_gguf="test.gguf",
        context_length=4096,
    )
    assert adapter.kind == "cafe-llama"


def test_reconciler_preserves_cafe_runtime_kind_in_verified_proof():
    calls = []
    adapter = CafeLlamaAdapter(
        restart=lambda env: calls.append("stage"),
        wait_ready=lambda env, gguf, ctx: dict(PROOF),
        expected_gguf="test.gguf",
        context_length=4096,
    )

    outcome = run_runtime_activation(adapter, {})

    assert outcome["ok"] is True
    assert outcome["runtimeKind"] == "cafe-llama"
    assert outcome["identity"] == "/models/test.gguf"
    assert outcome["contextVerified"] is True
    assert calls == ["stage"]


def test_cafe_adapter_fails_closed_when_runtime_proof_is_missing():
    adapter = CafeLlamaAdapter(
        restart=lambda env: None,
        wait_ready=lambda env, gguf, ctx: {},
        expected_gguf="test.gguf",
        context_length=4096,
    )

    outcome = run_runtime_activation(adapter, {})

    assert outcome["ok"] is False
    assert outcome["phase"] == "verify_identity"
