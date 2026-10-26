# ODS fork patch for ghcr.io/berriai/litellm:v1.90.7 (digest-pinned in
# Dockerfile.codex).
#
# Codex CLI (Responses wire API) sends its system prompt as input items with
# role=developer, two in a row. The Qwen3.x llama.cpp chat template accepts
# only a single leading system/developer message and aborts the Jinja render
# with "System message must be at the beginning".
#
# Two LiteLLM paths are affected:
#
# 1. Native Responses forward. When the deployment's provider natively
#    supports /v1/responses (OpenAI-compatible, incl. llama.cpp), LiteLLM
#    forwards the request as-is to the backend's /v1/responses and the
#    developer, developer, user ... items go straight to the template.
#    Fix: merge the consecutive leading system/developer input items into
#    one leading system item in BaseLLMHTTPHandler.response_api_handler /
#    async_response_api_handler, right before the outgoing body is built.
#
# 2. Responses -> chat conversion. When LiteLLM converts a Responses
#    request into chat completions, it keeps the item roles, so the chat
#    request starts developer, developer, user, ... Fix: merge the
#    consecutive leading system/developer messages into one leading system
#    message at the OpenAIChatCompletion transform sites.
#
# Both helpers are strict no-ops for requests with zero or one leading
# system/developer message. If the upstream image is bumped, re-verify the
# anchors.
import sys

OPENAI_SITE = '/app/.venv/lib/python3.13/site-packages/litellm/llms/openai/openai.py'
RESPONSES_SITE = ('/app/.venv/lib/python3.13/site-packages/'
                  'litellm/llms/custom_httpx/llm_http_handler.py')


def _content_text(content):
    # Join the text parts of a chat message content list.
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ' '.join(
            str(p.get('text') or '') for p in content
            if isinstance(p, dict) and p.get('type') == 'text' and p.get('text')
        )
    return ''


CHAT_HELPER = '''

def ods_normalize_chat_messages(messages):
    # Merge consecutive leading system/developer messages into a single
    # leading system message. llama.cpp chat templates for Qwen3.x reject a
    # second leading system/developer message. No-op when there is at most
    # one leading system/developer message or the items are not dicts.
    if not isinstance(messages, list) or len(messages) < 2:
        return messages
    keep = 0
    while keep < len(messages):
        m = messages[keep]
        role = m.get('role') if isinstance(m, dict) else getattr(m, 'role', None)
        if role not in ('system', 'developer'):
            break
        keep += 1
    if keep < 2:
        return messages
    for m in messages[:keep]:
        if not isinstance(m, dict):
            return messages
    first = dict(messages[0])
    first['role'] = 'system'
    parts = [t for t in (_content_text(m.get('content')) for m in messages[:keep]) if t]
    first['content'] = '\\n\\n'.join(parts)
    return [first] + list(messages[keep:])


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ' '.join(
            str(p.get('text') or '') for p in content
            if isinstance(p, dict) and p.get('type') == 'text' and p.get('text')
        )
    return ''
'''

RESPONSES_HELPER = '''

def ods_normalize_responses_input(items):
    # Merge consecutive leading system/developer input items into a single
    # leading system item. llama.cpp chat templates for Qwen3.x reject a
    # second leading system/developer message. No-op when there is at most
    # one leading system/developer item or the items are not dicts.
    if not isinstance(items, list) or len(items) < 2:
        return items
    keep = 0
    while keep < len(items):
        it = items[keep]
        role = it.get('role') if isinstance(it, dict) else getattr(it, 'role', None)
        if role not in ('system', 'developer'):
            break
        keep += 1
    if keep < 2:
        return items
    for it in items[:keep]:
        if not isinstance(it, dict):
            return items
    first = dict(items[0])
    first['role'] = 'system'
    first['type'] = 'message'
    parts = []
    for it in items[:keep]:
        c = it.get('content')
        if isinstance(c, str):
            if c:
                parts.append({'type': 'input_text', 'text': c})
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict):
                    parts.append(dict(p))
    first['content'] = parts
    return [first] + list(items[keep:])
'''

# Patch the three OpenAIChatCompletion transform sites (sync completion,
# async acompletion, async streaming). Only relevant to the Responses ->
# chat conversion path.
openai_sites = {
    'completion': '                    data = provider_config.transform_request(\n                        model=model,\n                        messages=messages,\n                        optional_params=inference_params,\n',
    'acompletion': '        data = await provider_config.async_transform_request(\n            model=model,\n            messages=messages,\n            optional_params=optional_params,\n',
    'async_streaming': '        data = provider_config.transform_request(\n            model=model,\n            messages=messages,\n            optional_params=optional_params,\n            litellm_params=litellm_params,\n            headers=headers or {},\n        )\n        data["stream"] = True\n',
}

s = open(OPENAI_SITE).read()
if 'ods_normalize_chat_messages' in s:
    print('already patched', OPENAI_SITE)
else:
    for name, sig in openai_sites.items():
        if s.count(sig) != 1:
            sys.exit('%s transform anchor not unique (%d); aborting' % (name, s.count(sig)))
        s = s.replace(sig, sig.replace('messages=messages,', 'messages=ods_normalize_chat_messages(messages),', 1), 1)
    s += CHAT_HELPER
    open(OPENAI_SITE, 'w').write(s)
    print('patched', OPENAI_SITE)

# Patch the native Responses forward sites: the sync and async
# response_api_handler both build the outgoing body through the same
# transform_responses_api_request call, so the anchor appears exactly twice.
s = open(RESPONSES_SITE).read()
if 'ods_normalize_responses_input' in s:
    print('already patched', RESPONSES_SITE)
else:
    anchor = ('        data = responses_api_provider_config.transform_responses_api_request(\n'
              '            model=model,\n'
              '            input=input,\n')
    if s.count(anchor) != 2:
        sys.exit('responses transform anchor count is %d (expected 2); aborting' % s.count(anchor))
    s = s.replace(anchor, '        input = ods_normalize_responses_input(input)\n\n' + anchor)
    s += RESPONSES_HELPER
    open(RESPONSES_SITE, 'w').write(s)
    print('patched', RESPONSES_SITE)
