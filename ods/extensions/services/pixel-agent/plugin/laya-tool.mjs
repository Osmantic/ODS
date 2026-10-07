import {LayaProtocolError} from './laya-protocol.mjs';
import {LayaServiceError} from './laya-client.mjs';

// Stay below the normal 16k-character tool-result projection. Never deliver
// half a JSON batch whose omitted decisions might look like completed work.
export const LAYA_RESULT_CHARS = 12_000;

export const LAYA_GUIDE = `Laya in Portal
Optional local text classification, ordered scores and yes/no estimates. It does not replace conversation, coding, browsing, tools or build verification. Ordinary chats and edits need no Laya call. Never send secrets or unrelated history; decisions grant no authority.
Deferred Laya tools use tool_call with the exact tool name as id and normal input as args; never substitute another visible tool.
For a dataset use pixel_ods_laya_batch. Read a header/sample only if columns are unknown. Example:
{"source":{"path":"Playground/tickets.csv","textColumn":"text","idColumn":"id"},"questions":[{"id":"category","type":"choice","instructions":"Choose the main topic.","choices":[{"id":"billing","description":"Charges and refunds"},{"id":"other","description":"Other topics"}]}],"outputDirectory":"Playground/results"}
Paths must be workspace-relative. Up to 128 rows/64 KiB, CSV/TSV/JSON/JSONL, 1-4 questions. Portal reads/batches the rows and creates a new report.csv plus decisions.json with verified bytes. No row transcription, overwritten files or extra owner confirmation for an already requested report.
Optional checkpoint, language and contextTokens are top-level fields beside source, questions and outputDirectory, never inside a question or an items wrapper. Preserve requested values when repairing a malformed call; move them to the documented location instead of dropping them and changing the requested behavior.
For texts already in context use pixel_ods_laya. Example:
{"items":[{"id":"a","text":"Refund requested."}],"questions":[{"id":"team","type":"choice","instructions":"Choose the responsible team.","choices":[{"id":"billing","description":"Payments"},{"id":"other","description":"Other topics"}]}]}
Inline limits: 32 items, 8 shared questions. Choice uses 2-20 IDs/descriptions; score uses 2-20 ordered levels, lowest first; noul returns P(yes). Include other/unknown when needed. Ask self-contained questions grounded in the source. Laya itself cannot read paths, URLs or images.
Review returned candidates against their original text before delivering a classification; correct unsupported CSV labels and verify edits. This is your review, not an owner approval. Original confidence/probabilities remain in decisions.json and do not apply to corrections. Neither confidence statistic guarantees accuracy. Do not invent universal thresholds or treat byte readback as semantic verification; unreviewed rows remain estimates.
Truncated context is rejected: split preserving evidence or raise contextTokens up to 8192. Failed/disabled/busy service must not stop the task: continue with ordinary tools where possible and disclose a material failure. Do not repeat unchanged failed calls. Keep source/partial outputs; cancellation does not prove inference stopped. Continue the requested work and answer normally. Do not end with a raw classifier response.`;

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
