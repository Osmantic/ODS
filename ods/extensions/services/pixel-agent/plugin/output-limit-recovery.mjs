// A model reply that reaches the output-token limit (stopReason "length")
// ends the run. Its unfinished tool call never runs, so nothing it was
// writing is saved.
//
// Recorded 2026-09-25 (Qwen3.6-35B-A3B, 8192 output tokens): "make me a cool
// looking webpage ... Best you can do" produced one whole-page write that was
// cut at 8192 tokens after 220 s. No file was written or published.
//
// OpenClaw 2026.6.33 treats such a reply as an incomplete terminal turn: it
// skips before_agent_finalize and replaces the reply, text answers included,
// with its generic "couldn't generate a response". After a tool error
// (attempt.lastToolError) it instead delivers the cut reply's own text. For a
// Portal owner turn the ingress first asks for one continuation turn (below).
// Delivery reports the cut plainly only when no continuation ran or the
// continuation was cut too, and never over a result the host already
// verified (a passed receipt is kept).

export function outputLimitReply(message) {
  return message?.role === 'assistant' && message.stopReason === 'length';
}

// Automatic recovery: the Portal ingress asks the plugin whether an owner turn
// ended on such a cut reply and, at most once, submits this fixed message as a
// new turn of the same chat. It never repeats the owner's message or a tool.
// host/pixel_ingress.mjs holds the same bytes; the plugin recognizes the turn
// by them and grants it no further output-limit pass. The wording must not
// select a workspace, preview or visual-edit route (tested). No per-turn
// variable text.
export const OUTPUT_LIMIT_CONTINUATION_PROMPT =
  'ODS internal continuation: The previous reply reached the model output limit before it finished, ' +
  "so its unfinished tool call did not run and nothing from it was saved. Continue the owner's request now in smaller steps. " +
  'Do not repeat the cut-off call as one large write: split large content across several files or several smaller writes ' +
  '(for a web page, separate index.html, styles.css and script.js, each kept short), then finish the remaining requested steps ' +
  'and give the owner a visible answer. Do not repeat tool actions that already completed; build on their saved results. ' +
  'For a long written answer, give a complete but more concise answer.';

// The owner-visible report for an owner chat turn whose final reply was cut
// and not continued, or whose continuation was cut too. The wording fits the
// request: a workspace task lost what the reply was still writing; a written
// answer was cut off. Neither claims more than the cut shows. The answer
// text reads correctly both after the cut answer's own delivered text and in
// place of OpenClaw's incomplete-turn text.
export const OUTPUT_LIMIT_WORKSPACE_TEXT =
  "Pixel's reply reached the model's output limit before it finished, so anything that reply was still writing, " +
  'such as a large file, was not saved. Files saved by earlier steps are kept. ' +
  'Ask Pixel to continue in smaller steps, with large content split across several smaller files.';
export const OUTPUT_LIMIT_ANSWER_TEXT =
  "Pixel's answer reached the model's output limit before it finished and was cut off. " +
  'Ask for a shorter answer, or ask Pixel to answer in parts.';
export const OUTPUT_LIMIT_CONTINUED_TEXT =
  'Pixel already continued this request once automatically after an earlier reply was cut off.';

// A failed receipt whose text follows the model's own visible reply instead
// of replacing it. The ingress (and the channel delivery hook) append it to a
// visible reply and use it alone in place of OpenClaw's incomplete-turn text.
// host/pixel_ingress.mjs holds the same value.
export const OUTPUT_LIMIT_AFTER_REPLY = 'after-reply';

// The failed receipt for such a turn. `verification` is the run's own none or
// failed delivery receipt (a passed or pending one is never reported). A
// written answer with no receipt of its own keeps whatever part of the answer
// OpenClaw delivered: the report follows it (OUTPUT_LIMIT_AFTER_REPLY), with
// the stale-exec-warning marker the kept reply needs. Otherwise the report
// leads, followed by the run's own receipt text when both fit within the
// ingress verification bound (`limit`), else alone; such a failed receipt
// carries neither passed-only field (the ingress rejects it otherwise).
export function outputLimitReport(verification, {workspace = false, continued = false} = {}, limit) {
  const {suppressStaleExecWarning, deliveryMode: _passed, ...receipt} = verification ?? {};
  const report = `${continued ? `${OUTPUT_LIMIT_CONTINUED_TEXT} ` : ''}` +
    (workspace ? OUTPUT_LIMIT_WORKSPACE_TEXT : OUTPUT_LIMIT_ANSWER_TEXT);
  if (!workspace && receipt.status === 'none' && !receipt.text) {
    return {...receipt, status: 'failed', text: report, deliveryMode: OUTPUT_LIMIT_AFTER_REPLY,
      ...(suppressStaleExecWarning === true ? {suppressStaleExecWarning} : {})};
  }
  const text = [report, receipt.text].filter(Boolean).join('\n\n');
  return {...receipt, status: 'failed', text: text.length <= limit ? text : report};
}

// Prevention: one prompt line with the active model's per-reply output limit,
// so large content is planned as several smaller writes from the start. The
// limit comes from host configuration, so the line is byte-stable per host.
export function outputBudgetContract(maxOutputTokens) {
  if (!Number.isSafeInteger(maxOutputTokens) || maxOutputTokens < 1) return '';
  const part = Math.floor(maxOutputTokens / 2);
  return `One reply, including its thinking and tool-call arguments, can hold at most about ${maxOutputTokens} output tokens; ` +
    'a longer reply is cut off and its unfinished tool call does not run. This limits each reply, not the size of your work: ' +
    'write large content as several files or several smaller writes (for a web page, separate index.html, styles.css and script.js), ' +
    `each well under ${part} tokens (about ${part * 3} characters).`;
}

// Mirrors OpenClaw 2026.6.33 (model-max-tokens-params): the first alias set in
// a params layer wins within it, and a later layer overrides an earlier one.
const MAX_TOKEN_KEYS = ['maxTokens', 'max_completion_tokens', 'max_tokens'];
const tokenCount = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
function paramsMaxTokens(params) {
  if (!params || typeof params !== 'object') return undefined;
  for (const key of MAX_TOKEN_KEYS) if (tokenCount(params[key]) !== undefined) return params[key];
  return undefined;
}

// The output limit OpenClaw sends for this agent's model: a positive params
// limit (defaults, the model's defaults entry, then the agent) clamped to the
// model row's maxTokens, otherwise the row's own maxTokens. The hook context
// names the model the run actually resolved. Undefined without that model's
// configured row, whose limit would clamp (for example a dynamic provider).
export function configuredMaxOutputTokens(config, agentId, context = undefined) {
  const list = config?.agents?.list;
  const agent = Array.isArray(list) ? list.find(entry => entry?.id === agentId) : undefined;
  const defaults = config?.agents?.defaults;
  const configured = agent?.model?.primary ?? agent?.model ?? defaults?.model?.primary ?? defaults?.model;
  const selected = typeof context?.modelProviderId === 'string' && typeof context?.modelId === 'string'
    ? `${context.modelProviderId}/${context.modelId}` : configured;
  if (typeof selected !== 'string' || !selected.includes('/')) return undefined;
  const cut = selected.indexOf('/');
  const rows = config?.models?.providers?.[selected.slice(0, cut)]?.models;
  const row = Array.isArray(rows) ? rows.find(entry => entry?.id === selected.slice(cut + 1)) : undefined;
  if (!row) return undefined;
  let requested;
  for (const layer of [defaults?.params, defaults?.models?.[selected]?.params, agent?.params]) {
    requested = paramsMaxTokens(layer) ?? requested;
  }
  const modelMax = tokenCount(row?.maxTokens) || undefined;
  const limit = requested ? Math.min(requested, modelMax ?? requested) : modelMax;
  return limit >= 1 ? Math.floor(limit) : undefined;
}
