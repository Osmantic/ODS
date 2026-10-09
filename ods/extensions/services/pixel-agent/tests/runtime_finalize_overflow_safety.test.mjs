// Negative contract controls complement the real provider/compaction fixture.
// Execute the exact installed recipe block: missing safety evidence is unsafe.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const recipe=JSON.parse(fs.readFileSync(new URL('../host/openclaw-yield-usage.json',import.meta.url)));
const block=recipe.replacements.find(([before])=>before.startsWith('\t\t\t\t\tif (contextOverflowError)'))[1];
const logic=block.slice(block.indexOf('// An initial precheck'),block.indexOf('const overflowDiagId'));
function recover(overrides={}){
  const context=vm.createContext({promptErrorSource:undefined,preflightRecovery:undefined,timedOut:false,
    contextOverflowError:{source:'assistantError'},currentAttemptAssistant:{stopReason:'error'},
    attempt:{replayMetadata:{replaySafe:true,hadPotentialSideEffects:false},toolMetas:[],itemLifecycle:{startedCount:0}},
    attemptPromptOverride:'exact pending revision',nextAttemptPromptOverride:null,terminal:false,
    hasAttemptTerminalState:()=>context.terminal,...overrides});
  context.continueFromCurrentTranscript=()=>{context.nextAttemptPromptOverride='native transcript continuation';};
  vm.runInContext(logic,context);return context.nextAttemptPromptOverride;
}
test('provider rejection with explicit no-work proof retains the pending revision',()=>assert.equal(recover(),'exact pending revision'));
for(const metadata of [{},{replaySafe:true},{hadPotentialSideEffects:false},{replaySafe:false,hadPotentialSideEffects:false},
  {replaySafe:true,hadPotentialSideEffects:true},{replaySafe:1,hadPotentialSideEffects:0}])
test('provider overflow cannot infer safe replay from '+JSON.stringify(metadata),()=>{
  assert.equal(recover({attempt:{replayMetadata:metadata,toolMetas:[],itemLifecycle:{startedCount:0}}}),null);
});
for(const [name,overrides] of [
  ['started external item',{attempt:{replayMetadata:{replaySafe:true,hadPotentialSideEffects:false},toolMetas:[],itemLifecycle:{startedCount:1}}}],
  ['unknown item count',{attempt:{replayMetadata:{replaySafe:true,hadPotentialSideEffects:false},toolMetas:[],itemLifecycle:{}}}],
  ['terminal external delivery',{terminal:true}],['timeout',{timedOut:true}],
  ['historical error only',{currentAttemptAssistant:undefined}],['successful assistant',{currentAttemptAssistant:{stopReason:'stop'}}],
  ['mid-turn precheck',{promptErrorSource:'precheck',contextOverflowError:{source:'promptError'},preflightRecovery:{source:'mid-turn',route:'compact_only'}}],
  ['unknown precheck source',{promptErrorSource:'precheck',contextOverflowError:{source:'promptError'},preflightRecovery:{source:'unknown',route:'compact_only'}}],
  ['unknown precheck route',{promptErrorSource:'precheck',contextOverflowError:{source:'promptError'},preflightRecovery:{route:'unknown'}}],
])test(name+' does not replay an old revision',()=>assert.equal(recover(overrides),null));
test('a completed tool keeps the current transcript instead of replaying owner or revision',()=>{
  assert.equal(recover({attempt:{replayMetadata:{replaySafe:true,hadPotentialSideEffects:false},toolMetas:[{toolName:'web_fetch'}],itemLifecycle:{startedCount:0}}}),'native transcript continuation');
});
