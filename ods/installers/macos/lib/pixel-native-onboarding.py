"""Render native onboarding from the installed ODS model contract.

Use the shared host installer functions so aliases, output budgets and reasoning
defaults do not diverge between Linux/WSL and macOS.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


def helper(name):
    spec = importlib.util.spec_from_file_location('native_onboarding_' + name,
        Path(__file__).with_name('pixel-native-' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def search_helper(ods_source):
    spec = importlib.util.spec_from_file_location('native_onboarding_search',
        Path(ods_source) / 'extensions/services/pixel-agent/host/native_search.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(*, source, ref, ods_source, install_dir, home, runtime, node, destination, workspace=None,
          previous_config=None):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    bootstrap = helper('bootstrap')
    bootstrap.selected_release(source, ref)
    environment = helper('env')
    raw, _ = environment.snapshot(Path(install_dir) / '.env')
    values = {}
    for line in raw.decode('utf-8').splitlines():
        match = environment.ASSIGNMENT.fullmatch(line)
        if match:
            if match[1] in values:
                raise ValueError('duplicate-ods-onboarding-value')
            values[match[1]] = environment.values.parse_env_value(match[2])
    allowed = ('GGUF_FILE', 'LLM_MODEL', 'EXTERNAL_LLM_URL', 'EXTERNAL_LLM_MODEL',
        'ODS_MODEL_SWITCHBOARD', 'LLAMA_REASONING', 'PIXEL_MODEL_RELAY_PORT', 'PIXEL_MODEL_RELAY_KEY', 'SEARXNG_PORT')
    env = {key: values[key] for key in allowed if key in values}
    context = values.get('CTX_SIZE', '')
    if not re.fullmatch('[0-9]{4,8}', context) or not 4096 <= int(context) <= 10000000:
        raise ValueError('installed-ods-context-required')
    if not values.get('EXTERNAL_LLM_URL') and not (values.get('GGUF_FILE') or values.get('LLM_MODEL')):
        raise ValueError('installed-ods-model-required')
    key = values.get('PIXEL_MODEL_RELAY_KEY', '')
    previous_snapshot = None
    if previous_config is not None:
        previous_snapshot = environment.snapshot(Path(previous_config))
        previous = json.loads(previous_snapshot[0])
        key = environment.relay_credential(previous, values)
        env['PIXEL_MODEL_RELAY_KEY'] = key
    if not re.fullmatch('[a-f0-9]{64}', key):
        raise ValueError('installed-model-relay-credential-required')
    node, ods_source = Path(node).resolve(strict=True), Path(ods_source).resolve(strict=True)
    home, runtime, destination = [Path(path) for path in (home, runtime, destination)]
    if not all(path.is_absolute() for path in (home, runtime, destination)):
        raise ValueError('absolute-native-onboarding-paths-required')
    if workspace is not None:
        workspace = Path(workspace)
        if (not workspace.is_absolute() or workspace == Path('/') or not workspace.is_dir()
                or workspace.resolve(strict=True) != workspace):
            raise ValueError('existing-canonical-migration-workspace-required')
    search = search_helper(ods_source)
    provider = search.select_provider(destination, values.get('PIXEL_WEB_SEARCH_PROVIDER'))
    parallel_path, parallel_digest = '', ''
    if provider == 'parallel-free':
        provisioned = search.prepare(destination.parent / 'native-search')
        parallel_path = provisioned['path']
        parallel_digest = bootstrap.command([str(node), str(Path(source) / 'scripts/hash-gateway-extension.mjs'),
            parallel_path], cwd=destination.parent)
        if not re.fullmatch('[a-f0-9]{64}', parallel_digest):
            raise ValueError('native-search-plugin-digest-required')
    plugin = ods_source / 'extensions/services/pixel-agent/plugin'
    digest = bootstrap.command([str(node), str(Path(source) / 'scripts/hash-gateway-extension.mjs'), str(plugin)],
        cwd=destination.parent)
    if not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('native-ods-plugin-digest-required')
    env.update(HOME=str(Path.home()), PATH=':'.join([str(Path(sys.executable).parent), str(node.parent),
        '/usr/bin', '/bin', '/usr/sbin', '/sbin']), MAX_CONTEXT=context,
        INSTALL_DIR=str(Path(install_dir).resolve(strict=True)),
        PIXEL_GATEWAY_PORT=values.get('PIXEL_NATIVE_GATEWAY_PORT') or values.get('PIXEL_GATEWAY_PORT', '18789'))
    script = ('source "$1"\n'
        'ods_pixel_run_as_owner() { shift 2; "$@"; }\n'
        'ai_bad() { printf "%s\\n" "$*" >&2; }\n'
        '_ods_pixel_write_operations_policy unused "$2" "$7" "${11}" || exit $?\n'
        '_ods_pixel_write_onboarding unused "$2" "$3" "$4" "$5" "$6" "$8" "$9" "${10}"\n')
    bootstrap.command(['/bin/bash', '-c', script, 'native-onboarding',
        str(ods_source / 'installers/lib/pixel-host-install.sh'), str(home), str(destination),
        str(runtime / 'node_modules/.bin/openclaw'), str(plugin), digest,
        str(destination.parent / 'operations-policy.json'), provider, parallel_path, parallel_digest,
        str(workspace) if workspace is not None else ''],
        cwd=destination.parent, env=env)
    if environment.snapshot(Path(install_dir) / '.env')[0] != raw:
        raise ValueError('ods-environment-changed-during-onboarding')
    if previous_snapshot is not None and environment.snapshot(Path(previous_config)) != previous_snapshot:
        raise ValueError('native-migration-source-config-changed')
    return destination
