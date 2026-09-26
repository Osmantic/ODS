import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  ODS_COMPACT_CONVERSATION_CONTRACT,
  ODS_CONVERSATION_CONTRACT,
  ODS_SANDBOX_RUNTIME_CONTRACT,
  conversationContractForExecution,
  promptContractForAgent,
} from '../plugin/prompt-contract.mjs';

const odsFile = name => fs.readFileSync(new URL(`../../../../${name}`, import.meta.url), 'utf8').replace(/\r\n/g, '\n');

// Every package the sandbox Dockerfile installs, mapped to the exact words that
// tell Pixel about it. Adding or removing a package fails this test until the
// contract says so. null marks a package deliberately left unadvertised.
const PACKAGE_CLAIMS = {
  bash: 'bash',
  'ca-certificates': null, // certificate store only; the sandbox has no network
  curl: null, // useless without network, which the contract states instead
  git: 'git',
  jq: 'jq',
  python3: 'Python 3.11',
  ripgrep: 'rg',
  rsync: 'rsync',
  'poppler-utils': 'pdftotext, pdfinfo, pdftoppm and the other poppler-utils',
  'tesseract-ocr': 'tesseract OCR',
  'tesseract-ocr-eng': 'with English language data',
  'python3-docx': 'python-docx (import docx)',
  'python3-pil': 'Pillow (import PIL)',
  catdoc: 'catdoc/xls2csv',
  unzip: 'unzip',
  file: 'file',
};
// The Debian release in the pinned base image and the python3 it ships.
const DEBIAN_RELEASES = {bookworm: {phrase: 'Debian 12 (bookworm) with Python 3.11', python: 'Python 3.11'}};
// Tools the contract says are absent, and the Debian packages that would add them.
const ABSENT = {pip: ['python3-pip', 'python3-venv'], pytest: ['python3-pytest'], 'Node.js': ['nodejs'], npm: ['npm']};

function dockerInstructions(text) {
  return text.replace(/\\\n/g, ' ').split('\n').map(line => line.trim())
    .filter(line => line && !line.startsWith('#'));
}

function installedPackages(instructions) {
  return instructions.filter(line => line.startsWith('RUN '))
    .flatMap(line => line.slice(4).split('&&').map(command => command.trim().split(/\s+/)))
    .filter(words => words[0] === 'apt-get' && words[1] === 'install')
    .flatMap(words => words.slice(2).filter(word => !word.startsWith('-')));
}

test('the sandbox contract states exactly what the sandbox Dockerfile installs', () => {
  const instructions = dockerInstructions(odsFile('vendor/pixel/deploy/sandbox/Dockerfile'));
  // Files or tools added any other way would be invisible to this contract.
  assert.deepEqual([...new Set(instructions.map(line => line.split(/\s/)[0]))].filter(kind =>
    !['FROM', 'ENV', 'RUN', 'ARG', 'LABEL', 'USER', 'WORKDIR', 'CMD'].includes(kind)), []);
  for (const line of instructions.filter(line => line.startsWith('RUN ')))
    assert.doesNotMatch(line, /\b(?:pip3?|npm|npx|yarn|gem|cargo|go)\s+install\b|\b(?:curl|wget)\b[^&]*\|/);
  const from = instructions.filter(line => line.startsWith('FROM '));
  assert.equal(from.length, 1);
  const base = /^FROM debian:([a-z]+)-slim@sha256:[0-9a-f]{64}$/.exec(from[0]);
  assert.ok(base, `unrecognized sandbox base image: ${from[0]}`);
  const release = DEBIAN_RELEASES[base[1]];
  assert.ok(release, `state the python3 that Debian ${base[1]} ships`);
  assert.ok(ODS_SANDBOX_RUNTIME_CONTRACT.startsWith(`The exec sandbox is ${release.phrase}. `));
  assert.equal(PACKAGE_CLAIMS.python3, release.python);

  const packages = installedPackages(instructions);
  assert.equal(new Set(packages).size, packages.length);
  assert.deepEqual([...packages].sort(), Object.keys(PACKAGE_CLAIMS).sort());
  for (const [name, phrase] of Object.entries(PACKAGE_CLAIMS))
    if (phrase !== null) assert.ok(ODS_SANDBOX_RUNTIME_CONTRACT.includes(phrase), `${name}: ${phrase}`);
  assert.match(ODS_SANDBOX_RUNTIME_CONTRACT,
    /Python packages beyond the standard library: Pillow \(import PIL\) and python-docx \(import docx\)\./);
  assert.deepEqual(packages.filter(name => name.startsWith('python3-')).sort(), ['python3-docx', 'python3-pil']);
  for (const [tool, providers] of Object.entries(ABSENT)) {
    assert.ok(ODS_SANDBOX_RUNTIME_CONTRACT.includes(tool), tool);
    for (const provider of providers) assert.ok(!packages.includes(provider), provider);
  }
  assert.match(ODS_SANDBOX_RUNTIME_CONTRACT, /pip, pytest, Node\.js and npm are not installed/);
});

test('the sandbox contract states the network and filesystem the rendered sandbox has', () => {
  const renderConfig = odsFile('vendor/pixel/scripts/render-config.mjs');
  const blocks = renderConfig.match(/sandbox: \{\s*mode: "all"[\s\S]*?\n\s*\},\n\s*\},/g);
  assert.equal(blocks?.length, 1);
  const [sandbox] = blocks;
  assert.match(sandbox, /workspaceAccess: "rw"/);
  assert.match(sandbox, /workdir: "\/workspace"/);
  assert.match(sandbox, /network: "none"/);
  assert.match(sandbox, /readOnlyRoot: true/);
  assert.match(sandbox, /tmpfs: \["\/tmp:rw,[a-z,]*size=\d+m"/);
  assert.match(ODS_SANDBOX_RUNTIME_CONTRACT, /the sandbox has no network, so nothing can be downloaded or installed/);
  assert.match(ODS_SANDBOX_RUNTIME_CONTRACT, /Write files in the workspace, which is writable and persists; \/tmp is small temporary scratch space; the root filesystem, including the home directory, is read-only\.$/);
});

test('Linux, WSL and native macOS installs build and run that same sandbox', () => {
  // WSL runs the Linux installer; native Windows has no Pixel host.
  assert.match(odsFile('vendor/pixel/scripts/bootstrap.sh'), /-t "\$candidate_sandbox_ref" "\$ROOT\/deploy\/sandbox"\n/);
  assert.match(odsFile('installers/macos/lib/pixel-native-bootstrap.py'), /context = source \/ 'deploy\/sandbox'\n/);
  assert.match(odsFile('installers/macos/lib/pixel-native-config.py'), /checkout \/ 'scripts\/render-config\.mjs'/);
  assert.match(odsFile('installers/windows/lib/service-plan.ps1'), /Pixel requires the ODS Linux installer in Ubuntu 24\.04 WSL2/);
});

test('sandboxed exec receives the static sandbox facts in its system contract; native exec does not', () => {
  const prompts = ['hello', 'write a python script that renames all photos in a folder by date taken, and test it',
    'Build a React website with Vite and publish its preview.'];
  for (const options of [{configuredContextWindow: 65536}, {configuredContextWindow: 16384}]) {
    const core = options.configuredContextWindow < 32768 ? ODS_COMPACT_CONVERSATION_CONTRACT : ODS_CONVERSATION_CONTRACT;
    for (const prompt of prompts) {
      const sandbox = promptContractForAgent({agentId: 'pixel'}, 'pixel', {prompt}, {...options, executionHost: 'sandbox'}).appendSystemContext;
      // Same bytes for every message, ahead of any per-message section.
      assert.ok(sandbox.startsWith(`${core} ${ODS_SANDBOX_RUNTIME_CONTRACT}`));
      assert.equal(sandbox.split(ODS_SANDBOX_RUNTIME_CONTRACT).length, 2);
      const native = promptContractForAgent({agentId: 'pixel'}, 'pixel', {prompt}, {...options, executionHost: 'gateway'}).appendSystemContext;
      assert.ok(!native.includes(ODS_SANDBOX_RUNTIME_CONTRACT));
      assert.doesNotMatch(native, /Python 3\.11|Pillow|pip, pytest/);
    }
  }
  assert.equal(conversationContractForExecution(ODS_CONVERSATION_CONTRACT, 'sandbox'),
    `${ODS_CONVERSATION_CONTRACT} ${ODS_SANDBOX_RUNTIME_CONTRACT}`);
  // Unproven execution modes make no sandbox claim.
  for (const value of [undefined, null, 'Sandbox', {host: 'sandbox'}])
    assert.equal(conversationContractForExecution(ODS_CONVERSATION_CONTRACT, value), ODS_CONVERSATION_CONTRACT);
});
