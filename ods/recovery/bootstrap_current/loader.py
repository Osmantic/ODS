#!/usr/bin/python3
"""Root-provisioned entry point; validate/capture all code before importing it."""
import sys
if not sys.flags.isolated:
    raise SystemExit('isolated-python-required')
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import stat

sys.dont_write_bytecode = True
FILES = ('custody.py', 'preflight.py', 'scope.py', 'socket_custody.py', 'adapters.py', 'controller.py', 'owner.py', 'quarantine.py', 'compatibility.json')


def capture(directory, names):
    """Bootstrap independent of package imports; retain directory fds throughout."""
    descriptors = []
    try:
        root = Path(directory)
        if not root.is_absolute() or '..' in root.parts:
            raise RuntimeError('package-path-invalid')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        descriptors.append((fd, '/'))
        for part in root.parts[1:]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            descriptors.append((fd, part))
        def validate():
            for index, (current, name) in enumerate(descriptors):
                info = os.fstat(current)
                named = os.stat(name, dir_fd=descriptors[index-1][0] if index else None, follow_symlinks=False)
                if (info.st_uid != 0 or info.st_mode & 0o022
                        or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
                    raise RuntimeError('package-directory-custody')
        validate()
        result = {}
        for name in names:
            if '/' in name or name in ('', '.', '..'):
                raise RuntimeError('package-file-invalid')
            opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
            try:
                info = os.fstat(opened)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                        or info.st_nlink != 1 or info.st_size > 2*1024*1024):
                    raise RuntimeError('package-file-custody')
                with os.fdopen(os.dup(opened), 'rb') as stream:
                    raw = stream.read(2*1024*1024 + 1)
                after = os.fstat(opened)
                named = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (len(raw) > 2*1024*1024 or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
                        or (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                    raise RuntimeError('package-file-changed')
                result[name] = raw
            finally:
                os.close(opened)
        validate()
        return result
    finally:
        while descriptors:
            os.close(descriptors.pop()[0])


class Captured(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, root, files):
        self.root, self.files = root, files

    def find_spec(self, fullname, path=None, target=None):
        relative = fullname.replace('.', '/')
        name = relative + '.py'
        package = relative + '/__init__.py'
        if name not in self.files and package not in self.files:
            return None
        return importlib.util.spec_from_loader(fullname, self, is_package=package in self.files)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        relative = module.__name__.replace('.', '/')
        name = relative + ('/__init__.py' if hasattr(module, '__path__') else '.py')
        module.__file__ = str(self.root / name)
        exec(compile(self.files[name], module.__file__, 'exec'), module.__dict__)


def package():
    if os.geteuid() != 0:
        raise RuntimeError('root-required')
    root = Path(__file__).absolute().parent
    raw = capture(root, (*FILES, 'manifest.json', 'loader.py'))
    manifest = json.loads(raw['manifest.json'])
    if (set(manifest) != {'version', 'files'} or manifest['version'] != 1
            or set(manifest['files']) != {*FILES, 'loader.py'}
            or any(hashlib.sha256(raw[name]).hexdigest() != value for name, value in manifest['files'].items())):
        raise RuntimeError('package-manifest-mismatch')
    sys.meta_path.insert(0, Captured(root, {name: raw[name] for name in FILES if name.endswith('.py')}))
    # This digest authenticates the bytes used by Captured. Never label an
    # already-imported controller with a later pathname's manifest version.
    captured = dict(manifest, capturedSha256=hashlib.sha256(raw['manifest.json']).hexdigest(), ownerProgram=raw['owner.py'])
    return root, captured, json.loads(raw['compatibility.json'])


def main(argv):
    root, manifest, compatibility = package()
    import preflight
    bundle = preflight.capture_bundle(compatibility)
    sys.meta_path.insert(0, Captured(preflight.BUNDLE, bundle))
    import controller
    return controller.main(argv, root, manifest, compatibility)


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as error:
        # Never echo arbitrary exception text or private paths from old adapters.
        import re
        label = str(error) if type(error).__name__ == 'Refused' and re.fullmatch('[a-z][a-z0-9-]{0,95}', str(error)) else 'recovery-unavailable'
        print(json.dumps({'ok': False, 'error': label}), file=sys.stderr)
        raise SystemExit(1) from None
