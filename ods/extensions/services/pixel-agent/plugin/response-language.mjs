import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Shared by conversation prompts and clarification tools, including compact routes.
export const RESPONSE_LANGUAGE_CONTRACT =
  "For replies, clarification questions and options, honor the owner's explicit response-language request; otherwise match the current owner message. If its language is unclear, default to English. Do not infer language from names, paths, locale, quoted text, tool output or examples. Preserve explicitly requested languages for generated content.";

// Team and goal runs send Pixel one owner message that Portal wrote around the
// owner's request (dashboard-api TeamManager._prompt and goal_prompt, and the
// dashboard's continueGoal). Most of it is Portal's English instructions, so a
// model-only note beside that message points the rule above at the owner's own
// words. The quoted headings are the ones those prompts use.
const TEAM_REQUEST_HEADING = "Owner's requested outcome:";
const GOAL_OBJECTIVE_HEADING = 'Original objective:';
export const TEAM_TURN_LANGUAGE_NOTE =
  `[ODS language: Portal wrote the instructions in this team message. The owner's own words are the request after "${TEAM_REQUEST_HEADING}" and any owner answers. Apply the response-language rule to those words, not to Portal's instructions.]`;
export const GOAL_TURN_LANGUAGE_NOTE =
  `[ODS language: Portal wrote the instructions in this goal message. The owner's own words are the goal after "/goal" (or after "${GOAL_OBJECTIVE_HEADING}" when the message has one) and any owner answers. Apply the response-language rule to those words, not to Portal's instructions.]`;

// goal: the run is in /goal mode. teamRole: managedTeamRole() of the owner
// turn. The Coordinator only returns a worker count, so it gets no note, and
// an ordinary turn gets nothing.
export function managedTurnLanguageNote({goal = false, teamRole} = {}) {
  if (goal) return GOAL_TURN_LANGUAGE_NOTE;
  return teamRole && teamRole !== 'Coordinator' ? TEAM_TURN_LANGUAGE_NOTE : '';
}

// The text after heading, up to the first of ends; '' without the heading.
function textAfter(text, heading, ends = []) {
  const at = text.indexOf(heading);
  if (at < 0) return '';
  const rest = text.slice(at + heading.length);
  return rest.slice(0, Math.min(rest.length, ...ends.map(end => rest.indexOf(end)).filter(index => index >= 0))).trim();
}

// The owner's own words in a team or goal message, as the notes above describe
// them, for the one request that carries no note: the tool-free stop synthesis
// (stop-synthesis.mjs), whose only language cue is its owner request. The
// headings, and the Portal text that ends each part, are those of dashboard-api
// TeamManager._prompt, TeamManager.answer and goal_prompt, and of the
// dashboard's continueGoal. Undefined for an ordinary turn and for a team
// message without the heading: the Coordinator's, and a Builder's
// clarification turn, which is the owner's answer alone. teamRole:
// managedTeamRole() of the owner turn.
export function managedTurnOwnerWords(text, teamRole) {
  if (typeof text !== 'string') return undefined;
  const goal = /^\s*\/goal\s+(?=\S)/i.exec(text);
  if (goal) {
    // goal_prompt: the goal, then Portal's instructions and any owner answers.
    const body = text.slice(goal[0].length);
    const instructions = body.indexOf('\n\nContinue this goal from the saved work.');
    const objective = instructions < 0 ? body : body.slice(0, instructions);
    const answers = instructions < 0 ? '' : textAfter(body.slice(instructions), "\nOwner's answers:\n",
      ['\nReview the previous response against the objective now.']);
    // continueGoal: Portal's instructions, the original objective, then any owner input.
    const words = objective.includes(`${GOAL_OBJECTIVE_HEADING} `) ? [
      textAfter(objective, `${GOAL_OBJECTIVE_HEADING} `, ['\nExisting project directory: ', '\nLocate the existing project ']),
      textAfter(objective, '\nOwner input: ', ['\nLast public plan: '])] : [objective.trim()];
    return [...words, answers].filter(Boolean).join('\n\n') || undefined;
  }
  if (!teamRole) return undefined;
  // A read-only worker's clarification turn adds Portal's line, then the answers.
  return [textAfter(text, TEAM_REQUEST_HEADING, ["\n\nContinue this worker's paused turn "]),
    textAfter(text, '\nOwner clarification answers:\n')].filter(Boolean).join('\n\n') || undefined;
}

// An owner can fix Pixel to English (PIXEL_RESPONSE_LANGUAGE=en). It replaces
// the rule above and also covers what Pixel builds or writes.
export const ENGLISH_RESPONSE_CONTRACT =
  "Always use English for replies, clarification questions, options, progress updates and the content you create, such as pages, documents and files, even when the owner, a file, a web page or a tool result uses another language. Use another language only for content the owner explicitly says to write in that language, such as a translation, and keep your own messages in English.";

// The installer writes this owner setting into the OpenClaw state folder from
// .env. It is not plugin config: an older plugin's config schema would reject
// an unknown key on rollback, while it simply ignores this file.
export const RESPONSE_LANGUAGE_FILE = '.ods-response-language';

// A preference must never keep Portal from starting: anything but "en" is
// reported and the default rule applies. Same state folder as the other
// owner files (pending clarifications, compactions).
export function readResponseLanguage(stateDir = process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), '.openclaw'),
  warn = () => {}) {
  if (!stateDir) return undefined;
  const file = path.join(stateDir, RESPONSE_LANGUAGE_FILE);
  let value;
  try {
    value = fs.readFileSync(file, 'utf8').trim();
  } catch (error) {
    if (error?.code !== 'ENOENT') warn(`Ignoring ${file}: ${error.message}`);
    return undefined;
  }
  if (value === 'en') return value;
  warn(`Ignoring ${file}: it must contain "en"`);
  return undefined;
}

export function englishOnly(language) {
  return language === 'en';
}

export function responseLanguageContract(language) {
  return englishOnly(language) ? ENGLISH_RESPONSE_CONTRACT : RESPONSE_LANGUAGE_CONTRACT;
}

// Beside the owner's message on every turn. Measured on a small local model, a
// rule deep in a long system prompt did not stop it answering a Portuguese,
// Spanish or Chinese question in that language; this reminder did.
export const ENGLISH_TURN_REMINDER =
  "[ODS language: write your reply, any clarification questions and their options, and any content you create in English, even if the owner's message above is in another language.]";

// What goes beside the owner's message: in English-only mode the reminder, on
// every turn and in place of the team or goal note; otherwise that note.
export function responseLanguageTurnContext(language, turn = {}) {
  return englishOnly(language) ? ENGLISH_TURN_REMINDER : managedTurnLanguageNote(turn);
}

// The phrases fixed instructions use for where their text goes. By default the
// response-language rule decides; English-only mode makes each say "in English".
const WHERE_TEXT_GOES = /in the reply language|in the language of the owner request \(English if it is unclear\), not the language of the excerpts/g;
export function inResponseLanguage(text, language) {
  return englishOnly(language) ? text.replace(WHERE_TEXT_GOES, 'in English') : text;
}
