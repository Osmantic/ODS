"""Finish a retained initial Pixel installation after final Docker health failure.

This verifies the completed protected activation twice; it never repeats it.
The original preparation and activation receipts remain untouched. A successful
readback publishes the existing atomic owner-selection format, after consumers
have been checked against the native stack.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
GUIDANCE = {
    'duplicate-recovery-python-setting': 'The saved installer Python is duplicated. Review ODS_PYTHON_CMD in the private .env; do not share that file.',
    'saved-recovery-python-invalid': 'The saved installer Python must be an executable, owner/root-owned Python without group or world write access. Preserve the installation and repair that interpreter.',
    'saved-recovery-python-unavailable': 'The saved installer Python no longer exists. Restore that interpreter before continuing; do not reinstall ODS or remove its receipts.',
    'recovery-python-dependency-unavailable': 'The saved installer Python cannot import PyYAML. Repair that Python environment before continuing; no services were changed.',
    'private-owner-environment-required': 'The retained .env must be a private, regular file owned by the signed-in user. Preserve it and review its ownership and permissions.',
    'duplicate-compose-selection': 'The saved Compose selection contains duplicate settings. Review the private .env without sharing it.',
    'saved-compose-selection-required': 'The retained Compose settings are incomplete or invalid. Preserve the installation and review its saved selection.',
    'native-compose-configuration-invalid': 'Docker Compose could not validate the retained stack. Keep Docker running and review the saved Compose selection before retrying.',
    'persisted-native-compose-environment-mismatch': 'The retained Compose bindings no longer match the prepared native runtime. Preserve both for review; recovery did not replace them.',
    'native-macos-owner-required': 'Run as the signed-in macOS owner; the proof requests sudo itself.',
    'retained-final-health-failure-required': 'Only an initial installation stopped after protected activation can use this recovery.',
    'retained-native-selection-mismatch': 'The preparation and activation identities disagree.',
    'native-recovery-selection-changed': 'The retained selection changed or a different update was published.',
    'native-initial-recovery-proof-failed': 'Protected activation could not be verified. The failed attempt remains intact.',
    'native-recovery-client-routing-failed': 'The native Dashboard/Portal route is not ready.',
    'native-client-has-legacy-edge-route': 'The selected client configuration still routes to a legacy Pixel Edge.',
    'native-recovery-host-agent-failed': 'Host-agent setup did not pass. Inspect the private continuation-host-agent.log; do not reinstall.',
    'native-model-upgrade-already-running': 'An existing full-model worker is running. Inspect its progress; do not start another download.',
    'retained-upgrade-arguments-conflict': 'Existing retry metadata names a different model. Preserve it and review the selection.',
    'native-model-upgrade-launch-failed': 'The downloader could not start. Retry metadata was preserved; review the local setup before retrying.',
    'native-model-worker-tracking-failed': 'A downloader was started but its PID could not be recorded. Inspect existing workers and the model-upgrade log before retrying.',
    'native-model-upgrade-process-check-failed': 'Existing downloader processes could not be checked; no new worker was started.',
    'native-recovery-compose-selection-changed': 'The Docker service selection changed during recovery. Review it before retrying.',
    'retained-model-environment-changed': 'Model configuration changed during recovery. No new worker was started.',
    'retained-bootstrap-model-required': 'The active model no longer matches the retained starter. Recovery will not replace an operator choice.',
    'saved-model-recommendation-required': 'The saved full-model recommendation is incomplete or malformed.',
    'saved-model-recommendation-mismatch': 'The saved recommendation does not match exactly one installed catalog model.',
    'saved-model-context-invalid': 'The saved context exceeds the installed catalog contract or is invalid.',
    'pinned-bootstrap-model-required': 'The full model lacks a matching pinned URL and checksum; automatic download was refused.',
    'single-file-bootstrap-model-required': 'The existing bootstrap downloader cannot safely handle this model artifact set.',
    'continuation-metadata-changed': 'Continuation metadata changed concurrently. Preserve the files and inspect any existing worker before retrying.',
    'owned-continuation-metadata-required': 'Continuation metadata has unsafe ownership, links, permissions or size; it was not replaced.',
    'retained-local-model-route-required': 'The saved inference route or model store is outside this initial-install continuation.',
    'invalid-retained-opencode-selection': 'The saved OpenCode choice must be true or false; it was not changed.',
    'duplicate-retained-opencode-setting': 'The saved OpenCode choice is duplicated; review the private environment without sharing it.',
    'confirmed-opencode-selection-conflict': 'The confirmed OpenCode choice conflicts with the retained choice; neither was changed.',
    'retained-opencode-choice-required': 'This older installation has no saved OpenCode choice. Confirm the original selection with --opencode-choice enabled or disabled; do not infer it from a missing binary.',
    'native-recovery-optional-tools-failed': 'Selected optional setup did not pass. Inspect the private continuation-optional-tools.log; preserve partial work and do not reinstall.',
    'retained-optional-route-required': 'The retained inference mode is not a supported local or cloud choice.',
    'retained-optional-context-invalid': 'The retained context is invalid for optional-tool configuration.',
    'retained-optional-port-invalid': 'A retained native or voice port is invalid; no optional setup was started.',
    'duplicate-retained-optional-setting': 'Optional-tool route settings are duplicated; review the private environment without sharing it.',
}


def helper(name):
    spec = importlib.util.spec_from_file_location('native_recover_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def failure_detail(error):
    """Keep failures actionable without printing commands, paths or secrets."""
    code = str(error) if isinstance(error, ValueError) else None
    if code in GUIDANCE:
        return '[' + code + '] ' + GUIDANCE[code]
    health = helper('pixel-native-compose').health_diagnostic(error)
    if health:
        return health
    location = 'recovery'
    traceback = error.__traceback__
    while traceback is not None:
        path = Path(traceback.tb_frame.f_code.co_filename)
        if path.parent == HERE and path.name.startswith('pixel-') and path.suffix == '.py':
            location = path.name + ':' + str(traceback.tb_lineno)
        traceback = traceback.tb_next
    if isinstance(error, subprocess.CalledProcessError):
        status = str(error.returncode) if type(error.returncode) is int else 'unknown'
        reason = 'a required command exited with status ' + status
    elif isinstance(error, subprocess.TimeoutExpired):
        reason = 'a required command timed out'
    elif isinstance(error, PermissionError):
        reason = 'a required file or command is not accessible to the signed-in owner'
    elif isinstance(error, FileNotFoundError):
        reason = 'a required file or command is missing'
    elif isinstance(error, OSError):
        reason = 'an operating-system operation failed'
        if type(error.errno) is int:
            reason += ' (errno ' + str(error.errno) + ')'
    elif isinstance(error, KeyError):
        reason = 'a required retained configuration field is missing'
    else:
        reason = 'a retained configuration or custody check failed'
    return ('[native-recovery-check-failed] ' + location + ': ' + reason
            + '. Share this diagnostic with the maintainers; do not share .env or private receipts.')


def prepare_python(install_dir):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    return helper('pixel-native-recovery-python').relaunch(install_dir, HERE / 'pixel-native-recover.py', sys.argv[1:])


def selection(receipt, activation):
    if (type(receipt) is not dict or type(activation) is not dict
            or receipt.get('kind') == 'legacy-native'
            or receipt.get('status') != 'prepared' or receipt.get('requiresActivation') is not True
            or receipt.get('phase') != 'awaiting-protected-activation'
            or activation.get('status') != 'error' or activation.get('requiresRecovery') is not True
            or activation.get('phase') not in ('final-health', 'webui-routing')
            or type(activation.get('schemaVersion')) is not int or activation['schemaVersion'] != 1
            or not re.fullmatch('[a-f0-9]{40}', str(receipt.get('pixelSourceRef', '')))):
        raise ValueError('retained-final-health-failure-required')
    for key in ('runtimeDigest', 'serviceDigest'):
        if (not re.fullmatch('[a-f0-9]{64}', str(receipt.get(key, '')))
                or activation.get(key) != receipt[key]):
            raise ValueError('retained-native-selection-mismatch')
    return {'schemaVersion': 1, 'preparation': receipt,
            'activation': dict(activation, status='ready', phase='services-ready', requiresRecovery=False)}


def finish(*, preparation, receipt, run, verify, compose, selected_services, restore_host_agent=None,
           resume_model=None, restore_optional_tools=None):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    config = helper('pixel-native-config')
    preparation = Path(preparation)
    info = preparation.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
            or preparation.resolve(strict=True) != preparation):
        raise ValueError('private-native-preparation-required')
    lock = os.open(preparation / '.selection.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('private-native-selection-lock-required')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = config.private_json(preparation / 'activation.json')
        document = selection(receipt, original)
        body = (json.dumps(document, sort_keys=True) + '\n').encode()
        if len(body) > 2 * 1024 * 1024:
            raise ValueError('native-recovery-selection-too-large')
        destination = preparation / 'selection-update.json'
        def unchanged():
            if (config.private_json(preparation / 'preparation.json') != receipt
                    or config.private_json(preparation / 'activation.json') != original
                    or os.path.lexists(destination) and config.private_json(destination) != document):
                raise ValueError('native-recovery-selection-changed')
        def prove():
            unchanged()
            expected = {'status': 'active', **{key: receipt[key] for key in ('runtimeDigest', 'serviceDigest')}}
            if verify() != expected:
                raise ValueError('native-initial-recovery-proof-failed')
            unchanged()
        prove()
        compose.wait_ready(run)
        clients = ['dashboard-api']
        if 'open-webui' in selected_services:
            clients.append('open-webui')
        # A stale override can leave healthy clients talking to a legacy Edge.
        for name in clients:
            definition = selected_services.get(name)
            if type(definition) is not dict:
                raise ValueError('native-client-service-missing')
            hosts = definition.get('extra_hosts', {})
            if type(hosts) not in (dict, list) or any(
                    str(host).split('=', 1)[0].split(':', 1)[0].lower().rstrip('.') == 'pixel-edge'
                    for host in hosts):
                raise ValueError('native-client-has-legacy-edge-route')
        if run('up', '-d', '--no-deps', '--wait', '--wait-timeout', '120', *clients, timeout=180).returncode:
            raise ValueError('native-recovery-client-routing-failed')
        probe = ('import urllib.request; '
            'r=urllib.request.build_opener(urllib.request.ProxyHandler({})).open('
            '"http://pixel-edge:9595/health",timeout=15); '
            'raise SystemExit(0 if r.status==200 else 1)')
        if run('exec', '-T', 'dashboard-api', 'python3', '-c', probe, timeout=30).returncode:
            raise ValueError('native-recovery-client-routing-failed')
        compose.wait_ready(run)
        optional_result = None
        if restore_optional_tools is not None:
            unchanged()
            optional_result = restore_optional_tools(unchanged)
        # Host setup may normalize its own Colima bridge setting in .env.
        # Finish optional setup's exact retained-environment checks first.
        if restore_host_agent is not None:
            unchanged()
            restore_host_agent()
        prove()
        # Publish only after routing and protected readback succeed. An earlier
        # interruption retains the error receipt and is safe to retry explicitly.
        with tempfile.TemporaryDirectory(prefix='.recovery-', dir=preparation) as temporary:
            staged = Path(temporary) / 'selection.json'
            with staged.open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            unchanged()
            os.replace(staged, destination)
            directory_fd = os.open(preparation, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        if resume_model is not None or restore_optional_tools is not None:
            result = {'selection': str(destination)}
            if optional_result is not None:
                result['optionalTools'] = optional_result
            if resume_model is not None:
                result['modelUpgrade'] = resume_model(unchanged)
            return result
        return destination
    finally:
        os.close(lock)


def recover(install_dir, ods_source, *, restore_host_agent=False, resume_model=False,
            inspect_continuation=False, opencode_choice=None, restore_optional_tools=False):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    if inspect_continuation and (restore_host_agent or resume_model or restore_optional_tools):
        raise ValueError('continuation-inspection-cannot-mutate')
    if opencode_choice is not None and not (inspect_continuation or restore_optional_tools):
        raise ValueError('opencode-choice-requires-optional-operation')
    install_dir = Path(install_dir).expanduser().resolve(strict=True)
    preparation = install_dir / 'data/pixel-native/preparation'
    config = helper('pixel-native-config')
    selection(config.private_json(preparation / 'preparation.json'),
              config.private_json(preparation / 'activation.json'))
    tokens = helper('pixel-native-finalize').compose_flags(install_dir, dict(os.environ), recovery=True)
    fragments = [install_dir / path for path in helper('pixel-native-install').FRAGMENTS]
    files = []
    for value in tokens[1::2]:
        path = (install_dir / value).resolve(strict=True)
        if install_dir not in path.parents:
            raise ValueError('installed-compose-file-required')
        if path not in fragments:
            files.append(path)
    return helper('pixel-native-activate').activate(preparation=preparation, install_dir=install_dir,
        ods_source=Path(ods_source).resolve(strict=True), compose_files=[*files, *fragments],
        resume_final_health=True, **({'restore_host_agent': True} if restore_host_agent else {}),
        **({'resume_model': True} if resume_model else {}),
        **({'inspect_continuation': True} if inspect_continuation else {}),
        **({'restore_optional_tools': True} if restore_optional_tools else {}),
        **({'opencode_choice': opencode_choice} if inspect_continuation or restore_optional_tools else {}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--ods-source', default=str(HERE.parents[2]))
    parser.add_argument('--restore-host-agent', action='store_true',
        help='Also install the login host agent and verify its authenticated Dashboard route; not a full installer continuation')
    parser.add_argument('--resume-model', action='store_true',
        help='After verified recovery, save Compose selection and resume the saved full-model download; starting is not completion')
    parser.add_argument('--inspect-continuation', action='store_true',
        help='Inspect retained choices and required checks without sudo, service changes or readiness publication')
    parser.add_argument('--restore-optional-tools', action='store_true',
        help='Run selected OpenCode, Whisper model and Perplexica setup after verified Pixel recovery; not final install readiness')
    parser.add_argument('--opencode-choice', choices=('enabled', 'disabled'),
        help='Confirm a missing historical OpenCode choice for inspection or optional setup; never overrides a saved choice')
    args = parser.parse_args()
    if args.inspect_continuation and (args.restore_host_agent or args.resume_model or args.restore_optional_tools):
        parser.error('--inspect-continuation cannot be combined with setup options')
    if args.opencode_choice is not None and not (args.inspect_continuation or args.restore_optional_tools):
        parser.error('--opencode-choice requires --inspect-continuation or --restore-optional-tools')
    try:
        relaunched = prepare_python(args.install_dir)
        if relaunched is not None:
            return relaunched
        path = recover(args.install_dir, args.ods_source,
            **({'restore_host_agent': True} if args.restore_host_agent else {}),
            **({'resume_model': True} if args.resume_model else {}),
            **({'inspect_continuation': True} if args.inspect_continuation else {}),
            **({'restore_optional_tools': True} if args.restore_optional_tools else {}),
            **({'opencode_choice': args.opencode_choice}
               if args.inspect_continuation or args.restore_optional_tools else {}))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        detail = failure_detail(error)
        print('Native Pixel recovery stopped. Keep the original receipts and services intact.'
              + (' ' + detail if detail else ''), file=sys.stderr)
        return 1
    if args.inspect_continuation:
        print(json.dumps(path))
        return 0
    result = {'status': 'native-pixel-ready', 'selection': str(path), 'installerComplete': False}
    if args.resume_model or args.restore_optional_tools:
        result.update(path)
    if args.restore_host_agent:
        result['hostAgentReady'] = True
    print(json.dumps(result))
    remaining = ([] if args.restore_host_agent else ['host agent'])
    if not args.restore_optional_tools:
        remaining.append('optional tools')
    remaining.append('full-model download/activation and final service/Portal checks')
    print('Pixel recovery completed. Remaining installer steps (' + ', '.join(remaining)
          + ') still need verification before declaring ODS installed.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
