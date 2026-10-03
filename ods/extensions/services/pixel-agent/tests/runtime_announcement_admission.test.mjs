import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

// Exercise the predicate that is actually installed by the reviewed recipe.
// Native transport and hook behavior are covered by the real-gateway fixture.
const recipe=JSON.parse(readFileSync(new URL('../host/openclaw-announcement-admission.json',import.meta.url)));
const helper=recipe.replacements[0][1].split('async function sendSubagentAnnounceDirectly')[0];
const predicate=(env={})=>runInNewContext(helper+'\nrequiresOdsAnnouncementAdmission',{process:{env}});
const owner=agent=>`agent:${agent}:openai-user:ods-${'a'.repeat(64)}`;
const completion=target=>({sourceTool:'subagent_announce',targetRequesterSessionKey:target});

test('trusted ODS completion uses admission for the configured agent',()=>{
  assert.equal(predicate()(completion(owner('pixel'))),true);
  assert.equal(predicate({PIXEL_AGENT_ID:'owner'})(completion(owner('owner'))),true);
  assert.equal(predicate({PIXEL_AGENT_ID:'owner'})(completion(owner('pixel'))),false);
});

test('unrelated native delivery routes preserve their existing transport',()=>{
  const requireAdmission=predicate();
  for(const target of ['agent:pixel:main','agent:pixel:subagent:123','agent:pixel:openai-user:regular-user',owner('other')])
    assert.equal(requireAdmission(completion(target)),false,target);
  assert.equal(requireAdmission({...completion(owner('pixel')),sourceTool:'sessions_send'}),false);
  assert.equal(requireAdmission({targetRequesterSessionKey:owner('pixel')}),false);
});

test('actual target takes precedence over origin and malformed owner identifiers do not widen scope',()=>{
  const requireAdmission=predicate();
  assert.equal(requireAdmission({...completion('agent:pixel:main'),requesterSessionKey:owner('pixel')}),false);
  assert.equal(requireAdmission({...completion(owner('pixel')),requesterSessionKey:'agent:pixel:main'}),true);
  assert.equal(requireAdmission({sourceTool:'subagent_announce',requesterSessionKey:owner('pixel')}),true);
  for(const target of [undefined,null,42,{},owner('pixel')+'x',owner('pixel').slice(0,-1)])
    assert.equal(requireAdmission(completion(target)),false);
});

test('cancelled or unrecognized children still reach the existing admission fence',()=>{
  // Transport selection must not require a live chain or pre-authorize a child;
  // the native run hook validates exact spawn provenance and rejects it there.
  const requireAdmission=predicate();
  assert.equal(requireAdmission(completion(owner('pixel'))),true);
  assert.equal(requireAdmission({...completion(owner('pixel')),sourceSessionKey:'unrecognized-child'}),true);
});
