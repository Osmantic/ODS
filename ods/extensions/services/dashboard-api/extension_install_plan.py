"""Read-only installation prerequisites for the Portal's catalog command."""
import re

ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
KEY = re.compile(r'[A-Z][A-Z0-9_]{0,127}')


class InstallPlanError(ValueError):
    pass


def build_install_plan(target, entries, load_service, configured, protected=()):
    if not isinstance(target, str) or not ID.fullmatch(target):
        raise InstallPlanError('Invalid extension ID')
    catalog = {}
    for entry in entries:
        key = entry.get('id') if isinstance(entry, dict) else None
        if not isinstance(key, str) or not ID.fullmatch(key) or key in catalog:
            raise InstallPlanError('Invalid or duplicate catalog ID')
        catalog[key] = entry
    visiting, visited, steps = set(), set(), []

    def visit(key):
        if key in visiting:
            raise InstallPlanError('Circular extension dependency')
        if key in visited:
            return
        if key not in catalog:
            raise InstallPlanError(f'Dependency absent from catalog: {key}')
        if len(visiting) + len(visited) >= 128:
            raise InstallPlanError('Extension dependency plan exceeds 128 services')
        visiting.add(key)
        svc = load_service(key)
        if not isinstance(svc, dict) or svc.get('id') != key:
            raise InstallPlanError(f'Invalid service definition: {key}')
        deps = svc.get('depends_on', [])
        if not isinstance(deps, list) or any(not isinstance(dep, str) or not ID.fullmatch(dep) for dep in deps):
            raise InstallPlanError(f'Invalid dependencies: {key}')
        deps = list(dict.fromkeys(deps))
        for dep in deps:
            visit(dep)
        row = catalog[key]
        status = row.get('status')
        if not isinstance(status, str):
            raise InstallPlanError(f'Invalid runtime state: {key}')
        action = {'enabled': 'none', 'cli_installed': 'none', 'disabled': 'enable',
                  'stopped': 'enable', 'not_installed': 'install',
                  'installing': 'wait', 'setting_up': 'wait'}.get(status, 'blocked')
        reason = None
        if action == 'blocked':
            reason = 'Inspect the unavailable or failed service before changing it'
        if key in protected and action not in {'none', 'wait'}:
            action, reason = 'blocked', 'ODS manages this service'
        if action == 'install' and row.get('installable') is not True:
            action, reason = 'blocked', 'No installable recipe for this host'
        declarations = svc.get('env_vars', [])
        if not isinstance(declarations, list) or len(declarations) > 128:
            raise InstallPlanError(f'Invalid configuration declarations: {key}')
        fields, seen = [], set()
        for item in declarations:
            if not isinstance(item, dict) or not isinstance(item.get('key'), str) or not KEY.fullmatch(item['key']):
                raise InstallPlanError(f'Invalid configuration declaration: {key}')
            name = item['key']
            required, secret = item.get('required', False), item.get('secret', False)
            if name in seen or type(required) is not bool or type(secret) is not bool:
                raise InstallPlanError(f'Ambiguous configuration declaration: {key}')
            seen.add(name)
            # Only presence crosses this boundary; never project .env values or defaults.
            present = configured(name)
            if type(present) is not bool:
                raise InstallPlanError('Invalid configuration presence result')
            description = item.get('description', '')
            if not isinstance(description, str):
                raise InstallPlanError(f'Invalid configuration description: {key}')
            fields.append({'key': name, 'required': required, 'secret': secret, 'configured': present,
                           'description': description[:500]})
        missing = [field['key'] for field in fields if field['required'] and not field['configured']]
        steps.append({'extensionId': key, 'status': status, 'action': action,
                      'dependsOn': deps, 'configuration': fields,
                      'missingConfiguration': missing if action in {'install', 'enable'} else [],
                      'reason': reason})
        visiting.remove(key)
        visited.add(key)

    visit(target)
    return {'schemaVersion': 1, 'extensionId': target, 'steps': steps,
            'requiresConfiguration': any(step['missingConfiguration'] for step in steps),
            'blocked': any(step['action'] == 'blocked' for step in steps),
            'pending': any(step['action'] == 'wait' for step in steps),
            'executionStarted': False}
