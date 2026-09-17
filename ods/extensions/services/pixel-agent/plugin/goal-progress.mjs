// Public work plans, not private chain-of-thought. Execution remains subject to
// the existing tool guard, cancellation, verification and resource budgets.
export const GOAL_CONTRACT = 'GOAL MODE: do the requested work, check it, and deliver the actual result. When no saved plan exists, publish a short plan through tool_call with id="pixel_ods_goal" and args={"status":"active","summary":"Working","steps":[{"id":"work","title":"Fulfill the request","status":"running"},{"id":"verify","title":"Check the result","status":"pending"}]}. Translate new titles to the owner language. When a saved plan exists, continue it instead of publishing a new active plan. Preserve its exact IDs and titles. After doing and checking the work, call pixel_ods_goal with args={"status":"completed","summary":"Work checked and completed","steps":[{"id":"work","title":"Fulfill the request","status":"completed"},{"id":"verify","title":"Check the result","status":"completed"}]}, using your original step IDs and titles. There is no completed boolean or questions field. Then give the final answer. If unable to continue, report status="blocked" and a concrete summary. Do not invent progress, evidence, or private reasoning.';
const text = (value, max) => typeof value === 'string' && value.trim().length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
const exact=(value,keys)=>value && typeof value==='object' && !Array.isArray(value) && Object.keys(value).sort().join(',')===keys.split(',').sort().join(',');
export function parseGoalProgress(value) {
  if(!exact(value,'status,summary,steps') || !['active','completed','blocked','waiting'].includes(value.status)
    || !text(value.summary,300) || !Array.isArray(value.steps) || value.steps.length>8) return null;
  const ids=new Set();
  for(const step of value.steps) {
    if(!exact(step,'id,title,status') || typeof step.id !== 'string' || !/^[a-z][a-z0-9_]{0,31}$/.test(step.id) || ids.has(step.id)
      || !text(step.title,160) || !['pending','running','completed','blocked'].includes(step.status))return null;
    ids.add(step.id);
  }
  if(value.status==='completed' && (!value.steps.length || value.steps.some(step=>step.status!=='completed')))return null;
  return structuredClone(value);
}
export function createGoalProgress({agentId='pixel',maximumRuns=64}={}) {
  const runs=new Map();
  const idFor=(event,context)=>context?.agentId===agentId ? context.runId ?? event?.runId : null;
  return {
    begin(event,context) {
      const id=idFor(event,context);
      if(!id || runs.has(id))return;
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
      runs.set(id,{goal:{status:'active',summary:'Preparing the plan',steps:[]},finished:false});
    },
    active(id) { return runs.has(id); },
    projection(id) { const value=runs.get(id)?.goal; return value ? structuredClone(value) : null; },
    before(event,context) {
      const name=context?.toolName ?? event?.toolName;
      const selected=name==='tool_call' ? String(event?.params?.id ?? '').split(':').at(-1) : name;
      if(selected!=='pixel_ods_goal')return;
      const run=runs.get(idFor(event,context));
      if(!run || run.finished)return {block:true,blockReason:'The owner has not enabled /goal for this turn.'};
      const goal=parseGoalProgress(name==='tool_call' ? event?.params?.args : event?.params);
      const supplied=name==='tool_call' ? event?.params?.args : event?.params;
      if(!goal && supplied?.completed===true)return {block:true,blockReason:'Replace the invalid field "completed":true with "status":"completed". Keep summary and the original steps, with every step status="completed". The exact top-level keys are status, summary, steps. Then deliver the actual result.'};
      if(!goal || !goal.steps.length || goal.status==='waiting')return {block:true,blockReason:'Wrong arguments: pixel_ods_goal takes a plan, not questions. Call tool_call with id="pixel_ods_goal" and args={"status":"active","summary":"Working","steps":[{"id":"work","title":"Fulfill the request","status":"running"}]}. Adapt the title to the request. Do not repeat the rejected arguments.'};
      const old=run.goal.steps;
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
      if(!run)return;
      run.finished=true;
      if(event?.success===false || event?.error)run.goal={...run.goal,status:'blocked',summary:'This run ended before the goal was completed. Review the result before continuing.'};
    },
  };
}
export function createGoalProgressTool() {
  return {name:'pixel_ods_goal',description:'Update the public /goal plan. Report observed progress, not private reasoning. Keep step IDs and titles unchanged after the initial plan. Completed requires every step completed. This only updates the plan; it does not execute work or prove success.',
    parameters:{type:'object',additionalProperties:false,required:['status','summary','steps'],properties:{
      status:{type:'string',enum:['active','completed','blocked']},summary:{type:'string',minLength:1,maxLength:300},
      steps:{type:'array',minItems:1,maxItems:8,items:{type:'object',additionalProperties:false,required:['id','title','status'],properties:{id:{type:'string',pattern:'^[a-z][a-z0-9_]{0,31}$'},title:{type:'string',minLength:1,maxLength:160},status:{type:'string',enum:['pending','running','completed','blocked']}}}}}},
    async execute(_id,params) {
      const goal=parseGoalProgress(params);
      if(!goal || !goal.steps.length || goal.status==='waiting')return {isError:true,content:[{type:'text',text:'Provide status active/completed/blocked, a brief summary, and 1–8 steps with id, title, status. Completed requires every step completed.'}]};
      return {content:[{type:'text',text:goal.status==='active'?'Plan recorded. Continue the next incomplete step now.':'Progress reported. Deliver the result and any limitations.'}],details:{goal}};
    }};
}
