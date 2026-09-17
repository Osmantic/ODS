// Qualify the installed SDK's pressure decision with a short incremental input.
// No gateway request, model inference, or owner transcript is read or changed.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const packageDir=process.env.OPENCLAW_PACKAGE_DIR;
if(!packageDir || !path.isAbsolute(packageDir)) throw new Error('Set OPENCLAW_PACKAGE_DIR to the installed pinned package.');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'ods-autocompact-sdk-'));
process.env.OPENCLAW_STATE_DIR=temporary;
process.env.OPENCLAW_CONFIG_PATH=path.join(temporary,'openclaw.json');
try {
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,'{}',{mode:0o600});
  const {shouldPreemptivelyCompactBeforePrompt}=await import(pathToFileURL(path.join(packageDir,'dist','plugin-sdk','agent-harness-runtime.js')).href);
  const prompt='Continue with the next step.';
  const history=Array.from({length:32},(_,index)=>({
    role:index%2?'assistant':'user',
    content:[{type:'text',text:`Archived turn ${index}: ${'Past conversation facts and decisions. '.repeat(400)}`}],
    timestamp:index+1,
  }));
  const results=[];
  for(const contextTokenBudget of [8192,32768,65536]) {
    const base={systemPrompt:'You are Portal.',prompt,contextTokenBudget,reserveTokens:19661};
    const fresh=shouldPreemptivelyCompactBeforePrompt({...base,messages:[]});
    assert.equal(fresh.shouldCompact,false,`short input alone must fit ${contextTokenBudget}`);
    const accumulated=shouldPreemptivelyCompactBeforePrompt({...base,messages:history});
    assert.equal(accumulated.shouldCompact,true,`native history must still trigger compact at ${contextTokenBudget}`);
    assert.equal(accumulated.route,'compact_only');
    assert.ok(accumulated.estimatedPromptTokens>fresh.estimatedPromptTokens);
    const windowed=shouldPreemptivelyCompactBeforePrompt({...base,messages:[],unwindowedMessages:history});
    assert.equal(windowed.shouldCompact,true,'context-engine history must not bypass pressure checks');
    results.push({contextTokenBudget,route:accumulated.route,promptBudget:accumulated.promptBudgetBeforeReserve});
  }
  console.log(JSON.stringify({qualified:'incremental-input-retains-native-auto-compaction',results}));
} finally {fs.rmSync(temporary,{recursive:true,force:true});}
