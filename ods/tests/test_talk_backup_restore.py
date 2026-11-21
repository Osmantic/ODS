"""Real backup/restore CLIs and WAL databases; Compose inventory is a fixture seam."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

ODS = Path(__file__).resolve().parents[1]
API = ODS / 'extensions/services/dashboard-api'


def load(name):
    spec = importlib.util.spec_from_file_location(name, API / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TalkBackupRestore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ods-talk-backup-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.source = self.install('source')
        self.destination = self.install('destination')
        self.bin = self.area / 'bin'
        self.bin.mkdir()
        # Exercise production stop_containers config/project/down/remaining
        # logic. This script never invokes a host Docker daemon.
        docker = self.bin / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ['DOCKER_CALLS']).open('a') as f:f.write(json.dumps(args)+'\\n')
if 'config' in args:print('{"name":"talk-backup-fixture"}')
elif args[:2]==['compose','ls']:print('talk-backup-fixture')
elif 'down' in args:pass
elif args[0]=='ps':
    if os.environ.get('FIXTURE_ACTIVE')=='true':print('fixture-still-running')
else:sys.exit(23)
''')
        docker.chmod(0o755)
        self.conversations = load('talk_conversations')
        self.signer = load('session_signer')
        self.signer._set_secret_for_tests('synthetic-backup-signing-secret')
        self.cookie = self.signer.issue(ttl_seconds=3600)
        self.owner = hashlib.sha256(self.cookie.encode()).hexdigest()
        self.store = self.conversations.Store(self.source / 'data/talk/conversations.sqlite3')
        self.addCleanup(self.store.close)
        self.mapping = sqlite3.connect(self.store.path)
        self.mapping.execute('PRAGMA journal_mode=WAL')
        self.mapping.execute('PRAGMA wal_autocheckpoint=0')
        self.addCleanup(self.mapping.close)
        self.empty = self.store.resolve(self.owner, 't' * 64, 'e' * 64, create=True)
        self.history = self.store.resolve(self.owner, 't' * 64, 'h' * 64, create=True)
        self.store.created(self.empty, 'empty-root', 'runtime-before-backup')
        self.store.created(self.history, 'history-root', 'runtime-before-backup')
        self.store.resumed(self.history, 'history-tip', 'runtime-before-backup', running=False)
        self.mapping.execute("UPDATE conversations SET runtime='runtime-in-live-wal'")
        self.mapping.commit()
        native = self.source / 'data/hermes/state.db'
        native.parent.mkdir(parents=True)
        self.native = sqlite3.connect(native)
        self.addCleanup(self.native.close)
        self.native.executescript('''PRAGMA journal_mode=WAL;
PRAGMA wal_autocheckpoint=0;
CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,end_reason TEXT);
CREATE TABLE messages(session_id TEXT,role TEXT,content TEXT);
INSERT INTO sessions VALUES ('empty-root',NULL,NULL),('history-root',NULL,'compression'),('history-tip','history-root',NULL);
INSERT INTO messages VALUES ('history-root','user','Retained synthetic original'),('history-tip','assistant','Retained synthetic compression answer');
''')
        native.chmod(0o600)
        self.assertGreater(Path(str(native) + '-wal').stat().st_size, 0)
        self.assertGreater(Path(str(self.store.path) + '-wal').stat().st_size, 0)

    def install(self, name):
        root = self.area / name
        (root / 'lib').mkdir(parents=True)
        for filename in ('rsync.sh', 'backup-paths.sh'):
            shutil.copy2(ODS / 'lib' / filename, root / 'lib' / filename)
        (root / '.env').write_text('ODS_SESSION_SECRET=synthetic-backup-signing-secret\nHERMES_DASHBOARD_SESSION_TOKEN=synthetic-hermes-seed\n')
        (root / 'docker-compose.yml').write_text('name: talk-backup-fixture\nservices: {}\n')
        (root / '.compose-flags').write_text('-f docker-compose.yml\n')
        return root

    def cli(self, root, script, *args, active=False):
        env = {**os.environ, 'ODS_DIR': str(root), 'HOME': str(self.area),
               'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
               'DOCKER_CALLS': str(self.area / 'docker-calls.jsonl'), 'FIXTURE_ACTIVE': str(active).lower()}
        result = subprocess.run(['bash', str(ODS / script), *args], env=env, text=True, capture_output=True)
        self.output = result.stdout + result.stderr
        return result.returncode

    def backup(self, kind='full'):
        self.assertEqual(self.cli(self.source, 'ods-backup.sh', '--type', kind), 0, self.output)
        candidates = list((self.source / '.backups').glob('backup-*'))
        self.assertEqual(len(candidates), 1)
        return candidates[0]

    def stage(self, archive):
        destination = self.destination / '.backups' / archive.name
        shutil.copytree(archive, destination)
        return destination

    def standalone(self, path):
        self.assertTrue(path.is_file(), 'Durable Talk database was omitted by the real backup CLI')
        for suffix in ('-wal', '-shm', '-journal'):
            self.assertFalse(Path(str(path) + suffix).exists())
        with sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))

    def test_real_wal_backup_restore_retains_empty_history_and_binding(self):
        archive = self.backup()
        mapping = archive / 'data/talk/conversations.sqlite3'
        self.standalone(mapping)
        self.assertEqual(mapping.stat().st_mode & 0o777, 0o600)
        self.standalone(archive / 'data/hermes/state.db')
        receipt = json.loads((archive / 'manifest.json').read_text())['talk_sqlite_snapshots']
        self.assertEqual(receipt['consistency'], 'individual-sqlite-snapshots')
        self.stage(archive)
        # Preexisting destination companions must not survive additive restore.
        for relative in ('data/talk/conversations.sqlite3', 'data/hermes/state.db'):
            path = self.destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            for suffix in ('-wal', '-shm', '-journal'):
                Path(str(path) + suffix).write_bytes(b'stale destination companion')
        (self.destination / 'data/talk/local-only.txt').write_text('keep additive local file')
        self.assertEqual(self.cli(self.destination, 'ods-restore.sh', '-f', '--stop-containers', archive.name), 0, self.output)
        for relative in receipt['sha256']:
            self.standalone(self.destination / relative)
        self.assertTrue((self.destination / 'data/talk/local-only.txt').exists())
        self.assertEqual((self.destination / '.env').read_bytes(), (self.source / '.env').read_bytes())
        restored = self.conversations.Store(self.destination / 'data/talk/conversations.sqlite3')
        self.addCleanup(restored.close)
        self.assertEqual(restored.resolve(self.owner, 't' * 64, 'e' * 64), self.empty)
        self.assertEqual(restored.get(self.empty)['root'], 'empty-root')
        self.assertEqual(restored.get(self.history)['tip'], 'history-tip')
        for owner, tab in ((self.owner, 'wrong' * 16), ('new-login-cookie-hash', 't' * 64)):
            with self.assertRaises(self.conversations.ConversationError):
                restored.resolve(owner, tab, 'h' * 64)
        self.assertTrue(self.signer.verify(self.cookie)[0])
        self.signer._set_secret_for_tests('different-restored-secret')
        self.assertFalse(self.signer.verify(self.cookie)[0])
        with sqlite3.connect(self.destination / 'data/hermes/state.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE session_id=?', ('empty-root',)).fetchone(), (0,))
            self.assertEqual(db.execute('SELECT content FROM messages ORDER BY rowid').fetchall(),
                             [('Retained synthetic original',), ('Retained synthetic compression answer',)])

    def test_default_and_still_running_restore_refuse_before_publication(self):
        archive = self.stage(self.backup())
        original = (self.destination / '.env').read_bytes()
        self.assertNotEqual(self.cli(self.destination, 'ods-restore.sh', '-f', archive.name), 0)
        self.assertIn('requires --stop-containers', self.output)
        self.assertFalse((self.area / 'docker-calls.jsonl').exists())
        self.assertNotEqual(self.cli(self.destination, 'ods-restore.sh', '-f', '--stop-containers', archive.name, active=True), 0)
        self.assertIn('remain running', self.output)
        self.assertFalse((self.destination / 'data/talk').exists())
        self.assertEqual((self.destination / '.env').read_bytes(), original)
        # Fresh target with authoritative fixture inventory showing none remains.
        self.assertEqual(self.cli(self.destination, 'ods-restore.sh', '-f', '--stop-containers', archive.name), 0, self.output)

    def test_user_data_captures_pair_without_exporting_environment(self):
        archive = self.backup('user-data')
        self.standalone(archive / 'data/talk/conversations.sqlite3')
        self.standalone(archive / 'data/hermes/state.db')
        self.assertFalse((archive / '.env').exists())

    def test_tampered_snapshot_refuses_even_when_general_checksums_skipped(self):
        archive = self.stage(self.backup())
        with sqlite3.connect(archive / 'data/talk/conversations.sqlite3') as db:
            db.execute("UPDATE conversations SET tip='tampered-tip'")
        self.assertNotEqual(self.cli(self.destination, 'ods-restore.sh', '-f', '--stop-containers', '--skip-verify', archive.name), 0)
        self.assertIn('snapshot digest', self.output)
        self.assertFalse((self.destination / 'data/talk').exists())
        self.assertFalse((self.area / 'docker-calls.jsonl').exists())

    def test_missing_root_or_compression_reference_cannot_publish(self):
        for statement in ("DELETE FROM sessions WHERE id='history-root'",
                          "DELETE FROM sessions WHERE id='history-tip'",
                          "UPDATE sessions SET parent_session_id='missing-parent' WHERE id='history-tip'",
                          "UPDATE sessions SET parent_session_id='history-tip' WHERE id='history-root'"):
            with self.subTest(statement=statement):
                self.native.execute('SAVEPOINT corrupt')
                self.native.execute(statement)
                self.native.execute('RELEASE corrupt')
                self.assertNotEqual(self.cli(self.source, 'ods-backup.sh', '--type', 'user-data'), 0)
                self.assertIn('Talk backup', self.output)
                self.assertFalse(list((self.source / '.backups').glob('backup-*')))
                self.native.executescript("DELETE FROM sessions; INSERT INTO sessions VALUES ('empty-root',NULL,NULL),('history-root',NULL,'compression'),('history-tip','history-root',NULL);")


if __name__ == '__main__':
    unittest.main()
