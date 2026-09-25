import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createPythonLibraryProposalTool} from '../plugin/extension-proposal.mjs';
import {createExtensionCompletionGate} from '../plugin/extension-completion-gate.mjs';

const identity={chatId:'chat',requestId:'turn'};
const sessionHash=createHash('sha256').update(identity.chatId).digest('hex');
const context={agentId:'pixel',sessionKey:'agent:pixel:openai-user:ods-'+sessionHash};
const args={repository:'https://github.com/o/r',serviceId:'example',name:'Example',
  pythonVersion:'3.12',pythonImports:['example']};
const cooldown={schemaVersion:1,kind:'ods-extension-request-repository-unavailable',...identity,
  repository:args.repository,reason:'github-rate-limited',retryAfter:120,installationStarted:false};

test('verified GitHub cooldown reaches model and completion gate without proposal or install',async()=>{
  const calls=[];
  const tool=createPythonLibraryProposalTool(context,{submit:async payload=>{
    calls.push(payload.action);
    if(payload.action==='github-request-resolve') return {schemaVersion:1,kind:'ods-extension-request-scope',
      sessionHash,request:identity,authorizationMode:'install'};
    assert.equal(payload.action,'github-request-pin');
    return cooldown;
  }});
  const result=await tool.execute('call',args);
  assert.equal(result.isError,true);
  assert.deepEqual(result.details,cooldown);
  assert.deepEqual(calls,['github-request-resolve','github-request-pin']);
  const gate=createExtensionCompletionGate('/extensions install '+args.repository);
  gate.observe(tool.name,result);
  assert.equal(gate.finalize(),undefined);
  assert.equal(gate.verification.status,'failed');
  assert.match(gate.verification.text,/120 seconds/);
});

test('foreign or malformed cooldown never becomes a verified blocker',async()=>{
  for(const changed of [{chatId:'other'},{retryAfter:0},{retryAfter:3601},{retryAfter:true},{installationStarted:true}]){
    const tool=createPythonLibraryProposalTool(context,{submit:async payload=>payload.action==='github-request-resolve'
      ? {schemaVersion:1,kind:'ods-extension-request-scope',sessionHash,request:identity,authorizationMode:'install'}
      : {...cooldown,...changed}});
    const result=await tool.execute('call',args);
    assert.equal(result.isError,true);
    assert.equal(result.details,undefined);
  }
});
