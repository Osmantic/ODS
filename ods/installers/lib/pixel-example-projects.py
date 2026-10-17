"""Add the finished example projects ODS ships to an owner's Portal workspace.

Each example is copied once to Playground/<name>, and only when nothing of that
name exists there. After that it is ordinary owner content: ODS never
overwrites, merges into, chmods or deletes it. Run as the non-root workspace
owner after Portal is verified. Every shipped file's SHA-256 is pinned below;
when any source file differs, nothing is copied.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat

SOURCE = Path(__file__).resolve().parents[2] / 'extensions/services/pixel-agent/example-projects'
MAX_BYTES = 4 * 1024 * 1024
STAGING_PREFIX = '.ods-example-'
# Elf of Doom: written in ODS by Portal from natural-language prompts. The game
# files are exactly the bytes published at https://odsspark.com/game/.
EXAMPLES = {
    'elf-of-doom': {
        'README.md': 'ab083d7279daf47a43f0e29df7368df8a2f4b61da8ae7234a62a8ccfd280ebd8',
        'chars.js': '161b08c8a331fef5f09ac45e6ade2cff62e03316ba7c6ac197adf8147af629db',
        'dungeon.js': '80dfc2e0517cb89bce4f445d75e6a85a4a9c2317893a6762d87666b5b136004c',
        'engine.js': '2f573a818c04b23554a95426bbe7a8a367780c9c583e03488fbe5e66e19dbedf',
        'index.html': 'af1c8b2b0a9f4e3ec4f5c6389078cf0b3a187a53badd451006ce24b737fd9906',
        'legacy.js': '0d33ded8670f4113f47baba1325ed52247305cbb9a0c92271aa7c8ef0c498165',
        'style.css': 'e063614db72fbc5c607bd4c79d4e2e967a0e5deceb3962f0a50f045e55197ee9',
        'ui.js': '5d0138ba4517d812359d4dd474f6febccf4dc703870412234a092cf8a04ef7f1',
        'world.js': 'b45864ccd2bed3d9413e571a167683f8b44cba29559bbee856a9f4d56ff8aff5',
    },
}


def _owner_directory(path):
    """Open the workspace with the guidance migration's checked owner walk.

    That walk opens every component from / without following links, and
    requires an owner-owned workspace that is not group- or other-writable.
    """
    spec = importlib.util.spec_from_file_location(
        'ods_example_workspace_guidance', Path(__file__).resolve().with_name('pixel-workspace-guidance.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._directory(path)


def _read_source(directory, expected):
    """Return the pinned files' bytes; any difference stops before a write."""
    files = {}
    for name, digest in sorted(expected.items()):
        fd = os.open(str(directory / name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('example source must be a regular file')
            body = stream.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError('example source does not match the shipped bytes')
        files[name] = body
    return files


def _exists(directory, name):
    """Any entry counts: a folder (even empty), a file or a link (even dangling)."""
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _playground(workspace):
    try:
        # Portal creates Playground the same way when it starts a project.
        os.mkdir('Playground', 0o700, dir_fd=workspace)
    except FileExistsError:
        pass
    playground = os.open('Playground', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=workspace)
    info = os.fstat(playground)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        os.close(playground)
        raise ValueError('private owner Playground required')
    return playground


def _write(directory, name, body):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        # Never inherit source or umask modes; Portal's preview refuses
        # group- or other-writable files.
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _discard(playground, staging, names):
    """Remove only this run's staging folder and the files it wrote there."""
    directory = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=playground)
    try:
        for name in names:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass  # The run stopped before it created this file.
    finally:
        os.close(directory)
    os.rmdir(staging, dir_fd=playground)


def _add(playground, name, files):
    if _exists(playground, name):
        return 'present'
    # Stage under a hidden sibling, which Portal's preview and project routing
    # ignore, so an interrupted copy never leaves a partial Playground/<name>.
    staging = STAGING_PREFIX + secrets.token_hex(8)
    os.mkdir(staging, 0o700, dir_fd=playground)
    renamed = False
    try:
        directory = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=playground)
        try:
            os.fchmod(directory, 0o700)
            for filename, body in sorted(files.items()):
                _write(directory, filename, body)
            os.fsync(directory)
        finally:
            os.close(directory)
        if _exists(playground, name):
            return 'present'
        # rename(2) refuses a non-empty folder, a file or a link here. Only an
        # empty folder created after the check above could be replaced.
        os.rename(staging, name, src_dir_fd=playground, dst_dir_fd=playground)
        renamed = True
        os.fsync(playground)
        return 'added'
    finally:
        if not renamed:
            _discard(playground, staging, files)


def add_projects(workspace, source=SOURCE):
    if os.geteuid() == 0:
        raise ValueError('add example projects as the non-root workspace owner')
    projects = {name: _read_source(Path(source) / name, expected) for name, expected in sorted(EXAMPLES.items())}
    root = _owner_directory(workspace)
    try:
        playground = _playground(root)
    finally:
        os.close(root)
    try:
        return {name: _add(playground, name, files) for name, files in projects.items()}
    finally:
        os.close(playground)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help="the owner's absolute Portal workspace path")
    args = parser.parse_args()
    print(json.dumps(add_projects(args.workspace), sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
