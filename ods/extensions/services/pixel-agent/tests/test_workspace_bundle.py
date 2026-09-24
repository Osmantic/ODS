import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('bundle', Path(__file__).parents[1] / 'plugin/workspace-bundle.py')
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)


@unittest.skipUnless(os.name == 'posix', 'Actual Pixel execution is POSIX, including Windows WSL.')
class WorkspaceBundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = {'outputRoot': 'project/public', 'mappingPath': 'sources.json',
                        'files': [{'source': 'project/source.py', 'key': 'original.py', 'copyTo': 'source.py.txt'}]}
        (self.root / 'project').mkdir()
        self.source = self.root / 'project/source.py'
        self.source.write_bytes(b'original\n')
        self.count = 0

    def execute(self, generation=None):
        self.count += 1
        return B.bundle({'request': self.request, 'generation': generation or 'bundle-' + f'{self.count:032x}'}, str(self.root))

    def test_exact_bytes_every_generation_and_unchanged_previous_output(self):
        previous = []
        for source in [b'', b'x = 1', b'x = 1\n', b'x = 1\r\n', b'\xef\xbb\xbfhello\n', 'snowman \u2603\n'.encode(), b'print("\\n")\n']:
            with self.subTest(source=source):
                self.source.write_bytes(source)
                result = self.execute()
                self.assertEqual(result['status'], 'succeeded')
                self.assertTrue(result['decodedAndRawBytesEqual'])
                raw = self.root / result['generationPath'] / 'source.py.txt'
                mapping = self.root / result['mappingPath']
                self.assertEqual(raw.read_bytes(), source)
                self.assertEqual(json.loads(mapping.read_bytes())['original.py'].encode(), source)
                previous.append((raw, source))
                for target, original in previous:
                    self.assertEqual(target.read_bytes(), original)

    def test_existing_generation_cannot_be_reused_or_overwritten(self):
        generation = 'bundle-' + 'a' * 32
        result = self.execute(generation)
        raw = self.root / result['generationPath'] / 'source.py.txt'
        raw.write_bytes(b'owner edit')
        failed = self.execute(generation)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['written'], [])
        self.assertEqual(raw.read_bytes(), b'owner edit')

    def test_concurrent_owner_write_before_atomic_link_wins(self):
        original = B.os.link
        def race(source, destination, **kwargs):
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kwargs['dst_dir_fd'])
            os.write(fd, b'owner concurrent bytes'); os.close(fd)
            return original(source, destination, **kwargs)
        with patch.object(B.os, 'link', side_effect=race):
            result = self.execute()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['readbackVerified'])
        raw = self.root / result['generationPath'] / 'source.py.txt'
        self.assertEqual(raw.read_bytes(), b'owner concurrent bytes')
        self.assertEqual(result['written'], [])

    def test_source_changed_during_generation_never_returns_success(self):
        original = B.atomic_write
        def mutate(*args):
            original(*args)
            self.source.write_bytes(b'changed\n')
        with patch.object(B, 'atomic_write', side_effect=mutate):
            result = self.execute()
        self.assertEqual(result['error'], 'source-changed-during-generation')
        self.assertFalse(result['readbackVerified'])
        self.assertEqual(len(result['written']), 2)

    def test_partial_failure_retains_incomplete_generation_and_exact_written_paths(self):
        original = B.atomic_write
        def partial(root, name, *args):
            if name == 'sources.json': raise OSError('simulated disk failure')
            original(root, name, *args)
        with patch.object(B, 'atomic_write', side_effect=partial):
            result = self.execute()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['readbackVerified'])
        self.assertEqual(result['written'], [result['generationPath'] + '/source.py.txt'])
        self.assertEqual(self.execute()['status'], 'succeeded')

    def test_replaced_source_identity_is_rejected_even_when_bytes_match(self):
        original = B.atomic_write
        replaced = False
        def replace(*args):
            nonlocal replaced
            original(*args)
            if not replaced:
                replaced = True
                replacement = self.root / 'replacement'
                replacement.write_bytes(self.source.read_bytes())
                replacement.replace(self.source)
        with patch.object(B, 'atomic_write', side_effect=replace):
            result = self.execute()
        self.assertEqual(result['error'], 'source-changed-during-generation')
        self.assertFalse(result['readbackVerified'])

    def test_links_fifo_invalid_utf8_and_large_input_rejected(self):
        for kind in ['symlink', 'hardlink', 'fifo', 'invalid-utf8', 'large']:
            with self.subTest(kind=kind):
                if self.source.exists() or self.source.is_symlink(): self.source.unlink()
                other = self.root / 'other'; other.write_bytes(b'private')
                if kind == 'symlink': self.source.symlink_to(other)
                elif kind == 'hardlink': os.link(other, self.source)
                elif kind == 'fifo': os.mkfifo(self.source)
                elif kind == 'invalid-utf8': self.source.write_bytes(b'\xff')
                else: self.source.write_bytes(b'x' * (B.MAX_FILE + 1))
                self.assertEqual(self.execute()['status'], 'failed')

    def test_path_escape_duplicate_keys_and_outputs_rejected(self):
        for field in ['outputRoot', 'mappingPath']:
            for bad in ['../outside', '/tmp/outside', 'project/../outside', 'project\\outside', 'project/./file']:
                request = json.loads(json.dumps(self.request)); request[field] = bad
                with self.subTest(field=field, path=bad), self.assertRaises(B.BundleError): B.validate(request)
        for fault in ['duplicate-key', 'duplicate-output', 'case-collision']:
            request = json.loads(json.dumps(self.request))
            second = {'source': 'project/second.py', 'key': 'second.py', 'copyTo': 'second.txt'}
            if fault == 'duplicate-key': second['key'] = 'original.py'
            if fault == 'duplicate-output': second['copyTo'] = request['mappingPath']
            if fault == 'case-collision': second['copyTo'] = request['files'][0]['copyTo'].upper()
            request['files'].append(second)
            with self.subTest(fault=fault), self.assertRaises(B.BundleError): B.validate(request)

    def test_symlink_output_root_never_traversed(self):
        outside = self.root / 'outside'; outside.mkdir()
        (self.root / 'project/public').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.execute()['status'], 'failed')
        self.assertEqual(list(outside.iterdir()), [])

    def test_parent_swapped_during_staging_rejected_before_link(self):
        self.request['files'][0]['copyTo'] = 'raw/source.py.txt'
        original, swapped = B.os.fsync, False
        def swap(fd):
            nonlocal swapped
            original(fd)
            if not swapped:
                swapped = True
                generation = next((self.root / 'project/public').iterdir())
                parent = generation / 'raw'; parent.rename(generation / 'retained'); parent.mkdir()
        with patch.object(B.os, 'fsync', side_effect=swap): result = self.execute()
        self.assertEqual(result['error'], 'output-parent-changed')
        self.assertEqual(result['written'], [])

    def test_generation_can_publish_original_index_and_download_filenames(self):
        (self.root / 'project/index.html').write_bytes(b'<!doctype html><a href="sources.json">Sources</a>')
        self.request['files'].append({'source':'project/index.html', 'key':'index.html', 'copyTo':'index.html'})
        result = self.execute()
        self.assertEqual(result['status'], 'succeeded')
        generation = self.root / result['generationPath']
        self.assertEqual((generation / 'index.html').read_bytes(), (self.root / 'project/index.html').read_bytes())
        self.assertTrue((generation / 'sources.json').is_file())


if __name__ == '__main__':
    unittest.main()
