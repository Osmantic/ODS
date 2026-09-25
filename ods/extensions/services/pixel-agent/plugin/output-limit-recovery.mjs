// A model reply that reaches the output-token limit (stopReason "length")
// ends the run. Its unfinished tool call never runs, so nothing it was
// writing is saved.
//
// strixy 2026-09-25 (Qwen3.6-35B-A3B, 8192 output tokens): "make me a cool
// looking webpage ... Best you can do" produced one whole-page write that was
// cut at 8192 tokens after 220 s. No file was written or published.
//
// OpenClaw 2026.6.33 treats such a reply as an incomplete terminal turn and
// skips before_agent_finalize, so Pixel cannot revise it there. Delivery of an
// owner's workspace task explains why the files are missing; text answers keep
// their partial reply unchanged.

export const OUTPUT_LIMIT_UNRECOVERED_TEXT =
  "Pixel's reply reached the model's output limit before its file write finished, so that write did not run " +
  'and nothing from it was saved. Large files have to be written in smaller parts.';

export function outputLimitReply(message) {
  return message?.role === 'assistant' && message.stopReason === 'length';
}
