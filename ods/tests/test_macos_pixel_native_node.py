import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_node',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-node.py')
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


@pytest.mark.parametrize('fault', [None, 'archive', 'binary', 'oversize', 'signature',
                                   'external-library', 'identity'])
def test_pinned_standalone_node_is_verified_before_bundle_use(tmp_path, monkeypatch, fault):
    binary = b'fixture standalone node executable'
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode='w:xz') as archive:
        member = tarfile.TarInfo('node-v' + native.VERSION + '-darwin-arm64/bin/node')
        member.mode = 0o755
        member.size = len(binary)
        archive.addfile(member, io.BytesIO(binary))
    body = package.getvalue()
    monkeypatch.setattr(native, 'ARCHIVE_SHA256', hashlib.sha256(body).hexdigest()
                        if fault != 'archive' else '0' * 64)
    monkeypatch.setattr(native, 'NODE_SHA256', hashlib.sha256(binary).hexdigest()
                        if fault != 'binary' else '0' * 64)
    if fault == 'oversize': monkeypatch.setattr(native, 'MAX_ARCHIVE', 8)
    monkeypatch.setattr(native.urllib.request, 'build_opener',
                        lambda *args: SimpleNamespace(open=lambda *a, **k: io.BytesIO(body)))
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        if args[0] == '/usr/bin/codesign':
            return SimpleNamespace(returncode=1 if fault == 'signature' else 0, stdout='', stderr='')
        if args[0] == '/usr/bin/otool':
            library = '/opt/homebrew/lib/libnode.dylib' if fault == 'external-library' else '/usr/lib/libSystem.B.dylib'
            return SimpleNamespace(returncode=0, stdout='node:\n\t' + library + ' (compatibility version 1.0.0)\n', stderr='')
        assert args[0] == str(tmp_path / 'node')
        identity = {'platform': 'darwin', 'arch': 'x64' if fault == 'identity' else 'arm64',
                    'version': 'v' + native.VERSION}
        return SimpleNamespace(returncode=0, stdout=json.dumps(identity), stderr='')
    monkeypatch.setattr(native.subprocess, 'run', run)
    if fault:
        with pytest.raises(native.NodeAcquisitionError): native.acquire(tmp_path, minimum_major=22)
    else:
        assert native.acquire(tmp_path, minimum_major=22) == tmp_path / 'node'
        assert (tmp_path / 'node').read_bytes() == binary
        if os.name != 'nt':
            assert (tmp_path / 'node').stat().st_mode & 0o777 == 0o755
        assert [item[0] for item in commands] == ['/usr/bin/codesign', '/usr/bin/otool',
                                                    str(tmp_path / 'node')]


def test_standalone_node_rejects_newer_runtime_requirement(tmp_path):
    with pytest.raises(native.NodeAcquisitionError, match='below-release-minimum'):
        native.acquire(tmp_path, minimum_major=26)


def test_standalone_node_refuses_redirect():
    with pytest.raises(native.NodeAcquisitionError, match='redirect-refused'):
        native.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.test')
