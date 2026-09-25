#!/usr/bin/env python3
"""Bootstrap's Pixel hold spans inference promotion and verified rollback."""
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = (ROOT / 'scripts/bootstrap-upgrade.sh').read_text()
INSTALLER = (ROOT / 'installers/lib/pixel-host-install.sh').read_text()
TX = 'a' * 64


def function(text, name):
    return re.search(r'^' + re.escape(name) + r'\(\) \{\n.*?^}', text, re.M | re.S).group(0)


class BootstrapPixelTransactionTests(unittest.TestCase):
    def run_shell(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / 'events'
            preamble = '''set -euo pipefail
BOOTSTRAP_PIXEL_TRANSACTION=""
BOOTSTRAP_PIXEL_OWNER=""
BOOTSTRAP_PIXEL_HOME=""
BOOTSTRAP_PIXEL_CONFIG_MUTATED=false
BOOTSTRAP_PIXEL_RELEASE_FAILED=false
FULL_LLM_MODEL=full9b
log() { :; }
prepare_bootstrap_pixel_model() { BOOTSTRAP_PIXEL_OWNER=owner; BOOTSTRAP_PIXEL_HOME=/home/owner; }
_ods_pixel_openclaw_bin() { echo /installed/openclaw; }
_ods_pixel_install_access_service() { echo access-controller >> "$EVENTS"; }
_ods_pixel_model_transition() {
    echo "transition:$1:${5:-}" >> "$EVENTS"
    case "$1" in
      begin) printf '%064d\\n' 0 | tr 0 a ;;
      finish) test "${FAIL_FINISH:-false}" != true ;;
    esac
}
read_env_value() { case "$1" in MAX_CONTEXT) echo 65536 ;; LLAMA_REASONING) echo off ;; esac; }
_ods_pixel_default_output_tokens() { echo 4096; }
ods_pixel_reconcile_promoted_model() {
    test "$9" = "$BOOTSTRAP_PIXEL_TRANSACTION"
    echo "reconcile:$3" >> "$EVENTS"
    test "${FAIL_RECONCILE:-false}" != true
}
'''
            names = ['acquire_bootstrap_pixel_model_transaction', 'finish_bootstrap_pixel_model_transaction',
                     'cleanup_bootstrap_pixel_model_transaction', 'reconcile_ods_managed_pixel_model',
                     'restore_docker_llama_server_after_swap_failure', 'rollback_windows_lemonade_swap',
                     'restore_active_model_config', 'restore_bootstrap_model_after_windows_swap_failure']
            script = preamble + '\n'.join(function(BOOTSTRAP, name) for name in names) + '\n' + body
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True,
                                    env={'PATH': '/usr/bin:/bin', 'EVENTS': str(log)})
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return log.read_text().splitlines() if log.exists() else []

    def test_pixel_drain_precedes_router_gate_and_model_snapshot(self):
        start = BOOTSTRAP.index('if [[ "$_windows_lemonade_swap_applies" == "true" ||', BOOTSTRAP.index('_docker_llama_swap_applies=false'))
        end = BOOTSTRAP.index('# ── Phase 3: Update .env', start)
        flow = BOOTSTRAP[start:end]
        events = self.run_shell('''
_windows_lemonade_swap_applies=false
_windows_native_llama_swap_applies=false
_docker_llama_swap_applies=true
acquire_model_router_swap_gate() { test -n "$BOOTSTRAP_PIXEL_TRANSACTION"; echo router-drained >> "$EVENTS"; }
snapshot_active_model_config() { echo snapshot-before-mutation >> "$EVENTS"; }
''' + flow)
        self.assertLess(events.index('transition:begin:'), events.index('router-drained'))
        self.assertLess(events.index('router-drained'), events.index('snapshot-before-mutation'))
        self.assertFalse(any(x.startswith('transition:finish:') for x in events))

    def test_promoted_route_releases_only_after_reconciliation(self):
        events = self.run_shell('''
acquire_bootstrap_pixel_model_transaction
BOOTSTRAP_PIXEL_CONFIG_MUTATED=true
reconcile_ods_managed_pixel_model
[[ -z "$BOOTSTRAP_PIXEL_TRANSACTION" ]]
''')
        self.assertLess(events.index('reconcile:full9b'), events.index('transition:finish:applied'))

    def test_failed_reconciliation_retains_hold_until_verified_old_route(self):
        events = self.run_shell('''
acquire_bootstrap_pixel_model_transaction
BOOTSTRAP_PIXEL_CONFIG_MUTATED=true
FAIL_RECONCILE=true
if reconcile_ods_managed_pixel_model; then exit 10; fi
if cleanup_bootstrap_pixel_model_transaction; then exit 11; fi
[[ -n "$BOOTSTRAP_PIXEL_TRANSACTION" ]]
echo inference-restored >> "$EVENTS"
FAIL_RECONCILE=false
reconcile_ods_managed_pixel_model bootstrap2b rolled-back
[[ -z "$BOOTSTRAP_PIXEL_TRANSACTION" ]]
''')
        self.assertEqual([x for x in events if x.startswith('transition:finish:')], ['transition:finish:rolled-back'])
        self.assertLess(events.index('inference-restored'), events.index('reconcile:bootstrap2b'))
        self.assertLess(events.index('reconcile:bootstrap2b'), events.index('transition:finish:rolled-back'))

    def test_pre_mutation_failure_can_release_unchanged_route(self):
        events = self.run_shell('acquire_bootstrap_pixel_model_transaction\ncleanup_bootstrap_pixel_model_transaction\n')
        self.assertEqual(events[-1], 'transition:finish:rolled-back')

    def test_lost_release_suppresses_unsafe_automatic_runtime_rollback(self):
        events = self.run_shell('''
acquire_bootstrap_pixel_model_transaction
BOOTSTRAP_PIXEL_CONFIG_MUTATED=true
FAIL_FINISH=true
if reconcile_ods_managed_pixel_model; then exit 10; fi
restore_active_model_config() { echo UNSAFE-MUTATION >> "$EVENTS"; }
if restore_docker_llama_server_after_swap_failure http://127.0.0.1/health; then exit 11; fi
[[ -n "$BOOTSTRAP_PIXEL_TRANSACTION" ]]
''')
        self.assertNotIn('UNSAFE-MUTATION', events)

    def test_uncertain_pre_mutation_finish_is_not_replayed_by_cleanup(self):
        events = self.run_shell('''
acquire_bootstrap_pixel_model_transaction
FAIL_FINISH=true
if cleanup_bootstrap_pixel_model_transaction; then exit 10; fi
if cleanup_bootstrap_pixel_model_transaction; then exit 11; fi
if finish_bootstrap_pixel_model_transaction rolled-back; then exit 12; fi
[[ -n "$BOOTSTRAP_PIXEL_TRANSACTION" ]]
''')
        self.assertEqual(events.count('transition:finish:rolled-back'), 1)

    def test_uncertain_release_blocks_windows_and_shared_restore_entry_points(self):
        self.run_shell('''
BOOTSTRAP_PIXEL_RELEASE_FAILED=true
if rollback_windows_lemonade_swap; then exit 10; fi
if restore_active_model_config; then exit 11; fi
if restore_bootstrap_model_after_windows_swap_failure; then exit 12; fi
''')

    def test_windows_borrowed_hold_requires_verified_inference_before_pixel_release(self):
        for restored in ('true', 'false'):
            events = self.run_shell('''
acquire_bootstrap_pixel_model_transaction
BOOTSTRAP_PIXEL_CONFIG_MUTATED=true
BOOTSTRAP_GGUF_FILE=bootstrap2b.gguf
WINDOWS_LEMONADE_OPENCLAW_PRESENT=false
snapshot_env_value() { case "$1" in GGUF_FILE) echo bootstrap2b.gguf ;; LLM_MODEL) echo bootstrap2b ;; LEMONADE_MODEL) echo bootstrap2b ;; esac; }
restore_bootstrap_model_after_windows_swap_failure() { :; }
restore_active_model_config() { :; }
restart_windows_lemonade_with_previous_model() { "''' + restored + '''"; }
read_env_value() { case "$1" in LEMONADE_MODEL) echo bootstrap2b ;; MAX_CONTEXT) echo 65536 ;; LLAMA_REASONING) echo off ;; esac; }
restart_windows_lemonade_dependents_after_rollback() { :; }
release_model_router_swap_gate() { :; }
verify_windows_lemonade_downstream_route() { :; }
rollback_windows_lemonade_swap || true
''')
            releases = [x for x in events if x.startswith('transition:finish:')]
            self.assertEqual(releases, ['transition:finish:rolled-back'] if restored == 'true' else [])

    def test_borrowed_transaction_must_match_held_root_journal(self):
        for phase, tx in [('draining', TX), ('held', 'b' * 64), ('error', TX)]:
            script = '''set -eu
_ods_pixel_model_transition() { printf '%s' "$STATUS"; }
_ods_pixel_managed_source_ref() { echo UNSAFE-MUTATION; return 1; }
''' + function(INSTALLER, 'ods_pixel_reconcile_promoted_model') + '''
if ods_pixel_reconcile_promoted_model owner /home/owner full9b ready 65536 4096 false "" "$TX"; then exit 1; fi
'''
            import json
            result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                env={'PATH': '/usr/bin:/bin', 'TX': TX, 'STATUS': json.dumps({'pending': True,
                    'kind': 'model', 'phase': phase, 'transaction_id': tx})})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('UNSAFE-MUTATION', result.stdout)


if __name__ == '__main__':
    unittest.main()
