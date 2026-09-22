"""Owner-scoped project references, separate from installation/runtime state."""
import json
import os
import re
import tempfile
from pathlib import Path


def validate_project(project):
    if (not isinstance(project, str) or len(project) > 512
            or not project.startswith('Playground/') or len(project.split('/')) != 2
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', project.split('/')[1])):
        raise ValueError('Select an observed Playground project')
    return project


def read_projects(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Invalid project reference file')
    if not path.exists():
        return []
    if path.stat().st_size > 128 * 1024:
        raise ValueError('Oversized project reference file')
    document = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(document, dict) or set(document) != {'schemaVersion', 'projects'}
            or document['schemaVersion'] != 1 or not isinstance(document['projects'], list)
            or len(document['projects']) > 200):
        raise ValueError('Invalid project references')
    projects = [validate_project(value) for value in document['projects']]
    if len(set(projects)) != len(projects):
        raise ValueError('Duplicate project references')
    return projects


def associate_project(path, project):
    """Caller owns the lock. Association never installs or writes project files."""
    project = validate_project(project)
    path = Path(path)
    projects = read_projects(path)
    if project in projects:
        return projects
    if len(projects) >= 200:
        raise ValueError('Project reference limit reached')
    projects = sorted([*projects, project])
    descriptor, temporary = tempfile.mkstemp(prefix='.project-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'schemaVersion': 1, 'projects': projects}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return projects
