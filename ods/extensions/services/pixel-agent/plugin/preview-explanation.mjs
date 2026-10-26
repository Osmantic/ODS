// What the owner reads after a workspace preview run.
//
// Owner wording. The guard keeps every check fact of a preview outcome in its
// own record (verificationForRun: "The attempted preview interaction checks
// have not passed browser inspection ..."), and the model reads what failed
// during the run (its inspection results and the guard's next-step
// instructions), so it can repair the page. The owner reads one short, plain
// line in place of those facts, which the dashboard shows muted. On the RTX Spark
// (2026-10-25) two of four builds ended with only that fact text under a
// preview that worked. The outcome keeps its status and its preview receipt,
// and nothing here certifies anything: each line says what Portal could not
// confirm, or what is wrong, and what the owner can do next. A tool-limit stop
// in a website build reads the same way (tool-loop-guard.mjs,
// progressStopPresentation).
//
// The agent's explanation. A failed or still-running preview outcome (a
// browser or interaction check that did not pass, requested text missing, a
// stale or unpublished snapshot, a verification command still running) used
// to replace the agent's whole final reply with the host's delivery text. On
// the RTX Spark (Elf of Doom, round 9) that hid the agent's correct root-cause
// explanation. Now the agent's final prose comes first and Portal's line and
// the host's remaining text follow it. The prose is delivered as written and
// is never evidence.
//
// The prose passes the filter every host-composed answer passes
// (deliverableAnswer in progress-finalization.mjs). It is dropped, and the host
// delivery stands alone, when it is empty or too short, silent (NO_REPLY),
// tool-call text, when it links any local URL other than this outcome's own
// preview, or when it carries Portal's own wording (its check label or receipt
// lines, however markdown or HTML draws them): a copied or imitated host note
// must not read as Portal's. Everything added here is fixed English text.
import { NARRATION, deliverableAnswer } from './progress-finalization.mjs';
import { promisesExecution } from './completion-assurance.mjs';
import { silentReplyText } from './owner-visible-reply.mjs';

// Portal's line after a preview outcome that did not pass. The dashboard
// mutes these exact paragraphs (dashboard/src/lib/portalDeliveryNotes.js).
export const PREVIEW_CONTROLS_NOTE =
  "Portal couldn't test every control automatically. Try it, and tell Portal if something isn't right.";
export const PREVIEW_UNCONFIRMED_NOTE =
  "Portal couldn't confirm everything automatically. Have a look, and tell Portal if something isn't right.";
export const PREVIEW_RUNNING_NOTE =
  "Portal's checks were still running when it replied. Have a look, and tell Portal if something isn't right.";
export const PREVIEW_SCRIPT_NOTE =
  'The page ran into a script problem while Portal was testing it, so parts of it may not work yet. Ask Portal to fix it.';
export const PREVIEW_MISSING_NOTE = "The page doesn't match everything you asked for yet. Ask Portal to fix it.";
// The preview card shows the last publication; the files changed after it.
export const PREVIEW_EARLIER_NOTE =
  "This preview shows the version from before Portal's latest changes. Ask Portal to publish the current files.";
export const PREVIEW_STOPPED_NOTE =
  'Portal stopped before it finished, so this preview shows an earlier version. Send a message to continue.';
// The tool limit stopped the run after the page was published: the page is
// current and up, and Portal's checks of it did not finish (RTX Spark,
// 2026-10-25: the model's own check plans kept failing under a working page).
export const PREVIEW_UNFINISHED_NOTE =
  "Portal published your page but couldn't finish checking it automatically. Try it, and tell Portal if something isn't right.";
// The run has ended: a step the reply announces will not start on its own.
export const PREVIEW_CONTINUE_NOTE = "The next step above hasn't started yet. Send a message to continue.";
export const PREVIEW_NOTES = Object.freeze([PREVIEW_CONTROLS_NOTE, PREVIEW_UNCONFIRMED_NOTE, PREVIEW_RUNNING_NOTE,
  PREVIEW_SCRIPT_NOTE, PREVIEW_MISSING_NOTE, PREVIEW_EARLIER_NOTE, PREVIEW_STOPPED_NOTE, PREVIEW_UNFINISHED_NOTE,
  PREVIEW_CONTINUE_NOTE]);

// The combined reply is host verification text (ingress limit: 32 KiB). Leave
// room for the extension and progress-lane notes that can follow it.
const MAX_EXPLAINED_CHARS = 24 * 1024;

// Portal's check label and receipt lines, matched on folded text (foldedText).
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

// Portal's line for a published preview whose checks did not pass, from the
// guard's facts about it. A page that throws is broken and a requested text
// that is missing is a known gap; anything else is what Portal could not
// confirm, and the owner can try it.
export function previewCheckNote({pending = false, pageErrors = false, missing = false, testFailed = false, controls = false} = {}) {
  if (pageErrors) return PREVIEW_SCRIPT_NOTE;
  if (missing) return PREVIEW_MISSING_NOTE;
  if (pending) return PREVIEW_RUNNING_NOTE;
  return controls && !testFailed ? PREVIEW_CONTROLS_NOTE : PREVIEW_UNCONFIRMED_NOTE;
}

// `text` as the owner reads it. `presentation` ({text, rest, note}) is a
// preview outcome's own host text, the part of it the owner keeps (its preview
// receipt or link) and Portal's line. Within `text`, which can carry other
// host lines around that host text (a missing download, a stopped lane), the
// host text becomes `line` and the kept part. Without it, `line` leads. Host
// text follows any answer in `text` (a tool-limit stop's answer comes first),
// so its last occurrence is the one replaced.
export function ownerPreviewText(text, presentation, line = presentation?.note) {
  const at = presentation ? text.lastIndexOf(presentation.text) : -1;
  if (at < 0) return line ? `${line}\n\n${text}` : text;
  return text.slice(0, at) + [line, presentation.rest].filter(Boolean).join('\n\n') +
    text.slice(at + presentation.text.length);
}

// The agent's final reply, when it can be delivered ahead of Portal's text.
function keptAnswer(reply, result) {
  if (typeof reply !== 'string' || silentReplyText(reply)) return undefined;
  const answer = deliverableAnswer(reply, {localUrlsForbidden: true,
    allowedUrls: result.preview?.url ? [result.preview.url] : []});
  return answer && !HOST_WORDING.test(foldedText(answer)) ? answer : undefined;
}

// Returns `result` in the owner's wording (ownerPreviewText), with the agent's
// final reply first when the outcome is failed or pending and the reply can be
// delivered. A reply that announces more work is followed by the line saying
// that step has not started. Returns `result` itself when nothing changes.
export function explainedPreviewDelivery(result, {reply, presentation} = {}) {
  if (typeof result?.text !== 'string' || !result.text) return result;
  const answer = ['failed', 'pending'].includes(result.status) ? keptAnswer(reply, result) : undefined;
  if (answer) {
    const fence = openFence(answer);
    const line = announcesMoreWork(answer) ? PREVIEW_CONTINUE_NOTE : presentation?.note;
    const text = [fence ? `${answer}\n${fence}` : answer, ownerPreviewText(result.text, presentation, line)].join('\n\n');
    if (text.length <= MAX_EXPLAINED_CHARS) return {...result, text};
  }
  const text = ownerPreviewText(result.text, presentation);
  return text === result.text ? result : {...result, text};
}
