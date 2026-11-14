"""CrewAI Studio must keep its SQLite database in the ./data/crewai mount.

The pinned image runs `streamlit run ./app/app.py` from /CrewAI-Studio, and
its db_utils.py opens DB_NAME = 'crewai.db' relative to that directory. The
recipe mounts ./data/crewai at /app, so the database has to be linked there
or every agent, task and crew is lost when the container is recreated.
"""
from pathlib import Path
import os
import shutil
import sqlite3
import subprocess
import tempfile
import textwrap
import unittest

import yaml


COMPOSE = Path(__file__).resolve().parents[1] / 'extensions/library/services/crewai/compose.yaml'
IMAGE_WORKDIR = '/CrewAI-Studio'
DATABASE = 'crewai.db'


def service():
    return yaml.safe_load(COMPOSE.read_text(encoding='utf-8'))['services']['crewai']


def data_mount_target(definition):
    for volume in definition['volumes']:
        source, target = str(volume).split(':')[:2]
        if source.rstrip('/') == './data/crewai':
            return target
    raise AssertionError('crewai has no ./data/crewai mount')


class CrewAiPersistence(unittest.TestCase):
    def test_keeps_the_image_working_directory(self):
        # app.py loads img/crewai_logo.png and .streamlit/ relative to it.
        self.assertEqual(service().get('working_dir', IMAGE_WORKDIR), IMAGE_WORKDIR)

    @unittest.skipIf(os.name == 'nt', 'needs POSIX symlinks')
    def test_database_written_by_studio_lands_in_the_data_mount(self):
        definition = service()
        command = definition.get('command')
        self.assertIsInstance(command, list, 'the image command leaves crewai.db in the container layer')
        self.assertEqual(command[:2], ['sh', '-c'])
        mount = data_mount_target(definition)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workdir = root / 'CrewAI-Studio'
            data = root / 'data'
            bin_dir = root / 'bin'
            for path in (workdir, data, bin_dir):
                path.mkdir()
            # Stand-in for Streamlit: open the database exactly as db_utils.py does.
            fake = bin_dir / 'streamlit'
            fake.write_text(textwrap.dedent(f"""\
                #!{shutil.which('python3') or 'python3'}
                import sqlite3
                with sqlite3.connect({DATABASE!r}) as connection:
                    connection.execute('CREATE TABLE IF NOT EXISTS entities (id TEXT)')
                    connection.execute("INSERT INTO entities VALUES ('crew')")
                """))
            fake.chmod(0o755)
            script = command[2].replace(mount.rstrip('/') + '/', f'{data}/')
            env = {**os.environ, 'PATH': f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
            for _ in range(2):  # a container restart runs the command again
                subprocess.run(['sh', '-c', script], cwd=workdir, env=env, check=True)

            with sqlite3.connect(data / DATABASE) as connection:
                rows = connection.execute('SELECT id FROM entities').fetchall()
            self.assertEqual(rows, [('crew',), ('crew',)])


if __name__ == '__main__':
    unittest.main()
