"""Offline, interactive owner reconciliation; never exposed as a model tool.

Current quiescence is checked. Historical engine identity is NOT recoverable:
the owner must attest it explicitly. Past execution/import stays unknown.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time

from project_jobs import ProjectJobs
from project_storage import ProjectStorage
from project_owner_resolution import installed_identity, installation_identity

STATE = '/var/lib/ods-pixel-project'


@contextmanager
def exclusive_service(root):
    fd = os.open(Path(root) / 'service.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError('unsafe service lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def command(args):
    import pwd
    owner = pwd.getpwuid(os.getuid())
    environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': owner.pw_dir,
                   'USER': owner.pw_name, 'LOGNAME': owner.pw_name}
    result = subprocess.run(['docker', *args], capture_output=True, timeout=10, check=True, env=environment)
    if len(result.stdout) > 65536:
        raise ValueError('engine evidence exceeds bound')
    return result.stdout.decode('ascii').strip()


def require_stopped_service():
    result = subprocess.run(['systemctl', 'show', 'ods-pixel-project.service',
                             '--property=ActiveState', '--property=ControlGroup'],
                            capture_output=True, timeout=5, check=True)
    if len(result.stdout) > 4096:
        raise ValueError('invalid service evidence')
    values = dict(line.split('=', 1) for line in result.stdout.decode('ascii').splitlines())
    if set(values) != {'ActiveState', 'ControlGroup'} or values['ActiveState'] != 'inactive' or values['ControlGroup']:
        raise ValueError('stop the installed project service and its entire control group before recovery')


def quiescence(job, image, after):
    engine = command(['info', '--format', '{{.ID}}'])
    if not re.fullmatch(r'[A-Za-z0-9:_-]{8,128}', engine):
        raise ValueError('invalid current engine identity')
    # No user program, shell, network, mount, or mutable image tag.
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('pinned installed image required')
    boot = command(['run', '--rm', '--pull=never', '--network', 'none', '--read-only', '--user', '1000:1000',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '16',
                    '--memory', '32m', '--memory-swap', '32m', '--log-driver', 'none',
                    '--entrypoint', '/bin/cat', image, '/proc/stat'])
    values = re.findall(r'^btime ([0-9]+)$', boot, re.MULTILINE)
    if len(values) != 1 or not after < int(values[0]) <= time.time():
        raise ValueError('engine VM must have booted after the last original job receipt update')
    for selector in ('name=^/' + job + '-', 'volume=' + job, 'label=org.osmantic.ods.project-job=' + job):
        if command(['container', 'ls', '--all', '--no-trunc', '--filter', selector, '--format', '{{.ID}}']):
            raise ValueError('job container or volume user remains; no resolution recorded')
    for selector in ('name=^' + job + '$', 'label=org.osmantic.ods.project-job=' + job):
        if command(['volume', 'ls', '--filter', selector, '--format', '{{.Name}}']):
            raise ValueError('job storage remains; no resolution recorded')
    if command(['info', '--format', '{{.ID}}']) != engine:
        raise ValueError('engine identity changed during verification')
    return {'engineId': engine, 'engineBootTime': int(values[0]), 'currentResourcesAbsent': True}


def require_no_import(workspace, project, job):
    """Traverse without symlinks; any old output generation needs separate review."""
    parts = project.split('/')
    from project_snapshot import _component
    if not parts or not all(_component(part) for part in parts):
        raise ValueError('invalid project path')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(workspace, flags)
    try:
        for part in parts:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        try:
            builds = os.open('ods-builds', flags, dir_fd=fd)
        except FileNotFoundError:
            return
        try:
            try:
                os.stat(job.removeprefix('ods-project-'), dir_fd=builds, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise ValueError('job output generation exists; inspect its incomplete or published artifacts first')
        finally:
            os.close(builds)
    finally:
        os.close(fd)


def reconcile(root, workspace, image, job, expected_hash, confirm, config_identity):
    jobs = ProjectJobs(root)
    storage = ProjectStorage(root)
    with exclusive_service(root), storage.locked():
        require_stopped_service()
        row = jobs.original_receipt(job)
        if row['state'] != 'unconfirmed' or json.loads(row['steps']) != [] or jobs.receipt_hash(row) != expected_hash:
            raise ValueError('exact current unconfirmed receipt hash required')
        request = json.loads(row['request'])
        if request.get('kind', 'build') != 'build':
            raise ValueError('only project build receipts supported')
        if job in storage._read():
            raise ValueError('storage reservation remains; recovery does not remove it')
        require_no_import(workspace, request['project'], job)
        evidence = quiescence(job, image, row['updated'])
        review = {'job': job, 'receiptSha256': expected_hash, 'project': request['project'],
                  'historicalOutcome': 'unknown', 'historicalEngineBinding': 'missing', **evidence}
        if confirm(review) is not True:
            raise PermissionError('owner did not acknowledge historical uncertainty')
        # Recheck after the human review; locks remain held throughout.
        require_stopped_service()
        if quiescence(job, image, row['updated']) != evidence:
            raise ValueError('engine evidence changed during owner review')
        require_no_import(workspace, request['project'], job)
        record = {**review, 'kind': 'owner-attested-retry-resolution', 'sameEngineAttested': True,
                  'ownerUid': os.geteuid(), 'acknowledgedAt': time.time(),
                  'stateRootSha256': hashlib.sha256(str(Path(root).absolute()).encode()).hexdigest(),
                  'installationSha256': installation_identity({'ownerUid': os.geteuid(), 'workspace': str(workspace)},
                                                              str(Path(root).absolute())),
                  'installedConfigSha256': config_identity}
        record.pop('project')  # Publicly readable root receipt contains no private project paths.
        jobs.record_owner_resolution(job, expected_hash, record)
        return record


def interactive_acknowledgement(review):
    print(json.dumps(review, indent=2))
    print('The original outcome stays UNKNOWN. This does not prove cancellation or absence of past writes.')
    print('The old receipt has no engine identity. Confirm that this is the SAME engine used by this job,')
    print('that its VM restarted after the job, and that you accept unknown historical execution/import.')
    nonce = secrets.token_hex(8)
    phrase = f"RESOLVE {review['job']} {review['receiptSha256']} SAME-ENGINE UNKNOWN-OUTCOME {nonce}"
    print('Type exactly: ' + phrase)
    return input() == phrase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    parser.add_argument('--receipt-sha256')
    args = parser.parse_args()
    if not re.fullmatch(r'ods-project-[a-f0-9]{24}', args.job):
        parser.error('invalid job identity')
    if os.getuid() != 0 or not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('protected interactive administrator invocation required')
    identity, config = installed_identity()
    import pwd
    owner = config['ownerUid']
    if type(owner) is not int or owner <= 0:
        parser.error('configured non-root owner required')
    account = pwd.getpwuid(owner)
    if config['workspace'] != str(Path(account.pw_dir) / '.openclaw/workspace-pixel'):
        parser.error('configured workspace mismatch')
    # Keep saved UID 0 only for issuing the root-owned attestation. Docker,
    # workspace and SQLite inspection run as the installed owner, never root.
    os.initgroups(account.pw_name, account.pw_gid)
    os.setegid(account.pw_gid)
    os.setresuid(owner, owner, 0)
    try:
        jobs = ProjectJobs(STATE)
        if args.receipt_sha256 is None:
            row = jobs.original_receipt(args.job)
            print(json.dumps({'job': args.job, 'receiptSha256': jobs.receipt_hash(row),
                              'state': row['state'], 'historicalOutcome': 'unknown'}, indent=2))
            return
        reconcile(STATE, config['workspace'], config['imageId'], args.job, args.receipt_sha256,
                  interactive_acknowledgement, identity)
    finally:
        os.seteuid(0)
    print('Owner resolution recorded. Original outcome remains unknown. No work was replayed or removed.')


if __name__ == '__main__':
    main()
