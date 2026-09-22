import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import contextlib
import hashlib
import json
import os
import stat
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "bin"))
import pixel_access_bridge as bridge


class OwnerLauncherTests(unittest.TestCase):
    @unittest.skipUnless(os.geteuid() == 0, 'root custody fixture required')
    def test_legacy_binding_pins_unit_owner_executable_and_rejects_unit_drift(self):
        # /root avoids deliberately writable /tmp ancestors, just like the
        # installed /etc unit custody requirement.
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            root = Path(directory)
            unit = root / 'gateway.service'
            unit.write_text('[Service]\nUser=fixture\n# '+str(root)+'\n')
            unit.chmod(0o644)
            adapter = bridge.SystemdAccessBridge(root, 'k'*64, gateway_owner='fixture', installed_binary='/opt/openclaw')
            fields = 'LoadState=loaded\nUser=fixture\nMainPID=123\nFragmentPath='+str(unit)+'\nDropInPaths=\nExecStart={ path=/opt/openclaw ; argv[]=/opt/openclaw gateway ; }'
            with patch.object(adapter, 'command', return_value=fields):
                original = adapter.gateway_installation_binding(require_running=True)
                self.assertEqual(original['owner'], 'fixture')
                adapter.gateway_binding = original
                adapter.verify_gateway_installation_binding()
                unit.write_text(unit.read_text()+'Environment=CHANGED=true\n')
                with self.assertRaisesRegex(bridge.AccessError, 'gateway-installation-changed'):
                    adapter.verify_gateway_installation_binding()
            for changed in (fields.replace('User=fixture','User=other'), fields.replace('path=/opt/openclaw ;','path=/opt/other ;')):
                with patch.object(adapter, 'command', return_value=changed), self.assertRaises(bridge.AccessError):
                    adapter.gateway_installation_binding()
            unit.chmod(0o666)
            with patch.object(adapter, 'command', return_value=fields), self.assertRaisesRegex(bridge.AccessError,'gateway-unit-custody-required'):
                adapter.gateway_installation_binding()

    def test_absent_host_agent_in_hybrid_guest_requires_positive_absence(self):
        adapter = bridge.SystemdAccessBridge(Path('/tmp/ods-access-test'), 'k' * 64)
        with patch.object(adapter, 'command', return_value='LoadState=not-found\nMainPID=0\nUser='):
            adapter.verify_host_agent_custody()
        for fields in ('User=\nMainPID=0', 'LoadState=error\nMainPID=0', 'LoadState=not-found\nMainPID=123'):
            with patch.object(adapter, 'command', return_value=fields), self.assertRaises(bridge.AccessError):
                adapter.verify_host_agent_custody()

    def test_existing_root_host_agent_still_requires_isolation(self):
        adapter = bridge.SystemdAccessBridge(Path('/tmp/ods-access-test'), 'k' * 64)
        with patch.object(adapter, 'command', return_value='LoadState=loaded\nActiveState=failed\nMainPID=0\nUser=root'), self.assertRaisesRegex(bridge.AccessError, 'host-agent-unavailable'):
            adapter.verify_host_agent_custody()

    @unittest.skipUnless(Path("/usr/sbin/runuser").exists(), "Linux runuser required")
    def test_owner_path_cannot_select_privileged_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attacker = root / "runuser"
            attacker.write_text("#!/bin/sh\nexit 99\n")
            attacker.chmod(0o755)
            adapter = bridge.SystemdAccessBridge(root, "k" * 64)
            adapter.home = root
            adapter.owner = types.SimpleNamespace(pw_name="fixture")
            adapter.binary = str(root / "openclaw")
            with patch.object(bridge.subprocess, "Popen", side_effect=RuntimeError("captured")) as launch:
                with self.assertRaisesRegex(RuntimeError, "captured"):
                    adapter.worker()
            args = launch.call_args.args[0]
            self.assertTrue(Path(args[0]).is_absolute())
            self.assertEqual(Path(args[0]), Path("/usr/sbin/runuser").resolve())
            self.assertNotEqual(Path(args[0]), attacker)
            self.assertEqual(launch.call_args.kwargs["env"]["PATH"].split(":")[0], str(root))

    def test_writable_launcher_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsafe = root / "runuser"
            unsafe.write_text("#!/bin/sh\nexit 99\n")
            unsafe.chmod(0o777)
            adapter = bridge.SystemdAccessBridge(root, "k" * 64)
            adapter.home = root
            adapter.owner = types.SimpleNamespace(pw_name="fixture")
            adapter.binary = str(root / "openclaw")
            with patch.object(bridge.platform, "system", return_value="Linux"), patch.object(bridge.Path, "resolve", return_value=unsafe), patch.object(bridge.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(bridge.AccessError, "unsafe-owner-launcher"):
                    adapter.worker()
            launch.assert_not_called()

    def test_macos_worker_drops_all_identity_fields_before_exec(self):
        import pwd
        owner = types.SimpleNamespace(pw_name="fixture", pw_uid=501, pw_gid=20)
        adapter = bridge.SystemdAccessBridge(Path("/tmp/ods"), "k" * 64)
        adapter.owner = owner
        with patch.object(bridge.platform, "system", return_value="Darwin"), \
                patch.object(bridge.os, "geteuid", return_value=0), \
                patch.object(pwd, "getpwnam", return_value=owner), \
                patch.object(bridge.os, "getgrouplist", return_value=[20, 80]), \
                patch.object(bridge.subprocess, "Popen") as launch:
            adapter._launch_owner_worker({"HOME": "/private/tmp/fixture"})
        args, options = launch.call_args.args[0], launch.call_args.kwargs
        self.assertEqual(args[:3], [sys.executable, "-I", "-u"])
        self.assertEqual(options["user"], 501)
        self.assertEqual(options["group"], 20)
        self.assertEqual(options["extra_groups"], [20, 80])
        self.assertNotIn("preexec_fn", options)
        self.assertNotIn("shell", options)

    def test_macos_worker_refuses_root_or_changed_identity(self):
        import pwd
        adapter = bridge.SystemdAccessBridge(Path("/tmp/ods"), "k" * 64)
        adapter.owner = types.SimpleNamespace(pw_name="fixture", pw_uid=501, pw_gid=20)
        for uid, gid, euid in ((0, 0, 0), (502, 20, 0), (501, 21, 0), (501, 20, 501)):
            with self.subTest(uid=uid, gid=gid, euid=euid), \
                    patch.object(bridge.platform, "system", return_value="Darwin"), \
                    patch.object(bridge.os, "geteuid", return_value=euid), \
                    patch.object(pwd, "getpwnam", return_value=types.SimpleNamespace(pw_uid=uid, pw_gid=gid)), \
                    patch.object(bridge.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(bridge.AccessError, "unsafe-owner-identity"):
                    adapter._launch_owner_worker({})
                launch.assert_not_called()

    @unittest.skipUnless(os.geteuid() == 0, "real credential drop requires root")
    def test_posix_child_really_has_target_credentials(self):
        import pwd
        owner = pwd.getpwnam("nobody")
        if owner.pw_uid <= 0:
            self.skipTest("positive unprivileged fixture identity required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o755)
            script = root / "access_mode_worker.py"
            script.write_text("import json,os; print(json.dumps([os.getuid(),os.geteuid(),os.getgid(),os.getgroups()]))\n")
            script.chmod(0o644)
            adapter = bridge.SystemdAccessBridge(root, "k" * 64)
            adapter.owner = owner
            with patch.object(bridge.platform, "system", return_value="Darwin"), \
                    patch.object(bridge, "__file__", str(root / "pixel_access_bridge.py")):
                with adapter._launch_owner_worker({"HOME": directory, "PATH": "/usr/bin:/bin"}) as process:
                    output, _ = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0)
            uid, euid, gid, groups = json.loads(output)
            self.assertEqual((uid, euid, gid), (owner.pw_uid, owner.pw_uid, owner.pw_gid))
            self.assertEqual(sorted(groups), sorted(os.getgrouplist(owner.pw_name, owner.pw_gid)))


class HostAgentDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.adapter = bridge.SystemdAccessBridge(self.root, "k" * 64)

    def tearDown(self):
        self.directory.cleanup()

    @contextlib.contextmanager
    def root_custody(self):
        info = types.SimpleNamespace(st_mode=0o100755, st_uid=0)
        with patch.object(bridge.platform, "system", return_value="Linux"), \
                patch.object(bridge.os, "geteuid", return_value=0), \
                patch.object(bridge.Path, "is_dir", return_value=True), \
                patch.object(bridge.Path, "lstat", return_value=info):
            yield

    def assert_unavailable_without_proc_read(self, properties):
        with self.root_custody(), \
                patch.object(self.adapter, "command", return_value=properties), \
                patch.object(bridge.Path, "read_bytes", side_effect=AssertionError("unexpected /proc read")):
            status = self.adapter.status()
        self.assertFalse(status["available"])
        self.assertFalse(status["runtime_verified"])
        self.assertEqual(status["reason"], "host-agent-unavailable")

    def test_missing_host_agent_is_allowed_for_hybrid_install_without_proc_read(self):
        properties = "MainPID=0\nUser=\nLoadState=not-found\nActiveState=failed"
        with patch.object(self.adapter, "command", return_value=properties), \
                patch.object(bridge.Path, "read_bytes", side_effect=AssertionError("unexpected /proc read")) as read:
            self.adapter.verify_host_agent_custody()
        read.assert_not_called()

    def test_inactive_root_host_agent_is_classified_before_proc_zero(self):
        self.assert_unavailable_without_proc_read(
            "MainPID=0\nUser=\nLoadState=loaded\nActiveState=failed"
        )

    def test_malformed_root_host_agent_pid_is_classified(self):
        self.assert_unavailable_without_proc_read(
            "MainPID=invalid\nUser=root\nLoadState=loaded\nActiveState=active"
        )

    def test_root_host_agent_exit_during_proc_inspection_is_classified(self):
        properties = "MainPID=987654\nUser=root\nLoadState=loaded\nActiveState=active"
        with self.root_custody(), \
                patch.object(self.adapter, "command", return_value=properties), \
                patch.object(bridge.Path, "read_bytes", side_effect=FileNotFoundError):
            status = self.adapter.status()
        self.assertFalse(status["available"])
        self.assertEqual(status["reason"], "host-agent-unavailable")

    def test_empty_root_host_agent_cmdline_is_classified(self):
        properties = "MainPID=987654\nUser=root\nLoadState=loaded\nActiveState=active"
        with self.root_custody(), \
                patch.object(self.adapter, "command", return_value=properties), \
                patch.object(bridge.Path, "read_bytes", return_value=b""):
            status = self.adapter.status()
        self.assertFalse(status["available"])
        self.assertEqual(status["reason"], "host-agent-unavailable")

    def test_live_root_host_agent_still_requires_isolated_execution(self):
        properties = "MainPID=987654\nUser=root\nLoadState=loaded\nActiveState=active"
        with self.root_custody(), \
                patch.object(self.adapter, "command", return_value=properties), \
                patch.object(bridge.Path, "read_bytes", return_value=b"python3\0/opt/ods-host-agent.py\0"):
            status = self.adapter.status()
        self.assertFalse(status["available"])
        self.assertEqual(status["reason"], "root-host-agent-isolation-required")


class LaunchdAccessBridgeTests(unittest.TestCase):
    def test_docker_inspect_and_exec_drop_to_bound_owner(self):
        adapter = object.__new__(bridge.LaunchdAccessBridge)
        context = dict(cwd='/', env={'PATH': '/owner/docker/bin:/usr/bin',
            'DOCKER_HOST': 'unix:///owner/docker.sock'}, user=501, group=20, extra_groups=[20, 12])
        with patch.object(adapter, '_docker_process_context', return_value=context), \
                patch.object(bridge.subprocess, 'run', return_value=types.SimpleNamespace(stdout='a' * 64 + ' true')) as run, \
                patch.object(bridge, '_edge_container_request', return_value={'phase': 'idle'}) as request:
            adapter._inspect_edge(timeout=3)
            self.assertEqual(run.call_args.args[0][:3], ['docker', 'inspect', 'ods-pixel-edge'])
            for key, value in context.items():
                self.assertEqual(run.call_args.kwargs[key], value)
            self.assertNotIn('preexec_fn', run.call_args.kwargs)
            self.assertNotIn('shell', run.call_args.kwargs)
            adapter._request_edge('a' * 64, '/v1/transition', 'k' * 64, None, timeout=3)
            self.assertEqual(request.call_args.kwargs['process_context'], context)
        with patch.object(adapter, '_docker_process_context', side_effect=bridge.AccessError('unsafe-owner-identity')), \
                patch.object(bridge.subprocess, 'run') as run, \
                patch.object(bridge, '_edge_container_request') as request:
            for action in (lambda: adapter._inspect_edge(timeout=3),
                           lambda: adapter._request_edge('a' * 64, '/v1/transition', 'k' * 64, None)):
                with self.assertRaisesRegex(bridge.AccessError, 'unsafe-owner-identity'):
                    action()
            run.assert_not_called()
            request.assert_not_called()

    @unittest.skipUnless(os.geteuid() == 0 and sys.platform == 'linux', 'isolated root fixture')
    def test_native_docker_child_really_runs_without_root(self):
        import pwd
        owner = pwd.getpwnam('nobody')
        adapter = object.__new__(bridge.LaunchdAccessBridge)
        adapter.owner = owner
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='ods-owner-exec-') as directory:
            root = Path(directory)
            root.chmod(0o755)
            docker = root / 'docker'
            docker.write_text('#!' + sys.executable + '\n'
                'import json,os,sys\n'
                'if sys.argv[1] == "exec": sys.stdin.buffer.read()\n'
                'print(json.dumps({"uid":os.getuid(),"euid":os.geteuid(),'
                '"gid":os.getgid(),"groups":os.getgroups(),"home":os.environ.get("HOME")}))\n')
            docker.chmod(0o755)
            env = {'PATH': str(root), 'HOME': owner.pw_dir}
            with patch.object(adapter, 'worker_environment', return_value=env):
                inspected = json.loads(adapter._inspect_edge(timeout=5))
                executed = adapter._request_edge('a' * 64, '/v1/transition', 'k' * 64, None, timeout=5)
            for result in (inspected, executed):
                self.assertEqual(result['uid'], owner.pw_uid)
                self.assertEqual(result['euid'], owner.pw_uid)
                self.assertEqual(result['gid'], owner.pw_gid)
                self.assertEqual(sorted(result['groups']), sorted(os.getgrouplist(owner.pw_name, owner.pw_gid)))
                self.assertEqual(result['home'], owner.pw_dir)

    def test_discovery_uses_existing_public_macos_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            adapter = object.__new__(bridge.LaunchdAccessBridge)
            adapter.gateway_binding = {'fixture': True}
            adapter.gateway_owner = 'fixture'
            adapter.installed_binary = '/bin/sh'
            adapter.native_port = adapter.native_key = None
            owner = types.SimpleNamespace(pw_name='fixture', pw_uid=os.getuid() or 501,
                                          pw_gid=os.getgid(), pw_dir=str(root))
            program = Path(bridge.__file__).resolve()
            protected = {program, *program.parents}
            original = Path.lstat
            def info(path):
                if path in protected:
                    return types.SimpleNamespace(st_mode=0o755, st_uid=0)
                if path == root:
                    return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=owner.pw_uid)
                return original(path)
            with patch.object(bridge.platform, 'system', return_value='Darwin'), \
                    patch.object(bridge.os, 'geteuid', return_value=0), \
                    patch.object(Path, 'lstat', info), \
                    patch('pwd.getpwnam', return_value=owner), \
                    patch.object(adapter, 'verify_gateway_installation_binding'), \
                    patch.object(adapter, '_runtime_environment', return_value={
                        'HOME': str(root), 'OPENCLAW_CONFIG_PATH': str(root / 'config.json')}), \
                    patch.object(bridge, 'private_json', return_value={'gateway': {'auth': {'token': 'k' * 64}}}), \
                    patch.object(adapter, 'configured_gateway_port', return_value=18789):
                adapter.discover()
            self.assertEqual(adapter.surface, 'darwin')
            self.assertEqual(adapter.native_port, 18789)

    def test_stopped_receipt_is_bound_to_current_provider_transaction(self):
        adapter = object.__new__(bridge.LaunchdAccessBridge)
        adapter.state = Path('/private/var/lib/ods-pixel-access')
        journal = {'kind': 'provider', 'phase': 'invoking', 'token': 'a' * 64}
        witness = {'target': 'system/com.ods.fixture'}
        with patch.object(adapter, 'pending', return_value=journal), \
                patch.object(bridge, 'atomic_json') as write:
            adapter._save_gateway_stop(witness)
            write.assert_called_once_with(adapter.state / 'launchd-stop.json',
                {'token': journal['token'], 'witness': witness})
            for token, stored in (('a' * 64, witness), ('b' * 64, witness), ('a' * 64, None)):
                with patch.object(bridge, 'private_json', return_value={'token': token, 'witness': stored}):
                    if token == journal['token'] and stored is not None:
                        self.assertEqual(adapter._load_gateway_stop(), witness)
                    else:
                        with self.assertRaisesRegex(bridge.AccessError, 'witness-unavailable'):
                            adapter._load_gateway_stop()
        for journal in (None, {'kind': 'settings', 'phase': 'invoking', 'token': 'a' * 64},
                        {'kind': 'provider', 'phase': 'complete', 'token': 'a' * 64}):
            with patch.object(adapter, 'pending', return_value=journal), \
                    self.assertRaisesRegex(bridge.AccessError, 'transaction-unavailable'):
                adapter._load_gateway_stop()

    def test_native_worker_uses_bound_config_and_runtime_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            adapter = object.__new__(bridge.LaunchdAccessBridge)
            adapter.home = root / 'home'
            adapter._runtime_config_path = root / 'openclaw.json'
            adapter.owner = types.SimpleNamespace(pw_name='fixture')
            adapter.binary = str(root / 'bin/openclaw')
            document = {'ProgramArguments': ['/usr/bin/env', '-i',
                'HOME=' + str(adapter.home), 'OPENCLAW_CONFIG_PATH=' + str(adapter._runtime_config_path),
                'OPENCLAW_STATE_DIR=' + str(root / 'state'), 'PATH=/qualified/node/bin:/usr/bin:/bin',
                'DOCKER_HOST=unix:///fixture/docker.sock', 'NODE_OPTIONS=--inspect',
                'PRIVATE_TOKEN=do-not-inherit', '/usr/bin/sandbox-exec', '-f', '/etc/ods/gateway.sb']}
            with patch.object(adapter, 'verify_gateway_installation_binding') as verify, \
                    patch.object(adapter, '_launchd_document', return_value=(b'', document)):
                env = adapter.worker_environment()
            verify.assert_called_once_with()
            self.assertEqual(env['HOME'], str(adapter.home))
            self.assertEqual(env['OPENCLAW_CONFIG_PATH'], str(root / 'openclaw.json'))
            self.assertEqual(env['OPENCLAW_STATE_DIR'], str(root / 'state'))
            self.assertEqual(env['PATH'], '/qualified/node/bin:/usr/bin:/bin')
            self.assertNotIn('NODE_OPTIONS', env)
            self.assertNotIn('PRIVATE_TOKEN', env)
            self.assertEqual(bridge.runtime_config_path(adapter), root / 'openclaw.json')
            # Existing Linux/fake adapters retain their original config path.
            self.assertEqual(bridge.runtime_config_path(types.SimpleNamespace(home=root)),
                             root / '.openclaw/openclaw.json')
            document['ProgramArguments'].insert(2, 'HOME=' + str(root / 'other'))
            with patch.object(adapter, '_launchd_document', return_value=(b'', document)), \
                    self.assertRaisesRegex(bridge.AccessError, 'environment-unavailable'):
                adapter._runtime_environment()

    def test_native_runtime_rejects_ambiguous_config_path(self):
        adapter = object.__new__(bridge.LaunchdAccessBridge)
        for value in ('relative.json', '/home/../other.json', '/home//other.json'):
            document = {'ProgramArguments': ['/usr/bin/env', '-i', 'HOME=/Users',
                'OPENCLAW_CONFIG_PATH=' + value, 'OPENCLAW_STATE_DIR=/Users/state', '/usr/bin/node']}
            with patch.object(adapter, '_launchd_document', return_value=(b'', document)), \
                    self.assertRaisesRegex(bridge.AccessError, 'path-unavailable'):
                adapter._runtime_environment()

    def test_production_bridge_rejects_gui_launchagent(self):
        with self.assertRaisesRegex(bridge.AccessError, 'system-launchdaemon-required'):
            bridge.LaunchdAccessBridge(
                '/opt/ods', 'k' * 64, gateway_target='gui/501/com.ods.fixture',
                gateway_plist='/Users/fixture/Library/LaunchAgents/com.ods.fixture.plist',
                gateway_process={'uid': 501, 'gid': 20, 'executable': '/opt/node'},
                state='/private/var/lib/ods-pixel-access', gateway_binding={},
                installed_binary='/opt/openclaw', gateway_owner='fixture')

    def test_launchd_unit_boundary_is_a_stable_non_systemd_receipt(self):
        adapter = object.__new__(bridge.LaunchdAccessBridge)
        adapter.gateway_target = 'system/com.ods.fixture'
        adapter.gateway_plist = Path('/Library/LaunchDaemons/com.ods.fixture.plist')
        adapter.gateway_service = types.SimpleNamespace(definition=lambda: 'd' * 64)
        with patch.object(adapter, '_policy_state', return_value={'activeMode': 'sandboxed'}):
            value = adapter.unit_boundary()
        self.assertEqual(json.loads(value), {
            'schemaVersion': 1, 'platform': 'macos-launchd',
            'target': 'system/com.ods.fixture',
            'plist': '/Library/LaunchDaemons/com.ods.fixture.plist',
            'definition': 'd' * 64, 'policy': {'activeMode': 'sandboxed'}})

    def test_policy_selection_does_not_claim_a_restart_or_proof(self):
        adapter = object.__new__(bridge.LaunchdAccessBridge)
        adapter.gateway_policy = {'fixture': True}
        adapter.state = Path('/private/var/lib/ods-pixel-access')
        identity = {'pid': 123, 'boot': 'test-boot', 'started': 1}
        adapter.gateway_service = types.SimpleNamespace(restart=Mock(), transaction_identity=lambda: identity)
        with patch.object(adapter, '_verify_launchd_loaded') as loaded, \
                patch.object(adapter, '_policy_state'), \
                patch.object(bridge, 'atomic_json') as write, \
                patch('pixel_macos_policy.select_policy') as select, \
                patch.object(adapter, 'native') as native:
            adapter.dropin_for(True)
            select.assert_called_once_with(adapter.gateway_policy, 'full-access')
            write.assert_called_once_with(adapter.state / 'policy-activation.json',
                {'mode': 'full-access', 'before': identity})
            loaded.assert_called_once_with()
            adapter.gateway_service.restart.assert_not_called()
            native.assert_not_called()

    def test_policy_path_must_be_the_bound_launchd_argument(self):
        adapter = object.__new__(bridge.LaunchdAccessBridge)
        adapter.gateway_policy = {}
        document = {'ProgramArguments': ['/usr/bin/env', '-i', '/usr/bin/sandbox-exec',
                    '-f', '/etc/ods/pixel-gateway.sb', '/opt/launcher']}
        state = {'active': '/private/etc/ods/pixel-gateway.sb', 'activeMode': 'sandboxed'}
        with patch.object(adapter, '_launchd_document', return_value=(b'', document)), \
                patch('pixel_macos_policy.policy_state', return_value=state):
            self.assertEqual(adapter._policy_state(), state)
            document['ProgramArguments'][4] = '/etc/ods/unapproved.sb'
            with self.assertRaisesRegex(bridge.AccessError, 'policy-binding-mismatch'):
                adapter._policy_state()

    def test_held_mode_requires_matching_profile_and_live_tool_proof(self):
        for mode in ('sandboxed', 'full-access'):
            for mismatch in (None, 'profile', 'definition', 'proof', 'config', 'baseline'):
                with self.subTest(mode=mode, mismatch=mismatch), tempfile.TemporaryDirectory() as directory:
                    adapter = object.__new__(bridge.LaunchdAccessBridge)
                    adapter.state = Path(directory)
                    (adapter.state / 'service-baseline.json').touch()
                    baseline = {'definition': 'approved', 'policy': {'activeMode': 'sandboxed'}}
                    current = {'definition': 'approved', 'policy': {'activeMode': mode}}
                    other = 'sandboxed' if mode == 'full-access' else 'full-access'
                    proof = {'pid': 123, 'proof': {'mode': mode}}
                    config = {'configured_status': mode, 'config_sha256': 'c' * 64}
                    if mismatch == 'profile': current['policy']['activeMode'] = other
                    if mismatch == 'definition': current['definition'] = 'changed'
                    if mismatch == 'baseline': baseline['policy']['activeMode'] = 'full-access'
                    if mismatch == 'proof': proof['proof']['mode'] = other
                    if mismatch == 'config': config['configured_status'] = other
                    boundary = json.dumps(current)
                    with patch.object(adapter, 'provision_probe'), \
                            patch.object(adapter, 'unit_boundary', return_value=boundary), \
                            patch.object(bridge, 'private_json', return_value={'boundary': json.dumps(baseline)}), \
                            patch.object(adapter, 'native', return_value=proof) as native, \
                            patch.object(adapter, 'worker', return_value=config), \
                            patch.object(bridge, 'atomic_json') as write:
                        if mismatch:
                            with self.assertRaises(bridge.AccessError):
                                adapter.verify_held_mode('a' * 64, mode)
                            write.assert_not_called()
                            if mismatch in ('profile', 'definition', 'baseline'):
                                native.assert_not_called()
                        else:
                            adapter.verify_held_mode('a' * 64, mode)
                            native.assert_called_once_with('probe', 'a' * 64)
                            self.assertEqual(write.call_args.args[1]['boundary'], boundary)

    def test_interrupted_policy_selection_requires_a_new_process(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = object.__new__(bridge.LaunchdAccessBridge)
            adapter.state = Path(directory)
            (adapter.state / 'policy-activation.json').touch()
            before = {'boot': 'boot-a', 'pid': 123, 'started': 100}
            record = {'mode': 'sandboxed', 'before': before}
            with patch.object(bridge, 'private_json', return_value=record), \
                    patch.object(adapter, '_policy_state', return_value={'activeMode': 'sandboxed'}):
                self.assertTrue(adapter.service_restore_required())
                for current in (before, dict(before, started=101), dict(before, pid=124),
                                dict(before, pid=124, started=101, boot='boot-b')):
                    adapter.gateway_service = types.SimpleNamespace(transaction_identity=lambda: current)
                    with self.assertRaisesRegex(bridge.AccessError, 'policy-restart-unconfirmed'):
                        adapter._policy_activation_identity('sandboxed')
                current = dict(before, pid=124, started=101)
                self.assertEqual(adapter._policy_activation_identity('sandboxed'), current)
                with self.assertRaisesRegex(bridge.AccessError, 'policy-restart-unconfirmed'):
                    adapter._policy_activation_identity('full-access')
            (adapter.state / 'policy-activation.json').unlink()
            with patch.object(adapter, '_policy_state', return_value={'activeMode': 'sandboxed'}):
                self.assertFalse(adapter.service_restore_required())

    def test_launchd_document_requires_owner_group_and_system_identity(self):
        import grp
        import pwd
        owner = pwd.getpwuid(os.getuid())
        group = grp.getgrgid(owner.pw_gid).gr_name
        adapter = object.__new__(bridge.LaunchdAccessBridge)
        adapter.gateway_owner = owner.pw_name
        adapter.gateway_target = 'system/com.ods.fixture'
        adapter.gateway_plist = Path('/Library/LaunchDaemons/com.ods.fixture.plist')
        expected = {'Label': 'com.ods.fixture', 'UserName': owner.pw_name,
                    'GroupName': group, 'ProgramArguments': ['/usr/bin/env', '-i', '/opt/node']}
        import pixel_macos_custody
        with patch.object(pixel_macos_custody, 'protected_bytes',
                          return_value=__import__('plistlib').dumps(expected)):
            _, actual = adapter._launchd_document()
        self.assertEqual(actual, expected)
        bad = dict(expected, UserName='other')
        with patch.object(pixel_macos_custody, 'protected_bytes',
                          return_value=__import__('plistlib').dumps(bad)), \
                self.assertRaisesRegex(bridge.AccessError, 'gateway-launchdaemon-identity-mismatch'):
            adapter._launchd_document()
class NativeReacquireTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.adapter = bridge.SystemdAccessBridge(root, 'k' * 64, state=root / 'state')
        self.adapter.home = root
        self.adapter.owner = types.SimpleNamespace(pw_uid=os.getuid())
        self.adapter.native_port, self.adapter.native_key = 18789, 'fixture'
        self.adapter.command = lambda *_args, **_kwargs: '123'
        self.token = 'd' * 64
        self.snapshot = dict(available=True, phase='held', active=0, pid=123, revision='a' * 64)
        self.record = root / '.openclaw/.ods-access-runtime/state.json'
        self.record.parent.mkdir(parents=True, mode=0o700)
        self.write_hold()

    def write_hold(self, **changes):
        self.record.write_text(json.dumps(dict(phase='held', revision=self.snapshot['revision'],
            tokenHash=hashlib.sha256(self.token.encode()).hexdigest(), **changes)))
        self.record.chmod(0o600)

    def test_one_409_reacquire_reuses_exact_request_after_unchanged_owned_hold_readback(self):
        rejected = bridge.AccessError('runtime-unavailable-or-busy', http_status=409)
        with patch.object(self.adapter, 'http', side_effect=[self.snapshot, rejected, self.snapshot, self.snapshot]) as http:
            result = self.adapter.native('acquire', self.token)
        self.assertEqual(result, self.snapshot)
        self.assertEqual(http.call_count, 4)
        self.assertEqual(http.call_args_list[1].args[3], http.call_args_list[3].args[3])
        self.assertIsNone(http.call_args_list[2].args[3])

    def test_changed_pid_revision_activity_or_hold_never_retries_post(self):
        for drift in ({'pid':124}, {'revision':'b'*64}, {'active':1}, {'phase':'idle'}, {'available':False}):
            with self.subTest(drift=drift), patch.object(self.adapter, 'http', side_effect=[self.snapshot,
                    bridge.AccessError('runtime-unavailable-or-busy', http_status=409), {**self.snapshot, **drift}]) as http:
                with self.assertRaises(bridge.AccessError): self.adapter.native('acquire', self.token)
                self.assertEqual(http.call_count, 3)

    def test_no_retry_for_new_hold_foreign_token_missing_custody_or_ambiguous_transport(self):
        cases = [('new', {**self.snapshot,'phase':'idle'}, self.token, 409),
                 ('foreign', self.snapshot, 'e'*64, 409),
                 ('timeout', self.snapshot, self.token, None),
                 ('forbidden', self.snapshot, self.token, 403),
                 ('missing', self.snapshot, self.token, 409)]
        for label, snapshot, token, code in cases:
            if label == 'missing': self.record.unlink()
            with self.subTest(case=label), patch.object(self.adapter, 'http', side_effect=[snapshot,
                    bridge.AccessError('runtime-unavailable-or-busy', http_status=code)]) as http:
                with self.assertRaises(bridge.AccessError): self.adapter.native('acquire', token)
                self.assertEqual(http.call_count, 2)

    def test_a_second_refusal_and_probe_failures_are_never_replayed(self):
        rejected = bridge.AccessError('runtime-unavailable-or-busy', http_status=409)
        with patch.object(self.adapter, 'http', side_effect=[self.snapshot,rejected,self.snapshot,rejected]) as http:
            with self.assertRaises(bridge.AccessError): self.adapter.native('acquire', self.token)
            self.assertEqual(http.call_count, 4)
        with patch.object(self.adapter, 'http', side_effect=[self.snapshot,rejected]) as http:
            with self.assertRaises(bridge.AccessError): self.adapter.native('probe', self.token)
            self.assertEqual(http.call_count, 2)

    def test_custody_change_during_readback_cannot_reacquire(self):
        def reply(_origin, _path, _key, payload=None, **_kwargs):
            if payload: raise bridge.AccessError('runtime-unavailable-or-busy', http_status=409)
            if self.calls:
                state=json.loads(self.record.read_text()); state['tokenHash']='0'*64
                self.record.write_text(json.dumps(state))
            self.calls += 1
            return self.snapshot
        self.calls = 0
        with patch.object(self.adapter, 'http', side_effect=reply) as http:
            with self.assertRaises(bridge.AccessError): self.adapter.native('acquire', self.token)
            self.assertEqual(http.call_count, 3)


class FakeBridge(bridge.SystemdAccessBridge):
    """Fake the installed services, retaining the real coordinator and journals."""
    def __init__(self, root):
        super().__init__(root, "k" * 64, state=root / "state", dropin=root / "dropin")
        self.gateway_service.boot_identity = lambda: '11111111-2222-3333-4444-555555555555'
        self.mode, self.managed, self.pid = "sandboxed", False, 123
        self.active = 0
        self.native_phase = self.edge_phase = "idle"
        self.nrev, self.erev = "a" * 64, "b" * 64
        self.proof = None
        self.probe_failure = None
        self.log = []
        self.fail = None

    def use_launchd_fixture(self, monkeypatch):
        """Real adapter/transactions, simulated kernel and launchd (not custody)."""
        from pixel_gateway_service import LaunchdGatewayService
        target = 'system/com.ods.fixture'
        def native_command(args, timeout=20):
            if args == ['/bin/launchctl', 'print', target]:
                fields = '\tstate = not running' if self.stopped else f'\tstate = running\n\tpid = {self.pid}'
                return target + ' = {\n' + fields + '\n}'
            if args == ['/bin/launchctl', 'kickstart', '-k', target]:
                return self.command(['restart'], timeout=timeout)
            if args == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
                return '11111111-2222-3333-4444-555555555555'
            raise AssertionError('Unexpected platform operation: ' + repr(args))
        monkeypatch.setattr('pixel_macos_process.process_identity',
            lambda pid, **kwargs: (pid, 1700000000, self.started, 501, 20, 501, 20, 501, 20, '/fixture/node'))
        self.gateway_service = LaunchdGatewayService(native_command, bridge.AccessError, target, lambda: None,
            process={'uid':501, 'gid':20, 'executable':'/fixture/node'})

    def discover(self, *, allow_installing=False):
        if allow_installing:
            self.log.append("discover-installing")
        self.surface = "linux-systemd"
        self.home = self.install
        self.owner = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_name="fixture")
        self.native_origin = "http://127.0.0.1:18789"
        self.native_key = "test"

    @contextlib.contextmanager
    def locked(self):
        self.state.mkdir(mode=0o700, exist_ok=True)
        yield

    def native(self, operation=None, token=None, *, timeout=60):
        if operation:
            self.log.append("native-" + operation)
            if self.fail == operation:
                if operation == "probe": self.probe_failure = "core-exec"
                raise bridge.AccessError("injected-" + operation)
            if operation == "acquire":
                if self.active: raise bridge.AccessError("busy")
                self.native_phase = "held"
            elif operation == "release": self.native_phase = "idle"
            elif operation == "probe": self.proof = {"mode": self.mode, "executed": True, "pid": self.pid}
        return {"available": True, "phase": self.native_phase, "revision": self.nrev,
                "pid": self.pid, "active": self.active, "proof": self.proof,
                "probe_failure": self.probe_failure}

    def edge(self, operation=None, token=None, revision=None):
        if operation:
            self.log.append("edge-" + operation)
            if self.fail == "edge-" + operation: raise bridge.AccessError("injected-edge")
            self.edge_phase = "idle" if operation == "release" else "held"
        return {"capability": "available", "phase": self.edge_phase, "revision": self.erev, "streams": 0}

    def worker(self, operation="status", **kwargs):
        if operation != "status":
            self.log.append("controller-" + operation)
            if kwargs["busy"](): raise bridge.AccessError("busy")
            self.mode, self.managed = operation, operation == "full-access"
            self.owner_config()
            if not kwargs["restart"](): raise bridge.AccessError("restart-failed")
        return {"configured_status": self.mode, "config_sha256": bridge.digest(self.mode), "managed": self.managed}

    def owner_config(self):
        folder = self.home / ".openclaw"
        folder.mkdir(exist_ok=True)
        bridge.atomic_json(folder / "openclaw.json", {"agents": {"list": [{"id": "pixel",
            "sandbox": {"mode": "off" if self.mode == "full-access" else "all"},
            "tools": {"exec": {"host": "gateway" if self.mode == "full-access" else "sandbox"}}}]}})

    def command(self, args, timeout=20):
        if "restart" in args:
            self.log.append("restart")
            self.pid += 1
            self.proof = None
            return ""
        return str(self.pid)

    def http(self, *_args, **_kwargs): return {"ok": True}
    def provision_probe(self): self.log.append("provision-probe")
    def dropin_for(self, enabled):
        # Filesystem service adapter is simulated; never write to real systemd.
        if enabled: self.dropin.write_text("[Service]\nProtectSystem=false\nProtectHome=false\n")
        elif self.dropin.exists(): self.dropin.unlink()
    def unit_boundary(self):
        return ("ProtectSystem=no\nProtectHome=no" if self.dropin.exists() else "ProtectSystem=strict\nProtectHome=tmpfs") + "\nNoNewPrivileges=yes"


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ods-access-bridge-test-"))
        self.runtime = FakeBridge(self.root)
        original = bridge.private_json
        # Dedicated owner-owned fixtures emulate root-owned journals; production
        # ownership checks themselves are exercised separately below.
        self.patched = patch.object(bridge, "private_json", side_effect=lambda path, _uid, maximum=1048576: original(path, os.getuid(), maximum))
        self.patched.start()

    def tearDown(self): self.patched.stop()
    def request(self, mode="full-access"):
        return {"mode": mode, "confirmed": mode == "full-access", "revision": self.runtime.status()["revision"]}

    def test_explicit_confirmation_and_exact_request_schema_before_mutation(self):
        for request in ({"mode": "full-access", "confirmed": False, "revision": "a" * 64},
                        {"mode": "full-access", "confirmed": "yes", "revision": "a" * 64},
                        {"mode": "full-access", "confirmed": True, "revision": "a" * 64, "path": "/etc"}):
            with self.assertRaises(bridge.AccessError): self.runtime.change(request)
        self.assertEqual(self.runtime.log, [])
        self.assertFalse(self.runtime.state.exists())

    def test_busy_and_stale_revision_do_not_write_receipts_or_settings(self):
        request = self.request()
        self.runtime.active = 1
        with self.assertRaises(bridge.AccessError): self.runtime.change(request)
        self.assertFalse((self.runtime.state / "transition.json").exists())
        self.runtime.active = 0
        self.runtime.nrev = "c" * 64
        with self.assertRaises(bridge.AccessError): self.runtime.change(request)
        self.assertEqual(self.runtime.log, [])

    def test_access_restore_cannot_consume_settings_or_unknown_root_journal(self):
        for kind in ("settings", "unknown"):
            with self.subTest(kind=kind):
                with self.runtime.locked():
                    bridge.atomic_json(self.runtime.state / "transition.json", {
                        "kind": kind, "phase": "error", "token": "d" * 64, "edge_revision": "b" * 64})
                before = (self.runtime.state / "transition.json").read_bytes()
                with self.assertRaisesRegex(bridge.AccessError, "settings-recovery-required"):
                    self.runtime.change(self.request("sandboxed"))
                self.assertEqual((self.runtime.state / "transition.json").read_bytes(), before)
                self.assertEqual(self.runtime.log, [])

    def test_legacy_root_access_journal_without_kind_still_recovers(self):
        self.runtime.fail = "probe"
        with self.assertRaises(bridge.AccessError):
            self.runtime.change(self.request("sandboxed"))
        journal = self.runtime.pending()
        journal.pop("kind")
        bridge.atomic_json(self.runtime.state / "transition.json", journal)
        self.runtime.fail = None
        self.assertEqual(self.runtime.change(self.request("sandboxed"))["effective_mode"], "sandboxed")

    def test_full_enable_and_restore_require_executed_proof_and_preserve_boundaries(self):
        self.assertEqual(self.runtime.status()["effective_mode"], "unknown")
        enabled = self.runtime.change(self.request())
        self.assertEqual(enabled["effective_mode"], "full-access")
        self.assertTrue(enabled["runtime_verified"])
        self.assertEqual(self.runtime.dropin.read_text(), "[Service]\nProtectSystem=false\nProtectHome=false\n")
        self.assertLess(self.runtime.log.index("native-acquire"), self.runtime.log.index("controller-full-access"))
        self.assertLess(self.runtime.log.index("native-probe"), self.runtime.log.index("edge-release"))
        restored = self.runtime.change(self.request("sandboxed"))
        self.assertEqual(restored["effective_mode"], "sandboxed")
        self.assertFalse(self.runtime.dropin.exists())
        self.assertFalse((self.runtime.state / "transition.json").exists())

    def test_failed_probe_retains_both_gates_and_recovery_receipt(self):
        self.runtime.fail = "probe"
        with self.assertRaisesRegex(bridge.AccessError, "runtime-proof-core-exec"):
            self.runtime.change(self.request())
        self.assertEqual(self.runtime.native_phase, "held")
        self.assertEqual(self.runtime.edge_phase, "held")
        self.assertEqual(self.runtime.pending()["phase"], "error")
        self.assertEqual(self.runtime.status()["effective_mode"], "unknown")
        self.runtime.fail = None
        self.assertEqual(self.runtime.change(self.request("sandboxed"))["effective_mode"], "sandboxed")

    def test_native_admission_failure_identifies_bounded_transition_stage(self):
        original = self.runtime.native
        def native(operation=None, token=None, *, timeout=60):
            if operation == "acquire": raise bridge.AccessError("runtime-unavailable-or-busy")
            return original(operation, token, timeout=timeout)
        with patch.object(self.runtime, "native", side_effect=native), \
                self.assertRaisesRegex(bridge.AccessError, "runtime-initial-acquire-unavailable"):
            self.runtime.change(self.request())
        self.assertEqual(self.runtime.pending()["error"], "runtime-initial-acquire-unavailable")

    def test_probe_diagnostic_is_allowlisted_and_arbitrary_value_is_not_forwarded(self):
        self.runtime.fail = "probe"
        self.runtime.probe_failure = "private-path-etc-shadow"
        original = self.runtime.native
        def native(operation=None, token=None, *, timeout=60):
            if operation == "probe": raise bridge.AccessError("runtime-unavailable-or-busy")
            result = original(operation, token, timeout=timeout)
            result["probe_failure"] = "private-path-etc-shadow"
            return result
        with patch.object(self.runtime, "native", side_effect=native), \
                self.assertRaisesRegex(bridge.AccessError, "runtime-unavailable-or-busy"):
            self.runtime.change(self.request())
        self.assertNotIn("private-path-etc-shadow", self.runtime.pending()["error"])

    def test_probe_diagnostic_requires_runtime_to_remain_held(self):
        original = self.runtime.native
        failed = [False]
        def native(operation=None, token=None, *, timeout=60):
            if operation == "probe":
                failed[0] = True
                raise bridge.AccessError("runtime-unavailable-or-busy")
            result = original(operation, token, timeout=timeout)
            if failed[0]: result.update(phase="idle", probe_failure="core-exec")
            return result
        with patch.object(self.runtime, "native", side_effect=native), \
                self.assertRaisesRegex(bridge.AccessError, "runtime-unavailable-or-busy"):
            self.runtime.change(self.request())

    def test_pristine_safer_probe_recovery_does_not_restart_unchanged_gateway(self):
        self.runtime.fail = "probe"
        with self.assertRaises(bridge.AccessError):
            self.runtime.change(self.request("sandboxed"))
        self.assertFalse(self.runtime.managed)
        self.assertEqual(self.runtime.pending()["phase"], "error")
        self.assertEqual(self.runtime.pending()["error"], "runtime-proof-core-exec")
        self.runtime.fail = None
        restored = self.runtime.change(self.request("sandboxed"))
        self.assertTrue(restored["runtime_verified"])
        self.assertEqual(restored["effective_mode"], "sandboxed")
        self.assertNotIn("restart", self.runtime.log)

    def test_slow_gateway_start_is_observed_without_restarting_again(self):
        original = self.runtime.native
        elapsed = [0.0]
        reads = []
        def native(operation=None, token=None, *, timeout=60):
            if "restart" in self.runtime.log and operation is None:
                reads.append(timeout)
                if len(reads) <= 40:
                    raise bridge.AccessError("native-idle-unconfirmed")
            return original(operation, token, timeout=timeout)
        def sleep(seconds): elapsed[0] += seconds
        with patch.object(self.runtime, "native", side_effect=native), \
                patch.object(bridge.time, "monotonic", side_effect=lambda: elapsed[0]), \
                patch.object(bridge.time, "sleep", side_effect=sleep):
            result = self.runtime.change(self.request())
        self.assertTrue(result["runtime_verified"])
        self.assertEqual(self.runtime.log.count("restart"), 1)
        self.assertEqual(reads[:40], [3] * 40)

    def test_gateway_readiness_deadline_retains_both_holds(self):
        original = self.runtime.native
        elapsed = [0.0]
        def native(operation=None, token=None, *, timeout=60):
            if "restart" in self.runtime.log and operation is None:
                raise bridge.AccessError("native-idle-unconfirmed")
            return original(operation, token, timeout=timeout)
        def sleep(seconds): elapsed[0] += seconds
        with patch.object(self.runtime, "native", side_effect=native), \
                patch.object(bridge.time, "monotonic", side_effect=lambda: elapsed[0]), \
                patch.object(bridge.time, "sleep", side_effect=sleep):
            with self.assertRaises(bridge.AccessError):
                self.runtime.change(self.request())
        self.assertEqual(elapsed[0], 120)
        self.assertEqual(self.runtime.log.count("restart"), 1)
        self.assertEqual(self.runtime.pending()["phase"], "error")
        self.assertEqual(self.runtime.native_phase, "held")
        self.assertEqual(self.runtime.edge_phase, "held")

    def test_unavailable_inspection_preserves_pending_transition_for_polling(self):
        self.runtime.fail = "probe"
        with self.assertRaises(bridge.AccessError):
            self.runtime.change(self.request("sandboxed"))
        with patch.object(self.runtime, "worker", side_effect=bridge.AccessError("controller-lock-busy")):
            status = self.runtime.status()
        self.assertTrue(status["pending"])
        self.assertFalse(status["available"])
        self.assertFalse(status["runtime_verified"])
        self.assertIsNone(status["revision"])

    def test_edge_release_failure_keeps_external_admission_blocked(self):
        self.runtime.fail = "edge-release"
        with self.assertRaises(bridge.AccessError): self.runtime.change(self.request())
        self.assertEqual(self.runtime.native_phase, "idle")
        self.assertEqual(self.runtime.edge_phase, "held")
        self.assertLess(self.runtime.log.index("native-release"), self.runtime.log.index("edge-release"))
        self.assertEqual(self.runtime.pending()["phase"], "error")

    def test_native_release_failure_keeps_both_admission_gates_held(self):
        self.runtime.fail = "release"
        with self.assertRaises(bridge.AccessError): self.runtime.change(self.request())
        self.assertEqual(self.runtime.native_phase, "held")
        self.assertEqual(self.runtime.edge_phase, "held")
        self.assertNotIn("edge-release", self.runtime.log)
        self.assertEqual(self.runtime.pending()["phase"], "error")

    def test_proof_invalidated_by_external_config_change_or_gateway_restart(self):
        self.runtime.change(self.request())
        self.runtime.pid += 1
        self.assertEqual(self.runtime.status()["effective_mode"], "unknown")

    def test_root_owned_gateway_port_overrides_missing_or_stale_owner_config(self):
        explicit = bridge.SystemdAccessBridge(self.root, "k" * 64, gateway_port=18790)
        self.assertEqual(explicit.configured_gateway_port({}), 18790)
        self.assertEqual(explicit.configured_gateway_port({"gateway": {"port": 18789}}), 18790)
        legacy = bridge.SystemdAccessBridge(self.root, "k" * 64)
        self.assertEqual(legacy.configured_gateway_port({}), 18789)
        self.assertEqual(legacy.configured_gateway_port({"gateway": {"port": 19432}}), 19432)
        for invalid in (0, 65536, True, "18790"):
            with self.subTest(invalid=invalid), self.assertRaises(bridge.AccessError):
                bridge.SystemdAccessBridge(self.root, "k" * 64, gateway_port=invalid)

    def test_stopped_gateway_recovery_requires_empty_unit_and_same_durable_lease(self):
        real = bridge.SystemdAccessBridge(self.root, "k" * 64)
        real.home = self.root
        real.owner = types.SimpleNamespace(pw_uid=os.getuid())
        directory = self.root / ".openclaw/.ods-access-runtime"
        directory.mkdir(parents=True, mode=0o700)
        token = "c" * 64
        bridge.atomic_json(directory / "state.json", {"phase": "held", "revision": "d" * 64,
                           "tokenHash": bridge.hashlib.sha256(token.encode()).hexdigest()})
        real.command = lambda *_args, **_kwargs: "MainPID=0\nActiveState=failed\nControlGroup="
        self.assertTrue(real.stopped_native(token)["stopped"])
        with self.assertRaises(bridge.AccessError): real.stopped_native("e" * 64)
        real.command = lambda *_args, **_kwargs: "MainPID=234\nActiveState=active\nControlGroup="
        with self.assertRaises(bridge.AccessError): real.stopped_native(token)
        real.command = lambda *_args, **_kwargs: "MainPID=0\nActiveState=failed\nControlGroup=/../../etc"
        with self.assertRaises(bridge.AccessError): real.stopped_native(token)


if __name__ == "__main__": unittest.main()
