#!/usr/bin/python3
"""Fixed unprivileged owner operations; never a root program/command relay."""
import sys
if not sys.flags.isolated:
    raise SystemExit('isolated-python-required')
sys.dont_write_bytecode = True
import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess


def main():
    if os.geteuid() == 0 or len(sys.argv) != 2 or sys.argv[1] not in ('inference', 'reconcile', 'restart'):
        raise RuntimeError('fixed-owner-operation-required')
    request = json.loads(sys.stdin.buffer.read(32769))
    if set(request) != {'owner', 'install', 'target', 'transaction'}:
        raise RuntimeError('fixed-owner-request-required')
    owner = pwd.getpwuid(os.geteuid())
    if request['owner'] != dict(name=owner.pw_name, uid=owner.pw_uid, gid=owner.pw_gid, home=owner.pw_dir):
        raise RuntimeError('owner-binding-changed')
    install = Path(request['install'])
    if not install.is_absolute() or install.resolve() != install:
        raise RuntimeError('install-binding-changed')
    # Code here is intentionally unprivileged. The root caller binds these
    # installed sources to the protected completed source plan before each run.
    specification = importlib.util.spec_from_file_location('recovery_installed_agent', install / 'bin/ods-host-agent.py')
    agent = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(agent)
    agent.INSTALL_DIR = install
    env = agent.load_env(install / '.env')
    target = request['target']
    if str(env.get('ODS_MODE') or 'local').lower() != 'local' or env.get('LLM_MODEL') != target['model']:
        raise RuntimeError('current-local-model-required')
    gguf = env.get('GGUF_FILE')
    if not isinstance(gguf, str) or not gguf or '\0' in gguf or any(c in gguf for c in '\r\n'):
        raise RuntimeError('current-model-file-required')
    if int(env.get('CTX_SIZE') or env.get('MAX_CONTEXT') or 0) != target['contextLength']:
        raise RuntimeError('current-context-changed')
    operation = sys.argv[1]
    if operation == 'inference':
        proof = agent._wait_for_model_readiness(env, model_id=target['model'], gguf_file=gguf,
            llm_model_name=target['model'], attempts=2, initial_delay=0, interval=1,
            return_proof=True, require_exact_context=True)
        if not proof:
            raise RuntimeError('current-inference-unproven')
        agent._verify_litellm_route(env, model='ods/current')
        print(json.dumps({'ok': True, 'operation': operation, 'target': target}))
        return
    if operation == 'reconcile':
        result = agent._reconcile_ods_managed_pixel_model(target['model'], target['contextLength'],
            max_tokens=target['maxTokens'], reasoning=target['reasoning'], borrowed_transaction=request['transaction'],
            image_input=target.get('imageInput', 'unknown'))
        if result != 'reconciled':
            raise RuntimeError('owner-reconciliation-incomplete')
    else:
        # Existing unprivileged, owner/PID-bound SIGTERM fallback is required.
        # No sudo privilege, root command input, or caller-selected service is
        # introduced. Restart=always is checked by the original helper.
        script = '''set -euo pipefail
INSTALL_DIR="$1"; owner="$2"; home="$3"; transaction="$4"
ODS_SUDO_AVAILABLE=false; INTERACTIVE=false; DRY_RUN=false
export INSTALL_DIR ODS_SUDO_AVAILABLE INTERACTIVE DRY_RUN
. "$INSTALL_DIR/installers/lib/sudo.sh"
. "$INSTALL_DIR/installers/lib/pixel-host-install.sh"
pixel_root="$(_ods_pixel_source_checkout "$owner" "$home" "$INSTALL_DIR/data/pixel/source-$(_ods_pixel_managed_source_ref "$owner" "$home")")"
_ods_pixel_restart_gateway_and_verify "$owner" "$home" "$pixel_root" "$transaction"
'''
        subprocess.run(['/usr/bin/bash', '-c', script, 'ods-recovery-restart', str(install), owner.pw_name,
                        owner.pw_dir, request['transaction']], check=True, timeout=300)
    print(json.dumps({'ok': True, 'operation': operation, 'target': target}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('fixed-owner-operation-failed', file=sys.stderr)
        raise SystemExit(1) from None
