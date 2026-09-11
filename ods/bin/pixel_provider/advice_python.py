"""Server-selected interpreters; an HTTP client never supplies executable paths."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .advice_process import worker_environment
from .advice_runtime import digest_file
from .store import StoreError

PROBE = ('import importlib.util,json,sys; print(json.dumps(dict(version=list(sys.version_info[:3]),'
         'venv=importlib.util.find_spec("venv") is not None,'
         'ensurepip=importlib.util.find_spec("ensurepip") is not None)))')


def candidates():
    if os.name != 'posix':
        return []
    paths=[Path(sys.executable)]
    for prefix in ('/usr/bin','/usr/local/bin','/opt/homebrew/bin'):
        paths.extend(Path(prefix)/('python'+version) for version in ('3.14','3.13','3.12','3.11'))
    result=[]; seen=set()
    for path in paths:
        try:
            binary=path.resolve(strict=True)
            if binary in seen:
                continue
            seen.add(binary)
            digest=digest_file(binary)
            probe=subprocess.run([str(binary),'-I','-S','-B','-c',PROBE],stdin=subprocess.DEVNULL,
                capture_output=True,timeout=3,env=worker_environment(),cwd='/')
            if probe.returncode or len(probe.stdout)>4096:
                continue
            value=json.loads(probe.stdout)
            version=value['version']
            if (not isinstance(version,list) or len(version)!=3 or any(type(v) is not int for v in version)
                    or version[:2]<[3,11] or type(value['venv']) is not bool or type(value['ensurepip']) is not bool
                    or digest_file(binary)!=digest):
                continue
            identity=hashlib.sha256((str(binary)+'\0'+digest).encode()).hexdigest()
            result.append(dict(id=identity,path=str(binary),version='.'.join(map(str,version)),
                               canPrepare=value['venv'] and value['ensurepip']))
        except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
            continue
    return result


def select_candidate(identity):
    for candidate in candidates():
        if candidate['id']==identity and candidate['canPrepare']:
            return candidate
    raise StoreError('advice-python-unavailable')
