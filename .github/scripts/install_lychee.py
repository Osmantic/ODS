#!/usr/bin/env python3
"""Install the reviewed link checker after verifying the complete archive hash."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile


def install(destination):
    metadata = json.loads((Path(__file__).resolve().parents[1] / 'lychee-tool.json').read_text())
    system = platform.system().lower()
    architecture = platform.machine().lower()
    if architecture in ('amd64', 'x86_64'):
        architecture = 'x86_64'
    artifact = metadata['artifacts'].get(f'{system}-{architecture}')
    if artifact is None:
        raise ValueError('No reviewed Lychee artifact for this platform; use the Linux CI job')
    request = urllib.request.Request(artifact['url'], headers={'User-Agent': 'ODS-public-doc-links/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response:
        archive = response.read(128 * 1024 * 1024 + 1)
    if len(archive) > 128 * 1024 * 1024 or hashlib.sha256(archive).hexdigest() != artifact['sha256']:
        raise ValueError('Lychee archive checksum mismatch; no executable installed')
    # Extract only the one expected regular executable, never archive paths.
    if artifact['url'].endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = [entry for entry in bundle.infolist() if not entry.is_dir() and Path(entry.filename).name == artifact['executable']]
            if len(members) != 1:
                raise ValueError('Expected exactly one Lychee executable')
            body = bundle.read(members[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
            members = [entry for entry in bundle.getmembers() if entry.isfile() and Path(entry.name).name == artifact['executable']]
            if len(members) != 1:
                raise ValueError('Expected exactly one Lychee executable')
            body = bundle.extractfile(members[0]).read()
    destination.mkdir(parents=True, exist_ok=True)
    executable = destination / artifact['executable']
    executable.write_bytes(body)
    executable.chmod(0o755)
    return executable


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(install(args.destination).resolve())
