import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

for (const wrapped of [false, true]) {
  for (const exhausted of ['search', 'fetch']) {
    test(`${exhausted} exhaustion preserves the other bucket across compaction (${wrapped ? 'Tool Search' : 'direct'})`, () => {
      const aborts = [];
      const guard = createToolLoopGuard({limits:{search:2,fetch:3,total:5},abortRun:id=>{aborts.push(id);return true;}});
      const context = {agentId:'pixel',runId:'run-1',sessionId:'session-1'};
      const observe = () => guard.observeRun(context,'pixel',{prompt:'Research public sources and save the findings in report.md.'});
      const round = () => guard.observeModelCall({runId:'run-1'},context);
      const invoke = (name,args={}) => {
        if (wrapped) {
          const outer=guard.beforeToolCall({toolName:'tool_call',runId:'run-1',params:{id:`openclaw:core:${name}`,args}},context,'pixel');
          if (outer?.block) return outer;
        }
        return guard.beforeToolCall({toolName:name,runId:'run-1',params:args},context,'pixel');
      };
      const search = i => invoke('web_search',{query:`source ${i}`});
      const fetch = i => invoke('web_fetch',{url:`https://docs.example.org/source-${i}`});
      observe();round();
      const limited = exhausted==='search' ? search : fetch;
      const other = exhausted==='search' ? fetch : search;
      const limit = exhausted==='search' ? 2 : 3;
      for(let i=0;i<limit;i++) assert.notEqual(limited(i)?.block,true);
      assert.equal(limited(limit)?.block,true);
      observe();round();
      assert.notEqual(other(20)?.block,true,'compaction must not revoke the independent allowance');
      assert.notEqual(invoke('write',{path:'report.md',content:'Collected evidence with source URLs.'})?.block,true);
      assert.deepEqual(aborts,[]);
      // Independent progress does not replenish the originally exhausted kind.
      assert.equal(limited(limit+1)?.block,true);
      observe();
      // Same model batch may contain several already-emitted denied calls.
      assert.equal(limited(limit+2)?.block,true);
      assert.deepEqual(aborts,[]);
      const otherLimit = exhausted==='search' ? 3 : 2;
      for(let i=1;i<otherLimit;i++) assert.notEqual(other(20+i)?.block,true);
      // The combined total remains fixed even after switching strategies.
      assert.match(other(30)?.blockReason,/web-research budget is exhausted/);
      assert.notEqual(invoke('read',{path:'report.md'})?.block,true);
      assert.deepEqual(aborts,[]);
      round();
      assert.equal(limited(40)?.block,true);
      round();
      assert.match(limited(41)?.blockReason,/stopped this response/);
      assert.deepEqual(aborts,['session-1']);
    });
  }
}
