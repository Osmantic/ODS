// An owner-authored Portal/dashboard message always needs a visible reply
// (prompt-contract.mjs). A final reply that is only OpenClaw's silent sentinel
// gets one bounded before_agent_finalize revision pass; a second silent reply
// keeps the ingress's honest "ended without a visible answer" fallback.
//
// Scope: OpenClaw retries an empty final reply once by itself and does not run
// before_agent_finalize for it, so only non-empty silent text reaches here.
// Heartbeat and cron runs carry another trigger (and silent-expected runs skip
// the hook entirely), so they keep NO_REPLY semantics.

// The revision prompt is appended after the history and is not persisted.
// Keep both strings byte-identical (no per-turn variable text).
export const OWNER_VISIBLE_REPLY_REASON =
  'The previous final answer was silent (NO_REPLY), but the owner is waiting for a reply in this chat.';
export const OWNER_VISIBLE_REPLY_INSTRUCTION =
  "Reply now to the owner's latest message above with a visible natural-language answer. " +
  'Do not output NO_REPLY or another silent token; if the message needs only an acknowledgement, acknowledge it briefly. ' +
  'Do not repeat completed work.';

const SILENT_TOKEN = /^\s*(?:NO_REPLY|HEARTBEAT_OK)(?:\s+(?:NO_REPLY|HEARTBEAT_OK))*\s*$/i;
const ODS_OWNER_USER = /^ods-[0-9a-f]{64}$/;

// Mirrors OpenClaw's silent forms: token-only text, a JSON string of the token,
// or a JSON object whose action is the token.
export function silentReplyText(text) {
  if (typeof text !== 'string') return false;
  const trimmed = text.trim();
  if (!trimmed || SILENT_TOKEN.test(trimmed)) return true;
  if (!/^["{]/.test(trimmed) || !/NO_REPLY/i.test(trimmed)) return false;
  try {
    const parsed = JSON.parse(trimmed);
    return typeof parsed === 'string' ? SILENT_TOKEN.test(parsed)
      : Boolean(parsed) && typeof parsed === 'object' && !Array.isArray(parsed) &&
        typeof parsed.action === 'string' && SILENT_TOKEN.test(parsed.action);
  } catch { return false; }
}

// Owner-authored interactive turn: a user-triggered run in the ODS owner chat
// session that the ingress derives from the dashboard/Portal user.
export function ownerInteractiveTurn(context, agentId = 'pixel') {
  const prefix = `agent:${agentId}:openai-user:`;
  return context?.trigger === 'user' && typeof context.sessionKey === 'string' &&
    context.sessionKey.startsWith(prefix) && ODS_OWNER_USER.test(context.sessionKey.slice(prefix.length));
}
