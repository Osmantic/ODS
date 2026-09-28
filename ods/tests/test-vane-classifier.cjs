'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const {patchBundle}=require('../extensions/services/perplexica/patch-research-quality.js');
const upstream=fs.readFileSync(path.join(__dirname,'fixtures/vane-classifier-v1.12.2.js.txt'),'utf8').replace(/\r\n/g,'\n').trimEnd();
const expected='34ed4268a0318a0a980a3bfcf72d052592043f9ea8d01994a25c3e83dc29006c';
function classifier(source){
 const exports={},schemas=[];
 const zod={object:shape=>{const node={shape,describe(){return this;}};schemas.push(node);return node;},boolean:()=>({type:'boolean',describe(){return this;}}),string:()=>({type:'string',describe(){return this;}})};
 const require=id=>{if(id===46901)return {Ay$:zod};if(id===80202)return {A:h=>JSON.stringify(h)};throw Error('Unexpected production import '+id);};
 require.d=(target,values)=>{for(const [key,get] of Object.entries(values))Object.defineProperty(target,key,{get});};
 vm.runInNewContext('('+source+')')(null,exports,require);
 return {run:exports.L,schemas};
}
test('classifier fixture is the pinned upstream compiled factory',()=>{
 assert.equal(crypto.createHash('sha256').update(upstream).digest('hex'),expected);
 assert.match(upstream,/straightforward, factual/);
});
test('actual classifier sends conservative evidence policy with unchanged schema and history',async()=>{
 const result=patchBundle(upstream),before=classifier(upstream),after=classifier(result.source),sent=[];
 assert.deepEqual(result.applied,[{id:'quality-47',count:1}]);
 assert.equal(patchBundle(result.source).source,result.source);
 assert.equal(patchBundle(result.source).applied.length,0);
 const answer={classification:{skipSearch:false,personalSearch:false,academicSearch:false,discussionSearch:false,showWeatherWidget:false,showStockWidget:false,showCalculationWidget:false},standaloneFollowUp:'Lookup the specified person and affiliation'};
 const input={query:'Who leads the named organization?',enabledSources:['web'],chatHistory:[{role:'user',content:'We are discussing an organization.'}],llm:{generateObject:async args=>{sent.push(args);return answer;}}};
 assert.equal(await before.run(input),answer);assert.equal(await after.run(input),answer);
 assert.deepEqual(sent.map(x=>Array.from(x.messages,m=>m.role)),[['system','user'],['system','user']]);
 assert.equal(sent[0].messages[1].content,sent[1].messages[1].content);
 assert.deepEqual(Object.keys(sent[0].schema.shape.classification.shape),Object.keys(sent[1].schema.shape.classification.shape));
 assert.equal(Object.keys(sent[1].schema.shape.classification.shape).length,7);
 const original=sent[0].messages[0].content,candidate=sent[1].messages[0].content;
 assert.doesNotMatch(candidate,/true if the query is straightforward, factual/);
 assert.match(candidate,/specific person, organization, or entity/);assert.match(candidate,/obscure or ambiguous identity/);
 assert.match(candidate,/true only for greetings, creative writing, mathematical facts, established general concepts/);
 assert.equal(candidate.slice(candidate.indexOf('2. personalSearch')),original.slice(original.indexOf('2. personalSearch')));
 // These tests prove the real module prompt/schema contract; live model decisions remain an installed gate.
});
