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
