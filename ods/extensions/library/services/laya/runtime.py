"""Choose bounded optional CUDA without changing the Portal model's settings."""
import gc
import logging
import os

LOG = logging.getLogger('ods.laya.runtime')
MIB = 1024 * 1024
MAX_CUDA_BYTES = 4096 * MIB
MIN_CUDA_BYTES = 2048 * MIB
HEADROOM_BYTES = 2048 * MIB


def select_device(torch, env):
    mode = env.get('LAYA_ACCELERATION', 'auto').strip()
    if mode not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('LAYA_ACCELERATION must be auto, cpu or cuda')
    reason, budget = 'owner-selected-cpu', 0
    if mode != 'cpu':
        reason = 'cuda-unavailable'
        if torch.cuda.is_available():
            try:
                free, total = torch.cuda.mem_get_info(0)
                budget = min(MAX_CUDA_BYTES, int(total * 0.25), max(0, free - HEADROOM_BYTES))
                if budget >= MIN_CUDA_BYTES:
                    torch.cuda.set_per_process_memory_fraction(budget / total, 0)
                    return {'device': 'cuda:0', 'allocatorBytes': budget, 'reason': 'cuda-with-headroom'}
                reason = 'insufficient-cuda-headroom'
            except RuntimeError:
                reason = 'cuda-initialization-failed'
        if mode == 'cuda':
            raise RuntimeError('Requested CUDA cannot satisfy the Laya memory budget: ' + reason)
    return {'device': 'cpu', 'allocatorBytes': 0, 'reason': reason}


def build_qualified_router(build_router, qualify, *, torch=None, env=None):
    if torch is None:
        import torch
    if env is None:
        env = os.environ
    selected = select_device(torch, env)
    env['LAYA_DEVICE'] = selected['device']
    if selected['device'].startswith('cuda'):
        # Avoid preloading all checkpoints before the memory budget takes effect.
        # One resident checkpoint leaves room for the main model; idle unload
        # returns allocations when the auxiliary extension is not being used.
        env.update(LAYA_PRELOAD='0', LAYA_MAX_LOADED='1', LAYA_IDLE_UNLOAD_SECONDS='60')
    LOG.warning('Laya runtime: device=%s allocator_bytes=%s reason=%s',
                selected['device'], selected['allocatorBytes'], selected['reason'])
    router = None
    try:
        router = build_router()
        qualify(router)
        return router
    except torch.cuda.OutOfMemoryError:
        # Retry only startup allocation exhaustion, not arbitrary model/runtime
        # bugs. Inference-time scoped CPU fallback is provided by pinned Laya.
        if not selected['device'].startswith('cuda') or env.get('LAYA_ACCELERATION', 'auto').strip() == 'cuda':
            raise
        if router is not None:
            router.unload()
        router = None
        gc.collect()
        torch.cuda.empty_cache()
        env['LAYA_DEVICE'] = 'cpu'
        LOG.warning('Laya startup allocation exhausted; qualifying CPU fallback')
    router = build_router()
    qualify(router)
    return router
