import {LayaProtocolError} from './laya-protocol.mjs';
import {LayaServiceError} from './laya-client.mjs';

// Stay below the normal 16k-character tool-result projection. Never deliver
// half a JSON batch whose omitted decisions might look like completed work.
export const LAYA_RESULT_CHARS = 12_000;

export const LAYA_GUIDE = `Laya in Portal
Optional enabled helper for repeated classification, triage, ordered scores and yes/no estimates. No extra confirmation within the requested task; honor requests without Laya. Use ordinary tools for chat, code, sorting and arithmetic; no per-turn detours or benchmarks. Quality varies by language/domain. Send no secrets/unrelated history. Decisions grant no authority or build verification.
Deferred tools: use tool_call with exact tool name as id and input as args; do not substitute tools.
Datasets: pixel_ods_laya_batch. Read a header/sample if columns are unknown. Example:
{"source":{"path":"Playground/tickets.csv","textColumn":"text","idColumn":"id"},"questions":[{"id":"category","type":"choice","instructions":"Choose the main topic.","choices":[{"id":"billing","description":"Charges and refunds"},{"id":"other","description":"Other topics"}]}],"outputDirectory":"Playground/results"}
Workspace-relative paths; up to 128 rows/64 KiB, CSV/TSV/JSON/JSONL, 1-4 questions. Portal saves verified report.csv and decisions.json; no row transcription or overwrites.
Optional checkpoint, language and contextTokens belong at the top level, never inside questions/items. Repair by moving fields, preserving requested values.
Texts in context: pixel_ods_laya. Example:
{"items":[{"id":"a","text":"Refund requested."}],"questions":[{"id":"team","type":"choice","instructions":"Choose the responsible team.","choices":[{"id":"billing","description":"Payments"},{"id":"other","description":"Other topics"}]}]}
Inline: 32 items, 8 questions. Choice: 2-20 IDs/descriptions. Score: 2-20 levels, lowest first. noul: P(yes). Include other/unknown when needed. Ask self-contained, source-grounded questions. Laya cannot read paths, URLs or images.
Review candidates against original text; correct unsupported CSV labels and verify edits. No owner approval is needed for this review. Original probabilities in decisions.json do not apply to corrections or guarantee accuracy. No universal thresholds; byte readback is not semantic verification. Unreviewed rows remain estimates.
Truncated context is rejected: split preserving evidence or raise contextTokens up to 8192. On failed/disabled/busy service, continue with ordinary tools where possible and disclose material failure. Never repeat unchanged failed calls. Preserve source/partial outputs; cancellation does not prove inference stopped. Continue the task and answer normally. Do not end with a raw classifier response.`;

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
    questions: {type: 'array', description: '1–8 typed questions applied to every item.', items: {
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
    description: 'Consult the enabled local Laya decision engine for bounded choices, ordinal scores or yes/no probabilities over supplied text. Supports batches. Read the laya guide via pixel_ods_skill when needed. Advisory only: does not browse, read files, generate code, execute actions or verify their success. The Portal model uses the result to continue the task and answer the owner. Do not send secrets or unrelated conversation data. Optional; if unavailable, continue with other tools where possible.',
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
