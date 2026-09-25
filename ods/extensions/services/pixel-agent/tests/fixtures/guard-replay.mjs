// Replays recorded tool sequences through the tool loop guard in OpenClaw's
// hook order (before_tool_call, execution, after_tool_call,
// tool_result_persist), against a real workspace directory on disk. Direct
// calls and the Tool Search `tool_call` envelope (with its nested catalog
// call) share one path, so every replay can run in both forms. `message`
// interleaves the tool calls of one model message as OpenClaw runs them in
// parallel.
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
  // One tool call split into OpenClaw's hook phases: before_tool_call, the
  // execution (with Tool Search's nested child hooks), after_tool_call and
  // tool_result_persist. `perform` receives the arguments that would execute
  // and returns the catalog tool's own result.
  function phases(name, args, perform, id = `${runId}-${name}-${++sequence}`) {
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id: `openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId: id};
    let decision, result, executed = params, inner;
    return {
      id,
      before() {
        decision = guard.beforeToolCall({toolName, params, toolCallId: id}, ctx);
        if (!decision?.block) executed = wrapped ? decision?.params ?? params : {...params, ...decision?.params};
        return decision;
      },
      run() {
        if (decision?.block) {
          result = {content: [{type: 'text', text: decision.blockReason}], details: {status: 'blocked'}};
          return;
        }
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
      },
      after() {
        const isError = decision?.block === true || result?.isError === true;
        guard.afterToolCall({toolName, params: executed, toolCallId: id, result,
          ...(isError ? {error: result.details?.error ?? result.content[0].text} : {})}, ctx);
      },
      persist() {
        const isError = decision?.block === true || result?.isError === true;
        const message = {role: 'toolResult', toolName, toolCallId: id, isError, ...structuredClone(result)};
        const persisted = guard.toolResultPersist({toolName, toolCallId: id, message}, ctx)?.message ?? message;
        return {id, decision, executed: wrapped ? executed.args : executed, persisted, inner: wrapped ? persisted.details?.result : persisted,
          text: persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n')};
      },
    };
  }
  function call(name, args, perform, id) {
    guard.observeModelCall({}, context);
    const step = phases(name, args, perform, id);
    step.before();
    step.run();
    step.after();
    return step.persist();
  }
  // One model message with several tool calls, as OpenClaw's default parallel
  // tool execution delivers them: every before_tool_call in message order,
  // then the executions and after_tool_call hooks in `schedule` order ("run 1",
  // "after 0", ...; after_tool_call fires on completion and is not awaited,
  // and the per-file mutation queue keeps message order within one file),
  // then every tool_result_persist in message order once all have completed.
  // The default schedule runs every call, then every after hook. Each call is
  // [name, args, perform].
  function message(calls, {schedule} = {}) {
    guard.observeModelCall({}, context);
    const steps = calls.map(([name, args, perform]) => phases(name, args, perform));
    for (const step of steps) step.before();
    for (const entry of schedule ?? [...steps.map((_, index) => `run ${index}`), ...steps.map((_, index) => `after ${index}`)]) {
      const [phase, index] = entry.split(' ');
      assert.ok(['run', 'after'].includes(phase) && steps[Number(index)], entry);
      steps[Number(index)][phase]();
    }
    return steps.map(step => step.persist());
  }
  const readArgs = file => ['read', {path: file}, args =>
    ({content: [{type: 'text', text: fs.readFileSync(hostPath(root, args.path), 'utf8')}], details: {}})];
  const editArgs = (file, edits) => ['edit', {path: file, edits}, args => executeOpenClawEdit(root, args)];
  const writeArgs = (file, content) => ['write', {path: file, content}, args => {
    fs.mkdirSync(path.dirname(hostPath(root, args.path)), {recursive: true});
    fs.writeFileSync(hostPath(root, args.path), args.content);
    return {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(args.content)} bytes to ${args.path}`}], details: {}};
  }];
  // `respond` maps the executed exec arguments to the command's result.
  const execArgs = (args, respond = () => execResult('(no output)', 0)) =>
    ['exec', typeof args === 'string' ? {command: args, workdir: '/workspace'} : args, respond];
  const read = file => call(...readArgs(file));
  const edit = (file, edits) => call(...editArgs(file, edits));
  const write = (file, content) => call(...writeArgs(file, content));
  const exec = (args, respond) => call(...execArgs(args, respond));
  return {guard, context, call, phases, message, read, edit, write, exec, readArgs, editArgs, writeArgs, execArgs, root};
}
