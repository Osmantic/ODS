import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('laya_files', Path(__file__).parents[1] / 'plugin/laya-files.py')
files = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(files)


@unittest.skipUnless(os.name == 'posix', 'Windows product execution uses WSL')
class LayaDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = {'path': 'tickets.csv', 'textColumn': 'text', 'idColumn': 'id'}
        (self.root / 'tickets.csv').write_text('id,text\nA,"Cobrança dupla, reembolso"\nB,"Erro ao abrir\na página"\n', encoding='utf-8')

    def read(self, source=None):
        return files.run({'operation': 'read', 'source': source or self.source}, str(self.root))['source']

    def write_request(self):
        snapshot = self.read()
        return {'operation': 'write', 'source': self.source, 'outputDirectory': 'reports', 'generation': 'laya-' + 'a'*32,
                'report': {'schemaVersion': 1, 'advisory': True, 'completeContext': True,
                           'source': {key: snapshot[key] for key in ('path', 'bytes', 'sha256')},
                           'questions': [{'id': 'category', 'type': 'choice'}],
                           'items': [{'id': item['id'], 'sourceId': item['sourceId'], 'answers': {'category': {
                               'type': 'choice', 'choice': 'billing', 'confidence': 0.7, 'answerConfidence': 0.8,
                               'probabilities': {'billing': 0.8, 'other': 0.2}}}} for item in snapshot['items']]}}

    def test_quoted_csv_unicode_newlines_and_ids_are_preserved(self):
        result = self.read()
        self.assertEqual(result['items'], [{'id': 'r1', 'sourceId': 'A', 'text': 'Cobrança dupla, reembolso'},
                                           {'id': 'r2', 'sourceId': 'B', 'text': 'Erro ao abrir\na página'}])
        self.assertEqual(result['sha256'], hashlib.sha256((self.root / 'tickets.csv').read_bytes()).hexdigest())

    def test_json_jsonl_tsv_and_automatic_numeric_ids(self):
        for name, text in [('a.json', '[{"text":"Bom dia"}]'), ('a.jsonl', '{"text":"Bom dia"}\n'), ('a.tsv', 'text\nBom dia\n')]:
            (self.root / name).write_text(text, encoding='utf-8')
            self.assertEqual(self.read({'path': name, 'textColumn': 'text'})['items'][0]['sourceId'], '1')

    def test_rejects_lossy_or_ambiguous_sources(self):
        for text in ['id,text\nA,hello\nA,again', 'id,text\nA,hello,extra', 'id,id\nA,hello',
                     'id,text\nA,', 'id,text\nA,"unterminated', 'id,text\nA,hello\n\n']:
            (self.root / 'tickets.csv').write_text(text, encoding='utf-8')
            with self.assertRaises((files.DatasetError, files.csv.Error)):
                self.read()
        (self.root / 'a.json').write_text('[{"text":"first", "text":"second"}]', encoding='utf-8')
        with self.assertRaisesRegex(files.DatasetError, 'duplicate-json-field'):
            self.read({'path': 'a.json', 'textColumn': 'text'})

    def test_limits_fail_without_partial_rows(self):
        for text in ['id,text\n' + ''.join(f'{i},row\n' for i in range(129)), 'id,text\nA,' + 'x'*65536]:
            (self.root / 'tickets.csv').write_text(text, encoding='utf-8')
            with self.assertRaises(files.DatasetError):
                self.read()

    def test_symlinks_hardlinks_and_path_traversal_are_refused(self):
        (self.root / 'link.csv').symlink_to(self.root / 'tickets.csv')
        for path in ['link.csv', '../tickets.csv', '/tickets.csv', '.hidden.csv']:
            with self.assertRaises((files.DatasetError, OSError)):
                self.read({**self.source, 'path': path})
        os.link(self.root / 'tickets.csv', self.root / 'hard.csv')
        with self.assertRaises(files.DatasetError):
            self.read()

    def test_changed_source_is_not_saved_as_current(self):
        request = self.write_request()
        (self.root / 'tickets.csv').write_text('id,text\nA,replaced\n', encoding='utf-8')
        with self.assertRaisesRegex(files.DatasetError, 'source-changed'):
            files.run(request, str(self.root))
        self.assertFalse((self.root / 'reports').exists())

    def test_create_only_reports_include_byte_hashes_and_full_alternatives(self):
        request = self.write_request()
        result = files.run(request, str(self.root))
        self.assertTrue(result['readbackVerified'])
        for item in result['outputs']:
            data = (self.root / item['path']).read_bytes()
            self.assertEqual(item['bytes'], len(data))
            self.assertEqual(item['sha256'], hashlib.sha256(data).hexdigest())
        saved = json.loads((self.root / result['outputs'][1]['path']).read_text())
        self.assertEqual(saved, request['report'])
        before = [(self.root / x['path']).read_bytes() for x in result['outputs']]
        with self.assertRaises(FileExistsError):
            files.run(request, str(self.root))
        self.assertEqual(before, [(self.root / x['path']).read_bytes() for x in result['outputs']])

    def test_output_symlink_parent_cannot_escape_workspace(self):
        (self.root / 'reports').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            files.run(self.write_request(), str(self.root))
        self.assertFalse((self.root / ('laya-' + 'a'*32)).exists())

    def test_csv_formula_ids_are_escaped_but_json_is_exact(self):
        (self.root / 'tickets.csv').write_text('id,text\n=1+1,refund\n', encoding='utf-8')
        result = files.run(self.write_request(), str(self.root))
        self.assertIn("'=1+1", (self.root / result['outputs'][0]['path']).read_text())
        self.assertEqual(json.loads((self.root / result['outputs'][1]['path']).read_text())['items'][0]['sourceId'], '=1+1')

    def test_score_and_yes_probability_remain_numeric_without_invented_confidence(self):
        request = self.write_request()
        request['report']['questions'] = [{'id': 'priority', 'type': 'score'}, {'id': 'urgent', 'type': 'noul'}]
        for item in request['report']['items']:
            item['answers'] = {'priority': {'type': 'score', 'score': 0, 'confidence': 0.6},
                               'urgent': {'type': 'noul', 'probability': 0.25}}
        result = files.run(request, str(self.root))
        self.assertEqual((self.root / result['outputs'][0]['path']).read_text(),
                         'id,priority,priority_confidence,priority_answer_confidence,urgent,urgent_confidence,urgent_answer_confidence\n'
                         'A,0,0.6,,0.25,,\nB,0,0.6,,0.25,,\n')


if __name__ == '__main__':
    unittest.main()
