from pathlib import Path
import posixpath
import unittest

import yaml


COMPOSE = Path(__file__).resolve().parents[1] / 'extensions/library/services/label-studio/compose.yaml'


def setting(environment, name):
    """Label Studio's get_env(): LABEL_STUDIO_<name>, then HEARTEX_<name>, then <name>."""
    if isinstance(environment, list):
        environment = dict(item.split('=', 1) for item in environment if '=' in item)
    for key in (f'LABEL_STUDIO_{name}', f'HEARTEX_{name}', name):
        if key in environment:
            return str(environment[key])
    return None


class LabelStudioLocalFiles(unittest.TestCase):
    def setUp(self):
        self.service = yaml.safe_load(COMPOSE.read_text(encoding='utf-8'))['services']['label-studio']
        self.environment = self.service.get('environment') or {}

    def test_local_file_serving_is_confined_to_a_mounted_dataset_directory(self):
        if (setting(self.environment, 'LOCAL_FILES_SERVING_ENABLED') or '').lower() not in ('1', 'true', 'yes', 'on'):
            return
        # Label Studio 1.22 defaults LOCAL_FILES_DOCUMENT_ROOT to "/", so any
        # signed-in user could register and download any readable file in the
        # container, including the SQLite database with every account.
        root = setting(self.environment, 'LOCAL_FILES_DOCUMENT_ROOT')
        self.assertIsNotNone(root, 'local file serving is on without LOCAL_FILES_DOCUMENT_ROOT')
        root = posixpath.normpath(root)
        self.assertTrue(posixpath.isabs(root))
        self.assertNotEqual(root, '/')
        targets = {posixpath.normpath(str(volume).split(':')[1]) for volume in self.service['volumes']}
        self.assertIn(root, targets, 'the document root must be its own bind mount')
        data_dir = posixpath.normpath(setting(self.environment, 'BASE_DATA_DIR') or '/label-studio/data')
        self.assertFalse(data_dir == root or data_dir.startswith(root + '/'),
                         'the document root must not contain the application database')


if __name__ == '__main__':
    unittest.main()
