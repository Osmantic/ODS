#!/usr/bin/env python3
"""Package an exact, GitHub-verified signed ODS tag; never execute its installers."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zipfile

REPOSITORY = 'Osmantic/ODS'
TAG = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', f'repos/{REPOSITORY}/{path}']))


def verified_tag(root, tag, expected_commit, get=api):
    if not TAG.fullmatch(tag) or not SHA.fullmatch(expected_commit):
        raise ValueError('Expected a version tag and full workflow source commit')
    ref = 'refs/tags/' + tag
    object_id = git(root, 'rev-parse', '--verify', ref).decode().strip()
    if git(root, 'cat-file', '-t', object_id).strip() != b'tag':
        raise ValueError('Release requires an annotated, signed tag; lightweight tags are rejected')
    remote = get('git/ref/tags/' + tag)
    obj = remote.get('object', {})
    if obj.get('sha') != object_id or obj.get('type') != 'tag':
        raise ValueError('Remote tag changed or does not identify the checked-out tag')
    annotation = get('git/tags/' + object_id)
    verification = annotation.get('verification', {})
    if (annotation.get('sha') != object_id or annotation.get('tag') != tag
            or verification.get('verified') is not True or verification.get('reason') != 'valid'):
        raise ValueError('GitHub did not verify the exact annotated tag signature')
    commit = git(root, 'rev-parse', '--verify', ref + '^{commit}').decode().strip()
    if (commit != expected_commit or annotation.get('object', {}).get('sha') != commit
            or annotation.get('object', {}).get('type') != 'commit'):
        raise ValueError('Signed tag must point directly to the workflow source commit')
    if git(root, 'rev-parse', 'HEAD').decode().strip() != commit:
        raise ValueError('Checkout does not match the signed source commit')
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', commit, 'origin/main'], check=True)
    return {'repository': REPOSITORY, 'tag': tag, 'tagObject': object_id, 'commit': commit}


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def package_source(root, identity, output):
    """Only git's committed tree is eligible; refuse links or nonportable names."""
    output.mkdir(parents=True, exist_ok=False)
    tree = output / 'source'
    tree.mkdir()
    # git archive also honors core.autocrlf: do not let the build host change
    # source bytes. Repository .gitattributes remain the reviewed authority.
    raw = git(root, '-c', 'core.autocrlf=false', '-c', 'core.eol=lf',
              'archive', '--format=tar', identity['commit'])
    members = []
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for item in archive.getmembers():
            path = PurePosixPath(item.name)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.name
                    or ':' in item.name or not (item.isfile() or item.isdir())):
                raise ValueError('Release source contains an unsupported path or link')
            key = path.as_posix().casefold()
            if key in seen:
                raise ValueError('Release source paths collide on case-insensitive hosts')
            seen.add(key)
            destination = tree.joinpath(*path.parts)
            if item.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            content = archive.extractfile(item).read()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            destination.chmod(0o755 if item.mode & 0o111 else 0o644)
            members.append((item, content))
    for required in ['install.sh', 'install.ps1', 'ods/install.sh', 'ods/installers/windows.ps1']:
        if not (tree / required).is_file():
            raise ValueError('Source archive is missing an installer entrypoint: ' + required)
    prefix = 'ODS-' + identity['tag'] + '-source'
    tar_path = output / (prefix + '.tar.gz')
    with tar_path.open('wb') as handle:
        with gzip.GzipFile(fileobj=handle, filename='', mode='wb', mtime=0) as compressed:
            compressed.write(raw)
    zip_path = output / (prefix + '.zip')
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for item, content in members:
            entry = zipfile.ZipInfo(item.name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (0o100755 if item.mode & 0o111 else 0o100644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)
    (output / 'source-identity.json').write_text(json.dumps(identity, indent=2) + '\n', encoding='utf-8')


def finalize(output):
    identity = json.loads((output / 'source-identity.json').read_text(encoding='utf-8'))
    sbom = output / 'source.spdx.json'
    document = json.loads(sbom.read_text(encoding='utf-8'))
    if document.get('spdxVersion') != 'SPDX-2.3' or not document.get('documentNamespace'):
        raise ValueError('Expected a generated SPDX 2.3 source inventory')
    prefix = 'ODS-' + identity['tag'] + '-source'
    files = [output / (prefix + '.tar.gz'), output / (prefix + '.zip'), sbom]
    manifest = dict(identity, schemaVersion=1,
                    sbomScope='Source dependency inventory; not a scan of container OS layers or downloaded models',
                    files={p.name: {'sha256': digest(p), 'size': p.stat().st_size} for p in files})
    manifest_path = output / 'release-manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    files.append(manifest_path)
    (output / 'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in files), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'finalize'])
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY:
            raise ValueError('Release packaging is restricted to Osmantic/ODS')
        ref = os.environ.get('GITHUB_REF', '')
        if not ref.startswith('refs/tags/'):
            raise ValueError('Run release packaging on the signed tag, not a branch')
        identity = verified_tag(args.root, ref.removeprefix('refs/tags/'), os.environ.get('GITHUB_SHA', ''))
        package_source(args.root, identity, args.output)
    else:
        finalize(args.output)


if __name__ == '__main__':
    main()
