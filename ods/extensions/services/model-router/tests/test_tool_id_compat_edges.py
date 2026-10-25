import copy
import unittest
from app import tool_id_compat as compat
from test_tool_id_compat import _assistant, _tool, LONG_ID

class ReviewerEdges(unittest.TestCase):
    def test_malformed_mixed_history_is_untouched(self):
        malformed = [None, {'role':'assistant','tool_calls':{}},
                     {'role':'assistant','tool_calls':[None]},
                     {'role':'assistant','tool_calls':[{'id':None}]},
                     {'role':'assistant','tool_calls':[{'id':3}]},
                     {'role':'assistant','tool_calls':[{}]},
                     {'role':'tool'}, {'role':'tool','tool_call_id':9}]
        for extra in malformed:
            with self.subTest(extra=extra):
                original=[_assistant(LONG_ID),_tool(LONG_ID),extra]
                snapshot=copy.deepcopy(original)
                out,changed=compat.normalize_history_tool_ids(original)
                self.assertFalse(changed)
                self.assertIs(out,original)
                self.assertEqual(original,snapshot)

    def test_nemo_family_variants_and_boundaries(self):
        for name in ('Mistral-Nemo-Instruct-2407-Q4_K_M.gguf',
                     'mistralai/Mistral-Nemo-Instruct-2407', 'mistral-nemo-instruct'):
            self.assertTrue(compat.route_requires_strict9_tool_ids(name,'llama-server'))
        for name in ('FakeMistral-Nemo','Mistral-Nemotron','Nemotron','Mistral-7B'):
            self.assertFalse(compat.route_requires_strict9_tool_ids(name,'llama-server'))

    def test_unencodable_id_preserves_baseline(self):
        original=[_assistant('call_\ud800'),_tool('call_\ud800')]
        out,changed=compat.normalize_history_tool_ids(original)
        self.assertFalse(changed)
        self.assertIs(out,original)

    def test_newline_is_not_valid_nine_character_id(self):
        original=[_assistant('abc123XYZ\n'),_tool('abc123XYZ\n')]
        out,changed=compat.normalize_history_tool_ids(original)
        self.assertTrue(changed)
        self.assertEqual(len(out[0]['tool_calls'][0]['id']),9)

    def test_existing_valid_id_collision_cannot_steal_pair(self):
        original=[_assistant(LONG_ID),_tool(LONG_ID),_assistant('abc123XYZ'),_tool('abc123XYZ')]
        out,changed=compat.normalize_history_tool_ids(original)
        self.assertTrue(changed)
        self.assertEqual(out[2],original[2])
        self.assertEqual(out[3],original[3])
