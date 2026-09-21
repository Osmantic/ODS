"""Publish the approved native service set without loading launchd jobs.

The installer holds its deployment lock and keeps admission closed until all
runtime directories, services and readiness checks have been completed.
"""
import hashlib
import base64
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import sys
import time
import stat
import subprocess
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone


def helper(name):
    spec = importlib.util.spec_from_file_location('native_services_' + name.replace('-', '_'),
        Path(__file__).with_name('pixel-native-' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish(*, bundle, expected_digest, expected_ref, expected_config_digest,
            identity, owner, environment, workspace, port, python,
            program_root='/usr/local/libexec/ods-pixel-services',
            definitions='/Library/LaunchDaemons', state='/private/var/lib/pixel-ops-broker'):
    """Publish exact verified snapshots under the caller's deployment lock.

    Expected identities must come from the approved transaction. This method
    never accepts candidate-supplied installation paths or launches services.
    Identical partial publication can be replayed by the individual publishers.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    snapshots = helper('config').verified_services(bundle, expected_digest=expected_digest,
        expected_ref=expected_ref, expected_config_digest=expected_config_digest)
    root, definitions = Path(program_root), Path(definitions)
    if not root.is_absolute() or not definitions.is_absolute():
        raise ValueError('absolute-native-service-paths-required')
    operations, manager, promoter = [helper(name + '-service') for name in ('ops', 'manager', 'promoter')]
    manager_options = dict(python=python, program_root=root / 'manager', environment=environment,
        owner=owner, port=port)
    promoter_options = dict(python=python, program_root=root / 'promoter', workspace=workspace,
        owner=owner, state=state)
    import json
    # Reject inconsistent render inputs before the first publisher writes files.
    operations.render(identity=identity, python=python, program_root=root / 'operations',
        state=state, policy=json.loads(snapshots['operations/policy.json']))
    manager.render(**manager_options)
    promoter.render(**promoter_options)
    installer = operations.installer_helpers()
    helpers = [(root / name, body, 0o640 if name.endswith('.json') else 0o644,
                identity['gid'] if name.endswith('.json') else 0)
               for name, body in snapshots.items() if name.startswith('helpers/')]
    for path, body, mode, gid in helpers:
        installer._preflight_file(path, body, mode=mode, uid=0, gid=gid)
    for path, body, mode, gid in helpers:
        installer._write_exact(path, body, mode=mode, uid=0, gid=gid)
    result = {}
    for name, module, options in (('manager', manager, manager_options), ('promoter', promoter, promoter_options)):
        sources = {path.split('/', 1)[1]: body for path, body in snapshots.items() if path.startswith(name + '/')}
        result[name] = module.publish(sources=sources,
            expected_sha256={path: hashlib.sha256(body).hexdigest() for path, body in sources.items()},
            definition=definitions / ('com.ods.pixel-native-' + name + '.plist'), **options)
    result['operations'] = operations.publish(broker_body=snapshots['operations/broker.py'],
        expected_broker_sha256=hashlib.sha256(snapshots['operations/broker.py']).hexdigest(),
        policy_body=snapshots['operations/policy.json'], identity=identity, python=python,
        program_root=root / 'operations', state=state,
        definition=definitions / 'com.ods.pixel-native-operations.plist')
    return result


def activation_adapters(*, definitions, expected, owner, identity, python, save_stop=None, load_stop=None):
    """Bind controls to approved plist bytes, not the currently loaded jobs."""
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    if ((save_stop is None) != (load_stop is None)
            or save_stop is not None and (not callable(save_stop) or not callable(load_stop))):
        raise ValueError('native-service-stop-store-required')
    names = {'manager', 'promoter', 'operations'}
    if set(definitions) != names or set(expected) != names:
        raise ValueError('complete-native-service-bindings-required')
    operations = helper('ops-service')
    installer = operations.installer_helpers()
    custody = operations.custody
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'bin'))
    from pixel_gateway_service import LaunchdGatewayService
    entry = pwd.getpwnam(owner)
    broker = pwd.getpwnam(identity['name'])
    if (entry.pw_uid <= 0 or identity['name'] != '_ods_pixel_ops'
            or broker.pw_uid != identity['uid'] or broker.pw_gid != identity['gid']):
        raise ValueError('native-service-identity-mismatch')
    custody.protected_bytes(str(python), limit=128 * 1024 * 1024)
    users = {'manager': (owner, entry.pw_uid, entry.pw_gid),
        'promoter': ('root', 0, 0), 'operations': (broker.pw_name, broker.pw_uid, broker.pw_gid)}
    adapters = {}
    for name in sorted(names):
        path, body = Path(definitions[name]), expected[name]
        label = 'com.ods.pixel-native-' + name
        if type(body) is not bytes or not 0 < len(body) <= 65536 or path.name != label + '.plist':
            raise ValueError('approved-native-service-definition-required')
        document = plistlib.loads(body)
        username, uid, gid = users[name]
        arguments = document.get('ProgramArguments', [])
        if (document.get('Label') != label or document.get('UserName') != username
                or type(arguments) is not list or arguments.count(str(python)) != 1):
            raise ValueError('native-service-definition-identity-mismatch')
        index = arguments.index(str(python))
        if arguments[index + 1:index + 3] != ['-I', '-B']:
            raise ValueError('native-service-isolated-python-required')
        target = 'system/' + label
        def verify_definition(path=path, body=body):
            if custody.protected_bytes(str(path)) != body:
                raise installer.InstallError('native-service-definition-changed')
        def verify(path=path, document=document, target=target, verify_definition=verify_definition):
            verify_definition()
            custody.verify_loaded_launchd_definition(
                installer._command(['/bin/launchctl', 'print', target]), target, path, document)
        verify_definition()
        adapters[name] = LaunchdGatewayService(installer._command, installer.InstallError, target, verify,
            plist=path, verify_definition=verify_definition,
            process={'uid': uid, 'gid': gid, 'executable': str(python)},
            save_stop=(lambda value, name=name: save_stop(name, value)) if save_stop else None,
            load_stop=(lambda name=name: load_stop(name)) if load_stop else None,
            allow_root_process=name == 'promoter')
    return adapters


def recovery_adapters(record, *, owner, save_stop, load_stop):
    """Rebuild controls from a caller-verified protected transaction record.

    This never infers approval from the currently loaded jobs. Definitions must
    still match the recorded bytes and process identities must still qualify.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    try:
        if (type(record) is not dict or set(record) != {'schemaVersion', 'owner', 'identity', 'python', 'definitions'}
                or type(record['schemaVersion']) is not int or record['schemaVersion'] != 1
                or record['owner'] != owner or not isinstance(record['python'], str)
                or not Path(record['python']).is_absolute()
                or type(record['definitions']) is not dict
                or set(record['definitions']) != {'manager', 'promoter', 'operations'}):
            raise ValueError()
        definitions, expected = {}, {}
        for name, value in record['definitions'].items():
            if (type(value) is not dict or set(value) != {'path', 'body'}
                    or not isinstance(value['path'], str) or not Path(value['path']).is_absolute()
                    or not isinstance(value['body'], str) or len(value['body']) > 90000):
                raise ValueError()
            body = base64.b64decode(value['body'], validate=True)
            if base64.b64encode(body).decode('ascii') != value['body']:
                raise ValueError()
            definitions[name], expected[name] = Path(value['path']), body
    except (ValueError, TypeError, KeyError):
        raise ValueError('native-service-recovery-record-invalid') from None
    return activation_adapters(definitions=definitions, expected=expected, owner=owner,
        identity=record['identity'], python=record['python'], save_stop=save_stop, load_stop=load_stop)


def stop_new(*, services, attempted, checkpoint, phase='services-stopped'):
    """Stop only the approved newly installed jobs, retaining recovery evidence."""
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    order = ('manager', 'promoter', 'operations')
    if (set(services) != set(order) or list(attempted) != list(order[:len(attempted)])
            or not callable(checkpoint)):
        raise ValueError('complete-native-service-activation-required')
    for name in attempted:
        if services[name].target != 'system/com.ods.pixel-native-' + name:
            raise ValueError('native-service-target-mismatch')
    installer = helper('ops-service').installer_helpers()
    incomplete = []
    checkpoint({'phase': 'stopping-services', 'attempted': list(attempted), 'requiresRecovery': True})
    for name in reversed(attempted):
        service = services[name]
        try:
            service.verify_definition()
            installer._command(['/bin/launchctl', 'disable', service.target])
            service.stop()
            service.assert_stopped()
        except Exception:
            try:
                service.verify_definition()
                service.assert_stopped()
                continue
            except Exception:
                pass
            incomplete.append(name)
            try:
                # Only unload our verified job. This does not prove descendants
                # have exited, so the uncertain stop remains in the journal.
                service.verify()
                installer._command(['/bin/launchctl', 'disable', service.target])
                installer._command(['/bin/launchctl', 'bootout', service.target])
            except Exception:
                pass
    checkpoint({'phase': 'service-stop-failed' if incomplete and phase == 'services-stopped' else phase,
        'attempted': list(attempted),
        'stopUnconfirmed': incomplete, 'requiresRecovery': True})
    if incomplete:
        raise ValueError('native-service-stop-unconfirmed')


def activate_new(*, services, readiness, checkpoint):
    """Activate approved adapters while the caller holds lock and admission.

    Adapters and readiness callbacks are constructed by the trusted installer,
    never supplied by an agent request. The caller persists checkpoints in its
    root-owned transaction journal. This does not implement updates or reopen
    admission; the gateway's end-to-end proof remains required afterwards.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    order = ('manager', 'promoter', 'operations')
    if (set(services) != set(order) or set(readiness) != set(order)
            or not all(callable(readiness[name]) for name in order) or not callable(checkpoint)):
        raise ValueError('complete-native-service-activation-required')
    installer = helper('ops-service').installer_helpers()
    for name in order:
        service = services[name]
        if service.target != 'system/com.ods.pixel-native-' + name:
            raise ValueError('native-service-target-mismatch')
        service.verify_definition()
        if installer._job_disabled(service.target):
            raise ValueError('native-service-intentionally-disabled')
        try:
            installer._command(['/bin/launchctl', 'print', service.target])
        except installer.InstallError as error:
            if getattr(error, 'returncode', None) != 113:
                raise
        else:
            raise ValueError('native-service-already-loaded')
    attempted = []
    try:
        for name in order:
            service = services[name]
            checkpoint({'phase': 'starting', 'service': name, 'attempted': attempted + [name]})
            attempted.append(name)
            service.verify_definition()
            installer._command(['/bin/launchctl', 'bootstrap', 'system', str(service.plist)], timeout=30)
            deadline = time.monotonic() + 20
            while True:
                try:
                    service.process_identity()
                    break
                except ValueError as error:
                    if (str(error) not in ('runtime-unavailable-or-busy', 'gateway-process-executable-mismatch')
                            or time.monotonic() >= deadline):
                        raise
                    time.sleep(0.1)
            if readiness[name]() is not True:
                raise ValueError('native-service-not-ready')
            checkpoint({'phase': 'verified', 'service': name, 'attempted': list(attempted)})
        checkpoint({'phase': 'services-active', 'attempted': list(attempted), 'requiresGatewayProof': True})
    except BaseException:
        stop_new(services=services, attempted=attempted, checkpoint=checkpoint, phase='activation-failed')
        raise


def readiness_checks(*, owner, identity, python,
                     program_root='/usr/local/libexec/ods-pixel-services',
                     state='/private/var/lib/pixel-ops-broker'):
    """Build non-mutating service probes for the approved initial installation.

    The Operations probe submits only host.os-release; it never approves a request or
    invokes an extension mutation. Call after publication, under the deployment
    lock, with admission still closed.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    owner_entry = pwd.getpwnam(owner)
    broker = pwd.getpwnam(identity['name'])
    if (owner_entry.pw_uid <= 0 or identity['name'] != '_ods_pixel_ops'
            or broker.pw_uid != identity['uid'] or broker.pw_gid != identity['gid']
            or owner_entry.pw_uid == broker.pw_uid):
        raise ValueError('native-service-identity-mismatch')
    custody = helper('ops-service').custody
    custody.protected_bytes(str(python), limit=128 * 1024 * 1024)
    root, state = Path(program_root), Path(state)
    scripts = {name: root / name / filename for name, filename in (
        ('manager', 'extension_manager.py'), ('promoter', 'artifact_promoter.py'))}
    for path in scripts.values():
        custody.protected_bytes(str(path))

    def client(name, user, arguments):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                result = subprocess.run([str(python), '-I', '-B', str(scripts[name]), *arguments],
                    user=user.pw_uid, group=user.pw_gid, extra_groups=[], cwd='/',
                    env={'PATH': '/usr/bin:/bin'}, stdin=subprocess.DEVNULL,
                    capture_output=True, timeout=min(5, max(0.01, deadline - time.monotonic())))
                value = json.loads(result.stdout) if result.returncode == 0 else None
                if isinstance(value, dict) and value.get('schemaVersion') == 1:
                    if name == 'manager' and value.get('kind') == 'ods-pixel-extension-inventory' \
                            and value.get('outcome') == 'succeeded':
                        return True
                    if name == 'promoter' and value.get('status') == 'ok':
                        # The promoter CLI validates the exact health contract.
                        return True
            except (subprocess.TimeoutExpired, ValueError):
                pass
            time.sleep(0.1)
        raise ValueError('native-' + name + '-readiness-failed')

    def operations():
        job = 'ops-' + str(int(time.time() * 1000)) + '-' + uuid.uuid4().hex[:12]
        body = json.dumps({'schemaVersion': 1, 'jobId': job,
            'createdAt': datetime.now(timezone.utc).isoformat(), 'kind': 'action',
            'target': 'ods-host', 'action': 'host.os-release'}).encode()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with ExitStack() as stack:
            parent = stack.enter_context(custody.protected_directory(state.parent))
            def directory(name, parent, uid):
                fd = os.open(name, flags, dir_fd=parent)
                stack.callback(os.close, fd)
                info = os.fstat(fd)
                if info.st_uid != uid or info.st_gid != broker.pw_gid or info.st_mode & 0o007:
                    raise ValueError('native-readiness-spool-identity-mismatch')
                return fd
            base = directory(state.name, parent, broker.pw_uid)
            requests = directory('requests', base, owner_entry.pw_uid)
            results = directory('results', base, broker.pw_uid)
            temporary, filename = '.' + job + '.tmp', job + '.json'
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=requests)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(body)
                    stream.flush()
                    os.fchown(stream.fileno(), owner_entry.pw_uid, broker.pw_gid)
                    os.fchmod(stream.fileno(), 0o640)
                    os.fsync(stream.fileno())
                os.rename(temporary, filename, src_dir_fd=requests, dst_dir_fd=requests)
            finally:
                try:
                    os.unlink(temporary, dir_fd=requests)
                except FileNotFoundError:
                    pass
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=results)
                except FileNotFoundError:
                    time.sleep(0.1)
                    continue
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_uid != broker.pw_uid or info.st_size > 65536):
                        raise ValueError('native-readiness-result-invalid')
                    receipt = json.loads(stream.read(65537))
                if receipt.get('jobId') != job:
                    raise ValueError('native-readiness-result-mismatch')
                if receipt.get('status') == 'succeeded':
                    steps = receipt.get('steps', [])
                    if len(steps) == 1 and json.loads(steps[0]['stdout']).get('available') is True:
                        return True
                    raise ValueError('native-operations-readiness-failed')
                if receipt.get('status') in ('failed', 'cancelled', 'rejected'):
                    raise ValueError('native-operations-readiness-failed')
                time.sleep(0.1)
        raise ValueError('native-operations-readiness-timeout')

    return {
        'manager': lambda: client('manager', broker,
            ['client', '/private/var/lib/ods-pixel-manager/extension-manager.sock', 'list', 'all']),
        'promoter': lambda: client('promoter', owner_entry,
            ['health', '/private/var/lib/ods-pixel-artifact-promoter/promoter.sock']),
        'operations': operations,
    }


def install_new(*, selection, owner, environment, workspace, port, checkpoint,
                save_stop=None, load_stop=None):
    """Provision and activate the initial service set under gateway admission.

    The protected gateway installer supplies the approved selection and its
    durable journal callback. Existing spools are never adopted or erased.
    A failure leaves the journal for explicit recovery, not automatic replay.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    if set(selection) != {'bundle', 'expected_digest', 'expected_ref', 'expected_config_digest'}:
        raise ValueError('complete-native-service-selection-required')
    snapshots = helper('config').verified_services(selection['bundle'],
        **{key: value for key, value in selection.items() if key != 'bundle'})
    # Validate the selection before provisioning any system identity or state.
    if not snapshots:
        raise ValueError('empty-native-service-selection')
    state = Path('/private/var/lib/pixel-ops-broker')
    runtime = Path('/private/var/lib/ods-pixel-manager')
    if any(os.path.lexists(path) for path in (state, runtime,
            Path('/private/var/lib/ods-pixel-artifact-promoter'))):
        raise ValueError('new-native-service-state-required')
    ops = helper('ops-service')
    installer = ops.installer_helpers()
    for name in ('manager', 'promoter', 'operations'):
        target = 'system/com.ods.pixel-native-' + name
        if installer._job_disabled(target):
            raise ValueError('native-service-intentionally-disabled')
        try:
            installer._command(['/bin/launchctl', 'print', target])
        except installer.InstallError as error:
            if getattr(error, 'returncode', None) != 113:
                raise
        else:
            raise ValueError('native-service-already-loaded')
    python = ops.select_python()
    entry = pwd.getpwnam(owner)
    if entry.pw_uid <= 0:
        raise ValueError('native-gateway-owner-invalid')
    checkpoint({'phase': 'provisioning-identity'})
    identity = helper('ops-account').provision()
    checkpoint({'phase': 'provisioning-state', 'identity': identity})
    state_helper = helper('ops-state')
    ids = dict(gateway_uid=entry.pw_uid, broker_uid=identity['uid'], broker_gid=identity['gid'])
    state_helper.provision(state=state, **ids)
    state_helper.provision_manager_runtime(runtime=runtime, **ids)
    checkpoint({'phase': 'publishing-services', 'identity': identity})
    definitions = publish(**selection, identity=identity, owner=owner,
        environment=environment, workspace=workspace, port=port, python=python)
    expected = {name: ops.custody.protected_bytes(str(path)) for name, path in definitions.items()}
    checkpoint({'phase': 'services-prepared', 'recovery': {
        'schemaVersion': 1, 'owner': owner, 'identity': identity, 'python': str(python),
        'definitions': {name: {'path': str(path), 'body': base64.b64encode(expected[name]).decode('ascii')}
                        for name, path in definitions.items()}}})
    adapters = activation_adapters(definitions=definitions, expected=expected,
        owner=owner, identity=identity, python=python, save_stop=save_stop, load_stop=load_stop)
    ready = readiness_checks(owner=owner, identity=identity, python=python)
    activate_new(services=adapters, readiness=ready, checkpoint=checkpoint)
    return adapters
