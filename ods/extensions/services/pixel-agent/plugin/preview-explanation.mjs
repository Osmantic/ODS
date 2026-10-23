// Keep the agent's own explanation when a workspace preview check fails.
//
// A failed or still-running preview outcome (a browser or interaction check
// that did not pass, requested text missing, a stale or unpublished snapshot,
// a verification command still running) used to replace the agent's whole
// final reply with the host's delivery text. On the RTX Spark (Elf of Doom,
// round 9) that hid the agent's correct root-cause explanation: the owner saw
// only "The attempted preview interaction checks have not passed browser
// inspection ... Your preview is ready."
//
// Now the agent's final prose comes first and the host facts follow it,
// unchanged, under a fixed label. The prose is delivered as written and is
// never evidence: the outcome keeps its status, every host failure line and
// the preview receipt still follow it, and the note after it always says the
// reply is the agent's own account and that the request did not pass (or has
// not finished) Portal's checks. No wording heuristic decides that: it is true
// for every outcome this applies to, whatever the prose claims.
//
// The prose passes the filter every host-composed answer passes
// (deliverableAnswer in progress-finalization.mjs). It is dropped, and the host
// delivery stands alone as before, when it is empty or too short, silent
// (NO_REPLY), tool-call text, when it links any local URL other than this
// outcome's own preview, or when it carries Portal's own wording (its label or
// receipt lines, however markdown or HTML draws them): a copied or imitated
// host note must not read as Portal's. Everything added here is fixed English
// text, apart from an inspection step's locator, which is quoted as code.
import { NARRATION, deliverableAnswer } from './progress-finalization.mjs';
import { promisesExecution } from './completion-assurance.mjs';
import { silentReplyText } from './owner-visible-reply.mjs';

export const PREVIEW_CHECK_LABEL = '**Portal check:**';
export const PREVIEW_ACCOUNT_NOTE = "The reply above is the agent's own account, not a verified result.";
export const PREVIEW_FAILED_NOTE = "This request did not pass Portal's checks.";
export const PREVIEW_PENDING_NOTE = "Portal's checks for this request have not finished.";
// The run has ended: a step the reply announces will not start on its own.
export const PREVIEW_STOPPED_NOTE =
  'Portal has stopped for this message, so the next step described above has not started. Send a message to continue.';

// The combined reply is host verification text (ingress limit: 32 KiB). Leave
// room for the extension and progress-lane notes that can follow it.
const MAX_EXPLAINED_CHARS = 24 * 1024;

// Portal's label and receipt lines, matched on folded text (foldedText).
const HOST_WORDING = /\bportal[\s\p{P}]{0,3}checks?\b|\byour (?:last published )?preview is (?:ready|still available)\b|\bpublished from your workspace\b|\bcreated by portal\b|\[open (?:last published )?preview\]|\bpublication scope\b/iu;

const codePoint = value => value > 0 && value <= 0x10ffff ? String.fromCodePoint(value) : ' ';
// The words a reader sees once the dashboard renders the text: character
// references decoded (named ones as a space), compatibility forms (fullwidth,
// no-break space) folded, invisible format characters and markdown emphasis,
// code and escape marks removed, whitespace collapsed.
export function foldedText(text) {
  return text
    .replace(/&#(?:x([0-9a-f]{1,6})|(\d{1,7}));?/gi, (_, hex, dec) => codePoint(hex ? parseInt(hex, 16) : Number(dec)))
    .replace(/&[a-z][a-z0-9]{1,31};/gi, ' ')
    .normalize('NFKC')
    .replace(/[\p{Cf}*_`~\\]/gu, '')
    .replace(/\s+/g, ' ');
}

// The code fence still open at the end of `text`, read as the dashboard reads
// it (publicationDisplay.js insideFence, CommonMark): a closing fence uses the
// opening character, is at least as long, and has nothing after it; a backtick
// line whose info string holds a backtick opens nothing.
export function openFence(text) {
  let fence;
  for (const line of text.split(/\r?\n/)) {
    const marker = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (!marker) continue;
    if (!fence) {
      if (marker[1][0] !== '`' || !marker[2].includes('`')) fence = marker[1];
    } else if (marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = undefined;
  }
  return fence;
}

// The reply announces more work: a first-person promise anywhere, or a last
// sentence that narrates the next action ("Now let me republish the preview.").
function announcesMoreWork(answer) {
  const line = answer.split('\n').map(value => value.trim()).filter(Boolean).at(-1) ?? '';
  const sentence = line.split(/(?<=[.!?])\s+(?=\S)/).at(-1).replace(/^[\s>#*_`|~+\-–—•]*(?:\d{1,3}[.)]\s*)?[\s*_`]*/u, '');
  return promisesExecution(answer) || NARRATION.test(sentence);
}

// A locator as an inline code span that no backtick inside it can close.
function codeSpan(value) {
  const text = value.replace(/\s+/g, ' ').trim();
  if (!text.includes('`')) return `\`${text}\``;
  const ticks = '`'.repeat(Math.max(0, ...[...text.matchAll(/`+/g)].map(match => match[0].length)) + 1);
  return `${ticks} ${text} ${ticks}`;
}

const STEP_FAILURES = new Map([
  ['no_match', 'matched no element'],
  ['selector_not_unique', 'matched more than one element'],
  ['invalid_selector', 'has an invalid CSS selector'],
  ['visibility_mismatch', 'did not show the requested visibility'],
  ['text_mismatch', 'did not show the expected text'],
  ['not_in_viewport', 'captured nothing on screen'],
  ['press_failed', 'could not complete the key press'],
  ['focus_failed', 'could not focus the page'],
  ['text_field_press_unsupported', 'was refused because its target accepts text'],
]);

// One fixed-English line naming the first failed step of a failed inspection
// receipt of `preview` (the inspect tool validated the receipt), or undefined.
// Only bounded fields are read: the step number, action, error code, the
// capsule-computed color and the plan's own locator. Never page text.
export function inspectionFailureLine(details, preview) {
  if (!preview || details?.status !== 'failed' || details.siteId !== preview.siteId || details.sha256 !== preview.sha256) return undefined;
  const lead = 'Latest browser inspection of this preview';
  const code = value => typeof value === 'string' && /^[a-z_]{1,40}$/.test(value) ? value : undefined;
  const step = Array.isArray(details.steps) ? details.steps.find(item => item?.status === 'failed') : undefined;
  if (!step) return code(details.errorCode) ? `${lead} did not complete (${details.errorCode}).` : undefined;
  if (!Number.isInteger(step.index) || step.index < 0 || step.index > 11 || !/^[a-z-]{1,20}$/.test(step.action ?? '')) return undefined;
  const locator = step.locator ?? {};
  const target = typeof locator.selector === 'string' && locator.selector.length <= 256 ? ` on ${codeSpan(locator.selector)}`
    : /^[a-z]{1,20}$/.test(locator.role ?? '') && typeof locator.name === 'string' && locator.name.length <= 120
      ? ` on the ${locator.role} named ${codeSpan(locator.name)}` : '';
  const hex = step.paint?.dominant?.hex;
  const failure = step.errorCode === 'uniform_paint' && /^#[0-9a-f]{6}$/.test(hex ?? '') ? `captured one color only (${hex})`
    : STEP_FAILURES.get(step.errorCode) ?? (code(step.errorCode) ? `did not pass (${step.errorCode})` : 'did not pass');
  return `${lead}: step ${step.index + 1} (${step.action})${target} ${failure}.`;
}

// Returns `result` with the agent's prose ahead of its host text, or `result`
// itself when it is not a failed or pending outcome or the prose cannot be
// delivered. `inspection` is a line from inspectionFailureLine.
export function explainedPreviewDelivery(result, {reply, inspection} = {}) {
  if (!['failed', 'pending'].includes(result?.status) || typeof result.text !== 'string' || !result.text) return result;
  if (typeof reply !== 'string' || silentReplyText(reply)) return result;
  const answer = deliverableAnswer(reply, {localUrlsForbidden: true,
    allowedUrls: result.preview?.url ? [result.preview.url] : []});
  if (!answer || HOST_WORDING.test(foldedText(answer))) return result;
  const fence = openFence(answer);
  const note = [PREVIEW_ACCOUNT_NOTE, result.status === 'pending' ? PREVIEW_PENDING_NOTE : PREVIEW_FAILED_NOTE,
    ...(announcesMoreWork(answer) ? [PREVIEW_STOPPED_NOTE] : [])].join(' ');
  const text = [fence ? `${answer}\n${fence}` : answer, `${PREVIEW_CHECK_LABEL} ${note}`, inspection, result.text]
    .filter(Boolean).join('\n\n');
  return text.length > MAX_EXPLAINED_CHARS ? result : {...result, text};
}
