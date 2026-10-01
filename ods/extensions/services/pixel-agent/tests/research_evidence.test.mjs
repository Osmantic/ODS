import test from 'node:test';
import assert from 'node:assert/strict';
import {createCompletionAssurance} from '../plugin/completion-assurance.mjs';
import {selectEvidenceWindow} from '../plugin/web-extract.mjs';
const official = 'https://docs.reportlab.com/reportlab/userguide/ch7_tables/';
const mirror = 'https://github.com/eugen-goebel/fpdf2/blob/master/docs/Unicode.md';
const search = url => ({result:{details:{results:[{url}]}}});
const read = url => ({result:{details:{url,status:200,text:'Returned page text.'}}});
for (const request of [
  'Pesquise e compare ReportLab e fpdf2. Use somente documentação e repositórios oficiais consultados agora. Se uma fonte não abrir, declare o limite sem inventar a leitura.',
  'Use somente fontes oficiais consultadas agora. Não instale dependências. Se uma fonte não abrir, declare o limite.',
  'Compare using only official documentation and repositories consulted now. If a source cannot be opened, disclose that limitation.',
]) test(`official source reads remain required: ${request}`, () => {
  const guard=createCompletionAssurance(); guard.begin(request);
  guard.observe('web_search', search(official));
  assert.equal(guard.finalize(`Verified conclusion: ${official}`)?.retry?.idempotencyKey,'ods-opened-source-attribution');
  guard.observe('web_fetch',read(official));
  const answer=`Supported conclusion: ${official}`;
  assert.equal(guard.finalize(answer),undefined);
  assert.equal(guard.terminal,undefined);
});
test('omitted citations never append discarded search or read pages', () => {
  for (const request of ['Search the web for PDF libraries.','Pesquise usando somente documentação oficial consultada agora.']) {
    const guard=createCompletionAssurance(); guard.begin(request);
    guard.observe('web_search',search(mirror)); guard.observe('web_fetch',read(mirror)); guard.observe('web_fetch',read(official));
    const answer='Relatório salvo. O espelho não oficial foi descartado.';
    for(let i=0;i<3;i++) guard.finalize(answer);
    assert.ok(guard.terminal.startsWith(answer));
    assert.equal(guard.terminalStatus,'failed');
    assert.doesNotMatch(guard.terminal,/https?:/);
    assert.match(guard.terminal,/attribution|atribui/i);
  }
});
test('repeated headings expose occurrence count and bounded navigation beyond TOC', () => {
  const heading='TableStyle Span Commands';
  const text=`Contents\n${heading}\n`+'Intro with no simple spanning.\n'.repeat(450)+`${heading}\nSPAN merges rectangular cells.\n`;
  const first=selectEvidenceWindow(text,heading);
  assert.equal(first.matchCount,2); assert.equal(first.occurrence,1);
  assert.equal(first.matchKind,'literal'); assert.equal(first.nextOccurrence,2);
  assert.doesNotMatch(first.text,/SPAN merges/);
  const second=selectEvidenceWindow(text,heading,2);
  assert.match(second.text,/SPAN merges/); assert.equal(second.occurrence,2);
  assert.equal(second.nextOccurrence,null); assert.equal(second.matchOffset,text.lastIndexOf(heading));
  assert.equal(second.text,text.slice(second.startOffset,second.endOffset));
  assert.ok(second.text.length<=6000);
  assert.equal(selectEvidenceWindow(text,heading,3).outOfRange,true);
});


test('explicit do-not-read commands do not require page reads, unlike conditional failures', () => {
  for(const request of ['Do not read sources. Explain from memory.','Não leia fontes. Explique de memória.','Translate: use official documentation.','Search official docs, but do not open any links.','Use fontes oficiais sem ler as páginas.','Search official sources without reading pages.','Use official sources. If a source cannot open, do not read any mirrors.']) {
    const guard=createCompletionAssurance(); guard.begin(request);
    guard.observe('web_search',search('https://example.org/page'));
    assert.equal(guard.unverifiedCitations('Example: https://example.org/page').length,0,request);
    assert.equal(guard.finalize('Example: https://example.org/page'),undefined,request);
  }
});
test('selected read citations remain byte-identical without discarded source injection', () => {
  const guard=createCompletionAssurance(); guard.begin('Pesquise com fontes oficiais consultadas agora.');
  guard.observe('web_search',search(mirror)); guard.observe('web_fetch',read(official));
  const answer=`A tabela suporta SPAN. [Documentação](${official}#tablestyle-span-commands)\nEspelhos foram descartados.`;
  assert.equal(guard.finalize(answer),undefined); assert.equal(guard.terminal,undefined);
});
