// Team and goal turns as Pixel receives them: the owner messages dashboard-api
// and the dashboard build, and the real plugin entry's before_prompt_build hook.
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath, pathToFileURL} from 'node:url';
import * as responseLanguage from '../plugin/response-language.mjs';
import {managedTeamRole} from '../plugin/tool-loop-guard.mjs';
import {promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {ACTIVITY_CONTRACT} from '../plugin/activity-display.mjs';
import {GOAL_CONTRACT} from '../plugin/goal-progress.mjs';
import {executionContext} from '../plugin/completion-assurance.mjs';

const PYTHON = process.platform === 'win32' ? 'python' : 'python3';
export const PYTHON_AVAILABLE = spawnSync(PYTHON, ['--version'], {windowsHide: true}).status === 0;
const DASHBOARD_API_TURNS = [
  'import json, sys',
  'sys.path.insert(0, sys.argv[1])',
  'import pixel_agent_teams as teams',
  'owner = json.loads(sys.argv[2])',
  "row = {'goal': owner['goal'], 'context': owner['context'], 'mode': 'team'}",
  'manager = teams.TeamManager.__new__(teams.TeamManager)',
  "prompts = {role: manager._prompt(row, teams.worker('t', 0, role)) for role in teams.ROLES}",
  "goal = {**row, 'mode': 'goal'}",
  "prompts['goal'] = manager._prompt(goal, teams.worker('t', 0, 'builder'))",
  "plan = {'status': 'active', 'summary': 'Working', 'steps': [{'id': 'work', 'title': owner['title'], 'status': 'running'}]}",
  "prompts['goal-continued'] = teams.goal_prompt(goal, {'goal_plan': plan, 'goal_answers': owner['answer']})",
  // TeamManager.answer, with only its store and scheduler replaced.
  'class Store:',
  "    instance = 'test'",
  '    def get(self, owner_id, team_id): return self.row',
  '    def save(self, owner_id, saved): self.row = saved',
  'def answered(mode, role):',
  '    store = Store()',
  '    answering = teams.TeamManager.__new__(teams.TeamManager)',
  '    answering.store, answering.tasks, answering.active_agents = store, {}, {}',
  '    answering._check_admission = lambda *args: None',
  '    answering._schedule = lambda *args: None',
  "    question = {'id': 'choice', 'question': owner['question'], 'options': [owner['answer'], 'Other']}",
  "    agent = {**teams.worker('t', 0, role), 'status': 'waiting', 'questions': [question]}",
  "    store.save('owner', {**row, 'id': 't', 'chat_id': 'chat', 'mode': mode, 'status': 'waiting', 'agents': [agent],",
  "                         'stop_requested': False, 'instance': 'test'})",
  "    answering.answer('owner', 't', '0', {'choice': owner['answer']})",
  "    return store.row['agents'][0]['messages'][-1]['content']",
  "answers = {'builder': answered('team', 'builder'), 'reviewer': answered('team', 'reviewer'), 'goal': answered('goal', 'builder')}",
  "print(json.dumps({'prompts': prompts, 'answers': answers}))",
].join('\n');

// The real TeamManager._prompt for every role, and goal_prompt for a new goal
// and for a continued one with a saved plan and an owner answer. answers: the
// message of the turn after the owner answers a clarification question
// (TeamManager.answer) for a Builder, a read-only Reviewer and a goal. owner
// holds the conversation's words: the goal, context, the question asked, the
// owner's answer and the plan step title.
export function dashboardApiTurns(owner) {
  const run = spawnSync(PYTHON, ['-I', '-c', DASHBOARD_API_TURNS,
    fileURLToPath(new URL('../../dashboard-api/', import.meta.url)), JSON.stringify(owner)], {encoding: 'utf8', windowsHide: true});
  assert.equal(run.status, 0, run.stderr);
  const turns = JSON.parse(run.stdout);
  assert.deepEqual(Object.keys(turns.prompts).sort(), ['builder', 'coordinator', 'explorer', 'goal', 'goal-continued', 'planner',
    'reviewer', 'summarizer', 'verifier']);
  return turns;
}

// The dashboard's continueGoal (dashboard/src/lib/portalGoal.js), which builds
// the message that resumes a goal or answers its question. Vite resolves the
// dashboard's extensionless imports; here each relative import becomes a
// data: URL module of that file.
export async function dashboardContinueGoal() {
  const moduleUrl = file => {
    const source = fs.readFileSync(file, 'utf8').replace(/from\s+(['"])(\.[^'"]+)\1/g, (_match, _quote, specifier) =>
      `from ${JSON.stringify(moduleUrl(fileURLToPath(new URL(/\.m?js$/.test(specifier) ? specifier : `${specifier}.js`,
        pathToFileURL(file)))))}`);
    return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
  };
  const {continueGoal} = await import(moduleUrl(fileURLToPath(new URL('../../dashboard/src/lib/portalGoal.js', import.meta.url))));
  return continueGoal;
}

// node:test runs a test's after-hooks in the order they were added, so one
// hook per test puts the variables back, undoing its loads newest first.
const environments = new WeakMap();
function setForTest(t, values) {
  if (!environments.has(t)) {
    const saved = [];
    environments.set(t, saved);
    t.after(() => {
      for (const previous of saved.reverse()) {
        for (const [name, value] of previous) if (value === undefined) delete process.env[name]; else process.env[name] = value;
      }
    });
  }
  environments.get(t).push(Object.keys(values).map(name => [name, process.env[name]]));
  Object.assign(process.env, values);
}

// The real plugin entry with only the OpenClaw SDK boundary replaced (the
// pending_clarification.test.mjs pattern), so before_prompt_build runs with
// the real goal mode, owner-turn and team-role detection. Each call registers
// a fresh index.js instance, a gateway start, with its own home and OpenClaw
// state folder; stateFiles are written there first. The returned function
// builds one owner turn, with the session's earlier messages, and returns the
// joined prompt context.
let loads = 0;
export async function realPromptBuild(t, {stateFiles = {}} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-prompt-build-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const state = path.join(root, '.openclaw');
  fs.mkdirSync(state, {recursive: true, mode: 0o700});
  for (const [name, content] of Object.entries(stateFiles)) fs.writeFileSync(path.join(state, name), content);
  // Every state root the full registration opens follows these.
  setForTest(t, {OPENCLAW_STATE_DIR: state, HOME: root, USERPROFILE: root});
  const entry = new URL('../plugin/index.js', import.meta.url);
  const source = fs.readFileSync(entry, 'utf8').replace(/from\s+(['"])([^'"]+)\1/g, (match, _quote, specifier) =>
    specifier.startsWith('.') ? `from ${JSON.stringify(new URL(specifier, entry).href)}` : match);
  // Only these SDK calls are expected; any other fails the test.
  const sdk = {getSessionEntry: () => undefined, resolveStorePath: () => path.join(root, 'sessions.json'),
    resolveActiveEmbeddedRunSessionId: () => undefined, resolveAgentWorkspaceDir: () => undefined, resolveUserPath: value => value};
  const load = ++loads, hook = `__odsPromptBuildSdk${load}`;
  globalThis[hook] = (name, args) => {
    if (!Object.hasOwn(sdk, name)) throw new Error(`Unexpected SDK call: ${name}`);
    return sdk[name](...args);
  };
  const stubbed = source.replace(/import\s*\{([^}]+)\}\s*from\s*['"]openclaw\/plugin-sdk\/[^'"]+['"];?/g, (_match, names) =>
    names.split(',').map(name => name.trim()).filter(Boolean).map(name => name === 'definePluginEntry'
      ? 'const definePluginEntry = entry => entry;' : name === 'OPENCLAW_VERSION' ? 'const OPENCLAW_VERSION = "2026.6.33";'
        : `const ${name} = (...args) => globalThis.${hook}(${JSON.stringify(name)}, args);`).join('\n'));
  assert.doesNotMatch(stubbed, /from\s*['"]openclaw\//, 'all SDK imports are isolated');
  const {default: plugin} = await import(`data:text/javascript;base64,${Buffer.from(
    `${stubbed}\n//# sourceURL=${entry.href}?prompt-build-${load}`).toString('base64')}`);
  const hooks = new Map();
  plugin.register({registrationMode: 'full', pluginConfig: {perplexicaPort: 1}, logger: {warn() {}, info() {}},
    config: {agents: {list: [{id: 'pixel', sandbox: {mode: 'off'}, tools: {exec: {host: 'gateway'}}}]}},
    on: (name, handler) => hooks.set(name, [...(hooks.get(name) ?? []), handler]),
    registerHttpRoute() {}, registerTool() {}});
  const fire = async (name, event, ctx) => {
    const results = [];
    for (const handler of hooks.get(name) ?? []) results.push(await handler(event, ctx));
    return results.filter(Boolean);
  };
  let runs = 0;
  return async (prompt, messages = []) => {
    const n = String(++runs).padStart(12, '0');
    const ctx = {agentId: 'pixel', runId: `chatcmpl_00000000-0000-4000-8${String(load).padStart(3, '0')}-${n}`,
      sessionId: `session-${load}-${n}`, sessionKey: `agent:pixel:openai-user:ods-${'e'.repeat(64)}`, trigger: 'user'};
    const event = {prompt, messages};
    // OpenClaw resolvePromptBuildHookResult: context fields of every handler, joined.
    const results = await fire('before_prompt_build', event, ctx);
    const joined = key => results.map(result => result[key]).filter(Boolean).join('\n\n') || undefined;
    await fire('before_agent_run', event, ctx);
    await fire('agent_end', {runId: ctx.runId, success: true, messages: []}, ctx);
    return {prependContext: joined('prependContext'), appendContext: joined('appendContext'),
      appendSystemContext: joined('appendSystemContext')};
  };
}

// The entry's before_prompt_build hook alone (plugin/index.js), run in a vm
// context where its collaborators are stubs: the response-language setting,
// goal mode and a clarification note are set directly, so the order of
// everything beside the owner turn can be checked for turns the real flow
// rarely combines.
export async function promptHookAlone({language, goal = false, clarificationNote,
  prompt = 'Quais são três dicas para manter o foco?'} = {}) {
  const source = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const begin = source.indexOf('    api.on("before_prompt_build",');
  const finish = source.indexOf('    api.on("model_call_started",', begin);
  assert.ok(begin >= 0 && finish > begin);
  let callback;
  vm.runInNewContext(source.slice(begin, finish), {
    ...responseLanguage,
    api: {config: {}, on: (_name, fn) => { callback = fn; }}, AGENT_ID: 'pixel',
    accessRuntime: {isProbe: () => false},
    delegationDelivery: {admission: () => undefined, observe() {}, promptContext: () => undefined, ownerRunFor: () => undefined},
    privateBrowserAccessForAgent: () => false, executionHostForAgent: () => 'sandbox',
    toolLoopGuard: {observeRun() {}, ownerIntentEventForRun: (_id, event) => event, verificationStatus: () => undefined,
      observeRepositorySource() {}, promptContextForRun: () => undefined,
      observeExampleRun() {}, exampleContextForRun: () => undefined,
      clarificationContextForRun: id => (id === 'run' ? clarificationNote : undefined)},
    goalProgress: {begin() {}, active: () => goal}, taskActivity: {begin() {}},
    promptContractForAgent, configuredContextWindow: 131072, configuredLeanPrompt: false, configuredResponseLanguage: language,
    extensionRepositoryContext: async () => '', managedTeamRole, ACTIVITY_CONTRACT, GOAL_CONTRACT,
    executionLocationContext: () => '', executionContext,
  });
  return callback({prompt}, {agentId: 'pixel', runId: 'run'});
}
