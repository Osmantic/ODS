"""Real SQLite transfer semantics; Docker identities are bounded fake fixtures."""
from io import BytesIO, TextIOWrapper
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ODS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ODS / "extensions/services/dashboard-api"))
from PIL import Image
from pixel_image_store import ImageStore, ConversationDeleted
from pixel_chat_results import ChatResultStore

SPEC = importlib.util.spec_from_file_location("private_state", ODS / "installers/macos/lib/dashboard-private-state.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
W = M.worker

DOCKER = r'''#!PYTHON
import json, os, pathlib, subprocess, sys
root = pathlib.Path(os.environ['ODS_DIR'])
state = root / 'docker-fixture.json'
data = json.loads(state.read_text())
args = sys.argv[1:]
def save(): state.write_text(json.dumps(data))
if args == ['context', 'show']: print('fixture'); sys.exit(0)
if args[:2] == ['context', 'inspect']:
    print(json.dumps([{'Endpoints': {'docker': {'Host': 'unix:///fixture.sock'}}}]))
elif args[:1] == ['info']: print('fixture-daemon')
elif args[:2] == ['image', 'inspect']:
    if not data['image']:
        print('Error: No such image', file=sys.stderr); sys.exit(1)
    print(json.dumps([{'Id': 'sha256:'+'a'*64}]))
elif args[:2] == ['volume', 'create']:
    name = args[-1]
    labels = dict(args[i+1].split('=', 1) for i, arg in enumerate(args) if arg == '--label')
    data['volumes'][name] = {'Name': name, 'Labels': labels, 'Driver': 'local', 'Options': None}
    (root/'volumes'/name).mkdir(parents=True, mode=0o700)
    save(); print(name)
elif args[:2] == ['volume', 'inspect']:
    if args[-1] not in data['volumes']:
        print('[]')
        print('Error response from daemon: get '+args[-1]+': no such volume', file=sys.stderr); sys.exit(1)
    print(json.dumps([data['volumes'][args[-1]]]))
elif args[:1] == ['ps']: pass
elif args[:1] == ['compose']:
    if 'config' in args:
        config = data['config']
        if data.get('reject_config_after_env') and (root/'.env').read_text().startswith('backup-'):
            sys.exit(34)
        if data.get('require_native'):
            assert args[1:7] == ['--project-directory', str(root), '--project-name', 'ods', '--env-file', '/dev/null']
            assert len([arg for arg in args if arg == '-f']) == 1
            selected = pathlib.Path(args[args.index('-f')+1])
            assert selected.name == 'lifecycle.compose.json'
            config = json.loads((root/selected).read_text())
            data['native_prefix_verified'] = True; save()
        elif not any(arg.endswith('/dashboard-private-state.compose.yaml') for arg in args):
            config['services']['dashboard-api']['volumes'] = [
                mount for mount in config['services']['dashboard-api']['volumes'] if mount['target'] == '/data']
            config['volumes'] = {}
        print(json.dumps(config))
    elif 'build' in args:
        data['builds'] += 1; data['image'] = True; save()
    elif 'up' in args:
        if data.get('require_native'):
            import fcntl
            with (root/'data/.dashboard-private-stores/lock').open('r') as lock:
                try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: data['storage_lock_verified'] = True
                else: sys.exit(37)
            assert args[1:7] == ['--project-directory', str(root), '--project-name', 'ods', '--env-file', '/dev/null']
            assert len([arg for arg in args if arg == '-f']) == 1
        data['launches'].append(args[args.index('up'):]); save()
    else: sys.exit(31)
elif args[:1] == ['run']:
    mount = args[args.index('--mount')+1]
    name = mount.split('src=', 1)[1].split(',', 1)[0]
    directory = root/'volumes'/name
    start = args.index('-c')
    program, child = args[start+1], args[start+2:]
    if child[0] == 'init':
        if list(directory.iterdir()): sys.exit(32)
        directory.chmod(0o700)
    else:
        # Only Docker's mount namespace is simulated. Execute the actual
        # transfer code, binary stdin/stdout and error status unchanged.
        program = program.replace('Path("/store")', 'Path('+repr(str(directory))+')')
        sys.exit(subprocess.run([sys.executable, '-c', program, *child]).returncode)
else: sys.exit(33)
'''


class MissingVolume(unittest.TestCase):
    def setUp(self):
        self.stores = object.__new__(M.Stores)
        self.stores.root = Path.cwd()
        self.stores.env = {}
        self.stores.binary = 'docker'
        self.stores.compose_prefix = []
        self.name = 'ods_dashboard-pixel-images'
        self.absent = ('Error response from daemon: get '+self.name+': no such volume\n').encode()

    def inspect(self, status=1, stdout=b'[]\n', stderr=None, *, missing=True, args=None):
        result = subprocess.CompletedProcess([], status, stdout, self.absent if stderr is None else stderr)
        with patch.object(M.subprocess, 'run', return_value=result):
            return self.stores.docker(*(args or ('volume', 'inspect', self.name)), missing=missing)

    def test_exact_daemon_absence_for_both_requested_volumes(self):
        for kind in W.STORES:
            name = 'ods_dashboard-'+kind
            with self.subTest(kind=kind):
                self.assertIsNone(self.inspect(args=('volume', 'inspect', name),
                    stderr=('Error response from daemon: get '+name+': no such volume\n').encode()))

    def test_other_errors_and_outputs_are_not_absence(self):
        cases = (
            {'status': 2}, {'stdout': b''}, {'stdout': b'null\n'}, {'stdout': b'[{}]\n'},
            {'stderr': b'Error response from daemon: get another: no such volume\n'},
            {'stderr': b'Error response from daemon: get ods_dashboard-pixel-images: permission denied\n'},
            {'stderr': b'dial unix /missing/docker.sock: connect: No such file or directory\n'},
            {'stderr': b'Error: No such volume\n'},
            {'stderr': self.absent+b'another daemon error\n'},
            {'missing': False}, {'args': ('volume', 'create', self.name)},
            {'args': ('volume', 'inspect', self.name, 'another')},
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, '^private-store-docker-volume$'):
                self.inspect(**case)

    def test_successful_inspection_is_preserved(self):
        self.assertEqual(self.inspect(status=0, stdout=b'[{"Name":"kept"}]\n', stderr=b''),
                         '[{"Name":"kept"}]\n')


class FakeStores(M.Stores):
    def __init__(self, root):
        self.root = root
        self.install = M.hashlib.sha256(str(root).encode()).hexdigest()
        self.project = "ods"
        self.image = "sha256:" + "a" * 64
        self.endpoint = "unix:///test/docker.sock"
        self.daemon = "test-daemon"
        self.volumes = {kind: "ods_dashboard-" + kind for kind in W.STORES}
        self.definitions = {}
        self.api_running = False
        self.foreign = False
        self.fail_action = None
        self.actions = []

    def path(self, kind):
        return self.root / "volumes" / self.volumes[kind]

    def docker(self, *args, missing=False):
        if args[:2] == ("volume", "inspect"):
            row = self.definitions.get(args[2])
            return json.dumps([row]) if row else None
        if args[:2] == ("volume", "create"):
            labels = dict(args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "--label")
            name = args[-1]
            self.definitions[name] = {"Name": name, "Labels": labels, "Driver": "local", "Options": None}
            (self.root / "volumes" / name).mkdir(parents=True, mode=0o700)
            return name
        raise AssertionError(args)

    def consumers(self, *, stopped=False):
        if self.foreign:
            raise ValueError("private-store-foreign-consumer")
        if stopped and self.api_running:
            raise ValueError("stopped writers required")

    def transfer(self, kind, action, token, *, payload=None, output=None, expected=""):
        self.actions.append((kind, action))
        if action == self.fail_action:
            raise ValueError("injected transfer failure")
        directory = self.path(kind)
        if action == "init":
            # Fake Docker directories already have the test user's UID. Still
            # exercise the actual init empty-directory predicate.
            with patch.object(W.os, "geteuid", return_value=0), patch.object(W.os, "chown"):
                W.worker(action, kind, token, expected, directory)
            return
        incoming = TextIOWrapper(BytesIO(payload.read_bytes() if payload else b""))
        outgoing = TextIOWrapper(BytesIO())
        with patch.object(W.sys, "stdin", incoming), patch.object(W.sys, "stdout", outgoing):
            try:
                W.worker(action, kind, token, expected, directory)
            except OSError as error:
                # Production worker turns OS refusals into a failed process;
                # Stores.transfer reports that as a fixed ValueError category.
                raise ValueError("private-store-transfer-" + action) from error
            if output:
                output.write_bytes(outgoing.buffer.getvalue())
                output.chmod(0o600)


class Migration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "ods"
        self.state = self.root / M.STATE
        self.state.mkdir(parents=True, mode=0o700)
        self.stores = FakeStores(self.root)

    def images(self, path=None):
        return ImageStore(path or self.root / "data/pixel-images")

    def populate(self):
        stream = BytesIO()
        Image.new("RGB", (2, 2), "red").save(stream, format="PNG")
        self.png = stream.getvalue()
        store = self.images()
        self.image = store.put("a" * 64, "kept", self.png, "image/png")
        store.begin_delete("a" * 64, "pending")
        store.delete_conversation("a" * 64, "deleted")
        store.close()
        chats = ChatResultStore(self.root / "data/pixel-chat-results")
        self.key = ("a" * 64, "kept", "attempt")
        chats.reserve(self.key, "fingerprint")
        chats.append(self.key, b"data: retained chunk\n\n")
        chats.close()

    def test_actual_stores_preserve_blobs_fences_and_interrupted_attempt(self):
        self.populate()
        self.stores.prepare(self.state)
        store = self.images(self.stores.path("pixel-images"))
        self.assertEqual(store.get("a" * 64, "kept", self.image["id"])["data"], self.png)
        self.assertEqual(store.deletion("a" * 64, "pending"), {"completed": False})
        self.assertEqual(store.deletion("a" * 64, "deleted"), {"completed": True})
        with self.assertRaises(ConversationDeleted):
            store.assert_available("a" * 64, "pending")
        store.close()
        chats = ChatResultStore(self.stores.path("pixel-chat-results"))
        self.assertEqual(chats.get(self.key)["state"], "unresolved")
        self.assertEqual(chats.db.execute("SELECT data FROM chunks").fetchone()[0], b"data: retained chunk\n\n")
        chats.close()

    def test_fresh_install_creates_private_stores(self):
        self.stores.prepare(self.state)
        for kind in W.STORES:
            self.assertEqual(self.stores.path(kind).stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.stores.path(kind) / W.STORES[kind]).stat().st_mode & 0o777, 0o600)
        self.images(self.stores.path("pixel-images")).close()
        ChatResultStore(self.stores.path("pixel-chat-results")).close()

    def test_running_legacy_api_refused_before_volume_creation(self):
        self.populate()
        self.stores.api_running = True
        with self.assertRaisesRegex(ValueError, "stopped writers"):
            self.stores.prepare(self.state)
        self.assertFalse(self.stores.definitions)
        self.assertFalse(list(self.state.glob("*.json")))

    def test_completed_volume_never_reimports_legacy_shadow(self):
        self.populate()
        self.stores.prepare(self.state)
        store = self.images(self.stores.path("pixel-images"))
        store.begin_delete("a" * 64, "new-fence")
        store.close()
        self.stores.api_running = True
        self.stores.actions.clear()
        self.stores.prepare(self.state)
        self.assertEqual([action for _, action in self.stores.actions], ["check", "check"])
        store = self.images(self.stores.path("pixel-images"))
        self.assertIsNotNone(store.deletion("a" * 64, "new-fence"))
        store.close()

    def test_interrupted_import_resumes_exact_snapshot(self):
        self.populate()
        self.stores.fail_action = "import"
        with self.assertRaisesRegex(ValueError, "injected"):
            self.stores.prepare(self.state)
        self.stores.fail_action = None
        self.stores.prepare(self.state)
        migrated = self.images(self.stores.path("pixel-images"))
        self.assertIsNotNone(migrated.deletion("a" * 64, "pending"))
        migrated.close()

    def test_legacy_write_after_interruption_refuses_stale_snapshot(self):
        self.populate()
        self.stores.fail_action = "import"
        with self.assertRaisesRegex(ValueError, "injected"):
            self.stores.prepare(self.state)
        self.stores.fail_action = None
        legacy = self.images()
        legacy.begin_delete("a" * 64, "later-legacy-write")
        legacy.close()
        with self.assertRaisesRegex(ValueError, "legacy-changed"):
            self.stores.prepare(self.state)
        self.assertFalse((self.stores.path("pixel-images") / "images.sqlite3").exists())

    def test_import_marker_survives_lost_host_completion(self):
        self.stores.prepare(self.state)
        path, record = self.stores.record(self.state, "pixel-images")
        record["complete"] = False
        M.write_record(path, record)
        store = self.images(self.stores.path("pixel-images"))
        store.begin_delete("a" * 64, "after-import")
        store.close()
        self.stores.prepare(self.state)
        store = self.images(self.stores.path("pixel-images"))
        self.assertIsNotNone(store.deletion("a" * 64, "after-import"))
        store.close()

    def test_completed_volume_loss_refuses_stale_host_import(self):
        self.populate()
        self.stores.prepare(self.state)
        del self.stores.definitions[self.stores.volumes["pixel-images"]]
        with self.assertRaisesRegex(ValueError, "completed-volume-missing"):
            self.stores.prepare(self.state)

    def test_foreign_consumer_and_unrecognized_volume_refused(self):
        self.stores.foreign = True
        with self.assertRaisesRegex(ValueError, "foreign-consumer"):
            self.stores.prepare(self.state)
        self.stores.foreign = False
        self.stores.definitions[self.stores.volumes["pixel-images"]] = {"Driver": "local", "Labels": {}}
        with self.assertRaisesRegex(ValueError, "unrecognized-volume"):
            self.stores.prepare(self.state)

    def test_changed_daemon_refused(self):
        self.stores.prepare(self.state)
        self.stores.daemon = "other-daemon"
        with self.assertRaisesRegex(ValueError, "receipt-selection"):
            self.stores.prepare(self.state)

    def export_with_temp_directory(self, *, aliased):
        self.populate()
        self.stores.prepare(self.state)
        temporary_root = self.root / "worker-temporary"
        temporary_root.mkdir(mode=0o700)
        selected = temporary_root
        if aliased:
            selected = self.root / "temporary-alias"
            selected.symlink_to(temporary_root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "private-store-custody"):
                W.private(selected, directory=True)
        source = self.stores.path("pixel-images") / "images.sqlite3"
        before = source.read_bytes()
        backup = self.root / "backup/data/pixel-images"
        with patch.object(W.tempfile, "tempdir", str(selected)):
            self.stores.export(self.state, backup)
        saved = self.images(backup)
        try:
            self.assertEqual(saved.get("a" * 64, "kept", self.image["id"])["data"], self.png)
            self.assertEqual(saved.deletion("a" * 64, "pending"), {"completed": False})
            self.assertEqual(saved.deletion("a" * 64, "deleted"), {"completed": True})
        finally:
            saved.close()
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(list(temporary_root.iterdir()), [])

    def test_export_uses_private_canonical_temporary_directory(self):
        self.export_with_temp_directory(aliased=False)

    def test_export_resolves_fresh_temporary_directory_under_real_alias(self):
        self.export_with_temp_directory(aliased=True)

    def test_backup_uses_active_image_volume_and_restore_rolls_back(self):
        self.populate()
        self.stores.prepare(self.state)
        current = self.images(self.stores.path("pixel-images"))
        current.begin_delete("a" * 64, "current-only")
        current.close()
        backup = self.root / "backup/data/pixel-images"
        self.stores.export(self.state, backup)
        saved = self.images(backup)
        self.assertIsNotNone(saved.deletion("a" * 64, "current-only"))
        saved.begin_delete("a" * 64, "backup-only")
        saved.close()
        transaction = self.root / "restore"
        transaction.mkdir(mode=0o700)
        self.stores.restore(self.state, "stage", transaction, backup)
        with self.assertRaisesRegex(ValueError, "restore-unfinished"):
            self.stores.prepare(self.state)
        self.stores.restore(self.state, "publish", transaction)
        current = self.images(self.stores.path("pixel-images"))
        self.assertIsNotNone(current.deletion("a" * 64, "backup-only"))
        current.close()
        self.stores.restore(self.state, "rollback", transaction)
        current = self.images(self.stores.path("pixel-images"))
        self.assertIsNone(current.deletion("a" * 64, "backup-only"))
        self.assertIsNotNone(current.deletion("a" * 64, "current-only"))
        current.close()
        self.stores.prepare(self.state)

    def test_restore_commit_clears_guard_and_preserves_chat_receipts(self):
        self.populate()
        self.stores.prepare(self.state)
        chat_before = (self.stores.path("pixel-chat-results") / "results.sqlite3").read_bytes()
        backup = self.root / "backup/data/pixel-images"
        self.stores.export(self.state, backup)
        transaction = self.root / "restore"
        transaction.mkdir(mode=0o700)
        for action in ("stage", "publish", "commit"):
            self.stores.restore(self.state, action, transaction, backup if action == "stage" else None)
        self.assertFalse((self.state / "image-restore-active.json").exists())
        self.assertEqual((self.stores.path("pixel-chat-results") / "results.sqlite3").read_bytes(), chat_before)

    def test_restore_refuses_running_consumer_even_without_cli_stop(self):
        self.stores.prepare(self.state)
        backup = self.root / "backup/data/pixel-images"
        self.stores.export(self.state, backup)
        transaction = self.root / "restore"
        transaction.mkdir(mode=0o700)
        self.stores.api_running = True
        with self.assertRaisesRegex(ValueError, "stopped writers"):
            self.stores.restore(self.state, "stage", transaction, backup)
        self.assertFalse(list(transaction.iterdir()))

    def test_wal_snapshot_includes_committed_rows(self):
        directory = self.root / "wal"
        directory.mkdir(mode=0o700)
        database = directory / "images.sqlite3"
        database.touch(mode=0o600)
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE evidence (value TEXT)")
            connection.execute("INSERT INTO evidence VALUES ('committed')")
            connection.commit()
            before = {suffix: Path(str(database) + suffix).read_bytes() for suffix in ("", "-wal")}
            target = self.state / "snapshot.sqlite3"
            W.snapshot(directory, "pixel-images", target)
            self.assertEqual(target.read_bytes()[18:20], b"\x01\x01")
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertEqual({suffix: Path(str(database) + suffix).read_bytes() for suffix in ("", "-wal")}, before)
            with sqlite3.connect(target) as result:
                self.assertEqual(result.execute("SELECT value FROM evidence").fetchone(), ("committed",))

    def test_exported_image_archive_restores_without_wal_sidecars(self):
        self.populate()
        self.stores.prepare(self.state)
        current = self.images(self.stores.path("pixel-images"))
        self.addCleanup(current.close)
        current.db.execute("PRAGMA journal_mode=WAL")
        current.db.execute("PRAGMA wal_autocheckpoint=0")
        current.begin_delete("a" * 64, "volume-only")
        backup = self.root / "backup/data/pixel-images"
        self.stores.export(self.state, backup)
        database = backup / "images.sqlite3"
        self.assertEqual(database.read_bytes()[18:20], b"\x01\x01")
        self.assertEqual(list(backup.iterdir()), [database])
        archive = self.root / "backup.tar.gz"
        with tarfile.open(archive, "w:gz") as compressed:
            compressed.add(database, arcname="images.sqlite3")
        extracted = self.root / "extracted"
        extracted.mkdir(mode=0o700)
        with tarfile.open(archive) as compressed:
            (extracted / "images.sqlite3").write_bytes(compressed.extractfile("images.sqlite3").read())
        (extracted / "images.sqlite3").chmod(0o600)
        # Staging is read-only with respect to the archive, including sidecars.
        before = (extracted / "images.sqlite3").read_bytes()
        current.close()
        transaction = self.root / "archive-restore"
        transaction.mkdir(mode=0o700)
        for action in ("stage", "publish", "commit"):
            self.stores.restore(self.state, action, transaction, extracted if action == "stage" else None)
        self.assertEqual(list(extracted.iterdir()), [extracted / "images.sqlite3"])
        self.assertEqual((extracted / "images.sqlite3").read_bytes(), before)
        restored = self.images(self.stores.path("pixel-images"))
        try:
            self.assertEqual(restored.get("a" * 64, "kept", self.image["id"])["data"], self.png)
            self.assertIsNotNone(restored.deletion("a" * 64, "volume-only"))
        finally:
            restored.close()

    def test_killed_snapshot_never_publishes_partial_rollback_database(self):
        directory = self.root / "interrupted"
        directory.mkdir(mode=0o700)
        database = directory / "images.sqlite3"
        database.touch(mode=0o600)
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE evidence (value BLOB)")
            db.execute("INSERT INTO evidence VALUES (zeroblob(4194304))")
        prior = directory / ".prior-test"
        code = '''import importlib.util, os, pathlib, sys
spec=importlib.util.spec_from_file_location('worker', sys.argv[1])
worker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
calls=0
def clock():
    global calls
    calls += 1
    if calls == 2: os._exit(86)
    return 0
worker.time.monotonic=clock
worker.snapshot(pathlib.Path(sys.argv[2]), 'pixel-images', pathlib.Path(sys.argv[3]))
'''
        result = subprocess.run([sys.executable, "-c", code, str(ODS / "installers/macos/lib/dashboard-private-state-worker.py"),
                                 str(directory), str(prior)], timeout=15)
        self.assertEqual(result.returncode, 86)
        self.assertFalse(prior.exists(), "partial copy became rollback authority")
        self.assertTrue(list(directory.glob(".snapshot-*")))
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute("SELECT length(value) FROM evidence").fetchone()[0], 4194304)

    def test_custody_negatives_do_not_change_source(self):
        for failure in ("mode", "symlink", "hardlink", "owner"):
            with self.subTest(failure=failure):
                directory = self.root / failure
                directory.mkdir(mode=0o700)
                database = directory / "images.sqlite3"
                database.touch(mode=0o600)
                if failure == "mode":
                    database.chmod(0o644)
                elif failure == "symlink":
                    database.unlink()
                    database.symlink_to(self.state / "not-created")
                elif failure == "hardlink":
                    os.link(database, directory / "another")
                context = patch.object(W.os, "geteuid", return_value=os.geteuid() + 1) if failure == "owner" else M.contextlib.nullcontext()
                with context, self.assertRaisesRegex(ValueError, "custody"):
                    W.snapshot(directory, "pixel-images", self.state / (failure + ".sqlite3"))
                self.assertFalse((self.state / (failure + ".sqlite3")).exists())

    def uninstall_fixture(self):
        sys.path.insert(0, str(ODS / "tests"))
        from test_uninstall_compose_volumes import FakeDocker, MODULE
        self.stores.prepare(self.state)
        (self.root / M.RECIPE).parent.mkdir(parents=True)
        shutil.copy2(ODS / M.RECIPE, self.root / M.RECIPE)
        fake = FakeDocker(self.root)
        fake.volumes = dict(self.stores.definitions)
        fake.containers = []  # Installer failed before the first Compose up.
        snapshot = self.state / "uninstall.json"
        snapshot.touch()

        def docker(root, *args):
            return self.stores.daemon if args[:1] == ("info",) else fake(root, *args)

        return MODULE, fake, snapshot, docker

    def test_failed_install_unmounted_volumes_have_exact_creation_custody(self):
        module, fake, snapshot, docker = self.uninstall_fixture()
        with patch.object(module, "docker", docker):
            module.preflight(self.root, snapshot, [])
            module.complete(self.root, snapshot)
        self.assertEqual(fake.volumes, {})

    def test_failed_install_volume_token_mismatch_refuses_purge(self):
        module, fake, snapshot, docker = self.uninstall_fixture()
        fake.volumes[self.stores.volumes["pixel-images"]]["Labels"][M.TOKEN] = "f" * 32
        with patch.object(module, "docker", docker), self.assertRaisesRegex(ValueError, "no container proves"):
            module.preflight(self.root, snapshot, [])
        self.assertFalse(fake.removed)

    def test_failed_install_foreign_consumer_refuses_purge(self):
        module, fake, snapshot, docker = self.uninstall_fixture()
        fake.foreign_consumers[self.stores.volumes["pixel-images"]] = ["b" * 64]
        with patch.object(module, "docker", docker), self.assertRaisesRegex(ValueError, "outside this installation"):
            module.preflight(self.root, snapshot, [])
        self.assertFalse(fake.removed)


class Selection(unittest.TestCase):
    def document(self):
        return {"name": "ods", "services": {"dashboard-api": {"volumes": [
            {"type": "bind", "source": "/ods/data", "target": "/data"},
            *({"type": "volume", "source": "dashboard-" + kind,
               "target": "/data/" + kind, "volume": {"nocopy": True}} for kind in W.STORES)]}},
            "volumes": {"dashboard-" + kind: {"name": "ods_dashboard-" + kind} for kind in W.STORES}}

    def test_both_normal_plain_volumes_resolve(self):
        project, volumes, image, source = M.selection(self.document())
        self.assertEqual(project, "ods")
        self.assertEqual(len(volumes), 2)
        self.assertEqual(image, "ods-dashboard-api")
        self.assertEqual(source, "/ods/data")

    def test_external_bind_custom_name_and_readonly_conflicts_refused(self):
        for change in ("external", "driver_opts", "name", "bind", "readonly", "copy"):
            with self.subTest(change=change):
                config = self.document()
                definition = config["volumes"]["dashboard-pixel-images"]
                mount = config["services"]["dashboard-api"]["volumes"][1]
                if change in {"external", "driver_opts"}:
                    definition[change] = True
                elif change == "name":
                    definition[change] = "foreign"
                elif change == "bind":
                    mount["type"] = "bind"
                elif change == "readonly":
                    mount["read_only"] = True
                else:
                    mount["volume"] = {}
                with self.assertRaisesRegex(ValueError, "definition"):
                    M.selection(config)

    def test_saved_and_cloud_flags_keep_user_override_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for relative in ("docker-compose.base.yml", "docker-compose.cloud.yml", "docker-compose.override.yml", M.RECIPE):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            flags = "-f docker-compose.base.yml -f docker-compose.cloud.yml -f docker-compose.override.yml"
            selected = M.selected_flags(root, flags)
            self.assertEqual(M.selected_flags(root, selected), selected)
            self.assertLess(selected.index(M.RECIPE), selected.index("docker-compose.override.yml"))
            self.assertIn("docker-compose.cloud.yml", selected)

    def test_dependency_closure_and_unrelated_service(self):
        config = self.document()
        config["services"].update({"dashboard": {"depends_on": {"dashboard-api": {}}},
                                    "indirect": {"network_mode": "service:dashboard"}, "tts": {}})
        self.assertTrue(M.needs_stores(config, "dashboard"))
        self.assertTrue(M.needs_stores(config, "indirect"))
        self.assertFalse(M.needs_stores(config, "tts"))


class PublicCommands(unittest.TestCase):
    def setUp(self):
        # Reuse the existing real restore CLI/fault-injection harness.
        sys.path.insert(0, str(ODS / "tests"))
        from test_restore_transaction import RestoreTransaction
        self.fixture = RestoreTransaction()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        # Darwin's temporary directory may traverse /var -> /private/var.
        # Product paths are canonical; bind the fixture to that same contract.
        self.fixture.root = self.fixture.root.resolve()
        self.fixture.backup = self.fixture.backup.resolve()
        self.fixture.bin = self.fixture.bin.resolve()
        self.fixture.env['ODS_DIR'] = str(self.fixture.root)
        self.fixture.env['PATH'] = str(self.fixture.bin) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']
        self.root = self.fixture.root
        library = self.root / "installers/macos/lib"
        library.mkdir(parents=True)
        for name in ("dashboard-private-state.py", "dashboard-private-state-worker.py"):
            shutil.copy2(ODS / "installers/macos/lib" / name, library / name)
        shutil.copy2(ODS / M.RECIPE, self.root / M.RECIPE)
        (self.root / "docker-compose.base.yml").write_text("services: {}\n")
        (self.root / ".compose-flags").write_text("-f docker-compose.base.yml -f " + M.RECIPE)
        config = Selection().document()
        config["services"]["dashboard-api"]["volumes"][0]["source"] = str(self.root / "data")
        (self.root / "docker-fixture.json").write_text(json.dumps({
            "config": config, "image": True, "builds": 0, "launches": [], "volumes": {}}))
        docker = self.fixture.bin / "docker"
        docker.write_text(DOCKER.replace("#!PYTHON", "#!" + sys.executable))
        docker.chmod(0o755)

    def command(self, *arguments):
        return subprocess.run([sys.executable, "-B", str(self.root / "installers/macos/lib/dashboard-private-state.py"),
                               *arguments, "--install-dir", str(self.root)], env=self.fixture.env,
                              capture_output=True, text=True, timeout=30)

    def image_path(self):
        return self.root / "volumes/ods_dashboard-pixel-images"

    def test_real_fresh_install_handles_exact_missing_volume_transcript(self):
        result = self.command("launch", "--operation", "install")
        self.assertEqual(result.returncode, 0, result.stderr)
        for kind in W.STORES:
            self.assertTrue((self.root / "volumes" / ("ods_dashboard-"+kind) / W.MARKER).is_file())
        data = json.loads((self.root / "docker-fixture.json").read_text())
        self.assertEqual(set(data["volumes"]), {"ods_dashboard-"+kind for kind in W.STORES})
        self.assertEqual(data["launches"], [["up", "-d", "--remove-orphans", "--no-build", "--pull", "never"]])

    def test_real_cli_builds_missing_start_and_update_but_never_restart(self):
        fixture = self.root / "docker-fixture.json"
        data = json.loads(fixture.read_text())
        data["image"] = False
        fixture.write_text(json.dumps(data))
        restart = self.command("launch", "--operation", "restart")
        self.assertNotEqual(restart.returncode, 0)
        self.assertIn("image-missing", restart.stderr)
        self.assertEqual(json.loads(fixture.read_text())["builds"], 0)
        for operation in ("start", "restart", "update", "install"):
            if operation == "update":
                data = json.loads(fixture.read_text())
                data["image"] = False
                fixture.write_text(json.dumps(data))
            result = self.command("launch", "--operation", operation)
            self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(fixture.read_text())
        self.assertEqual(data["builds"], 2)
        self.assertEqual(data["launches"], [["up", "-d"], ["up", "-d", "--force-recreate", "--no-build", "--pull", "never"],
                                            ["up", "-d", "--force-recreate"],
                                            ["up", "-d", "--remove-orphans", "--no-build", "--pull", "never"]])

    def test_selected_dashboard_prepares_dependency_and_keeps_explicit_target(self):
        fixture = self.root / "docker-fixture.json"
        data = json.loads(fixture.read_text())
        data["config"]["services"]["dashboard"] = {"depends_on": {"dashboard-api": {}}}
        fixture.write_text(json.dumps(data))
        result = self.command("launch", "--operation", "start", "--service", "dashboard")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.image_path() / W.MARKER).is_file())
        self.assertEqual(json.loads(fixture.read_text())["launches"], [["up", "-d", "dashboard"]])

    def test_unrelated_service_does_not_build_or_migrate_api(self):
        fixture = self.root / "docker-fixture.json"
        data = json.loads(fixture.read_text())
        data["config"]["services"]["tts"] = {}
        data["image"] = False
        fixture.write_text(json.dumps(data))
        result = self.command("launch", "--operation", "start", "--service", "tts")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(fixture.read_text())
        self.assertEqual(data["builds"], 0)
        self.assertEqual(data["volumes"], {})
        self.assertEqual(data["launches"], [["up", "-d", "tts"]])

    def test_real_restore_failure_rolls_back_volume_and_host_items(self):
        self.fixture.fixture()
        (self.root / ".compose-flags").write_text("-f docker-compose.base.yml")
        fixture = self.root / "docker-fixture.json"
        data = json.loads(fixture.read_text())
        data["reject_config_after_env"] = True
        fixture.write_text(json.dumps(data))
        result = self.command("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = ImageStore(self.image_path())
        current.begin_delete("a" * 64, "original")
        current.close()
        target = self.fixture.backup / "data/pixel-images"
        result = self.command("export", "--path", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        archive = ImageStore(target)
        archive.begin_delete("a" * 64, "restored")
        archive.close()
        before = (self.root / ".env").read_bytes()
        self.assertNotEqual(self.fixture.restore("publish_failure"), 0, self.fixture.output)
        self.assertEqual((self.root / ".env").read_bytes(), before)
        current = ImageStore(self.image_path())
        self.assertIsNotNone(current.deletion("a" * 64, "original"))
        self.assertIsNone(current.deletion("a" * 64, "restored"))
        current.close()
        self.assertFalse((self.root / M.STATE / "image-restore-active.json").exists(), self.fixture.output)
        self.assertEqual(self.fixture.restore(), 0, self.fixture.output)
        current = ImageStore(self.image_path())
        self.assertIsNotNone(current.deletion("a" * 64, "restored"))
        current.close()

    def test_real_backup_exports_volume_instead_of_retained_legacy_shadow(self):
        # Upgrade cache predates the private recipe. CLI and backup must both
        # select the new mount instead of relying on a rewritten cache.
        (self.root / ".compose-flags").write_text("-f docker-compose.base.yml")
        legacy = ImageStore(self.root / "data/pixel-images")
        legacy.begin_delete("a" * 64, "legacy")
        legacy.close()
        result = self.command("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = ImageStore(self.image_path())
        current.begin_delete("a" * 64, "volume-only")
        current.close()
        result = subprocess.run(["bash", str(ODS / "ods-backup.sh"), "--type", "user-data"],
                                env=self.fixture.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archives = list((self.root / ".backups").glob("backup-*"))
        self.assertEqual(len(archives), 1)
        archived = ImageStore(archives[0] / "data/pixel-images")
        self.assertIsNotNone(archived.deletion("a" * 64, "volume-only"))
        archived.close()
        self.assertFalse((archives[0] / "data/pixel-chat-results").exists())
        self.assertTrue((archives[0] / "checksums.sha256").is_file())


if __name__ == "__main__":
    unittest.main()
