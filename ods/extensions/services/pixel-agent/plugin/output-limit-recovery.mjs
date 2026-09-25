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
