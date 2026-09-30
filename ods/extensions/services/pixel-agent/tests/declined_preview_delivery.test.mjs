import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, userMessageRequestsWorkspacePreview} from '../plugin/tool-loop-guard.mjs';

for (const prompt of [
  'Crie despesas.csv e resumo.md, leia os dois e entregue links para baixar. Não crie site e não publique preview.',
  'Create expenses.csv and summary.md and provide downloads. Do not publish a preview.',
  'Em Playground/ods-qa-delivery-retest-20260930-1525, crie despesas.csv com cabeçalho categoria,valor e duas linhas Hospedagem,120 e Domínio,40. Crie também resumo.md em português informando total de R$ 160,00. Leia os dois arquivos de volta e entregue links para baixar ambos com esses nomes. Não crie site nem publique preview. Não apague nada existente.',
  'Crie os arquivos para download. Não construa um site nem mostre uma pré-visualização.',
  'Crie um CSV. Não edite os arquivos existentes ou publique um preview.',
  'Create downloadable files. Do not create a website nor publish a preview.',
]) {
  test(`a declined preview error does not invent a website delivery obligation: ${prompt}`, () => {
    const guard=createToolLoopGuard();
    const context={agentId:'pixel',runId:'file-delivery',sessionId:'file-delivery',toolCallId:'preview'};
    guard.observeRun(context,'pixel',{prompt});
    assert.equal(userMessageRequestsWorkspacePreview([],prompt),false);
    assert.equal(guard.verificationForRun(context.runId).status,'none');
    const event={toolName:'pixel_ods_workspace_preview',params:{relativeDirectory:'reports'}};
    const refusal=guard.beforeToolCall(event,context);
    assert.equal(refusal.block,true);
    guard.afterToolCall({...event,result:{isError:true,content:[{type:'text',text:refusal.blockReason}]}},context);
    const verification=guard.verificationForRun(context.runId);
    assert.equal(verification.status,'none');
    assert.equal(verification.preview,undefined);
    assert.doesNotMatch(verification.text ?? '',/website|preview is ready/);
    // Unexpected success after denial must never become a valid publication.
    guard.afterToolCall({...event,result:{isError:false,details:{status:'published'}}},context);
    assert.equal(guard.verificationForRun(context.runId).status,'failed');
  });
}

test('independent positive publication and real websites keep their verification obligation',()=>{
  for (const prompt of ['Crie um site e publique preview.',
    'Não altere os arquivos. Publique o site existente.',
    'Do not delete the files, but publish the existing website.']) {
    const guard=createToolLoopGuard();
    const context={agentId:'pixel',runId:prompt,sessionId:'positive'};
    guard.observeRun(context,'pixel',{prompt});
    assert.equal(userMessageRequestsWorkspacePreview([],prompt),true,prompt);
    assert.equal(guard.verificationForRun(context.runId).status,'failed',prompt);
  }
});

test('a failed requested preview still reports incomplete website delivery',()=>{
  const guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:'site',sessionId:'site'};
  guard.observeRun(context,'pixel',{prompt:'Build and publish a website preview.'});
  guard.afterToolCall({toolName:'pixel_ods_workspace_preview',params:{relativeDirectory:'site'},result:{isError:true}},context);
  assert.equal(guard.verificationForRun(context.runId).status,'failed');
  assert.match(guard.verificationForRun(context.runId).text,/website/);
});
