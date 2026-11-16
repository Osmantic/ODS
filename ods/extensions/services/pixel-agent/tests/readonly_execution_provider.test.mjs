import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createReadonlyExecutionProvider} from '../plugin/readonly-execution-provider.mjs';
import {buildReadonlyExecutionConfig} from '../plugin/readonly-execution-config.mjs';

const WS = '/srv/ods/workspace';
const BOOT = '/srv/private/ods/bootstrap';
const baseConfig = () => ({agents:{list:[{id:'pixel',workspace:WS}],defaults:{workspace:WS}},
  tools:{allow:['exec','read'],deny:['write'],exec:{host:'gateway',security:'allowlist',ask:'always'},elevated:{enabled:true}}});
const active = () => ({agentId:'pixel',runId:'run-1',sessionId:'sess-1',sessionKey:'key-1',toolCallId:'call-1'});
const factory = () => ({agentId:'pixel',sessionId:'sess-1',sessionKey:'key-1',workspaceDir:WS,
  activeModel:{provider:'anthropic',modelId:'claude-x'}});
const request = () => ({command:'ls',workdir:'.',timeoutSeconds:5});
const digest = c => createHash('sha256').update(JSON.stringify(c)).digest('hex');

function harness(overrides = {}) {
  const state = {config:baseConfig(),allocations:[],cleanupCount:0,recovered:0,attached:null,created:null,resolved:null};
  const deps = {
    readConfig:() => state.config,
    resolveSandbox:async args => { state.resolved = args; return {enabled:true,workspaceAccess:'ro',agentWorkspaceDir:WS,workspaceDir:BOOT}; },
    createTools:args => { state.created = args; return [{name:'exec',execute:async()=>({})}]; },
    allocator:{
      allocate:async (scope) => {
        const built = buildReadonlyExecutionConfig(state.config, {workspaceRoot:scope.workspaceRoot,privateRoot:'/srv/private/ods',nonce:'c'.repeat(32),imageId:'sha256:'+'b'.repeat(64)});
        const a = {config:built.config, sandboxSessionKey:'agent:pixel:ods-readonly:' + 'c'.repeat(32),
          ownerWorkspace:scope.workspaceRoot, createdAt:Date.now(),
          attach:async sandbox => { state.attached = sandbox; },
          cleanup:async () => { state.cleanupCount++; }};
        state.allocations.push(a);
        return a;
      },
      recover:async () => { state.recovered++; },
    },
    ...overrides,
  };
  return {state,deps,provider:createReadonlyExecutionProvider(deps)};
}

test('scopeForContext requires exact pixel scope and workspace', () => {
  const {provider} = harness();
  const scope = provider.scopeForContext(active(), factory());
  assert.equal(scope.workspaceRoot, WS);
  assert.equal(scope.configDigest.length, 64);
  assert.throws(() => provider.scopeForContext({...active(),agentId:'other'}, factory()), /readonly-scope-invalid/);
  assert.throws(() => provider.scopeForContext({...active(),toolCallId:''}, factory()), /readonly-scope-invalid/);
  assert.throws(() => provider.scopeForContext(active(), {...factory(),workspaceDir:'/elsewhere'}), /readonly-scope-mismatch/);
});

test('scopeForContext rejects duplicate pixel config entries', () => {
  const {provider,state} = harness();
  state.config.agents.list.push({id:'pixel',workspace:WS});
  assert.throws(() => provider.scopeForContext(active(), factory()), /readonly-scope-invalid/);
});

test('scopeForContext preserves optional model and tolerates absent model', () => {
  const {provider} = harness();
  const withModel = provider.scopeForContext(active(), factory());
  assert.equal(withModel.factory.activeModel.provider, 'anthropic');
  const noModel = provider.scopeForContext(active(), {...factory(), activeModel:undefined});
  assert.equal(noModel.factory.activeModel, undefined);
});

test('prepare normalizes request before allocation', async () => {
  const {provider,state} = harness();
  const scope = provider.scopeForContext(active(), factory());
  await assert.rejects(provider.prepare(scope, {command:'',workdir:'.',timeoutSeconds:5}), err => {
    assert.equal(err.readonlyCleanupConfirmed, true);
    return true;
  });
  assert.equal(state.allocations.length, 0);
});

test('prepare marks no-resource drift as confirmed without allocation', async () => {
  const {provider,state} = harness();
  const scope = provider.scopeForContext(active(), factory());
  state.config = {...state.config, extra:1};
  await assert.rejects(provider.prepare(scope, request()), err => {
    assert.equal(err.code, 'readonly-config-drift');
    assert.equal(err.readonlyCleanupConfirmed, true);
    return true;
  });
  assert.equal(state.allocations.length, 0);
});

test('prepare rechecks config digest after allocation and rolls back', async () => {
  const {provider,state,deps} = harness();
  const scope = provider.scopeForContext(active(), factory());
  const original = deps.allocator.allocate;
  deps.allocator.allocate = async (s) => { const a = await original(s); state.config = {...state.config,extra:1}; return a; };
  await assert.rejects(provider.prepare(scope, request()), /readonly-config-drift/);
  assert.equal(state.allocations.length, 1);
  assert.equal(state.cleanupCount, 1);
});

test('prepare validates sandbox before attach and rejects drift', async () => {
  const {provider,state} = harness({resolveSandbox:async () => ({enabled:true,workspaceAccess:'rw',agentWorkspaceDir:WS,workspaceDir:BOOT})});
  const scope = provider.scopeForContext(active(), factory());
  await assert.rejects(provider.prepare(scope, request()), /readonly-sandbox-invalid/);
  assert.equal(state.attached, null);
  assert.equal(state.cleanupCount, 1);
});

test('prepare requires sandbox owner and distinct bootstrap', async () => {
  const {provider} = harness({resolveSandbox:async () => ({enabled:true,workspaceAccess:'ro',agentWorkspaceDir:'/other',workspaceDir:BOOT})});
  const scope = provider.scopeForContext(active(), factory());
  await assert.rejects(provider.prepare(scope, request()), /readonly-sandbox-owner-mismatch/);
  const {provider:p2} = harness({resolveSandbox:async () => ({enabled:true,workspaceAccess:'ro',agentWorkspaceDir:WS,workspaceDir:WS})});
  const s2 = p2.scopeForContext(active(), factory());
  await assert.rejects(p2.prepare(s2, request()), /readonly-sandbox-bootstrap-invalid/);
});

test('prepare preserves original ids and model, uses bootstrap workdir', async () => {
  const {provider,state} = harness();
  const scope = provider.scopeForContext(active(), factory());
  const handle = await provider.prepare(scope, request());
  assert.equal(state.created.modelProvider, 'anthropic');
  assert.equal(state.created.modelId, 'claude-x');
  assert.equal(state.created.emitBeforeToolCallDiagnostics, false);
  assert.equal(state.created.runId, 'run-1');
  assert.equal(state.created.sessionKey, 'key-1');
  assert.equal(state.created.workspaceDir, WS);
  assert.equal(handle.params.workdir, BOOT);
  assert.equal(handle.params.background, false);
  assert.equal(handle.params.yieldMs, 120000);
  await handle.cleanup();
  assert.equal(state.cleanupCount, 1);
});

test('prepare uses scoped config for sandbox and preserves allow/deny/ask/security', async () => {
  const {provider,state} = harness();
  const scope = provider.scopeForContext(active(), factory());
  const handle = await provider.prepare(scope, request());
  assert.equal(state.resolved.sessionKey, 'agent:pixel:ods-readonly:' + 'c'.repeat(32));
  assert.equal(state.resolved.workspaceDir, WS);
  assert.equal(state.created.sessionKey, 'key-1');
  assert.equal(state.created.config.agents.list[0].sandbox.workspaceAccess, 'ro');
  assert.equal(state.created.config.tools.exec.host, 'sandbox');
  assert.equal(state.created.config.tools.elevated.enabled, false);
  assert.deepEqual(state.created.config.tools.allow,['exec','read']);
  assert.deepEqual(state.created.config.tools.deny,['write']);
  assert.equal(state.created.config.tools.exec.security,'allowlist');
  assert.equal(state.created.config.tools.exec.ask,'always');
  await handle.cleanup();
});

test('prepare rolls back allocation on SDK failure and marks confirmed', async () => {
  const {provider,state} = harness({resolveSandbox:async () => { throw new Error('sdk-down'); }});
  const scope = provider.scopeForContext(active(), factory());
  await assert.rejects(provider.prepare(scope, request()), err => {
    assert.equal(err.readonlyCleanupConfirmed, true);
    return true;
  });
  assert.equal(state.cleanupCount, 1);
});

test('prepare surfaces unconfirmed cleanup with retryable recovery handle', async () => {
  let failCleanup=true,recovery;
  const {provider,state,deps}=harness({resolveSandbox:async()=>{throw Object.freeze(new Error('SDK failure'));}});
  const original=deps.allocator.allocate;
  deps.allocator.allocate=async(s,c)=>{
    const allocation=await original(s,c);
    allocation.cleanup=async function(){
      assert.equal(this,allocation,'cleanup receiver preserved');
      if(failCleanup){failCleanup=false;throw new Error('temporarily unavailable');}
      state.cleanupCount++;
    };
    return allocation;
  };
  const scope=provider.scopeForContext(active(),factory());
  await assert.rejects(provider.prepare(scope,request()),error=>{
    assert.equal(error.code,'readonly-cleanup-unconfirmed');recovery=error.readonlyRecovery;return true;
  });
  await recovery.cleanup();assert.equal(state.cleanupCount,1);
});

test('recover delegates and prepare rejects missing exec tool', async () => {
  const {provider,state} = harness({createTools:() => [{name:'process'}]});
  await provider.recover();
  assert.equal(state.recovered, 1);
  const scope = provider.scopeForContext(active(), factory());
  await assert.rejects(provider.prepare(scope, request()), /readonly-exec-unavailable/);
  assert.equal(state.cleanupCount, 1);
});
