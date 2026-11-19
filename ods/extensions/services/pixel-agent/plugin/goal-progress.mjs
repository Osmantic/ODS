// Public work plans, not private chain-of-thought. Execution remains subject to
// the existing tool guard, cancellation, verification and resource budgets.
const ID_RULE = 'Step IDs start with a lowercase letter, then use only lowercase letters, digits or underscores, at most 32 characters, with no hyphens; for example "check_result".';
export const GOAL_CONTRACT = 'GOAL MODE: do the requested work, check it, and deliver the actual result. When no saved plan exists, publish a short plan through tool_call with id="pixel_ods_goal" and args={"status":"active","summary":"Working","steps":[{"id":"work","title":"Fulfill the request","status":"running"},{"id":"verify","title":"Check the result","status":"pending"}]}. Write new titles in the reply language. ' + ID_RULE + ' When a saved plan exists, continue it instead of publishing a new active plan. Preserve its exact IDs and titles. After doing and checking the work, call pixel_ods_goal with args={"status":"completed","summary":"Work checked and completed","steps":[{"id":"work","title":"Fulfill the request","status":"completed"},{"id":"verify","title":"Check the result","status":"completed"}]}, using your original step IDs and titles. There is no completed boolean or questions field. Then give the final answer. If unable to continue, report status="blocked" and a concrete summary. Do not invent progress, evidence, or private reasoning.';
const text = (value, max) => typeof value === 'string' && value.trim().length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
const exact=(value,keys)=>value && typeof value==='object' && !Array.isArray(value) && Object.keys(value).sort().join(',')===keys.split(',').sort().join(',');
const STATUS_RULE='status must be "active", "completed" or "blocked"', STEPS_RULE='steps must be a list of 1 to 8 steps';
// The one plan rule walk: null when valid, otherwise the first broken field and
// its rule. It names positions and rules, never the rejected value.
function planProblem(value) {
  if(!exact(value,'status,summary,steps'))return 'args must be an object with exactly the keys status, summary and steps';
  if(!['active','completed','blocked','waiting'].includes(value.status))return STATUS_RULE;
  if(!text(value.summary,300))return 'summary must be non-empty text of at most 300 characters without control characters';
  if(!Array.isArray(value.steps) || value.steps.length>8)return STEPS_RULE;
  const ids=new Set();
  let index=0;
  for(const step of value.steps) {
    const at=`steps[${index++}]`;
    if(!exact(step,'id,title,status'))return `${at} must have exactly the keys id, title and status`;
    if(typeof step.id !== 'string' || !/^[a-z][a-z0-9_]{0,31}$/.test(step.id))return `${at}.id breaks the step ID rule. ${ID_RULE}`;
    if(ids.has(step.id))return `${at}.id repeats an earlier step ID; each step ID must be unique`;
    if(!text(step.title,160))return `${at}.title must be non-empty text of at most 160 characters without control characters`;
    if(!['pending','running','completed','blocked'].includes(step.status))return `${at}.status must be "pending", "running", "completed" or "blocked"`;
    ids.add(step.id);
  }
  if(value.status==='completed' && (!value.steps.length || value.steps.some(step=>step.status!=='completed')))return 'status "completed" requires every step status "completed" and at least one step';
  return null;
}
// Internal projections may be waiting or have no steps yet; the tool may not.
const runtimeProblem=(value,problem=planProblem(value))=>problem ?? (!value.steps.length ? STEPS_RULE : value.status==='waiting' ? STATUS_RULE : null);
export function parseGoalProgress(value) {
  return planProblem(value) ? null : structuredClone(value);
}
export function createGoalProgress({agentId='pixel',maximumRuns=64}={}) {
  const runs=new Map();
  const idFor=(event,context)=>context?.agentId===agentId ? context.runId ?? event?.runId : null;
  return {
    begin(event,context) {
      const id=idFor(event,context);
      if(!id)return;
      const existing=runs.get(id);
      if(existing) {
        const fresh=event && typeof event==='object' && !existing.promptBuildEvents.has(event);
        if(event && typeof event==='object')existing.promptBuildEvents.add(event);
        if((context?.sessionId && existing.sessionId && context.sessionId!==existing.sessionId)
          || (context?.sessionKey && existing.sessionKey && context.sessionKey!==existing.sessionKey))existing.sessionConflict=true;
        // agent_end closes an embedded attempt, which the SDK may retry after
        // compaction. Restore the last public plan only for its owned retry.
        if(fresh && typeof event.prompt==='string' && event.prompt.trim()
          && existing.finished && existing.resumeGoal && !existing.sessionConflict
          && typeof existing.sessionId==='string' && existing.sessionId
          && existing.sessionId===context?.sessionId
          && typeof existing.sessionKey==='string' && existing.sessionKey
          && existing.sessionKey===context?.sessionKey) {
          existing.goal=existing.resumeGoal;
          existing.resumeGoal=null;
          existing.finished=false;
        }
        return;
      }
      // Only the current owner input can enable this mode, never tool text or
      // an old /goal request in conversation history.
      let prompt=typeof event?.prompt==='string' ? event.prompt.replace(/\r\n/g,'\n') : '';
      const marker='[Current message - respond to this]\nUser:';
      if(prompt.startsWith('[Chat messages since your last reply - for context]\n') && prompt.split(marker).length===2)prompt=prompt.split(marker)[1].trimStart();
      if(!/^\s*\/goal\s+\S/i.test(prompt))return;
      while(runs.size>=maximumRuns) {
        const old=[...runs].find(([,run])=>run.finished);
        if(!old)return;
        runs.delete(old[0]);
      }
      const promptBuildEvents=new WeakSet();
      if(event && typeof event==='object')promptBuildEvents.add(event);
      runs.set(id,{goal:{status:'active',summary:'Preparing the plan',steps:[]},finished:false,
        sessionId:context?.sessionId,sessionKey:context?.sessionKey,promptBuildEvents});
    },
    active(id) { return runs.has(id); },
    projection(id) { const value=runs.get(id)?.goal; return value ? structuredClone(value) : null; },
    before(event,context) {
      const name=context?.toolName ?? event?.toolName;
      const selected=name==='tool_call' ? String(event?.params?.id ?? '').split(':').at(-1) : name;
      if(selected!=='pixel_ods_goal')return;
      const run=runs.get(idFor(event,context));
      if(!run || run.finished)return {block:true,blockReason:'The owner has not enabled /goal for this turn.'};
      const supplied=name==='tool_call' ? event?.params?.args : event?.params;
      const invalid=planProblem(supplied);
      if(invalid && supplied?.completed===true)return {block:true,blockReason:'Replace the invalid field "completed":true with "status":"completed". Keep summary and the original steps, with every step status="completed". The exact top-level keys are status, summary, steps. Then deliver the actual result.'};
      const problem=runtimeProblem(supplied,invalid);
      if(problem)return {block:true,blockReason:`Wrong arguments for pixel_ods_goal: ${problem}. Correct this and call pixel_ods_goal again; do not repeat the rejected arguments. If a saved plan exists, keep its exact step IDs and titles.`};
      const goal=structuredClone(supplied), old=run.goal.steps;
      if(old.length && (goal.steps.length!==old.length || old.some((step,i)=>step.id!==goal.steps[i].id || step.title!==goal.steps[i].title)))return {block:true,blockReason:'Update the original plan. Keep all step IDs and titles; do not drop incomplete steps.'};
    },
    update(event,context) {
      const run=runs.get(idFor(event,context));
      // Inner Tool Search executions emit the same direct-tool hook. Accept
      // only that receipt, avoiding duplicate wrapper delivery and spoofing.
      if(!run || run.finished || (context?.toolName ?? event?.toolName)!=='pixel_ods_goal' || event?.error || event?.result?.isError)return;
      const goal=parseGoalProgress(event?.result?.details?.goal);
      if(!goal)return;
      const old=run.goal.steps;
      if(old.length && (goal.steps.length!==old.length || old.some((step,i)=>step.id!==goal.steps[i].id || step.title!==goal.steps[i].title)))return;
      run.goal=goal;
    },
    finalize(event,context,{allowed=true,waiting=false,guardDecision}={}) {
      const run=runs.get(idFor(event,context));
      if(!run || run.finished)return guardDecision;
      if(guardDecision?.action==='revise')return guardDecision;
      if(waiting) {run.goal={...run.goal,status:'waiting',summary:'Waiting for your answer or approval'};return guardDecision;}
      if(!allowed) {run.goal={...run.goal,status:'blocked',summary:'Execution stopped; review the response before continuing.'};return guardDecision;}
      if(['completed','blocked'].includes(run.goal.status))return guardDecision;
      // Continuation belongs to the durable dashboard controller. Never ask
      // the native harness to replay a turn that may have produced effects.
      return guardDecision;
    },
    finish(event,context) {
      const run=runs.get(idFor(event,context));
      if(!run || run.finished)return;
      run.finished=true;
      if(event?.success===false || event?.error) {
        run.resumeGoal=structuredClone(run.goal);
        run.goal={...run.goal,status:'blocked',summary:'This run ended before the goal was completed. Review the result before continuing.'};
      }
    },
  };
}
export function createGoalProgressTool() {
  return {name:'pixel_ods_goal',description:'Update the public /goal plan. Report observed progress, not private reasoning. Keep step IDs and titles unchanged after the initial plan. ' + ID_RULE + ' Completed requires every step completed. This only updates the plan; it does not execute work or prove success.',
    parameters:{type:'object',additionalProperties:false,required:['status','summary','steps'],properties:{
      status:{type:'string',enum:['active','completed','blocked']},summary:{type:'string',minLength:1,maxLength:300},
      steps:{type:'array',minItems:1,maxItems:8,items:{type:'object',additionalProperties:false,required:['id','title','status'],properties:{id:{type:'string',pattern:'^[a-z][a-z0-9_]{0,31}$'},title:{type:'string',minLength:1,maxLength:160},status:{type:'string',enum:['pending','running','completed','blocked']}}}}}},
    async execute(_id,params) {
      const problem=runtimeProblem(params);
      if(problem)return {isError:true,content:[{type:'text',text:`Wrong arguments: ${problem}. Provide status active/completed/blocked, a brief summary, and 1–8 steps with id, title, status. Completed requires every step completed.`}]};
      const goal=structuredClone(params);
      return {content:[{type:'text',text:goal.status==='active'?'Plan recorded. Continue the next incomplete step now.':'Progress reported. Deliver the result and any limitations.'}],details:{goal}};
    }};
}
