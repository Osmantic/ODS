"""Unit tests for the ODS LiteLLM Codex patch (ods_codex_chat_patch.py).

Covers the two message-merge helpers (the canonical implementation the
Dockerfile injects into the LiteLLM image) and the anchor-based apply_patch
rewriting against synthetic package files.
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

PATCH = Path(__file__).resolve().parents[2].joinpath('extensions', 'services', 'litellm', 'ods_codex_chat_patch.py')
SPEC = importlib.util.spec_from_file_location('ods_codex_chat_patch', PATCH)
PATCHES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCHES)


def chat(role, content):
    return {'role': role, 'content': content}


class ChatMessageMergeTests(unittest.TestCase):
    def test_merges_two_leading_developer_messages(self):
        out = PATCHES.ods_normalize_chat_messages([
            chat('developer', 'A'),
            chat('developer', 'B'),
            chat('user', 'hello'),
        ])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['role'], 'system')
        self.assertEqual(out[0]['content'], 'A\n\nB')
        self.assertEqual(out[1], {'role': 'user', 'content': 'hello'})

    def test_merges_system_then_developer_and_content_lists(self):
        out = PATCHES.ods_normalize_chat_messages([
            chat('system', [{'type': 'text', 'text': 'X'}, {'type': 'image_url'}]),
            chat('developer', [{'type': 'text', 'text': 'Y'}, {'type': 'text', 'text': 'Z'}]),
            chat('user', 'hi'),
        ])
        self.assertEqual(out[0]['role'], 'system')
        self.assertEqual(out[0]['content'], 'X\n\nY Z')

    def test_single_leading_system_is_untouched(self):
        msgs = [chat('system', 'only'), chat('user', 'hi')]
        out = PATCHES.ods_normalize_chat_messages(msgs)
        self.assertIs(out, msgs)

    def test_no_leading_system_is_untouched(self):
        msgs = [chat('user', 'hi'), chat('assistant', 'yo')]
        out = PATCHES.ods_normalize_chat_messages(msgs)
        self.assertIs(out, msgs)

    def test_trailing_system_is_not_merged(self):
        msgs = [chat('user', 'hi'), chat('system', 'late')]
        out = PATCHES.ods_normalize_chat_messages(msgs)
        self.assertIs(out, msgs)

    def test_single_message_list_is_untouched(self):
        msgs = [chat('developer', 'A')]
        out = PATCHES.ods_normalize_chat_messages(msgs)
        self.assertIs(out, msgs)

    def test_empty_and_non_list_inputs_pass_through(self):
        self.assertIsNone(PATCHES.ods_normalize_chat_messages(None))
        self.assertEqual(PATCHES.ods_normalize_chat_messages('str'), 'str')

    def test_input_is_not_mutated(self):
        msgs = [chat('developer', 'A'), chat('developer', 'B'), chat('user', 'hi')]
        snapshot = [dict(m) for m in msgs]
        PATCHES.ods_normalize_chat_messages(msgs)
        self.assertEqual(msgs, snapshot)


class ResponsesInputMergeTests(unittest.TestCase):
    def _item(self, role, text):
        return {'type': 'message', 'role': role, 'content': [{'type': 'input_text', 'text': text}]}

    def test_merges_two_leading_developer_items(self):
        out = PATCHES.ods_normalize_responses_input([
            self._item('developer', 'A'),
            self._item('developer', 'B'),
            self._item('user', 'hi'),
        ])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['role'], 'system')
        self.assertEqual(out[0]['type'], 'message')
        self.assertEqual(out[0]['content'], [
            {'type': 'input_text', 'text': 'A'},
            {'type': 'input_text', 'text': 'B'},
        ])
        self.assertEqual(out[1], self._item('user', 'hi'))

    def test_single_leading_system_is_untouched(self):
        items = [self._item('system', 'S'), self._item('user', 'hi')]
        out = PATCHES.ods_normalize_responses_input(items)
        self.assertIs(out, items)

    def test_single_item_list_is_untouched(self):
        items = [self._item('developer', 'A')]
        out = PATCHES.ods_normalize_responses_input(items)
        self.assertIs(out, items)


OPENAI_SOURCE = '''def completion():
    if True:
        if True:
            if True:
                if True:
                    data = provider_config.transform_request(
                        model=model,
                        messages=messages,
                        optional_params=inference_params,
                        other=1,
                    )
    return data

async def acompletion():
        data = await provider_config.async_transform_request(
            model=model,
            messages=messages,
            optional_params=optional_params,
        )
    return data

async def astreaming():
        data = provider_config.transform_request(
            model=model,
            messages=messages,
            optional_params=optional_params,
            litellm_params=litellm_params,
            headers=headers or {},
        )
        data["stream"] = True
    return data
'''

RESPONSES_SOURCE = '''def response_api_handler():
    if True:
        data = responses_api_provider_config.transform_responses_api_request(
            model=model,
            input=input,
        )
    return data

async def async_response_api_handler():
    if True:
        data = responses_api_provider_config.transform_responses_api_request(
            model=model,
            input=input,
        )
    return data
'''


class ApplyPatchTests(unittest.TestCase):
    def test_apply_patch_rewrites_anchors_and_appends_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            openai = Path(tmp) / 'openai.py'
            responses = Path(tmp) / 'llm_http_handler.py'
            openai.write_text(OPENAI_SOURCE)
            responses.write_text(RESPONSES_SOURCE)
            PATCHES.apply_patch(str(openai), str(responses))
            out = openai.read_text()
            self.assertEqual(out.count('messages=ods_normalize_chat_messages(messages),'), 3)
            self.assertIn('def ods_normalize_chat_messages(messages):', out)
            self.assertIn('def _content_text(content):', out)
            # The injected source is the canonical implementation itself.
            self.assertIn('parts = [t for t in (_content_text(m.get(\'content\')) for m in messages[:keep]) if t]', out)
            r = responses.read_text()
            self.assertEqual(r.count('input = ods_normalize_responses_input(input)'), 2)
            self.assertIn('def ods_normalize_responses_input(items):', r)

    def test_apply_patch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            openai = Path(tmp) / 'openai.py'
            responses = Path(tmp) / 'llm_http_handler.py'
            openai.write_text(OPENAI_SOURCE)
            responses.write_text(RESPONSES_SOURCE)
            PATCHES.apply_patch(str(openai), str(responses))
            openai_first = openai.read_text()
            responses_first = responses.read_text()
            PATCHES.apply_patch(str(openai), str(responses))
            self.assertEqual(openai.read_text(), openai_first)
            self.assertEqual(responses.read_text(), responses_first)

    def test_apply_patch_aborts_on_drifted_openai_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            openai = Path(tmp) / 'openai.py'
            responses = Path(tmp) / 'llm_http_handler.py'
            # A second occurrence of the acompletion anchor: upstream drift.
            acompletion_dup = '''
async def acompletion_dup():
        data = await provider_config.async_transform_request(
            model=model,
            messages=messages,
            optional_params=optional_params,
        )
    return data
'''
            openai.write_text(OPENAI_SOURCE + acompletion_dup)
            responses.write_text(RESPONSES_SOURCE)
            with self.assertRaises(SystemExit) as ctx:
                PATCHES.apply_patch(str(openai), str(responses))
            self.assertIn('acompletion transform anchor not unique', ctx.exception.args[0])

    def test_apply_patch_aborts_on_drifted_responses_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            openai = Path(tmp) / 'openai.py'
            responses = Path(tmp) / 'llm_http_handler.py'
            openai.write_text(OPENAI_SOURCE)
            # Only one of the two expected transform sites survived upstream.
            responses.write_text(RESPONSES_SOURCE.split('async def async_response_api_handler')[0])
            with self.assertRaises(SystemExit) as ctx:
                PATCHES.apply_patch(str(openai), str(responses))
            self.assertIn('responses transform anchor count is 1 (expected 2)', ctx.exception.args[0])


if __name__ == '__main__':
    unittest.main()
