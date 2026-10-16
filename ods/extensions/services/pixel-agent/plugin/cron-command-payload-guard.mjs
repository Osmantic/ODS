// Refuse agent cron calls that would create or edit a command job.
//
// OpenClaw 2026.6.33's agent cron tool rejects `payload.kind === "command"`
// before it normalizes the job, and normalization then trims and lowercases the
// kind. A kind such as "Command" therefore passed the check and became a command
// job, which runs in the gateway process outside the sandbox and exec policy
// (GHSA-8xxh-v4vc-qvm4, fixed upstream in 2026.7.1). Until the pinned runtime
// moves past that release, refuse any agent cron add or update that carries a
// kind normalizing to "command", compared exactly as the normalizer compares it
// (trim, then lowercase). Schedule kinds are not payload kinds and are ignored.
// Command jobs the owner creates through the CLI or Gateway API are unaffected.

const CRON_TOOL_NAMES = Object.freeze(new Set(["cron", "openclaw:core:cron"]));
const WRAPPER_TOOL_NAME = "tool_call";

export const CRON_COMMAND_PAYLOAD_REASON =
  "Scheduled jobs created by the agent cannot run OS commands. Use an agent " +
  "turn or a system event; command jobs are reserved for the owner's CLI.";

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cronArguments(params, toolName) {
  if (!isPlainObject(params)) return undefined;
  if (CRON_TOOL_NAMES.has(toolName)) return params;
  if (
    toolName === WRAPPER_TOOL_NAME &&
    typeof params.id === "string" &&
    CRON_TOOL_NAMES.has(params.id)
  ) {
    return isPlainObject(params.args) ? params.args : undefined;
  }
  return undefined;
}

// Whether any `kind` outside a schedule normalizes to "command".
function carriesCommandKind(value, parentKey) {
  if (Array.isArray(value)) {
    return value.some((item) => carriesCommandKind(item, parentKey));
  }
  if (!isPlainObject(value)) return false;
  return Object.entries(value).some(([key, child]) =>
    key === "kind"
      ? parentKey !== "schedule" &&
        typeof child === "string" &&
        child.trim().toLowerCase() === "command"
      : carriesCommandKind(child, key),
  );
}

// Composition entry for the before_tool_call hook. An earlier block is kept as
// is; otherwise the effective parameters (an earlier hook's rewrite, or the
// call's own) are checked.
export function withCronCommandPayloadBlock(guardResult, event, context) {
  if (isPlainObject(guardResult) && guardResult.block === true) {
    return guardResult;
  }
  const toolName = context?.toolName ?? event?.toolName;
  const params =
    isPlainObject(guardResult) && guardResult.params !== undefined
      ? guardResult.params
      : event?.params;
  const args = cronArguments(params, toolName);
  if (!args) return guardResult;
  // Upstream dispatches on the exact action; check every spelling that could
  // still reach add or update.
  const action =
    typeof args.action === "string" ? args.action.trim().toLowerCase() : "";
  if (action !== "add" && action !== "update") return guardResult;
  if (!carriesCommandKind(args)) return guardResult;
  return { block: true, blockReason: CRON_COMMAND_PAYLOAD_REASON };
}
