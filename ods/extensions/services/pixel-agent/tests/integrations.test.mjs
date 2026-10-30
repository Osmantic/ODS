import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createIntegrationTools, submitIntegration, INTEGRATION_TOOL_NAMES} from '../plugin/integrations.mjs';
import {withPiToolErrorContract} from '../plugin/pi-tool-result.mjs';
import {failedToolOutcome} from '../plugin/run-progress-budget.mjs';

const tool = (tools, name) => tools.find(item => item.name === name);

test('a cancelled request never opens a service connection or submits an action', async () => {
  const controller = new AbortController();
  controller.abort();
  let connected = false;
  await assert.rejects(submitIntegration({action: 'mcp-call', server: 'fixture', tool: 'write', arguments: {}}, {
    platform: 'linux', signal: controller.signal,
    connect: () => { connected = true; throw new Error('A cancelled request reached the service'); },
  }), /Cancelled/);
  assert.equal(connected, false);
});

function recording(replies) {
  const sent = [];
  const submit = async payload => {
    sent.push(payload);
    return replies.shift();
  };
  return {sent, tools: createIntegrationTools({submit})};
}

test('the tools match the declared names and keep schemas small enough for the model grammar', () => {
  const tools = createIntegrationTools();
  assert.deepEqual(tools.map(item => item.name).sort(), [...INTEGRATION_TOOL_NAMES]);
  for (const item of tools) {
    assert.equal(item.parameters.additionalProperties, false);
    assert.doesNotMatch(JSON.stringify(item.parameters), /maxLength|minLength/);
  }
});

test('notes actions become exact service requests and readable results', async () => {
  const {sent, tools} = recording([
    {ok: true, results: [{path: 'Home/Lease.md', line: 2, matched: 2, of: 2, snippet: 'The deposit is two months of rent.'}]},
    {ok: true, path: 'Home/Lease.md', text: 'Notice: 60 days.'},
    {ok: true, written: 'Groceries.md', mode: 'append'},
  ]);
  const notes = tool(tools, 'pixel_ods_notes');
  const search = await notes.execute('1', {action: 'search', query: 'lease deposit'});
  assert.match(search.content[0].text, /Home\/Lease\.md \(line 2, 2\/2 words\): The deposit/);
  assert.match((await notes.execute('2', {action: 'read', path: 'Home/Lease.md'})).content[0].text, /60 days/);
  assert.equal((await notes.execute('3', {action: 'write', path: 'Groceries.md', text: 'bread', mode: 'append'})).content[0].text, 'Added to Groceries.md.');
  assert.deepEqual(sent, [
    {action: 'notes-search', query: 'lease deposit'},
    {action: 'notes-read', path: 'Home/Lease.md'},
    {action: 'notes-write', path: 'Groceries.md', text: 'bread', mode: 'append'},
  ]);
  const wrong = await notes.execute('4', {action: 'delete'});
  assert.equal(wrong.isError, true);
});

test('an action waiting for approval tells the model to stop and repeat the same call later', async () => {
  const {tools} = recording([{ok: true, status: 'approval-required', approvalId: 'abc', summary: 'Home Assistant: unlock {"entity":"lock.front"}'}]);
  const result = await tool(tools, 'pixel_ods_mcp').execute('1', {action: 'call', server: 'home', tool: 'unlock', arguments: {entity: 'lock.front'}});
  assert.equal(result.details.status, 'approval-required');
  assert.notEqual(result.isError, true);
  assert.equal(failedToolOutcome({result}), false);
  assert.match(result.content[0].text, /approval in Settings → Portal integrations/);
  assert.match(result.content[0].text, /scheduled task, do not wait/);
  assert.match(result.content[0].text, /exactly the same arguments/);
});

test('refusals and an absent service are errors with the service message', async () => {
  const {tools} = recording([{ok: false, error: 'notifications-off', message: 'Notifications are not set up.'}]);
  const refused = await tool(tools, 'pixel_ods_notify').execute('1', {message: 'hi'});
  assert.equal(refused.isError, true);
  assert.equal(refused.content[0].text, 'Notifications are not set up.');
  const absent = createIntegrationTools({submit: async () => { throw new Error('The integrations service is not running.'); }});
  const result = await tool(absent, 'pixel_ods_mcp').execute('1', {action: 'list'});
  assert.equal(result.isError, true);
  assert.equal(result.details.status, 'unavailable');
});

test('MCP execution success and failure reach the native Pi and progress error contract', async () => {
  const {tools} = recording([
    {ok: true, text: 'Invented sensor value: 21', isError: false},
    {ok: true, text: 'Invented device is unavailable', isError: true},
  ]);
  // The plugin entry uses this same existing adapter when registering tools.
  const mcp = withPiToolErrorContract(tool(tools, 'pixel_ods_mcp'));
  const params = {action: 'call', server: 'fixture', tool: 'sensor', arguments: {}};
  const success = await mcp.execute('success', params);
  assert.equal(success.content[0].text, 'Invented sensor value: 21');
  assert.equal(success.details.status, 'done');
  assert.notEqual(success.details.ok, false);
  assert.notEqual(success.isError, true);
  assert.equal(failedToolOutcome({result: success}), false);
  const failure = await mcp.execute('failure', params);
  assert.match(failure.content[0].text, /The tool reported an error:\nInvented device is unavailable/);
  assert.equal(failure.details.status, 'failed');
  assert.equal(failure.details.ok, false);
  assert.equal(failure.isError, true);
  assert.equal(failedToolOutcome({result: failure}), true);
});

test('approval stays pending through Pi while transport failures use a fixed non-reflecting fallback', async () => {
  const pending = recording([{ok: true, status: 'approval-required', approvalId: 'fixture-approval', summary: 'Fixture action'}]);
  const mcp = withPiToolErrorContract(tool(pending.tools, 'pixel_ods_mcp'));
  const params = {action: 'call', server: 'fixture', tool: 'action', arguments: {}};
  const waiting = await mcp.execute('waiting', params);
  assert.equal(waiting.details.status, 'approval-required');
  assert.equal(waiting.details.approvalId, 'fixture-approval');
  assert.notEqual(waiting.details.ok, false);
  assert.notEqual(waiting.isError, true);
  const unavailable = createIntegrationTools({submit: async () => { throw new Error('Bearer invented-secret-transport-canary'); }});
  const failure = await withPiToolErrorContract(tool(unavailable, 'pixel_ods_mcp')).execute('transport', params);
  assert.equal(failure.content[0].text, 'The integrations service is unavailable.');
  assert.equal(failure.details.status, 'unavailable');
  assert.equal(failure.details.ok, false);
  assert.equal(failure.isError, true);
  assert.equal(failedToolOutcome({result: failure}), true);
  assert.doesNotMatch(JSON.stringify(failure), /invented-secret|Bearer/);
});

test('mcp list shows servers, tools, arguments and which need approval', async () => {
  const {tools} = recording([{ok: true, servers: [{id: 'home', name: 'Home Assistant', tools: [
    {name: 'HassTurnOn', description: 'Turn on a device', inputSchema: {type: 'object', properties: {name: {type: 'string'}}, required: ['name'], additionalProperties: false}, needsApproval: false},
    {name: 'HassUnlock', description: 'Unlock', inputSchema: {}, needsApproval: true}]}]}]);
  const text = (await tool(tools, 'pixel_ods_mcp').execute('1', {action: 'list'})).content[0].text;
  assert.match(text, /Server "home" \(Home Assistant\)/);
  assert.match(text, /- HassTurnOn: Turn on a device/);
  const schemaLine = text.split('\n').find(line => line.trim().startsWith('arguments:'));
  assert.deepEqual(JSON.parse(schemaLine.slice(schemaLine.indexOf('arguments:') + 'arguments:'.length)),
    {type: 'object', properties: {name: {type: 'string'}}, required: ['name'], additionalProperties: false});
  assert.match(text, /HassUnlock \[needs owner approval\]/);
});

test('unsupported native systems refuse before connecting', async () => {
  for (const platform of ['win32', 'darwin']) {
    let connected = false;
    await assert.rejects(submitIntegration({action: 'mcp-tools'}, {platform,
      connect: () => { connected = true; throw new Error('Unsupported transport opened'); },
    }), /not available|aren't available/);
    assert.equal(connected, false);
  }
});

test('a submitted integration remains admitted until the service settles after cancellation',
  {skip: process.platform === 'win32' ? 'The production Unix socket transport runs inside WSL/Linux.' : false}, async t => {
  const directory = mkdtempSync(join(tmpdir(), 'integrations-drain-'));
  const path = join(directory, 'control.sock');
  const sockets = new Set();
  let admitted;
  const received = new Promise(resolve => { admitted = resolve; });
  let complete;
  const serviceDone = new Promise(resolve => { complete = resolve; });
  const controller = new AbortController();
  const server = net.createServer(socket => {
    sockets.add(socket);
    socket.on('error', () => {});
    socket.on('data', async () => {
      controller.abort();
      admitted();
      await serviceDone;
      socket.end(JSON.stringify({ok: true, sent: true}) + '\n');
    });
  });
  t.after(async () => {
    complete();
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    rmSync(directory, {recursive: true, force: true});
  });
  await new Promise(resolve => server.listen(path, resolve));
  let settled = false, result, error;
  const request = submitIntegration({action: 'notify', message: 'invented QA result'}, {
    platform: 'linux', signal: controller.signal, connect: () => net.createConnection({path}),
  }).then(value => { settled = true; result = value; }, failure => { settled = true; error = failure; });
  await received;
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(settled, false, 'aborting the UI must not release admission while the service action still runs');
  complete();
  await request;
  assert.equal(error, undefined);
  assert.deepEqual(result, {ok: true, sent: true});
});

test('losing the reply after submission preserves an unknown outcome without replaying the action',
  {skip: process.platform === 'win32' ? 'The production Unix socket transport runs inside WSL/Linux.' : false}, async t => {
  const directory = mkdtempSync(join(tmpdir(), 'integrations-lost-reply-'));
  const path = join(directory, 'control.sock');
  let requests = 0;
  const server = net.createServer(socket => {
    socket.on('error', () => {});
    socket.once('data', () => { requests++; socket.destroy(); });
  });
  await new Promise(resolve => server.listen(path, resolve));
  t.after(async () => {
    await new Promise(resolve => server.close(resolve));
    rmSync(directory, {recursive: true, force: true});
  });
  const submit = (payload, options) => submitIntegration(payload, {
    ...options, platform: 'linux', connect: () => net.createConnection({path}),
  });
  const notify = withPiToolErrorContract(tool(createIntegrationTools({submit}), 'pixel_ods_notify'));
  const result = await notify.execute('lost-reply', {message: 'invented QA notification'});
  assert.equal(requests, 1);
  assert.equal(result.details.status, 'outcome-unknown');
  assert.equal(result.details.ok, false);
  assert.equal(result.isError, true);
  assert.equal(failedToolOutcome({result}), true);
  assert.match(result.content[0].text, /was submitted.*could not be verified/);
  assert.match(result.content[0].text, /do not repeat the action automatically/);
  assert.doesNotMatch(result.content[0].text, /Notification sent|unavailable/);
});

test('the Unix socket client sends one JSON line and reads one reply',
  {skip: process.platform === 'win32' ? 'The production Unix socket transport runs inside WSL/Linux.' : false}, async () => {
  const directory = mkdtempSync(join(tmpdir(), 'integrations-'));
  const path = join(directory, 'control.sock');
  let received;
  const server = net.createServer(socket => socket.on('data', chunk => {
    received = JSON.parse(chunk.toString());
    socket.end(JSON.stringify({ok: true, sent: true}) + '\n');
  }));
  await new Promise(resolve => server.listen(path, resolve));
  try {
    const reply = await submitIntegration({action: 'notify', message: 'hi'}, {
      platform: 'linux', connect: () => net.createConnection({path})});
    assert.deepEqual(reply, {ok: true, sent: true});
    assert.deepEqual(received, {schemaVersion: 1, action: 'notify', message: 'hi'});
    await assert.rejects(submitIntegration({action: 'mcp-tools'}, {platform: 'darwin'}), /aren't available on macOS yet/);
  } finally {
    server.close();
    rmSync(directory, {recursive: true, force: true});
  }
});
