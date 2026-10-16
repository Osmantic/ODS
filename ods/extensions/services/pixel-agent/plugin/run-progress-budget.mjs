import { createHash } from 'node:crypto';

// Independent of tool dispatch: the host can reject a call before plugin tool
// hooks run. Model-round accounting must still bound that continuation loop.
export const RUN_PROGRESS_LIMITS = Object.freeze({
  consecutiveFailures: 4,
  totalFailures: 12,
  roundsWithoutProgress: 8,
  identicalSuccesses: 2,
});

export const RUN_PROGRESS_STOP_REASON =
  'This response was stopped after repeated tool failures or attempts without progress. ' +
  'Saved files and previously verified publications were preserved. ' +
  'The full request was not completed; continue from the preserved work with a corrected approach.';

const PROGRESS_LANES = new Set(['workspace', 'extension']);
export function progressLaneStopReason(lane) {
  return `The ${lane === 'extension' ? 'extension' : 'workspace'} portion of this response stopped after repeated failures. ` +
    'Do not retry that portion in this response. Continue the separately requested work that remains available. ' +
    'Preserve saved files and accepted operations; the full request remains incomplete.';
}

export function failedToolOutcome(event) {
  if (event?.error) return true;
  let result = event?.result;
  for (let depth = 0; depth < 3 && result && typeof result === 'object'; depth++) {
    const details = result.details;
    if (result.isError === true || ['failed', 'error', 'blocked'].includes(details?.status) ||
        (Number.isInteger(details?.exitCode) && details.exitCode !== 0)) return true;
    result = details?.result;
  }
  return false;
}

export function createRunProgressBudget() {
  let rounds = 0;
  let failures = 0;
  let consecutiveFailures = 0;
  let terminal = false;
  const seenCalls = new Set();
  const successes = new Map();
  const laneFailures = new Map();
  const exhaustedLanes = new Set();
  return {
    get exhausted() { return terminal; },
    // Another guard (the research web-loop terminal) stopped the response.
    // Sticky, like exhaustion; it grants nothing and changes no limit.
    stop() { terminal = true; },
    get exhaustedLanes() { return [...exhaustedLanes]; },
    laneExhausted(lane) { return exhaustedLanes.has(lane); },
    beginModelRound() {
      if (++rounds > RUN_PROGRESS_LIMITS.roundsWithoutProgress) terminal = true;
      return terminal;
    },
    observeResult({ callId, tool, params, failed, pending = false, discovery = false, lane }) {
      if (terminal || typeof callId !== 'string' || !callId || seenCalls.has(callId)) return;
      seenCalls.add(callId);
      if (seenCalls.size > 256) seenCalls.delete(seenCalls.values().next().value);
      const classifiedLane = PROGRESS_LANES.has(lane) ? lane : undefined;
      if (failed) {
        failures += 1;
        if (classifiedLane) {
          const count = (laneFailures.get(classifiedLane) ?? 0) + 1;
          laneFailures.set(classifiedLane, count);
          if (count >= RUN_PROGRESS_LIMITS.consecutiveFailures) exhaustedLanes.add(classifiedLane);
        } else consecutiveFailures += 1;
        // A mixed task may retain its other lane, never an unlimited retry
        // allowance. Unknown calls retain the strict global consecutive fuse;
        // every failure still consumes the unchanged total/global round caps.
        terminal = exhaustedLanes.size === PROGRESS_LANES.size ||
          consecutiveFailures >= RUN_PROGRESS_LIMITS.consecutiveFailures ||
          failures >= RUN_PROGRESS_LIMITS.totalFailures;
        return;
      }
      // Discovery changes the available schemas, not the task's outcome. A
      // successful search between failed actions must not erase their history
      // or let differently worded searches keep a run alive indefinitely.
      if (discovery || tool === 'tool_search' || tool === 'tool_describe' || tool === 'pixel_ods_skill') return;
      consecutiveFailures = 0;
      if (classifiedLane) laneFailures.set(classifiedLane, 0);
      // An actual running-process receipt is a verified wait, not a failure.
      // Plain text saying "running" must never be supplied as this signal.
      if (pending) { rounds = 0; return; }
      const fingerprint = createHash('sha256').update(JSON.stringify([tool, params], (_key, value) =>
        value && typeof value === 'object' && !Array.isArray(value)
          ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]]))
          : value)).digest('hex');
      const count = (successes.get(fingerprint) ?? 0) + 1;
      successes.set(fingerprint, count);
      if (successes.size > 128) successes.delete(successes.keys().next().value);
      if (count <= RUN_PROGRESS_LIMITS.identicalSuccesses) rounds = 0;
    },
  };
}

// A lookup that found nothing answered its question; it did not fail.
// grep, egrep, fgrep, rg and git grep exit 1 only when nothing matched (their
// errors exit 2, git 128), and `which` exits 1 only when a name did not resolve.
// Models also chain searches (`grep a f | head; echo ===; grep b g`), so a list
// joined by `;` or `&&` qualifies when every step is a lookup, an echo, or sed
// printing a line range, optionally piped into head, tail, cat, wc, sort or
// uniq, and the last step is a bare lookup. None of the other steps can exit 1,
// so exit 1 still means a search came up empty. OpenClaw merges stdout and
// stderr, and an error in any step disqualifies the command. rg 13 prints some
// errors with no recognisable prefix, and grep -s suppresses errors, so these
// may only be the last step, where their own exit status distinguishes errors.
// Options that run programs or write files (rg --pre, git grep -O, sort -o,
// sed -i) disqualify it too. One literal `cd DIR &&` may lead. Redirections, substitutions, `||`,
// background jobs and escapes outside quotes stay ordinary failures, as does
// every other exit code.
const LOOKUP_CD = /^cd[ \t]+(?:'[^'\r\n]*'|"[^"$`\\\r\n]*"|[A-Za-z0-9._/-]+)[ \t]*&&[ \t]*/;
// GNU grep prints these on stderr for a search that still succeeded.
const LOOKUP_NOTICE = /^[ef]?grep: (?:warning: .*|.*: binary file matches)$/gm;
const LOOKUP_ERROR = new RegExp([
  '^(?:sh|bash|dash|[ef]?grep|rg|sed|git|which|head|tail|cat|wc|sort|uniq|fatal)(?:: \\d+)?: ',
  // rg 13 prints regex, argument and file errors without its name.
  '^(?:regex parse error|error|usage|Usage|Illegal option)\\b',
  ': (?:No such file or directory|Permission denied|Is a directory)$',
  '\\(os error \\d+\\)$',
].join('|'), 'm');

// Splits a command into steps at `;` and `&&`, each step into stages at `|`, and
// each stage into words with the quotes removed. Null for `||`, a lone `&`,
// redirection, grouping, substitution, comments, escapes outside quotes, `$` or
// backticks inside double quotes, and newlines.
function lookupSteps(command) {
  const steps = [[[]]];
  let word = null;
  const endWord = () => {
    if (word !== null) steps.at(-1).at(-1).push(word);
    word = null;
  };
  for (let i = 0; i < command.length; i++) {
    const c = command[i];
    if (c === "'") {
      const end = command.indexOf("'", i + 1);
      if (end < 0 || /[\r\n]/.test(command.slice(i, end))) return null;
      word = (word ?? '') + command.slice(i + 1, end);
      i = end;
    } else if (c === '"') {
      let text = '';
      let j = i + 1;
      for (; j < command.length && command[j] !== '"'; j++) {
        if (/[$`\r\n]/.test(command[j]) || (command[j] === '\\' && /^[\r\n]?$/.test(command[j + 1] ?? ''))) return null;
        // Inside double quotes a backslash escapes only `"` and `\` here.
        if (command[j] === '\\' && '"\\'.includes(command[j + 1])) j++;
        text += command[j];
      }
      if (j >= command.length) return null;
      word = (word ?? '') + text;
      i = j;
    } else if (c === ' ' || c === '\t') {
      endWord();
    } else if (c === ';' || (c === '&' && command[i + 1] === '&')) {
      endWord();
      steps.push([[]]);
      if (c === '&') i++;
    } else if (c === '|' && command[i + 1] !== '|') {
      endWord();
      steps.at(-1).push([]);
    } else if (/[|&<>()`$\\#\r\n]/.test(c)) {
      return null;
    } else {
      word = (word ?? '') + c;
    }
  }
  endWord();
  return steps.every(stages => stages.every(words => words.length)) ? steps : null;
}

function isLookup([program, ...args]) {
  if (program === 'git') {
    // git grep -O / --open-files-in-pager runs a command on the matches.
    return args[0] === 'grep' && !args.some(arg => /^--op|^-[^-]*O/.test(arg));
  }
  if (program === 'rg') return !args.some(arg => /^--(?:pre|hostname-bin)(?:=|$)/.test(arg));
  return ['grep', 'egrep', 'fgrep', 'which'].includes(program);
}

function lookupNeedsOwnExitStatus([program, ...args]) {
  if (program === 'rg') return true;
  // Quotes have already been removed. Include clustered short flags and GNU's
  // abbreviated --no-messages; conservatively decline option-shaped operands.
  return ['grep', 'egrep', 'fgrep'].includes(program) &&
    args.some(arg => /^-[^-]*s/.test(arg) || /^--no-m/.test(arg));
}

function isLookupCompanion([program, ...args]) {
  if (program === 'echo') return true;
  // GNU sed reads options after operands too, so no operand may look like one.
  return program === 'sed' && args.length > 2 && args[0] === '-n' &&
    /^[1-9]\d*(?:,[1-9]\d*)?p$/.test(args[1]) && args.slice(2).every(arg => !arg.startsWith('-'));
}

function isLookupSink([program, ...args]) {
  if (program === 'head' || program === 'tail') return /^(?:-(?:n ?\+?)?[1-9]\d*)?$/.test(args.join(' '));
  if (program === 'wc') return args.length === 1 && /^-[lwc]$/.test(args[0]);
  if (program === 'sort') return args.every(arg => /^-[bdfhinruV]+$/.test(arg));
  if (program === 'uniq') return args.every(arg => arg === '-c');
  return program === 'cat' && args.length === 0;
}

export function lookupFoundNothing(command, result) {
  if (typeof command !== 'string' || command.length > 4096) return false;
  const steps = lookupSteps(command.trim().replace(LOOKUP_CD, ''));
  if (!steps || !steps.every(([first, ...sinks]) =>
    (isLookup(first) || isLookupCompanion(first)) && sinks.every(isLookupSink))) return false;
  const last = steps.at(-1);
  if (last.length !== 1 || !isLookup(last[0]) || steps.slice(0, -1).some(([first]) => lookupNeedsOwnExitStatus(first))) return false;
  const details = result?.details;
  if (!details || typeof details !== 'object' || details.status !== 'completed' || details.exitCode !== 1 ||
      Object.hasOwn(details, 'sessionId')) return false;
  const output = typeof details.aggregated === 'string' ? details.aggregated
    : (Array.isArray(result.content) ? result.content : [])
      .map(block => block?.type === 'text' && typeof block.text === 'string' ? block.text : '').join('');
  // OpenClaw appends this exit note to every non-zero completion.
  return !LOOKUP_ERROR.test(output.replace(/\(Command exited with code 1\)/, '').replace(LOOKUP_NOTICE, ''));
}

// This deliberately does not classify general shell commands as read-only.
// Only a single quoted literal echo is provably unable to mutate the project.
export function isLiteralEcho(command) {
  return typeof command === 'string' &&
    /^\s*echo\s+(?:"[^"$`\\\r\n]*"|'[^'\r\n]*')\s*$/.test(command);
}
