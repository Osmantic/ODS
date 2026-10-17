// The shipped example's guard through the plugin entry's own registration
// code (plugin/index.js): its before_prompt_build, before_tool_call,
// after_tool_call, tool_result_persist, before_agent_finalize,
// reply_payload_sending and agent_end callbacks, and the construction option
// that links a delegated run to its owner turn (exampleOwnerRun), run in a vm
// with a real tool-loop guard and delegation custody. The owner's reply is
// what Portal's ingress delivers from the run's verification
// (host/pixel_ingress.mjs). This is a source composition fixture, not gateway
// qualification; example_guard.test.mjs covers the guard's behaviour itself.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, {after} from 'node:test';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

import {applyVerificationToCompletion, parseVerificationResponse} from '../host/pixel_ingress.mjs';
import {withCronCommandPayloadBlock} from '../plugin/cron-command-payload-guard.mjs';
import {withPixelCronDeliveryDefault} from '../plugin/cron-delivery-default.mjs';
import {EXAMPLE_NOTES} from '../plugin/example-guard.mjs';
import {exampleRecordDirectory, exampleRecordFile} from '../plugin/example-projects.mjs';
import {createProjectRunControl} from '../plugin/project-run-control.mjs';
import {promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {createSubagentDelivery} from '../plugin/subagent-delivery.mjs';
import {withPixelSubagentWorkspace} from '../plugin/subagent-workspace.mjs';
import {compactToolResultEnvelope} from '../plugin/tool-result-envelope.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createWorkspaceArtifactAdmission} from '../plugin/workspace-artifact.mjs';
import {createWorkspaceBundleAdmission} from '../plugin/workspace-bundle.mjs';

const source = fs.readFileSync(process.env.PIXEL_PLUGIN_ENTRY ?? new URL('../plugin/index.js', import.meta.url), 'utf8');
function slice(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `expected the registration block starting ${start}`);
  return source.slice(from, to);
}
const HOOKS = [
  slice('    api.on("before_prompt_build", async (event, context) => {', '    api.on("model_call_started"'),
  slice('    api.on("agent_end", (event, context) => {', "    api.registerHttpRoute({path: '/pixel-ods/runtime-identity'"),
  slice('    api.on("tool_result_persist", (event, context) => {', '    api.registerHttpRoute({\n      path: "/pixel-ods/abort"'),
].join('\n');
// One construction option's expression, as the entry passes it.
function option(name) {
  const match = new RegExp(String.raw`\n\s*${name}:\s*([^\n]+?),\n`).exec(source);
  assert.ok(match, `expected the ${name} option`);
  return match[1];
}

const SHIPPED = fileURLToPath(new URL('../example-projects/elf-of-doom/', import.meta.url));
const temporary = [];
const scratch = prefix => { const folder = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix))); temporary.push(folder); return folder; };
after(() => { for (const folder of temporary) fs.rmSync(folder, {recursive: true, force: true}); });
process.env.OPENCLAW_STATE_DIR = scratch('ods-example-wiring-state-');
fs.mkdirSync(exampleRecordDirectory(), {mode: 0o700});

function workspace() {
  const root = scratch('ods-example-wiring-');
  fs.cpSync(SHIPPED, path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
  fs.writeFileSync(exampleRecordFile(root), `${JSON.stringify({schemaVersion: 1, workspace: root, received: ['elf-of-doom'], added: ['elf-of-doom']})}\n`);
  const example = path.join(root, 'Playground', 'elf-of-doom');
  return {root, example, ui: () => fs.readFileSync(path.join(example, 'ui.js'), 'utf8')};
}

const OWNER = `agent:pixel:openai-user:ods-${'a'.repeat(64)}`;
const USER = `ods-${'a'.repeat(64)}`;
const OWNER_RUN = 'chatcmpl_11111111-2222-4333-8444-555555555555';
const CHILD = 'agent:pixel:subagent:22222222-2222-4333-8444-555555555555';
const CHILD_RUN = '33333333-2222-4333-8444-555555555555';
// The reply Portal receives: the model's text with the verification the
// gateway serves (JSON), parsed and applied by the ingress.
function delivered(text, served, runId) {
  const completion = {id: runId, choices: [{index: 0, message: {role: 'assistant', content: text}, finish_reason: 'stop'}]};
  return applyVerificationToCompletion(completion, parseVerificationResponse(JSON.parse(JSON.stringify(served)), runId))
    .choices[0].message.content;
}

// The entry's hooks around a real tool-loop guard and delegation custody,
// each built with the entry's own option expressions.
function entry(root) {
  const callbacks = {}, links = {};
  const linked = vm.createContext(links);
  const ownerRun = vm.runInContext(`(${option('exampleOwnerRun')})`, linked);
  links.toolLoopGuard = createToolLoopGuard({exampleOwnerRun: ownerRun});
  links.delegationDelivery = createSubagentDelivery({
    resolveOwnerSession: key => key === OWNER ? {sessionId: `session-${OWNER_RUN}`} : null,
    finalText: message => message.content.filter(block => block.type === 'text').map(block => block.text).join('\n'),
    verificationForRun: runId => links.toolLoopGuard.deliveryVerificationForRun(runId)});
  const quiet = {begin() {}, active() { return false; }, before() {}, update() {}, finish() {}, after() {}};
  vm.runInNewContext(HOOKS, {
    api: {on: (name, callback) => { callbacks[name] = callback; }, logger: {warn() {}},
      config: {agents: {list: [{id: 'pixel', workspace: root}]}}},
    AGENT_ID: 'pixel', toolLoopGuard: links.toolLoopGuard, delegationDelivery: links.delegationDelivery,
    accessRuntime: {isProbe: () => false, beforeTool() {}, afterTool() {}, finish() {}},
    privateBrowserAccessForAgent: () => false, executionHostForAgent: () => 'sandbox',
    goalProgress: {...quiet, finalize: (_event, _context, {guardDecision}) => guardDecision}, taskActivity: quiet,
    promptContractForAgent, configuredContextWindow: undefined, configuredLeanPrompt: false,
    extensionRepositoryContext: async () => '', GOAL_CONTRACT: '', ACTIVITY_CONTRACT: '',
    executionLocationContext: () => '', executionContext: () => '',
    conversationImageLifecycle: {observe() {}}, managedRuntime: undefined,
    withPixelCronDeliveryDefault, withCronCommandPayloadBlock, withPixelSubagentWorkspace,
    getSessionEntry: () => undefined, resolveStorePath: () => undefined, resolveUserPath: value => value,
    resolveAgentWorkspaceDir: config => config?.agents?.list?.find(agent => agent.id === 'pixel')?.workspace,
    bundleAdmission: createWorkspaceBundleAdmission(), artifactAdmission: createWorkspaceArtifactAdmission(),
    projectRunControl: createProjectRunControl(), compactToolResultEnvelope,
  });
  return {callbacks, delivery: links.delegationDelivery, guard: links.toolLoopGuard};
}

// One run through the entry's callbacks: its prompt build (returning what
// the model is given), tool calls that act on the workspace, and its end.
async function run({callbacks, guard}, context, prompt) {
  const built = await callbacks.before_prompt_build({prompt, messages: [{role: 'user', content: prompt}]}, context);
  let calls = 0;
  return {
    built,
    async call(toolName, params, act) {
      const toolCallId = `${context.runId}-${++calls}`, ctx = {...context, toolName, toolCallId};
      const decision = await callbacks.before_tool_call({toolName, params, toolCallId}, ctx);
      assert.notEqual(decision?.block, true, decision?.blockReason);
      act();
      const result = {content: [{type: 'text', text: 'ok'}], details: {status: 'completed', exitCode: 0}};
      callbacks.after_tool_call({toolName, params, toolCallId, result}, ctx);
      const message = {role: 'toolResult', toolName, toolCallId, ...structuredClone(result)};
      const persisted = callbacks.tool_result_persist({toolName, toolCallId, message}, ctx)?.message ?? message;
      return persisted.content.map(block => block.text).join('\n');
    },
    // The run's finalize and end, then the reply as Portal's ingress
    // delivers it. The channel reply hook never carries the notice.
    async finish(text) {
      await callbacks.before_agent_finalize({runId: context.runId, lastAssistantMessage: text}, context);
      assert.doesNotMatch(callbacks.reply_payload_sending({runId: context.runId, kind: 'final', payload: {text}})?.payload?.text ?? '',
        /ODS put|could not put/);
      callbacks.agent_end({runId: context.runId, success: true,
        messages: [{role: 'assistant', stopReason: 'stop', content: [{type: 'text', text}]}]}, context);
      return delivered(text, guard.deliveryVerificationForRun(context.runId), context.runId);
    },
  };
}
const owner = (runId = OWNER_RUN) => ({agentId: 'pixel', runId, sessionId: `session-${runId}`, sessionKey: OWNER, trigger: 'user'});

test('a turn that does not name it gets the game back through the entry\'s hooks, and the owner hears it', async () => {
  const {root, example, ui} = workspace();
  const original = ui();
  const turn = await run(entry(root), owner(), 'Make elf-of-doom faster');
  assert.match(turn.built.prependContext, new RegExp(EXAMPLE_NOTES.guidance.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  const text = await turn.call('exec', {command: 'echo // faster >> Playground/elf-of-doom/ui.js', workdir: '/workspace'},
    () => fs.appendFileSync(path.join(example, 'ui.js'), '// faster\n'));
  assert.ok(text.endsWith(EXAMPLE_NOTES.restored.model), text);
  assert.equal(ui(), original);
  fs.appendFileSync(path.join(example, 'ui.js'), '// late\n');
  assert.equal(await turn.finish('It is faster now.'), `It is faster now.\n\n${EXAMPLE_NOTES.restored.owner}`);
  assert.equal(ui(), original, 'put back at the end of the run too');
});

test('a turn that starts with "In elf-of-doom," changes it through the entry\'s hooks', async () => {
  const {root, example, ui} = workspace();
  const turn = await run(entry(root), owner(), 'In elf-of-doom, make it faster');
  assert.equal(turn.built.prependContext ?? '', '', 'no guidance');
  assert.doesNotMatch(await turn.call('exec', {command: 'echo // faster >> Playground/elf-of-doom/ui.js', workdir: '/workspace'},
    () => fs.appendFileSync(path.join(example, 'ui.js'), '// faster\n')), /\[ODS Portal\]/);
  assert.equal(await turn.finish('It is faster now.'), 'It is faster now.');
  assert.match(ui(), /\/\/ faster\n$/);
});

test('a turn that starts with "In elf-of-doom," writes in the real folder through the entry\'s hooks, even worded like a new game', async () => {
  const {root, example} = workspace();
  const wired = entry(root);
  const turn = await run(wired, owner(), 'In elf-of-doom, make a game over screen');
  const params = {path: 'Playground/elf-of-doom/gameover.js', content: '// game over\n'};
  const decision = await wired.callbacks.before_tool_call({toolName: 'write', params, toolCallId: 'write-1'},
    {...owner(), toolName: 'write', toolCallId: 'write-1'});
  assert.notEqual(decision?.block, true, decision?.blockReason);
  assert.equal((decision?.params ?? params).path, 'Playground/elf-of-doom/gameover.js');
  fs.writeFileSync(path.join(example, 'gameover.js'), params.content);
  assert.doesNotMatch(await turn.finish('There is a game over screen now.'), /ODS put/);
  assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['elf-of-doom']);
});

// The owner run delegates one child and yields, as delegation custody sees it.
async function delegate(wired, prompt) {
  const turn = await run(wired, owner(), prompt);
  const spawn = {...owner(), toolName: 'sessions_spawn', toolCallId: 'spawn'};
  wired.delivery.before({params: {runtime: 'subagent', mode: 'run'}}, spawn);
  wired.delivery.nativeSpawn({runId: CHILD_RUN, childSessionKey: CHILD}, {runId: CHILD_RUN, childSessionKey: CHILD, requesterSessionKey: OWNER});
  wired.delivery.after({result: {details: {status: 'accepted', runId: CHILD_RUN, childSessionKey: CHILD}}}, spawn);
  const yielded = {...owner(), toolName: 'sessions_yield', toolCallId: 'yield'};
  wired.delivery.before({params: {}}, yielded);
  wired.delivery.after({result: {details: {status: 'yielded'}}}, yielded);
  await turn.finish('Working on it.');
  return run(wired, {agentId: 'pixel', runId: CHILD_RUN, sessionId: `session-${CHILD_RUN}`, sessionKey: CHILD}, 'Edit ui.js');
}
async function announce(wired, text) {
  const continuation = {...owner(), runId: `announce:v1:${CHILD}:${CHILD_RUN}`,
    inputProvenance: {kind: 'inter_session', sourceTool: 'subagent_announce', sourceSessionKey: CHILD}};
  await (await run(wired, continuation, 'The child finished.')).finish(text);
  const read = wired.delivery.read(USER, OWNER_RUN);
  assert.equal(read.status, 'ready');
  return delivered(read.text, read.verification, OWNER_RUN);
}

test('a delegated child of a turn that names it keeps its change through the entry\'s hooks', async () => {
  const {root, example, ui} = workspace();
  const wired = entry(root);
  const child = await delegate(wired, 'In elf-of-doom, add a boss');
  assert.doesNotMatch(await child.call('exec', {command: 'echo // boss >> Playground/elf-of-doom/ui.js', workdir: '/workspace'},
    () => fs.appendFileSync(path.join(example, 'ui.js'), '// boss\n')), /\[ODS Portal\]/);
  await child.finish('Added a boss.');
  assert.match(ui(), /\/\/ boss\n$/);
  assert.equal(await announce(wired, 'There is a boss now.'), 'There is a boss now.');
});

test('a delegated child of a turn that does not name it is put back, and the delivered answer says so', async () => {
  const {root, example, ui} = workspace();
  const original = ui();
  const wired = entry(root);
  const child = await delegate(wired, 'Make the snake faster');
  assert.ok((await child.call('exec', {command: 'echo // snake >> Playground/elf-of-doom/ui.js', workdir: '/workspace'},
    () => fs.appendFileSync(path.join(example, 'ui.js'), '// snake\n'))).endsWith(EXAMPLE_NOTES.restored.model));
  await child.finish('Done.');
  assert.equal(ui(), original);
  assert.equal(await announce(wired, 'The snake is faster.'), `The snake is faster.\n\n${EXAMPLE_NOTES.restored.owner}`);
});
