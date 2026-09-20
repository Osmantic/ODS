"""Per-model-call eligibility. Never replay tools or modify message history."""
import copy
from .store import StoreError


def select_candidates(config,payload):
    if not config['enabled']:
        raise StoreError('provider-routing-disabled')
    by_id = {p['id']:p for p in config['providers']}
    leader = by_id[config['roles']['leader']]
    ordered = [leader]+[by_id[pid] for pid in config['roles']['backups']]
    tools = bool(payload.get('tools')) or any(m.get('role') == 'tool' or m.get('tool_calls')
        for m in payload['messages'])
    vision = any(part.get('type') == 'image_url' for m in payload['messages']
        if isinstance(m.get('content'),list) for part in m['content'])
    budget = payload.get('max_tokens',payload.get('max_completion_tokens',min(1024,leader['maxOutputTokens'])))
    if type(budget) is not int or not 1 <= budget <= leader['maxOutputTokens']:
        raise StoreError('output-limit-exceeded')
    selected,skipped = [],[]
    for provider in ordered:
        if provider['kind'] == 'cloud' and not config['policy']['allowCloud']:
            raise StoreError('cloud-not-authorized')
        if provider['model'] in ('ods/pixel','pixel/default','openclaw/default'):
            raise StoreError('provider-route-cycle')
        incompatible = (not provider['enabled'] or provider['contextTokens'] < leader['contextTokens']
            or provider['maxOutputTokens'] < budget or tools and not provider['supportsTools']
            or vision and not provider['supportsVision'] or leader['reasoning'] and not provider['reasoning'])
        if incompatible:
            if provider['id'] == leader['id']:
                raise StoreError('leader-incompatible')
            skipped.append({'providerId':provider['id'],'reason':'incompatible-backup'})
        else:
            selected.append(copy.deepcopy(provider))
    return selected[:config['policy']['maxAttempts']],skipped
