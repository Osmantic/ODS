import test from 'node:test';
import assert from 'node:assert/strict';
import {createLayaTool, LAYA_GUIDE, LAYA_TOOL_SCHEMA} from '../plugin/laya-tool.mjs';
import {LayaServiceError} from '../plugin/laya-client.mjs';
import {LayaProtocolError} from '../plugin/laya-protocol.mjs';

test('disabled extension is rechecked even for a previously discovered tool', async () => {
  let enabled = true, calls = 0;
  const tool = createLayaTool({enabled: () => enabled, client: {decide: async () => {calls++; return {items: []}; }}});
  await tool.execute('a', {});
  enabled = false;
  const response = await tool.execute('b', {});
  assert.equal(response.isError, true);
  assert.equal(response.details.status, 'disabled');
  assert.equal(calls, 1);
});

test('successful consultation preserves the agent cancellation signal without granting authority', async () => {
  const controller = new AbortController();
  const args = {items: [{id: 'a', text: 'text'}]};
  const result = {kind: 'laya-decisions', items: [{id: 'a', answers: {}}], advisory: true};
  const tool = createLayaTool({enabled: () => true, client: {decide: async (input, signal) => {
    assert.equal(input, args); assert.equal(signal, controller.signal); return result;
  }}});
  const response = await tool.execute('x', args, controller.signal);
  assert.deepEqual(JSON.parse(response.content[0].text), result);
  assert.equal(response.details.executionAuthorized, false);
});

test('known inference and protocol failures keep continuation possible', async () => {
  for (const error of [new LayaServiceError('timed_out', 'Deadline exceeded.', true),
    new LayaProtocolError('truncated_context', 'Split the source.')]) {
    const tool = createLayaTool({enabled: () => true, client: {decide: async () => { throw error; }}});
    const response = await tool.execute('x', {});
    assert.equal(response.isError, true);
    assert.equal(response.details.status, error.code);
    assert.equal(response.details.upstreamCancellationVerified, false);
    assert.match(response.content[0].text, /Continue the owner's task/);
  }
});

test('programming failures are not silently converted into classifier unavailability', async () => {
  const tool = createLayaTool({enabled: () => true, client: {decide: async () => {throw new Error('implementation bug');}}});
  await assert.rejects(tool.execute('x', {}), /implementation bug/);
});

test('schema has no destination, credential, code execution or high repetition fields', () => {
  assert.deepEqual(Object.keys(LAYA_TOOL_SCHEMA.properties), ['items', 'questions', 'language', 'checkpoint']);
  assert.doesNotMatch(JSON.stringify(LAYA_TOOL_SCHEMA), /"maxLength"|"token"|"url"|"command"/);
  assert.match(LAYA_GUIDE, /Do not end with a raw classifier response/);
  assert.match(LAYA_GUIDE, /does not prove inference stopped/);
});
