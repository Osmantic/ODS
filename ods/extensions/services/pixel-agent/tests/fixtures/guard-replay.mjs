// Replays recorded tool sequences through the tool loop guard in OpenClaw's
// hook order (before_tool_call, execution, after_tool_call,
// tool_result_persist), against a real workspace directory on disk. Direct
// calls and the Tool Search `tool_call` envelope (with its nested catalog
// call) share one path, so every replay can run in both forms.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard} from '../../plugin/tool-loop-guard.mjs';
import {executeOpenClawEdit} from './openclaw-edit-2026.6.33.mjs';

const workspaces = [];
export function workspaceWith(files = {}) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-replay-')));
  workspaces.push(root);
  for (const [file, content] of Object.entries(files)) place(root, file, content);
  return root;
}
export function removeWorkspaces() {
  for (const root of workspaces.splice(0)) fs.rmSync(root, {recursive: true, force: true});
}
export function place(root, file, content) {
  const target = path.join(root, ...file.split('/'));
  fs.mkdirSync(path.dirname(target), {recursive: true});
  fs.writeFileSync(target, content);
}
// The sandbox path /workspace/<file> or a model spelling of it, on the host.
export function hostPath(root, file) {
  return path.join(root, ...file.replace(/^\/?workspace\//, '').split('/'));
}

export const execResult = (text, exitCode) => ({
  ...(exitCode === 0 ? {} : {isError: true}),
  content: [{type: 'text', text}],
  details: {status: 'completed', exitCode, durationMs: 1, aggregated: text},
});

// `root` holds the files; `workspaceRoot` is what the guard is told (the same
// directory unless a test withholds it).
// Production always passes an exec controller (index.js); this one keeps the
// command unchanged so a replay can run it.
export const passthroughExecControl = {prepare: (_runId, command) => command};
export function guardReplay({wrapped = false, root, workspaceRoot = root, runId = 'run',
  guard = createToolLoopGuard({execControl: passthroughExecControl}), prompt} = {}) {
  const context = {agentId: 'pixel', runId, sessionId: `session-${runId}`, sessionKey: 'owner-key'};
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot, executionHost: 'sandbox'});
  let sequence = 0;
  // `perform` receives the arguments that would execute and returns the
  // catalog tool's own result.
  function call(name, args, perform, id = `${runId}-${name}-${++sequence}`) {
    guard.observeModelCall({}, context);
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id: `openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId: id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId: id}, ctx);
    let result, executed = params, inner;
    if (decision?.block) {
      result = {content: [{type: 'text', text: decision.blockReason}], details: {status: 'blocked'}};
    } else {
      executed = wrapped ? decision?.params ?? params : {...params, ...decision?.params};
      let selected = wrapped ? executed.args : executed;
      if (wrapped) {
        // Tool Search runs the catalog tool under a child ID with its own hooks.
        const child = `tool_search_code:${id}:${name}:1`;
        const childContext = {...context, toolName: name, toolCallId: child};
        const prepared = guard.beforeToolCall({toolName: name, toolCallId: child, params: selected}, childContext);
        assert.notEqual(prepared?.block, true, prepared?.blockReason);
        selected = {...selected, ...prepared?.params};
        inner = perform(selected);
        guard.afterToolCall({toolName: name, toolCallId: child, params: selected, result: inner,
          ...(inner?.isError ? {error: inner.details?.error ?? inner.content?.[0]?.text} : {})}, childContext);
      } else inner = perform(selected);
      const envelope = {tool: {id: `openclaw:core:${name}`, name, source: 'openclaw', sourceName: 'core'}, result: inner};
      result = wrapped ? {content: [{type: 'text', text: JSON.stringify(envelope)}], details: envelope} : inner;
    }
    const isError = decision?.block === true || result?.isError === true;
    guard.afterToolCall({toolName, params: executed, toolCallId: id, result,
      ...(isError ? {error: result.details?.error ?? result.content[0].text} : {})}, ctx);
    const message = {role: 'toolResult', toolName, toolCallId: id, isError, ...structuredClone(result)};
    const persisted = guard.toolResultPersist({toolName, toolCallId: id, message}, ctx)?.message ?? message;
    return {decision, executed: wrapped ? executed.args : executed, persisted, inner: wrapped ? persisted.details?.result : persisted,
      text: persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n')};
  }
  const read = file => call('read', {path: file}, args =>
    ({content: [{type: 'text', text: fs.readFileSync(hostPath(root, args.path), 'utf8')}], details: {}}));
  const edit = (file, edits) => call('edit', {path: file, edits}, args => executeOpenClawEdit(root, args));
  const write = (file, content) => call('write', {path: file, content}, args => {
    fs.mkdirSync(path.dirname(hostPath(root, args.path)), {recursive: true});
    fs.writeFileSync(hostPath(root, args.path), args.content);
    return {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(args.content)} bytes to ${args.path}`}], details: {}};
  });
  // `respond` maps the executed exec arguments to the command's result.
  const exec = (args, respond = () => execResult('(no output)', 0)) =>
    call('exec', typeof args === 'string' ? {command: args, workdir: '/workspace'} : args, respond);
  return {guard, context, call, read, edit, write, exec, root};
}
