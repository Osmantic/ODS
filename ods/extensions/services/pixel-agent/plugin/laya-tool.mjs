import {LayaProtocolError} from './laya-protocol.mjs';
import {LayaServiceError} from './laya-client.mjs';

// Stay below the normal 16k-character tool-result projection. Never deliver
// half a JSON batch whose omitted decisions might look like completed work.
export const LAYA_RESULT_CHARS = 12_000;

export const LAYA_GUIDE = `Laya in Portal
Enabled-only semantic helper: repeated classification, triage, ordered scores and yes/no estimates. Choose by subtask, including categorizing content for a website; then finish the website with ordinary tools. The owner need not name Laya or approve an in-scope consultation. Honor opt-outs. Skip chat, a few obvious labels, code, visual design, arithmetic, sorting and integrity checks. No per-turn detours/benchmarks. Send no secrets/unrelated history; decisions grant no authority.
Deferred: tool_call with exact tool name as id, input as args.
Datasets: pixel_ods_laya_batch. Read unknown columns first:
{"source":{"path":"Playground/tickets.csv","textColumn":"text","idColumn":"id"},"questions":[{"id":"category","type":"choice","instructions":"Choose the main topic.","choices":[{"id":"billing","description":"Charges and refunds"},{"id":"other","description":"Other topics"}]}],"outputDirectory":"Playground/results"}
Workspace-relative paths; 128 rows/64 KiB, CSV/TSV/JSON/JSONL, 1-4 questions. Honor outputDirectory. Join report.csv to original rows by ID using file commands, not transcription, to build the deliverable.
One question per requested semantic output; no questions about IDs, order, counts, output format, hashes or build success: use ordinary checks. Language refers to source text. Keep context/checkpoint defaults unless needed. Optional checkpoint, language, contextTokens belong at the top level; preserve requested values when repairing calls.
Texts in context: pixel_ods_laya. Example:
{"items":[{"id":"a","text":"Refund requested."}],"questions":[{"id":"team","type":"choice","instructions":"Choose the responsible team.","choices":[{"id":"billing","description":"Payments"},{"id":"other","description":"Other topics"}]}]}
Inline: 32 items, 8 questions; choice: 2-20 options; score: 2-20 levels, lowest first; noul: P(yes). Use other/unknown when needed and self-contained source-grounded criteria. Laya cannot read paths, URLs or images.
Quality varies by domain/language. Review against source; correct unsupported CSV labels and verify edits. Original JSON probabilities do not apply to corrections or prove accuracy. No universal thresholds; saved bytes do not prove semantic review.
On truncation split preserving evidence or raise contextTokens up to 8192. On failed/disabled/busy service, continue normally and disclose material failure; never repeat unchanged failed calls. Preserve partial outputs; cancellation does not prove inference stopped. Do not end with a raw classifier response.`;

// No large string bounds in the public JSON schema: llama.cpp turns them
// into expensive grammar repetitions. The adapter enforces exact limits.
export const LAYA_TOOL_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['items', 'questions'],
  properties: {
    items: {type: 'array', description: '1–32 independent items. Total UTF-8 request <=128 KB.', items: {
      type: 'object', additionalProperties: false, required: ['id', 'text'], properties: {
        id: {type: 'string', description: 'Unique simple ID, at most 64 characters.'},
        text: {type: 'string', description: 'Relevant source text, at most 20,000 characters. Never a path to read.'},
      },
    }},
    questions: {type: 'array', description: '1–8 requested semantic judgments per item. One question per requested label/score; not ID, row order, counts, hashes or execution verification.', items: {
      type: 'object', additionalProperties: false, required: ['id', 'type', 'instructions'], properties: {
        id: {type: 'string', description: 'Unique question ID.'},
        type: {type: 'string', enum: ['choice', 'score', 'noul']},
        instructions: {type: 'string', description: 'Self-contained question and criteria, at most 1,200 characters.'},
        choices: {type: 'array', description: 'Required only for choice: 2–20 distinct options.', items: {
          type: 'object', additionalProperties: false, required: ['id', 'description'], properties: {
            id: {type: 'string'}, description: {type: 'string', description: 'Meaning of the option, at most 400 characters.'},
          },
        }},
        levels: {type: 'array', description: 'Required only for score: 2–20 labels ordered lowest to highest.', items: {type: 'string'}},
      },
    }},
    language: {type: 'string', description: 'Optional source language code, for example pt or en.'},
    checkpoint: {type: 'string', enum: ['auto', 'english', 'multilingual', 'typed-decisions'],
      description: 'Default auto chooses a checkpoint. This is not the Portal chat model.'},
    contextTokens: {type: 'integer', enum: [512, 1024, 2048, 4096, 8192],
      description: 'Optional per-question input budget. Omit for checkpoint default; raise only for relevant longer context. Truncated decisions are rejected.'},
  },
};

function unavailable(status, message, submitted = false) {
  const continuation = "No Laya decision was accepted. Continue the owner's task using other available capabilities where possible. If the owner requested Laya, explain that this consultation failed and distinguish your own conclusions from Laya results. Do not repeat an unchanged failed request.";
  return {isError: true, content: [{type: 'text', text: `${message} ${continuation}`}],
    details: {kind: 'laya-decisions', status, inferenceSubmitted: submitted,
      upstreamCancellationVerified: false, executionAuthorized: false}};
}

export function createLayaTool({client, enabled = () => false, resolveClient} = {}) {
  return {
    name: 'pixel_ods_laya', label: 'Consult Laya',
    description: 'Optional enabled helper for repeated semantic choices, ordinal scores or yes/no estimates over supplied text, including a classification subtask inside a website or report. For existing datasets use pixel_ods_laya_batch. Answer a few obvious items directly unless the owner requests Laya. Not for code, visual design, arithmetic or integrity checks. Read the laya guide via pixel_ods_skill as needed. Advisory only; send no secrets/unrelated history. Continue the larger task with ordinary tools; if unavailable, continue without it.',
    parameters: LAYA_TOOL_SCHEMA,
    async execute(_callId, args, signal) {
      // Recheck at execution: descriptor caches can outlive extension disable.
      const activeClient = resolveClient ? resolveClient() : (enabled() ? client : undefined);
      if (!activeClient) return unavailable('disabled', 'Laya is not enabled for Portal. Continue with existing tools.');
      try {
        const result = await activeClient.decide(args, signal);
        const text = JSON.stringify(result);
        if (text.length > LAYA_RESULT_CHARS) {
          return unavailable('result_too_large', 'Laya completed inference, but the complete result exceeds the Portal tool budget. Split the items or questions into smaller batches; no decisions from this batch were delivered.', true);
        }
        return {content: [{type: 'text', text}, {type: 'text', text: 'These are estimates. Compare answers and alternatives with the supplied source before using them. Preserve ambiguity; correct an unsupported label instead of copying it as fact. Confidence is not guaranteed accuracy.'}],
          details: {kind: 'laya-decisions', status: 'completed', itemCount: result.items.length,
            executionAuthorized: false}};
      } catch (error) {
        if (!(error instanceof LayaProtocolError) && !(error instanceof LayaServiceError)) throw error;
        return unavailable(error.code, error.message, error.submitted === true);
      }
    },
  };
}
