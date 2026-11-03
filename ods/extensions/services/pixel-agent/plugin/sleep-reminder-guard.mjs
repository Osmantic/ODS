// Refuse exec commands that wait to do something later (issue #7794).
//
// Asked for a reminder, a model may run `(sleep 120 && echo "Stretch!") &` in the sandbox. That
// looks like it worked, but the command never reaches the owner: its output belongs to a turn
// that has ended, and the sandbox does not keep it running. Refuse any sleep of a minute or more,
// a sleep of 20 seconds or more beside a background `&`, and a sleep in a detached command, and
// point the model at pixel_ods_notify, which schedules a real reminder. Short waits, such as
// `npm start & sleep 2 && curl ...` while a server starts, still run.

const EXEC_TOOL_NAMES = Object.freeze(new Set(["exec", "openclaw:core:exec"]));
const WRAPPER_TOOL_NAME = "tool_call";
const LONG_SECONDS = 60;
const BACKGROUND_SECONDS = 20;
const UNIT_SECONDS = { "": 1, s: 1, m: 60, h: 3600, d: 86400 };
const SLEEP = /\bsleep\s+(\d+(?:\.\d+)?)([smhd]?)\b/g;
const BACKGROUND = /(^|[^&>])&(?![&>])/;  // A single &, not && or a redirection such as 2>&1.
const DETACHED = /\b(nohup|setsid|disown)\b|\bat\s+now\b/;

export const SLEEP_REMINDER_REASON =
  "This command waits to do something later, but nothing from this turn keeps running, so it " +
  "would never reach the owner. To remind the owner later, call pixel_ods_notify with message " +
  "and inMinutes (such as 10) or at (such as 17:30); Portal schedules it and it reaches their phone then.";

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function execCommand(params, toolName) {
  if (!isPlainObject(params)) return undefined;
  const args = EXEC_TOOL_NAMES.has(toolName) ? params
    : toolName === WRAPPER_TOOL_NAME && EXEC_TOOL_NAMES.has(params.id) && isPlainObject(params.args) ? params.args
      : undefined;
  return typeof args?.command === "string" ? args.command : undefined;
}

// Whether the command waits to act later: a long sleep, or a sleep left running in the background.
export function waitsToActLater(command) {
  const longest = Math.max(0, ...[...command.matchAll(SLEEP)].map(([, amount, unit]) => Number(amount) * UNIT_SECONDS[unit]));
  if (!longest) return false;
  return longest >= LONG_SECONDS || DETACHED.test(command) || (longest >= BACKGROUND_SECONDS && BACKGROUND.test(command));
}

// Composition entry for the before_tool_call hook, like the cron payload guard: an earlier
// block is kept, otherwise the effective parameters are checked.
export function withSleepReminderBlock(guardResult, event, context) {
  if (isPlainObject(guardResult) && guardResult.block === true) return guardResult;
  const toolName = context?.toolName ?? event?.toolName;
  const params = isPlainObject(guardResult) && guardResult.params !== undefined ? guardResult.params : event?.params;
  const command = execCommand(params, toolName);
  if (command === undefined || !waitsToActLater(command)) return guardResult;
  return { block: true, blockReason: SLEEP_REMINDER_REASON };
}
