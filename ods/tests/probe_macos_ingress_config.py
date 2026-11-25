"""Opt-in synthetic macOS engine probe; never selects installed ODS state.

Requires an already local, exact Node image ID. No pulls, published ports,
networking, model files, credentials, or production container/volume names.
Docker Desktop is the default. Colima requires explicit endpoint/OS bindings
and its result is Colima qualification, not Docker Desktop reproduction.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
READ = """import fs from 'node:fs';
import {readGatewayConfiguration} from '/source/pixel_ingress.mjs';
const p=process.argv[1], s=fs.lstatSync(p);
const result={euid:process.geteuid(),uid:s.uid,gid:s.gid,mode:(s.mode&511).toString(8),regular:s.isFile(),symlink:s.isSymbolicLink()};
try {readGatewayConfiguration(p);result.read_ok=true;}
catch(e){result.read_ok=false;result.reason=e.message==='token file owner mismatch'?'owner-mismatch':'other-reader-rejection';}
console.log(JSON.stringify(result));
"""
INIT = """const fs=require('fs'),uid=Number(process.argv[1]),gid=Number(process.argv[2]),s=fs.lstatSync('/config');
if(!s.isDirectory()||s.isSymbolicLink())throw Error('directory');
if(s.uid===0){fs.chmodSync('/config',448);fs.chownSync('/config',uid,gid);}
const a=fs.lstatSync('/config');if(a.uid!==uid||a.gid!==gid||(a.mode&511)!==448)throw Error('owner');
"""


def probe_workspace(output, project):
    # The reviewed output parent is on the engine's shared home mount. macOS's
    # default /var/folders temporary tree need not be visible to the engine.
    return tempfile.TemporaryDirectory(prefix=project + '-', dir=Path(output).parent)


def record_docker_failure(output, operation, result):
    """Keep the first CLI failure privately, without logging command arguments."""
    diagnostic = Path(output).with_name(Path(output).name + '.private-docker-failure.json')
    try:
        fd = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return  # Preserve the initial failure if cleanup encounters another.
    with os.fdopen(fd, 'w') as stream:
        json.dump({'operation': operation, 'exitCode': result.returncode,
            'stdoutTail': result.stdout[-4096:], 'stderrTail': result.stderr[-8192:]}, stream)
        stream.flush()
        os.fsync(stream.fileno())


def validate_engine(mode, endpoint, operating_system, *, expected_endpoint=None, expected_operating_system=None):
    if mode == 'docker-desktop':
        if 'docker desktop' not in operating_system.lower():
            raise ValueError('This probe requires Docker Desktop')
    elif mode == 'colima':
        if (not expected_endpoint or not expected_operating_system
                or endpoint != expected_endpoint or operating_system != expected_operating_system
                or not endpoint.startswith('unix:///') or '/.colima/' not in endpoint
                or 'docker desktop' in operating_system.lower()):
            raise ValueError('An exact independently reviewed Colima endpoint and engine are required')
    else:
        raise ValueError('Unknown qualification engine')


def probe(image, output, *, engine='docker-desktop', expected_endpoint=None, expected_operating_system=None):
    if platform.system() != 'Darwin' or os.getuid() == 0:
        raise ValueError('Run only as the signed-in Mac owner')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('An already local exact image ID is required')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('A new receipt path is required')
    project = 'ods-config-probe-' + uuid.uuid4().hex
    volume = project + '_pixel-native-config'
    env = dict(os.environ)
    def docker(*args, input=None, timeout=30):
        result = subprocess.run(['docker', *args], input=input, text=True, capture_output=True,
            timeout=timeout, env=env)
        if result.returncode:
            # CLI stderr can contain arbitrary engine details. Keep it out of
            # the public receipt; retain bounded owner-private diagnostics.
            record_docker_failure(output, args[0], result)
            raise RuntimeError('Docker probe operation failed: ' + args[0])
        return result
    context = json.loads(docker('context', 'inspect').stdout)
    endpoint = context[0]['Endpoints']['docker']['Host']
    if env.get('DOCKER_HOST') not in (None, '', endpoint):
        raise ValueError('Explicit Docker endpoint differs from the selected context')
    if not endpoint.startswith('unix:///') or not Path(endpoint[7:]).is_socket():
        raise ValueError('A local Docker Desktop socket is required')
    for key in ('DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    env['DOCKER_HOST'] = endpoint
    operating_system = docker('info', '--format', '{{.OperatingSystem}}').stdout.strip()
    validate_engine(engine, endpoint, operating_system, expected_endpoint=expected_endpoint,
        expected_operating_system=expected_operating_system)
    if docker('image', 'inspect', '--format', '{{.Id}}', image).stdout.strip() != image:
        raise ValueError('Local image identity changed')
    uid, gid = os.getuid(), os.getgid()
    receipt = {'source': 'synthetic-private-config', 'project': project, 'image': image,
        'operatingSystem': operating_system, 'engineMode': engine, 'endpoint': endpoint,
        'reporterReproduced': False, 'productMutation': False}
    created = False
    with probe_workspace(output, project) as directory:
        directory = Path(directory)
        (directory / 'data/pixel-native/preparation').mkdir(parents=True, mode=0o700)
        source = directory / 'gateway.json'
        source.write_text('{"gateway":{"auth":{"token":"synthetic-probe-only-token"}}}')
        source.chmod(0o600)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        common = {'image': image, 'network_mode': 'none', 'read_only': True,
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'pids_limit': 32, 'mem_limit': '128m', 'memswap_limit': '128m', 'cpus': 0.5,
            'volumes': [str(ROOT / 'extensions/services/pixel-agent/host') + ':/source:ro', 'pixel-native-config:/config']}
        definition = {'name': project, 'services': {
            'pixel-native-config-init': {**common, 'user': '0:0', 'cap_add': ['CHOWN'],
                'entrypoint': ['node', '-e', INIT, str(uid), str(gid)]},
            'pixel-native-config-write': {**common, 'user': f'{uid}:{gid}',
                'entrypoint': ['node', '/source/native_ingress_config.mjs']},
            'pixel-native-ingress': {'image': image, 'profiles': ['native-config-maintenance'],
                'volumes': ['pixel-native-config:/run/pixel-config:ro']}},
            'volumes': {'pixel-native-config': {'name': volume}}}
        compose = directory / 'compose.json'
        compose.write_text(json.dumps(definition))
        def run(*args, timeout=30, input=None):
            if args[0] == 'run':
                args = ('run', '--pull', 'never', *args[1:])
            return docker('compose', '--project-name', project, '-f', str(compose),
                *args, timeout=timeout, input=input)
        # Retain the random namespace before the first container/volume action,
        # so a killed transport can still be reconciled without guessing owners.
        receipt['status'] = 'prepared'
        with output.open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(receipt, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        try:
            docker('volume', 'create', '--label', 'ods.synthetic.config-probe=' + project,
                '--label', 'com.docker.compose.project=' + project,
                '--label', 'com.docker.compose.volume=pixel-native-config', volume)
            created = True
            def read(mount, target):
                value = docker('run', '--rm', '--pull', 'never', '--name', project + '-reader',
                    '--label', 'ods.synthetic.config-probe=' + project,
                    '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges:true', '--pids-limit', '32',
                    '--memory', '128m', '--memory-swap', '128m', '--cpus', '0.5',
                    '--user', f'{uid}:{gid}', '--mount', mount, '--mount',
                    'type=bind,source=' + str(ROOT / 'extensions/services/pixel-agent/host') + ',target=/source,readonly',
                    image, 'node', '--input-type=module', '-e', READ, target)
                return json.loads(value.stdout)
            receipt['host'] = {'uid': source.stat().st_uid, 'gid': source.stat().st_gid, 'mode': '600'}
            receipt['bind'] = read('type=bind,source=' + str(source) + ',target=/run/gateway.json,readonly', '/run/gateway.json')
            spec = importlib.util.spec_from_file_location('probe_ingress_config',
                ROOT / 'installers/macos/lib/pixel-native-ingress-config.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            receipt['staging'] = module.stage(run, source, user=f'{uid}:{gid}',
                docker_run=docker, install_dir=directory)
            receipt['volume'] = read('type=volume,source=' + volume + ',target=/config,readonly', '/config/' + digest + '.json')
            receipt['reportedBoundaryObservedHere'] = (receipt['bind'].get('reason') == 'owner-mismatch'
                and receipt['bind']['uid'] != uid)
            assert receipt['volume']['read_ok'] and receipt['volume']['uid'] == uid
            assert receipt['volume']['mode'] == '600'
            receipt['status'] = 'synthetic-volume-reader-passed'
        finally:
            # Compose owns only this random project; timeout remnants remain
            # visible until this bounded cleanup completes successfully.
            try:
                run('down', '--timeout', '5', timeout=20)
                for label in ('ods.synthetic.config-probe=' + project, 'com.docker.compose.project=' + project):
                    ids = docker('ps', '-aq', '--filter', 'label=' + label).stdout.split()
                    for identity in ids:
                        if not re.fullmatch('[a-f0-9]{12,64}', identity): raise ValueError('Invalid probe container ID')
                        docker('rm', '-f', identity)
                if created:
                    labels = json.loads(docker('volume', 'inspect', '--format', '{{json .Labels}}', volume).stdout)
                    if labels.get('ods.synthetic.config-probe') != project: raise ValueError('Probe volume ownership changed')
                    docker('volume', 'rm', volume)
                receipt['cleanup'] = 'owned-probe-containers-and-volume-removed'
            except BaseException as error:
                receipt['cleanup'] = 'attention-required'
                receipt['cleanupErrorType'] = type(error).__name__
                raise
            finally:
                output.write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--engine', choices=('docker-desktop', 'colima'), default='docker-desktop')
    parser.add_argument('--expected-endpoint')
    parser.add_argument('--expected-operating-system')
    args = parser.parse_args()
    probe(args.image, args.output, engine=args.engine, expected_endpoint=args.expected_endpoint,
        expected_operating_system=args.expected_operating_system)
