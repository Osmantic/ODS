import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {createSourceProposalTool, createExtensionProposalTool, createPythonLibraryProposalTool, createExtensionRequestStatusTool, createExtensionRequestPrepareTool, createExtensionRequestAdvanceTool, createExtensionRequestRetryTool, submitExtensionProposal} from '../plugin/extension-proposal.mjs';

const context = {agentId: 'pixel', sessionKey: 'agent:pixel:openai-user:ods-' + createHash('sha256').update('chat').digest('hex')};
const args = {chatId: 'chat', requestId: 'turn', candidate: {repository: 'https://github.com/o/r',
  commit: 'a'.repeat(40), manifest: {service: {id: 'example'}}, compose: {services: {}}}};
const receipt = {schemaVersion: 1, kind: 'ods-extension-request-proposal', chatId: 'chat', requestId: 'turn',
  state: 'pending', installationStarted: false, proposal: {draftId: 'b'.repeat(64), recipeDigest: 'c'.repeat(64), extensionId: 'example'}};

for (const [factory, input] of [
  [createExtensionProposalTool,{candidate:args.candidate}],
  [createPythonLibraryProposalTool,{repository:'https://github.com/o/r',commit:'a'.repeat(40),
    serviceId:'example',name:'Example',pythonVersion:'3.12',pythonImports:['example']}],
]) test(`${factory.name} resolves proposal identity without model routing fields`,async()=>{
  const calls=[];
  let request={chatId:'chat',requestId:'turn'};
  const tool=factory(context,{submit:async payload=>{
    calls.push(payload);
    return payload.action==='github-request-resolve'
      ? {schemaVersion:1,kind:'ods-extension-request-scope',sessionHash:context.sessionKey.split('ods-')[1],request}
      : receipt;
  }});
  assert.equal(tool.parameters.properties.chatId,undefined);
  assert.equal(tool.parameters.properties.requestId,undefined);
  assert.equal((await tool.execute('proposal',input)).isError,undefined);
  assert.deepEqual(calls.map(x=>x.action),['github-request-resolve','github-request-propose','github-request-status']);
  assert.equal(calls[1].action,'github-request-propose');
  assert.equal(calls[1].chatId,'chat');
  assert.equal(calls[1].requestId,'turn');
  assert.equal(calls[1].candidate.repository,input.repository ?? input.candidate.repository);
  for (const scope of [null,{chatId:'foreign',requestId:'turn'}]) {
    request=scope; calls.length=0;
    assert.equal((await tool.execute('proposal',input)).isError,true);
    assert.equal(calls.length,1,'no draft submission without verified session scope');
  }
});

for (const [factory, action] of [[createExtensionRequestStatusTool,'status'],
  [createExtensionRequestPrepareTool,'prepare'], [createExtensionRequestAdvanceTool,'advance']]) {
  test(`${action} binds empty arguments to the trusted session and rejects foreign scope`, async () => {
    const sessionHash=createHash('sha256').update('chat').digest('hex');
    let request={chatId:'chat',requestId:'original'};
    const calls=[];
    const tool=factory(context,{submit:async payload=>{
      calls.push(payload);
      if(payload.action==='github-request-resolve') return {schemaVersion:1,
        kind:'ods-extension-request-scope',sessionHash,request};
      return {}; // Receipt validation remains independent of scope resolution.
    }});
    assert.deepEqual(Object.keys(tool.parameters.properties),action==='prepare' ? ['extensionId'] : []);
    await tool.execute('turn',{});
    assert.deepEqual(calls,[{schemaVersion:1,action:'github-request-resolve',sessionHash},
      {schemaVersion:1,action:`github-request-${action}`,chatId:'chat',requestId:'original'}]);
    for (const invalid of [null,{chatId:'another-chat',requestId:'original'},
      {chatId:'chat',requestId:'original',extra:true}]) {
      request=invalid; calls.length=0;
      assert.equal((await tool.execute('turn',{})).isError,true);
      assert.equal(calls.length,1,'no downstream operation for absent or invalid scope');
    }
  });
}

test('request status is owner-bound, read-only and never promotes missing evidence to success', async () => {
  const value={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
    requestState:'pending',proposalAccepted:true,prepared:false,extensionId:'example',runtimeStatus:'not_observed'};
  const calls=[];
  const tool=createExtensionRequestStatusTool(context,{submit:async payload=>{calls.push(payload);return value;}});
  assert.equal(createExtensionRequestStatusTool({...context,agentId:'other'}),null);
  assert.equal((await tool.execute('id',{chatId:'other',requestId:'turn'})).isError,true);
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn',action:'install'})).isError,true);
  assert.equal(calls.length,0);
  assert.deepEqual((await tool.execute('id',{chatId:'chat',requestId:'turn'})).details,value);
  const pending = await tool.execute('id',{chatId:'chat',requestId:'turn'});
  assert.match(JSON.parse(pending.content[1].text).next, /pixel_ods_extension_request_prepare/);
  assert.equal(calls[0].action,'github-request-status');
  value.runtimeStatus='enabled';
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
  value.prepared=true;
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).details.runtimeStatus,'enabled');
  value.runtimeStatus='error'; value.runtimeError='Build failed: missing pyproject.toml';
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).details.runtimeError,value.runtimeError);
  const failure=await tool.execute('id',{chatId:'chat',requestId:'turn'});
  assert.equal(JSON.parse(failure.content[1].text).serviceId,'example');
  assert.match(JSON.parse(failure.content[1].text).workspaceScope,/do not modify the managed extension recipe/);
  value.requestState='cancelled';
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).content.length,1);
  value.requestState='pending';
  value.runtimeError='Build output: '+ 'x'.repeat(7000);
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).details.runtimeError,value.runtimeError);
  for (const error of ['', null, 42, 'x'.repeat(8193)]) {
    value.runtimeError=error;
    assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
  }
  value.runtimeError='Build failed'; value.runtimeStatus='enabled';
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
  delete value.runtimeError;
  value.requestId='other';
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
});

test('status separates an active request record from verified installation readiness', async () => {
  const value={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
    requestState:'pending',authorizationMode:'install',proposalAccepted:true,integrationBound:false,
    prepared:true,extensionId:'example',runtimeStatus:'cli_installed',installationVerified:true};
  const tool=createExtensionRequestStatusTool(context,{submit:async()=>value});
  const observed=await tool.execute('id',{chatId:'chat',requestId:'turn'});
  assert.equal(observed.isError,undefined);
  assert.equal(observed.details.requestState,'pending');
  assert.equal(observed.details.installationVerified,true);
  assert.equal(observed.content.length,1);
  const visible=JSON.parse(observed.content[0].text);
  assert.equal(Object.hasOwn(visible,'requestState'),false);
  assert.equal(visible.requestRecordState,'active');
  assert.equal(visible.installationState,'verified_installed');
  assert.equal(visible.installationVerified,true);
  assert.equal(visible.runtimeStatus,'cli_installed');
  assert.equal(visible.scope,'managed-runtime-readiness');
  value.requestState='cancelled';value.installationVerified=false;value.prepared=false;value.runtimeStatus='not_observed';
  const closed=await tool.execute('id',{chatId:'chat',requestId:'turn'});
  assert.equal(JSON.parse(closed.content[0].text).requestRecordState,'cancelled');
  value.requestState='pending';value.prepared=true;value.runtimeStatus='cli_installed';
  value.installationVerified=false;
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
  value.installationVerified=true; value.runtimeStatus='installing';
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
});

test('saved research scope cannot be mistaken for installation authority', async () => {
  const sessionHash=context.sessionKey.split('ods-')[1];
  const value={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
    requestState:'pending',authorizationMode:'research',proposalAccepted:false,prepared:false,
    extensionId:null,runtimeStatus:'not_observed',existingExtensionIds:[]};
  const tool=createExtensionRequestStatusTool(context,{submit:async payload=>
    payload.action==='github-request-resolve'
      ? {schemaVersion:1,kind:'ods-extension-request-scope',sessionHash,
        request:{chatId:'chat',requestId:'turn'},authorizationMode:'research'} : value});
  const result=await tool.execute('id',{});
  assert.equal(result.details.authorizationMode,'research');
  assert.match(JSON.parse(result.content[1].text).next,/does not authorize preparing or installing/);
  value.authorizationMode='install';
  assert.match(JSON.parse((await tool.execute('id',{})).content[1].text).next,/pixel_ods_python_library_proposal/);
  value.authorizationMode='unknown';
  assert.equal((await tool.execute('id',{})).isError,true);
});

test('advance rejection names missing preparation only after a matching status read', async () => {
  const status={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
    requestState:'pending',proposalAccepted:true,prepared:false,extensionId:'example',
    runtimeStatus:'not_observed',existingExtensionIds:[],integrationBound:false};
  const calls=[];
  const tool=createExtensionRequestAdvanceTool(context,{submit:async payload=>{
    calls.push(payload.action);
    if (payload.action==='github-request-advance') return {
      kind:'ods-pixel-extension-lifecycle',outcome:'failed',externalEffectOccurred:false,
    };
    if (payload.action==='github-request-resolve') return {schemaVersion:1,
      kind:'ods-extension-request-scope',sessionHash:context.sessionKey.split('ods-')[1],
      request:{chatId:'chat',requestId:'turn'}};
    if (payload.action==='github-request-status') return status;
    throw Error('Unexpected action');
  }});
  const result=await tool.execute('id',{chatId:'chat',requestId:'turn'});
  assert.equal(result.isError,true);
  assert.deepEqual(calls,['github-request-advance','github-request-status']);
  assert.equal(JSON.parse(result.content[0].text).installationStarted,false);
  assert.match(JSON.parse(result.content[0].text).next,/pixel_ods_extension_request_prepare/);
});

test('advance without a proposal reports the verified prerequisite, not an uncertain host operation', async () => {
  const status={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
    requestState:'pending',authorizationMode:'install',proposalAccepted:false,prepared:false,
    extensionId:null,runtimeStatus:'not_observed',existingExtensionIds:[],integrationBound:false};
  const failed={kind:'ods-pixel-extension-lifecycle',outcome:'failed',externalEffectOccurred:false};
  const calls=[];
  let value=status;
  const submit=async payload=>{calls.push(payload.action);
    if(payload.action==='github-request-advance') return failed;
    if(payload.action==='github-request-status') return value;
    throw Error('Unexpected action');
  };
  const tool=createExtensionRequestAdvanceTool(context,{submit});
  const result=await tool.execute('id',{chatId:'chat',requestId:'turn'});
  assert.deepEqual(calls,['github-request-advance','github-request-status']);
  assert.equal(result.isError,true);
  assert.equal(result.details.reason,'proposal_required');
  assert.equal(result.details.installationStarted,false);
  assert.match(result.details.next,/pixel_ods_python_library_proposal/);
  assert.doesNotMatch(result.content[0].text,/outcome is unconfirmed/);

  value={...status,existingExtensionIds:['existing']};
  const existing=await tool.execute('id',{chatId:'chat',requestId:'turn'});
  assert.equal(existing.details.reason,'integration_preparation_required');
  assert.deepEqual(existing.details.existingExtensionIds,['existing']);
  assert.match(existing.details.next,/pixel_ods_extension_request_prepare/);

  value={...status,authorizationMode:'research'};
  const research=await tool.execute('id',{chatId:'chat',requestId:'turn'});
  assert.equal(research.details.reason,'installation_not_authorized');
  assert.match(research.details.next,/\/extensions install https:\/\/github\.com\/OWNER\/REPO/);
});

test('advance preserves uncertainty when a host effect or matching no-work receipt is not proven', async () => {
  const status={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
    requestState:'pending',authorizationMode:'install',proposalAccepted:false,prepared:false,
    extensionId:null,runtimeStatus:'not_observed',existingExtensionIds:[],integrationBound:false};
  let lifecycle={kind:'ods-pixel-extension-lifecycle',outcome:'failed',externalEffectOccurred:false};
  let observed=status;
  const tool=createExtensionRequestAdvanceTool(context,{submit:async payload=>
    payload.action==='github-request-advance' ? lifecycle : observed});
  for (const changed of [
    {requestId:'different'}, {prepared:true,extensionId:'example'},
    {runtimeStatus:'installing'}, {requestState:'expired'}, {authorizationMode:undefined},
  ]) {
    observed={...status,...changed};
    const result=await tool.execute('id',{chatId:'chat',requestId:'turn'});
    assert.equal(result.isError,true);
    assert.equal(result.details,undefined);
    assert.match(result.content[0].text,/outcome is unconfirmed/);
  }
  observed=status;
  lifecycle={...lifecycle,externalEffectOccurred:true};
  const result=await tool.execute('id',{chatId:'chat',requestId:'turn'});
  assert.equal(result.details,undefined);
  assert.match(result.content[0].text,/outcome is unconfirmed/);
});

test('preparation uses only the bound request and does not claim an installation', async () => {
  const value={schemaVersion:1,kind:'ods-extension-request-preparation',chatId:'chat',requestId:'turn',
    draftId:'a'.repeat(64),recipeDigest:'b'.repeat(64),extensionId:'example',state:'available',
    installationStarted:false,registered:false,runtimeVerified:false};
  const calls=[];
  const tool=createExtensionRequestPrepareTool(context,{submit:async payload=>{calls.push(payload);return value;}});
  assert.equal(createExtensionRequestPrepareTool({...context,agentId:'other'}),null);
  for (const input of [{chatId:'other',requestId:'turn'},{chatId:'chat',requestId:'turn',repository:'https://github.com/other/repo'}]) {
    assert.equal((await tool.execute('id',input)).isError,true);
  }
  assert.equal(calls.length,0);
  assert.deepEqual((await tool.execute('id',{chatId:'chat',requestId:'turn'})).details,value);
  assert.deepEqual(calls[0],{schemaVersion:1,action:'github-request-prepare',chatId:'chat',requestId:'turn'});
  for (const change of [{runtimeVerified:true},{requestId:'other'},{recipeDigest:'bad'}]) {
    const invalid=createExtensionRequestPrepareTool(context,{submit:async()=>({...value,...change})});
    assert.equal((await invalid.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
  }
});

test('an explicitly authorized accepted proposal advances by verified managed receipts', async () => {
  const mode = {value:'install'};
  const actions=[];
  const status={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
    authorizationMode:'install',requestState:'pending',proposalAccepted:true,prepared:false,
    extensionId:'example',runtimeStatus:'not_observed',existingExtensionIds:[],integrationBound:false};
  const preparation={schemaVersion:1,kind:'ods-extension-request-preparation',chatId:'chat',requestId:'turn',
    draftId:receipt.proposal.draftId,recipeDigest:receipt.proposal.recipeDigest,extensionId:'example',
    state:'available',installationStarted:false,registered:false,runtimeVerified:false};
  const installation={schemaVersion:1,kind:'ods-extension-request-installation',chatId:'chat',requestId:'turn',
    extensionId:'example',state:'pending',activeExtensionId:'example',operationId:'a'.repeat(32),dispatched:true};
  const tool=createExtensionProposalTool(context,{submit:async payload=>{
    actions.push(payload.action);
    if (payload.action==='github-request-propose') return receipt;
    if (payload.action==='github-request-status') return {...status,authorizationMode:mode.value};
    if (payload.action==='github-request-prepare') return preparation;
    if (payload.action==='github-request-advance') return installation;
    throw Error('Unexpected action');
  }});
  const pending=await tool.execute('id',args);
  assert.equal(pending.isError,undefined);
  assert.deepEqual(actions,['github-request-propose','github-request-status','github-request-prepare','github-request-advance']);
  assert.equal(pending.details.state,'pending');
  assert.equal(pending.details.authorizationMode,'install');
  assert.match(pending.content.at(-1).text,/not yet verified complete/);
  mode.value='research'; actions.length=0;
  const research=await tool.execute('id',args);
  assert.equal(research.isError,undefined);
  assert.deepEqual(actions,['github-request-propose','github-request-status']);
  assert.equal(JSON.parse(research.content[0].text).installationStarted,false);
  assert.equal(research.details?.authorizationMode,undefined);
});

test('accepted proposal never claims installation when preparation has no matching receipt', async () => {
  const actions=[];
  const tool=createExtensionProposalTool(context,{submit:async payload=>{
    actions.push(payload.action);
    if (payload.action==='github-request-propose') return receipt;
    if (payload.action==='github-request-status') return {schemaVersion:1,kind:'ods-extension-request-status',
      chatId:'chat',requestId:'turn',authorizationMode:'install',requestState:'pending',
      proposalAccepted:true,prepared:false,extensionId:'example',runtimeStatus:'not_observed'};
    if (payload.action==='github-request-prepare') return {kind:'unknown'};
    throw Error('Advance must not follow uncertain preparation');
  }});
  const blocked=await tool.execute('id',args);
  assert.equal(blocked.isError,true);
  assert.deepEqual(actions,['github-request-propose','github-request-status','github-request-prepare']);
  assert.match(blocked.content.at(-1).text,/No installation success/);
});

test('accepted proposal carries a verified preparation blocker and never advances it', async () => {
  const actions=[];
  const rejection={schemaVersion:1,kind:'ods-extension-request-preparation-rejected',
    chatId:'chat',requestId:'turn',reason:'license_review_required',installationStarted:false};
  const tool=createExtensionProposalTool(context,{submit:async payload=>{
    actions.push(payload.action);
    if (payload.action==='github-request-propose') return receipt;
    if (payload.action==='github-request-status') return {schemaVersion:1,kind:'ods-extension-request-status',
      chatId:'chat',requestId:'turn',authorizationMode:'install',requestState:'pending',
      proposalAccepted:true,prepared:false,extensionId:'example',runtimeStatus:'not_observed'};
    if (payload.action==='github-request-prepare') return rejection;
    throw Error('Advance must not follow a preparation blocker');
  }});
  const blocked=await tool.execute('id',args);
  assert.equal(blocked.isError,true);
  assert.deepEqual(blocked.details,rejection);
  assert.deepEqual(actions,['github-request-propose','github-request-status','github-request-prepare']);
  assert.match(blocked.content.at(-1).text,/Stop this installation attempt/);
});

test('only Portal sessions can submit proposals for their own conversation', async () => {
  let calls = 0;
  const tool = createExtensionProposalTool(context, {submit: async () => {calls++; return receipt;}});
  assert.equal(createExtensionProposalTool({...context, agentId: 'other'}), null);
  assert.equal(createExtensionProposalTool({...context, sessionKey: 'main'}), null);
  assert.equal((await tool.execute('id', {...args, chatId: 'other-chat'})).isError, true);
  assert.equal((await tool.execute('id', {...args, command: 'install'})).isError, true);
  assert.equal(calls, 0);
  const result = await tool.execute('id', args);
  assert.equal(result.isError, undefined);
  assert.equal(JSON.parse(result.content[0].text).state, 'draft');
  assert.equal(calls, 2);
});

test('flat Python library proposals keep identical owner binding and immutable source checks', async () => {
  let submitted;
  const tool = createPythonLibraryProposalTool(context, {submit:async value => {if(value.action==='github-request-propose')submitted=value; return receipt;}});
  const input = {chatId:'chat',requestId:'turn',repository:'https://github.com/o/r',commit:'a'.repeat(40),
    serviceId:'example',name:'Example',pythonVersion:'3.12',pythonImports:['actual_package']};
  assert.equal(createPythonLibraryProposalTool({...context,agentId:'other'}),null);
  assert.equal((await tool.execute('one',{...input,command:['invented']})).isError,true);
  assert.equal((await tool.execute('one',{...input,chatId:'other'})).isError,true);
  assert.equal(submitted,undefined);
  assert.equal((await tool.execute('one',input)).isError,undefined);
  assert.equal(submitted.action,'github-request-propose');
  assert.equal(submitted.candidate.compose.services.example.build.context,'https://github.com/o/r.git#'+'a'.repeat(40));
  assert.equal(submitted.candidate.manifest.service.startup_check,false);
  assert.match(submitted.candidate.compose.services.example.command[2], /importlib.import_module/);
  assert.equal(tool.parameters.properties.command,undefined);
  assert.equal(tool.parameters.properties.source,undefined);
});

test('small-model HEAD or omitted commit is pinned from the saved request before proposal submission', async () => {
  const pinned = 'd'.repeat(40);
  const base = {repository:'https://github.com/o/r',serviceId:'example',name:'Example',
    pythonVersion:'3.12',pythonImports:['example']};
  const actions=[];
  const pin = {schemaVersion:1,kind:'ods-extension-request-commit',chatId:'chat',requestId:'turn',
    repository:base.repository,commit:pinned,evidenceScope:'repository-default-branch-at-inspection',
    installationStarted:false};
  let pinResult=pin;
  const tool=createPythonLibraryProposalTool(context,{submit:async payload=>{
    actions.push(payload);
    if (payload.action==='github-request-resolve') return {schemaVersion:1,
      kind:'ods-extension-request-scope',sessionHash:context.sessionKey.split('ods-')[1],
      request:{chatId:'chat',requestId:'turn'}};
    if (payload.action==='github-request-pin') return pinResult;
    return receipt;
  }});
  assert.equal(tool.parameters.required.includes('commit'),false);
  for (const input of [base,{...base,commit:'HEAD'}]) {
    actions.length=0;
    const proposed = await tool.execute('id',input);
    assert.equal(proposed.isError,undefined);
    assert.deepEqual(actions.map(x=>x.action),['github-request-resolve','github-request-pin',
      'github-request-propose','github-request-status']);
    assert.equal(actions[2].candidate.commit,pinned);
    assert.deepEqual(JSON.parse(proposed.content[0].text).pinnedSource,
      {repository:base.repository,commit:pinned});
    assert.equal(actions[2].candidate.compose.services.example.build.context,
      'https://github.com/o/r.git#'+pinned);
  }
  actions.length=0;
  assert.equal((await tool.execute('id',{...base,commit:'a'.repeat(40)})).isError,undefined);
  assert.deepEqual(actions.map(x=>x.action),['github-request-resolve',
    'github-request-propose','github-request-status']);

  for (const changed of [{repository:'https://github.com/other/repo'},
    {commit:'HEAD'}, {chatId:'other'}, {installationStarted:true},
    {evidenceScope:'unverified'}, {secret:'unexpected'}]) {
    pinResult={...pin,...changed}; actions.length=0;
    const result=await tool.execute('id',{...base,commit:'HEAD'});
    assert.equal(result.isError,true);
    assert.deepEqual(actions.map(x=>x.action),['github-request-resolve','github-request-pin']);
  }
});

test('rejects large proposals and ambiguous or changed receipts without echoing errors', async () => {
  const large = {...args, candidate: {...args.candidate, compose: {data: 'x'.repeat(32768)}}};
  const unavailable = createExtensionProposalTool(context, {submit: async () => {throw Error('private-token');}});
  assert.equal((await unavailable.execute('id', large)).isError, true);
  assert.ok(!JSON.stringify(await unavailable.execute('id', args)).includes('private-token'));
  for (const change of [{requestId: 'other'}, {installationStarted: true}, {state: 'cancelled'}, {proposal: {}}]) {
    const tool = createExtensionProposalTool(context, {submit: async () => ({...receipt, ...change})});
    assert.equal((await tool.execute('id', args)).isError, true);
  }
});

test('wrong tool arguments return the actual schema without echoing submitted questions', async () => {
  let submitted = false;
  const tool = createExtensionProposalTool(context, {submit: async () => {submitted = true;}});
  const result = await tool.execute('id', {questions:[{question:'private-user-text'}]});
  const detail = JSON.parse(result.content[0].text);
  assert.equal(result.isError, true);
  assert.equal(detail.proposalSubmitted, false);
  assert.deepEqual(detail.parameters.required, ['source']);
  assert.deepEqual(detail.parameters.properties.source, tool.parameters.properties.source.description
    ? Object.fromEntries(Object.entries(tool.parameters.properties.source).filter(([key]) => key !== 'description'))
    : tool.parameters.properties.source);
  assert.equal(submitted, false);
  assert.ok(!result.content[0].text.includes('private-user-text'));
});

test('transport uses only fixed manager socket and accepts fragmented bounded frames', async () => {
  const socket = new EventEmitter();
  socket.destroy = () => {};
  socket.write = text => {
    assert.equal(JSON.parse(text).action, 'github-request-propose');
    socket.emit('data', Buffer.from('{"ok":'));
    socket.emit('data', Buffer.from('true}\n'));
  };
  const pending = submitExtensionProposal({action: 'github-request-propose'}, {connect: options => {
    assert.deepEqual(options, {path: process.platform === 'darwin' ? '/private/var/lib/ods-pixel-manager/extension-manager.sock' : '/run/ods-pixel-manager/extension-manager.sock'});
    return socket;
  }});
  socket.emit('connect');
  assert.deepEqual(await pending, {ok: true});
});

test('transport rejects oversized response rather than returning arbitrary manager output', async () => {
  const socket = new EventEmitter();
  socket.destroy = () => {};
  socket.write = () => socket.emit('data', Buffer.alloc(16385, 65));
  const pending = submitExtensionProposal({}, {connect: () => socket});
  socket.emit('connect');
  await assert.rejects(pending, /unavailable/);
});

test('surfaces only bounded value-free diagnostics for this exact request', async () => {
  const diagnostic = {...receipt, state: 'invalid-recipe', errors: [
    {code: 'manifest-schema', path: 'manifest/required'},
    {code: 'healthcheck-required', path: 'compose/services/healthcheck'},
  ]};
  const run = result => createExtensionProposalTool(context, {submit: async () => result}).execute('id', args);
  const valid = await run(diagnostic);
  assert.equal(valid.isError, true);
  assert.match(valid.content[0].text, /healthcheck-required/);
  for (const changed of [{chatId: 'other'}, {installationStarted: true},
    {errors: [{code: 'manifest-schema', path: 'manifest', value: 'private-token'}]}]) {
    const result = await run({...diagnostic, ...changed});
    assert.doesNotMatch(result.content[0].text, /private-token|healthcheck-required/);
  }
});

test('existing integration selection resolves session scope and never claims runtime success', async () => {
  const calls=[];
  let value={schemaVersion:1,kind:'ods-extension-request-binding',chatId:'chat',requestId:'turn',
    extensionId:'existing',definitionDigest:'a'.repeat(64),state:'bound',installationStarted:false,runtimeVerified:false};
  const submit=async payload=>{
    calls.push(payload);
    return payload.action==='github-request-resolve'
      ? {schemaVersion:1,kind:'ods-extension-request-scope',sessionHash:context.sessionKey.split('ods-')[1],request:{chatId:'chat',requestId:'turn'}}
      : value;
  };
  const tool=createExtensionRequestPrepareTool(context,{submit});
  assert.deepEqual((await tool.execute('id',{extensionId:'existing'})).details,value);
  assert.deepEqual(calls[1],{schemaVersion:1,action:'github-request-prepare',chatId:'chat',requestId:'turn',extensionId:'existing'});
  assert.deepEqual((await tool.execute('id',{})).details,value);
  const original=value;
  for(const change of [{extensionId:'different'},{requestId:'other'},{runtimeVerified:true},{installationStarted:true},{definitionDigest:'bad'}]) {
    value={...original,...change};
    assert.equal((await tool.execute('id',{extensionId:'existing'})).isError,true);
  }
  calls.length=0;
  for(const input of [{extensionId:'../escape'},{extensionId:null},{extensionId:'existing',requestId:'injected'}]) {
    assert.equal((await tool.execute('id',input)).isError,true);
  }
  assert.equal(calls.length,0);
});

test('status distinguishes an existing binding from a newly accepted proposal', async () => {
  const value={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
    requestState:'pending',proposalAccepted:false,integrationBound:true,prepared:true,
    extensionId:'existing',existingExtensionIds:['existing'],runtimeStatus:'not_installed'};
  const tool=createExtensionRequestStatusTool(context,{submit:async()=>value});
  assert.deepEqual((await tool.execute('id',{chatId:'chat',requestId:'turn'})).details,value);
  value.proposalAccepted=true;
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
  value.proposalAccepted=false;value.integrationBound=null;
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
});

test('repository conflicts retain existing IDs without suggesting a renamed duplicate', async () => {
  const diagnostic = {...receipt, state: 'invalid-recipe',
    errors: [{code: 'repository-already-exists', path: 'repository'}],
    existingExtensionIds: ['registered-click']};
  const run = value => createExtensionProposalTool(context, {submit: async () => value}).execute('id', args);
  const result = await run(diagnostic);
  const evidence = JSON.parse(result.content[0].text);
  assert.equal(result.isError, true);
  assert.deepEqual(evidence.existingExtensionIds, ['registered-click']);
  assert.equal(evidence.installationStarted, false);
  assert.match(evidence.next, /Changing serviceId cannot resolve/);
  assert.match(evidence.next, /Registration alone does not establish installation/);
  const malformed = await run({...diagnostic, existingExtensionIds: ['../private']});
  assert.doesNotMatch(malformed.content[0].text, /\.\.\/private/);
});

test('simple source proposals use the same scoped API and immutable recipe validation', async () => {
  let submitted;
  const tool = createExtensionProposalTool(context, {submit: async payload => { if(payload.action==='github-request-propose')submitted = payload; return receipt; }});
  const source = {repository: args.candidate.repository, commit: args.candidate.commit,
    serviceId: 'example', name: 'Example', dockerfile: 'Dockerfile', port: 8080, healthPath: '/health',
    healthcheck: ['CMD', 'curl', '-f', 'http://localhost:8080/health']};
  assert.equal((await tool.execute('id', {chatId: 'chat', requestId: 'turn', source})).isError, undefined);
  assert.equal(submitted.action, 'github-request-propose');
  assert.equal(submitted.candidate.manifest.service.id, 'example');
  assert.equal(submitted.candidate.compose.services.example.pull_policy, 'never');
  assert.equal((await tool.execute('id', {...args, source})).isError, true);
});

for (const [platform, path] of [['darwin', '/private/var/lib/ods-pixel-manager/extension-manager.sock'], ['linux', '/run/ods-pixel-manager/extension-manager.sock']]) {
  test(`proposal reaches the native manager on ${platform}`, async () => {
    const socket = new EventEmitter();
    socket.destroy = () => {};
    socket.write = () => socket.emit('data', Buffer.from('{"ok":true}\n'));
    const pending = submitExtensionProposal({}, {platform, connect: options => {
      assert.deepEqual(options, {path});
      return socket;
    }});
    socket.emit('connect');
    assert.deepEqual(await pending, {ok: true});
  });
}


test('managed advance binds the session and preserves uncertain host outcomes', async () => {
  const value={schemaVersion:1,kind:'ods-extension-request-installation',chatId:'chat',requestId:'turn',
    extensionId:'example',state:'pending',activeExtensionId:'example',operationId:'a'.repeat(32),dispatched:true};
  const calls=[];
  const tool=createExtensionRequestAdvanceTool(context,{submit:async payload=>{calls.push(payload);return value;}});
  assert.equal(createExtensionRequestAdvanceTool({...context,agentId:'other'}),null);
  for(const input of [{chatId:'other',requestId:'turn'}, {chatId:'chat',requestId:'turn',extensionId:'other'}])
    assert.equal((await tool.execute('id',input)).isError,true);
  assert.equal(calls.length,0);
  assert.deepEqual((await tool.execute('id',{chatId:'chat',requestId:'turn'})).details,value);
  assert.equal(calls[0].action,'github-request-advance');
  value.state='succeeded';
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
  value.dispatched=false;value.activeExtensionId=null;value.operationId=null;
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).details.state,'succeeded');
  value.requestId='different';
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn'})).isError,true);
});

test('managed retry uses the same request binding and cannot supply a target', async () => {
  const value={schemaVersion:1,kind:'ods-extension-request-installation',chatId:'chat',requestId:'turn',
    extensionId:'example',state:'pending',activeExtensionId:'example',operationId:'b'.repeat(32),dispatched:true};
  const calls=[];
  const tool=createExtensionRequestRetryTool(context,{submit:async payload=>{calls.push(payload);return value;}});
  assert.equal((await tool.execute('id',{chatId:'chat',requestId:'turn',extensionId:'other'})).isError,true);
  assert.equal(calls.length,0);
  assert.deepEqual((await tool.execute('id',{chatId:'chat',requestId:'turn'})).details,value);
  assert.equal(calls[0].action,'github-request-retry');
  assert.deepEqual(Object.keys(calls[0]).sort(),['action','chatId','requestId','schemaVersion']);
});

test('invalid small-model library arguments identify fields without submitting or coercing a version', async () => {
  const calls=[];
  const tool=createPythonLibraryProposalTool(context,{submit:async payload=>{calls.push(payload);return receipt;}});
  const input={chatId:'chat',requestId:'turn',repository:'https://github.com/o/r',commit:'a'.repeat(40),
    serviceId:'pixel_ods_python_library_proposal',name:'Example',pythonVersion:3.10,pythonImports:['example']};
  const failure=await tool.execute('id',input);
  assert.equal(failure.isError,true);
  assert.match(failure.content[0].text,/serviceId:/);
  assert.match(failure.content[0].text,/pythonVersion:.*JSON string/);
  assert.equal(calls.length,0);
  const repaired=await tool.execute('id',{...input,serviceId:'example',pythonVersion:'3.10'});
  assert.equal(repaired.isError,undefined);
  assert.equal(calls.length,2);
  assert.match(calls[0].candidate.compose.services.example.build.dockerfile_inline,/FROM python:3\.10-slim/);
});


test('library description becomes catalog metadata without changing runtime verification', async () => {
  const calls=[];
  const tool=createPythonLibraryProposalTool(context,{submit:async payload=>{calls.push(payload);return receipt;}});
  const input={chatId:'chat',requestId:'turn',repository:'https://github.com/o/r',commit:'a'.repeat(40),
    serviceId:'example',name:'Example',pythonVersion:'3.12',pythonImports:['example'],description:'A documented data parser.'};
  assert.equal((await tool.execute('id',input)).isError,undefined);
  assert.equal(calls[0].candidate.manifest.service.description,input.description);
  assert.match(calls[0].candidate.compose.services.example.command[2],/import_module/);
  for(const description of ['', '   ']) {
    assert.equal((await tool.execute('id',{...input,description})).isError,undefined);
    assert.equal(Object.hasOwn(calls.filter(x=>x.action==='github-request-propose').at(-1).candidate.manifest.service,'description'),false);
  }
  for(const description of [{},'x'.repeat(601)]) assert.equal((await tool.execute('id',{...input,description})).isError,true);
  assert.equal(calls.filter(x=>x.action==='github-request-propose').length,3);
});

test('flat Python library can add a documented function check without host commands', async () => {
  const calls=[];
  const tool=createPythonLibraryProposalTool(context,{submit:async payload=>{
    calls.push(payload);
    return payload.action==='github-request-resolve'
      ? {schemaVersion:1,kind:'ods-extension-request-scope',sessionHash:context.sessionKey.split('ods-')[1],
        request:{chatId:'chat',requestId:'turn'}} : receipt;
  }});
  const input={repository:'https://github.com/o/r',commit:'a'.repeat(40),
    serviceId:'example',name:'Example',pythonVersion:'3.12',pythonImports:['example'],
    description:'Documented parser.',pythonVerification:{expression:'modules["example"].parse("v1")',expected:'1'}};
  assert.equal((await tool.execute('one',input)).isError,undefined);
  assert.deepEqual(calls.map(x=>x.action),['github-request-resolve','github-request-propose','github-request-status']);
  const candidate=calls[1].candidate;
  assert.equal(candidate.manifest.service.description,input.description);
  assert.match(candidate.compose.services.example.command[2],/importlib\.import_module/);
  assert.match(candidate.compose.services.example.command[2],/assert str\(actual\) ==/);
  assert.match(candidate.compose.services.example.command[2],/modules\[/);
  assert.equal(tool.parameters.properties.pythonVerification.required.join(','),'expression,expected');
  calls.length=0;
  assert.equal((await tool.execute('one',{...input,pythonVerification:{expression:'',expected:'x'}})).isError,true);
  assert.deepEqual(calls.map(x=>x.action),['github-request-resolve']);
});


test('repository matches are bounded discovery evidence, not a prepared installation', async () => {
  for (const matches of [[], ['existing-a'], null, ['../escape'], ['same', 'same'], Array(65).fill('a')]) {
    const value={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
      requestState:'pending',proposalAccepted:false,prepared:false,extensionId:null,
      runtimeStatus:'not_observed',existingExtensionIds:matches};
    const tool=createExtensionRequestStatusTool(context,{submit:async()=>value});
    const result=await tool.execute('id',{chatId:'chat',requestId:'turn'});
    if (matches === null || matches.length > 1 || matches[0] === '../escape') {
      assert.equal(result.isError,true);
    } else {
      assert.deepEqual(result.details,value);
      assert.equal(result.details.prepared,false);
    }
  }
});

test('an unproposed GitHub request explains its pending state and the managed next step', async () => {
  const value={schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'turn',
    requestState:'pending',proposalAccepted:false,prepared:false,extensionId:null,
    runtimeStatus:'not_observed',existingExtensionIds:[]};
  const tool=createExtensionRequestStatusTool(context,{submit:async()=>value});
  const result=await tool.execute('status',{chatId:'chat',requestId:'turn'});
  assert.deepEqual(result.details,value);
  const guidance=JSON.parse(result.content[1].text);
  assert.equal(guidance.kind,'ods-extension-request-next-step');
  assert.equal(guidance.installationStarted,false);
  assert.match(guidance.meaning,/active GitHub request record/);
  assert.match(guidance.next,/pixel_ods_python_library_proposal/);
  assert.match(guidance.next,/pixel_ods_extension_request_advance/);
  assert.match(guidance.next,/Do not read GitHub source from the agent workspace/);
  value.existingExtensionIds=['existing'];
  const existing=JSON.parse((await tool.execute('status',{chatId:'chat',requestId:'turn'})).content[1].text);
  assert.match(existing.next,/pixel_ods_extension_request_prepare/);
  assert.doesNotMatch(existing.next,/pixel_ods_python_library_proposal/);
  value.proposalAccepted=true;
  value.extensionId='existing';
  const proposed=await tool.execute('status',{chatId:'chat',requestId:'turn'});
  assert.equal(proposed.content.length,2);
  assert.match(JSON.parse(proposed.content[1].text).next,/pixel_ods_extension_request_prepare/);
});

test('preparation rejections preserve scope and distinguish missing proposal from transport uncertainty', async () => {
  const rejection={schemaVersion:1,kind:'ods-extension-request-preparation-rejected',
    chatId:'chat',requestId:'turn',reason:'proposal_required',installationStarted:false};
  let value=rejection;
  const tool=createExtensionRequestPrepareTool(context,{submit:async()=>value});
  for (const reason of ['proposal_required','integration_selection_required',
    'license_review_required','repository_evidence_unavailable','recipe_inspection_required','request_changed']) {
    value={...rejection,reason};
    const result=await tool.execute('prepare',{chatId:'chat',requestId:'turn'});
    assert.equal(result.isError,true);
    assert.deepEqual(JSON.parse(result.content[0].text),value);
    assert.match(result.content[1].text,/No installation started|No installation started\.|Stop this|Do not repeat|Stop this attempt/);
  }
  for (const changed of [{chatId:'other'},{requestId:'other'},{reason:'secret error'},
    {installationStarted:true},{installationStarted:0},{extra:'unexpected'}]) {
    value={...rejection,...changed};
    const result=await tool.execute('prepare',{chatId:'chat',requestId:'turn'});
    assert.equal(result.isError,true);
    assert.equal(result.details,undefined);
    assert.match(result.content[0].text,/not confirmed/);
  }
});

test('incomplete CLI source reports missing verification before submitting any proposal', async () => {
  const calls=[];
  const tool=createExtensionProposalTool(context,{submit:async payload=>{
    calls.push(payload);
    return {schemaVersion:1,kind:'ods-extension-request-scope',
      sessionHash:context.sessionKey.split('ods-')[1],request:{chatId:'chat',requestId:'turn'}};
  }});
  const result=await tool.execute('proposal',{source:{repository:'https://github.com/o/r',
    commit:'a'.repeat(40),serviceId:'example',name:'Example',port:0,cliOnly:true}});
  assert.equal(result.isError,true);
  assert.match(result.content[0].text,/requires source.command/);
  assert.deepEqual(calls.map(x=>x.action),['github-request-resolve']);
  assert.equal(tool.parameters.properties.source.allOf,undefined);
});

test('flat source capability preserves the same compiled request and validation as advanced proposal', async () => {
  const source={repository:'https://github.com/o/r',commit:'a'.repeat(40),serviceId:'example',
    name:'Example',port:0,cliOnly:true,dockerfile:'Dockerfile',command:['example','self-test']};
  const calls=[];
  const dependencies={submit:async payload=>{
    calls.push(payload);
    return payload.action==='github-request-resolve'
      ? {schemaVersion:1,kind:'ods-extension-request-scope',sessionHash:context.sessionKey.split('ods-')[1],request:{chatId:'chat',requestId:'turn'}}
      : receipt;
  }};
  const flat=createSourceProposalTool(context,dependencies);
  assert.equal(createSourceProposalTool({agentId:'other'},dependencies),null);
  const {cliOnly,dockerfile,command,...identity}=source;
  const input={...identity,runtime:'cli',buildKind:'dockerfile',buildDefinition:dockerfile,verificationCommand:command};
  const first=await flat.execute('flat',input);
  const submitted=calls[1]; calls.length=0;
  const second=await createExtensionProposalTool(context,dependencies).execute('advanced',{source});
  assert.deepEqual(first,second);
  assert.deepEqual(calls[1],submitted);
  assert.equal(flat.parameters.properties.candidate,undefined);
  assert.equal(flat.parameters.properties.chatId,undefined);
  calls.length=0;
  assert.equal((await flat.execute('invalid',{...input,verificationCommand:undefined})).isError,true);
  assert.deepEqual(calls,[]);
  for (const change of [{buildKind:'shell'}, {buildDefinition:''}, {runtime:'daemon'},
    {verificationCommand:['CMD','test']}, {applicationCommand:['server']},
    {port:8080}, {healthPath:'/health'}, {runtime:'http',port:0},
    {runtime:'http',port:8080}, {runtime:'http',port:65536,healthPath:'/health'}]) {
    assert.equal((await flat.execute('invalid',{...input,...change})).isError,true);
    assert.deepEqual(calls,[]);
  }
  const mismatch=await flat.execute('runtime-mismatch',{...input,runtime:'http'});
  assert.match(mismatch.content[0].text,/runtime=http requires port/);
  assert.match(mismatch.content[0].text,/runtime=cli/);
  assert.equal(mismatch.details.proposalSubmitted,false);
  assert.deepEqual(calls,[]);
  const web={...input,runtime:'http',port:8080,healthPath:'/health',verificationCommand:['curl','-f','http://localhost:8080/health']};
  await flat.execute('web',web);
  assert.deepEqual(calls[1].candidate.compose.services.example.healthcheck.test,['CMD',...web.verificationCommand]);
  assert.equal(calls[1].candidate.compose.services.example.command,undefined);
});

test('flat source proposal also pins HEAD while advanced candidates keep a full SHA requirement', async () => {
  const pin={schemaVersion:1,kind:'ods-extension-request-commit',chatId:'chat',requestId:'turn',
    repository:'https://github.com/o/r',commit:'e'.repeat(40),
    evidenceScope:'repository-default-branch-at-inspection',installationStarted:false};
  const calls=[];
  const submit=async payload=>{
    calls.push(payload);
    if (payload.action==='github-request-resolve') return {schemaVersion:1,
      kind:'ods-extension-request-scope',sessionHash:context.sessionKey.split('ods-')[1],
      request:{chatId:'chat',requestId:'turn'}};
    if (payload.action==='github-request-pin') return pin;
    return receipt;
  };
  const flat=createSourceProposalTool(context,{submit});
  assert.equal(flat.parameters.required.includes('commit'),false);
  const input={repository:'https://github.com/o/r',commit:'HEAD',serviceId:'example',
    name:'Example',port:0,runtime:'cli',buildKind:'dockerfile',
    buildDefinition:'Dockerfile',verificationCommand:['example','self-test']};
  assert.equal((await flat.execute('id',input)).isError,undefined);
  assert.equal(calls.find(x=>x.action==='github-request-propose').candidate.commit,pin.commit);
  calls.length=0;
  assert.equal((await createExtensionProposalTool(context,{submit}).execute('id',
    {candidate:{...args.candidate,commit:'HEAD'}})).isError,true);
  assert.equal(calls.some(x=>x.action==='github-request-pin'),false);
  assert.equal(calls.some(x=>x.action==='github-request-propose'),false);
});
