"""Release contract: all Milvus standalone storage belongs to a persistent mount.

Defaults are from milvus-io/milvus v2.4.5 configs/milvus.yaml. This checks
the shipped deployment plan; it is not a live collection recovery test.
"""
from pathlib import Path, PurePosixPath
import unittest

import yaml


class MilvusPersistenceTest(unittest.TestCase):
    def test_embedded_metadata_and_data_are_persistent(self):
        root = Path(__file__).resolve().parents[1]
        service = yaml.safe_load((root / 'extensions/library/services/milvus/compose.yaml').read_text())['services']['milvus']
        self.assertEqual(service['image'], 'milvusdb/milvus:v2.4.5')
        env = dict(item.split('=', 1) for item in service['environment'])
        self.assertEqual(env['ETCD_USE_EMBED'], 'true')
        self.assertEqual(env['COMMON_STORAGETYPE'], 'local')
        mounts = [PurePosixPath(volume.split(':')[1]) for volume in service['volumes']]
        paths = {
            'metadata': env.get('ETCD_DATA_DIR', 'default.etcd'),
            'vectors': env.get('LOCALSTORAGE_PATH', '/var/lib/milvus/data'),
            'message queue': env.get('ROCKSMQ_PATH', '/var/lib/milvus/rdb_data'),
        }
        for name, value in paths.items():
            with self.subTest(storage=name):
                path = PurePosixPath(value)
                self.assertTrue(path.is_absolute(), f'{name} uses container-relative storage: {path}')
                self.assertTrue(any(path.is_relative_to(mount) for mount in mounts), f'{name} is outside persistent mounts: {path}')


if __name__ == '__main__':
    unittest.main()
