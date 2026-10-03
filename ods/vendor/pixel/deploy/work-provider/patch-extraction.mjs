import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { verifyDisposablePatch, WorkProviderPatchInputError } from "./patch-verifier.mjs";

// Shared, closed patch-submission contract for the K3 patch-proposal qualification case. A
// K3 patch proposal must be delivered as exactly one schema-bounded submit_patch tool call.
// The local lane retains its deterministic raw-diff request. Both the qualification runner and
// routed equivalence harness use this one extractor, then pass the exact extracted string to the
// unchanged disposable verifier and deterministic grader.

export const PATCH_SUBMISSION_TOOL_NAME = "submit_patch";
// Matches the disposable verifier's untrusted-patch byte ceiling. JSON Schema can bound only
// characters, so extraction independently enforces this as a UTF-8 byte limit too.
export const PATCH_SUBMISSION_MAX_PATCH_BYTES = 64 * 1024;

export const PATCH_SUBMISSION_TOOL = Object.freeze({
  name: PATCH_SUBMISSION_TOOL_NAME,
  description: "Submit exactly one unified diff that transforms the disposable fixture.mjs to the required result.",
  parameters: Object.freeze({
    type: "object",
    properties: Object.freeze({
      patch: Object.freeze({ type: "string", minLength: 1, maxLength: PATCH_SUBMISSION_MAX_PATCH_BYTES }),
    }),
    required: Object.freeze(["patch"]),
    additionalProperties: false,
  }),
  strict: true,
});

// Kimi currently accepts only auto/none/null tool_choice values. There is one available tool,
// the prompt requires it, and the extractor independently rejects any non-tool response.
export const PATCH_SUBMISSION_TOOL_CHOICE = "auto";

const PATCH_TASKS = new Set(["patch-proposal", "disposable-coding-patch"]);

// Returns the exact patch string or null for an ordinary semantic failure. The K3 path accepts
// one tool call with the exact name, no companion content, and one strict JSON string argument.
export function extractPatchProposal({ task, lane, assistant }) {
  if (!PATCH_TASKS.has(task)) throw new Error(`extractPatchProposal is not valid for task ${task}`);
  if (!assistant || typeof assistant !== "object") return null;
  const message = assistant.message;
  if (!message || typeof message !== "object") return null;
  const content = typeof message.content === "string" && message.content.length > 0 ? message.content : null;
  const calls = Array.isArray(message.toolCalls) ? message.toolCalls : [];

  if (task === "patch-proposal" && lane !== "local-only") {
    if (calls.length !== 1 || content !== null) return null;
    const call = calls[0];
    if (!call || typeof call !== "object" || call.name !== PATCH_SUBMISSION_TOOL_NAME) return null;
    return extractPatchArgument(call.arguments);
  }

  // Local patch-proposal and the generic disposable-coding-patch task remain raw-diff lanes.
  if (calls.length > 0) return null;
  return typeof content === "string" ? content : null;
}

// Invalid untrusted syntax/shape is a semantic failure. Trusted-input or verifier infrastructure
// failures retain their distinct base class and abort fail-closed.
export async function verifyPatchString({ patch, base, expected }) {
  try {
    return await verifyDisposablePatch({ patch, base, expected });
  } catch (error) {
    if (error instanceof WorkProviderPatchInputError) {
      return Object.freeze({ checkPassed: false, applied: false, exact: false, secondApplyRejected: false, reverseCheckPassed: false });
    }
    throw error;
  }
}

function extractPatchArgument(raw) {
  if (typeof raw !== "string" || raw.length < 1) return null;
  let parsed;
  try { parsed = parseStrictJson(raw, "submit_patch arguments"); }
  catch { return null; }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const keys = Object.keys(parsed);
  if (keys.length !== 1 || keys[0] !== "patch") return null;
  const patch = parsed.patch;
  if (typeof patch !== "string" || patch.length < 1 || patch.includes("\u0000")) return null;
  if (Buffer.byteLength(patch, "utf8") > PATCH_SUBMISSION_MAX_PATCH_BYTES) return null;
  return patch;
}
