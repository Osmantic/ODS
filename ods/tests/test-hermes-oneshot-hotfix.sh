#!/bin/bash
# Contract + functional tests for the Hermes KeyError hotfix (#1497).
#
# Validates that:
#   1. The hotfix module + .pth file exist in the expected locations
#   2. The compose.yaml mounts both into the venv's site-packages
#   3. The compose.yaml command is the clean `gateway run` (no shell wrapper)
#   4. The hotfix module is syntactically valid Python
#   5. The bootstrap-upgrade.sh references the issue in its warm-up log
#   6. (Functional) The patch actually intercepts Agent.chat in-process

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HOTFIX="$ROOT_DIR/extensions/services/hermes/dream_hotfix_1497.py"
PTH="$ROOT_DIR/extensions/services/hermes/dream-hotfix-1497.pth"
COMPOSE="$ROOT_DIR/extensions/services/hermes/compose.yaml"
BOOTSTRAP="$ROOT_DIR/scripts/bootstrap-upgrade.sh"

PASS=0
FAIL=0

pass() {
    echo "  ✓ $1"
    PASS=$((PASS + 1))
}

fail() {
    echo "  ✗ $1"
    FAIL=$((FAIL + 1))
}

echo "=== Hermes KeyError hotfix (#1497) contract tests ==="
echo ""

# ── File existence ──────────────────────────────────────────────────────

# 1. Hotfix module exists
if [[ -f "$HOTFIX" ]]; then
    pass "dream_hotfix_1497.py exists"
else
    fail "dream_hotfix_1497.py not found at $HOTFIX"
fi

# 2. .pth file exists
if [[ -f "$PTH" ]]; then
    pass "dream-hotfix-1497.pth exists"
else
    fail "dream-hotfix-1497.pth not found at $PTH"
fi

# ── Syntax check ────────────────────────────────────────────────────────

# 3. Hotfix module is valid Python
_py_cmd=""
if python3 --version >/dev/null 2>&1; then
    _py_cmd="python3"
elif python --version >/dev/null 2>&1; then
    _py_cmd="python"
fi
if [[ -n "$_py_cmd" ]] \
   && $_py_cmd -c "import py_compile, sys; py_compile.compile(sys.argv[1], doraise=True)" "$HOTFIX" 2>/dev/null; then
    pass "dream_hotfix_1497.py is syntactically valid Python"
else
    fail "dream_hotfix_1497.py has Python syntax errors"
fi

# ── Content checks ──────────────────────────────────────────────────────

# 4. Module contains the key fix — catching KeyError for 'final_response'
if grep -qF 'final_response' "$HOTFIX" \
   && grep -qF 'KeyError' "$HOTFIX"; then
    pass "hotfix module handles KeyError for 'final_response'"
else
    fail "hotfix module does not reference KeyError/final_response"
fi

# 5. Module has idempotency guard
if grep -qF '_dream_hotfix_1497' "$HOTFIX"; then
    pass "hotfix module has idempotency guard"
else
    fail "hotfix module missing idempotency guard (_dream_hotfix_1497)"
fi

# 6. Module installs a sys.meta_path hook (in-process patching)
if grep -qF 'sys.meta_path' "$HOTFIX"; then
    pass "hotfix module uses sys.meta_path (in-process hook)"
else
    fail "hotfix module does not use sys.meta_path"
fi

# 7. .pth file imports the hotfix module
if grep -qF 'import dream_hotfix_1497' "$PTH"; then
    pass ".pth file imports dream_hotfix_1497"
else
    fail ".pth file does not import dream_hotfix_1497"
fi

# ── Compose wiring ──────────────────────────────────────────────────────

# 8. Compose mounts the hotfix module into site-packages
if grep -qF 'dream_hotfix_1497.py' "$COMPOSE" \
   && grep -qF 'site-packages/dream_hotfix_1497.py' "$COMPOSE"; then
    pass "compose.yaml mounts hotfix module into site-packages"
else
    fail "compose.yaml does not mount hotfix module into site-packages"
fi

# 9. Compose mounts the .pth file into site-packages
if grep -qF 'dream-hotfix-1497.pth' "$COMPOSE" \
   && grep -qF 'site-packages/dream-hotfix-1497.pth' "$COMPOSE"; then
    pass "compose.yaml mounts .pth file into site-packages"
else
    fail "compose.yaml does not mount .pth file into site-packages"
fi

# 10. Compose command is clean gateway run (no sh -c wrapper)
#     The old broken approach ran `sh -c 'python hotfix.py; exec hermes gateway run'`
#     which patched a throwaway process. The fix uses .pth for in-process patching
#     so the command should be the clean `gateway run`.
if grep -qF -- '- gateway' "$COMPOSE" \
   && grep -qF -- '- run' "$COMPOSE" \
   && ! grep -qF 'oneshot-keyerror-hotfix' "$COMPOSE"; then
    pass "compose.yaml command is clean 'gateway run' (no shell wrapper)"
else
    fail "compose.yaml command is not the expected clean 'gateway run'"
fi

# 11. No reference to the old oneshot script (fully replaced)
if ! grep -qF 'oneshot-keyerror-hotfix.py' "$COMPOSE"; then
    pass "compose.yaml has no reference to old oneshot-keyerror-hotfix.py"
else
    fail "compose.yaml still references old oneshot-keyerror-hotfix.py"
fi

# ── Bootstrap ───────────────────────────────────────────────────────────

# 12. Bootstrap upgrade references issue #1497 in warm-up failure log
if grep -qF '#1497' "$BOOTSTRAP"; then
    pass "bootstrap-upgrade.sh references issue #1497 in warm-up log"
else
    fail "bootstrap-upgrade.sh does not reference #1497"
fi

# ── Functional test: sys.meta_path hook path (primary) ──────────────────
# This is the CRITICAL test: prove the .pth → import dream_hotfix_1497 →
# sys.meta_path hook → import run_agent → Agent.chat patched path works
# in a fresh interpreter, matching the production startup sequence.
#
# We create a temp directory with a fake run_agent.py on disk, put both it
# and the hotfix module on PYTHONPATH, then run a fresh Python subprocess
# that imports dream_hotfix_1497 (installs hook) and then imports run_agent
# (triggers hook).  This is exactly what the .pth file does at interpreter
# startup inside the Hermes container.
#
# NOTE: The compose mount hard-codes the Python 3.11 venv path
# (/opt/hermes/.venv/lib/python3.11/site-packages).  If the pinned Hermes
# image is bumped to a different Python version, the mount path must be
# updated — that is a separate maintenance concern tracked in compose.yaml.

echo ""
echo "--- Functional: sys.meta_path hook fires on fresh import (primary) ---"

if [[ -z "$_py_cmd" ]]; then
    fail "SKIPPED functional hook test: no Python interpreter found"
else
    _hook_tmpdir=$(mktemp -d)
    trap 'rm -rf "$_hook_tmpdir"' EXIT

    # Create a fake run_agent.py on disk (NOT pre-loaded into sys.modules).
    cat > "$_hook_tmpdir/run_agent.py" <<'FAKE_RUN_AGENT'
class Agent:
    def chat(self, prompt, *args, **kwargs):
        # Simulates the upstream bug: result['final_response'] on a
        # response dict that has no 'final_response' key.
        raise KeyError('final_response')
FAKE_RUN_AGENT

    _hotfix_dir=$(dirname "$HOTFIX")

    # Run in a FRESH subprocess with PYTHONPATH set so both the hotfix
    # module and the fake run_agent.py are importable — no sys.modules
    # pre-loading, no direct apply() call.
    _hook_result=$(PYTHONPATH="$_hotfix_dir:$_hook_tmpdir" $_py_cmd -c "
import sys

# Sanity: run_agent must NOT be in sys.modules yet.
assert 'run_agent' not in sys.modules, 'run_agent was pre-loaded'

# 1. Import the hotfix — installs the sys.meta_path hook.
#    This is what the .pth file does at interpreter startup.
import dream_hotfix_1497

# 2. Verify the hook is installed.
hook_installed = any(
    type(f).__name__ == '_HotfixFinder' for f in sys.meta_path
)
if not hook_installed:
    print('FAIL: _HotfixFinder not found in sys.meta_path after import')
    sys.exit(1)

# 3. Import run_agent — this should trigger the hook, which wraps the
#    real loader and calls _apply_patch() after exec_module.
import run_agent

# 4. Verify Agent.chat is patched (has the _dream_hotfix_1497 sentinel).
if not getattr(run_agent.Agent.chat, '_dream_hotfix_1497', False):
    print('FAIL: Agent.chat was not patched by the meta_path hook')
    sys.exit(1)

# 5. Verify the patched chat() returns '' instead of raising KeyError.
agent = run_agent.Agent()
try:
    result = agent.chat('test prompt')
except KeyError:
    print('FAIL: Agent.chat still raises KeyError after hook patching')
    sys.exit(1)

if result != '':
    print('FAIL: expected empty string, got: ' + repr(result))
    sys.exit(1)

# 6. Verify the hook removed itself (one-shot behavior).
hook_still_present = any(
    type(f).__name__ == '_HotfixFinder' for f in sys.meta_path
)
if hook_still_present:
    print('FAIL: _HotfixFinder still in sys.meta_path (should be one-shot)')
    sys.exit(1)

print('OK')
" 2>/dev/null)

    if [[ "$_hook_result" == "OK" ]]; then
        pass "functional (hook): sys.meta_path hook patches Agent.chat on fresh import"
    else
        fail "functional (hook): $_hook_result"
    fi
fi

# ── Functional test: direct apply() helper (supplementary) ─────────────
# This verifies the _apply_patch() / apply() helper independently —
# useful as a unit test for the patch logic, separate from the hook wiring.

echo ""
echo "--- Functional: direct apply() verification (supplementary) ---"

if [[ -z "$_py_cmd" ]]; then
    fail "SKIPPED functional apply test: no Python interpreter found"
else
    _func_result=$($_py_cmd -c "
import sys, types, os

# 1. Create a fake run_agent module with Agent.chat that raises KeyError
fake_run_agent = types.ModuleType('run_agent')

class FakeAgent:
    def chat(self, prompt, *args, **kwargs):
        raise KeyError('final_response')

fake_run_agent.Agent = FakeAgent
sys.modules['run_agent'] = fake_run_agent

# 2. Import the hotfix and call apply() directly.
hotfix_dir = os.path.dirname(os.path.abspath('$HOTFIX'))
sys.path.insert(0, hotfix_dir)
import dream_hotfix_1497
patched = dream_hotfix_1497.apply()

if not patched:
    print('FAIL: apply() returned False — patch was not applied')
    sys.exit(1)

# 3. Verify the patch is active on the SAME Agent class
agent = FakeAgent()
try:
    result = agent.chat('test prompt')
except KeyError:
    print('FAIL: Agent.chat still raises KeyError after patching')
    sys.exit(1)

if result != '':
    print('FAIL: expected empty string, got: ' + repr(result))
    sys.exit(1)

# 4. Verify idempotency — second apply() should be a no-op
second = dream_hotfix_1497.apply()
if second:
    print('FAIL: second apply() should return False (idempotent)')
    sys.exit(1)

# 5. Verify unrelated KeyErrors still propagate
class AgentOtherError:
    def chat(self, prompt, *args, **kwargs):
        raise KeyError('some_other_key')

fake_run_agent.Agent = AgentOtherError
third = dream_hotfix_1497.apply()
if third:
    agent2 = AgentOtherError()
    try:
        agent2.chat('test')
        print('FAIL: unrelated KeyError was swallowed')
        sys.exit(1)
    except KeyError:
        pass  # correct — unrelated KeyErrors propagate

print('OK')
" 2>/dev/null)

    if [[ "$_func_result" == "OK" ]]; then
        pass "functional (apply): monkey-patch intercepts KeyError('final_response') in-process"
    else
        fail "functional (apply): $_func_result"
    fi
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
