// A model reply that reaches the output-token limit (stopReason "length")
// ends the run. Its unfinished tool call never runs, so nothing it was
// writing is saved, and the owner sees only the reply's opening text.
//
// strixy 2026-09-25 (Qwen3.6-35B-A3B, 8192 output tokens): "make me a cool
// looking webpage ... Best you can do" produced one whole-page write that was
// cut at 8192 tokens after 220 s. The owner saw one sentence and no page.
//
// Delivery reports a final reply cut at the output limit plainly, in place of
// OpenClaw's generic "couldn't generate a response". OpenClaw 2026.6.33 skips
// before_agent_finalize when such a reply is the whole turn; when the hook does
// run, it grants one bounded revision pass that asks for smaller steps.

// The revision prompt is appended after the history and is not persisted.
// Keep both strings byte-identical (no per-turn variable text).
export const OUTPUT_LIMIT_REASON =
  'The previous reply reached the model output limit before it finished, so its unfinished tool call did not run and nothing from it was saved.';
export const OUTPUT_LIMIT_INSTRUCTION =
  "Continue the owner's request now in smaller steps. Do not repeat the cut-off call as one large write: " +
  'split large content across several files or several smaller writes (for a web page, separate index.html, ' +
  'styles.css and script.js, each kept short), then finish the remaining requested steps and give the owner a visible answer.';
export const OUTPUT_LIMIT_UNRECOVERED_TEXT =
  "Pixel's reply reached the model output limit before its last step finished, so that step did not run. " +
  'The workspace is preserved; ask Pixel to continue in smaller steps.';

export function outputLimitReply(message) {
  return message?.role === 'assistant' && message.stopReason === 'length';
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
