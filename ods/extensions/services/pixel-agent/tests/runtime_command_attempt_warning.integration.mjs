// Exercise the actual pinned formatter after the normal source-bound repair.
// The command-status cases use the real SDK exec tool in a private workspace;
// no model, owner configuration, or running installation is involved.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, readFileSync, writeFileSync, mkdirSync, readdirSync, symlinkSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createExecCancellationControl} from '../plugin/tool-loop-guard.mjs';

const installed = process.env.OPENCLAW_PACKAGE;
const manifestPath = fileURLToPath(new URL('../host/openclaw-command-attempt-warning.json', import.meta.url));
const helper = fileURLToPath(new URL('../host/openclaw_tool_recovery.py', import.meta.url));
const manifest = JSON.parse(readFileSync(manifestPath));
const moduleName = 'payloads-CC0zlj7W.js';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const converter = 'import sys\nvalue=float(sys.argv[1])\nif value<0:\n print("Error: distance must be non-negative",file=sys.stderr)\n sys.exit(1)\nprint(value/1.609344)\n';
let root, baseline, patched, repair, oldSelection, newSelection, mainPayload, sdk, config, workspace, keepAlive;
after(() => { clearInterval(keepAlive); if (root) rmSync(root, {recursive: true, force: true}); });

function reviewedOriginal(source, recipe) {
  const current = hash(source);
  if (current === recipe.sourceSha256) return source;
  const replacements = current === recipe.patchedSha256 ? recipe.replacements : recipe.previousReplacements?.[current];
  assert.ok(replacements, 'require a reviewed runtime predecessor, never reverse unknown bytes');
  for (const [before, after] of [...replacements].reverse()) {
    assert.equal(source.split(after).length, 2);
    source = source.replace(after, before);
  }
  assert.equal(hash(source), recipe.sourceSha256);
  return source;
}
function applyRecipe(source, replacements) {
  for (const [before, after] of replacements) {
    assert.equal(source.split(before).length, 2);
    source = source.replace(before, after);
  }
  return source;
}

if (installed) {
  root = mkdtempSync(join(tmpdir(), 'ods-command-attempt-'));
  process.env.OPENCLAW_STATE_DIR = join(root, 'private-state');
  process.env.OPENCLAW_CONFIG_PATH = join(root, 'private-config.json');
  workspace = join(root, 'workspace');
  mkdirSync(workspace);
  writeFileSync(join(workspace, 'main.py'), converter);
  config = {agents: {defaults: {workspace, sandbox: {mode: 'off'}}, list: [{id: 'pixel', workspace}]},
    plugins: {enabled: false}, tools: {profile: 'coding', exec: {host: 'gateway', security: 'full', ask: 'off'}}};
  writeFileSync(process.env.OPENCLAW_CONFIG_PATH, JSON.stringify(config));
  keepAlive = setInterval(() => {}, 1000);
  mkdirSync(join(root, 'dist'));
  writeFileSync(join(root, 'package.json'), readFileSync(join(installed, 'package.json')));
  writeFileSync(join(root, 'openclaw.mjs'), '');
  symlinkSync(join(installed, 'node_modules'), join(root, 'node_modules'));
  for (const name of readdirSync(join(installed, 'dist'))) {
    if (![moduleName, 'selection-BEwSQKM-.js'].includes(name)) symlinkSync(join(installed, 'dist', name), join(root, 'dist', name));
  }
  let source = readFileSync(join(installed, 'dist', moduleName), 'utf8');
  source = reviewedOriginal(source, manifest);
  writeFileSync(join(root, 'dist', moduleName), source);
  baseline = (await import(pathToFileURL(join(root, 'dist', moduleName)) + '?original')).t;
  repair = (...extra) => spawnSync('python3', [helper, '--openclaw-bin', join(root, 'openclaw.mjs'),
    '--state-dir', join(root, 'state'), '--command-attempt-warning', ...extra], {encoding: 'utf8'});
  const result = repair();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(hash(readFileSync(join(root, 'dist', moduleName))), manifest.patchedSha256);
  patched = process.env.ODS_COMMAND_ATTEMPT_WARNING_RED === '1' ? baseline :
    (await import(pathToFileURL(join(root, 'dist', moduleName)) + '?patched')).t;
  // Compare against the shipped ODS recipes, not only the public SDK baseline.
  const oldPayloadHash = '026b1f330b210aa931143733a518d73408422036e2ee95164132a565c298a21f';
  const oldPayload = applyRecipe(source, manifest.previousReplacements[oldPayloadHash]);
  assert.equal(hash(oldPayload), oldPayloadHash);
  writeFileSync(join(root, 'dist', 'payloads-main-proof.js'), oldPayload);
  mainPayload = (await import(pathToFileURL(join(root, 'dist', 'payloads-main-proof.js')))).t;
  if (process.env.ODS_COMMAND_ATTEMPT_WARNING_RED === '1') patched = mainPayload;
  const selectionName = 'selection-BEwSQKM-.js';
  const selectionRecipe = JSON.parse(readFileSync(new URL('../host/openclaw-compaction-budget.json', import.meta.url)));
  const originalSelection = reviewedOriginal(readFileSync(join(installed, 'dist', selectionName), 'utf8'), selectionRecipe);
  const oldSelectionHash = '2382052dd075b2eae2d6fee59d5485bc5b63a988286a1595cb9e122c0fd87d12';
  const oldSelectionSource = applyRecipe(originalSelection, selectionRecipe.previousReplacements[oldSelectionHash]);
  assert.equal(hash(oldSelectionSource), oldSelectionHash);
  writeFileSync(join(root, 'dist', selectionName), originalSelection);
  const selectionRepair = spawnSync('python3', [helper, '--openclaw-bin', join(root, 'openclaw.mjs'),
    '--state-dir', join(root, 'selection-state'), '--compaction-budget'], {encoding: 'utf8'});
  assert.equal(selectionRepair.status, 0, selectionRepair.stderr);
  const repairedSelection = readFileSync(join(root, 'dist', selectionName), 'utf8');
  assert.equal(hash(repairedSelection), selectionRecipe.patchedSha256);
  // TEST ONLY: expose the real subscription factory in exact verified copies.
  // Its initializer, event handlers, lifecycle and result projection are intact.
  const testExport = '\nexport { subscribeEmbeddedAgentSession as testSubscribe };\n';
  for (const [name, bytes] of [['selection-main-proof.js', oldSelectionSource], ['selection-status-proof.js', repairedSelection]])
    writeFileSync(join(root, 'dist', name), bytes + testExport);
  oldSelection = (await import(pathToFileURL(join(root, 'dist', 'selection-main-proof.js')))).testSubscribe;
  newSelection = process.env.ODS_COMMAND_ATTEMPT_WARNING_RED === '1' ? oldSelection :
    (await import(pathToFileURL(join(root, 'dist', 'selection-status-proof.js')))).testSubscribe;
  sdk = await import(pathToFileURL(join(installed, 'dist/plugin-sdk/agent-harness.js')));
  assert.equal(sdk.OPENCLAW_VERSION, manifest.version);
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
  assert.match(after.at(-1).text, /^⚠️ Command attempt reported an error: /);
  assert.equal(after.at(-1).isError, true);
  assert.deepEqual(after.slice(0, -1), before.slice(0, -1));
  assert.equal(input.lastToolError.actionFingerprint, 'unchanged-evidence');
  assert.doesNotMatch(after.at(-1).text, /recovered|resolved|succeeded/);
});

for (const toolName of ['exec', 'bash']) {
  run(`${toolName}: unresolved failure remains visible and verbose details remain intact`, () => {
    const output = patched(params({verboseLevel: 'full', lastToolError: {...failure, toolName}}));
    assert.equal(output.at(-1).isError, true);
    assert.match(output.at(-1).text, /Command attempt reported an error:/);
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
  ['dynamic suppression', {suppressToolErrorWarnings: () => true}],
  ['middleware without a final answer', {assistantTexts: [], lastToolError: {...failure, middlewareError: true}}],
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

let sequence = 0;
function subscription(factory) {
  const runId = `command-status-${++sequence}`;
  const value = factory({config, runId, agentId: 'pixel', sessionKey: `agent:pixel:${runId}`,
    session: {subscribe() { return () => {}; }, sessionManager: {}, isCompacting: false},
    toolProgressDetail: 'explain', verboseLevel: 'off', toolResultFormat: 'markdown'});
  const tools = sdk.createOpenClawCodingTools({config, agentId: 'pixel', runId,
    sessionId: runId, sessionKey: `agent:pixel:${runId}`, runSessionKey: `agent:pixel:${runId}`,
    workspaceDir: workspace, cwd: workspace, oneShotCliRun: true, emitBeforeToolCallDiagnostics: false});
  const exec = tools.find(tool => tool.name === 'exec');
  return {value, async execute(args, executionArgs = args) {
    return value.runToolLifecycle({toolName: 'exec', toolCallId: `exec-${++sequence}`, args,
      execute: () => exec.execute(`sdk-${sequence}`, executionArgs)});
  }};
}
const footer = (formatter, lastToolError, extra = {}) => formatter(params({lastToolError, ...extra}));

run('real SDK exec result traverses actual subscribed handlers: expected exit 1 keeps its argument and neutral status', async () => {
  const args = {command: 'python3 main.py -1', workdir: workspace, timeout: 5, yieldMs: 10000};
  const before = subscription(oldSelection), after = subscription(newSelection);
  try {
    const oldResult = await before.execute(args), newResult = await after.execute(args);
    assert.equal(oldResult.details.exitCode, 1);
    assert.equal(newResult.details.exitCode, 1);
    const oldError = before.value.getLastToolError(), newError = after.value.getLastToolError();
    assert.equal(newError.error, 'Error: distance must be non-negative');
    assert.equal(newError.exitCode, 1);
    assert.equal(newError.meta, oldError.meta, 'progress detail stays unchanged');
    assert.equal(newError.actionFingerprint, oldError.actionFingerprint);
    assert.equal(newError.mutatingAction, oldError.mutatingAction);
    assert.deepEqual(after.value.toolMetas, before.value.toolMetas);
    const oldFooter = footer(mainPayload, oldError).at(-1);
    assert.match(oldFooter.text, /Earlier command attempt failed:/);
    assert.doesNotMatch(oldFooter.text, /main\.py -1/);
    const status = footer(patched, newError).at(-1);
    assert.match(status.text, /^⚠️ Command exit status 1:/);
    assert.match(status.text, /python3 main\.py -1/);
    assert.equal(status.isError, true);
    assert.doesNotMatch(status.text, /task failed|recovered|resolved|succeeded/i);
    assert.deepEqual(footer(patched, newError, {assistantTexts: []}),
      footer(mainPayload, oldError, {assistantTexts: []}), 'no-final-answer warning remains unchanged');
    // Preserve the runtime's existing same-action recovery decision exactly.
    const successArgs = {command: 'python3 main.py 1.609344', workdir: workspace, timeout: 5, yieldMs: 10000};
    await before.execute(successArgs); await after.execute(successArgs);
    assert.equal(Boolean(after.value.getLastToolError()), Boolean(before.value.getLastToolError()));
  } finally { before.value.unsubscribe(); after.value.unsubscribe(); }
});

run('real successful SDK command produces no warning or invented failure status', async () => {
  const active = subscription(newSelection);
  try {
    const result = await active.execute({command: 'python3 -c "print(42)"', workdir: workspace, timeout: 5, yieldMs: 10000});
    assert.equal(result.details.exitCode, 0);
    assert.equal(active.value.getLastToolError(), undefined);
    assert.equal(footer(patched, undefined).length, 1);
  } finally { active.value.unsubscribe(); }
});

run('failure context is captured before the actual cancellation wrapper and remains SDK-redacted', async () => {
  const controlRoot = join(root, 'control'); mkdirSync(controlRoot, {mode: 0o700});
  writeFileSync(join(controlRoot, 'cancellable-exec.sh'), readFileSync(new URL('../host/cancellable-exec.sh', import.meta.url)), {mode: 0o500});
  const control = createExecCancellationControl({root: controlRoot, executionHost: 'gateway'});
  const active = subscription(newSelection);
  const secret = 'sk-proj-' + 'fixture-only-'.repeat(5);
  const args = {command: `OPENAI_API_KEY=${secret} python3 main.py -1`, workdir: workspace, timeout: 5, yieldMs: 10000};
  try {
    const wrapped = control.prepare('private-wrapper', args.command);
    const result = await active.execute(args, {...args, command: wrapped});
    assert.equal(result.details.exitCode, 1);
    const error = active.value.getLastToolError();
    assert.match(error.failureMeta, /main\.py -1/);
    assert.doesNotMatch(error.failureMeta, new RegExp(secret));
    assert.doesNotMatch(error.failureMeta, /cancellable-exec|base64/);
    assert.ok(error.failureMeta.length < 512);
    const status = footer(patched, error).at(-1);
    assert.match(status.text, /Command exit status 1:/);
    assert.doesNotMatch(status.text, new RegExp(secret));
  } finally { active.value.unsubscribe(); }
});

run('long real command context is capped after redaction with a visible, UTF-16-safe head and tail', async () => {
  const secret = 'sk-proj-' + 'private-fixture-'.repeat(5);
  let unicodeCase;
  // Choose a real SDK-produced detail with a surrogate pair at a cut boundary,
  // so naive substring truncation cannot accidentally satisfy this regression.
  for (let padding = 0; padding < 64 && !unicodeCase; padding++) {
    const filename = `${'x'.repeat(padding)}😀main.py`;
    const command = `OPENAI_API_KEY=${secret} ` + Array(160).fill(`python3 ${filename} -1`).join('; ');
    const args = {command, workdir: workspace, timeout: 30, yieldMs: 30000};
    const raw = sdk.inferToolMetaFromArgs('exec', args, {detailMode: 'raw'});
    if (!(raw.slice(0, 255) + '…' + raw.slice(-256)).isWellFormed()) unicodeCase = {filename, args, raw};
  }
  assert.ok(unicodeCase, 'exercise an actual surrogate boundary, not merely Unicode elsewhere');
  writeFileSync(join(workspace, unicodeCase.filename), converter);
  for (const args of [
    {command: Array(160).fill('python3 main.py -1').join('; '), workdir: workspace, timeout: 30, yieldMs: 30000},
    unicodeCase.args,
  ]) {
    const raw = sdk.inferToolMetaFromArgs('exec', args, {detailMode: 'raw'});
    assert.ok(raw.length > 512, 'the SDK bounds raw fragments, not the entire composed detail');
    assert.doesNotMatch(raw, new RegExp(secret));
    const active = subscription(newSelection);
    try {
      const result = await active.execute(args);
      assert.equal(result.details.exitCode, 1);
      const error = active.value.getLastToolError();
      assert.ok(error.failureMeta.length <= 512);
      assert.equal(error.failureMeta.isWellFormed(), true);
      assert.ok(error.failureMeta.includes('…'));
      assert.ok(error.failureMeta.startsWith(raw.slice(0, 100)));
      assert.ok(error.failureMeta.endsWith(raw.slice(-100)));
      assert.match(error.failureMeta, / -1/);
      assert.doesNotMatch(error.failureMeta, new RegExp(secret));
      assert.equal(error.meta, sdk.inferToolMetaFromArgs('exec', args, {detailMode: 'explain'}));
      assert.match(footer(patched, error).at(-1).text, /Command exit status 1:/);
    } finally { active.value.unsubscribe(); }
  }
});

for (const [label, result] of [
  ['timeout', {isError: true, content: [{type: 'text', text: 'Command timed out'}], details: {status: 'timeout', exitCode: 137}}],
  ['unknown', {content: [{type: 'text', text: 'Command unavailable'}], details: {status: 'error'}}],
]) {
  run(`${label}: structured producer never invents an exit status`, async () => {
    const active = subscription(newSelection);
    try {
      await active.value.runToolLifecycle({toolName: 'exec', toolCallId: `status-${label}`, args: {command: 'python3 main.py -1'},
        execute: async () => result});
      const error = active.value.getLastToolError();
      assert.equal(error.exitCode, undefined);
      const status = footer(patched, error).at(-1);
      assert.match(status.text, /Command attempt reported an error:/);
      assert.doesNotMatch(status.text, /Command exit status/);
      assert.equal(status.isError, true);
    } finally { active.value.unsubscribe(); }
  });
}
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
