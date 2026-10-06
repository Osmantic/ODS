import {LayaProtocolError} from './laya-protocol.mjs';
import {LayaServiceError} from './laya-client.mjs';

// Stay below the normal 16k-character tool-result projection. Never deliver
// half a JSON batch whose omitted decisions might look like completed work.
export const LAYA_RESULT_CHARS = 12_000;

export const LAYA_GUIDE = `Laya in Portal
Use pixel_ods_laya for bounded choices, ordinal scores or yes/no probabilities when they help the owner's task. You still handle conversation, research, file edits and verification.
Read the relevant source first. Send only necessary text, never passwords, API keys or unrelated history. Each item has a unique ID and text. Ask self-contained questions grounded in that text: choice has 2–20 option IDs and descriptions; score has ordered levels, lowest first; noul returns P(yes). Include unknown/other when alternatives are incomplete. Narrow larger option sets using evidence or meaningful groups, never arbitrary omissions. Batch independent items sharing questions.
Batch example: {"items":[{"id":"a","text":"Refund requested."},{"id":"b","text":"Login crashes."}],"questions":[{"id":"team","type":"choice","instructions":"Which team handles this ticket?","choices":[{"id":"billing","description":"Charges and refunds"},{"id":"technical","description":"Software bugs"},{"id":"other","description":"Anything else"}]}]}. This asks one shared question about each item; do not duplicate it per item. Then examine ambiguity, save the requested report and verify the saved file.
For development, classification can organize evidence; build/run/browser tools establish whether code and UI work. Laya cannot inspect an unseen image, read a path or URL, browse, generate a website, execute commands or prove a claim by scoring it.
Results are estimates. Confidence and answerConfidence are different statistics, neither guaranteed accuracy. Do not invent universal thresholds, treat decisions as authorization or claim external verification. Preserve uncertainty.
If context is truncated or invalid, no decision is usable: increase contextTokens up to 8192 or split the source while preserving relevant evidence. Larger budgets cost more time and memory. Do not present partial context as a full-file review. Failed, disabled or busy Laya does not end the Portal task: continue with other capabilities where possible, disclosing a material limitation if the owner required Laya. Do not repeat unchanged failed inputs. A cancelled or timed-out observation does not prove inference stopped on the server.
Continue the requested task and answer normally. Do not end with a raw classifier response. Simple conversation and direct edits need no Laya call. Do not add Laya to a created app unless requested.`;

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
  return {isError: true, content: [{type: 'text', text: message}],
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
        return {content: [{type: 'text', text}],
          details: {kind: 'laya-decisions', status: 'completed', itemCount: result.items.length,
            executionAuthorized: false}};
      } catch (error) {
        if (!(error instanceof LayaProtocolError) && !(error instanceof LayaServiceError)) throw error;
        return unavailable(error.code, `${error.message} No Laya decision was accepted. Continue the owner's task using other available capabilities where possible.`, error.submitted === true);
      }
    },
  };
}
