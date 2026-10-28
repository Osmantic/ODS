import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createIntegrationTools, submitIntegration, INTEGRATION_TOOL_NAMES} from '../plugin/integrations.mjs';

const tool = (tools, name) => tools.find(item => item.name === name);

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
  assert.match(result.content[0].text, /approval in Settings → Portal integrations/);
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

test('mcp list shows servers, tools, arguments and which need approval', async () => {
  const {tools} = recording([{ok: true, servers: [{id: 'home', name: 'Home Assistant', tools: [
    {name: 'HassTurnOn', description: 'Turn on a device', inputSchema: {properties: {name: {type: 'string'}}}, needsApproval: false},
    {name: 'HassUnlock', description: 'Unlock', inputSchema: {}, needsApproval: true}]}]}]);
  const text = (await tool(tools, 'pixel_ods_mcp').execute('1', {action: 'list'})).content[0].text;
  assert.match(text, /Server "home" \(Home Assistant\)/);
  assert.match(text, /- HassTurnOn: Turn on a device\n {2}arguments: \{"name":\{"type":"string"\}\}/);
  assert.match(text, /HassUnlock \[needs owner approval\]/);
});

test('the socket client sends one JSON line and reads one reply', async () => {
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
    await assert.rejects(submitIntegration({action: 'mcp-tools'}, {platform: 'darwin'}), /not available on this system/);
  } finally {
    server.close();
    rmSync(directory, {recursive: true, force: true});
  }
});
