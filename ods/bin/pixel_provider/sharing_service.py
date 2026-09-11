"""Explicit, isolated lifecycle for the optional inference-sharing container."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import uuid
import re

from .store import StoreError

SERVICE = 'pixel-inference'
CONTAINER = 'ods-pixel-inference'
ACTIVATION_LABEL = 'ods.pixel-inference.activation'


def validate_compose(document, *, directory, port, uid, gid):
    """Validate the resolved service, not merely the checked-in template."""
    try:
        service = document['services'][SERVICE]
        ports = service['ports']
        volumes = service['volumes']
        environment = service['environment']
        if (service.get('container_name') != CONTAINER or service.get('privileged', False)
                or service.get('network_mode') in ('host', 'service:model-router')
                or service.get('cap_add') or service.get('read_only') is not True
                or service.get('user') != f'{uid}:{gid}' or uid == 0
                or 'ALL' not in service.get('cap_drop', [])
                or not any(value in ('no-new-privileges:true','no-new-privileges') for value in service.get('security_opt', []))
                or len(ports) != 1 or ports[0].get('host_ip') != '127.0.0.1'
                or str(ports[0].get('published')) != str(port) or ports[0].get('target') != 8093
                or ports[0].get('protocol', 'tcp') != 'tcp'
                or len(volumes) != 1 or volumes[0].get('type') != 'bind'
                or Path(volumes[0]['source']).absolute() != Path(directory).absolute()
                or volumes[0].get('target') != '/state' or volumes[0].get('read_only') is not True
                or volumes[0].get('bind', {}).get('create_host_path') is not False
                or environment.get('ODS_INFERENCE_STATE_DIR') != '/state'
                or environment.get('ODS_INFERENCE_ROUTER_URL') != 'http://model-router:9099'):
            raise ValueError('unsafe')
    except (KeyError, TypeError, ValueError, AttributeError):
        raise StoreError('unsafe-sharing-compose') from None


class SharingService:
    def __init__(self, install_dir, data_dir, extension_dir, *, port, resolve_flags, invalidate, run=None):
        if os.name != 'posix' or not hasattr(os, 'geteuid'):
            raise StoreError('unsupported-platform')
        if type(port) is not int or not 1024 <= port <= 65535:
            raise StoreError('invalid-sharing-port')
        self.install_dir = Path(install_dir).absolute()
        self.directory = Path(data_dir).absolute() / SERVICE
        self.extension_dir = Path(extension_dir).absolute()
        self.port, self.resolve_flags, self.invalidate = port, resolve_flags, invalidate
        self.run = run or subprocess.run

    def _run(self, args, *, timeout=10):
        try:
            return self.run(args, cwd=str(self.install_dir), capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise StoreError('sharing-service-unavailable') from None

    def _inspect(self, name, *, expected_service=None):
        result = self._run(['docker','container','inspect',name])
        if result.returncode:
            if 'No such object' in result.stderr or 'No such container' in result.stderr:
                return None
            raise StoreError('sharing-service-unavailable')
        try:
            if len(result.stdout) > 1024 * 1024:
                raise ValueError('oversize')
            items = json.loads(result.stdout)
            if not isinstance(items, list) or len(items) != 1:
                raise ValueError('invalid')
            item = items[0]
            labels = item['Config']['Labels']
            if (not isinstance(item.get('Id'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['Id'])
                    or labels.get('com.docker.compose.service') != (expected_service or name.removeprefix('ods-'))
                    or Path(labels['com.docker.compose.project.working_dir']).absolute() != self.install_dir):
                raise ValueError('identity')
            return item
        except (ValueError, KeyError, TypeError, AttributeError):
            raise StoreError('sharing-container-identity-mismatch') from None

    def status(self):
        try:
            item = self._inspect(CONTAINER)
            if item is None or item['State']['Running'] is not True:
                return {'status':'stopped'}
            return {'status':'ready' if item['State'].get('Health',{}).get('Status') == 'healthy' else 'unavailable'}
        except (StoreError, KeyError, TypeError):
            return {'status':'unavailable'}

    @staticmethod
    def _fingerprint(path):
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_mode & 0o022 or metadata.st_size > 256 * 1024):
            raise StoreError('unsafe-sharing-compose')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def start(self):
        enabled = self.extension_dir / 'compose.yaml'
        disabled = self.extension_dir / 'compose.yaml.disabled'
        renamed = False
        fingerprint = None
        activation = uuid.uuid4().hex
        override = None
        try:
            route = self._inspect('ods-model-router')
            if (route is None or route['State']['Running'] is not True
                    or route['State'].get('Health',{}).get('Status') != 'healthy'):
                raise StoreError('model-router-not-ready')
            # A colliding container is never ours to recreate or stop.
            self._inspect(CONTAINER)
            if enabled.exists() or enabled.is_symlink():
                fingerprint = self._fingerprint(enabled)
                if disabled.exists() or disabled.is_symlink():
                    raise StoreError('sharing-compose-conflict')
            else:
                fingerprint = self._fingerprint(disabled)
                os.rename(disabled, enabled)
                renamed = True
                self.invalidate()
            flags = self.resolve_flags()
            # The nonce identifies exactly this activation, including a partial
            # `up` that times out before returning a container ID. A name or a
            # different owner's subsequent replacement is not cleanup authority.
            with tempfile.NamedTemporaryFile(mode='w', dir=self.install_dir,
                    prefix='.ods-sharing-activation-', suffix='.json', delete=False) as handle:
                override = Path(handle.name)
                json.dump({'services': {SERVICE: {'labels': {ACTIVATION_LABEL: activation}}}}, handle)
            prefix = ['docker','compose',*flags,'-f',str(override)]
            resolved = self._run([*prefix,'config','--format','json'])
            if resolved.returncode or len(resolved.stdout) > 8 * 1024 * 1024:
                raise StoreError('sharing-compose-invalid')
            validate_compose(json.loads(resolved.stdout), directory=self.directory, port=self.port,
                             uid=os.geteuid(), gid=os.getegid())
            built = self._run([*prefix,'build',SERVICE], timeout=300)
            if built.returncode:
                raise StoreError('sharing-build-failed')
            started = self._run([*prefix,'up','-d','--no-deps','--no-build','--pull','never',SERVICE], timeout=60)
            if started.returncode:
                raise StoreError('sharing-start-failed')
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                current = self._inspect(CONTAINER)
                if current is None or current['Config']['Labels'].get(ACTIVATION_LABEL) != activation:
                    raise StoreError('sharing-activation-changed')
                if (current['State']['Running'] is True
                        and current['State'].get('Health',{}).get('Status') == 'healthy'):
                    return
                time.sleep(.5)
            raise StoreError('sharing-health-unverified')
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            try:
                candidate = self._inspect(CONTAINER)
                if candidate is not None and candidate['Config']['Labels'].get(ACTIVATION_LABEL) == activation:
                    self._run(['docker','container','stop','--time','10',candidate['Id']], timeout=20)
            except (StoreError, KeyError, TypeError):
                pass  # Owner controller also closes admission on failed start.
            # Restore only the exact template this operation enabled. Never
            # overwrite an intervening edit, and never restart dependencies.
            if renamed and not disabled.exists():
                try:
                    if self._fingerprint(enabled) == fingerprint:
                        os.rename(enabled, disabled)
                        self.invalidate()
                except (OSError, StoreError):
                    pass
            if isinstance(error, StoreError):
                raise
            raise StoreError('sharing-service-unavailable') from None
        finally:
            if override is not None:
                try:
                    override.unlink()
                except OSError:
                    pass

    def stop(self):
        existing = self._inspect(CONTAINER)
        if existing is not None and existing['State']['Running'] is True:
            stopped = self._run(['docker','container','stop','--time','10',existing['Id']], timeout=20)
            if stopped.returncode:
                raise StoreError('sharing-stop-failed')
        if self.status()['status'] != 'stopped':
            raise StoreError('sharing-stop-unverified')
        enabled = self.extension_dir / 'compose.yaml'
        disabled = self.extension_dir / 'compose.yaml.disabled'
        if enabled.exists() or enabled.is_symlink():
            self._fingerprint(enabled)
            if disabled.exists() or disabled.is_symlink():
                raise StoreError('sharing-compose-conflict')
            os.rename(enabled, disabled)
            self.invalidate()
