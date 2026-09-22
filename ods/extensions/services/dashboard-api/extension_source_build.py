"""Commit-bound source builds. Does not execute or download repository code."""
import re
import hashlib
from urllib.parse import urlsplit

from extension_github import repository_identity


def source_builds(candidate):
    """Bind upstream or proposed Dockerfiles to the immutable source context."""
    repository = repository_identity(candidate['repository']).lower()
    commit = candidate['commit']
    if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Immutable source revision required')
    result = []
    for name, service in candidate['compose']['services'].items():
        if not isinstance(service, dict) or 'build' not in service:
            continue
        build = service['build']
        if (not isinstance(build, dict) or set(build) - {'context', 'dockerfile', 'dockerfile_inline', 'target'}
                or not isinstance(build.get('context'), str)):
            raise ValueError('Unsupported source build settings')
        context = build['context']
        parsed = urlsplit(context)
        source = context.split('#', 1)[0]
        revision, separator, directory = parsed.fragment.partition(':')
        if (repository_identity(source).lower() != repository or revision != commit
                or not parsed.path.endswith('.git') or (separator and not directory)):
            raise ValueError('Build must use the selected repository and revision')
        dockerfile = build.get('dockerfile', 'Dockerfile')
        for path, required in ((directory, False), (dockerfile, True)):
            if (not isinstance(path, str) or len(path) > 256
                    or (required and not path)
                    or (path and any(not re.fullmatch(r'[A-Za-z0-9_.-]+', part)
                                     or part in {'.', '..'} for part in path.split('/')))):
                raise ValueError('Build file must stay within the repository context')
        target = build.get('target')
        if target is not None and (not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', target)):
            raise ValueError('Invalid Dockerfile target')
        if (service.get('image') != f'ods-source-{name}:{commit}'
                or service.get('pull_policy') != 'never'):
            raise ValueError('Source builds require an owned local image')
        if 'dockerfile_inline' in build:
            inline = build['dockerfile_inline']
            if ('dockerfile' in build or not isinstance(inline, str) or not inline.strip()
                    or len(inline.encode('utf-8')) > 24576
                    or any(ord(char) < 32 and char not in '\n\r\t' for char in inline)
                    or '$' in re.findall(r'\$\$|\$', inline)):
                raise ValueError('Inline Dockerfile must be bounded and escape Compose dollars')
            result.append({'service': name, 'kind': 'proposed-dockerfile',
                           'sha256': hashlib.sha256(inline.encode('utf-8')).hexdigest()})
        else:
            result.append({'service': name, 'path': '/'.join(filter(None, (directory, dockerfile)))})
    return result


async def inspect_source_builds(candidate, inspect_file):
    receipts, cached = [], {}
    for spec in source_builds(candidate):
        if spec.get('kind') == 'proposed-dockerfile':
            # These bytes belong to the reviewed recipe, not to an upstream
            # file. Never fabricate a Git blob or claim upstream authorship.
            receipts.append(spec)
            continue
        path = spec['path']
        if path not in cached:
            file = await inspect_file(candidate['repository'], candidate['commit'], path)
            if (repository_identity(file.get('repository')).lower() != repository_identity(candidate['repository']).lower()
                    or file.get('commit') != candidate['commit'] or file.get('path') != path
                    or file.get('evidenceScope') != 'repository-file-at-commit'
                    or not isinstance(file.get('content'), str) or not file['content'].strip()
                    or not isinstance(file.get('blob'), str)
                    or not re.fullmatch(r'[a-f0-9]{40}', file.get('blob', ''))):
                raise ValueError('Matching source file evidence required')
            cached[path] = file['blob']
        receipts.append({**spec, 'blob': cached[path]})
    return receipts


def verify_source_receipts(candidate, receipts):
    specs = source_builds(candidate)
    if not specs and receipts is None:
        return
    if not isinstance(receipts, list) or len(receipts) != len(specs):
        raise ValueError('Source build evidence required')
    for spec, receipt in zip(specs, receipts):
        if spec.get('kind') == 'proposed-dockerfile':
            if receipt != spec:
                raise ValueError('Proposed Dockerfile changed')
            continue
        if (not isinstance(receipt, dict) or set(receipt) != {'service', 'path', 'blob'}
                or {key: receipt[key] for key in spec} != spec
                or not isinstance(receipt['blob'], str) or not re.fullmatch(r'[a-f0-9]{40}', receipt['blob'])):
            raise ValueError('Source build evidence changed')
