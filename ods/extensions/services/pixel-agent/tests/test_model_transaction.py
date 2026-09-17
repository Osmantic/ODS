"""Real private filesystem writes; simulated service callbacks, not runtime proof."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX custody tests run in Linux/WSL")
import hashlib
import json
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "bin"), str(Path(__file__).resolve().parents[1] / "host")]
import model_transaction as tx
from pixel_model_contract import ModelError, plan, projection

def config():
    return {"agents":{"defaults":{"compaction":{"mode":"safeguard"}},"list":[{"id":"pixel","model":"ods-gateway/ods/current","contextTokens":65536,"sandbox":{"mode":"all"},"verboseDefault":"on"}]},
        "models":{"providers":{"ods-gateway":{"baseUrl":"http://127.0.0.1:4000/v1","apiKey":"PRIVATE","models":[{"id":"ods/current","name":"ODS Current (Qwen-4B)","contextWindow":65536,"maxTokens":8192,"reasoning":False}]}}},
        "plugins":{"entries":{"pixel-ods":{"enabled":True,"config":{"modelContextWindow":65536}}}},"tools":{"deny":["x"]}}

NEW = dict(model="Qwen-27B", contextLength=16384, maxTokens=4096, reasoning=False)
ID = "a" * 64
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

@pytest.fixture
def owner(tmp_path):
    tmp_path.chmod(0o700)
    path=tmp_path / "openclaw.json"
    path.write_text(json.dumps(config(), indent=4)+"\n\n")
    path.chmod(0o600)
    return path, tmp_path / "state"

def call(owner, operation, **kw):
    path,state=owner
    return tx.operate(str(path),state_dir=str(state),operation=operation,transaction_id=ID,
        expected_config_sha256=sha(path),validate_config=lambda p: bool(json.loads(Path(p).read_text())),check_no_active_run=lambda:False,**kw)

def test_apply_context_and_exact_rollback(owner):
    path,state=owner;before=path.read_bytes()
    call(owner,"model-begin")
    call(owner,"model-apply",proposed={**NEW,"routeFingerprint":"b"*64})
    current=json.loads(path.read_bytes())
    assert projection(current)["contract"] == {**NEW,"routeFingerprint":"b"*64}
    assert projection(current)["limits"]["contextTokens"] == 16384
    assert current["tools"] == config()["tools"] and current["agents"]["list"][0]["sandbox"] == {"mode":"all"}
    assert call(owner,"model-status")["pending"]
    call(owner,"model-rollback")
    assert path.read_bytes()==before
    call(owner,"model-finish",outcome="rollback")
    assert not call(owner,"model-status")["pending"]
    assert call(owner,"model-status")["completion"]["outcome"]=="rollback"

def test_replay_target_cas_and_busy_checks(owner):
    path,state=owner
    call(owner,"model-begin");call(owner,"model-begin")
    call(owner,"model-apply",proposed=NEW);first=path.read_bytes()
    call(owner,"model-apply",proposed=NEW);assert path.read_bytes()==first
    with pytest.raises(ModelError,match="target-changed"): call(owner,"model-apply",proposed={**NEW,"model":"other"})
    path.write_bytes(first+b" ")
    with pytest.raises(ModelError,match="config-changed"): call(owner,"model-rollback")

def test_backup_tamper_and_other_transition_rejected(owner):
    path,state=owner;call(owner,"model-begin")
    (state/tx.BACKUP).write_bytes(b"{}")
    with pytest.raises(ModelError,match="backup-mismatch"):call(owner,"model-rollback")

def test_local_clear_and_invalid_target_no_mutation(owner):
    c=config();c["plugins"]["entries"]["pixel-ods"]["config"]["modelRouteFingerprint"]="b"*64
    assert "routeFingerprint" not in projection(plan(c,NEW))["contract"]
    for value in ({**NEW,"contextLength":True},{**NEW,"maxTokens":17000},{**NEW,"routeFingerprint":"b"*64+"\n"},{**NEW,"baseUrl":"https://injected"}):
        with pytest.raises(ModelError):plan(c,value)


def test_output_budget_overrides_all_inherited_aliases_and_preserves_sampling():
    c=config()
    c["agents"]["defaults"].update(params={"max_tokens":2048},models={"ods-gateway/ods/current":{"params":{"max_completion_tokens":3072}}})
    params={"max_tokens":1024,"max_completion_tokens":1536,"temperature":0.6,"chat_template_kwargs":{"enable_thinking":True}}
    c["agents"]["list"][0]["params"]=params
    updated=plan(c,NEW)
    assert projection(updated)["limits"]["maxOutputTokens"]==NEW["maxTokens"]
    assert updated["agents"]["list"][0]["params"] == {"maxTokens":NEW["maxTokens"],"temperature":0.6,"chat_template_kwargs":{"enable_thinking":True}}
    assert c["agents"]["list"][0]["params"]==params
