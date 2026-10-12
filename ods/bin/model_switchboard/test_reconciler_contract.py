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

def test_rollback_backend_comes_from_captured_pre_transaction_route():
    # Import the host agent by path: the hyphenated script name is not a
    # normal Python module. Importing it does not start the agent process.
    import importlib.util
    import sys
    from pathlib import Path

    agent_path = Path(__file__).resolve().parents[1] / "ods-host-agent.py"
    spec = importlib.util.spec_from_file_location(
        "_ods_host_agent_rollback_contract", agent_path
    )
    assert spec is not None and spec.loader is not None
    agent = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = agent
    spec.loader.exec_module(agent)

    assert agent._previous_verified_backend_kind(
        {"backend": {"kind": "cafe-llama"}}
    ) == "cafe-llama"
    assert agent._previous_verified_backend_kind(
        {"backend": {"kind": "llama-server"}}
    ) == "llama-server"
    # Missing/unrecognized prior state must fail safe to the ODS default.
    assert agent._previous_verified_backend_kind({}) == "llama-server"

