// Exercise the actual pinned formatter after the normal source-bound repair.
// No model, shell command execution, or running installation is involved.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, readFileSync, writeFileSync, mkdirSync, readdirSync, symlinkSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const installed = process.env.OPENCLAW_PACKAGE;
const manifestPath = fileURLToPath(new URL('../host/openclaw-command-attempt-warning.json', import.meta.url));
const helper = fileURLToPath(new URL('../host/openclaw_tool_recovery.py', import.meta.url));
const manifest = JSON.parse(readFileSync(manifestPath));
const moduleName = 'payloads-CC0zlj7W.js';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
let root, baseline, patched, repair;
after(() => { if (root) rmSync(root, {recursive: true, force: true}); });

if (installed) {
  root = mkdtempSync(join(tmpdir(), 'ods-command-attempt-'));
  mkdirSync(join(root, 'dist'));
  writeFileSync(join(root, 'package.json'), readFileSync(join(installed, 'package.json')));
  writeFileSync(join(root, 'openclaw.mjs'), '');
  symlinkSync(join(installed, 'node_modules'), join(root, 'node_modules'));
  for (const name of readdirSync(join(installed, 'dist'))) {
    if (name !== moduleName) symlinkSync(join(installed, 'dist', name), join(root, 'dist', name));
  }
  let source = readFileSync(join(installed, 'dist', moduleName), 'utf8');
  if (hash(source) === manifest.patchedSha256) {
    for (const [old, replacement] of [...manifest.replacements].reverse()) {
      assert.equal(source.split(replacement).length, 2);
      source = source.replace(replacement, old);
    }
  }
  assert.equal(hash(source), manifest.sourceSha256, 'require exact reviewed public runtime bytes');
  writeFileSync(join(root, 'dist', moduleName), source);
  baseline = (await import(pathToFileURL(join(root, 'dist', moduleName)) + '?original')).t;
  repair = (...extra) => spawnSync('python3', [helper, '--openclaw-bin', join(root, 'openclaw.mjs'),
    '--state-dir', join(root, 'state'), '--command-attempt-warning', ...extra], {encoding: 'utf8'});
  const result = repair();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(hash(readFileSync(join(root, 'dist', moduleName))), manifest.patchedSha256);
  patched = process.env.ODS_COMMAND_ATTEMPT_WARNING_RED === '1' ? baseline :
    (await import(pathToFileURL(join(root, 'dist', moduleName)) + '?patched')).t;
}

const run = (title, body) => test(title, {skip: !installed && 'requires pinned OPENCLAW_PACKAGE'}, body);
const failure = {toolName: 'exec', meta: 'run unzip integrity check',
  error: 'sh: 1: Syntax error: "(" unexpected', mutatingAction: true, actionFingerprint: 'unchanged-evidence'};
const params = extra => ({assistantTexts: ['Site publicado e ZIP verificado.'], toolMetas: [],
  toolResultFormat: 'markdown', verboseLevel: 'off', lastToolError: {...failure}, ...extra});

run('pinned baseline reproduces ambiguous footer; repair identifies the failed attempt without claiming recovery', () => {
  const input = params();
  const before = baseline(input), after = patched(input);
  assert.match(before.at(-1).text, /`run unzip integrity check` failed$/);
  assert.match(after.at(-1).text, /^⚠️ Earlier command attempt failed: /);
  assert.equal(after.at(-1).isError, true);
  assert.deepEqual(after.slice(0, -1), before.slice(0, -1));
  assert.equal(input.lastToolError.actionFingerprint, 'unchanged-evidence');
  assert.doesNotMatch(after.at(-1).text, /recovered|resolved|succeeded/);
});

for (const toolName of ['exec', 'bash']) {
  run(`${toolName}: unresolved failure remains visible and verbose details remain intact`, () => {
    const output = patched(params({verboseLevel: 'full', lastToolError: {...failure, toolName}}));
    assert.equal(output.at(-1).isError, true);
    assert.match(output.at(-1).text, /Earlier command attempt failed:/);
    assert.ok(output.at(-1).text.endsWith(': ' + failure.error));
  });
}
for (const [label, extra] of [
  ['no visible final answer', {assistantTexts: []}],
  ['silent reply', {assistantTexts: ['NO_REPLY']}],
  ['write failure', {lastToolError: {...failure, toolName: 'write'}}],
  ['deferred tool wrapper', {lastToolError: {...failure, toolName: 'tool_call'}}],
  ['explicit owner approval prompt', {didSendDeterministicApprovalPrompt: true}],
  ['existing acknowledgement', {assistantTexts: ['The command failed. Please review the error.']}],
  ['configured suppression', {config: {messages: {suppressToolErrors: true}}}],
  ['no tool failure', {lastToolError: undefined}],
]) {
  run(`${label}: existing payload and warning policy are unchanged`, () => {
    assert.deepEqual(patched(params(extra)), baseline(params(extra)));
  });
}
run('a model-written warning is never stripped or rewritten', () => {
  const text = '⚠️ 🛠️ `user text` failed';
  assert.deepEqual(patched(params({assistantTexts: [text], lastToolError: undefined})),
    baseline(params({assistantTexts: [text], lastToolError: undefined})));
});
run('reviewed patch reapplies, restores, and rejects unknown executable bytes without rewriting them', () => {
  let result = repair();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).status, 'unchanged');
  result = repair('--restore');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(hash(readFileSync(join(root, 'dist', moduleName))), manifest.sourceSha256);
  assert.equal(repair().status, 0);
  const alien = 'export const changed = true;\n';
  writeFileSync(join(root, 'dist', moduleName), alien);
  result = repair();
  assert.notEqual(result.status, 0);
  assert.equal(readFileSync(join(root, 'dist', moduleName), 'utf8'), alien);
  assert.notEqual(repair('--restore').status, 0);
  assert.equal(readFileSync(join(root, 'dist', moduleName), 'utf8'), alien);
});
