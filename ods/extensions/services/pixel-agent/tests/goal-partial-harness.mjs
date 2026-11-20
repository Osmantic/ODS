// The real plugin entry (plugin/index.js) for /goal partial-work rounds: every
// hook it registers, its own goal-progress, task-activity and tool-loop guard
// construction, and its /pixel-ods/verification route, with only the OpenClaw
// SDK boundary replaced (the prompt-build-harness.mjs pattern). Each call is a
// fresh gateway start with its own home, exec-control root and workspace.
// Tool results are synthetic; no command, model or network request runs.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {parseVerificationResponse} from '../host/pixel_ingress.mjs';

const ENTRY = new URL('../plugin/index.js', import.meta.url);
let loads = 0;

function setEnvironment(t, values) {
  const previous = Object.keys(values).map(name => [name, process.env[name]]);
  t.after(() => { for (const [name, value] of previous) if (value === undefined) delete process.env[name]; else process.env[name] = value; });
  Object.assign(process.env, values);
}

export const OWNER_USER = `ods-${'c'.repeat(64)}`;
export const SITE = 'site';

// One gateway start. Returns a driver for one owner run of that gateway.
export async function goalGateway(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ods-goal-partial-')));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const home = path.join(root, 'home'), state = path.join(home, '.openclaw'), workspace = path.join(root, 'workspace');
  fs.mkdirSync(state, {recursive: true, mode: 0o700});
  // The sandbox execution-control root the entry validates before any exec.
  const control = path.join(state, '.ods-exec-control');
  fs.mkdirSync(control, {mode: 0o700});
  fs.writeFileSync(path.join(control, 'cancellable-exec.sh'), '#!/bin/sh\nexit 0\n', {mode: 0o500});
  fs.mkdirSync(path.join(workspace, SITE), {recursive: true});
  fs.writeFileSync(path.join(workspace, SITE, 'index.html'), '<!doctype html><title>Original</title><form></form>\n');
  setEnvironment(t, {OPENCLAW_STATE_DIR: state, HOME: home, USERPROFILE: home});
  const source = fs.readFileSync(ENTRY, 'utf8').replace(/from\s+(['"])([^'"]+)\1/g, (match, _quote, specifier) =>
    specifier.startsWith('.') ? `from ${JSON.stringify(new URL(specifier, ENTRY).href)}` : match);
  const sdk = {getSessionEntry: () => undefined, resolveStorePath: () => path.join(root, 'sessions.json'),
    resolveActiveEmbeddedRunSessionId: () => undefined, resolveAgentWorkspaceDir: () => workspace, resolveUserPath: value => value,
    // The SDK's visible text of an assistant message: its text blocks.
    extractAssistantVisibleText: message => typeof message?.content === 'string' ? message.content
      : (Array.isArray(message?.content) ? message.content : []).filter(block => block?.type === 'text').map(block => block.text).join('\n')};
  const load = ++loads, hook = `__odsGoalPartialSdk${load}`;
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
    `${stubbed}\n//# sourceURL=${ENTRY.href}?goal-partial-${load}`).toString('base64')}`);
  const hooks = new Map(), routes = new Map();
  plugin.register({registrationMode: 'full', pluginConfig: {perplexicaPort: 1}, logger: {warn() {}, info() {}},
    config: {agents: {list: [{id: 'pixel', workspace, sandbox: {mode: 'all'}, tools: {exec: {host: 'sandbox'}}}]}},
    on: (name, handler) => hooks.set(name, [...(hooks.get(name) ?? []), handler]),
    registerHttpRoute: route => routes.set(route.path, route), registerTool() {}});
  const fire = async (name, event, ctx) => {
    const results = [];
    for (const handler of hooks.get(name) ?? []) results.push(await handler(event, ctx));
    return results.filter(Boolean);
  };
  async function post(route, payload) {
    const body = Buffer.from(JSON.stringify(payload));
    const req = {method: 'POST', headers: {'content-type': 'application/json'}, async *[Symbol.asyncIterator]() { yield body; }};
    let sent;
    const res = {statusCode: 0, setHeader() {}, end(value) { sent = value; }};
    await routes.get(route).handler(req, res);
    return {status: res.statusCode, body: JSON.parse(sent)};
  }
  async function verification(runId) {
    const body = Buffer.from(JSON.stringify({runId}));
    const req = {method: 'POST', headers: {'content-type': 'application/json'}, async *[Symbol.asyncIterator]() { yield body; }};
    let sent;
    const res = {statusCode: 0, setHeader() {}, end(value) { sent = value; }};
    await routes.get('/pixel-ods/verification').handler(req, res);
    assert.equal(res.statusCode, 200, String(sent));
    return JSON.parse(sent);
  }
  let runs = 0;
  // One owner run: its prompt build, tool calls through the real hooks, the
  // final reply, the run's end, and what the verification route serves.
  function run(prompt) {
    const n = String(++runs).padStart(12, '0');
    const ctx = {agentId: 'pixel', runId: `chatcmpl_00000000-0000-4000-8${String(load).padStart(3, '0')}-${n}`,
      sessionId: `session-goal-${load}-${n}`, sessionKey: `agent:pixel:openai-user:${OWNER_USER}`, trigger: 'user'};
    let calls = 0;
    return {
      ctx, fire, verification: () => verification(ctx.runId),
      // The owner's cancel, through the entry's own abort route.
      abort: () => post('/pixel-ods/abort', {user: OWNER_USER}),
      async start() {
        await fire('before_prompt_build', {prompt, messages: []}, ctx);
        await fire('before_agent_run', {prompt, messages: []}, ctx);
      },
      // One tool call: before (whose rewritten params the runtime passes on),
      // after and persist, unless an option leaves a hook out or alters it.
      async call(toolName, params, result, {id, error, context = {}, afterContext = {}, before = true, after = true,
        afterParams, persist = true, event = {}} = {}) {
        const toolCallId = id ?? `call-${++calls}`;
        const hookContext = {...ctx, toolName, toolCallId, ...context};
        const decisions = before ? await fire('before_tool_call', {toolName, toolCallId, params, ...event}, hookContext) : [];
        const decision = decisions.at(-1);
        if (decision?.block) return {toolCallId, decision};
        const actual = toolName === 'tool_call' ? (decision?.params ?? params) : {...params, ...decision?.params};
        if (after) await fire('after_tool_call', {toolName, toolCallId, params: afterParams ?? actual, result,
          ...(error ? {error} : {}), ...event}, {...hookContext, ...afterContext});
        if (persist) await fire('tool_result_persist', {toolName, toolCallId,
          message: {role: 'toolResult', toolName, toolCallId, ...structuredClone(result)}}, {...hookContext, ...afterContext});
        return {toolCallId, decision, actual};
      },
      async finish(text, {stopReason = 'stop', content} = {}) {
        const message = {role: 'assistant', content: content ?? [{type: 'text', text}], stopReason};
        await fire('before_message_write', {message}, ctx);
        const decisions = await fire('before_agent_finalize', {runId: ctx.runId, lastAssistantMessage: text,
          messages: [{role: 'user', content: [{type: 'text', text: prompt}]}, message]}, ctx);
        await fire('agent_end', {runId: ctx.runId, success: true, messages: []}, ctx);
        const served = await verification(ctx.runId);
        return {decision: decisions.at(-1), served, parsed: parseVerificationResponse(structuredClone(served), ctx.runId)};
      },
    };
  }
  return {run, workspace};
}

// A Tool Search catalog call's outer result, as the pinned runtime returns it.
export function catalogResult(id, inner) {
  const [source, sourceName, name] = id.split(':');
  const envelope = {tool: {id, name, source, sourceName}, result: inner};
  return {details: envelope, content: [{type: 'text', text: JSON.stringify(envelope)}]};
}

// A catalog tool call as Tool Search runs it: the outer tool_call (whose
// before hook may rewrite its args), the tool's own nested child hooks under
// tool_search_code:<outer>:<tool>:1, then the outer receipt and its persist.
export async function catalog(owner, id, args, inner, {callId} = {}) {
  const outerId = callId ?? `cat-${Math.random().toString(16).slice(2, 10)}`, name = id.split(':').at(-1);
  const outer = {...owner.ctx, toolName: 'tool_call', toolCallId: outerId};
  const decision = (await owner.fire('before_tool_call', {toolName: 'tool_call', toolCallId: outerId, params: {id, args}}, outer)).at(-1);
  if (decision?.block) return {outerId, decision};
  const actual = decision?.params ?? {id, args};
  const childId = `tool_search_code:${outerId}:${name}:1`, child = {...owner.ctx, toolName: name, toolCallId: childId};
  const childDecision = (await owner.fire('before_tool_call', {toolName: name, toolCallId: childId, params: actual.args}, child)).at(-1);
  if (childDecision?.block) return {outerId, decision: childDecision};
  await owner.fire('after_tool_call', {toolName: name, toolCallId: childId, params: {...actual.args, ...childDecision?.params}, result: inner}, child);
  const result = catalogResult(id, inner);
  await owner.fire('after_tool_call', {toolName: 'tool_call', toolCallId: outerId, params: actual, result}, outer);
  await owner.fire('tool_result_persist', {toolName: 'tool_call', toolCallId: outerId,
    message: {role: 'toolResult', toolName: 'tool_call', toolCallId: outerId, ...structuredClone(result)}}, outer);
  return {outerId, decision};
}

export const PLAN = {status: 'active', summary: 'Working', steps: [
  {id: 'work', title: 'Repair the appointment form', status: 'running'},
  {id: 'verify', title: 'Check and publish the site', status: 'pending'}]};

// The goal plan through the catalog: the outer tool_call and the plan tool's
// own inner execution, whose receipt goal-progress.mjs accepts.
export async function plan(owner, {id = 'openclaw:pixel-ods:pixel_ods_goal', inner = true} = {}) {
  const outerId = `plan-${Math.random().toString(16).slice(2, 10)}`;
  const innerResult = {content: [{type: 'text', text: 'Plan recorded. Continue the next incomplete step now.'}], details: {goal: PLAN}};
  const ctx = owner.ctx;
  const outer = {...ctx, toolName: 'tool_call', toolCallId: outerId};
  const decisions = await owner.fire('before_tool_call', {toolName: 'tool_call', toolCallId: outerId, params: {id, args: PLAN}}, outer);
  if (decisions.at(-1)?.block) return {blocked: decisions.at(-1)};
  if (inner) {
    const child = `tool_search_code:${outerId}:pixel_ods_goal:1`;
    const innerCtx = {...ctx, toolName: 'pixel_ods_goal', toolCallId: child};
    await owner.fire('before_tool_call', {toolName: 'pixel_ods_goal', toolCallId: child, params: PLAN}, innerCtx);
    await owner.fire('after_tool_call', {toolName: 'pixel_ods_goal', toolCallId: child, params: PLAN, result: innerResult}, innerCtx);
  }
  const result = catalogResult(id, innerResult);
  await owner.fire('after_tool_call', {toolName: 'tool_call', toolCallId: outerId, params: {id, args: PLAN}, result}, outer);
  await owner.fire('tool_result_persist', {toolName: 'tool_call', toolCallId: outerId,
    message: {role: 'toolResult', toolName: 'tool_call', toolCallId: outerId, ...structuredClone(result)}}, outer);
  return {outerId};
}
