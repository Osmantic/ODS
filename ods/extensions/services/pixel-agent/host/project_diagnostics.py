"""Fixed offline programs and strict receipts; no project or caller code."""
import hashlib
import json
import re
import subprocess
from project_storage import StorageAdmissionError

SCRATCH_BYTES = 64 * 1024 * 1024
CHECKS = ('node', 'npm', 'python', 'pip', 'venv', 'scratch')
CODES = {'ready', 'missing', 'unavailable', 'incompatible', 'unsupported'}
FAILURE_PHASES = {'configuration', 'authorization', 'storage-reservation', 'storage-create', 'execution', 'output-validation'}
FAILURE_CODES = {'storage-recovery-required', 'storage-capacity-reserved', 'engine-headroom-insufficient',
                 'operation-timeout', 'command-failed', 'host-io-error', 'invalid-evidence', 'authorization-revoked', 'probe-failed'}


def diagnostic_failure(error, phase):
    if phase not in FAILURE_PHASES:
        raise ValueError('invalid diagnostic failure phase')
    code = ('authorization-revoked' if isinstance(error, PermissionError) else
            'operation-timeout' if isinstance(error, subprocess.TimeoutExpired) else
            'command-failed' if isinstance(error, subprocess.SubprocessError) else
            'host-io-error' if isinstance(error, OSError) else 'invalid-evidence')
    if isinstance(error, StorageAdmissionError) and error.code in FAILURE_CODES:
        code = error.code
    return {'phase': phase, 'code': code}

PYTHON_PROBE = r'''import json, pathlib, shutil, subprocess, tempfile, venv
checks = {}
for key, argv in [('node',['node','--version']),('npm',['npm','--version']),('python',['python','--version']),('pip',['python','-I','-m','pip','--version'])]:
    if shutil.which(argv[0]) is None:
        checks[key] = {'code':'missing'}
        continue
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        version = (result.stdout or result.stderr).strip().splitlines()[0][:96] if (result.stdout or result.stderr).strip() else ''
        checks[key] = {'code':'ready','version':version} if result.returncode == 0 else {'code':'unavailable'}
    except (OSError, subprocess.SubprocessError):
        checks[key] = {'code':'unavailable'}
root = None
try:
    root = pathlib.Path(tempfile.mkdtemp(prefix='diagnostic-',dir='/home/node'))
    path = root/'scratch-check'
    path.write_bytes(b'ODS diagnostic')
    assert path.read_bytes() == b'ODS diagnostic'
    path.unlink()
    checks['scratch'] = {'code':'ready'}
except (OSError, AssertionError):
    checks['scratch'] = {'code':'unavailable'}
try:
    if root is None:
        raise OSError('scratch unavailable')
    venv.EnvBuilder(with_pip=True).create(root/'venv')
    result = subprocess.run([str(root/'venv/bin/python'),'-I','-m','pip','--version'],capture_output=True,timeout=10)
    checks['venv'] = {'code':'ready' if result.returncode == 0 else 'incompatible'}
except (OSError, subprocess.SubprocessError):
    checks['venv'] = {'code':'unavailable'}
print(json.dumps({'schemaVersion':1,'scope':'managed-executor','runtime':'python','checks':checks},separators=(',',':')))
'''

NODE_PROBE = r'''const fs=require('fs'),cp=require('child_process');
const checks={};
for(const [key,argv] of [['node',['node','--version']],['npm',['npm','--version']],['python',['python3','--version']],['pip',['python3','-I','-m','pip','--version']]]){
 const r=cp.spawnSync(argv[0],argv.slice(1),{encoding:'utf8',timeout:5000,maxBuffer:4096});
 checks[key]=r.error?{code:r.error.code==='ENOENT'?'missing':'unavailable'}:r.status===0?{code:'ready',version:(r.stdout||r.stderr).trim().split('\n')[0].slice(0,96)}:{code:'unavailable'};
}
checks.venv={code:'unsupported'};
try{const root=fs.mkdtempSync('/home/node/diagnostic-'),path=root+'/scratch-check';fs.writeFileSync(path,'ODS diagnostic');if(fs.readFileSync(path,'utf8')!=='ODS diagnostic')throw Error();fs.unlinkSync(path);checks.scratch={code:'ready'};}catch{checks.scratch={code:'unavailable'};}
console.log(JSON.stringify({schemaVersion:1,scope:'managed-executor',runtime:'npm',checks}));
'''


def diagnostic_command(runtime):
    if runtime == 'python':
        return ['python', '-I', '-c', PYTHON_PROBE]
    if runtime == 'npm':
        return ['node', '-e', NODE_PROBE]
    raise ValueError('unsupported diagnostic runtime')


def diagnostic_digest(runtime):
    return hashlib.sha256(json.dumps(diagnostic_command(runtime)).encode()).hexdigest()


def validate_diagnostic(payload, runtime):
    if not isinstance(payload, str) or len(payload.encode()) > 4096:
        raise ValueError('invalid diagnostic output')
    value = json.loads(payload)
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'scope', 'runtime', 'checks'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['scope'] != 'managed-executor' or value['runtime'] != runtime
            or not isinstance(value['checks'], dict) or set(value['checks']) != set(CHECKS)):
        raise ValueError('invalid diagnostic receipt')
    for check in value['checks'].values():
        if (not isinstance(check, dict) or set(check) not in ({'code'}, {'code', 'version'})
                or not isinstance(check['code'], str) or check['code'] not in CODES or ('version' in check and
                    (check['code'] != 'ready' or not isinstance(check['version'], str)
                     or not re.fullmatch(r'[\x20-\x7e]{1,96}', check['version'])))):
            raise ValueError('invalid diagnostic check')
    return value


def diagnostic_output(runtime, *, report=None, code='unavailable', cleanup='unconfirmed', scratch_bytes=SCRATCH_BYTES, failure=None):
    next_action = ({'code': 'recover-owned-job', 'tool': 'pixel_ods_project_build', 'action': 'cancel'}
                   if cleanup == 'unconfirmed' else
                   {'code': 'prepare-locked-project', 'tool': 'pixel_ods_project_build', 'action': 'submit',
                    'requiredFiles': ['ods-project.json', 'requirements.lock', 'main.py', 'tests/test_*.py']
                    if runtime == 'python' else ['package.json', 'package-lock.json'],
                    'dependencyPolicy': 'exact-versions-and-wheel-sha256' if runtime == 'python' else 'matching-npm-lock',
                    'stages': ['acquire', 'test', 'build']}
                   if code == 'ready' else {'code': 'inspect-runtime-configuration', 'automaticInstall': False})
    result = {'schemaVersion': 1, 'kind': 'ods-project-diagnostic', 'scope': 'managed-executor',
            'runtime': runtime, 'code': code, 'checks': report['checks'] if report else {},
            'cleanup': cleanup, 'network': 'none', 'scratchLimitBytes': scratch_bytes,
            'nextAction': next_action,
            'chatSandboxVerified': False}
    if failure is not None:
        if (type(failure) is not dict or set(failure) != {'phase', 'code'}
                or failure.get('phase') not in FAILURE_PHASES or failure.get('code') not in FAILURE_CODES):
            raise ValueError('invalid diagnostic failure')
        result['failure'] = failure
    return result
