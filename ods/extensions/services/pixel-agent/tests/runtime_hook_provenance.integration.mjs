// Execute exact pinned runtime bytes in memory; no installed files are changed.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const root=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(root && path.isAbsolute(root),'provide the reviewed OpenClaw package explicitly');
const manifest=JSON.parse(fs.readFileSync(new URL('../host/openclaw-hook-provenance.json',import.meta.url)));
assert.equal(JSON.parse(fs.readFileSync(path.join(root,'package.json'))).version,manifest.version);
const hash=value=>createHash('sha256').update(value).digest('hex');
let original=fs.readFileSync(path.join(root,'dist/hook-agent-context-ugCMMoT5.js'),'utf8');
if(hash(original)===manifest.patchedSha256) {
  for(const [before,after] of [...manifest.replacements].reverse()) original=original.replace(after,before);
}
assert.equal(hash(original),manifest.sourceSha256,'reject unreviewed runtime source');
let patched=original;
for(const [before,after] of manifest.replacements) {
  assert.equal(patched.split(before).length,2);
  patched=patched.replace(before,after);
}
assert.equal(hash(patched),manifest.patchedSha256);
async function load(source) {
  const absolute=source.replace(/from "(\.\/[^"\n]+)"/g,(_all,relative)=>
    `from "${pathToFileURL(path.join(root,'dist',relative)).href}"`);
  return (await import(`data:text/javascript;base64,${Buffer.from(absolute).toString('base64')}`)).t;
}
const before=await load(original),after=await load(patched);
const inputProvenance={kind:'inter_session',sourceTool:'subagent_announce',sourceSessionKey:'agent:pixel:subagent:child'};
const ctx={agentId:'pixel',runId:'r',sessionId:'s',sessionKey:'agent:pixel:owner'};
const owner={role:'user',content:'Read Playground/shop and consolidate two independent code reviews.'};
const prompt=`<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>
Observação de limite: a task pedia validação via ferramenta \`read\`; o \`read\` desta sessão resolveu caminhos com prefixo indevido (ENOENT) e retornou conteúdo vazio, então li os arquivos via \`cat -n\` no shell (mesmo conteúdo) e reproduzi a lógica de cálculo em Node para confirmar os números.
A completed subagent task is ready for parent review. Review/verify the result above before deciding whether the original task is done. Keep this internal context private (don't mention system/log/stats/session details or announce type).
<<<END_OPENCLAW_INTERNAL_CONTEXT>>>`;
function decision(project) {
  const guard=createToolLoopGuard();
  const context={...ctx,...project({...ctx,inputProvenance})};
  guard.observeRun(context,'pixel',{prompt,messages:[owner]});
  return guard.beforeToolCall({toolName:'tool_call',params:{id:'openclaw:core:sessions_yield',args:{}}},
    {...context,toolName:'tool_call'},'pixel');
}
test('pinned hook baseline drops provenance and reproduces the live yield denial',()=>{
  assert.equal(before({...ctx,inputProvenance}).inputProvenance,undefined);
  assert.match(decision(before)?.blockReason ?? '',/owner requested host or Operations/);
});
test('pinned hook projection fixes continuation without a tool allowlist exception',()=>{
  assert.equal(decision(after)?.block,undefined);
});
test('provenance projection is bounded immutable and carries no arbitrary payload',()=>{
  const source={...inputProvenance,prompt:'PRIVATE',headers:{authorization:'PRIVATE'}};
  const result=after({...ctx,inputProvenance:source});
  assert.deepEqual(result.inputProvenance,inputProvenance);
  assert.ok(Object.isFrozen(result.inputProvenance));
  source.sourceTool='modified';
  assert.equal(result.inputProvenance.sourceTool,'subagent_announce');
  for(const value of [{kind:'user'}, {...inputProvenance,sourceTool:'x'.repeat(129)},
    {...inputProvenance,sourceSessionKey:'x'.repeat(513)}, {sourceTool:'subagent_announce'}]) {
    assert.equal(after({...ctx,inputProvenance:value}).inputProvenance,undefined);
  }
  assert.deepEqual(after(ctx),before(ctx),'ordinary owner channel context is unchanged');
});
test('both native and embedded prompt hooks use the reviewed shared projection',()=>{
  for(const module of ['prepare.runtime-KE-MD2qg.js','selection-BEwSQKM-.js','embedded-agent-CJx-nG3W.js']) {
    const source=fs.readFileSync(path.join(root,'dist',module),'utf8');
    assert.match(source,/from "\.\/hook-agent-context-ugCMMoT5\.js"/);
    assert.match(source,/\.\.\.buildAgentHookContextChannelFields\(params\)/);
  }
});
