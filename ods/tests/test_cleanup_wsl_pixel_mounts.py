"""Fail-closed checks for retiring Docker Desktop's Pixel bind projections."""
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'cleanup_wsl_pixel_mounts', Path(__file__).resolve().parents[1]
    / 'scripts/cleanup-wsl-pixel-mounts.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
SOURCE = str(M.BASE / 'ingress')
PROXY = '/mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/' + 'a' * 64
ROOT = '/ods-portal-runtime/ingress'


def row(identity, root=ROOT, target=SOURCE, **kwargs):
    return dict(id=identity, parent=1, root=root, target=target, device='0:32',
                fs='tmpfs', propagation=['shared:1'], **kwargs)


def mounts():
    return [row(1, '/', '/mnt/wsl'), row(2), row(3, target=PROXY)]


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.stack = []
        for item in (
                patch.object(M, 'directory'),
                patch.object(M.os, 'stat', return_value=types.SimpleNamespace(st_dev=32, st_ino=5)),
                patch.object(Path, 'exists', return_value=True),
                patch.object(Path, 'is_symlink', return_value=False)):
            self.stack.append(item)
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.stack)])

    def test_known_source_and_proxy(self):
        self.assertEqual(len(M.plan(mounts())), 2)

    def test_unmounted_empty_directories(self):
        self.assertEqual(M.plan(mounts()[:1]), [])

    def test_missing_sibling(self):
        with patch.object(Path, 'exists', side_effect=lambda: False):
            self.assertEqual(len(M.plan(mounts())), 2)

    def test_foreign_source_layer(self):
        rows = mounts(); rows[1]['device'] = '8:1'
        with self.assertRaises(M.Refusal): M.plan(rows)

    def test_foreign_proxy_layer(self):
        rows = mounts() + [row(4, '/foreign', PROXY)]
        with self.assertRaises(M.Refusal): M.plan(rows)

    def test_foreign_child_mount(self):
        rows = mounts() + [row(4, '/foreign', SOURCE + '/child')]
        with self.assertRaises(M.Refusal): M.plan(rows)

    def test_unexpected_peer_path(self):
        rows = mounts() + [row(4, target='/tmp/foreign-peer')]
        with self.assertRaises(M.Refusal): M.plan(rows)

    def test_base_mount(self):
        rows = mounts() + [row(4, '/ods-portal-runtime', str(M.BASE))]
        with self.assertRaises(M.Refusal): M.plan(rows)

    def test_different_shared_group(self):
        rows = mounts(); rows[2]['propagation'] = ['shared:9']
        with self.assertRaises(M.Refusal): M.plan(rows)

    def test_wrong_source_inode(self):
        def info(path, *args, **kwargs):
            return types.SimpleNamespace(st_dev=32, st_ino=9 if str(path) == PROXY else 5)
        with patch.object(M.os, 'stat', side_effect=info):
            with self.assertRaises(M.Refusal): M.plan(mounts())


class DirectoryTests(unittest.TestCase):
    def test_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'real').mkdir(); (root / 'link').symlink_to(root / 'real')
            with self.assertRaises(M.Refusal): M.directory(root / 'link')

    def test_nonempty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); root.chmod(0o755); (root / 'socket-or-file').write_text('keep')
            info = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
            with patch.object(Path, 'lstat', return_value=info):
                with self.assertRaises(M.Refusal): M.directory(root, empty=True)
            self.assertEqual((root / 'socket-or-file').read_text(), 'keep')


class ConsumerTests(unittest.TestCase):
    def check(self, *, containers=None, engine='expected', state='inactive', volume_device=None):
        containers = [] if containers is None else containers
        def docker(*args):
            if args[0] == 'info': return json.dumps({'ID': engine, 'OperatingSystem': 'Docker Desktop'})
            if args[0] == 'ps': return '\n'.join(c['Id'] for c in containers)
            if args[0] == 'inspect': return json.dumps(containers)
            if args[0] == 'volume':
                return json.dumps([{'Name': args[2], 'Driver': 'local',
                                    'Mountpoint': '/var/lib/docker/volumes/test/_data',
                                    'Options': {'device': volume_device} if volume_device else None}])
            self.fail(str(args))
        with patch.object(M, 'docker', side_effect=docker), patch.object(M, 'run', return_value=state):
            M.ensure_unused(mounts()[1:], 'expected')

    def test_no_consumers(self): self.check()

    def test_wrong_engine(self):
        with self.assertRaises(M.Refusal): self.check(engine='other')

    def test_active_service(self):
        with self.assertRaises(M.Refusal): self.check(state='active')

    def test_stopped_or_created_container_still_blocks(self):
        for source in [SOURCE, str(M.BASE), '/mnt/wsl', PROXY,
                       SOURCE.replace('/mnt/wsl', '/run/desktop/mnt/host/wsl'),
                       SOURCE.replace('/mnt/wsl', '/mnt/host/wsl')]:
            for place in ['Mounts', 'HostConfig']:
                container = {'Id': 'c' * 64}
                values = [{'Type': 'bind', 'Source': source}]
                container[place] = values if place == 'Mounts' else {'Mounts': values}
                with self.subTest(source=source, place=place):
                    with self.assertRaises(M.Refusal): self.check(containers=[container])

    def test_legacy_bind_of_created_container(self):
        with self.assertRaises(M.Refusal):
            self.check(containers=[{'Id': 'c' * 64, 'HostConfig': {'Binds': [SOURCE + ':/data:ro']}}])

    def test_unrelated_container_allowed(self):
        self.check(containers=[{'Id': 'c' * 64, 'Mounts': [{'Type': 'bind', 'Source': '/home/other/data'}]}])

    def test_local_volume_bind_consumer(self):
        container = {'Id': 'c' * 64, 'Mounts': [{'Type': 'volume', 'Name': 'test-volume'}]}
        for source in [SOURCE, SOURCE.replace('/mnt/wsl', '/run/desktop/mnt/host/wsl')]:
            with self.subTest(source=source):
                with self.assertRaises(M.Refusal):
                    self.check(containers=[container], volume_device=source)

    def test_unrelated_local_volume(self):
        self.check(containers=[{'Id': 'c' * 64, 'Mounts': [{'Type': 'volume', 'Name': 'test-volume'}]}])

    def test_inline_anonymous_bind_options(self):
        for device in [SOURCE, '../relative']:
            container = {'Id': 'c' * 64, 'HostConfig': {'Mounts': [{
                'Type': 'volume', 'Target': '/data', 'VolumeOptions': {'DriverConfig': {
                    'Name': 'local', 'Options': {'type': 'none', 'o': 'bind', 'device': device}}}}]}}
            with self.subTest(device=device):
                with self.assertRaises(M.Refusal): self.check(containers=[container])


class CleanupTests(unittest.TestCase):
    def execute(self, plans):
        with patch.object(Path, 'read_text', return_value='microsoft'), \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(M.os, 'geteuid', return_value=0), \
             patch.object(M, 'read_mounts', return_value=[]), \
             patch.object(M, 'plan', side_effect=plans), \
             patch.object(M, 'ensure_unused') as inspections, patch.object(M, 'run') as command:
            try:
                return M.cleanup('expected', apply=True)
            finally:
                self.commands = command.call_args_list
                self.inspections = inspections.call_args_list

    def test_changed_preflight_refused_before_unmount(self):
        with self.assertRaises(M.Refusal): self.execute([[row(2)], [row(3)]])
        self.assertEqual(self.commands, [])

    def test_new_mount_after_unmount_refused(self):
        with self.assertRaises(M.Refusal):
            self.execute([[row(2), row(3)], [row(2), row(3)], [row(4)]])
        self.assertEqual(len(self.commands), 1)

    def test_no_progress_refused(self):
        with self.assertRaises(M.Refusal): self.execute([[row(2)], [row(2)], [row(2)]])
        self.assertEqual(len(self.commands), 1)

    def test_ordinary_unmount_and_complete(self):
        result = self.execute([[row(2)], [row(2)], []])
        self.assertEqual(result['remainingMounts'], 0)
        self.assertEqual(self.commands[0].args, ('umount', '--', SOURCE))

    def test_no_mounts_needs_no_docker_or_services(self):
        self.assertEqual(self.execute([[]])['removedMounts'], 0)
        self.assertEqual(self.inspections, [])


if __name__ == '__main__': unittest.main()
