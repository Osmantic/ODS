"""Use the interactive host/native transaction for background model promotion.

The host journal and protected coordinator remain the authorities. This adapter
never retries a mutation, repairs a journal, or writes native Pixel configuration.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import sys


class PromotionError(RuntimeError):
    pass


class RuntimeNotReady(PromotionError):
    """No Pixel apply was attempted; the same held transaction may roll back."""


def configured(host, env):
    if env.get('PIXEL_OPENWEBUI_KEY'):
        return True
    if env.get('PIXEL_NATIVE_CONFIG_PATH') or (host.INSTALL_DIR / 'data/pixel-native').exists():
        raise PromotionError('native-model-controller-configuration-required')
    return False


def transaction(host, env, identity):
    if identity == 'unmanaged' and not configured(host, env):
        return None
    journal = host._read_pixel_model_journal()
    if (not re.fullmatch('[a-f0-9]{64}', identity or '') or journal is None
            or journal['transactionId'] != identity or journal['phase'] != 'held'
            or journal['target'] is not None):
        raise PromotionError('native-model-transaction-recovery-required')
    current = host._PixelModelTransaction(env)
    current.id = identity
    current.previous = journal['previous']
    current.journal = journal
    current.verify_held()
    return current


def begin(host, env):
    if not configured(host, env):
        return 'unmanaged'
    # Refuse a stale previous contract before taking a new hold or changing
    # inference. An old bootstrap mismatch requires explicit recovery.
    status = host._runtime_model_control('model-status', config=env)
    if status['pending'] or not host._prove_pixel_model_contract(env, status['contract']):
        raise PromotionError('native-model-previous-contract-unverified')
    current = host._begin_pixel_model_transaction(env)
    if current is None or current.previous != status['contract']:
        raise PromotionError('native-model-previous-contract-changed')
    return current.id


def restart(host):
    root = host.INSTALL_DIR
    host._restart_macos_native_llama_server(root / '.env', root / 'bin/llama-server',
        Path.home() / 'Library/Logs/ODS/llama-server.log', root / 'data/.llama-server.pid')


def promote(host, env, identity, gguf, context):
    current = transaction(host, env, identity)
    if env.get('GGUF_FILE') != gguf or env.get('CTX_SIZE') != str(context):
        raise PromotionError('native-model-promotion-settings-changed')
    before = host._pixel_model_config_digests()
    if current is not None:
        # Only bootstrap's captured env/models.ini changes belong to this
        # participant. Other consumer changes require explicit recovery.
        original = current.journal['before']
        if (set(before) != set(original) or 'unavailable' in before.values()
                or any(value != original[name] for name, value in before.items()
                       if name not in {'.env', 'config/llama-server/models.ini'})):
            raise PromotionError('native-model-promotion-host-state-changed')
    model = env.get('LLM_MODEL') or gguf
    try:
        restart(host)
        proof = host._wait_for_model_readiness(env, model_id=model, gguf_file=gguf,
            llm_model_name=model, return_proof=True, require_exact_context=True)
        if (not isinstance(proof, dict) or proof.get('contextVerified') is not True
                or proof.get('contextLength') != context
                or not host._pixel_local_identity_matches(env, proof.get('identity'), gguf)):
            raise RuntimeError('native-model-runtime-proof-failed')
    except Exception as error:
        # The shell may restore its captured env/models.ini only before any
        # Pixel apply. Re-prove the same hold before authorizing that rollback.
        if current is not None:
            current.verify_held()
        if host._pixel_model_config_digests() != before:
            raise PromotionError('native-model-promotion-host-state-changed') from error
        raise RuntimeNotReady('native-model-runtime-proof-failed') from error
    if host._pixel_model_config_digests() != before:
        raise PromotionError('native-model-promotion-host-state-changed')
    catalog_id, record = host._catalog_model_for_current_env(env)
    publish_route = host._normal_switchboard_mode(env) == 'enabled'
    if publish_route:
        host._publish_activation_route(env, catalog_id, proof, {
            'chat': True, 'tools': bool(record.get('tools')), 'vision': bool(record.get('vision')),
            'agentViable': host._model_agent_viable(record, context),
        })
    after_route = host._pixel_model_config_digests()
    if (set(before) != set(after_route) or 'unavailable' in after_route.values()
            or any(value != before[name] for name, value in after_route.items()
                   if not (publish_route and name == 'data/model-state.json'))):
        raise PromotionError('native-model-promotion-host-state-changed')
    if current is not None:
        current.verify_held()
        target = dict(model=proof['identity'], contextLength=context,
            maxTokens=host._pixel_max_tokens_for_context(context),
            reasoning=host._pixel_model_reasoning_capable(model, env),
            imageInput=host._pixel_model_image_input(catalog_id))
        applied_host_state = after_route
        current.apply(target)
        if host._pixel_model_config_digests() != applied_host_state:
            raise PromotionError('native-model-promotion-host-state-changed')
        current.finish('commit')


def rollback(host, env, identity, *, restart_runtime=False):
    current = transaction(host, env, identity)
    before = host._pixel_model_config_digests()
    if current is not None and (before != current.journal['before'] or 'unavailable' in before.values()):
        raise PromotionError('native-model-rollback-host-state-changed')
    if restart_runtime:
        restart(host)
    previous = current.previous if current is not None else dict(
        model=env.get('GGUF_FILE'), contextLength=int(env.get('CTX_SIZE') or 0))
    if restart_runtime:
        # A restored model may still be loading. Use the interactive switch's
        # bounded readiness wait before its final exact-contract check.
        gguf = env.get('GGUF_FILE') or ''
        model = env.get('LLM_MODEL') or gguf
        proof = host._wait_for_model_readiness(env, model_id=model, gguf_file=gguf,
            llm_model_name=model, return_proof=True, require_exact_context=True)
        if (not isinstance(proof, dict) or proof.get('contextVerified') is not True
                or proof.get('contextLength') != previous['contextLength']
                or not host._pixel_local_identity_matches(env, proof.get('identity'), previous['model'])):
            raise PromotionError('native-model-rollback-unverified')
    if (not host._prove_pixel_model_contract(env, previous)
            or host._pixel_model_config_digests() != before):
        raise PromotionError('native-model-rollback-unverified')
    if current is not None:
        current.verify_held()
        current.finish('rollback')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('begin', 'promote', 'rollback', 'release-unchanged'))
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--transaction', default='')
    parser.add_argument('--gguf-file', default='')
    parser.add_argument('--context', type=int, default=0)
    args = parser.parse_args(argv)
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise PromotionError('native-macos-owner-required')
    root = args.install_dir.resolve(strict=True)
    spec = importlib.util.spec_from_file_location('native_promotion_host', root / 'bin/ods-host-agent.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    host.INSTALL_DIR, host.DATA_DIR, host.GPU_BACKEND = root, root / 'data', 'apple'
    env = host.load_env(root / '.env')
    if args.operation == 'begin':
        print(begin(host, env))
    elif args.operation == 'promote':
        promote(host, env, args.transaction, args.gguf_file, args.context)
    else:
        rollback(host, env, args.transaction, restart_runtime=args.operation == 'rollback')


if __name__ == '__main__':
    try:
        main()
    except RuntimeNotReady:
        print('native-model-runtime-proof-failed; verified rollback required', file=sys.stderr)
        raise SystemExit(20) from None
    except PromotionError as error:
        print(str(error) + '; recovery evidence preserved', file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        # Do not echo subprocess output, credentials or arbitrary exception text.
        print('native-model-promotion-unconfirmed; recovery evidence preserved', file=sys.stderr)
        raise SystemExit(1) from None
