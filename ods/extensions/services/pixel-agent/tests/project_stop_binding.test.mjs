import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const user = `ods-${'a'.repeat(64)}`;
const context = {agentId: 'pixel', runId: 'project-stop-run', sessionId: 'project-stop-session',
  sessionKey: `agent:pixel:openai-user:${user}`};

test('owner Stop waits for exact-run managed project cancellation before acknowledging', async () => {
  let finish, started;
  const entered = new Promise(resolve => { started = resolve; });
  const drained = new Promise(resolve => { finish = resolve; });
  const calls = [];
  const guard = createToolLoopGuard({
    abortRunAndDrain: async () => ({aborted: true, drained: true}),
    cancelProjectRun: async scope => { calls.push(scope); started(); return drained; },
  });
  guard.observeRun(context, 'pixel', {prompt: 'Build the existing project.'});
  let settled = false;
  const stopping = guard.abortUserRun(user).then(value => { settled = true; return value; });
  await Promise.race([entered, new Promise((_, reject) => setTimeout(() => reject(Error('project cancellation was not invoked')), 100))]);
  assert.equal(settled, false);
  assert.deepEqual(calls, [{runId: context.runId, sessionId: context.sessionId, sessionKey: context.sessionKey}]);
  finish(true);
  assert.equal(await stopping, true);
});

for (const outcome of ['unknown', 'throws']) {
  test(`owner Stop does not acknowledge ${outcome} project cancellation`, async () => {
    const guard = createToolLoopGuard({abortRunAndDrain: async () => ({aborted: true, drained: true}),
      cancelProjectRun: async () => { if (outcome === 'throws') throw Error('controller offline'); return false; }});
    guard.observeRun(context, 'pixel', {prompt: 'Build the existing project.'});
    assert.equal(await guard.abortUserRun(user), false);
  });
}

test('unknown owner and unacknowledged run abort cannot trigger project cancellation', async () => {
  let calls = 0;
  const guard = createToolLoopGuard({abortRunAndDrain: async () => ({aborted: false}),
    cancelProjectRun: async () => { calls++; return true; }});
  guard.observeRun(context, 'pixel', {prompt: 'Build the existing project.'});
  assert.equal(await guard.abortUserRun(`ods-${'b'.repeat(64)}`), false);
  assert.equal(await guard.abortUserRun(user), false);
  assert.equal(calls, 0);
});
