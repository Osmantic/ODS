import {LayaProtocolError} from './laya-protocol.mjs';
import {LayaServiceError} from './laya-client.mjs';

export const LAYA_GUIDE = `Laya in Portal
Use Laya for bounded choices, ordinal scores or yes/no probabilities when they help the owner's task. It is available through pixel_ods_laya while enabled. You continue to own the conversation, research, file edits and verification.
Read the relevant source material first. Send only the context needed for these questions from this conversation. Do not send passwords, API keys or unrelated chat history. Each item has a unique ID and its own text. Questions must be self-contained and grounded in that text, with clear criteria. A choice has 2–20 distinct option IDs and descriptions; a score has ordered levels from lowest to highest; noul asks a yes/no question and returns P(yes). Supply an explicit unknown/other choice when the alternatives are incomplete. For many options, narrow them using actual evidence or divide into meaningful groups rather than arbitrarily discarding candidates. Batch independent items that share questions.
Example: read support tickets, ask for department among billing/technical/other and urgency among low/medium/high, then examine important or ambiguous tickets and save the owner's requested report. Laya does not contact customers or decide permissions. For development, its classification can help organize existing evidence; build/run/browser tools must establish whether code and UI work. It cannot inspect an unseen image, read a path or URL, browse, produce a website, execute commands, or prove a claim merely by scoring it. Use the appropriate Portal tool for those operations.
Treat results as model estimates. Confidence and answerConfidence are different upstream statistics; neither is guaranteed accuracy. Do not invent a universal confidence threshold, treat an inferred action as authorization, or claim external verification. Preserve uncertainty when explaining results.
Never pass the complete conversation automatically. If context is reported truncated or invalid, no decision is usable: shorten or split the actual source while preserving the evidence needed for the question. Do not disguise partial context as a full-file review. A failed/disabled/busy Laya tool is not a failed Portal task; continue with the main model and existing tools where possible, and disclose a material limitation if the owner explicitly required Laya. Do not repeatedly call unchanged failed inputs. A cancelled or timed-out observation does not prove inference stopped on the server.
Use the result to continue the requested task and provide a normal answer to the owner. Do not end with a raw classifier response. Simple conversation and direct edits need no ceremonial Laya call. Do not put Laya in a created app unless the owner requests that integration.`;

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
  },
};

function unavailable(status, message, submitted = false) {
  return {isError: true, content: [{type: 'text', text: message}],
    details: {kind: 'laya-decisions', status, inferenceSubmitted: submitted,
      upstreamCancellationVerified: false, executionAuthorized: false}};
}

export function createLayaTool({client, enabled = () => false} = {}) {
  return {
    name: 'pixel_ods_laya', label: 'Consult Laya',
    description: 'Consult the enabled local Laya decision engine for bounded choices, ordinal scores or yes/no probabilities over supplied text. Supports batches. Read the laya guide via pixel_ods_skill when needed. Advisory only: does not browse, read files, generate code, execute actions or verify their success. The Portal model uses the result to continue the task and answer the owner. Do not send secrets or unrelated conversation data. Optional; if unavailable, continue with other tools where possible.',
    parameters: LAYA_TOOL_SCHEMA,
    async execute(_callId, args, signal) {
      // Recheck at execution: descriptor caches can outlive extension disable.
      if (!enabled() || !client) return unavailable('disabled', 'Laya is not enabled for Portal. Continue with existing tools.');
      try {
        const result = await client.decide(args, signal);
        return {content: [{type: 'text', text: JSON.stringify(result)}],
          details: {kind: 'laya-decisions', status: 'completed', itemCount: result.items.length,
            executionAuthorized: false}};
      } catch (error) {
        if (!(error instanceof LayaProtocolError) && !(error instanceof LayaServiceError)) throw error;
        return unavailable(error.code, `${error.message} No Laya decision was accepted. Continue the owner's task using other available capabilities where possible.`, error.submitted === true);
      }
    },
  };
}
