import test from 'node:test';
import assert from 'node:assert/strict';
import {createCompletionAssurance, promisesExecution, researchRequested, executionContext} from '../plugin/completion-assurance.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

test('screenshot research request requires evidence, not an answer from memory', () => {
  const guard = createCompletionAssurance();
  guard.begin('queria saber notícias de hoje sobre o stf dia 15/09/2026');
  assert.equal(guard.finalize('As notícias são estas.')?.action, 'revise');
});
test('two revisions then honest terminal outcome; discovery is not research', () => {
  const guard = createCompletionAssurance();
  guard.begin('ok consulte.');
  guard.observe('tool_search', {result:{content:[{text:'web_search'}]}});
  for (let n=0;n<2;n++) assert.equal(guard.finalize('Vou consultar agora as notícias sobre o STF.')?.action, 'revise');
  assert.equal(guard.finalize('Vou consultar agora.')?.action, 'finalize');
  assert.match(guard.terminal, /incompleta/);
});
test('successful web receipt allows answer; failed/wrapped receipt cannot count', () => {
  const guard = createCompletionAssurance();
  guard.begin('Search the web for current news');
  for (const [name,result] of [['web_search',{isError:true}],['web_fetch',{details:{status:'blocked'}}],['tool_call',{content:[{text:'searched'}]}]]) {
    guard.observe(name,{result});
  }
  assert.equal(guard.finalize('Here are the headlines.')?.action, 'revise');
  guard.observe('web_search',{result:{details:{results:[{url:'https://example.org/news'}]}}});
  assert.equal(guard.finalize('Results: https://example.org/news'), undefined);
  assert.equal(guard.terminal, undefined);
});
test('candid limitations do not loop and state is run-local', () => {
  const a=createCompletionAssurance(),b=createCompletionAssurance();
  a.begin('Notícias de hoje');b.begin('Notícias de hoje');
  a.observe('web_search',{result:{details:{results:[{url:'https://example.org/news'}]}}});
  assert.equal(a.finalize('Results: https://example.org/news'),undefined);
  assert.equal(b.finalize('Results')?.action,'revise');
  assert.equal(b.finalize('Não consegui pesquisar: serviço indisponível.'),undefined);
});
test('greetings, translations, explanations and explicit offline requests are conversational', () => {
  for (const text of ['oi','Qual o seu nome?','Traduza: vou pesquisar notícias hoje','Explique como pesquisar na internet','Não consulte a internet; explique STF','Search algorithms explained']) {
    assert.equal(researchRequested(text),false,text);
    const guard=createCompletionAssurance();guard.begin(text);
    assert.equal(guard.finalize('Uma resposta normal.'),undefined);
  }
  assert.equal(promisesExecution('> Vou pesquisar agora.\nEsse é um exemplo.'),false);
  assert.equal(promisesExecution('```\nI will run the code.\n```'),false);
});
test('promise detection supports Portuguese and English without counting future discussion', () => {
  for (const text of ['Vou procurar as notícias de hoje.','Sim! Vou consultar agora.','I will search for sources.']) assert.equal(promisesExecution(text),true,text);
  for (const text of ['Você pode pesquisar online.','Posso pesquisar se você quiser.','I will be happy to help.']) assert.equal(promisesExecution(text),false,text);
});
test('actual guard wires bounded recovery and truthful delivery', () => {
  const guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:'research-fixture',sessionId:'research-session'};
  guard.observeRun(context,'pixel',{prompt:'notícias de hoje'});
  const event={lastAssistantMessage:'Vou pesquisar agora.'};
  assert.equal(guard.beforeAgentFinalize(event,context)?.action,'revise');
  assert.equal(guard.beforeAgentFinalize(event,context)?.action,'revise');
  assert.equal(guard.beforeAgentFinalize(event,context)?.action,'finalize');
  assert.equal(guard.deliveryVerificationForRun(context.runId).status,'failed');
  assert.match(guard.deliveryVerificationForRun(context.runId).text,/incompleta/);
  assert.equal(guard.beforeAgentFinalize(event,{...context,agentId:'other'}),undefined);
});

test('installation promises trigger bounded recovery rather than a successful empty turn', () => {
  for (const text of ['OK, vou começar a instalar o pacote agora mesmo.',
    'Sim, vou instalar a extensão.', 'Vou configurar o serviço.',
    'Okay, I will start to install the package.', "I'll download the source."]) {
    const guard = createCompletionAssurance();
    guard.begin('/extensions https://github.com/example/package pode começar a instalar.');
    assert.equal(guard.finalize(text)?.action, 'revise', text);
    assert.equal(guard.finalize(text)?.action, 'revise', text);
    assert.equal(guard.finalize(text)?.action, 'finalize', text);
    assert.equal(guard.terminalStatus, 'failed');
    assert.match(guard.terminal, /incompleta/);
  }
});

test('installation examples, permission questions and failures never authorize execution', () => {
  for (const [request, reply] of [
    ['Traduza: I will install the package.', 'Vou instalar o pacote.'],
    ['Explique a instalação.', 'OK, vou começar a instalar.'],
    ['Prepare a instalação.', 'Vou instalar. Posso confirmar?'],
    ['Instale a extensão.', 'Não consegui instalar: runtime indisponível.'],
    ['Mostre um exemplo.', '> OK, vou começar a instalar.'],
  ]) {
    const guard = createCompletionAssurance();
    guard.begin(request);
    assert.equal(guard.finalize(reply), undefined, reply);
  }
});
test('literal promise requests and approval questions must never initiate execution', () => {
  const guard=createCompletionAssurance();guard.begin('Repita exatamente: Vou pesquisar agora.');
  assert.equal(guard.finalize('Vou pesquisar agora.'),undefined);
  const approval=createCompletionAssurance();approval.begin('Prepare uma publicação.');
  assert.equal(approval.finalize('Vou enviar a publicação. Posso confirmar?'),undefined);
});
test('a promise after partial progress still needs a delivered result', () => {
  const guard=createCompletionAssurance();guard.begin('Pesquise na internet sobre o STF');
  guard.observe('web_search',{result:{content:[{text:'Sources'}]}});
  assert.equal(guard.finalize('Vou consultar as fontes agora.')?.action,'revise');
});
test('clock context is explicit, portable and preserves requested dates', () => {
  const value=executionContext(new Date('2026-09-16T00:03:00Z'));
  assert.match(value,/2026-09-16T00:03:00.000Z/);
  assert.match(value,/owner's explicit date and timezone/);
  assert.match(value,/prefer write and verify the bytes with read/);
  assert.match(value,/portable printf/);
  assert.match(value,/Do not claim a match when readback differs/);
});
test('sources must come from structured tool evidence, not invented prose links', () => {
  const guard=createCompletionAssurance();guard.begin('Notícias de hoje');
  guard.observe('web_search',{result:{content:[{type:'text',text:JSON.stringify({results:[
    {url:'https://example.org/news'},{url:'javascript:alert(1)'},{url:'http://localhost/secret'},
    {url:'https://user:password@example.org/'},
  ]})}]}});
  for (let i=0;i<2;i++) assert.equal(guard.finalize('Resumo sem fontes.')?.action,'revise');
  assert.equal(guard.finalize('Resumo sem fontes.')?.action,'finalize');
  assert.match(guard.terminal,/https:\/\/example.org\/news/);
  assert.doesNotMatch(guard.terminal,/javascript|localhost|password/);
  assert.equal(guard.finalize('Resumo: [fonte](https://example.org/news)'),undefined);
  assert.equal(guard.terminal,undefined);
});
test('empty search is an attempt, not evidence for news from memory', () => {
  const guard=createCompletionAssurance();guard.begin('Notícias de hoje');
  guard.observe('web_search',{result:{details:{count:0,results:[]}}});
  assert.equal(guard.finalize('Estas são as notícias de hoje.')?.action,'revise');
  assert.match(guard.terminal,/incompleta/);
  assert.equal(guard.finalize('Não consegui encontrar fontes para essa data.'),undefined);
});
test('short continuation keeps research requirements from the preceding owner request', () => {
  const guard=createCompletionAssurance();
  guard.begin('ok consulte.',{prompt:'[Chat messages since your last reply - for context]\nUser: Pesquise notícias de hoje sobre o STF.\nAssistant: Vou procurar.\n\n[Current message - respond to this]\nUser: ok consulte.'});
  guard.observe('web_search',{result:{details:{count:0,results:[]}}});
  assert.equal(guard.finalize('Estas são as notícias.')?.action,'revise');
  const other=createCompletionAssurance();
  other.begin('continue',{messages:[{role:'user',content:'Notícias de hoje'},{role:'user',content:'Explique álgebra'},{role:'user',content:'continue'}]});
  assert.equal(other.finalize('Explicação de álgebra.'),undefined);
});

const pageReceipt = (url, status = 200) => ({result:{details:{url, finalUrl:url, status, text:'Returned page evidence.'}}});
function sourceReadGuard() {
  const guard = createCompletionAssurance();
  guard.begin('Search the web and open sources before citing findings.');
  return guard;
}

test('explicit source reads distinguish search leads from successfully opened pages', () => {
  const guard = sourceReadGuard(), url = 'https://example.org/event';
  guard.observe('web_search', {result:{details:{results:[{url}]}}});
  const answer = `The event is confirmed. [Source](${url})`;
  const correction = guard.finalize(answer);
  assert.equal(correction?.action, 'revise');
  assert.equal(correction.retry.maxAttempts, 1);
  assert.equal(guard.terminalStatus, 'failed');
  assert.doesNotMatch(guard.terminal, /event is confirmed/);
  guard.observe('web_fetch', pageReceipt(url));
  assert.equal(guard.finalize(answer), undefined);
  assert.equal(guard.terminal, undefined);
});

test('403, failed, empty and wrapped receipts cannot establish source reads', () => {
  for (const [tool, receipt] of [
    ['web_fetch', pageReceipt('https://example.org/page', 403)],
    ['web_fetch', {...pageReceipt('https://example.org/page'), error:'failed'}],
    ['web_fetch', {result:{...pageReceipt('https://example.org/page').result,isError:true}}],
    ['web_fetch', {result:{details:{url:'https://example.org/page',status:200,text:''}}}],
    ['tool_call', pageReceipt('https://example.org/page')],
  ]) {
    const guard = sourceReadGuard(); guard.observe(tool, receipt);
    assert.equal(guard.finalize('Verified: https://example.org/page')?.action, 'revise');
    assert.equal(guard.finalize('Verified: https://example.org/page')?.action, 'finalize');
    assert.equal(guard.terminalStatus, 'failed');
  }
});

test('honest local limitations do not repeat denied fetches', () => {
  for (const answer of [
    'I could not read the sources; the task remains incomplete.',
    'Unverified search lead: https://example.org/page',
    'https://example.org/page — not opened because the fetch returned HTTP 403.',
    '[Source not verified](https://example.org/page).',
  ]) {
    const guard = sourceReadGuard();
    guard.observe('web_search',{result:{details:{results:[{url:'https://example.org/page'}]}}});
    guard.observe('web_fetch',pageReceipt('https://example.org/page',403));
    assert.equal(guard.finalize(answer), undefined, answer);
  }
});

test('one valid source or unrelated limitation cannot cover an unread citation', () => {
  const guard = sourceReadGuard();
  guard.observe('web_fetch',pageReceipt('https://example.org/read'));
  for (const answer of [
    'Fact: https://example.org/read. Another fact: https://example.org/unread. Retail availability is unavailable.',
    'https://example.org/read — unavailable stock. Another fact: https://example.org/unread.',
  ]) assert.ok(guard.finalize(answer), answer);
  assert.equal(guard.finalize('Fact: https://example.org/read. Unverified lead: https://example.org/unread'), undefined);
  assert.equal(guard.terminal, undefined);
});

test('redirects and page anchors count; neighboring paths and guessed replacements do not', () => {
  const guard = sourceReadGuard();
  const receipt = pageReceipt('https://example.org/old');
  receipt.result.details.finalUrl = 'https://example.org/page_(topic)';
  guard.observe('web_fetch',receipt);
  assert.equal(guard.finalize('[Read](https://example.org/page_(topic)#section)'), undefined);
  assert.equal(guard.finalize('Original: https://example.org/old'), undefined);
  assert.equal(guard.finalize('Source: https://example.org/page_(topic)/other')?.action, 'revise');
});

test('targeted extraction requires returned evidence, not an unmatched query or recovered missing URL', () => {
  for (const [details, accepted] of [
    [{matched:true},true], [{matched:false,mode:'overview'},true], [{matched:false},false],
  ]) {
    const guard = sourceReadGuard();
    guard.observe('pixel_ods_web_extract',{result:{details:{boundary:'public-web-read-only',source_url:'https://example.org/read',...details},content:[{type:'text',text:'Evidence'}]}});
    // Ordinary citation presence must also recognize the exact extraction URL.
    const result = guard.finalize('Read: https://example.org/read');
    assert.equal(result?.action, accepted ? undefined : 'revise');
  }
});

test('source-read state is current-turn only and JSON citations remain subject to the check', () => {
  const first = sourceReadGuard(), second = sourceReadGuard();
  first.observe('web_fetch',pageReceipt('https://example.org/read'));
  const answer = '```json\n{"source":"https://example.org/read","value":42}\n```';
  assert.equal(first.finalize(answer),undefined);
  assert.equal(second.finalize(answer)?.action,'revise');
});

test('ordinary search, explicitly unread lists and translation requests retain their prior behavior', () => {
  for (const request of ['Search the web for sources.', 'Search the web; do not open sources.', 'Translate: open sources.']) {
    const guard = createCompletionAssurance(); guard.begin(request);
    guard.observe('web_search',{result:{details:{results:[{url:'https://example.org/lead'}]}}});
    assert.equal(guard.finalize('https://example.org/lead'),undefined,request);
  }
});

for (const name of ['web_fetch','pixel_ods_web_extract']) for (const deferred of [false,true]) {
  test(`actual guard preserves failed delivery until a successful read: ${name}, deferred=${deferred}`, () => {
    const guard = createToolLoopGuard();
    const context = {agentId:'pixel',runId:'source-read-run',sessionId:'source-read-session'};
    guard.observeRun(context,'pixel',{prompt:'Search the web and open sources before citing findings.'});
    const answer = {lastAssistantMessage:'Confirmed finding: https://example.org/page'};
    assert.equal(guard.beforeAgentFinalize(answer,context)?.action,'revise');
    assert.equal(guard.deliveryVerificationForRun(context.runId).status,'failed');
    const params = {url:'https://example.org/page'};
    const sourceName = name === 'web_fetch' ? 'core' : 'pixel-ods';
    const id = `openclaw:${sourceName}:${name}`;
    const toolName = deferred ? 'tool_call' : name;
    const args = deferred ? {id,args:params} : params;
    const call = {toolName,params:args,toolCallId:'read-call'};
    const ctx = {...context,toolName,toolCallId:'read-call'};
    assert.notEqual(guard.beforeToolCall(call,ctx)?.block,true);
    const result = name === 'web_fetch' ? pageReceipt(params.url).result : {
      details:{boundary:'public-web-read-only',source_url:params.url,matched:true},content:[{type:'text',text:'Page evidence'}],
    };
    const envelope = {tool:{id,source:'openclaw',sourceName,name},result};
    const observed = deferred ? {content:[{type:'text',text:JSON.stringify(envelope)}],details:envelope} : result;
    guard.afterToolCall({...call,result:observed},ctx);
    assert.equal(guard.beforeAgentFinalize(answer,context),undefined);
    assert.notEqual(guard.deliveryVerificationForRun(context.runId)?.status,'failed');
  });
}

test('short continuation carries the requested source-read boundary without trusting old receipts', () => {
  const guard = createCompletionAssurance();
  guard.begin('continue',{messages:[{role:'user',content:'Search the web and open sources.'},{role:'user',content:'continue'}]});
  assert.equal(guard.finalize('Verified https://example.org/source')?.action,'revise');
});

test('successful browser snapshot retains existing assurance; discovery, navigation and failures do not bypass', () => {
  const result = {details:{ok:true,url:'https://example.org/page'},content:[{type:'text',text:'Visible page snapshot'}]};
  for (const [tool, event, fallback] of [
    ['browser',{params:{action:'snapshot'},result},true],
    ['browser',{params:{action:'navigate'},result},false],
    ['tool_search',{params:{action:'snapshot'},result},false],
    ['browser',{params:{action:'snapshot'},result:{...result,isError:true}},false],
    ['browser',{params:{action:'snapshot'},result:{...result,details:{...result.details,ok:false}}},false],
    ['browser',{params:{action:'snapshot'},result:{...result,details:{...result.details,status:403}}},false],
    ['browser',{params:{action:'snapshot'},result:{details:result.details,content:[]}},false],
  ]) {
    const guard = sourceReadGuard(); guard.observe(tool,event);
    assert.equal(guard.finalize('Source: https://example.org/page')?.action, fallback ? undefined : 'revise');
  }
  const guard = sourceReadGuard();
  guard.observe('browser',{params:{action:'snapshot'},result});
  assert.equal(guard.finalize('A research answer without citations.')?.action,'revise','ordinary attribution check remains enabled');
  assert.equal(guard.finalize('Source: https://example.org/page. Other fact: https://example.org/unread')?.retry?.idempotencyKey,
    'ods-opened-source-attribution','snapshot of A cannot excuse an unread B');
});

test('actual source-reading request wording enables the boundary', () => {
  const guard = createCompletionAssurance();
  guard.begin('Search the live web for public events. Actually search and open sources. Give a direct official source URL. Explain any unavailable result honestly.');
  guard.observe('web_search',{result:{details:{results:[{url:'https://example.org/event'}]}}});
  assert.equal(guard.finalize('Verified: https://example.org/event')?.retry?.idempotencyKey,'ods-opened-source-attribution');
});

test('omitted citations cannot turn search-only leads into successful requested reads', () => {
  const guard = sourceReadGuard();
  guard.observe('web_search',{result:{details:{results:[{url:'https://example.org/lead'}]}}});
  for (let i = 0; i < 2; i++) assert.equal(guard.finalize('Here is a finding without a source link.')?.action,'revise');
  assert.equal(guard.finalize('Here is a finding without a source link.')?.action,'finalize');
  assert.equal(guard.terminalStatus,'failed');
  assert.doesNotMatch(guard.terminal,/finding|example.org/);
  assert.match(guard.terminal,/incomplete/);
  assert.equal(guard.finalize('Unverified lead, not opened: https://example.org/lead'),undefined);
  assert.equal(guard.terminal,undefined);
});

test('a failed read can be disclosed with its URL without first running search', () => {
  const guard = sourceReadGuard();
  guard.observe('web_fetch',{result:{...pageReceipt('https://example.org/page',403).result,isError:true}});
  assert.equal(guard.finalize('Not opened: https://example.org/page (HTTP 403). Research remains incomplete.'),undefined);
});

test('citation fallback after an actual read cannot append other unread search leads', () => {
  const guard = sourceReadGuard();
  guard.observe('web_search',{result:{details:{results:[{url:'https://example.org/lead'}]}}});
  guard.observe('web_fetch',pageReceipt('https://example.org/read'));
  for (let i=0;i<3;i++) guard.finalize('Finding without a citation.');
  assert.match(guard.terminal,/https:\/\/example.org\/read/);
  assert.doesNotMatch(guard.terminal,/https:\/\/example.org\/lead/);
});
