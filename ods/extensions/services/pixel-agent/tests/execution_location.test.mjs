import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {EXECUTION_LOCATION_CONTRACT, executionLocationContext} from '../plugin/execution-location.mjs';

test('local execution and loopback routing never establish local inference', () => {
  const context = executionLocationContext({agentId:'pixel'});
  assert.equal(context, EXECUTION_LOCATION_CONTRACT);
  assert.match(context, /does not prove that the selected model is local/);
  assert.match(context, /localhost or ods\/current gateway can route to a remote provider/);
  assert.match(context, /without current route and usage evidence/);
  assert.match(context, /leave inference location unverified/);
  assert.match(context, /do not invent additional machines/);
  assert.match(context, /actual owner request and configured capabilities/);
});

test('guidance is stable across chats and absent for other agents or missing identity', () => {
  for (const context of [undefined, {}, {agentId:'other'}, {sessionKey:'agent:pixel'}]) {
    assert.equal(executionLocationContext(context), '');
  }
  assert.equal(executionLocationContext({agentId:'pixel',sessionKey:'one'}),
    executionLocationContext({agentId:'pixel',sessionKey:'two'}));
});

test('registered prompt hook includes neutral guidance only with its existing agent contract', () => {
  const source = readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  assert.match(source, /import \{executionLocationContext\} from '\.\/execution-location\.mjs'/);
  assert.match(source, /return contract \?[^\n]+executionLocationContext\(context, AGENT_ID\)[^\n]+: undefined/);
});
