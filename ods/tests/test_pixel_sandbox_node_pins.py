"""Static pins for the Node.js runtime in Pixel's agent sandbox image.

The image itself is built and run by .github/workflows/pixel-sandbox-image.yml
(tests/test_pixel_sandbox_image.py). Pixel's own check-release-contract.mjs is
not run by ODS CI, so these checks are the pin gate on every pull request.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIXEL = ROOT / 'vendor/pixel'
MANIFEST = json.loads((PIXEL / 'RELEASE-MANIFEST.json').read_text(encoding='utf-8'))
NODE = MANIFEST['sandboxNodeRuntime']
VERSION = NODE['version']
# Debian architecture -> nodejs.org architecture.
ARCHITECTURES = {'amd64': 'x64', 'arm64': 'arm64'}


def read(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


def dockerfile():
    return read('vendor/pixel/deploy/sandbox/Dockerfile')


def sandbox_runtime_contract():
    source = read('extensions/services/pixel-agent/plugin/prompt-contract.mjs')
    match = re.search(r'export const ODS_SANDBOX_RUNTIME_CONTRACT =\n  "([^"\n]+)";', source)
    assert match, 'ODS_SANDBOX_RUNTIME_CONTRACT must stay one static string literal'
    return match.group(1)


def test_manifest_pins_official_linux_builds_for_both_architectures():
    assert re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', VERSION)
    assert set(NODE['artifacts']) == set(ARCHITECTURES)
    for arch, node_arch in ARCHITECTURES.items():
        artifact = NODE['artifacts'][arch]
        assert set(artifact) == {'url', 'sha256'}
        assert artifact['url'] == (
            f'https://nodejs.org/dist/v{VERSION}/node-v{VERSION}-linux-{node_arch}.tar.xz')
        assert re.fullmatch(r'[0-9a-f]{64}', artifact['sha256'])
    assert NODE['artifacts']['amd64']['sha256'] != NODE['artifacts']['arm64']['sha256']


def test_dockerfile_installs_exactly_the_pinned_artifacts():
    text = dockerfile()
    lines = text.splitlines()
    rows = [line for line in lines if re.match(r'^      [a-z0-9]+\) node_url=', line)]
    assert rows == [
        f'      {arch}) node_url={NODE["artifacts"][arch]["url"]} '
        f'node_sha256={NODE["artifacts"][arch]["sha256"]} ;; \\'
        for arch in ARCHITECTURES
    ]
    for artifact in NODE['artifacts'].values():
        assert text.count(artifact['url']) == 1
        assert text.count(artifact['sha256']) == 1
    # Any other architecture stops the build instead of guessing.
    assert '      *) echo "Pixel sandbox: no pinned Node.js build for $arch" >&2; exit 1 ;; \\' in lines
    assert 'arch="$(dpkg --print-architecture)"; \\' in text
    assert "curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o /tmp/node.tar.xz \"$node_url\"" in text
    assert 'echo "$node_sha256  /tmp/node.tar.xz" | sha256sum -c - >/dev/null' in text
    assert 'did not match its pinned SHA-256; nothing was installed' in text
    assert f'test "$(/usr/local/bin/node --version)" = "v{VERSION}"; \\' in text
    assert f'LABEL org.osmantic.pixel.sandbox-node="{VERSION}"' in lines


def test_only_the_node_binary_and_its_license_are_installed():
    text = dockerfile()
    extract = [line for line in text.splitlines() if 'tar -x' in line]
    assert extract == [
        '    tar -xJf /tmp/node.tar.xz -C /tmp/node --no-same-owner'
        ' "$node_dir/bin/node" "$node_dir/LICENSE"; \\'
    ]
    assert 'install -m 0755 "/tmp/node/$node_dir/bin/node" /usr/local/bin/node; \\' in text
    assert 'install -D -m 0644 "/tmp/node/$node_dir/LICENSE" /usr/local/share/doc/nodejs/LICENSE; \\' in text
    assert 'node_modules' not in text
    # The build fails if a package manager is reachable.
    assert '    for tool in npm npx corepack; do \\' in text
    assert re.search(r'^\s+if command -v "\$tool" >/dev/null 2>&1; then .*exit 1; fi; \\$', text, re.M)
    assert 'rm -rf /tmp/node /tmp/node.tar.xz; \\' in text
    assert 'NODE_OPTIONS' not in text


def test_dockerfile_builds_with_legacy_docker_builders():
    text = dockerfile()
    for buildkit_only in ('ADD --checksum', 'COPY --chmod', 'RUN --network', 'RUN --mount',
                          'TARGETARCH', 'TARGETPLATFORM', 'BUILDPLATFORM'):
        assert buildkit_only not in text, buildkit_only
    assert not re.search(r'^#\s*syntax\s*=', text, re.M)
    assert text.splitlines()[0] == f'FROM {MANIFEST["baseImage"]}'
    # tests/static.sh greps these lines byte for byte.
    assert 'ARG PIXEL_SANDBOX_UID=1000' in text.splitlines()
    assert 'LABEL org.osmantic.pixel.sandbox-uid="$PIXEL_SANDBOX_UID"' in text.splitlines()
    # tar -J needs xz; debian:bookworm-slim does not include it.
    apt = re.search(r'apt-get install -y --no-install-recommends \\\n(.*?)&& rm -rf', text, re.S)
    assert apt and 'xz-utils' in apt.group(1).split()


def test_generated_release_files_carry_the_sandbox_node_pin():
    constants = json.loads(read('vendor/pixel/scripts/generated/release-constants.json'))
    assert constants['sandboxNodeRuntime'] == NODE
    env = read('vendor/pixel/scripts/generated/release.env').splitlines()
    assert f"PIXEL_GENERATED_SANDBOX_NODE_VERSION='{VERSION}'" in env
    notices = read('vendor/pixel/THIRD_PARTY_NOTICES.md')
    assert f'official Node.js {VERSION} `node` binary' in notices
    assert f'https://github.com/nodejs/node/blob/v{VERSION}/LICENSE' in notices


def test_linux_bootstrap_proves_the_runtime_before_any_live_change():
    bootstrap = read('vendor/pixel/scripts/bootstrap.sh')
    validate = bootstrap.index('candidate_sandbox_id=$(pixel_validate_sandbox_image "$candidate_sandbox_ref"')
    prove = bootstrap.index('pixel_prove_sandbox_runtime "$candidate_sandbox_id" '
                            '"$PIXEL_GENERATED_SANDBOX_NODE_VERSION"')
    # Both new and reused candidates reach the proof; bootstrap mutates no live state.
    assert bootstrap.index('candidate_sandbox_ref=$(pixel_sandbox_candidate_tag') < validate < prove
    common = read('vendor/pixel/scripts/lib/common.sh')
    body = common[common.index('pixel_prove_sandbox_runtime() {'):]
    body = body[:body.index('\n}\n')]
    for flag in ('--network none', '--read-only', '--cap-drop ALL', '--user sandbox',
                 'no-new-privileges:true', '--entrypoint /usr/local/bin/node'):
        assert flag in body, flag
    assert '["npm", "npx", "corepack"]' in body
    assert '[[ "$observed" == "v$expected_version $expected_arch none" ]]' in body


def test_macos_preparation_proves_node_and_no_package_manager():
    source = read('installers/macos/lib/pixel-native-bootstrap.py')
    assert "release.get('sandboxNodeRuntime')" in source
    assert '"-p","JSON.stringify([process.version,process.arch])"' in source
    assert '["npm","npx","corepack"]' in source
    assert "'node': ['v' + node_version, 'arm64'], 'packageManagers': []" in source


def test_prompt_claims_node_exactly_as_the_image_provides_it():
    contract = sandbox_runtime_contract()
    installed = 'install -m 0755 "/tmp/node/$node_dir/bin/node" /usr/local/bin/node' in dockerfile()
    assert installed
    assert 'git, rg, jq and node (no npm, npx or packages), but no network.' in contract
    assert 'node --check' in contract and 'node --test' in contract
    assert 'there is no DOM or canvas' in contract
    assert 'no node' not in contract
    # Full Access drops the sandbox sentence instead of making a host claim.
    source = read('extensions/services/pixel-agent/plugin/prompt-contract.mjs')
    assert ".replace(` ${ODS_SANDBOX_RUNTIME_CONTRACT}`, '')" in source
