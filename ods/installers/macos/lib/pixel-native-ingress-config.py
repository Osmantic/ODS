"""Stage immutable ingress configuration through the approved Compose runner.

This primitive does not select or restart ingress. Its caller must publish the
returned digest using its existing private selection/rollback transaction.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re


def _environment():
    spec = importlib.util.spec_from_file_location('native_ingress_environment',
        Path(__file__).with_name('pixel-native-env.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rendered_literal(value):
    """Decode one Compose config-JSON escape layer for host-side consumption.

    Keep the original rendered strings when saving a Compose replay file.
    This is not variable expansion; even ${NAME} remains literal data.
    """
    if type(value) is not str:
        raise ValueError('native-ingress-compose-string-required')
    return value.replace('$$', '$')


def stage(run, source, *, user, docker_run, install_dir, document=None):
    """Return a verified digest; leave all existing snapshots untouched."""
    if not re.fullmatch(str(os.getuid()) + r':[0-9]+', user) or os.getuid() == 0:
        raise ValueError('native-ingress-owner-required')
    environment = _environment()
    before = environment.snapshot(source)
    body = before[0]
    configuration = json.loads(body)
    if type(configuration) is not dict:
        raise ValueError('native-ingress-json-config-required')
    expected = hashlib.sha256(body).hexdigest()
    if document is None:
        selected = run('--profile', 'native-config-maintenance', 'config', '--format', 'json', timeout=30)
        if selected.returncode or len(selected.stdout) > 4 * 1024 * 1024:
            raise ValueError('native-ingress-config-compose-required')
        document = json.loads(selected.stdout)
    spec = importlib.util.spec_from_file_location('native_config_volume',
        Path(__file__).with_name('pixel-native-config-volume.py'))
    storage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(storage)
    storage.ensure(docker_run, document, install_dir)
    # Runners validate the selected Compose files and pin the local Docker
    # endpoint. Neither operation starts ingress or publishes a new selection.
    initialized = run('run', '--rm', '--no-deps', '-T', 'pixel-native-config-init', timeout=30)
    if initialized.returncode:
        raise ValueError('native-ingress-config-storage-failed')
    if environment.snapshot(source) != before:
        raise ValueError('native-ingress-config-source-changed')
    result = run('run', '--rm', '--no-deps', '-T', 'pixel-native-config-write', expected,
        input=body.decode('utf-8'), timeout=30)
    if result.returncode or len(result.stdout) > 1024:
        raise ValueError('native-ingress-config-staging-failed')
    try:
        receipt = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError('native-ingress-config-readback-failed') from None
    if (type(receipt) is not dict or set(receipt) != {'sha256', 'reused'}
            or receipt['sha256'] != expected or type(receipt['reused']) is not bool):
        raise ValueError('native-ingress-config-readback-failed')
    if environment.snapshot(source) != before:
        raise ValueError('native-ingress-config-source-changed')
    return receipt


def prepare(run, *, docker_run, environment_path, backup, checkpoint=None):
    """Stage first, then select the digest with existing guarded env custody.

    The caller holds its lifecycle/admission boundary and retains backup until
    final health. On failure it can use pixel-native-env.restore with the
    returned post-write bytes. Immutable prior snapshots are never deleted.
    """
    environment = _environment()
    env_before = environment.snapshot(environment_path)
    result = run('--profile', 'native-config-maintenance', 'config', '--format', 'json', timeout=30)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError('native-ingress-config-compose-required')
    document = json.loads(result.stdout)
    services = document['services']
    writer, ingress = services['pixel-native-config-write'], services['pixel-native-ingress']
    user = writer.get('user')
    image = writer.get('image', '')
    if (user != ingress.get('user') or image != ingress.get('image')
            or not re.fullmatch('sha256:[a-f0-9]{64}', image)
            or writer.get('network_mode') != 'none' or writer.get('read_only') is not True
            or writer.get('cap_drop') != ['ALL']
            or writer.get('entrypoint') != ['node', '/source/native_ingress_config.mjs']):
        raise ValueError('native-ingress-config-writer-selection-required')
    source = Path(rendered_literal(writer['labels']['org.osmantic.ods.native-config-source']))
    source_before = environment.snapshot(source)
    receipt = stage(run, source, user=user, docker_run=docker_run,
        install_dir=Path(environment_path).parent, document=document)
    if receipt['sha256'] != hashlib.sha256(source_before[0]).hexdigest():
        raise ValueError('native-ingress-config-source-changed')
    def unchanged():
        if environment.snapshot(source) != source_before:
            raise ValueError('native-ingress-config-source-changed')
    after_path = Path(str(backup) + '.after')
    def selection(body, changed):
        return {**receipt, 'environmentChanged': changed,
            'environmentBackup': str(backup), 'environmentAfter': str(after_path),
            'environmentBeforeSha256': hashlib.sha256(env_before[0]).hexdigest(),
            'environmentAfterSha256': hashlib.sha256(body).hexdigest()}
    def retain_after(body):
        with after_path.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # Persist recovery custody before the environment can be published.
        if checkpoint is not None:
            checkpoint(selection(body, True))
    after = environment._persist(environment_path, {'PIXEL_NATIVE_CONFIG_SHA256': receipt['sha256']},
        backup=backup, expected=env_before, before_replace=unchanged, return_bytes=True,
        replace_ingress_digest=True, before_write=retain_after)
    return selection(after or env_before[0], after is not None)


def restore_environment(environment_path, receipt):
    """Restore the previous digest only when this attempt still owns .env."""
    if not receipt['environmentChanged']:
        return False
    environment = _environment()
    if hashlib.sha256(environment.snapshot(environment_path)[0]).hexdigest() == receipt['environmentBeforeSha256']:
        return False
    after = environment.snapshot(receipt['environmentAfter'])[0]
    if hashlib.sha256(after).hexdigest() != receipt['environmentAfterSha256']:
        raise ValueError('native-ingress-config-recovery-changed')
    return environment.restore(environment_path, backup=receipt['environmentBackup'], expected=after)
