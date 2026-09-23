"""Acquire a pinned, owner-run OpenClaw runtime for native Pixel provisioning.

This stages artifacts only. It neither activates a gateway nor accepts a
license on the owner's behalf. The caller must supply the selected Pixel ref.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid


class BootstrapError(ValueError):
    pass


PLUGINS = (
    ('discord', 'discord', '@openclaw/discord'),
    ('searxng', 'searxng', '@openclaw/searxng-plugin'),
    ('llamaCpp', 'llama-cpp', '@openclaw/llama-cpp-provider'),
)
PIXEL_PLUGINS = (('plugin', 'pixel-source-broker'),
                 ('plugin-ops', 'pixel-operations-broker'),
                 ('plugin-frontier', 'pixel-frontier-broker'))
MACOS_CA_BUNDLE = Path('/etc/ssl/cert.pem')


def validate_package(package, name, version):
    if not re.fullmatch(r'\d{4}\.\d+\.\d+(?:-\d+)?', version):
        raise BootstrapError('invalid-pinned-package-version')
    expected = 'https://registry.npmjs.org/' + name + '/-/' + name.split('/')[-1] + '-' + version + '.tgz'
    if (package.get('url') != expected
            or not re.fullmatch(r'[a-f0-9]{64}', package.get('sha256', ''))
            or not re.fullmatch(r'sha512-[A-Za-z0-9+/]{86}==', package.get('integrity', ''))):
        raise BootstrapError('invalid-pinned-openclaw-package')


def command(args, *, cwd, env=None, timeout=120):
    result = subprocess.run(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=timeout, check=False)
    if result.returncode:
        # npm and Git errors can contain credentials or private registry URLs.
        raise BootstrapError('native-bootstrap-command-failed')
    return result.stdout.strip()


def selected_release(source, ref):
    source = Path(source).resolve(strict=True)
    if not re.fullmatch(r'[a-f0-9]{40}', ref):
        raise BootstrapError('exact-pixel-source-ref-required')
    if (command(['git', 'rev-parse', 'HEAD'], cwd=source) != ref
            or command(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=source)):
        raise BootstrapError('pixel-source-checkout-changed')
    relative = 'scripts/generated/release-constants.json'
    body = command(['git', 'show', ref + ':' + relative], cwd=source)
    if (source / relative).read_text().strip() != body:
        raise BootstrapError('pixel-release-constants-changed')
    value = json.loads(body)
    if (value.get('schemaVersion') != 1
            or not re.fullmatch(r'\d{4}\.\d+\.\d+(?:-\d+)?', value.get('openclaw', ''))
            or not re.fullmatch(r'>=\d+', value.get('node', ''))):
        raise BootstrapError('invalid-pixel-release')
    validate_package(value['openclawPackage'], 'openclaw', value['openclaw'])
    for key, _, name in PLUGINS:
        validate_package(value['openclawPluginPackages'][key], name, value['openclawPlugins'][name])
    return value


ODS_BUNDLED_REF = '817214d5ec3d8aa583fe50c1dc7561f3c1a16dff'
ODS_BUNDLED_SHA256 = '8fea465b1b42d82da0a286936d0e029b038321fd39793f5a849843ef11aee865'


def acquire_source(*, ref, destination, license_authorized=False,
                   source_url='https://github.com/Osmantic/Pixel.git'):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise BootstrapError('native-macos-owner-required')
    if not re.fullmatch(r'[a-f0-9]{40}', ref):
        raise BootstrapError('exact-pixel-source-ref-required')
    # The public ODS installer passes its pinned local bundle. Retain the
    # canonical remote and a clean local checkout as explicit developer paths.
    if source_url != 'https://github.com/Osmantic/Pixel.git':
        local = Path(source_url)
        if not local.is_absolute() or local.is_symlink():
            raise BootstrapError('official-or-local-pixel-source-required')
        if local.is_file():
            if local.name != 'pixel.bundle' or ref != ODS_BUNDLED_REF:
                raise BootstrapError('invalid-bundled-pixel-source')
            if local.stat().st_size > 64 * 1024 * 1024:
                raise BootstrapError('bundled-pixel-source-too-large')
            if hashlib.sha256(local.read_bytes()).hexdigest() != ODS_BUNDLED_SHA256:
                raise BootstrapError('bundled-pixel-source-digest-mismatch')
        elif not local.is_dir():
            raise BootstrapError('official-or-local-pixel-source-required')
        source_url = str(local.resolve(strict=True))
    destination = Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination):
        raise BootstrapError('new-absolute-source-destination-required')
    destination = destination.parent.resolve(strict=True) / destination.name
    temporary = Path(tempfile.mkdtemp(prefix='.pixel-source-', dir=destination.parent))
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    try:
        checkout = temporary / 'checkout'
        command(['git', '-c', 'credential.interactive=never', 'clone', '--no-local',
                 '--no-checkout', '--', source_url, str(checkout)], cwd=temporary, env=env, timeout=180)
        command(['git', '-c', 'credential.interactive=never', 'fetch', '--no-tags',
                 '--depth=1', 'origin', ref], cwd=checkout, env=env, timeout=180)
        command(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'advice.detachedHead=false',
                 'checkout', '--detach', ref], cwd=checkout, env=env, timeout=60)
        selected_release(checkout, ref)
        # Do not overwrite a checkout created while the clone was in progress.
        if os.path.lexists(destination):
            raise BootstrapError('source-destination-appeared')
        checkout.rename(destination)
        return destination
    finally:
        shutil.rmtree(temporary)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BootstrapError('pinned-package-redirect-refused')


def download(package, destination):
    opener = urllib.request.build_opener(NoRedirect())
    digest, integrity, count = hashlib.sha256(), hashlib.sha512(), 0
    with opener.open(package['url'], timeout=60) as response, destination.open('xb') as output:
        for block in iter(lambda: response.read(1024 * 1024), b''):
            count += len(block)
            if count > 64 * 1024 * 1024:
                raise BootstrapError('pinned-package-too-large')
            digest.update(block)
            integrity.update(block)
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    if (digest.hexdigest() != package['sha256']
            or 'sha512-' + base64.b64encode(integrity.digest()).decode() != package['integrity']):
        raise BootstrapError('pinned-package-checksum-mismatch')


def probe_plugins(node, runtime, plugins, temporary, env, *, entrypoint='node_modules/openclaw/openclaw.mjs'):
    home = temporary / 'probe-home'
    home.mkdir(mode=0o700)
    config = home / 'openclaw.json'
    paths = {item['id']: str(runtime / item['path']) for item in plugins}
    config.write_text(json.dumps({'plugins': {'allow': list(paths),
        'load': {'paths': list(paths.values())},
        'entries': {plugin_id: {'enabled': True} for plugin_id in paths}}}))
    config.chmod(0o600)
    output = command([str(node), str(runtime / entrypoint),
                      'plugins', 'list', '--json'], cwd=temporary,
        env={**env, 'HOME': str(home), 'OPENCLAW_CONFIG_PATH': str(config),
             'OPENCLAW_STATE_DIR': str(home / 'state'), 'OPENCLAW_SKIP_CHANNELS': '1'}, timeout=180)
    result = json.loads(output)
    for plugin_id, path in paths.items():
        matches = [item for item in result.get('plugins', []) if item.get('id') == plugin_id
                   and item.get('status') == 'loaded' and item.get('rootDir') == path]
        if len(matches) != 1:
            raise BootstrapError('native-plugin-load-proof-failed')


def stage(*, source, ref, destination, node, npm):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise BootstrapError('native-macos-owner-required')
    release = selected_release(source, ref)
    destination = Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination):
        raise BootstrapError('new-absolute-bootstrap-destination-required')
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    node, npm = Path(node).resolve(strict=True), Path(npm).resolve(strict=True)
    env = {'HOME': str(Path.home()), 'PATH': str(node.parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
           'TMPDIR': tempfile.gettempdir()}
    # npm's bundled trust store can miss certificates trusted by macOS. Keep
    # strict TLS verification while giving Node the system's PEM CA bundle.
    if MACOS_CA_BUNDLE.is_file():
        env['NODE_EXTRA_CA_CERTS'] = str(MACOS_CA_BUNDLE)
    identity = json.loads(command([str(node), '-p',
        'JSON.stringify({platform:process.platform,arch:process.arch,major:Number(process.versions.node.split(".")[0])})'],
        cwd='/', env=env))
    if (identity.get('platform') != 'darwin' or identity.get('arch') != 'arm64'
            or type(identity.get('major')) is not int
            or identity['major'] < int(release['node'][2:])):
        raise BootstrapError('unsupported-native-node-runtime')
    temporary = Path(tempfile.mkdtemp(prefix='.pixel-bootstrap-', dir=parent))
    try:
        archive = temporary / 'openclaw.tgz'
        download(release['openclawPackage'], archive)
        archives = [str(archive)]
        for key, plugin_id, _ in PLUGINS:
            archive = temporary / (plugin_id + '.tgz')
            download(release['openclawPluginPackages'][key], archive)
            archives.append(str(archive))
        runtime = temporary / 'runtime'
        runtime.mkdir(mode=0o700)
        # Nested installation keeps dependencies within the selected package
        # so the protected bundle can relocate it without a global npm tree.
        command([str(npm), 'install', '--prefix', str(runtime),
                 '--install-strategy=nested', '--omit=dev', '--no-audit', '--no-fund',
                 '--registry=https://registry.npmjs.org', *archives],
                cwd=temporary, env={**env, 'npm_config_cache': str(temporary / 'npm-cache')}, timeout=1800)
        package = runtime / 'node_modules/openclaw'
        metadata = json.loads((package / 'package.json').read_text())
        if metadata.get('name') != 'openclaw' or metadata.get('version') != release['openclaw']:
            raise BootstrapError('installed-openclaw-version-mismatch')
        plugins = []
        for key, plugin_id, name in PLUGINS:
            path = runtime / 'node_modules' / name
            metadata = json.loads((path / 'package.json').read_text())
            manifest = json.loads((path / 'openclaw.plugin.json').read_text())
            if (metadata.get('name') != name or metadata.get('version') != release['openclawPlugins'][name]
                    or manifest.get('id') != plugin_id):
                raise BootstrapError('installed-plugin-identity-mismatch')
            plugins.append({'id': plugin_id, 'path': str(path.relative_to(runtime)),
                            'version': metadata['version'],
                            'sha256': release['openclawPluginPackages'][key]['sha256']})
        version = command([str(node), str(package / 'openclaw.mjs'), '--version'], cwd=temporary, env=env)
        if not re.search(r'(?<![\d.])' + re.escape(release['openclaw']) + r'(?![\d.])', version):
            raise BootstrapError('installed-openclaw-probe-failed')
        pixel_plugins = []
        for directory, plugin_id in PIXEL_PLUGINS:
            path = runtime / 'pixel-plugins' / directory
            shutil.copytree(Path(source) / directory, path, ignore=shutil.ignore_patterns('node_modules'))
            command([str(npm), 'ci', '--prefix', str(path), '--omit=dev', '--no-audit', '--no-fund',
                     '--registry=https://registry.npmjs.org'], cwd=path,
                    env={**env, 'npm_config_cache': str(temporary / 'npm-cache')}, timeout=600)
            metadata = json.loads((path / 'package.json').read_text())
            manifest = json.loads((path / 'openclaw.plugin.json').read_text())
            if metadata.get('version') != release['pixel'] or manifest.get('id') != plugin_id:
                raise BootstrapError('installed-pixel-plugin-identity-mismatch')
            pixel_plugins.append({'id': plugin_id, 'path': str(path.relative_to(runtime)),
                                  'version': release['pixel'], 'sourceRef': ref})
        if selected_release(source, ref) != release:
            raise BootstrapError('pixel-source-changed-during-bootstrap')
        probe_plugins(node, runtime, plugins + pixel_plugins, temporary, env)
        receipt = {'schemaVersion': 1, 'pixelSourceRef': ref, 'pixelVersion': release['pixel'],
                   'openclawVersion': release['openclaw'], 'packageSha256': release['openclawPackage']['sha256'],
                   'nodeIdentity': identity, 'plugins': plugins, 'pixelPlugins': pixel_plugins}
        (runtime / 'ods-bootstrap.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
        (runtime / 'ods-bootstrap.json').chmod(0o600)
        if os.path.lexists(destination):
            raise BootstrapError('bootstrap-destination-appeared')
        os.rename(runtime, destination)
        return destination / 'node_modules/openclaw'
    finally:
        shutil.rmtree(temporary)


def prepare_sandbox(*, source, ref, docker):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise BootstrapError('native-macos-owner-required')
    release = selected_release(source, ref)
    version, uid = release['pixel'], os.getuid()
    if not re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}', version) or uid <= 0:
        raise BootstrapError('invalid-sandbox-owner-or-version')
    docker = str(Path(docker).resolve(strict=True))
    source = Path(source).resolve(strict=True)
    context = source / 'deploy/sandbox'
    tag = 'ods-pixel-native-sandbox:' + version + '-uid-' + str(uid) + '-' + ref[:12]
    # Do not overwrite a pre-existing tag. A matching image must pass the same
    # identity and execution checks as an image built by this invocation.
    images = json.loads(command([docker, 'image', 'ls', '--no-trunc', '--format', 'json',
                                 '--filter', 'reference=' + tag], cwd=source) or 'null')
    if images is None:
        command([docker, 'build', '--platform', 'linux/arm64',
                 '--build-arg', 'PIXEL_SANDBOX_UID=' + str(uid),
                 '--label', 'org.osmantic.pixel.sandbox-version=' + version,
                 '--label', 'org.osmantic.pixel.source-ref=' + ref,
                 '-t', tag, str(context)], cwd=source, timeout=1800)
    if selected_release(source, ref) != release:
        raise BootstrapError('sandbox-build-source-changed')
    def inspect():
        result = json.loads(command([docker, 'image', 'inspect', tag], cwd=source))
        if type(result) is not list or len(result) != 1:
            raise BootstrapError('invalid-native-sandbox-image')
        value = result[0]
        labels = value.get('Config', {}).get('Labels') or {}
        if (not re.fullmatch(r'sha256:[a-f0-9]{64}', value.get('Id', ''))
                or value.get('Os') != 'linux' or value.get('Architecture') != 'arm64'
                or value.get('Config', {}).get('User') != 'sandbox'
                or labels.get('org.osmantic.pixel.sandbox-version') != version
                or labels.get('org.osmantic.pixel.sandbox-uid') != str(uid)
                or labels.get('org.osmantic.pixel.source-ref') != ref):
            raise BootstrapError('invalid-native-sandbox-image')
        return value['Id']
    image_id = inspect()
    name = 'ods-pixel-sandbox-proof-' + uuid.uuid4().hex
    program = ('import json,os,pathlib,shutil; '
               'p=pathlib.Path("/workspace/ods-proof.txt"); p.write_text("ods-sandbox-proof"); '
               'print(json.dumps({"uid":os.getuid(),"readback":p.read_text(),'
               '"tools":all(shutil.which(x) for x in '
               '["bash","curl","git","jq","python3","rg","rsync"])}))')
    # Colima does not share macOS's per-user TMPDIR (/var/folders) with its VM.
    # The selected source is staged beside the installed ODS data, a filesystem
    # already shared with Docker, and the proof must not dirty the Git checkout.
    with tempfile.TemporaryDirectory(prefix='ods-sandbox-workspace-', dir=source.parent) as workspace:
        try:
            result = json.loads(command([docker, 'run', '--rm', '--name', name,
                '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges:true', '--pids-limit', '128',
                '--memory', '512m', '--cpus', '1', '--tmpfs', '/tmp:rw,nosuid,nodev,size=16m',
                '--mount', 'type=bind,source=' + workspace + ',target=/workspace',
                '--entrypoint', '/usr/bin/python3', image_id, '-c', program], cwd=source, timeout=60))
            host_readback = (Path(workspace) / 'ods-proof.txt').read_text()
        finally:
            # A timed-out Docker client does not necessarily stop its container.
            subprocess.run([docker, 'rm', '-f', name], cwd=source, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
    if (result != {'uid': uid, 'readback': 'ods-sandbox-proof', 'tools': True}
            or host_readback != 'ods-sandbox-proof' or inspect() != image_id):
        raise BootstrapError('native-sandbox-execution-proof-failed')
    return {'image': tag, 'imageId': image_id, 'ownerUid': uid, 'pixelSourceRef': ref}


def sandbox_main():
    parser = argparse.ArgumentParser(description='Prepare and verify the native Pixel Docker sandbox')
    for name in ('source', 'source-ref', 'docker'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--license-authorized', action='store_true')
    args = parser.parse_args(sys.argv[2:])
    try:
        receipt = prepare_sandbox(source=args.source, ref=args.source_ref, docker=args.docker)
    except BootstrapError as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('error: native-pixel-sandbox-failed', file=sys.stderr)
        return 1
    print(json.dumps(receipt))
    return 0


def main():
    if sys.argv[1:2] == ['sandbox']:
        return sandbox_main()
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'source-ref', 'destination', 'node', 'npm'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--license-authorized', action='store_true',
                        help='Confirm the caller already obtained applicable Pixel authorization')
    args = parser.parse_args()
    try:
        path = stage(source=args.source, ref=args.source_ref, destination=args.destination,
                     node=args.node, npm=args.npm)
    except BootstrapError as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('error: native-pixel-bootstrap-failed; existing installation was not changed', file=sys.stderr)
        return 1
    print(json.dumps({'runtime': str(path), 'status': 'staged'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
