// No model inference. Copies the installed pinned SDK into an owned fixture;
// exact repair recipes are applied only to that copy.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
const installed = process.env.OPENCLAW_PACKAGE;
assert.ok(installed && path.isAbsolute(installed));
assert.equal(JSON.parse(fs.readFileSync(path.join(installed, 'package.json'))).version, '2026.6.33');
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-file-receipts-'));
const runtime = path.join(temporary, 'runtime');
fs.cpSync(installed, runtime, { recursive: true });
const recipes = [
  ['openclaw-compaction-resume.json', 'sessions-CZbwb3_c.js'],
  ['openclaw-read-range.json', 'openclaw-tools-iHHy99PD.js'],
  ['openclaw-file-operations.json', 'agent-tools-D1DOpg6D.js'],
  ['openclaw-file-identity.json', 'sandbox-Y3MbG9Od.js'],
];
function apply(source, pairs, reverse = false) {
  for (const pair of reverse ? [...pairs].reverse() : pairs) {
    const [before, after] = reverse ? [...pair].reverse() : pair;
    assert.equal(source.split(before).length, 2, `exact source occurrence: ${before.slice(0, 100)}`);
    source = source.replace(before, () => after);
  }
  return source;
}
const installedHashes = {};
for (const [name, module] of recipes) {
  const manifest = JSON.parse(fs.readFileSync(new URL(`../host/${name}`, import.meta.url)));
  const file = path.join(runtime, 'dist', module);
  let source = fs.readFileSync(file, 'utf8');
  installedHashes[module] = digest(source);
  if (digest(source) !== manifest.sourceSha256) {
    const previous = digest(source) === manifest.patchedSha256 ? manifest.replacements : manifest.previousReplacements?.[digest(source)];
    assert.ok(previous, `installed ${module} must match reviewed original or prior exact repair`);
    source = apply(source, previous, true);
  }
  assert.equal(digest(source), manifest.sourceSha256);
  const repaired = apply(source, manifest.replacements);
  assert.equal(digest(repaired), manifest.patchedSha256);
  fs.writeFileSync(file, repaired);
}
process.env.OPENCLAW_STATE_DIR = path.join(temporary, 'state');
process.env.OPENCLAW_CONFIG_PATH = path.join(temporary, 'config.json');
fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH, '{}');
const require = createRequire(path.join(runtime, 'package.json'));
const { createOpenClawCodingTools, resolveSandboxContext } = await import(pathToFileURL(require.resolve('openclaw/plugin-sdk/agent-harness')));
const { odsCreateFileReceiptContext } = await import(pathToFileURL(path.join(runtime, 'dist/sessions-CZbwb3_c.js')));
const image = process.env.OPENCLAW_TEST_SANDBOX_IMAGE;

for (const mode of image ? ['native', 'sandbox'] : ['native']) test(`actual SDK ${mode} file receipts`, async () => {
  const workspace = path.join(temporary, mode); fs.mkdirSync(workspace);
  fs.mkdirSync(path.join(workspace, '.openclaw', 'sandbox-skills'), { recursive: true });
  const sessionKey = `agent:pixel:receipt-${mode}`;
  const settings = mode === 'sandbox' ? { mode: 'all', scope: 'session', workspaceAccess: 'rw', docker: {
    image, containerPrefix: 'ods-file-receipts-', network: 'none', readOnlyRoot: true,
    user: `${process.getuid()}:${process.getgid()}`, capDrop: ['ALL'], memory: '256m', cpus: 1,
  } } : { mode: 'off' };
  const config = { agents: { defaults: { workspace, sandbox: settings }, list: [{ id: 'pixel', workspace }] },
    plugins: { enabled: false }, tools: { profile: 'coding', fs: { workspaceOnly: true } } };
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH, JSON.stringify(config));
  const sandbox = mode === 'sandbox' ? await resolveSandboxContext({ config, sessionKey, workspaceDir: workspace }) : undefined;
  const receipt = odsCreateFileReceiptContext({ agentId: 'pixel', sessionKey, workspace, enabled: true });
  try {
    const tools = createOpenClawCodingTools({ config, agentId: 'pixel', sessionKey, workspaceDir: workspace, sandbox, odsFileReceiptContext: receipt });
    const tool = name => { const value = tools.find(t => t.name === name); assert.ok(value, name); return value; };
    const history = [];
    const execute = async (name, id, args) => {
      const result = await tool(name).execute(id, args);
      history.push({ role: 'toolResult', toolName: name, toolCallId: id, ...result });
      receipt.updateVisible(history);
      return result;
    };
    const original = '\ufefffirst\r\nsecond🙂\r\n';
    const created = await execute('write', 'write-1', { path: 'a.txt', content: original });
    assert.equal(created.details.fileReceipt.bytes, Buffer.byteLength(original));
    assert.deepEqual(fs.readFileSync(path.join(workspace, 'a.txt')), Buffer.from(original));
    const read = await execute('read', 'read-1', { path: 'a.txt' });
    assert.equal(read.details.fileReceipt.version, digest(Buffer.from(original)));
    assert.match(read.content[0].text, /2\|second🙂/);
    const edited = await execute('edit', 'edit-1', { path: 'a.txt', edits: [{ oldText: 'second🙂', newText: 'second λ' }] });
    assert.equal(edited.details.fileReceipt.version, digest(fs.readFileSync(path.join(workspace, 'a.txt'))));
    assert.deepEqual(fs.readFileSync(path.join(workspace, 'a.txt')), Buffer.from(original.replace('second🙂', 'second λ')));
    fs.writeFileSync(path.join(workspace, 'a.txt'), 'external owner change');
    await assert.rejects(execute('write', 'write-stale', { path: 'a.txt', content: 'wrong' }), /File changed|no longer visible/);
    assert.equal(fs.readFileSync(path.join(workspace, 'a.txt'), 'utf8'), 'external owner change');
    await execute('read', 'read-fresh', { path: 'a.txt' });
    receipt.updateVisible([]);
    await assert.rejects(execute('edit', 'edit-invisible', { path: 'a.txt', edits: [{ oldText: 'external', newText: 'wrong' }] }), /File changed|no longer visible/);
    const restored = odsCreateFileReceiptContext({ agentId: 'pixel', sessionKey, workspace, enabled: true });
    restored.hydrateTrustedHistory(history); restored.updateVisible(history);
    assert.equal(restored.visible('a.txt').version, digest(Buffer.from('external owner change')));
    await assert.rejects(tool('read').execute('escape', { path: '../escape.txt' }), /workspace|outside|not found|does not exist|ENOENT/i);
    await assert.rejects(tool('write').execute('escape-write', {path:'../escape.txt',content:'must not write'}), /workspace|outside|escape|relative/i);
    assert.equal(fs.existsSync(path.join(temporary,'escape.txt')),false);
    for(const dir of ['left','right']) {fs.mkdirSync(path.join(workspace,dir));fs.writeFileSync(path.join(workspace,dir,'same.txt'),'same bytes');}
    fs.symlinkSync('left',path.join(workspace,'alias'));
    await execute('read','alias-read',{path:'alias/same.txt'});
    fs.unlinkSync(path.join(workspace,'alias'));fs.symlinkSync('right',path.join(workspace,'alias'));
    await assert.rejects(execute('edit','alias-edit',{path:'alias/same.txt',edits:[{oldText:'same',newText:'changed'}]}),/File changed|no longer visible|symlink|alias/i);
    for(const dir of ['left','right'])assert.equal(fs.readFileSync(path.join(workspace,dir,'same.txt'),'utf8'),'same bytes');
    console.log(JSON.stringify({ kind: 'actual-sdk-file-receipt', mode, status: 'passed', nativeBytesVerified: true, staleRejected: true, contextLossRejected: true }));
  } finally {
    if (sandbox?.containerName) {
      assert.match(sandbox.containerName, /^ods-file-receipts-/);
      const result = spawnSync('docker', ['rm', '-f', sandbox.containerName], { encoding: 'utf8' });
      assert.equal(result.status, 0, result.stderr);
    }
  }
});
test.after(() => {
  for (const [module, hash] of Object.entries(installedHashes)) assert.equal(digest(fs.readFileSync(path.join(installed, 'dist', module))), hash, 'live SDK bytes unchanged');
  fs.rmSync(temporary, { recursive: true, force: true });
});
