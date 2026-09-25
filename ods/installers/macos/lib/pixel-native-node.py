"""Acquire the pinned, self-contained Node executable for protected Pixel bundles.

Homebrew's node may link to an owner-writable libnode outside the protected
bundle. It is suitable for npm preparation, but not for the published gateway.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request


VERSION = '24.21.0'
ARCHIVE_NAME = 'node-v' + VERSION + '-darwin-arm64.tar.xz'
ARCHIVE_URL = 'https://nodejs.org/dist/v' + VERSION + '/' + ARCHIVE_NAME
# nodejs.org/dist/v24.21.0/SHASUMS256.txt, plus the extracted executable hash.
ARCHIVE_SHA256 = '6239d4cf92d864487ec8cd3615038f7b67e7f58b77b21cd2f09ea9fbd68065fe'
NODE_SHA256 = 'e4b5a3af0e05c75de2eae013904145f40fe7fc2a6e6f17510128bf45cca4e79b'
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_NODE = 160 * 1024 * 1024


class NodeAcquisitionError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NodeAcquisitionError('standalone-node-redirect-refused')


def _checked_run(args, *, timeout=30):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise NodeAcquisitionError('standalone-node-verification-failed') from error
    if result.returncode:
        raise NodeAcquisitionError('standalone-node-verification-failed')
    return result.stdout.strip()


def acquire(destination, *, minimum_major):
    """Return a verified node file inside an existing private staging directory."""
    destination = Path(destination).resolve(strict=True)
    if not destination.is_dir() or os.path.lexists(destination / 'node'):
        raise NodeAcquisitionError('new-standalone-node-destination-required')
    if type(minimum_major) is not int or minimum_major > 24:
        raise NodeAcquisitionError('pinned-node-below-release-minimum')
    archive = destination / ARCHIVE_NAME
    digest = hashlib.sha256()
    count = 0
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(ARCHIVE_URL, timeout=60) as response, archive.open('xb') as output:
            for block in iter(lambda: response.read(1024 * 1024), b''):
                count += len(block)
                if count > MAX_ARCHIVE:
                    raise NodeAcquisitionError('standalone-node-archive-too-large')
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    except (OSError, urllib.request.URLError) as error:
        raise NodeAcquisitionError('standalone-node-download-failed') from error
    if digest.hexdigest() != ARCHIVE_SHA256:
        raise NodeAcquisitionError('standalone-node-archive-digest-mismatch')
    node = destination / 'node'
    try:
        with tarfile.open(archive, 'r:xz') as package:
            member = package.getmember('node-v' + VERSION + '-darwin-arm64/bin/node')
            if not member.isfile() or not 0 < member.size <= MAX_NODE:
                raise NodeAcquisitionError('invalid-standalone-node-member')
            stream = package.extractfile(member)
            if stream is None:
                raise NodeAcquisitionError('invalid-standalone-node-member')
            binary_digest = hashlib.sha256()
            with stream, node.open('xb') as output:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    binary_digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
    except (tarfile.TarError, KeyError, OSError) as error:
        raise NodeAcquisitionError('invalid-standalone-node-archive') from error
    if binary_digest.hexdigest() != NODE_SHA256:
        raise NodeAcquisitionError('standalone-node-binary-digest-mismatch')
    node.chmod(0o755)
    _checked_run(['/usr/bin/codesign', '--verify', '--strict', str(node)])
    dependencies = _checked_run(['/usr/bin/otool', '-L', str(node)]).splitlines()
    libraries = [line.strip().split(' (', 1)[0] for line in dependencies[1:]]
    if not libraries or any(not library.startswith(('/usr/lib/', '/System/Library/'))
                            for library in libraries):
        raise NodeAcquisitionError('standalone-node-external-library-refused')
    identity = json.loads(_checked_run([str(node), '-p',
        'JSON.stringify({platform:process.platform,arch:process.arch,version:process.version})']))
    if identity != {'platform': 'darwin', 'arch': 'arm64', 'version': 'v' + VERSION}:
        raise NodeAcquisitionError('standalone-node-identity-mismatch')
    return node
