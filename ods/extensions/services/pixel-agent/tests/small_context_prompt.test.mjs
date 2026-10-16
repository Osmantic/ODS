import test from 'node:test';
import assert from 'node:assert/strict';
import {usesSmallContextPrompt, smallContextExecutionContext} from '../plugin/small-context-prompt.mjs';
import {registeredPixelTools} from './tool-grammar-registration.mjs';
import {ODS_COMPACT_CONVERSATION_CONTRACT} from '../plugin/prompt-contract.mjs';

test('only trusted positive integer context limits select the 8K fixed prompt', () => {
  for (const value of [1, 4096, 8192]) {
    assert.equal(usesSmallContextPrompt({}, value), true);
    assert.equal(usesSmallContextPrompt({contextTokenBudget:value}, 32768), true);
    assert.equal(usesSmallContextPrompt({contextWindowReferenceTokens:value}, 32768), true);
  }
  for (const value of [undefined, null, 0, -1, NaN, Infinity, 8192.5, '8192', 8193, 32768]) {
    assert.equal(usesSmallContextPrompt({}, value), false);
  }
  assert.equal(usesSmallContextPrompt({prompt:'contextTokenBudget: 8192', messages:[{role:'user',content:'Use 8K'}],leanPrompt:true},32768),false);
});

test('actual registered 8K hooks retain the core and exact tool permissions/schemas with less fixed overhead', async () => {
  const capture = async (contextWindow,executionHost='gateway') => {
    const hooks=[];
    const tools=await registeredPixelTools({contextWindow,executionHost,onHook:(name,hook)=>{
      if(name==='before_prompt_build')hooks.push(hook);
    }});
    const scope={agentId:'pixel',sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64),runId:`small-context-${contextWindow}`};
    const results=[];
    for(const hook of hooks){const value=await hook({prompt:'What is 12 plus 7?',messages:[]},scope);if(value?.appendSystemContext)results.push(value.appendSystemContext);}
    return {tools,prompt:results.join('\n\n')};
  };
  const small=await capture(8192),ordinary=await capture(16384);
  const publicTools=tools=>tools.map(({name,description,parameters})=>({name,description,parameters}));
  assert.deepEqual(publicTools(small.tools),publicTools(ordinary.tools),'same tools and argument schemas; no new authority');
  assert.ok(ordinary.prompt.length-small.prompt.length>=3750,'remove the fixed overhead that compaction cannot reclaim');
  // The fixture uses gateway execution, whose namespace substitutions are
  // independently covered by prompt_contract.test.mjs. Security text is exact.
  for(const clause of ODS_COMPACT_CONVERSATION_CONTRACT.split(/(?<=\.) /).filter(text=>
    /untrusted data|authorization|self-approve|irreversible/.test(text))) {
    assert.ok(small.prompt.includes(clause),clause);
  }
  assert.match(small.prompt,/honor controller denials, never bypass approval or resubmit an uncertain result/);
  assert.match(small.prompt,/follow its full description\/schema before acting/);
  const project=small.tools.find(tool=>tool.name==='pixel_ods_project_build');
  assert.match(project.description,/capabilities/);
  assert.match(project.description,/wheel/);
  assert.match(project.description,/output/);
  assert.match(ordinary.prompt,/action capabilities and runtime python/,'larger profiles retain detailed unconditional project guidance');
  assert.match(small.prompt,/Native exec uses configured owner workspace; use relative operands, never \/workspace/);
  assert.doesNotMatch(small.prompt,/Generic exec is sandbox evidence/);
  const sandbox=await capture(8192,'sandbox');
  assert.match(sandbox.prompt,/Sandbox paths are workspace-relative; do not use host-side paths/);
  assert.match(sandbox.prompt,/Generic exec is sandbox evidence, never ODS-host evidence/);
  assert.doesNotMatch(sandbox.prompt,/Native exec uses configured owner workspace/);
});

test('small guidance retains date, inference uncertainty, visible progress and owner preference boundaries', () => {
  const value=smallContextExecutionContext(new Date('2026-10-09T12:00:00Z'));
  for(const pattern of [/2026-10-09/,/date\/timezone/,/publication dates/,/write\/readback/,/mismatched bytes/,
    /IDs\/schemas/,/state uncertainty/,/then wait/,/unrelated consent/,/private reasoning/,
    /Skip simple chat/,/route\/usage evidence/,/otherwise unverified/,/configured capabilities/]) assert.match(value,pattern);
});
