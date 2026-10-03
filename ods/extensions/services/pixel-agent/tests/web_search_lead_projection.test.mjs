import test from 'node:test';
import assert from 'node:assert/strict';
import {captureNativeWebSearchResult, projectNativeWebSearchResult, projectWebResult} from '../plugin/web-result-projection.mjs';

const message = {role:'toolResult', toolName:'web_search', toolCallId:'exact-call'};
const wrapped = text => `\n<<<EXTERNAL_UNTRUSTED_CONTENT id="fixture">>>\nSource: Web Search\n---\n${text}\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="fixture">>>`;
const make = rows => {
  const details = {provider:'parallel-free',searchQueries:['requested exact entity'],count:rows.length,
    externalContent:{untrusted:true,source:'web_search',provider:'parallel-free',wrapped:true},results:rows};
  return {content:[{type:'text',text:JSON.stringify(details,null,2)}],details};
};
const rows = () => Array.from({length:5},(_,i)=>({title:wrapped(`Source ${i} 😀`),
  url:`https://source${i}.example/item?variant=exact&year=2026`,
  description:wrapped(i===0?'long source text '.repeat(900):`short source ${i}`),
  siteName:`source${i}.example`,published:'2026-09-24'}));

test('long first description no longer hides later URLs or splits JSON/trust wrappers',()=>{
  const input=make(rows()),before=structuredClone(input);
  assert.ok(input.content[0].text.indexOf(input.details.results[4].url)>8000);
  const output=projectNativeWebSearchResult(message,input),evidence=JSON.parse(output.content[0].text);
  assert.ok(output.content[0].text.length<=8000);
  assert.equal(evidence.results.length,5);
  assert.deepEqual(evidence.externalContent,input.details.externalContent);
  for(let i=0;i<5;i++) {
    const expected={...input.details.results[i]};if(i===0)delete expected.description;
    assert.deepEqual(evidence.results[i],expected);
  }
  assert.match(output.content[2].text,/not source evidence/);
  assert.match(output.content[2].text,/existing research allowance/);
  assert.deepEqual(input,before);assert.deepEqual(output.details,before.details);
  assert.deepEqual(captureNativeWebSearchResult(input),before);
});

test('unique excerpts can be omitted only as whole fields and the original receipt remains intact',()=>{
  const r=rows();r[0].description=wrapped('short');r[2].excerpts=[wrapped('unique '.repeat(2000))];
  const input=make(r),out=projectNativeWebSearchResult(message,input);
  const projected=JSON.parse(out.content[0].text);
  assert.equal(Object.hasOwn(projected.results[2],'excerpts'),false);
  assert.deepEqual(out.details,input.details);
  for(let i=0;i<5;i++)assert.equal(projected.results[i].description,r[i].description);
});

test('whitespace compaction alone preserves all fields without omission guidance',()=>{
  const r=Array.from({length:20},(_,i)=>({title:`Lead ${i}`,url:`https://example.com/${i}`,description:'x'.repeat(300)}));
  const input=make(r);assert.ok(input.content[0].text.length>8000);assert.ok(JSON.stringify(input.details).length<8000);
  const out=projectNativeWebSearchResult(message,input);
  assert.deepEqual(JSON.parse(out.content[0].text),input.details);assert.equal(out.content.length,2);
});

test('oversized identity metadata uses the old bounded fallback without shortening any URL in the receipt',()=>{
  const input=make([{title:wrapped('Actual title'),url:'https://example.com/'+'x'.repeat(9000),description:wrapped('evidence')}]);
  const out=projectNativeWebSearchResult(message,input);
  assert.ok(out.content[0].text.length<=8014);assert.ok(out.content[0].text.endsWith('\n…(truncated)…'));
  assert.equal(out.content.length,2);assert.deepEqual(out.details,input.details);
});

test('deferred tool envelope is not subjected to a different native-only cap',()=>{
  const input=make(rows()),tool={id:'openclaw:core:web_search',name:'web_search',source:'openclaw',sourceName:'core'};
  const out=projectWebResult({...message,toolName:'tool_call'},{tool,result:input});
  assert.equal(out.content[1].text,input.content[0].text);assert.deepEqual(out.details.result,input);
});

test('a partial result not matching the exact native sanitizer cannot recover hidden leads',()=>{
  const input=make(rows());input.content[0].text=input.content[0].text.slice(0,2000)+'\n…(truncated)…';
  assert.equal(projectNativeWebSearchResult(message,input),undefined);
  assert.equal(captureNativeWebSearchResult(input),undefined);
});
