// Use the pinned catalog and its actual native schema validator. No model,
// installed owner state, provider or application data is used here.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

const packageDir=process.env.OPENCLAW_PACKAGE_DIR;
const manifest=JSON.parse(readFileSync(new URL('../host/openclaw-image-envelope.json',import.meta.url)));
const source=join(packageDir,'dist/tool-search-BInRpkE3.js');
assert.equal(createHash('sha256').update(readFileSync(source)).digest('hex'),manifest.patchedSha256);
for(const [name,hash] of Object.entries(manifest.reviewedDependencies))
  assert.equal(createHash('sha256').update(readFileSync(join(packageDir,'dist',name))).digest('hex'),hash);
const {a:ToolSearchRuntime,u:applyToolSearchCatalog}=await import(pathToFileURL(source));

function fixture(agentId='pixel') {
  const calls=[];
  const tools=[{name:'wiki_get',description:'Read one wiki page.',
    parameters:{type:'object',required:['lookup'],properties:{lookup:{type:'string'}}},
    async execute(id,args){ calls.push({id,args});return {content:[{type:'text',text:args.lookup.trim()}]}; }}];
  const catalogRef={};
  const config={tools:{toolSearch:{mode:'tools',enabled:true}}};
  tools.push({name:'tool_call',description:'Native discovery control',parameters:{type:'object'},async execute(){throw new Error('control must not execute');}});
  applyToolSearchCatalog({tools,agentId,catalogRef,config});
  return {runtime:new ToolSearchRuntime({agentId,catalogRef,config},{mode:'tools'}),calls};
}

test('deferred Pixel call rejects missing or invalid required arguments before execution',async()=>{
  const {runtime,calls}=fixture();
  for(const args of [{path:'note.md'},{id:'note'},undefined])
    await assert.rejects(runtime.call('wiki_get',args),/Validation failed[\s\S]*lookup/);
  assert.deepEqual(calls,[]);
  const result=await runtime.call('wiki_get',{lookup:'synthesis.fixture'});
  assert.equal(result.result.content[0].text,'synthesis.fixture');
  assert.equal(calls.length,1);
  const coerced=await runtime.call('wiki_get',{lookup:42});
  assert.equal(coerced.result.content[0].text,'42','retain native schema coercion');
  assert.equal(calls.length,2);
});

test('native describe and permission-filtered catalog remain authoritative',async()=>{
  const {runtime,calls}=fixture();
  const description=await runtime.describe('wiki_get');
  assert.deepEqual(description.parameters.required,['lookup']);
  await assert.rejects(runtime.call('not-granted',{lookup:'note'}),/Unknown tool/);
  assert.deepEqual(calls,[]);
});

test('other native agents retain their preexisting deferred dispatch behavior',async()=>{
  const {runtime,calls}=fixture('another-agent');
  await assert.rejects(runtime.call('wiki_get',{path:'note.md'}),/trim/);
  assert.equal(calls.length,1);
});
