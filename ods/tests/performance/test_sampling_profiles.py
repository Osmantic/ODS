import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import probe
from test_probe import _Server, _manifest, _start_chunked, _chunk, _sse, _content, _finish, _done, _end_chunks


class TestSamplingProfiles(unittest.TestCase):
    @staticmethod
    def serve_ok(server, body):
        _start_chunked(server)
        for data in (_sse(_content('m1', 'hello')), _sse(_finish('m1')), _done()):
            _chunk(server, data)
        _end_chunks(server)

    def test_wire_and_saved_profile_preserve_each_backend_spelling(self):
        with _Server(self.serve_ok) as server, tempfile.TemporaryDirectory() as directory:
            for name, other in [('repeat_penalty', 'repetition_penalty'), ('repetition_penalty', 'repeat_penalty')]:
                with self.subTest(name=name):
                    sampling = {'temperature': 0.7, 'top_p': 0.8, 'top_k': 20,
                                'min_p': 0.0, 'presence_penalty': 1.5, name: 1.1}
                    output = Path(directory) / name
                    result = probe.run(_manifest(server.endpoint, sampling=sampling), str(output))
                    self.assertEqual(result['quality_pass'], 1)
                    wire = json.loads(server.requests[-1]['body'])
                    saved = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
                    expected = {**sampling, 'max_tokens': 128}
                    self.assertEqual(saved['manifest']['sampling'], expected)
                    self.assertEqual({key: wire[key] for key in expected}, expected)
                    self.assertNotIn(other, wire)
                    canonical = json.dumps(saved['manifest'], ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':'), allow_nan=False).encode('utf-8')
                    self.assertEqual(saved['manifest_sha256'], hashlib.sha256(canonical).hexdigest())
                    normalized = probe.validate_manifest(saved['manifest'])
                    self.assertEqual(probe._build_payload(normalized, normalized['tasks'][0]), wire)

    def test_only_sampling_change_changes_run_fingerprint(self):
        with _Server(self.serve_ok) as server, tempfile.TemporaryDirectory() as directory:
            saved = []
            for value in [1.0, 1.1]:
                output = Path(directory) / str(value)
                probe.run(_manifest(server.endpoint, sampling={'repeat_penalty': value}), str(output))
                saved.append(json.loads((output / 'manifest.json').read_text(encoding='utf-8')))
            self.assertNotEqual(saved[0]['manifest_sha256'], saved[1]['manifest_sha256'])
            saved[1]['manifest']['sampling']['repeat_penalty'] = 1.0
            self.assertEqual(saved[0]['manifest'], saved[1]['manifest'])

    def test_omission_preserves_existing_request_defaults(self):
        with _Server(self.serve_ok) as server, tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out'
            probe.run(_manifest(server.endpoint), str(output))
            wire = json.loads(server.requests[0]['body'])
            self.assertEqual(set(wire), {'model', 'messages', 'stream', 'stream_options', 'max_tokens'})
            self.assertEqual(wire['max_tokens'], 128)
            saved = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['manifest']['sampling'], {'max_tokens': 128})

    def test_invalid_profiles_fail_before_creating_run_or_sending_request(self):
        bounds = {'top_k': [-1, 2147483648, 20.0], 'min_p': [-0.01, 1.01],
                  'presence_penalty': [-2.01, 2.01], 'repeat_penalty': [0, -1],
                  'repetition_penalty': [0, -1]}
        bad_types = [True, False, None, '1', [], {}, float('nan'), float('inf'), float('-inf')]
        cases = [{key: value} for key, values in bounds.items() for value in values + bad_types]
        cases += [{'repeat_penalty': 1, 'repetition_penalty': 1}, {'model': 'other'},
                  {'messages': []}, {'stream': False}, {'top_k_extra': 20}]
        with _Server(self.serve_ok) as server, tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'must-not-exist'
            for sampling in cases:
                with self.subTest(sampling=sampling):
                    with self.assertRaises(probe.ManifestError):
                        probe.run(_manifest(server.endpoint, sampling=sampling), str(output))
                    self.assertFalse(output.exists())
            self.assertEqual(server.requests, [])

    def test_valid_boundaries_survive_normalization_and_payload(self):
        bounds = {'top_k': [0, 2147483647], 'min_p': [0, 1], 'presence_penalty': [-2, 2],
                  'repeat_penalty': [0.5, 1, 2], 'repetition_penalty': [0.5, 1, 2]}
        for key, values in bounds.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    normalized = probe.validate_manifest(_manifest('http://127.0.0.1:1/v1/chat/completions',
                                                                  sampling={key: value}))
                    self.assertEqual(probe._build_payload(normalized, normalized['tasks'][0])[key], value)


if __name__ == '__main__':
    unittest.main()
