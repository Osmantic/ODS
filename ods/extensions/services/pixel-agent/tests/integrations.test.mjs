import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import {EventEmitter} from 'node:events';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createIntegrationTools, submitIntegration, INTEGRATION_TOOL_NAMES} from '../plugin/integrations.mjs';
import {createIntegrationOutcomes} from '../plugin/integration-outcomes.mjs';
import {withPiToolErrorContract} from '../plugin/pi-tool-result.mjs';
import {failedToolOutcome} from '../plugin/run-progress-budget.mjs';
import {PORTAL_INTEGRATIONS_CONTRACT, promptContractForAgent} from '../plugin/prompt-contract.mjs';

const tool = (tools, name) => tools.find(item => item.name === name);
async function admitReviewRead(outcomes,context) {
  const candidate=await outcomes.prepareReviewCandidate({toolName:'pixel_ods_notes',toolCallId:'read',params:{action:'read',path:'a.md'}},context);
  const verdict=await outcomes.checkNativeCandidate(candidate,value=>Object.freeze({candidate:value.ref,admission:'native-review-policy-v1'}));
  assert.equal(outcomes.commitNativeCandidate(candidate,verdict),undefined);
}

test('requireBound is dynamic, strict and fail-closed without changing ordinary optional reads', async () => {
  let required=false,submissions=0;
  const tools=createIntegrationTools({context:{agentId:'pixel'},requireBound:()=>required,
    submit:async()=>{submissions++;return {ok:true,path:'a.md',text:'owned'};}});
  assert.equal((await tool(tools,'pixel_ods_notes').execute('normal',{action:'read',path:'a.md'})).details.status,'done');
  required=true;
  assert.equal((await tool(tools,'pixel_ods_notes').execute('review',{action:'read',path:'a.md'})).details.status,'blocked');
  assert.equal(submissions,1);
  for(const predicate of [true,null,()=>undefined,()=>null,()=>1,()=>{throw new Error('private');}]) {
    const guarded=createIntegrationTools({requireBound:predicate,submit:async()=>{submissions++;return {ok:true,servers:[]};}});
    assert.equal((await tool(guarded,'pixel_ods_mcp').execute('missing',{action:'list'})).details.status,'blocked');
  }
  assert.equal(submissions,1);
});

test('bound review reads reject malformed or approval replies and record transport uncertainty without inventing pending', async () => {
  const context={agentId:'pixel',runId:'owned',sessionId:'owned-session',sessionKey:'agent:pixel:owned'};
  for(const reply of [
    null,{},[],
    {ok:true,path:'a.md',text:'owned'},
    {ok:true,recoveryReviewOnly:true,status:'approval-required',approvalId:'forged',actionExecuted:false,approvalConsumed:false},
    {ok:true,recoveryReviewOnly:true,path:'a.md'},
  ]) {
    const outcomes=createIntegrationOutcomes();outcomes.begin({},context);
    await admitReviewRead(outcomes,context);
    const tools=createIntegrationTools({context,outcomes,requireBound:()=>true,submit:async()=>reply});
    assert.equal((await tool(tools,'pixel_ods_notes').execute('read',{action:'read',path:'a.md'})).details.status,'outcome-unknown');
    assert.match(outcomes.projection(context.runId),/Read an Obsidian note/);
    assert.doesNotMatch(outcomes.projection(context.runId),/requested approval/);
  }
  const outcomes=createIntegrationOutcomes();outcomes.begin({},context);
  await admitReviewRead(outcomes,context);
  const tools=createIntegrationTools({context,outcomes,requireBound:()=>true,submit:async()=>{
    throw Object.assign(new Error('private'),{code:'integration-outcome-unknown'});
  }});
  assert.equal((await tool(tools,'pixel_ods_notes').execute('read',{action:'read',path:'a.md'})).details.status,'outcome-unknown');
  assert.match(outcomes.projection(context.runId),/Read an Obsidian note/);
});

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

test('review native admission is checked both before connecting and before writing a connected socket',async()=>{
  let connects=0,writes=0,destroyed=0,checks=0;
  const connect=()=>{
    connects++;const socket=new EventEmitter();
    socket.write=()=>{writes++;};socket.destroy=()=>{destroyed++;};
    queueMicrotask(()=>socket.emit('connect'));return socket;
  };
  await assert.rejects(submitIntegration({action:'notes-read',path:'a.md'},
    {platform:'linux',connect,admitted:()=>false}),{code:'integration-call-unbound'});
  assert.equal(connects,0);
  await assert.rejects(submitIntegration({action:'notes-read',path:'a.md'},
    {platform:'linux',connect,admitted:()=>++checks===1}),{code:'integration-call-unbound'});
  assert.equal(connects,1);assert.equal(writes,0);assert.equal(destroyed,1);assert.equal(checks,2);
});

function recording(replies) {
  const sent = [];
  const submit = async payload => {
    sent.push(payload);
    return replies.shift();
  };
  return {sent, tools: createIntegrationTools({submit})};
}

test('digest snapshots carry only native run identity, never identities supplied by the model', async () => {
  const sent = [], context = {agentId: 'pixel', sessionId: 'native-session', sessionKey: 'agent:pixel:cron:owned:run:1'};
  const tools = createIntegrationTools({context, submit: async request => {
    sent.push(request); return {ok:true, text:'Invented news'};
  }});
  const mcp = tool(tools, 'pixel_ods_mcp');
  await mcp.execute('digest', {action:'call',server:'app:news',tool:'digest',arguments:{},
    runContext:{sessionId:'model-forged',sessionKey:'forged'}});
  assert.deepEqual(sent[0].runContext, {sessionId:'native-session',sessionKey:'agent:pixel:cron:owned:run:1'});
  assert.deepEqual(sent[0].arguments, {});
  for (const server of ['news','home','app:photos']) {
    await mcp.execute(server, {action:'call',server,tool:'digest',arguments:{}});
    assert.equal(Object.hasOwn(sent.at(-1),'runContext'), false);
  }
  for (const invalid of [undefined, {...context,agentId:'other'}, {...context,sessionId:''},
    {...context,sessionKey:'agent:other:cron:1'}, {...context,sessionKey:'agent:pixel:cron:\nforged'}]) {
    const items = createIntegrationTools({context:invalid,submit:async request=>{
      assert.equal(Object.hasOwn(request,'runContext'), false);return {ok:true,text:'no identity'};
    }});
    await tool(items,'pixel_ods_mcp').execute('missing', {action:'call',server:'app:news',tool:'digest',arguments:{}});
  }
  assert.equal(Object.hasOwn(mcp.parameters.properties,'runContext'), false);
});

test('the tools match the declared names and keep schemas small enough for the model grammar', () => {
  const tools = createIntegrationTools();
  assert.deepEqual(tools.map(item => item.name).sort(), [...INTEGRATION_TOOL_NAMES]);
  for (const item of tools) {
    assert.equal(item.parameters.additionalProperties, false);
    assert.doesNotMatch(JSON.stringify(item.parameters), /maxLength|minLength/);
  }
  // The owner's apps add no tools of their own; the model finds them in the MCP tool.
  assert.match(tools.find(item => item.name === 'pixel_ods_mcp').description, /documents.*photos.*watched web pages.*news/);
});

test('explicit approval requests cannot fall back to an executable write', async () => {
  const call = {action:'request_approval',path:'Test.md',text:'line',mode:'append'};
  const receipt = {ok:true,status:'approval-required',approvalId:'native-id',proposalOnly:true,actionExecuted:false,approvalConsumed:false};
  for (const [reply, status] of [[receipt,'approval-required'], [{...receipt,status:'approval-ready'},'approval-ready'],
    [{...receipt,actionExecuted:true},'outcome-unknown'], [{...receipt,approvalId:''},'outcome-unknown'],
    [{ok:true,written:'Test.md',mode:'append'},'outcome-unknown']]) {
    const {tools,sent} = recording([reply]);
    const result = await tool(tools,'pixel_ods_notes').execute('1',call);
    assert.equal(result.details.status,status);
    assert.deepEqual(sent,[{action:'notes-request-approval',path:'Test.md',text:'line',mode:'append'}]);
  }
  const {tools,sent} = recording([]);
  const result = await tool(tools,'pixel_ods_notes').execute('2',{...call,mode:'other'});
  assert.equal(result.details.status,'invalid');
  assert.deepEqual(sent,[]);
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

test('a call without arguments shows the model how to call again', async () => {
  const {sent, tools} = recording([]);
  for (const params of [undefined, {}, {action: 'delete'}]) {
    const result = await tool(tools, 'pixel_ods_mcp').execute('1', params);
    assert.equal(result.isError, true);
    assert.equal(result.details.status, 'invalid');
    assert.match(result.content[0].text, /\{"action": "list"\}/);
    assert.match(result.content[0].text, /"action": "call", "server": "app:photos", "tool": "search", "arguments": \{"place": "Lisbon"\}/);
  }
  assert.deepEqual(sent, []);
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

test('unknown MCP identifiers retain refusal while explaining the separate native discovery namespace',async()=>{
  for(const error of ['server-missing','tool-not-allowed']){
    const {tools,sent}=recording([{ok:false,error,message:'The requested MCP capability is unavailable.'}]);
    const params={action:'call',server:'invented-account-provider',tool:'count',arguments:{date:'2026-11-04'}};
    const original=JSON.stringify(params);
    const result=await withPiToolErrorContract(tool(tools,'pixel_ods_mcp')).execute('missing',params);
    assert.equal(result.isError,true);
    assert.deepEqual(result.details,{status:'refused',code:error,catalogScope:'mcp',recordCountEstablished:false,permissionBypassAllowed:false,ok:false});
    assert.match(result.content[0].text,/This refusal establishes no application read, record count or account status/);
    assert.match(result.content[0].text,/only the server\/tool IDs and argument schemas returned by this tool's action list/);
    assert.match(result.content[0].text,/Respect disabled server\/tool policies/);
    assert.match(result.content[0].text,/grants no permission and does not authorize an automatic retry of the refused MCP action/);
    if(error==='server-missing'){
      assert.match(result.content[0].text,/Native account\/provider tools .* are outside this MCP catalog/);
      assert.match(result.content[0].text,/tool_search.*tool_describe.*tool_call/);
      assert.match(result.content[0].text,/Use native tools only under their own normal authorization for the original owner request/);
    }else assert.doesNotMatch(result.content[0].text,/tool_search|tool_describe|tool_call/,'an Off/tool refusal does not suggest native fallback');
    assert.equal(JSON.stringify(params),original,'metadata cannot rewrite the attempted action');
    assert.deepEqual(sent,[{action:'mcp-call',server:params.server,tool:params.tool,arguments:params.arguments}],
      'no automatic lookup, fallback provider call or retry follows a refusal');
  }
});

test('a tool Off refusal remains authoritative and cannot trigger discovery or another provider',async()=>{
  const {tools,sent}=recording([{ok:false,error:'tool-not-allowed',message:'This server has no allowed tool with that name.'}]);
  const result=await tool(tools,'pixel_ods_mcp').execute('off',{action:'call',server:'configured',tool:'off-action',arguments:{}});
  assert.equal(result.isError,true);
  assert.equal(result.details.status,'refused');
  assert.equal(result.details.code,'tool-not-allowed');
  assert.equal(result.details.permissionBypassAllowed,false);
  assert.doesNotMatch(result.content[0].text,/tool_search|tool_describe|tool_call/);
  assert.equal(sent.length,1,'no automatic retry, discovery or alternate provider dispatch');
});

test('namespace guidance does not reroute or forbid a configured custom account server',async()=>{
  const {tools,sent}=recording([{ok:true,text:'Invented configured account count: 2'}]);
  const params={action:'call',server:'google-calendar',tool:'count',arguments:{window:'invented'}};
  const result=await tool(tools,'pixel_ods_mcp').execute('configured',params);
  assert.notEqual(result.isError,true);
  assert.equal(result.details.status,'done');
  assert.deepEqual(sent,[{action:'mcp-call',server:params.server,tool:params.tool,arguments:params.arguments}]);
  assert.doesNotMatch(result.content[0].text,/tool_search|record count/,'successful configured tools keep their normal result');
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

test('MCP catalogs never imply absence of the separate native notification connection', async () => {
  for (const servers of [[], [{id:'fixture', name:'Fixture', tools:[]}]]) {
    const {tools} = recording([{ok:true, servers}]);
    const reply = await tool(tools, 'pixel_ods_mcp').execute('catalog', {action:'list'});
    assert.match(reply.content[0].text, /configured Documents, Photos, Watched pages and News apps, plus custom MCP servers/);
    assert.match(reply.content[0].text, /ntfy notifications use pixel_ods_notify/);
    assert.match(reply.content[0].text, /connection state is not reported here/);
    assert.match(reply.content[0].text,/only the server\/tool IDs and argument schemas returned by this tool's action list/);
    assert.match(reply.content[0].text,/Native account\/provider tools .* are outside this MCP catalog/);
    assert.match(reply.content[0].text,/tool_search.*tool_describe.*tool_call/);
    assert.doesNotMatch(reply.content[0].text,/Google uses the connected-app tools/,'avoid the ambiguous former namespace');
    assert.notEqual(reply.isError, true);
  }
});

test('an unavailable notes connection does not diagnose documents or dispatch another action', async () => {
  const {sent, tools} = recording([{ok:false, error:'notes-off', message:'Notes are not set up.'}]);
  const reply = await tool(tools,'pixel_ods_notes').execute('read',{action:'read',path:'Example.md'});
  assert.equal(reply.isError, true);
  assert.deepEqual(reply.details, {status:'refused',code:'notes-off'});
  assert.match(reply.content[0].text, /only Obsidian notes, not the Paperless document connection/);
  assert.deepEqual(sent, [{action:'notes-read',path:'Example.md'}]);
});

test('integration capability guidance is stable across requests and model context profiles', () => {
  for (const contextTokenBudget of [8192, 16384, 65536]) {
    for (const prompt of ['Send a test notification.', 'What can the connected house do?', 'Olá!']) {
      const rendered = promptContractForAgent({agentId:'pixel', contextTokenBudget}, 'pixel', {prompt}).appendSystemContext;
      assert.equal(rendered.split(PORTAL_INTEGRATIONS_CONTRACT).length, 2);
      assert.match(rendered, /Obsidian: pixel_ods_notes/);
      assert.match(rendered, /Paperless docs\/PDFs, Immich photos, watches\/news\/MCP: pixel_ods_mcp \(list first; no Google\/notes\/ntfy status\)/);
      assert.match(rendered, /ntfy: pixel_ods_notify/);
      assert.match(rendered, /Workspace writes aren't vault updates/);
    }
  }
  const other = promptContractForAgent({agentId:'unrelated'}, 'pixel', {prompt:'Send a notification.'});
  assert.ok(!other?.appendSystemContext?.includes(PORTAL_INTEGRATIONS_CONTRACT));
});

test('unsupported native systems refuse before connecting', async () => {
  for (const platform of ['win32', 'freebsd']) {
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
    for (const platform of ['linux', 'darwin']) {
      const reply = await submitIntegration({action: 'notify', message: 'hi'}, {
        platform, connect: options => {
          assert.equal(options.path, platform === 'darwin'
            ? '/private/var/lib/ods-pixel-integrations/control.sock' : '/run/ods-pixel-integrations/control.sock');
          return net.createConnection({path});
        }});
      assert.deepEqual(reply, {ok: true, sent: true});
      assert.deepEqual(received, {schemaVersion: 1, action: 'notify', message: 'hi'});
    }
  } finally {
    server.close();
    rmSync(directory, {recursive: true, force: true});
  }
});
