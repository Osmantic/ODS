import test from 'node:test';
import assert from 'node:assert/strict';
import {createAskUserTool,parseQuestions,choiceQuestionFromText} from '../plugin/ask-user.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {readFileSync} from 'node:fs';
const questions=[{id:'style',question:'Qual estilo você prefere?',options:['Minimalista','Colorido']}];

test('isolated plugin and installed ingress share the same bounded schema',()=>{
  assert.equal(readFileSync(new URL('../plugin/questions-schema.mjs',import.meta.url),'utf8'),readFileSync(new URL('../host/questions_schema.mjs',import.meta.url),'utf8'));
});
test('bounded question contract rejects malformed or oversized model requests',async()=>{
  const tool=createAskUserTool();
  assert.equal((await tool.execute('a',{questions})).details.status,'awaiting_user');
  for(const value of [[],[...questions,...questions],Array(4).fill(questions[0]),[{...questions[0],question:'x'.repeat(301)}],[{...questions[0],options:['one']}],[{...questions[0],options:['a','a']}],[{...questions[0],html:'<script/>'}]]) {
    assert.equal(parseQuestions(value),null);
    assert.equal((await tool.execute('a',{questions:value})).isError,true);
  }
});

test('advertised ID constraints agree with executable validation at string boundaries',async()=>{
  const tool=createAskUserTool();
  const schema=tool.parameters.properties.questions.items.properties.id;
  const samples=[['a',true],['question_2',true],['a'.repeat(40),true],['a'.repeat(41),false],
    ['',false],['Style',false],['2style',false],['a-b',false],['a b',false],['é',false],
    ['a\n',false],['a'.repeat(40)+'\n',false]];
  for (const [id,expected] of samples) {
    const advertised=(schema.minLength===undefined || id.length>=schema.minLength) &&
      (schema.maxLength===undefined || id.length<=schema.maxLength) &&
      (schema.pattern===undefined || new RegExp(schema.pattern).test(id));
    const value=[{...questions[0],id}];
    assert.equal(advertised,expected,`advertised ID ${JSON.stringify(id)}`);
    assert.equal(Boolean(parseQuestions(value)),advertised,`parsed ID ${JSON.stringify(id)}`);
    assert.equal((await tool.execute('id-boundary',{questions:value})).details?.status==='awaiting_user',advertised);
  }
});

test('distinct questions still reject repeated IDs and advertise request-wide uniqueness',async()=>{
  const tool=createAskUserTool();
  assert.match(tool.parameters.properties.questions.items.properties.id.description,/unique within this questions array/i);
  const first={id:'style',question:'What style of response should I use?',options:['Minimal and concise','Detailed with time calculations']};
  const second={id:'style',question:'Do you want me to verify my calculations?',options:['Yes','No']};
  const rejected=await tool.execute('duplicate-ids',{questions:[first,second]});
  assert.equal(rejected.isError,true);
  assert.equal(rejected.details,undefined);
  const corrected=await tool.execute('unique-ids',{questions:[first,{...second,id:'verify'}]});
  assert.equal(corrected.details.status,'awaiting_user');
  assert.deepEqual(corrected.details.questions.map(q=>q.id),['style','verify']);
});

test('normalizes observed small-model field aliases without relaxing delivery bounds',async()=>{
  const tool=createAskUserTool();
  const input=[{text:'Qual estilo?',options:[{text:'Clean'},{text:'Colorido'}],wait:true}];
  assert.equal(parseQuestions(input),null);
  assert.deepEqual((await tool.execute('ask',{questions:input})).details.questions,[{id:'question_1',question:'Qual estilo?',options:['Clean','Colorido']}]);
  assert.deepEqual((await tool.execute('ask',{questions:[{text:'Qual estilo?',choices:input[0].options,required:false}]})).details.questions,[{id:'question_1',question:'Qual estilo?',options:['Clean','Colorido']}]);
  for(const q of [{...input[0],wait:false},{...input[0],question:'Different question'},{...input[0],options:[{text:'a',execute:'x'},{text:'b'}]}]) {
    assert.equal((await tool.execute('ask',{questions:[q]})).isError,true);
  }
});
test('a real question receipt pauses tools, finalizes and delivers structured questions',async()=>{
  const guard=createToolLoopGuard(),ctx={agentId:'pixel',runId:'questions-fixture',sessionId:'session'};
  guard.observeRun(ctx,'pixel',{prompt:'Pergunte o estilo antes de criar.'});
  const result=await createAskUserTool().execute('ask',{questions});
  guard.afterToolCall({toolName:'pixel_ods_ask_user',result},{...ctx,toolCallId:'ask'});
  assert.equal(guard.beforeToolCall({toolName:'exec',params:{command:'echo test'}},ctx).block,true);
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage:'Vou escolher por você.'},ctx).action,'finalize');
  assert.deepEqual(guard.deliveryVerificationForRun(ctx.runId).questions,questions);
  assert.equal(guard.deliveryVerificationForRun(ctx.runId).status,'pending');
  const next={...ctx,runId:'next-turn'};guard.observeRun(next,'pixel',{prompt:'Minimalista'});
  assert.equal(guard.deliveryVerificationForRun(next.runId).questions,undefined);
});

test('deferred dispatch requires matching tool provenance and allows questions before website work',async()=>{
  const name='pixel_ods_ask_user',id=`openclaw:pixel-ods:${name}`;
  const result=await createAskUserTool().execute('ask',{questions});
  for (const valid of [true,false]) {
    const guard=createToolLoopGuard(),ctx={agentId:'pixel',runId:`wrapped-${valid}`,sessionId:'session'};
    guard.observeRun(ctx,'pixel',{prompt:'Crie um site, mas pergunte o estilo antes.'});
    const event={toolName:'tool_call',params:{id,args:{questions}},result:{details:{tool:{id,source:'openclaw',sourceName:valid?'pixel-ods':'other',name},result}}};
    assert.equal(guard.beforeToolCall(event,ctx)?.block,undefined);
    guard.afterToolCall(event,{...ctx,toolCallId:'ask'});
    assert.equal(Boolean(guard.deliveryVerificationForRun(ctx.runId).questions),valid);
  }
});

test('explicit choice requests repair a plain preference question without retrying the model',()=>{
  const text='Qual cor principal você prefere?\n\n- Azul - calma\n- Roxo - criatividade\n- Vermelho - energia';
  assert.equal(choiceQuestionFromText(text).length,1);
  for(const invalid of ['Example:\n'+text,'```\n'+text+'\n```',text+'\nExtra prose',text.replace('Qual cor principal você prefere?','Como instalar?'),text.replace('- Azul','1. Azul')]) assert.equal(choiceQuestionFromText(invalid),null);
  for (const prompt of ['Me pergunte qual cor com opções para escolher.','Traduza este texto de perguntas com opções','Explique opções de cores.']) {
    const guard=createToolLoopGuard(),ctx={agentId:'pixel',runId:prompt,sessionId:'session'};
    guard.observeRun(ctx,'pixel',{prompt});
    guard.beforeAgentFinalize({lastAssistantMessage:text},ctx);
    assert.equal(Boolean(guard.deliveryVerificationForRun(ctx.runId).questions),prompt.startsWith('Me pergunte'));
  }
});
