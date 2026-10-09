"""Execute the real Mac bootstrap branch with inert process/controller boundaries."""
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'scripts/bootstrap-upgrade.sh').read_text()
OLD = 'Qwen3.5-2B-Q4_K_M.gguf'
NEW = 'Qwen3.5-9B-Q4_K_M.gguf'


def function(name):
    return re.search(r'^' + name + r'\(\) \{\n.*?^}', SOURCE, re.M | re.S).group()


def test_darwin_begins_before_router_drain_snapshot_and_env_write(tmp_path):
    start = SOURCE.index('if [[ "$_windows_native_llama_swap_applies" == "true" ||',
        SOURCE.index('_docker_llama_swap_applies=false'))
    end = SOURCE.index('# ── Phase 3: Update .env', start)
    script = '''set -euo pipefail
uname() { echo Darwin; }
log() { :; }
BOOTSTRAP_PIXEL_TRANSACTION=""; BOOTSTRAP_PIXEL_NATIVE=false
_windows_native_llama_swap_applies=false; _docker_llama_swap_applies=true
_macos_native_llama_swap_applies=true
prepare_bootstrap_pixel_model() { BOOTSTRAP_PIXEL_OWNER=""; }
native_bootstrap_model() { echo begin >> "$EVENTS"; printf '%064d\\n' 1; }
acquire_model_router_swap_gate() { echo router >> "$EVENTS"; }
snapshot_active_model_config() { echo snapshot >> "$EVENTS"; }
'''+function('acquire_bootstrap_pixel_model_transaction')+'\n'+SOURCE[start:end]
    events = tmp_path / 'events'
    result = subprocess.run(['bash'], input=script, text=True, capture_output=True,
        env={**os.environ, 'EVENTS': str(events)})
    assert result.returncode == 0, result.stderr
    assert events.read_text().splitlines() == ['begin', 'router', 'snapshot']


@pytest.mark.parametrize('failure,rollback_failure', [(0, 0), (1, 0), (20, 0), (20, 1)])
def test_native_cleanup_requires_committed_contract(tmp_path, failure, rollback_failure):
    install = tmp_path / 'ods'
    models = install / 'data/models'
    models.mkdir(parents=True)
    (models / OLD).write_text('bootstrap fixture')
    (models / NEW).write_text('full fixture')
    (install / 'data/.llama-server.pid').write_text('55555')
    (install / '.env').write_text(f'GGUF_FILE={NEW}\nCTX_SIZE=65536\n')
    (install / 'contract').write_text(OLD)
    binary = install / 'bin/llama-server'
    binary.parent.mkdir()
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o700)
    service = install / 'installers/macos/lib/native-llama-service.sh'
    service.parent.mkdir(parents=True)
    service.write_text('#!/bin/bash\nprintf "66666\\n" > "$4"\n')
    start = SOURCE.index('elif [[ -f "$INSTALL_DIR/data/.llama-server.pid" ]]; then')
    end = SOURCE.index('# ── Phase 5c:', start)
    block = SOURCE[start:end].replace('elif [[', 'if [[', 1)
    script = '''set -uo pipefail
INSTALL_DIR="$FIXTURE"; MODELS_DIR="$FIXTURE/data/models"; ENV_FILE="$FIXTURE/.env"
HOME="$FIXTURE/home"; FULL_GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf; FULL_MAX_CONTEXT=65536
BOOTSTRAP_GGUF=Qwen3.5-2B-Q4_K_M.gguf; BOOTSTRAP_PATH="$MODELS_DIR/$BOOTSTRAP_GGUF"
HOT_SWAP_VERIFIED=false; TOTAL_BYTES=1; BOOTSTRAP_PIXEL_TRANSACTION=fixture-held
log() { printf '%s\\n' "$*" >> "$FIXTURE/events"; }
kill() { return 0; }; sleep() { :; }; curl() { return 0; }
ps() { if [[ "$*" == *comm=* ]]; then echo llama-server; else echo "llama-server --model $BOOTSTRAP_PATH"; fi; }
read_env_value() { sed -n "s/^$1=//p" "$ENV_FILE"; }
native_bootstrap_model() {
    log "adapter:$1"
    if [[ "$1" == promote ]]; then
        [[ "$FAILURE" == 0 ]] || return "$FAILURE"
        echo "$FULL_GGUF_FILE" > "$FIXTURE/contract"
    else
        [[ "$ROLLBACK_FAILURE" == 0 ]] || return 1
    fi
}
restore_active_model_config() { log restore; }
discard_active_model_config_snapshot() { log discard; }
write_status() { log "status:$1"; }
'''+block+'\nprintf "verified=%s;transaction=%s\\n" "$HOT_SWAP_VERIFIED" "$BOOTSTRAP_PIXEL_TRANSACTION"\n'
    result = subprocess.run(['bash'], input=script, text=True, capture_output=True,
        env={**os.environ, 'FIXTURE': str(install), 'FAILURE': str(failure), 'ROLLBACK_FAILURE': str(rollback_failure)})
    events = (install / 'events').read_text().splitlines()
    if failure == 0:
        assert result.returncode == 0, result.stderr
        assert (install / 'contract').read_text().strip() == NEW
        assert events.index('adapter:promote') < events.index('discard')
        assert not (models / OLD).exists()
        assert 'verified=true;transaction=\n' == result.stdout
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert (models / OLD).read_text() == 'bootstrap fixture'
        assert (install / 'contract').read_text() == OLD
        assert 'discard' not in events and 'status:failed' in events
        assert ('adapter:rollback' in events) == (failure == 20)
        assert ('restore' in events) == (failure == 20)
