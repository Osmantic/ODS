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
# The ods_normalize_* functions below are the canonical implementation.
# apply_patch() injects them into the installed LiteLLM package as the
# inspected source of these functions, so the image and the unit tests in
# tests/contracts/test-litellm-codex-chat-patch.py exercise the same code.
# They are strict no-ops for requests with zero or one leading
# system/developer message. If the upstream image is bumped, re-verify the
# anchors.
import inspect
import sys

OPENAI_SITE = '/app/.venv/lib/python3.13/site-packages/litellm/llms/openai/openai.py'
RESPONSES_SITE = ('/app/.venv/lib/python3.13/site-packages/'
                  'litellm/llms/custom_httpx/llm_http_handler.py')


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
    first['content'] = '\n\n'.join(parts)
    return [first] + list(messages[keep:])


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


# The three OpenAIChatCompletion transform sites (sync completion, async
# acompletion, async streaming). Only relevant to the Responses -> chat
# conversion path. Each signature contains the `messages=messages,` keyword
# that apply_patch wraps in the chat normalization.
OPENAI_SITES = {
    'completion': '                    data = provider_config.transform_request(\n                        model=model,\n                        messages=messages,\n                        optional_params=inference_params,\n',
    'acompletion': '        data = await provider_config.async_transform_request(\n            model=model,\n            messages=messages,\n            optional_params=optional_params,\n',
    'async_streaming': '        data = provider_config.transform_request(\n            model=model,\n            messages=messages,\n            optional_params=optional_params,\n            litellm_params=litellm_params,\n            headers=headers or {},\n        )\n        data["stream"] = True\n',
}

# The sync and async response_api_handler both build the outgoing body
# through the same transform call, so this anchor appears exactly twice in
# the Responses handler.
RESPONSES_ANCHOR = ('        data = responses_api_provider_config.transform_responses_api_request(\n'
                    '            model=model,\n'
                    '            input=input,\n')


def apply_patch(openai_site=OPENAI_SITE, responses_site=RESPONSES_SITE):
    """Patch the installed LiteLLM in place. Image build only; idempotent.

    Raises SystemExit when an anchor is not unique (upstream drifted).
    """
    # The injected helper is the inspected source of the canonical functions
    # above, so the shipped image and the unit tests stay in sync.
    chat_helpers = '\n\n'.join(
        inspect.getsource(fn) for fn in (ods_normalize_chat_messages, _content_text)
    )
    responses_helper = inspect.getsource(ods_normalize_responses_input)

    with open(openai_site) as f:
        s = f.read()
    if 'ods_normalize_chat_messages' in s:
        print('already patched', openai_site)
    else:
        for name, sig in OPENAI_SITES.items():
            if s.count(sig) != 1:
                sys.exit('%s transform anchor not unique (%d); aborting' % (name, s.count(sig)))
            s = s.replace(sig, sig.replace('messages=messages,', 'messages=ods_normalize_chat_messages(messages),', 1), 1)
        s += '\n\n' + chat_helpers
        with open(openai_site, 'w') as f:
            f.write(s)
        print('patched', openai_site)

    with open(responses_site) as f:
        s = f.read()
    if 'ods_normalize_responses_input' in s:
        print('already patched', responses_site)
    else:
        if s.count(RESPONSES_ANCHOR) != 2:
            sys.exit('responses transform anchor count is %d (expected 2); aborting' % s.count(RESPONSES_ANCHOR))
        s = s.replace(RESPONSES_ANCHOR, '        input = ods_normalize_responses_input(input)\n\n' + RESPONSES_ANCHOR)
        s += '\n\n' + responses_helper
        with open(responses_site, 'w') as f:
            f.write(s)
        print('patched', responses_site)


if __name__ == '__main__':
    apply_patch()
