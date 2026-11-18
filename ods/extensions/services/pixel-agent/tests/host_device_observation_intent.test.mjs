import test from 'node:test';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');
const original = 'Run a read-only hostname check on this Mac. Then use Python in the workspace to create fleet-squares.csv with columns number,square and rows for 1 through 12. Read the file back to verify the last row is 12,144, and give me a downloadable link to the CSV.';
const quote = String.fromCharCode(96);

for (const device of ['Mac', 'MacBook', 'Mac Mini', 'mac mini', 'MacBook Air', 'Mac Studio', 'iMac', 'computer', 'laptop']) {
  test('natural hostname observation on ' + device + ' requires typed host evidence', () => {
    const prompt = original.replace('this Mac.', 'this ' + device + '.');
    assert.equal(api.userMessageRequestsHostCommand([], prompt), false);
    assert.deepEqual(api.userMessageOperationsRequirements([], prompt),
      {required:true, actions:['host.identity']});
  });
}

for (const prompt of [
  'Check the hostname on this Mac.',
  "What's the hostname of my MacBook?",
  'Please run a hostname check on this laptop.',
  'Could you run a read-only hostname check on this Mac Mini?',
]) {
  test('ordinary host identity wording: ' + prompt, () => {
    assert.deepEqual(api.userMessageOperationsRequirements([], prompt),
      {required:true, actions:['host.identity']});
  });
}

for (const prompt of [
  'The Mac button in the UI does nothing.',
  'On my Mac the start screen is cut off. Fix the game in the workspace.',
  'First launch on my Mac shows a blank screen.',
  'The game makes my Mac restart.',
  'Will this Mac run the game?',
  'Run a command on this Mac.',
  'Check the MAC address format in the workspace validator.',
  'Check the MAC address and hostname format in the workspace validator.',
  'Check the MAC-address and hostname format in the workspace validator.',
  'Check the MAC_address and hostname format in the workspace validator.',
  'Explain how to run a hostname check on this Mac.',
  'Do not run a hostname check on this Mac.',
  'Explain this quote: "Run a read-only hostname check on this Mac".',
  'Explain this quote: "Run ' + quote + 'hostname' + quote + ' on this Mac."',
  'Create a workspace note.\n> Run a read-only hostname check on this Mac.',
  'Create a workspace note.\n' + quote.repeat(3) + 'text\nRun a read-only hostname check on this Mac.\n' + quote.repeat(3),
  'Inspect /workspace/Mac/hostname.txt.',
  'Explain how to check a hostname on this Mac. What is uptime?',
  'The Mac button in the UI does nothing. Check the CPU.',
]) {
  test('mentions, explanation and exclusions do not require host work: ' + prompt, () => {
    assert.equal(api.userMessageOperationsRequirements([], prompt).required, false);
  });
}

for (const [facet,action] of [
  ['host identity','host.identity'],['host platform','host.platform'],
  ['kernel','host.kernel'],['machine architecture','host.architecture'],
  ['operating system','host.os-release'],['uptime','host.uptime'],
  ['cpu','host.cpu'],['gpu','host.gpu'],['memory','host.memory'],['storage','host.storage'],
]) {
  test('natural observation preserves the narrow ' + facet + ' scope',()=>{
    assert.deepEqual(api.userMessageOperationsRequirements([],
      'Execute a read-only ' + facet + ' check on this Mac.'),
    {required:true,actions:[action]});
  });
}

// Workstation06, 2026-10-27: "the GPUs in this machine" required only
// host.memory, and plural CPUs, processors and disks required no host fact.
for (const [prompt, actions] of [
  ['How much free memory do the GPUs in this machine have right now?', ['host.gpu', 'host.memory']],
  ['How many CPUs does this machine have?', ['host.cpu']],
  ['What processors does this computer have?', ['host.cpu']],
  ['How much space is left on the disks of this machine?', ['host.storage']],
  ['Which video cards are in this computer?', ['host.gpu']],
]) {
  test('plural hardware nouns keep their host facet: ' + prompt, () => {
    assert.deepEqual(api.userMessageOperationsRequirements([], prompt), {required: true, actions});
  });
}

for (const prompt of ['Write a script that lists GPUs.', 'Explain what GPUs are.']) {
  test('plural hardware nouns outside a host request need no host work: ' + prompt, () => {
    assert.equal(api.userMessageOperationsRequirements([], prompt).required, false);
  });
}

test('an excluded plural facet stays excluded', () => {
  assert.deepEqual(api.userMessageOperationsRequirements([],
    'Report the CPU and memory of this machine, but do not report the GPUs.'),
  {required: true, actions: ['host.cpu', 'host.memory']});
});

for (const device of ['Mac', 'MacBook', 'Mac Mini', 'computer', 'laptop']) {
  test('exact commands on ' + device + ' retain immutable approval', () => {
    const prompt = 'Run ' + quote + 'hostname' + quote + ' on this ' + device + '.';
    assert.equal(api.userMessageExactHostCommand([], prompt), 'hostname');
    assert.deepEqual(api.userMessageOperationsRequirements([], prompt),
      {required:true, actions:['raw-shell']});
  });
  for (const prompt of [
    'Restart Docker on this ' + device + '.',
    'Run an unknown diagnostic on this ' + device + '.',
    'Run a read-only hostname check on this ' + device + ' and restart Docker.',
    'Run a read-only hostname check on this ' + device + '. Restart Docker on this ' + device + '.',
    'Run a read-only hostname check on this ' + device + ' "and restart Docker".',
  ]) {
    test('observation grammar cannot swallow another command: ' + prompt, () => {
      assert.deepEqual(api.userMessageOperationsRequirements([], prompt),
        {required:true, actions:['raw-shell']});
    });
  }
}

test('a separate network peer does not erase the requested Mac identity', () => {
  const required=api.userMessageOperationsRequirements([],
    'Check the hostname on this Mac. Check the reachability of archive.local on the LAN.');
  assert.equal(required.required,true);
  assert.deepEqual(required.actions,['host.identity','host.network-peer']);
});

const ctx = {trigger:'user',agentId:'pixel',runId:'device-host',sessionId:'device-session',
  sessionKey:'agent:pixel:openai-user:ods-' + 'a'.repeat(64)};
function fixture() {
  const guard = api.createToolLoopGuard();
  guard.observeRun(ctx, 'pixel', {prompt:original});
  const before = (toolName,params) => guard.beforeToolCall(
    {toolName,params,runId:ctx.runId},{...ctx,toolName},'pixel');
  const after = (toolName,params,result) => guard.afterToolCall(
    {toolName,params,result},{...ctx,toolName},'pixel');
  return {guard,before,after};
}

test('sandbox hostname cannot satisfy the Mac request or preserve a false success claim', () => {
  const f=fixture();
  // Independent workspace tools remain allowed; their output is not host evidence.
  f.before('exec',{command:'hostname'});
  f.after('exec',{command:'hostname'},{details:{status:'completed',exitCode:0},
    content:[{type:'text',text:'sandbox-container\n'}]});
  assert.equal(f.guard.verificationForRun(ctx.runId).status,'failed');
  const delivered=f.guard.replyPayloadSending({runId:ctx.runId,kind:'final',
    payload:{text:'The Mac hostname is sandbox-container. Everything succeeded.'}});
  assert.doesNotMatch(delivered.payload.text,/The Mac hostname is sandbox-container/);
});

test('typed host receipt composes with Python, readback and downloadable CSV', () => {
  const f=fixture();
  const params={actions:['host.identity']};
  assert.notEqual(f.before('pixel_ods_host_observe',params)?.block,true);
  f.after('pixel_ods_host_observe',params,{details:{
    jobId:'ops-1234567890123-abcdef123456',status:'succeeded',waitTimedOut:false,
    steps:[{stepId:'identity',target:'ods-host',action:'host.identity',exitCode:0,
      stdout:'actual-mac\n',stderr:'',outputTruncated:{stdout:false,stderr:false},riskSignals:[]}]}});
  assert.equal(f.guard.verificationForRun(ctx.runId).status,'passed');
  assert.notEqual(f.before('exec',{command:'python3 -c "print(12 * 12)"'})?.block,true);
  assert.notEqual(f.before('read',{path:'fleet-squares.csv'})?.block,true);
  assert.notEqual(f.before('pixel_ods_workspace_artifact',{relativePath:'fleet-squares.csv'})?.block,true);
});

for (const variant of ['failed','wrong-target']) {
  test(variant + ' host receipt cannot complete the Mac request', () => {
    const f=fixture(),params={actions:['host.identity']};
    f.before('pixel_ods_host_observe',params);
    f.after('pixel_ods_host_observe',params,{details:{
      jobId:'ops-1234567890123-abcdef123456',status:variant==='failed'?'failed':'succeeded',waitTimedOut:false,
      steps:[{stepId:'identity',target:variant==='wrong-target'?'other-host':'ods-host',
        action:'host.identity',exitCode:variant==='failed'?1:0,
        stdout:'other\n',stderr:'',outputTruncated:{stdout:false,stderr:false},riskSignals:[]}]}});
    assert.equal(f.guard.verificationForRun(ctx.runId).status,'failed');
  });
}
