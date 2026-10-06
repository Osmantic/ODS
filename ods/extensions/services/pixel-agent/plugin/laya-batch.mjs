import {randomUUID} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';
import {LAYA_TOOL_SCHEMA} from './laya-tool.mjs';
import {prepareLayaRequest, LayaProtocolError} from './laya-protocol.mjs';
import {LayaServiceError} from './laya-client.mjs';

export const LAYA_BATCH_TOOL = 'pixel_ods_laya_batch';
export class LayaBatchError extends Error {
  constructor(code) { super(code); this.name = 'LayaBatchError'; this.code = code; }
}
const fail = code => { throw new LayaBatchError(code); };
const safePath = value => typeof value === 'string' && value.length <= 512 &&
  value.split('/').length <= 16 && value.split('/').every(part =>
    /^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$/.test(part) && !/[. ]$/.test(part));
const record = value => value && typeof value === 'object' && !Array.isArray(value);
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export function normalizeLayaBatch(value) {
  const keys = ['source', 'questions', 'outputDirectory', 'language', 'checkpoint', 'contextTokens'];
  if (!record(value) || Object.keys(value).some(key => !keys.includes(key)) ||
      !record(value.source) || Object.keys(value.source).some(key => !['path','textColumn','idColumn'].includes(key)) ||
      !safePath(value.source.path) || !/\.(csv|tsv|json|jsonl)$/i.test(value.source.path) ||
      !safePath(value.outputDirectory) || !safePath(value.outputDirectory + '/laya-' + 'a'.repeat(32) + '/decisions.json') ||
      ['textColumn','idColumn'].some(key => key === 'textColumn' || Object.hasOwn(value.source,key)
        ? typeof value.source[key] !== 'string' || !value.source[key].trim() || value.source[key].length > 128 || /[\x00-\x1f]/.test(value.source[key]) : false) ||
      !Array.isArray(value.questions) || value.questions.length > 4) fail('invalid-batch-request');
  const {source, outputDirectory, ...decisions} = value;
  prepareLayaRequest({...decisions, items:[{id:'check',text:'schema validation'}]});
  const columns = ['id', ...value.questions.map(q => q.id)];
  if (new Set(columns).size !== columns.length) fail('duplicate-report-column');
  return structuredClone(value);
}

export const LAYA_BATCH_SCHEMA = {
  type:'object', additionalProperties:false, required:['source','questions','outputDirectory'],
  properties:{
    source:{type:'object', additionalProperties:false, required:['path','textColumn'], properties:{
      path:{type:'string',description:'Existing workspace-relative CSV, TSV, JSON array or JSONL. UTF-8; <=64 KiB, 1–128 rows.'},
      textColumn:{type:'string',description:'Exact column/key containing each text to classify.'},
      idColumn:{type:'string',description:'Optional unique string/integer row ID column; otherwise uses 1-based row numbers.'},
    }},
    questions:{...LAYA_TOOL_SCHEMA.properties.questions,description:'1–4 shared decision questions. The Portal applies them to every source row.'},
    outputDirectory:{type:'string',description:'Workspace-relative parent for a new laya-<id>/report.csv and decisions.json. Existing files are never replaced.'},
    language:LAYA_TOOL_SCHEMA.properties.language,
    checkpoint:LAYA_TOOL_SCHEMA.properties.checkpoint,
    contextTokens:LAYA_TOOL_SCHEMA.properties.contextTokens,
  },
};

// Factory metadata alone cannot authorize workspace I/O. Bind one admitted
// invocation, including deferred Tool Search's parent/child call convention.
export function createLayaBatchAdmission() {
  const pending = new Map();
  return {
    before(event, context, decision) {
      const id = context?.toolCallId ?? event?.toolCallId;
      if (id) pending.delete(id);
      if (decision?.block || context?.agentId !== 'pixel' || !context.runId || !context.sessionId || !context.sessionKey || !id) return;
      const params = decision?.params ?? event?.params;
      const direct = event.toolName === LAYA_BATCH_TOOL;
      const deferred = event.toolName === 'tool_call' && [LAYA_BATCH_TOOL,'openclaw:pixel-ods:'+LAYA_BATCH_TOOL].includes(params?.id);
      if (!direct && !deferred) return;
      try {
        const request = normalizeLayaBatch(deferred ? params.args : params);
        while (pending.size >= 128) pending.delete(pending.keys().next().value);
        pending.set(id,{scope:{...context},request,deferred});
      } catch (error) {
        if (!(error instanceof LayaBatchError || error instanceof LayaProtocolError)) throw error;
      }
    },
    take(id, request, factory) {
      let entry = pending.get(id);
      if (!entry) {
        const matches = [...pending].filter(([parentId,item]) => {
          const parent = parentId.trim().replace(/[^A-Za-z0-9_.:-]+/g,'_').slice(0,120) || 'call';
          const prefix = `tool_search_code:${parent}:${LAYA_BATCH_TOOL}:`;
          return item.deferred && id.startsWith(prefix) && /^[1-9][0-9]*$/.test(id.slice(prefix.length));
        });
        if (matches.length === 1) entry = matches[0][1];
      }
      if (!entry || entry.used || !isDeepStrictEqual(request,entry.request) ||
          ['agentId','sessionId','sessionKey'].some(key => entry.scope[key] !== factory?.[key])) fail('batch-call-unbound');
      entry.used = true;
      return {...entry.scope};
    },
    after(event, context) { pending.delete(context?.toolCallId ?? event?.toolCallId); },
  };
}

function sourceReceipt(value, request) {
  const source = value?.source;
  if (value?.status !== 'succeeded' || !record(source) || source.path !== request.source.path ||
      !Number.isSafeInteger(source.bytes) || source.bytes < 1 || source.bytes > 65536 || !sha(source.sha256) ||
      !Array.isArray(source.items) || source.items.length < 1 || source.items.length > 128 ||
      source.items.some((item,index) => item?.id !== 'r'+(index+1) || typeof item.sourceId !== 'string' ||
        !item.sourceId || item.sourceId.length > 128 || /[\x00-\x1f]/.test(item.sourceId) || typeof item.text !== 'string') ||
      new Set(source.items.map(item=>item.sourceId)).size !== source.items.length) fail('unverified-dataset-read');
  return source;
}

function chunksFor(source, controls) {
  const batches = [];
  let items = [];
  for (const {id,text} of source.items) {
    if (items.length === 32) { batches.push({...controls,items}); items = []; }
    const candidate = [...items,{id,text}];
    try { prepareLayaRequest({...controls,items:candidate}); }
    catch (error) {
      if (!(error instanceof LayaProtocolError) || error.code !== 'request_too_large' || !items.length) throw error;
      batches.push({...controls,items}); items = [];
    }
    items.push({id,text});
  }
  if (items.length) batches.push({...controls,items});
  for (const batch of batches) prepareLayaRequest(batch);
  return batches;
}

function reportReceipt(value, prefix) {
  const expected = [prefix+'/report.csv',prefix+'/decisions.json'];
  if (value?.status !== 'succeeded' || value.readbackVerified !== true || value.overwritten !== false ||
      !Array.isArray(value.outputs) || value.outputs.length !== 2 || value.outputs.some((item,index) =>
        item?.path !== expected[index] || !sha(item.sha256) || !Number.isSafeInteger(item.bytes) || item.bytes < 1 || item.bytes > 131072))
    fail('unverified-batch-report');
  return value.outputs;
}

function choiceCounts(items, questions) {
  return Object.fromEntries(questions.filter(q=>q.type==='choice').map(question => {
    const counts = Object.fromEntries(question.choices.map(choice=>[choice.id,0]));
    for (const item of items) counts[item.answers[question.id].choice]++;
    return [question.id,counts];
  }));
}

// Prioritize inspection without inventing a universal confidence threshold.
// A place outside this shortlist is never a claim that a label is correct.
function reviewCandidates(items, source) {
  return items.flatMap((item,index)=>Object.entries(item.answers).filter(([,answer])=>answer.type==='choice').map(([question,answer])=>{
    const alternatives=Object.entries(answer.probabilities).sort((a,b)=>b[1]-a[1]);
    return {id:item.sourceId,question,choice:answer.choice,checkpoint:item.checkpoint,
      margin:alternatives[0][1]-alternatives[1][1],alternatives:alternatives.slice(0,2),
      text:source.items[index].text.slice(0,400),textComplete:source.items[index].text.length<=400};
  })).sort((a,b)=>a.margin-b.margin).slice(0,8);
}

function summaryText(value) {
  // Keep the complete JSON below the smallest normal Portal projection budget.
  // Only inspection examples/derived counts may be omitted; report files retain
  // every row and probability, and omissions remain explicit to the model.
  value.reviewCandidatesOmitted = 0;
  while (JSON.stringify(value).length > 7000 && value.reviewCandidates.length > 1) {
    value.reviewCandidates.pop(); value.reviewCandidatesOmitted++;
  }
  if (JSON.stringify(value).length > 7000) {delete value.counts;value.countsOmitted = true;}
  if (JSON.stringify(value).length > 7000) fail('report-summary-too-large');
  return JSON.stringify(value);
}

export function createLayaBatchTool(factory, {resolveClient, admission, execution, invalidatePreview, now = () => performance.now()} = {}) {
  return {name:LAYA_BATCH_TOOL, label:'Classify dataset with Laya', parameters:LAYA_BATCH_SCHEMA,
    description:'Classify or score an existing workspace CSV/TSV/JSON/JSONL dataset with local Laya, in one call. Supply source path, textColumn, optional idColumn, shared questions and outputDirectory. Portal reads rows, batches inference and saves a new CSV and full JSON evidence with verified bytes. Prefer this to copying file rows into pixel_ods_laya and manually rewriting results. At most 128 rows/64 KiB and four questions; no overwritten files. Confidence is an estimate, not accuracy. No URLs, arbitrary host paths or commands. If unavailable, continue the task using ordinary tools.',
    async execute(id, supplied, signal) {
      let prefix, inferenceBatches = 0;
      const started = now();
      try {
        const request = normalizeLayaBatch(supplied);
        const client = resolveClient();
        if (!client) fail('laya-disabled');
        const active = admission.take(id,request,factory);
        const scope = execution.scopeForContext(active,factory);
        const current = () => {
          if (signal?.aborted) fail('batch-cancelled');
          if (resolveClient() !== client) fail('laya-activation-changed');
          if (now()-started > 90000) fail('batch-deadline');
        };
        current();
        const source = sourceReceipt(await execution.runHelper(scope,{operation:'read',source:request.source},signal),request);
        const {source:unused,outputDirectory, ...controls} = request;
        const batches = chunksFor(source,controls), items = [];
        let inferenceMs = 0;
        for (const batch of batches) {
          current();
          const before = now();
          const result = await client.decide(batch,signal);
          inferenceMs += now()-before;
          inferenceBatches++;
          for (const item of result.items) items.push({...item,sourceId:source.items[items.length].sourceId});
        }
        current();
        const report = {schemaVersion:1,kind:'laya-dataset-decisions',advisory:true,completeContext:true,
          source:{path:source.path,bytes:source.bytes,sha256:source.sha256},questions:request.questions,items};
        const generation = 'laya-'+randomUUID().replaceAll('-','');
        if (invalidatePreview && invalidatePreview(scope) !== true) fail('batch-run-no-longer-active');
        prefix = outputDirectory+'/'+generation;
        const outputs = reportReceipt(await execution.runHelper(scope,{operation:'write',source:request.source,
          outputDirectory,generation,report},signal),prefix);
        return {content:[{type:'text',text:summaryText({status:'predictions-saved',decisionReview:'pending',
          nextAction:'Before delivering the classification, compare the candidate labels below with their original text and your criteria. Fix unsupported labels in the CSV, then verify the edit. Merely reading the CSV or checking its hash is not label review. The file is a draft prediction until you perform that review. This is your review, not another owner approval step.',
          reviewCandidates:reviewCandidates(items,source),rows:items.length,source:report.source,
          outputs,readbackVerified:true,accuracyVerified:false,counts:choiceCounts(items,request.questions),
          inferenceBatches,inferenceMs:Math.round(inferenceMs),totalMs:Math.round(now()-started),
          note:'CSV contains editable estimates. This shortlist does not certify other rows; inspect more source when the task requires it. Original Laya probabilities and confidence are preserved separately in decisions.json and do not apply to model corrections. Readback verifies bytes, not accuracy; never claim every label is verified from this receipt.'})}],
          details:{kind:'laya-batch-report',status:'completed',itemCount:items.length,outputs,readbackVerified:true,accuracyVerified:false}};
      } catch (error) {
        if (!(error instanceof LayaBatchError || error instanceof LayaProtocolError || error instanceof LayaServiceError)) throw error;
        const guidance = error.code === 'invalid-batch-request'
          ? 'Expected source:{path,textColumn,idColumn?}, questions:[{id,type:"choice",instructions,choices:[{id,description},...]}], outputDirectory. Both paths must be workspace-relative, not absolute. Do not supply items, columns, options or outputPath. Read pixel_ods_skill with {"topic":"laya"} for a complete dataset example before correcting the call. '
          : '';
        return {isError:true,details:{kind:'laya-batch-report',status:error.code,inferenceBatches,outputPrefix:prefix ?? null},
          content:[{type:'text',text:`Laya dataset processing did not produce a confirmed complete report (${error.code}). ${prefix ? `Inspect ${prefix} before another attempt; partial files may remain. ` : 'No report was written. '}${guidance}Continue the task with other available tools when possible. If the owner requested Laya, explain that this consultation failed and distinguish your own conclusions from Laya results. Do not replay an unchanged failed request or treat partial decisions as complete.`}]};
      }
    },
  };
}
