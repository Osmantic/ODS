import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createAccessRuntime} from '../plugin/access-runtime.mjs';

// Exercise the callback from the real entry, rather than duplicating its
// forwarding logic: a deferred-only guard here previously hid a direct-call
// leak despite the runtime and runner unit tests passing independently.
test('plugin settlement callback releases both direct and deferred admissions', t => {
  const entry = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const callbacks = [...entry.matchAll(/onSettled:\s*scope\s*=>\s*\{([\s\S]*?)\n\s*\},/g)];
  assert.equal(callbacks.length, 1, 'locate the production readonly settlement callback');
  const settle = new Function('registeredAccessRuntime', 'scope', callbacks[0][1]);
  for (const deferred of [false, true]) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-readonly-settlement-'));
    fs.chmodSync(root, 0o700);
    t.after(() => fs.rmSync(root, {recursive:true, force:true}));
    const runtime = createAccessRuntime({directory:path.join(root, 'runtime'),
      runtimeVersion:'2026.6.33',hooksAllowed:true});
    const scope = {agentId:'pixel',runId:'run',sessionId:'session',sessionKey:'key',toolCallId:'direct',
      ...(deferred ? {deferredParentCallId:'parent'} : {})};
    const event = {toolCallId:deferred ? 'parent' : 'direct',
      toolName:deferred ? 'tool_call' : 'pixel_ods_readonly_exec'};
    runtime.beforeTool(event, scope);
    runtime.beforeTool({toolCallId:'unrelated',toolName:'exec'}, scope);
    settle(runtime, scope);
    assert.equal(runtime.status().activity.tools, 1, 'only the exact protected admission settles');
    settle(runtime, scope);
    runtime.afterTool(event, scope);
    assert.equal(runtime.status().activity.tools, 1, 'late SDK completion stays idempotent');
    runtime.afterTool({toolCallId:'unrelated',toolName:'exec'}, scope);
    assert.equal(runtime.status().phase, 'idle');
    assert.throws(() => settle(undefined, scope), /readonly admission runtime unavailable/);
  }
});
