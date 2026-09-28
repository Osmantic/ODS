// Keep Pixel's system prompt identical across chats.
//
// The pinned OpenClaw runtime ends the base system prompt with
//   Runtime: agent=pixel | session=<session key> | sessionId=<uuid> | host=… | thinking=off
// The session key and id differ in every chat. On a local llama.cpp server the
// Portal side chat, a new chat, or a chat the owner returns to therefore
// shares only the tokens before them with the chat resident in the slot, and
// re-prefills everything after (fleet: first difference at token 12,969 of a
// 17.5k-token first request, checkpoint 8,192 restored, 9.3k tokens re-read).
//
// The model does not need either value. Tools receive the session from the
// runtime, cron uses "current", and in 21k recorded Pixel tool calls the only
// uses of them were two `process` polls that mistook the chat id for a
// background-process id. Removing both fields makes the whole system prompt
// the same bytes for every Pixel chat on a host, so a new chat reuses the
// cached prefix up to its own first message.
//
// Only the two fields of the `pixel` agent's Runtime line are removed; the
// rest of the prompt and other agents are left as they are. OpenClaw passes
// message text through the same replacement on each model call, so a message
// line that itself starts with this Runtime prefix and these fields loses them
// too, identically on every call; stored transcripts are never rewritten.

export const PIXEL_RUNTIME_LINE_PREFIX = 'Runtime: agent=pixel';

// `session=` and `sessionId=` directly after `agent=pixel`, in OpenClaw's
// order, up to the next ` | name=` field or the end of the line. A session key
// may contain any printable text (OpenClaw strips control characters), so the
// field ends only where the next known-shaped field begins. Not global: the
// base prompt has one Runtime line, and a non-global pattern keeps no state.
export const PIXEL_RUNTIME_SESSION_FIELDS =
  /^(Runtime: agent=pixel)(?: \| session=[^\n]*?)?(?: \| sessionId=[^\n]*?)?(?= \| (?!session=|sessionId=)[a-z_]+=|$)/m;

export const PIXEL_RUNTIME_LINE_TRANSFORMS = Object.freeze({
  input: Object.freeze([Object.freeze({from: PIXEL_RUNTIME_SESSION_FIELDS, to: '$1'})]),
});

export function stablePixelRuntimeLine(text) {
  return typeof text === 'string' ? text.replace(PIXEL_RUNTIME_SESSION_FIELDS, '$1') : text;
}

// OpenClaw applies registered input text transforms to the system prompt when
// it builds each attempt, before hook context is appended, and to message text
// on each model call (inside its tool-result projection, which keeps its own
// state on the original messages). Registration is skipped on runtimes
// without the API and when the owner disabled this plugin's prompt changes.
export function registerStableRuntimeLine(api) {
  if (typeof api?.registerTextTransforms !== 'function') return false;
  if (api.config?.plugins?.entries?.['pixel-ods']?.hooks?.allowPromptInjection === false) return false;
  api.registerTextTransforms(PIXEL_RUNTIME_LINE_TRANSFORMS);
  return true;
}
