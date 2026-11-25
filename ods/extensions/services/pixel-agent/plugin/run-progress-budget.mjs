import { createHash } from 'node:crypto';

// Independent of tool dispatch: the host can reject a call before plugin tool
// hooks run. Model-round accounting must still bound that continuation loop.
//
// Failures count over the most recent charged results, not over the whole
// response. A flat total of 12 per response stopped every long elf-of-doom
// change on the laptop (2026-10-28 and 2026-10-30: 85, 140 and 226 calls) on
// harmless misses spread across the run: searches that found nothing, a file
// that was not there, an edit whose old text had changed. Those runs had at
// most 9 failures in any 40 results; a stuck loop fails far more often.
export const RUN_PROGRESS_LIMITS = Object.freeze({
  consecutiveFailures: 4,
  recentFailures: 12,
  recentResults: 40,
  roundsWithoutProgress: 8,
  identicalSuccesses: 2,
});

// The one notice before a stop (takeWarning): the streak or the recent
// failures came within reach of their limit.
export const RUN_PROGRESS_WARNING = Object.freeze({
  consecutiveFailures: 3,
  recentFailures: 8,
});

// Delivered at the end of a persisted tool result, never in the system prompt,
// and byte-identical every time (see PR #6647, llama.cpp prefix caching). Its
// marker differs from the stop's "[ODS Pixel tool limit]": tools still work.
export const RUN_PROGRESS_WARNING_TEXT =
  '[ODS Pixel failure warning] Several recent tool calls failed. If failures continue, Portal stops this response ' +
  'and switches tools off, even in the middle of a change. If a change across several files is only partly made, ' +
  'finish it now or undo the parts already made, so the saved project still works. Check a path before reading it, ' +
  're-read the exact lines an edit replaces, and change approach instead of repeating a call that failed.';

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
  let consecutiveFailures = 0;
  let terminal = false;
  // Charged results, oldest first: true for a failure. Discovery and process
  // polls never enter.
  const recent = [];
  let charged = 0;
  // takeWarning: whether the current streak was warned, and when the last
  // warning about the recent failures came.
  let streakWarned = false;
  let windowWarnedAt;
  const seenCalls = new Set();
  const successes = new Map();
  const laneFailures = new Map();
  const exhaustedLanes = new Set();
  const charge = failed => {
    recent.push(failed);
    if (recent.length > RUN_PROGRESS_LIMITS.recentResults) recent.shift();
    charged += 1;
  };
  const recentFailures = () => recent.filter(Boolean).length;
  return {
    get exhausted() { return terminal; },
    // Another guard (the research web-loop terminal) stopped the response.
    // Sticky, like exhaustion; it grants nothing and changes no limit.
    stop() { terminal = true; },
    get exhaustedLanes() { return [...exhaustedLanes]; },
    laneExhausted(lane) { return exhaustedLanes.has(lane); },
    // True when the run has come within reach of a failure limit: once per
    // failure streak (the global one, or a lane's that has not stopped that
    // lane), and for the recent failures at most once per recentResults
    // charged results. The caller delivers RUN_PROGRESS_WARNING_TEXT, so a
    // change spread over several files can be finished or undone before tools
    // are switched off.
    takeWarning() {
      if (terminal) return false;
      const streak = consecutiveFailures >= RUN_PROGRESS_WARNING.consecutiveFailures ||
        [...laneFailures].some(([lane, count]) => count >= RUN_PROGRESS_WARNING.consecutiveFailures && !exhaustedLanes.has(lane));
      if (streak && !streakWarned) {
        streakWarned = true;
        windowWarnedAt = charged;
        return true;
      }
      if (recentFailures() < RUN_PROGRESS_WARNING.recentFailures ||
          (windowWarnedAt !== undefined && charged - windowWarnedAt < RUN_PROGRESS_LIMITS.recentResults)) return false;
      windowWarnedAt = charged;
      return true;
    },
    // Compaction may have summarized a delivered warning away: the next one
    // that is due is delivered again.
    rearmWarning() {
      streakWarned = false;
      windowWarnedAt = undefined;
    },
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
        charge(true);
        if (classifiedLane) {
          const count = (laneFailures.get(classifiedLane) ?? 0) + 1;
          laneFailures.set(classifiedLane, count);
          if (count >= RUN_PROGRESS_LIMITS.consecutiveFailures) exhaustedLanes.add(classifiedLane);
        } else consecutiveFailures += 1;
        // A mixed task may retain its other lane, never an unlimited retry
        // allowance. Unknown calls retain the strict global consecutive fuse;
        // every failure still counts toward the recent-failure and round caps.
        terminal = exhaustedLanes.size === PROGRESS_LANES.size ||
          consecutiveFailures >= RUN_PROGRESS_LIMITS.consecutiveFailures ||
          recentFailures() >= RUN_PROGRESS_LIMITS.recentFailures;
        return;
      }
      // Discovery changes the available schemas, not the task's outcome. A
      // successful search between failed actions must not erase their history
      // or let differently worded searches keep a run alive indefinitely.
      if (discovery || tool === 'tool_search' || tool === 'tool_describe' || tool === 'pixel_ods_skill') return;
      consecutiveFailures = 0;
      if (classifiedLane) laneFailures.set(classifiedLane, 0);
      if (!classifiedLane || [...laneFailures.values()].every(count => count === 0)) streakWarned = false;
      // An actual running-process receipt is a verified wait, not a failure.
      // Plain text saying "running" must never be supplied as this signal. Nor
      // does it dilute the recent failures: a loop that polls a server that
      // never answers between failing calls still stops.
      if (pending) { rounds = 0; return; }
      charge(false);
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
// printing a line range, optionally piped into head, tail, cat, wc, sort, uniq,
// grep, or sed printing a line range of what it is piped, and the last step is
// a bare lookup. None of the other steps can exit 1, so exit 1 still means a
// search came up empty. OpenClaw merges stdout and stderr, and an error in any
// step disqualifies the command. rg 13 prints some errors with no recognisable
// prefix, so rg may only be the last step, where its own exit status tells an
// error from an empty search. Options that run programs or write files
// (rg --pre, git grep -O, sort -o, sed -i) disqualify it too. One literal
// `cd DIR &&` or `cd DIR;` may lead: a cd that fails prints the shell's error,
// which disqualifies the command (laptop, 2026-10-30: seven empty searches,
// most written `cd DIR; grep …`, were charged and helped stop the run).
// Redirections, substitutions, `||`, background jobs and escapes outside
// quotes stay ordinary failures, as does every other exit code.
const LOOKUP_CD = /^cd[ \t]+(?:'[^'\r\n]*'|"[^"$`\\\r\n]*"|[A-Za-z0-9._/-]+)[ \t]*(?:&&|;)[ \t]*/;
// GNU grep prints these on stderr for a search that still succeeded.
const LOOKUP_NOTICE = /^[ef]?grep: (?:warning: .*|.*: binary file matches)$/gm;
const LOOKUP_ERROR = new RegExp([
  '^(?:sh|bash|dash|[ef]?grep|rg|sed|git|which|head|tail|cat|wc|sort|uniq|fatal)(?:: \\d+)?: ',
  // A cd that failed, whatever path the shell names itself by.
  "\\bcd: can't cd to ",
  // rg 13 prints regex, argument and file errors without its name.
  '^(?:regex parse error|error|usage|Usage|Illegal option)\\b',
  ': (?:No such file or directory|Permission denied|Is a directory)$',
  '\\(os error \\d+\\)$',
].join('|'), 'm');

// Splits a command into steps at `;` and `&&`, each step into stages at `|`, and
// each stage into words with the quotes removed. Null for `||`, a lone `&`,
// redirection, grouping, substitution, comments, escapes outside quotes, `$` or
// backticks inside double quotes, and newlines. With mergeStderr, a whole
// unquoted `2>&1` word is dropped: OpenClaw already merges stderr into stdout.
function lookupSteps(command, {mergeStderr = false} = {}) {
  const steps = [[[]]];
  let word = null;
  const endWord = () => {
    if (word !== null) steps.at(-1).at(-1).push(word);
    word = null;
  };
  for (let i = 0; i < command.length; i++) {
    const c = command[i];
    if (mergeStderr && word === null && command.startsWith('2>&1', i) && /^(?:[ \t;]|&&|$)/.test(command.slice(i + 4, i + 6))) {
      i += 3;
    } else if (c === "'") {
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

function isLookupCompanion([program, ...args]) {
  if (program === 'echo') return true;
  // GNU sed reads options after operands too, so no operand may look like one.
  return program === 'sed' && args.length > 2 && args[0] === '-n' &&
    /^[1-9]\d*(?:,[1-9]\d*)?p$/.test(args[1]) && args.slice(2).every(arg => !arg.startsWith('-'));
}

function isLookupSink([program, ...args]) {
  if (program === 'head' || program === 'tail') return /^(?:-(?:n ?\+?)?[1-9]\d*)?$/.test(args.join(' '));
  // `grep -n "" ui.js | sed -n '560,600p'`: a line range of the piped text only.
  if (program === 'sed') return args.length === 2 && args[0] === '-n' && /^[1-9]\d*(?:,[1-9]\d*)?p$/.test(args[1]);
  // `grep -rn x *.js | grep function`: grep has no option that writes or runs
  // anything, and its own errors carry its name.
  if (['grep', 'egrep', 'fgrep'].includes(program)) return true;
  if (program === 'wc') return args.length === 1 && /^-[lwc]$/.test(args[0]);
  if (program === 'sort') return args.every(arg => /^-[bdfhinruV]+$/.test(arg));
  if (program === 'uniq') return args.every(arg => arg === '-c');
  return program === 'cat' && args.length === 0;
}

// The edit-early notice in tool-loop-guard.mjs counts commands that only read
// files, whatever they returned: the lookup shape above, or a file read (cat,
// head, tail, nl, wc, a sed range print), piped through filters (the sinks
// above, grep, a sed range print), with a trailing 2>&1. Never a command that
// can write: redirections, tee, sed -i or a sed w command, sort -o.
const SED_RANGE_PRINT = /^(?:\d+|\/[^/\\]*\/)(?:,(?:\d+|\/[^/\\]*\/|\$))?p$/;
function isFileRead([program, ...args]) {
  const options = args.filter(arg => arg.startsWith('-'));
  if (options.length === args.length) return false;
  if (program === 'cat') return options.every(arg => arg === '-n');
  if (program === 'head' || program === 'tail') return options.every(arg => /^-n?\d*$/.test(arg) && arg !== '-');
  if (program === 'nl') return options.every(arg => arg === '-ba');
  if (program === 'wc') return options.every(arg => /^-[lwc]$/.test(arg));
  return program === 'sed' && args[0] === '-n' && SED_RANGE_PRINT.test(args[1] ?? '') &&
    args.length > 2 && args.slice(2).every(arg => !arg.startsWith('-'));
}
function isReadFilter(stage) {
  const [program, ...args] = stage;
  if (['grep', 'egrep', 'fgrep'].includes(program)) return true;
  if (program === 'sed') return args.length === 2 && args[0] === '-n' && SED_RANGE_PRINT.test(args[1]);
  return isLookupSink(stage);
}
export function readOnlyLookup(command) {
  if (typeof command !== 'string' || command.length > 4096) return false;
  const steps = lookupSteps(command.trim().replace(LOOKUP_CD, ''), {mergeStderr: true});
  return Boolean(steps) && steps.every(([first, ...filters]) =>
    (isLookup(first) || isLookupCompanion(first) || isFileRead(first)) && filters.every(isReadFilter)) &&
    steps.some(([[program]]) => program !== 'echo');
}

export function lookupFoundNothing(command, result) {
  if (typeof command !== 'string' || command.length > 4096) return false;
  const steps = lookupSteps(command.trim().replace(LOOKUP_CD, ''));
  if (!steps || !steps.every(([first, ...sinks]) =>
    (isLookup(first) || isLookupCompanion(first)) && sinks.every(isLookupSink))) return false;
  const last = steps.at(-1);
  if (last.length !== 1 || !isLookup(last[0]) || steps.slice(0, -1).some(([[program]]) => program === 'rg')) return false;
  const details = result?.details;
  if (!details || typeof details !== 'object' || details.status !== 'completed' || details.exitCode !== 1 ||
      Object.hasOwn(details, 'sessionId')) return false;
  const output = typeof details.aggregated === 'string' ? details.aggregated
    : (Array.isArray(result.content) ? result.content : [])
      .map(block => block?.type === 'text' && typeof block.text === 'string' ? block.text : '').join('');
  // OpenClaw appends this exit note to every non-zero completion.
  return !LOOKUP_ERROR.test(output.replace(/\(Command exited with code 1\)/, '').replace(LOOKUP_NOTICE, ''));
}

// A node check, test or harness that ran and reported a failure is a test
// result, not a failed tool call. The sandbox has node without npm so the agent
// can check JavaScript offline (the verification guide in agent-skills.mjs):
// node --check exits 1 on a syntax error, node --test exits 1 when a test
// fails, and a harness script exits 1 when its own check fails or it throws.
// Charged as failures, the test-fix loop that guide asks for (edit, red run,
// edit) reached the 12-failure total, and one red node --check per file the
// 4-failure streak, before the fix. A command qualifies when every step is a
// node run: `node` with long options (--name or --name=value) and either
// --check (or -c) and one script, --test and its files, or a script file and
// its arguments, optionally behind `timeout DURATION` and followed by 2>&1,
// joined by `;` or `&&`, after one optional literal `cd DIR &&` or `cd DIR;`. Inline code
// (-e, -p, --eval, --print), stdin, the REPL, --run (a package.json shell
// script), other short options, pipes, `||`, redirections, substitutions and
// environment assignments disqualify it. Only exit 1 with output qualifies.
// node not found (127), a timeout (124, or OpenClaw's own), a signal or abort
// (128 and above), a bad option (9), no output at all, a shell error, and a
// script, module or file that does not exist (a wrong path) stay failures.
// A script path, quoted or not (the tokenizer refuses line breaks); never an option.
const NODE_SCRIPT = /^(?!-).*\.[cm]?[jt]s$/;
const NODE_REFUSED_OPTION = /^--(?:eval|print|interactive|run)(?:=|$)/;
const NODE_SETUP_ERROR = new RegExp([
  // The shell, timeout or env could not start node, or a step failed to start.
  '^(?:sh|bash|dash|timeout|env)(?:: \\d+)?: ',
  "\\bcd: can't cd to ",
  // A wrong path: the script, an imported module or a file it opened is missing.
  '\\b(?:MODULE_NOT_FOUND|ERR_MODULE_NOT_FOUND|ENOENT)\\b',
  // node --test names a test file that does not exist.
  "^Could not find '",
  '^(?:FATAL ERROR|Segmentation fault|Aborted|Killed)\\b',
].join('|'), 'm');

function nodeRun(words) {
  let rest = words;
  if (rest[0] === 'timeout') {
    rest = rest.slice(1);
    for (;;) {
      if (/^-[ks]$/.test(rest[0] ?? '') && rest.length > 2) rest = rest.slice(2);
      else if (/^--(?:kill-after|signal)=[A-Za-z0-9.]+$|^--(?:foreground|preserve-status)$/.test(rest[0] ?? '')) rest = rest.slice(1);
      else break;
    }
    if (!/^\d+(?:\.\d+)?[smhd]?$/.test(rest[0] ?? '')) return false;
    rest = rest.slice(1);
  }
  if (rest[0] !== 'node') return false;
  const args = rest.slice(1);
  let check = false;
  let test = false;
  let i = 0;
  for (; i < args.length && args[i].startsWith('-') && args[i] !== '-'; i++) {
    const arg = args[i];
    if (arg === '--') { i++; break; }
    if (arg === '--check' || arg === '-c') check = true;
    else if (arg === '--test') test = true;
    else if (!/^--[a-z][a-z0-9-]*(?:=.*)?$/.test(arg) || NODE_REFUSED_OPTION.test(arg)) return false;
  }
  const operands = args.slice(i);
  if (check) return !test && operands.length === 1 && NODE_SCRIPT.test(operands[0]);
  if (test) return !operands.includes('-');
  return operands.length > 0 && NODE_SCRIPT.test(operands[0]);
}

export function nodeReportedFailure(command, result) {
  if (typeof command !== 'string' || command.length > 4096) return false;
  const steps = lookupSteps(command.trim().replace(LOOKUP_CD, ''), {mergeStderr: true});
  if (!steps || !steps.every(stages => stages.length === 1 && nodeRun(stages[0]))) return false;
  const details = result?.details;
  if (!details || typeof details !== 'object' || details.status !== 'completed' || details.exitCode !== 1 ||
      Object.hasOwn(details, 'sessionId')) return false;
  const output = (typeof details.aggregated === 'string' ? details.aggregated
    : (Array.isArray(result.content) ? result.content : [])
      .map(block => block?.type === 'text' && typeof block.text === 'string' ? block.text : '').join(''))
    .replace(/\(Command exited with code 1\)/, '');
  return /\S/.test(output) && !NODE_SETUP_ERROR.test(output);
}

// This deliberately does not classify general shell commands as read-only.
// Only a single quoted literal echo is provably unable to mutate the project.
export function isLiteralEcho(command) {
  return typeof command === 'string' &&
    /^\s*echo\s+(?:"[^"$`\\\r\n]*"|'[^'\r\n]*')\s*$/.test(command);
}
