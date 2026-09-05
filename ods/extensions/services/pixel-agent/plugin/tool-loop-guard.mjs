// Pixel per-run tool-loop guard.
//
// OpenClaw's built-in identical-call detector blocks a repeated tool call, but
// a model can keep asking for the blocked tool on later continuation passes.
// Bound the web-research and coding-repair portions of a Pixel response. A
// duplicate fetch gets one nonterminal pivot to targeted extraction, while
// repeated foreground or background verification failures share a run-wide
// budget. Terminal blocks get one result in which to produce a useful final
// answer; if the model ignores one, abort only that active agent run through
// OpenClaw's public harness runtime.

import { createHash, randomBytes } from "node:crypto";
import * as fs from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { isIP } from "node:net";

export const DEFAULT_WEB_TOOL_LIMITS = Object.freeze({
  search: 2,
  fetch: 2,
  total: 4,
  failedExecRetries: 3,
  failedVerificationAttempts: 6,
});

const MAX_COMPARE_SWAP_REPAIR_CHARS = 32_768;
const MAX_COMPARE_SWAP_REPAIRS_PER_PATH = 3;
const MAX_TRACKED_WORKSPACE_FILE_BYTES = 4 * 1024 * 1024;

export const WEB_BUDGET_EXHAUSTED_REASON =
  "Pixel's web-research budget is exhausted for this response. Do not call any tool again in this turn. Give the user a visible final answer now using the evidence already collected, clearly stating any uncertainty.";

export const WEB_LOOP_ABORT_REASON =
  "Pixel stopped this response because it requested another web tool after the bounded research budget was exhausted. Start a fresh message to continue with a narrower research question.";

export const WEB_FETCH_REPEAT_PIVOT_REASON =
  "Pixel already fetched this public page in this response. Do not repeat web_fetch or narrate a retry. If the needed detail was beyond the returned prefix, call pixel_ods_web_extract now with the same URL and a distinctive literal method or section name; otherwise answer from the evidence already returned.";

export const WEB_FETCH_TRUNCATED_PIVOT_REASON =
  "The fetched public page was truncated. Either answer from evidence already present or make exactly one pixel_ods_web_extract call now using that same page URL and a distinctive literal method or section name. Do not call or narrate any other tool; a different next tool will stop this response.";

export const WEB_FETCH_PUBLIC_ONLY_REASON =
  "Pixel blocked this fetch because web_fetch is restricted to public HTTP(S) hostnames and must not contact local, private, or raw-IP destinations. Do not call another tool in this turn; explain the boundary to the user.";

export const GITHUB_CANONICAL_SOURCE_PREFIX =
  "Pixel already has the owner's identified canonical public GitHub source:";

export const GITHUB_CANONICAL_FETCH_FAILED_REASON =
  "Pixel could not fetch the owner's identified canonical GitHub README. Do not call another tool in this turn or answer repository facts from memory; state that the requested source could not be verified.";

export const GITHUB_SOURCE_UNVERIFIED_DELIVERY_PREFIX =
  "Pixel could not verify the requested GitHub repository because its identified canonical README was not successfully fetched in this response. No repository claims are verified; please retry.";

export const EXEC_PRIVATE_NETWORK_REASON =
  "Pixel blocked this command because shell execution cannot be used to contact local, private, or raw-IP HTTP(S) destinations. Do not call another tool in this turn; explain the boundary to the user.";

export const PRIVATE_NETWORK_LOOP_ABORT_REASON =
  "Pixel stopped this response because it requested another tool after a private-network boundary was enforced. Start a fresh message with a safe public destination or an approved ODS status capability.";

export const PRIVATE_URL_REQUEST_REASON =
  "This request contains a private URL that Pixel cannot open from this chat. Do not call or substitute any tool, including ODS status or shell tools. Reply concisely that the private page was not accessed and ask the user to provide its content or use a separately approved private-access capability.";

export const CODING_RETRY_EXHAUSTED_REASON =
  "Pixel stopped a no-progress coding repair loop after its bounded failed-verification limit. Do not call another tool in this turn. Give the user a visible summary of the verified failure, the changes attempted, and the most useful next step.";

export const CODING_REPEAT_NO_PROGRESS_REASON =
  "That exact command already succeeded twice without a workspace mutation. Do not run it again. Perform the requested change with write, edit, or apply_patch, choose a materially different command, or give the owner a visible blocker.";

export const CODING_LOOP_ABORT_REASON =
  "Pixel stopped this response because it requested another coding tool after the repeated-command limit was reached. Start a fresh message to continue from the preserved workspace with a different approach.";

export const VISIBLE_REPLY_REQUIRES_FINAL_REASON =
  "Do not use a tool to deliver the reply and do not send a message to this same session. End the turn now with the requested text as the normal assistant response.";

export const EDIT_CREATE_REQUIRES_WRITE_REASON =
  "edit cannot create a new file because every edit replacement requires a non-empty oldText copied from existing content. Use the visible tool_call control now with id write and args containing the same path plus the exact newText as content. Do not retry edit.";

export const EDIT_CREATE_RETRY_EXHAUSTED_REASON =
  "Pixel blocked a repeated invalid attempt to create a file with edit. Do not call another tool in this turn. Tell the owner the file was not created and that a fresh retry must use write.";

export const EDIT_CREATE_LOOP_ABORT_REASON =
  "Pixel stopped this response because it kept retrying edit after the new-file write correction. The workspace is preserved; start a fresh message to retry with write.";

export const REPEATED_WRITE_REQUIRES_PATCH_REASON =
  "That file was already written successfully in this turn. Preserve it and use edit or apply_patch for the smallest relevant correction; do not rewrite the whole file.";

export const REPEATED_WRITE_RETRY_EXHAUSTED_REASON =
  "Pixel blocked a repeated full-file rewrite after directing a focused edit. Do not call another tool in this turn. The existing file is preserved; start a fresh message and continue with edit or apply_patch.";

export const FOCUSED_EDIT_REQUIRED_REASON =
  "This edit repeats a large existing file in oldText and newText. Preserve context and make only the smallest unique replacements with edit, or use a focused apply_patch; do not resend the whole file.";

export const FOCUSED_EDIT_RETRY_EXHAUSTED_REASON =
  "Pixel blocked a second oversized whole-file edit after directing focused replacements. Do not call another tool in this turn. The existing file is preserved; start a fresh message and continue with small edit blocks or apply_patch.";

export const NOOP_EDIT_REQUIRES_CHANGE_REASON =
  "This edit makes no change because every oldText and newText pair is identical. Re-read the exact verification error already present in this turn and make one meaningful focused replacement. If the test asserts behavior the owner did not request, correct that test expectation; otherwise repair the implementation. Do not rerun verification until a real edit succeeds.";

export const NOOP_EDIT_RETRY_EXHAUSTED_REASON =
  "Pixel blocked a repeated no-op edit after explaining that identical replacement text cannot repair the failure. Do not call another tool in this turn. The workspace is preserved; start a fresh message and make one evidence-based focused change.";

export const PENDING_EXEC_REQUIRES_POLL_REASON =
  "That exact command is already running. Do not call exec again or start a replacement process. Use the visible tool_call control now with id process and args containing action poll plus the exact sessionId returned by the running command; continue polling that same session until it reaches a terminal result.";

export const PENDING_EXEC_RETRY_EXHAUSTED_REASON =
  "Pixel blocked another attempt to restart a command that is still running. Do not call exec again in this turn. Poll only the exact existing process session to a terminal result, then report its real output.";

export const PENDING_EXEC_LOOP_ABORT_REASON =
  "Pixel stopped this response because it kept restarting an already-running command instead of polling its process session. The original process was preserved for cancellation cleanup; start a fresh message to continue safely.";

export const VERIFICATION_PENDING_DELIVERY_PREFIX =
  "Pixel stopped before the verification process reached a terminal result, so success is unverified. The workspace is preserved; ask Pixel to continue the run or inspect the process.";

export const VERIFICATION_FAILED_DELIVERY_PREFIX =
  "Pixel could not complete this task successfully because the latest verification check failed. The workspace is preserved; ask Pixel to continue with a focused repair.";

export const VERIFICATION_NOT_RUN_DELIVERY_PREFIX =
  "Pixel could not complete this task successfully because the owner-requested verification was not executed. The workspace is preserved; ask Pixel to continue and run the requested checks.";

export const VERIFICATION_COMMAND_NOT_AUDITABLE_REASON =
  "Pixel blocked this verification because a shell pipeline, redirect, or chained command can hide the test runner's exit status or truncate its evidence. Rerun the same test command directly, with no pipeline, redirection, chaining, or output filter, and inspect its complete output.";

export const REQUESTED_UNITTEST_REQUIRED_REASON =
  "The owner explicitly requested Python unittest coverage, so that attempted file was not written. Make exactly one tool_call now with id write, the same path, and a complete replacement under 1000 characters. Begin with the needed imports including unittest; use one unittest.TestCase class with only the requested test_* methods and assertions; finish with unittest.main(). No narration, comments, docstrings, extra cases, or print-only custom runner. Do not run verification before this test file is accepted.";

export const REQUESTED_UNITTEST_RETRY_REASON =
  "That replacement still was not unittest and was not written. Make another write call now, with no prose, using this exact outer shape: `import subprocess, sys, unittest`; `class Tests(unittest.TestCase):`; one `def test_name(self):` per owner-requested case; each method calls `subprocess.run(...)` and uses `self.assertEqual(...)`; finish with `if __name__ == '__main__': unittest.main()`. Keep the complete file under 1000 characters. If this shape is still invalid, one final literal scaffold remains before the turn stops.";

export const REQUESTED_UNITTEST_FINAL_RETRY_REASON =
  "Your previous replacement repeated the forbidden custom runner and was not written. One last attempt: discard every prior byte. Begin exactly with `import json, subprocess, sys, unittest`, then `class Tests(unittest.TestCase):`. Put each owner-requested case in its own `def test_name(self):`, call `result = subprocess.run([sys.executable, 'PROGRAM.py', 'INPUT'], capture_output=True, text=True)`, and assert its returncode, parsed stdout, or stderr with `self.assertEqual`. Finish with `if __name__ == '__main__': unittest.main()`. Replace PROGRAM.py and INPUT with the requested values. Do not define run_test, all_passed, print, comments, docstrings, or top-level subprocess code. Keep it under 1000 characters. Another invalid shape will stop this turn.";

export const REQUESTED_PARSED_JSON_REQUIRED_REASON =
  "The owner explicitly required parsed JSON verification, so that raw-text comparison test was not written. Write the same test file with `json.loads(result.stdout)` and compare the resulting Python object and numeric values; do not compare JSON whitespace or a literal expression such as `10/3` inside a string.";

export const RECURSIVE_DELETE_REQUIRES_OWNER_REASON =
  "Pixel blocked this recursive forced deletion because the owner's current request did not explicitly authorize deleting that workspace tree. Inspect the exact target and use focused file edits, or ask the owner for deletion approval. Do not substitute another destructive command.";

export const CANCELLABLE_EXEC_UNAVAILABLE_REASON =
  "Pixel could not establish the exact cancellation boundary for this command. Do not call another tool in this turn; explain that execution is temporarily unavailable.";

export const EXEC_ARGUMENTS_REQUIRE_COMMAND_REASON =
  "The exec command was not a non-empty string, so nothing was executed. Retry with command containing the shell text and workdir as a separate field, not an object inside command. For tool_call, use id exec and args containing those fields. Do not change the intended command or its authority.";

export const WORKSPACE_PREVIEW_REQUIRES_TOOL_REASON =
  "A server started inside Pixel's disposable sandbox is not reachable from the owner's browser. Do not start python http.server, npm dev, Vite, or another background server and do not claim any localhost port. Finish the static files, then call pixel_ods_workspace_preview with their one workspace-relative directory; share only its independently verified URL.";

export const WORKSPACE_PREVIEW_REQUIRES_FILES_REASON =
  "Pixel cannot publish this website yet because this response has not created or inspected an index.html in the requested workspace directory. Create the static site files first, then call pixel_ods_workspace_preview with that one relative directory.";

export const WORKSPACE_PREVIEW_REQUIRES_READBACK_REASON =
  "The workspace preview is already published and independently verified. The owner also asked to inspect every preview file, so use only read on the next unread file inside the verified preview directory. Do not curl the preview URL, start a server, run another check, or call an unrelated tool.";

export const WORKSPACE_PREVIEW_COMPLETE_REASON =
  "The workspace preview is already published and independently verified, and every owner-requested preview-file readback is complete. Do not call another tool or curl the preview URL; give the owner the concise final result now. ODS will attach the verified preview receipt and native side panel.";

export const WORKSPACE_VISUAL_CONTINUATION_REQUIRES_READ_REASON =
  "Pixel is updating the most recently verified visual artifact in this chat. Read the existing file inside that exact artifact directory before editing it; do not guess its contents, overwrite it with write, or create a replacement project.";

export const WORKSPACE_VISUAL_CONTINUATION_REQUIRES_EDIT_REASON =
  "Pixel has not yet completed the requested visual change. Make one focused edit to a file already read inside the bound artifact directory, then republish that same directory; do not publish an unchanged snapshot.";

export const WORKSPACE_VISUAL_CONTINUATION_SCOPE_REASON =
  "Keep this visual follow-up in the bound artifact directory. Read existing files before focused edits, use sandbox exec/process for inspection and verification, then republish the same directory with pixel_ods_workspace_preview. Do not blindly overwrite it, create a replacement project, or modify another directory.";

export const WORKSPACE_VISUAL_CONTINUATION_UNAVAILABLE_REASON =
  "Pixel could not find a readback-verified visual artifact bound to this chat, so it did not guess or modify unrelated workspace files. Ask the owner to build or republish the artifact first, or provide its exact workspace path explicitly.";

export const WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX =
  "Pixel preserved the website files in its workspace, but ODS did not verify a browser-accessible preview. No localhost URL is live or claimed; ask Pixel to continue and publish the static site through the workspace preview capability.";

export const WORKSPACE_PREVIEW_NOT_CREATED_DELIVERY_PREFIX =
  "Pixel did not create or verify the requested website files, so ODS did not publish a browser preview. No localhost URL is live or claimed; ask Pixel to retry the build.";

export const WORKSPACE_PREVIEW_PUBLISHED_DELIVERY_PREFIX =
  "Pixel published and independently read back the static website bytes:";

export const CLIENT_CANCELLED_REASON =
  "The owner cancelled this Pixel response. Do not call another tool or continue the task in this turn.";

export const ODS_TOOL_ROUTING_ABORT_REASON =
  "Pixel stopped this response because the required dedicated ODS projection tool was not used after one correction. Do not call another tool in this turn. State that the requested ODS facts were not verified and ask the owner to retry.";

export const ODS_TOOL_ROUTING_LOOP_ABORT_REASON =
  "Pixel stopped this response because it requested another tool after the ODS projection route was enforced. Start a fresh message to continue.";

export const EXACT_DOWNLOAD_REQUIRES_BROKER_REASON =
  "Pixel cannot turn web_fetch or another transformed page view into an exact-byte download. Call pixel_ops_download_stage now; ODS will bind it to the owner's exact HTTPS URL, destination basename, and expected digest. Wait for that exact job with pixel_ops_job_wait, then publish only its verified receipt with pixel_ods_download_promote. Do not create a substitute file.";

export const EXACT_DOWNLOAD_REQUIRES_WAIT_REASON =
  "Pixel submitted the exact-byte staged download but has not obtained its terminal receipt. Call pixel_ops_job_wait now; ODS will bind it to the submitted job. Do not read, recreate, or transfer the quarantine path.";

export const EXACT_DOWNLOAD_REQUIRES_PROMOTION_REASON =
  "Pixel verified the staged artifact in quarantine. Call pixel_ods_download_promote now; ODS will bind the job, source URL, digest, filename, and workspace-relative destination. Do not read the root-only quarantine path or create a substitute file.";

export const EXACT_DOWNLOAD_COMPLETE_REASON =
  "Pixel has already published and reverified the requested exact-byte artifact. Do not call another tool; give the owner the final path, byte count, SHA-256, source, and non-executable status.";

export const EXACT_DOWNLOAD_REQUEST_UNBOUND_REASON =
  "Pixel could not bind this exact-byte request to one unambiguous HTTPS source URL and one safe workspace-relative destination. Do not call another tool or create a substitute; ask the owner for one exact HTTPS URL and destination path.";

export const EXACT_DOWNLOAD_LOOP_ABORT_REASON =
  "Pixel stopped this response because it requested another tool after the exact-download provenance boundary was enforced. Start a fresh message with an approved staged-download capability or ask for a non-byte-exact page summary.";

export const EXACT_DOWNLOAD_UNAVAILABLE_DELIVERY_PREFIX =
  "Pixel did not submit the requested exact-byte download through a verified broker path. No downloadable artifact was created. web_fetch and page extraction return transformed, safety-marked evidence rather than origin bytes; retry with the policy-approved staged-download capability or provide a trusted local artifact and digest.";

export const EXACT_DOWNLOAD_UNVERIFIED_DELIVERY_PREFIX =
  "Pixel did not verify that the requested artifact was staged. A broker request may have been submitted, but exact-byte success requires a matching terminal succeeded Operations receipt with an absolute quarantine path, byte count, SHA-256 digest, HTTPS source, and non-executable artifact evidence. Continue or retry the broker job; do not treat a workspace substitute as the download.";

export const EXACT_DOWNLOAD_UNPUBLISHED_DELIVERY_PREFIX =
  "Pixel verified the requested bytes in Operations quarantine but did not publish them into the owner workspace. No workspace download was accepted; retry the verified create-only promotion path.";

export const EXACT_DOWNLOAD_PROMOTION_FAILED_DELIVERY_PREFIX =
  "Pixel could not publish the verified staged bytes into the owner workspace. No overwrite or substitute file was accepted.";

export const EXACT_DOWNLOAD_PUBLISHED_DELIVERY_PREFIX =
  "Pixel securely published the requested exact-byte download into the owner workspace:";

export const EXACT_DOWNLOAD_FAILED_DELIVERY_PREFIX =
  "Pixel's staged-download job reached a verified terminal failure. No artifact was created, and Pixel did not claim success.";

export const EXACT_DOWNLOAD_APPROVAL_DELIVERY_PREFIX =
  "Pixel staged the requested download as an immutable plan, but external approval is required. No artifact was created, and Pixel did not self-approve it.";

export const OPERATIONS_REQUIRES_BROKER_REASON =
  "The owner requested host or Operations evidence. Generic exec runs inside Pixel's sandbox and cannot establish host facts. For requested host.* observations, use the visible tool_call Tool Search control once with id pixel_ods_host_observe and args containing the exact requested actions; it returns the terminal broker receipt. Use pixel_ops_inventory, pixel_ops_run, and pixel_ops_job_wait only for other named Operations work. A status projection cannot substitute for required host work; use it only for an owner-requested ODS runtime facet after terminal host evidence.";

export const OPERATIONS_NOT_REQUESTED_REASON =
  "Pixel blocked this Operations tool because the owner's current request did not ask for host or ODS Operations work. Continue only the owner's original authorized task. For requested sandbox workspace work, use read, write, edit, apply_patch, exec, or process; do not submit an Operations job or broaden the task.";

export const UNREQUESTED_OPERATIONS_TERMINAL_REASON =
  "Pixel blocked another unrequested Operations attempt after a routing correction. Do not call another tool in this response or submit an Operations job. Give the owner a final answer explaining what was verified and what remains incomplete; existing work is preserved.";

export const UNREQUESTED_OPERATIONS_LOOP_ABORT_REASON =
  "Pixel stopped this response after another tool was requested following repeated unrequested Operations attempts and a terminal no-more-tools instruction. No additional tool was authorized; existing work is preserved.";

export const NETWORK_DISCOVERY_UNVERIFIED_TEXT =
  "LAN discovery and remote SSH availability were not verified by these local-host observations. No peer scan or remote login was performed.";

export const OPERATIONS_INVENTORY_REQUIRES_TOOL_REASON =
  "The owner asked what Operations capabilities are actually available. Call only pixel_ops_inventory with no arguments, then report its bounded current inventory. Do not submit a job, call status, search for tools, or exercise any capability.";

export const OPERATIONS_INVENTORY_COMPLETE_REASON =
  "Pixel already obtained the current bounded Operations capability inventory. Do not call another tool; report that inventory and its authority boundary now.";

export const OPERATIONS_INVENTORY_EVIDENCE_PREFIX =
  "Pixel verified the current Operations capability inventory through the external broker's bounded projection:";

export const OPERATIONS_INVENTORY_UNVERIFIED_DELIVERY_PREFIX =
  "Pixel did not obtain a structurally valid current Operations capability inventory. No capability availability or authority claim was accepted.";

export const OPERATIONS_HOST_COMMAND_REQUIRES_PROPOSAL_REASON =
  "The owner requested one protected command from the local ODS host, possibly including an explicit SSH operation to an owner-named destination. Call only pixel_ods_host_command_propose with the exact command. The ODS adapter fixes execution to ods-host and waits internally for the immutable approval plan or terminal broker receipt. Do not use generic exec, inventory, a named action, a workflow, another broker target, pixel_ops_shell_propose, pixel_ops_job_wait, or a second command proposal.";

export const OPERATIONS_HOST_COMMAND_COMPLETE_REASON =
  "Pixel already obtained the broker's terminal state for this protected host-command proposal. Do not call another tool; report the verified approval requirement or terminal outcome now.";

export const OPERATIONS_HOST_COMMAND_EVIDENCE_PREFIX =
  "Pixel verified this owner-approved ODS host command through a structurally matched terminal Operations Broker receipt:";

export const WORKSPACE_TOOL_SEARCH_COMPLETE_REASON =
  "Pixel already resolved the deferred workspace tools. Do not search again. Call tool_call now with the returned exact id, such as openclaw:core:exec, openclaw:core:write, openclaw:core:read, openclaw:core:edit, openclaw:core:apply_patch, or openclaw:core:process, and put that tool's normal arguments in args.";

export const WORKSPACE_UNREQUESTED_PROJECTION_REASON =
  "This is a sandbox workspace task, not an ODS status or application-list request. Do not call pixel_ods_status or pixel_ods_apps_list. Call tool_search once for write read edit apply_patch exec process, then use the returned exact workspace tool id to inspect or change only the owner-requested workspace path.";

export const OPERATIONS_REQUIRES_PROJECTIONS_REASON =
  "Pixel completed the requested host Operations jobs, but the owner also requested ODS status evidence that is still missing. Call each requested pixel_ods_status or pixel_ods_apps_list projection exactly once now. After every requested projection is verified, continue any explicitly requested workspace work.";

export const OPERATIONS_LOOP_ABORT_REASON =
  "Pixel stopped this response because it requested another non-Operations tool after the host Operations boundary was enforced. Start a fresh message to retry the named broker action.";

export const OPERATIONS_UNAVAILABLE_DELIVERY_PREFIX =
  "Pixel did not submit the requested host or Operations work through the isolated Operations Broker. No sandbox command was accepted as host evidence.";
export const OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE =
  "operations-unavailable-zero-submissions";

export const OPERATIONS_UNVERIFIED_DELIVERY_PREFIX =
  "Pixel submitted Operations work but did not obtain a matching terminal broker result in this response. Treat the host outcome as pending or unverified, not completed.";

export const OPERATIONS_MISSING_REQUIRED_DELIVERY_PREFIX =
  "Pixel completed its submitted Operations work but did not request every required host observation.";

export const OPERATIONS_WRONG_ACTION_REASON =
  "Pixel blocked an Operations submission that did not match the host facts requested. Use only the exact named ods-host actions listed in this correction, then wait for every submitted job to reach a terminal state.";

export const OPERATIONS_REQUIRES_WORKFLOW_REASON =
  "Pixel blocked a fragmented host inventory. Submit exactly one pixel_ops_workflow_submit containing every required ods-host action, then call pixel_ops_job_wait once for that workflow job. Do not submit separate pixel_ops_run jobs.";

export const OPERATIONS_EXTENSION_LIFECYCLE_SEQUENCE_REASON =
  "Pixel blocked an extension lifecycle shortcut. Submit exactly one ods.extensions.inspect action for the owner's extension ID and wait for its terminal receipt before submitting the requested lifecycle action. Do not combine lifecycle actions in a workflow or continue when inspection reports missing configuration.";

export const OPERATIONS_CONTINUATION_REQUIRES_STATUS_REASON =
  "Pixel blocked a new action while checking an existing immutable Operations plan. Query only the exact owner-supplied job with pixel_ops_job_get or pixel_ops_job_wait; do not resubmit, repeat, approve, or widen the operation.";

export const OPERATIONS_CONTINUATION_COMPLETE_REASON =
  "Pixel already obtained a structurally matched terminal receipt for the exact owner-supplied Operations job and plan hash. Do not call another tool; report only that verified outcome.";

export const OPERATIONS_CONTINUATION_UNVERIFIED_DELIVERY_PREFIX =
  "Pixel did not obtain a structurally matched terminal Operations receipt for the exact owner-supplied job and plan hash. The owner's approval or success statement was not accepted as host evidence.";

export const OPERATIONS_HOST_EVIDENCE_PREFIX =
  "Pixel verified these ODS host facts through structurally matched terminal Operations Broker receipts:";

export const OPERATIONS_ODS_APPS_UNAVAILABLE_TEXT =
  "ODS containers: a current sanitized ODS application projection was not obtained. Host Operations facts above remain verified, but Pixel cannot claim a container inventory from them.";

export const OPERATIONS_ODS_STATUS_UNAVAILABLE_TEXT =
  "ODS runtime status: a current sanitized ODS status projection was not obtained. Host Operations facts above remain verified, but Pixel cannot claim the active model, context, version, or Pixel availability from them.";

export const OPERATIONS_TRUSTED_CONTINUATION_PREFIX =
  "[ODS Pixel trusted continuation]";

export const OPERATIONS_EXTENSION_CATALOG_EVIDENCE_PREFIX =
  "Pixel verified this ODS extension catalog result through a structurally matched terminal Operations Broker receipt:";

export const OPERATIONS_EXTENSION_INVENTORY_EVIDENCE_PREFIX =
  "Pixel verified this live ODS extension inventory through a structurally matched terminal Operations Broker receipt:";

export const OPERATIONS_EXTENSION_LIFECYCLE_EVIDENCE_PREFIX =
  "Pixel verified this ODS extension lifecycle result through structurally matched Operations Broker receipts:";

const WEB_TOOLS = new Set(["web_search", "web_fetch", "pixel_ods_web_extract"]);
const CODING_TOOLS = new Set(["exec", "write", "edit", "apply_patch"]);
const WORKSPACE_MUTATION_TOOLS = new Set(["write", "edit", "apply_patch"]);
const FILE_PATH_TOOLS = new Set(["read", "write", "edit"]);
const WORKSPACE_CONTINUATION_TOOLS = new Set([
  "read", "write", "edit", "apply_patch", "exec", "process",
  "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
]);
const WORKSPACE_TOOL_SEARCH_QUERY = "write read edit apply_patch exec process";
const WORKSPACE_INSPECTION_COMPLETE_REASON =
  "The workspace inspection already completed and returned the directory, kernel, and listing; do not search, list, read the directory, or poll again. Continue the owner's requested task now. If the owner requested new files, call tool_call with id openclaw:core:write and args containing the first workspace-relative path and its full content. Do not call exec or process before that write.";
const FAILED_TEST_READ_REPAIR_REASON =
  "The verification command failed. Preserve the owner's explicit behavior contract: correct a test only when its expectation contradicts the owner; otherwise repair the implementation, and never weaken an assertion merely to match broken output. A blank label such as `Invalid integer:` is not a helpful empty-input message. Do not reread a file you just authored or run another diagnostic when the failure already contains actual and expected evidence. Apply one focused edit to the file implicated by the failure (test or implementation), then rerun the same verification command. If that evidence is insufficient, give the owner a visible blocker instead of repeating reads or tests.";
const EXACT_DOWNLOAD_BROKER_TOOLS = new Set([
  "pixel_ops_download_stage",
  "pixel_ops_job_get",
  "pixel_ops_job_wait",
  "pixel_ods_download_promote",
]);
const OPERATIONS_TOOLS = new Set([
  "pixel_ods_host_observe",
  "pixel_ods_host_command_propose",
  "pixel_ops_inventory",
  "pixel_ops_run",
  "pixel_ops_workflow_submit",
  "pixel_ops_download_stage",
  "pixel_ops_artifact_transfer",
  "pixel_ops_shell_propose",
  "pixel_ops_job_get",
  "pixel_ops_job_wait",
  "pixel_ops_job_events",
  "pixel_ops_job_cancel",
]);
const OPERATIONS_SUBMISSION_TOOLS = new Set([
  "pixel_ops_run",
  "pixel_ops_workflow_submit",
  "pixel_ops_download_stage",
  "pixel_ops_artifact_transfer",
  "pixel_ops_shell_propose",
]);
const SYNCHRONOUS_HOST_OBSERVE_TOOL = "pixel_ods_host_observe";
const SYNCHRONOUS_HOST_COMMAND_TOOL = "pixel_ods_host_command_propose";
const EVIDENCE_REPORT_TOOL = "pixel_ods_evidence_report";
const EVIDENCE_READBACK_TOOL = "pixel_ods_evidence_readback";
const WORKSPACE_PREVIEW_TOOL = "pixel_ods_workspace_preview";
const MAX_TRACKED_RUNS = 256;
const MAX_PENDING_EXEC_SESSIONS = 64;
const ODS_OPENAI_USER = /^ods-[0-9a-f]{64}$/;
const EXEC_CONTROL_WRAPPER = "/run/pixel-ods-control/cancellable-exec.sh";
const ARTIFACT_DRAFT_PREFIX =
  /^\s*(?:please\s+)?(?:build|write|draft|document|compose|create|edit|update|refactor|implement|generate)\b/i;
const ARTIFACT_NOUN =
  /\b(?:app(?:lication)?|code|config(?:uration)?|documentation|example|file|fixture|page|project|readme|script|site|snippet|test|web(?:site|page)?|workspace)\b/i;
const FOLLOWUP_PRIVATE_ACCESS =
  /\b(?:and\s+)?then\s+(?:access|browse|call|check|connect|download|fetch|inspect|open|query|read|request|retrieve|summari[sz]e|test|visit)\b/i;

function execMarkerId(runId) {
  if (typeof runId !== "string" || !runId) throw new Error("invalid Pixel run id");
  return createHash("sha256").update(runId, "utf8").digest("hex");
}

export function createExecCancellationControl({
  root = path.join(homedir(), ".openclaw", ".ods-exec-control"),
} = {}) {
  const resolvedRoot = path.resolve(root);

  function assertRoot() {
    const owner = typeof process.getuid === "function" ? process.getuid() : undefined;
    const rootInfo = fs.lstatSync(resolvedRoot);
    const wrapperInfo = fs.lstatSync(path.join(resolvedRoot, "cancellable-exec.sh"));
    if (
      !rootInfo.isDirectory() ||
      rootInfo.isSymbolicLink() ||
      (rootInfo.mode & 0o777) !== 0o700 ||
      (owner !== undefined && rootInfo.uid !== owner) ||
      !wrapperInfo.isFile() ||
      wrapperInfo.isSymbolicLink() ||
      wrapperInfo.nlink !== 1 ||
      (wrapperInfo.mode & 0o777) !== 0o500 ||
      (owner !== undefined && wrapperInfo.uid !== owner)
    ) {
      throw new Error("unsafe Pixel execution control root");
    }
  }

  function markerPath(runId) {
    return path.join(resolvedRoot, `${execMarkerId(runId)}.cancel`);
  }

  return {
    prepare(runId, command) {
      if (typeof command !== "string" || !command.trim() || command.includes("\0")) {
        throw new Error("invalid Pixel exec command");
      }
      assertRoot();
      try {
        fs.unlinkSync(markerPath(runId));
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      }
      const encoded = Buffer.from(command, "utf8").toString("base64");
      return `${EXEC_CONTROL_WRAPPER} ${execMarkerId(runId)} ${encoded}`;
    },

    signal(runId) {
      assertRoot();
      const target = markerPath(runId);
      const temporary = path.join(
        resolvedRoot,
        `.${execMarkerId(runId)}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`
      );
      try {
        const fd = fs.openSync(temporary, "wx", 0o600);
        fs.closeSync(fd);
        fs.renameSync(temporary, target);
        return true;
      } finally {
        try {
          fs.unlinkSync(temporary);
        } catch (error) {
          if (error?.code !== "ENOENT") throw error;
        }
      }
    },

    clear(runId) {
      assertRoot();
      try {
        fs.unlinkSync(markerPath(runId));
        return true;
      } catch (error) {
        if (error?.code === "ENOENT") return false;
        throw error;
      }
    },
  };
}

function validLimit(value, fallback) {
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function normalizedLimits(limits = {}) {
  return {
    search: validLimit(limits.search, DEFAULT_WEB_TOOL_LIMITS.search),
    fetch: validLimit(limits.fetch, DEFAULT_WEB_TOOL_LIMITS.fetch),
    total: validLimit(limits.total, DEFAULT_WEB_TOOL_LIMITS.total),
    failedExecRetries: validLimit(
      limits.failedExecRetries,
      DEFAULT_WEB_TOOL_LIMITS.failedExecRetries
    ),
    failedVerificationAttempts: validLimit(
      limits.failedVerificationAttempts,
      DEFAULT_WEB_TOOL_LIMITS.failedVerificationAttempts
    ),
  };
}

function normalizeWorkspaceFilePath(value) {
  if (value === "/workspace" || value === "workspace") return ".";
  if (typeof value === "string" && value.startsWith("/workspace/")) {
    return value.slice("/workspace/".length);
  }
  if (typeof value === "string" && value.startsWith("workspace/")) {
    return value.slice("workspace/".length);
  }
  return value;
}

function stripTrailingToolEnvelopeLeak(value) {
  if (typeof value !== "string") return value;
  const sanitized = value.replace(
    /\r?\n?(?:<\/parameter>[ \t]*)+<\/function>(?:[ \t]+[A-Za-z0-9][A-Za-z0-9._-]{0,127})?[ \t]*$/,
    ""
  );
  return sanitized.trim().length > 0 ? sanitized : value;
}

function completeRequestedUnittestImports(value, state, requestedPath) {
  const workspaceTestPath =
    typeof requestedPath === "string" &&
    typeof state?.workspaceTaskDirectory === "string" &&
    requestedPath.startsWith(`${state.workspaceTaskDirectory}/`) &&
    /^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(
      requestedPath.split("/").at(-1)
    );
  const originallyRequestedTestPath = state?.workspaceRequestedFiles?.some((file) =>
    requestedPath === `${state.workspaceTaskDirectory}/${file}` &&
    /^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(file)
  );
  if (
    typeof value !== "string" ||
    !workspaceTestPath ||
    !(originallyRequestedTestPath || state?.workspacePythonUnittestRequested)
  ) {
    return value;
  }
  const imports = [];
  if (
    /\bunittest\s*\./.test(value) &&
    !/^\s*(?:import\s+unittest\b|from\s+unittest\s+import\b)/m.test(value)
  ) {
    imports.push("import unittest");
  }
  if (
    /\bjson\.loads\s*\(/.test(value) &&
    !/^\s*(?:import\s+json\b|from\s+json\s+import\b)/m.test(value)
  ) {
    imports.push("import json");
  }
  for (const file of state.workspaceRequestedFiles) {
    if (
      /^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(file) ||
      !file.endsWith(".py")
    ) continue;
    const stem = file.slice(0, -3);
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(stem)) continue;
    const escaped = stem.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (
      new RegExp(`\\b${escaped}\\s*\\(`).test(value) &&
      !new RegExp(
        `^\\s*(?:import\\s+${escaped}\\b|from\\s+${escaped}\\s+import\\b)`,
        "m"
      ).test(value)
    ) {
      imports.push(`from ${stem} import ${stem}`);
    }
  }
  if (imports.length === 0) return value;
  const prefix = `${imports.join("\n")}\n\n`;
  const shebang = value.match(/^(#![^\r\n]+\r?\n)/);
  return shebang
    ? `${shebang[1]}${prefix}${value.slice(shebang[1].length)}`
    : `${prefix}${value}`;
}

function hasRequestedUnittestStructure(value) {
  if (typeof value !== "string") return false;
  return (
    /^\s*(?:import\s+unittest\b|from\s+unittest\s+import\b)/m.test(value) &&
    /class\s+[A-Za-z_][A-Za-z0-9_]*\s*\(\s*(?:unittest\.)?TestCase\s*\)\s*:/m.test(value) &&
    /\bdef\s+test_[A-Za-z0-9_]*\s*\(/m.test(value)
  );
}

function requestedUnittestFinalRetryReason(state) {
  const program = state?.workspaceRequestedFiles?.find((file) =>
    /^[A-Za-z_][A-Za-z0-9._-]*\.py$/i.test(file) &&
    !/^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(file)
  );
  return program
    ? REQUESTED_UNITTEST_FINAL_RETRY_REASON.replaceAll("PROGRAM.py", program)
    : REQUESTED_UNITTEST_FINAL_RETRY_REASON;
}

function normalizeExecWorkdir(value) {
  if (value === "/workspace" || value === "workspace" || value === ".") return ".";
  if (typeof value === "string" && value.startsWith("workspace/")) {
    return `/${value}`;
  }
  if (
    typeof value === "string" &&
    /^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/.test(value) &&
    !value.split("/").includes("..")
  ) {
    return `/workspace/${value}`;
  }
  return value;
}

function normalizeApplyPatchInput(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return undefined;
  if (typeof params.input === "string") return undefined;
  if (typeof params.path !== "string" || typeof params.patch !== "string") return undefined;
  if (!Object.keys(params).every((key) => key === "path" || key === "patch")) {
    return undefined;
  }
  const relativePath = normalizeWorkspaceFilePath(params.path);
  if (
    typeof relativePath !== "string" ||
    !relativePath ||
    relativePath === "." ||
    relativePath.startsWith("/") ||
    relativePath.includes("\\") ||
    relativePath.split("/").some(
      (component) =>
        !component ||
        component === "." ||
        component === ".." ||
        !/^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$/.test(component)
    )
  ) {
    return undefined;
  }
  let hunks = params.patch.replace(/\r\n?/g, "\n").trim();
  if (hunks.startsWith("*** Begin Patch\n") && hunks.endsWith("\n*** End Patch")) {
    return { input: hunks };
  }
  const unifiedHeader = hunks.match(/^--- [^\n]+\n\+\+\+ [^\n]+\n([\s\S]+)$/);
  if (unifiedHeader) hunks = unifiedHeader[1].trim();
  if (!/^@@(?: |\n)/.test(hunks)) return undefined;
  return {
    input:
      `*** Begin Patch\n*** Update File: ${relativePath}\n` +
      `${hunks}\n*** End Patch`,
  };
}

function normalizeWorkspaceParams(toolName, params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return undefined;
  const updated = { ...params };
  let changed = false;
  // Tool Search is the model-facing transport for core workspace tools. Some
  // smaller models occasionally append a single XML-like closing character to
  // an otherwise exact catalog id (for example `exec>`). Letting that typo hit
  // Tool Search is worse than one failed call: its missing-tool fuse can then
  // reject the corrected id for the rest of the turn, leaving a half-written
  // workspace and encouraging an unbounded retry loop. Repair only this narrow,
  // unambiguous suffix on the five owner-workspace tools; never fuzzy-match an
  // Operations, network, or third-party capability name.
  if (
    toolName === "tool_call" &&
    typeof params.id === "string" &&
    /^(?:read|write|edit|exec|process)>$/.test(params.id)
  ) {
    updated.id = params.id.slice(0, -1);
    changed = true;
  }
  const nestedCoreToolName =
    toolName === "tool_call" && typeof updated.id === "string"
      ? updated.id.startsWith("openclaw:core:")
        ? updated.id.slice("openclaw:core:".length)
        : updated.id
      : undefined;
  if (
    nestedCoreToolName &&
    ["read", "write", "edit", "apply_patch", "exec", "process"].includes(nestedCoreToolName) &&
    params.args &&
    typeof params.args === "object" &&
    !Array.isArray(params.args)
  ) {
    const nestedArgs = normalizeWorkspaceParams(nestedCoreToolName, params.args);
    if (nestedArgs) {
      updated.args = nestedArgs;
      changed = true;
    }
  }
  // Tool Search exposes the OpenClaw exec catalog to heterogeneous models.
  // Some otherwise capable models use the common `cmd` spelling learned from
  // other agent harnesses. Normalize that unambiguous alias before the
  // cancellation wrapper and retry fingerprints inspect `command`; otherwise
  // execution fails closed before it can run and a model can churn forever by
  // varying only yieldMs.
  if (
    toolName === "exec" &&
    typeof params.command !== "string" &&
    typeof params.cmd === "string"
  ) {
    updated.command = params.cmd;
    delete updated.cmd;
    changed = true;
  }
  if (
    toolName === "exec" &&
    typeof updated.command !== "string" &&
    typeof params.shell === "string" &&
    (params.context === undefined || params.context === "fork") &&
    Object.keys(params).every((key) =>
      ["shell", "context", "workdir", "yieldMs", "timeout", "pty", "background"].includes(key)
    )
  ) {
    // `shell` is another common command-value spelling emitted by compact
    // OpenAI-compatible models. It is not an OpenClaw exec field, so adapt it
    // only when the rest of the envelope is already an exec control field.
    // The recovered command still traverses cancellation and safety policy.
    updated.command = params.shell;
    delete updated.shell;
    if (updated.context === "fork") delete updated.context;
    changed = true;
  }
  if (
    toolName === "exec" &&
    typeof updated.command !== "string" &&
    typeof params.script === "string" &&
    (params.context === undefined || params.context === "fork") &&
    Object.keys(params).every((key) =>
      ["script", "context", "workdir", "yieldMs", "timeout", "pty", "background"].includes(key)
    )
  ) {
    // A compact model can borrow the `script` + `context: fork` envelope from
    // another agent harness even after selecting OpenClaw's exact exec tool.
    // Recover only that observed, closed set of fields. OpenClaw already runs
    // this agent's commands in its isolated workspace, so the foreign `fork`
    // hint adds no execution property and is discarded. The command still
    // traverses the cancellation wrapper, private-network policy, destructive
    // operation checks, and retry accounting below.
    updated.command = params.script;
    delete updated.script;
    if (updated.context === "fork") delete updated.context;
    changed = true;
  }
  if (
    toolName === "exec" &&
    typeof updated.command === "string" &&
    updated.workdir === undefined
  ) {
    const leakedWorkdir = updated.command.match(
      /^([\s\S]+),\s*workdir=(["'])(\/workspace\/[A-Za-z0-9._/-]+)\2\s*$/
    );
    const path = leakedWorkdir?.[3];
    const components = typeof path === "string"
      ? path.slice("/workspace/".length).split("/")
      : [];
    if (
      leakedWorkdir &&
      components.length > 0 &&
      components.length <= 16 &&
      components.every(
        (component) =>
          !["", ".", ".."].includes(component) &&
          WORKSPACE_PATH_COMPONENT.test(component)
      )
    ) {
      // Some compact models serialize the separately documented workdir field
      // into the command string. Recover only one trailing, quoted, absolute
      // /workspace path; shell syntax and every other suffix remain untouched.
      updated.command = leakedWorkdir[1];
      updated.workdir = path;
      changed = true;
    }
  }
  if (FILE_PATH_TOOLS.has(toolName) && typeof params.path === "string") {
    const path = normalizeWorkspaceFilePath(params.path);
    if (path !== params.path) {
      updated.path = path;
      changed = true;
    }
  }
  if (
    toolName === "write" &&
    Object.keys(params).sort().join("\u0000") === ["path", "text"].sort().join("\u0000") &&
    typeof params.text === "string"
  ) {
    updated.content = params.text;
    delete updated.text;
    changed = true;
  }
  if (
    toolName === "edit" &&
    typeof params.path === "string" &&
    typeof params.oldText === "string" &&
    typeof params.newText === "string"
  ) {
    updated.edits = [{ oldText: params.oldText, newText: params.newText }];
    delete updated.oldText;
    delete updated.newText;
    changed = true;
  } else if (
    toolName === "edit" &&
    params.edits &&
    typeof params.edits === "object" &&
    !Array.isArray(params.edits) &&
    typeof params.edits.oldText === "string" &&
    typeof params.edits.newText === "string"
  ) {
    updated.edits = [{
      oldText: params.edits.oldText,
      newText: params.edits.newText,
    }];
    changed = true;
  }
  if (toolName === "apply_patch") {
    const patchInput = normalizeApplyPatchInput(params);
    if (patchInput) {
      return patchInput;
    }
  }
  if (toolName === "exec" && typeof params.workdir === "string") {
    const workdir = normalizeExecWorkdir(params.workdir);
    if (workdir === ".") {
      delete updated.workdir;
      changed = true;
    } else if (workdir !== params.workdir) {
      updated.workdir = workdir;
      changed = true;
    }
  }
  return changed ? updated : undefined;
}

function editReplacementPairs(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return [];
  if (Array.isArray(params.edits)) {
    return params.edits.flatMap((entry) =>
      entry &&
      typeof entry === "object" &&
      !Array.isArray(entry) &&
      typeof entry.oldText === "string" &&
      typeof entry.newText === "string"
        ? [{ oldText: entry.oldText, newText: entry.newText }]
        : []
    );
  }
  return typeof params.oldText === "string" && typeof params.newText === "string"
    ? [{ oldText: params.oldText, newText: params.newText }]
    : [];
}

function sharedLineRatio(left, right) {
  const leftLines = left.split(/\r?\n/);
  const rightLines = right.split(/\r?\n/);
  const counts = new Map();
  for (const line of leftLines) counts.set(line, (counts.get(line) ?? 0) + 1);
  let shared = 0;
  for (const line of rightLines) {
    const remaining = counts.get(line) ?? 0;
    if (remaining <= 0) continue;
    shared += 1;
    counts.set(line, remaining - 1);
  }
  return {
    ratio: shared / Math.max(leftLines.length, rightLines.length, 1),
    minimumLines: Math.min(leftLines.length, rightLines.length),
  };
}

function oversizedWholeFileEdit(params) {
  return editReplacementPairs(params).some(({ oldText, newText }) => {
    if (Math.min(oldText.length, newText.length) < 6000) return false;
    const overlap = sharedLineRatio(oldText, newText);
    return overlap.minimumLines >= 80 && overlap.ratio >= 0.45;
  });
}

function noOpEdit(params) {
  const pairs = editReplacementPairs(params);
  return pairs.length > 0 && pairs.every(({ oldText, newText }) => oldText === newText);
}

function execFingerprint(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return undefined;
  const command = params.command;
  if (typeof command !== "string" || !command.trim()) return undefined;
  const normalizedWorkdir = normalizeExecWorkdir(params.workdir);
  const workdir = normalizedWorkdir === "." ? "" : normalizedWorkdir;
  return JSON.stringify([command.trim(), typeof workdir === "string" ? workdir : ""]);
}

function verificationCommand(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return undefined;
  if (typeof params.command !== "string" || !params.command.trim()) return undefined;
  let command = params.command.trim();
  let commandWorkdir;
  const workspaceCd = command.match(
    /^cd\s+(?:"(\/workspace(?:\/[^"\r\n]*)?)"|'(\/workspace(?:\/[^'\r\n]*)?)'|(\/workspace(?:\/[A-Za-z0-9._/-]+)?))\s*&&\s*(.+)$/is
  );
  if (workspaceCd) {
    commandWorkdir = workspaceCd[1] ?? workspaceCd[2] ?? workspaceCd[3];
    command = workspaceCd[4];
  }
  const withoutStderrMerge = command.replace(/\s+2>&1\s*$/i, "").trim();
  if (
    !/^(?:python(?:3(?:\.\d+)?)?\s+-m\s+(?:unittest|pytest)\b|python(?:3(?:\.\d+)?)?\s+(?:(?:-B|-u)\s+)*(?:\.\/)?(?:[A-Za-z0-9._-]+\/)*(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py\b|pytest\b|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b|go\s+test\b|cargo\s+test\b|dotnet\s+test\b|mvn(?:w)?\s+test\b|gradle(?:w)?\s+test\b)/i.test(withoutStderrMerge)
  ) {
    return undefined;
  }
  return { command, commandWorkdir, withoutStderrMerge };
}

function verificationCommandIsAuditable(params) {
  const parsed = verificationCommand(params);
  if (!parsed) return true;
  return !/(?:\r|\n|[|;<>`]|&&|\|\||\$\()/.test(parsed.withoutStderrMerge);
}

function verificationExecFingerprint(params) {
  const parsed = verificationCommand(params);
  if (!parsed || !verificationCommandIsAuditable(params)) return undefined;
  const command = parsed.command
    .replace(/\s+2>&1\s*$/i, "")
    .replace(/\s+/g, " ");
  const normalizedWorkdir = normalizeExecWorkdir(params.workdir ?? parsed.commandWorkdir);
  const workdir = normalizedWorkdir === "." ? "" : normalizedWorkdir;
  return JSON.stringify([command, typeof workdir === "string" ? workdir : ""]);
}

function canonicalRequestedUnittestParams(params, state) {
  const parsed = verificationCommand(params);
  if (
    !parsed ||
    !verificationCommandIsAuditable(params) ||
    typeof state?.workspaceTaskDirectory !== "string" ||
    !/^[A-Za-z0-9._/-]+$/.test(state.workspaceTaskDirectory)
  ) {
    return undefined;
  }
  const requestedTests = state.workspaceRequestedFiles.filter((file) =>
    /^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(file)
  );
  if (requestedTests.length !== 1) return undefined;
  const testFile = requestedTests[0];
  const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  let command = parsed.command;
  for (const path of [
    `/workspace/${state.workspaceTaskDirectory}/${testFile}`,
    `${state.workspaceTaskDirectory}/${testFile}`,
  ]) {
    command = command.replace(
      new RegExp(`(^|\\s)(["']?)${escapeRegExp(path)}\\2(?=\\s|$)`, "g"),
      `$1${testFile}`
    );
  }
  const directScript = command.match(
    new RegExp(
      `^(python(?:3(?:\\.\\d+)?)?)(?:\\s+(?:-B|-u))*\\s+(?:\\./)?${escapeRegExp(testFile)}(?:\\s+-v)?\\s*$`,
      "i"
    )
  );
  if (directScript) {
    // A directly executed test module can implement its own ad-hoc runner and
    // still exit zero after printing failures. For the one Python test file
    // explicitly named by the owner and bound to this workspace task, run the
    // standard unittest loader instead. A file with no discoverable tests then
    // produces an auditable `Ran 0 tests` receipt rather than false success.
    command = `${directScript[1]} -m unittest -v ${testFile}`;
  } else if (!/^python(?:3(?:\.\d+)?)?\s+-m\s+unittest\b/i.test(command)) {
    return undefined;
  }
  const canonical = {
    ...params,
    command,
    workdir: `/workspace/${state.workspaceTaskDirectory}`,
  };
  if (!verificationCommand(canonical) || !verificationCommandIsAuditable(canonical)) {
    return undefined;
  }
  if (canonical.command === params.command && canonical.workdir === params.workdir) {
    return undefined;
  }
  return canonical;
}

function verificationFingerprintIsPythonUnittest(fingerprint) {
  if (typeof fingerprint !== "string" || !fingerprint) return false;
  try {
    const parsed = JSON.parse(fingerprint);
    return (
      Array.isArray(parsed) &&
      typeof parsed[0] === "string" &&
      /^(?:python(?:3(?:\.\d+)?)?\s+-m\s+unittest\b|python(?:3(?:\.\d+)?)?\s+(?:(?:-B|-u)\s+)*(?:\.\/)?(?:[A-Za-z0-9._-]+\/)*(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py\b)/i.test(parsed[0])
    );
  } catch {
    return false;
  }
}

function execResultHasNonCleanUnittestOutcome(event) {
  const result = event?.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return false;
  const values = [
    result?.details?.aggregated,
    result?.details?.stdout,
    result?.details?.stderr,
    ...(Array.isArray(result.content)
      ? result.content.map((item) => item?.type === "text" ? item.text : undefined)
      : []),
  ];
  return values.some(
    (value) =>
      typeof value === "string" &&
      (
        /\bexpected failures?\s*=\s*[1-9][0-9]*\b/i.test(value) ||
        /\bunexpected successes?\s*=\s*[1-9][0-9]*\b/i.test(value) ||
        /\.\.\.\s+expected failure\b/i.test(value) ||
        /\.\.\.\s+unexpected success\b/i.test(value) ||
        /(?:^|\n)\s*(?:FAIL|ERROR)(?::|\s|\()/i.test(value) ||
        /\bAssertionError\b/i.test(value) ||
        /(?:^|\n)\s*FAILED\s*\(/i.test(value) ||
        /\bRan\s+0\s+tests?\b/i.test(value) ||
        /\bNO\s+TESTS?\s+RAN\b/i.test(value)
      )
  );
}

function execFailed(event) {
  if (typeof event?.error === "string" && event.error) return true;
  const result = event?.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return false;
  if (result.isError === true) return true;
  const exitCode = result?.details?.exitCode;
  return Number.isInteger(exitCode) && exitCode !== 0;
}

function runningExecSessionId(event) {
  const details = event?.result?.details;
  if (
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.status !== "running" ||
    typeof details.sessionId !== "string" ||
    !details.sessionId
  ) {
    return undefined;
  }
  return details.sessionId;
}

function completedProcessResult(event) {
  const action = event?.params?.action;
  if (action !== "poll" && action !== "log") return undefined;
  const details = event?.result?.details;
  if (
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.status !== "completed" ||
    typeof details.sessionId !== "string" ||
    !details.sessionId ||
    !Number.isInteger(details.exitCode)
  ) {
    return undefined;
  }
  return { sessionId: details.sessionId, failed: details.exitCode !== 0 };
}

function canonicalPendingProcessSessionId(params, pendingSessions) {
  if (
    !params ||
    typeof params !== "object" ||
    Array.isArray(params) ||
    typeof params.sessionId !== "string" ||
    !params.sessionId ||
    !(pendingSessions instanceof Map)
  ) {
    return undefined;
  }
  if (pendingSessions.has(params.sessionId)) return params.sessionId;
  // Small local models sometimes combine OpenClaw's human-facing output
  // ("session fast-breeze, pid 95242") into the invented identifier
  // "session-fast-breeze-95242". Correct only that exact shape and only when
  // the embedded label is already a pending execution created by this run.
  // This cannot widen session visibility or select an unrelated process.
  const alias = params.sessionId.match(/^session-(.+)-([1-9][0-9]*)$/);
  if (!alias || !pendingSessions.has(alias[1])) return undefined;
  return alias[1];
}

function toolCallFailed(event) {
  if (event?.error) return true;
  const result = event?.result;
  return Boolean(
    result &&
      typeof result === "object" &&
      !Array.isArray(result) &&
      result.isError === true
  );
}

const OPS_JOB_ID = /^ops-[0-9]{13}-[a-f0-9]{12}$/;
const OPS_ARTIFACT_FILENAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/;
const WORKSPACE_PATH_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SHA256 = /^[a-f0-9]{64}$/;
const OPS_NAME = /^[a-z][a-z0-9_.-]{1,127}$/;
const OPS_FIELD_NAME = /^[A-Za-z][A-Za-z0-9_.-]{0,127}$/;
const DOWNLOAD_PROMOTION_BOUNDARY =
  "Verified create-only promotion from Pixel Operations quarantine into the configured owner workspace; no arbitrary source, overwrite, execution, or path traversal authority.";

function boundedOperationsNames(value, maximum, pattern = OPS_NAME) {
  if (
    !Array.isArray(value) ||
    value.length > maximum ||
    value.some((item) => typeof item !== "string" || !pattern.test(item))
  ) {
    return undefined;
  }
  return [...new Set(value)];
}

function operationsInventoryProjection(event) {
  if (toolCallFailed(event)) return undefined;
  const details = event?.result?.details;
  if (
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.schemaVersion !== 2 ||
    typeof details.generatedAt !== "string" ||
    !Number.isFinite(Date.parse(details.generatedAt)) ||
    typeof details.policySha256 !== "string" ||
    !SHA256.test(details.policySha256) ||
    !details.authority ||
    typeof details.authority !== "object" ||
    Array.isArray(details.authority) ||
    typeof details.authority.defaultLevel !== "string" ||
    !["observe", "propose", "execute"].includes(details.authority.defaultLevel) ||
    typeof details.authority.paused !== "boolean"
  ) {
    return undefined;
  }
  const standingGrantIds = boundedOperationsNames(
    details.authority.standingGrantIds,
    64,
    OPS_FIELD_NAME
  );
  const activeLeaseIds = boundedOperationsNames(
    details.authority.activeLeaseIds,
    64,
    OPS_FIELD_NAME
  );
  if (!standingGrantIds || !activeLeaseIds) return undefined;
  if (!Array.isArray(details.targets) || details.targets.length > 64) return undefined;
  const targetIds = new Set();
  const targets = [];
  for (const target of details.targets) {
    if (
      !target ||
      typeof target !== "object" ||
      Array.isArray(target) ||
      typeof target.id !== "string" ||
      !OPS_NAME.test(target.id) ||
      targetIds.has(target.id) ||
      !["local", "ssh"].includes(target.backend)
    ) {
      return undefined;
    }
    const capabilities = boundedOperationsNames(target.capabilities, 64, OPS_FIELD_NAME);
    if (!capabilities) return undefined;
    targetIds.add(target.id);
    targets.push({ id: target.id, backend: target.backend, capabilities });
  }
  if (!Array.isArray(details.actions) || details.actions.length > 512) return undefined;
  const actionIds = new Set();
  const actions = [];
  for (const action of details.actions) {
    if (
      !action ||
      typeof action !== "object" ||
      Array.isArray(action) ||
      typeof action.id !== "string" ||
      !OPS_NAME.test(action.id) ||
      actionIds.has(action.id) ||
      !["read", "staging", "managed", "change"].includes(action.tier) ||
      !["observe", "stage", "manage", "change"].includes(action.effect) ||
      !["observe", "propose", "execute"].includes(action.defaultAuthority)
    ) {
      return undefined;
    }
    const actionTargets = boundedOperationsNames(action.targets, 64, /^[a-z*][a-z0-9_.*-]{0,127}$/);
    const parameters = boundedOperationsNames(action.parameters, 64, OPS_FIELD_NAME);
    if (!actionTargets || !parameters) return undefined;
    actionIds.add(action.id);
    actions.push({
      id: action.id,
      tier: action.tier,
      effect: action.effect,
      defaultAuthority: action.defaultAuthority,
      targets: actionTargets,
      parameters,
    });
  }
  return {
    generatedAt: new Date(details.generatedAt).toISOString(),
    policySha256: details.policySha256,
    authority: {
      defaultLevel: details.authority.defaultLevel,
      paused: details.authority.paused,
      standingGrantIds,
      activeLeaseIds,
    },
    targets,
    actions,
  };
}

function operationsInventoryEvidenceText(inventory) {
  if (!inventory) return undefined;
  const actionIds = new Set(inventory.actions.map(({ id }) => id));
  const targetLines = inventory.targets.map((target) =>
    `  - \`${target.id}\` (${target.backend}); capabilities: ${target.capabilities.length > 0
      ? target.capabilities.map((item) => `\`${item}\``).join(", ")
      : "none"}.`
  );
  const authorityGroups = ["observe", "propose", "execute"]
    .map((level) => ({
      level,
      ids: inventory.actions
        .filter((action) => action.defaultAuthority === level)
        .map((action) => `\`${action.id}\``),
    }))
    .filter(({ ids }) => ids.length > 0)
    .map(({ level, ids }) => `  - ${level}: ${ids.join(", ")}.`);
  const missing = [];
  if (!inventory.targets.some((target) => target.backend === "ssh")) {
    missing.push("no SSH-backed remote target");
  }
  for (const [label, pattern] of [
    ["interactive browser", /browser/i],
    ["email or messaging", /(?:email|mail|message)/i],
    ["scheduled or goal work", /(?:schedule|cron|goal)/i],
  ]) {
    if (![...actionIds].some((id) => pattern.test(id))) missing.push(`no ${label} action`);
  }
  return [
    OPERATIONS_INVENTORY_EVIDENCE_PREFIX,
    `- Generated: ${inventory.generatedAt}; policy SHA-256: \`${inventory.policySha256}\`.`,
    `- Authority: default \`${inventory.authority.defaultLevel}\`; paused ${inventory.authority.paused ? "yes" : "no"}; active leases ${inventory.authority.activeLeaseIds.length}.`,
    `- Standing grant IDs: ${inventory.authority.standingGrantIds.length > 0
      ? inventory.authority.standingGrantIds.map((id) => `\`${id}\``).join(", ")
      : "none"}.`,
    "- Enabled targets:",
    ...targetLines,
    "- Exact named actions by default authority:",
    ...authorityGroups,
    `- Not present in this Operations inventory: ${missing.join("; ")}.`,
    "- Boundary: this inventory is descriptive only. It grants no authority, and it does not enumerate separate sandbox/core tools.",
  ].join("\n");
}

function exactDownloadSubmission(event, requested) {
  if (toolCallFailed(event)) return undefined;
  const params = event?.params;
  const details = event?.result?.details;
  if (
    !params ||
    typeof params !== "object" ||
    Array.isArray(params) ||
    typeof params.url !== "string" ||
    params.url !== requested?.url ||
    typeof params.filename !== "string" ||
    params.filename !== requested?.filename ||
    !OPS_ARTIFACT_FILENAME.test(params.filename) ||
    (params.expectedSha256 !== undefined &&
      (typeof params.expectedSha256 !== "string" || !SHA256.test(params.expectedSha256))) ||
    params.expectedSha256 !== requested?.expectedSha256 ||
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.status !== "submitted" ||
    details.kind !== "download" ||
    typeof details.jobId !== "string" ||
    !OPS_JOB_ID.test(details.jobId)
  ) {
    return undefined;
  }
  let parsed;
  try {
    parsed = new URL(params.url);
  } catch {
    return undefined;
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.hash ||
    !parsed.hostname
  ) {
    return undefined;
  }
  return {
    jobId: details.jobId,
    url: params.url,
    safeSource: params.url.split("?", 1)[0],
    filename: params.filename,
    expectedSha256: params.expectedSha256,
    relativePath: requested.relativePath,
  };
}

function exactDownloadTerminalArtifact(event, submissions) {
  if (toolCallFailed(event) || !(submissions instanceof Map)) return undefined;
  const requestedJobId = event?.params?.jobId;
  const details = event?.result?.details;
  const submission = submissions.get(requestedJobId);
  if (
    typeof requestedJobId !== "string" ||
    !submission ||
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.jobId !== requestedJobId ||
    details.status !== "succeeded" ||
    details.waitTimedOut === true ||
    !Array.isArray(details.steps) ||
    details.steps.length !== 1
  ) {
    return undefined;
  }
  const step = details.steps[0];
  const artifact = step?.artifact;
  if (
    !step ||
    typeof step !== "object" ||
    Array.isArray(step) ||
    step.action !== "download.stage" ||
    step.target !== "broker" ||
    step.exitCode !== 0 ||
    !artifact ||
    typeof artifact !== "object" ||
    Array.isArray(artifact) ||
    typeof artifact.path !== "string" ||
    artifact.path !==
      `/var/lib/pixel-ops-broker/artifacts/${requestedJobId}/${submission.filename}` ||
    typeof artifact.filename !== "string" ||
    artifact.filename !== submission.filename ||
    !Number.isSafeInteger(artifact.bytes) ||
    artifact.bytes < 0 ||
    typeof artifact.sha256 !== "string" ||
    !SHA256.test(artifact.sha256) ||
    typeof artifact.source !== "string" ||
    !artifact.source.startsWith("https://") ||
    !Array.isArray(artifact.redirects) ||
    (artifact.redirects.length === 0 && artifact.source !== submission.safeSource) ||
    (artifact.redirects.length > 0 && artifact.redirects[0] !== submission.safeSource) ||
    artifact.redirects.some((source) =>
      typeof source !== "string" || !source.startsWith("https://")
    ) ||
    (submission.expectedSha256 !== undefined &&
      (artifact.sha256 !== submission.expectedSha256 ||
        artifact.expectedSha256Matched !== true)) ||
    artifact.executable !== false
  ) {
    return undefined;
  }
  return {
    ...artifact,
    jobId: requestedJobId,
    requestedSource: submission.url,
    relativePath: submission.relativePath,
  };
}

function exactDownloadPromotion(event, artifact) {
  if (toolCallFailed(event) || !artifact) return undefined;
  const params = event?.params;
  const details = event?.result?.details;
  if (
    !params ||
    typeof params !== "object" ||
    Array.isArray(params) ||
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.schemaVersion !== 1 ||
    details.kind !== "ods-pixel-download-promotion" ||
    details.status !== "succeeded" ||
    details.jobId !== artifact.jobId ||
    details.filename !== artifact.filename ||
    details.relativePath !== artifact.relativePath ||
    details.bytes !== artifact.bytes ||
    details.sha256 !== artifact.sha256 ||
    details.requestedSource !== artifact.requestedSource ||
    typeof details.source !== "string" ||
    !details.source.startsWith("https://") ||
    details.executable !== false ||
    details.overwritten !== false ||
    details.boundary !== DOWNLOAD_PROMOTION_BOUNDARY
  ) {
    return undefined;
  }
  return details;
}

function exactDownloadPublishedText(promotion) {
  if (!promotion) return undefined;
  return [
    EXACT_DOWNLOAD_PUBLISHED_DELIVERY_PREFIX,
    `- Workspace path: \`${promotion.relativePath}\`.`,
    `- Bytes: ${promotion.bytes}.`,
    `- SHA-256: \`${promotion.sha256}\`.`,
    `- Source: ${promotion.source}.`,
    "- Executable: no; overwrite: no.",
    `- Operations job: \`${promotion.jobId}\`.`,
  ].join("\n");
}

function exactDownloadTerminalOutcome(event, submissions) {
  if (toolCallFailed(event) || !(submissions instanceof Map)) return undefined;
  const requestedJobId = event?.params?.jobId;
  const details = event?.result?.details;
  if (
    typeof requestedJobId !== "string" ||
    !submissions.has(requestedJobId) ||
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.jobId !== requestedJobId ||
    details.waitTimedOut === true ||
    !["failed", "cancelled", "rejected", "awaiting-approval"].includes(details.status)
  ) {
    return undefined;
  }
  if (details.status === "awaiting-approval") {
    if (details.approvalRequired !== true || typeof details.planHash !== "string" || !SHA256.test(details.planHash)) {
      return undefined;
    }
    return { jobId: requestedJobId, status: details.status, planHash: details.planHash };
  }
  return { jobId: requestedJobId, status: details.status };
}

function exactDownloadTerminalText(outcome) {
  if (!outcome) return undefined;
  if (outcome.status === "awaiting-approval") {
    return `${EXACT_DOWNLOAD_APPROVAL_DELIVERY_PREFIX} Job: ${outcome.jobId}. Plan SHA-256: ${outcome.planHash}.`;
  }
  return `${EXACT_DOWNLOAD_FAILED_DELIVERY_PREFIX} Job: ${outcome.jobId}. Terminal status: ${outcome.status}.`;
}

function operationsSubmission(event, toolName) {
  if (!OPERATIONS_SUBMISSION_TOOLS.has(toolName) || toolCallFailed(event)) {
    return undefined;
  }
  const details = event?.result?.details;
  if (
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.status !== "submitted" ||
    typeof details.jobId !== "string" ||
    !OPS_JOB_ID.test(details.jobId)
  ) {
    return undefined;
  }
  const actions = [];
  if (toolName === "pixel_ops_run") {
    if (
      details.kind !== "action" ||
      typeof event?.params?.target !== "string" ||
      typeof event?.params?.action !== "string"
    ) {
      return undefined;
    }
    actions.push({
      target: event.params.target,
      action: event.params.action,
      parameters: event.params.parameters,
    });
  } else if (toolName === "pixel_ops_workflow_submit") {
    if (details.kind !== "workflow" || !Array.isArray(event?.params?.steps)) {
      return undefined;
    }
    for (const step of event.params.steps) {
      if (
        !step ||
        typeof step !== "object" ||
        Array.isArray(step) ||
        typeof step.target !== "string" ||
        typeof step.action !== "string"
      ) {
        return undefined;
      }
      actions.push({ target: step.target, action: step.action, parameters: step.parameters });
    }
  } else if (toolName === "pixel_ops_download_stage" && details.kind === "download") {
    actions.push({ target: "broker", action: "download.stage" });
  } else if (
    toolName === "pixel_ops_artifact_transfer" &&
    details.kind === "transfer" &&
    typeof event?.params?.target === "string"
  ) {
    actions.push({ target: event.params.target, action: "artifact.transfer" });
  } else if (
    toolName === "pixel_ops_shell_propose" &&
    details.kind === "shell" &&
    typeof event?.params?.target === "string"
  ) {
    actions.push({ target: event.params.target, action: "raw-shell" });
  } else {
    return undefined;
  }
  return { jobId: details.jobId, actions };
}

function operationsTerminalOutcome(event, submittedJobs) {
  if (toolCallFailed(event) || !(submittedJobs instanceof Map)) return undefined;
  const requestedJobId = event?.params?.jobId;
  const details = event?.result?.details;
  const submission = submittedJobs.get(requestedJobId);
  if (
    typeof requestedJobId !== "string" ||
    !submission ||
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.jobId !== requestedJobId ||
    details.waitTimedOut === true ||
    !["succeeded", "failed", "cancelled", "rejected", "awaiting-approval"].includes(
      details.status
    )
  ) {
    return undefined;
  }
  if (
    details.status === "awaiting-approval" &&
    (details.approvalRequired !== true ||
      typeof details.planHash !== "string" ||
      !SHA256.test(details.planHash))
  ) {
    return undefined;
  }
  if (details.status !== "succeeded") {
    return {
      jobId: requestedJobId,
      status: details.status,
      planHash: details.planHash,
      approvalRequired: details.approvalRequired,
      actions: submission.actions,
      steps: [],
    };
  }
  if (!Array.isArray(details.steps) || details.steps.length !== submission.actions.length) {
    return undefined;
  }
  const expected = submission.actions
    .map(({ target, action }) => `${target}\u0000${action}`)
    .sort();
  const observed = [];
  for (const step of details.steps) {
    if (
      !step ||
      typeof step !== "object" ||
      Array.isArray(step) ||
      typeof step.target !== "string" ||
      typeof step.action !== "string" ||
      step.exitCode !== 0 ||
      typeof step.stdout !== "string" ||
      typeof step.stderr !== "string" ||
      !step.outputTruncated ||
      typeof step.outputTruncated !== "object" ||
      step.outputTruncated.stdout !== false ||
      step.outputTruncated.stderr !== false ||
      !Array.isArray(step.riskSignals)
    ) {
      return undefined;
    }
    observed.push(`${step.target}\u0000${step.action}`);
  }
  observed.sort();
  if (expected.length !== observed.length || expected.some((value, index) => value !== observed[index])) {
    return undefined;
  }
  return {
    jobId: requestedJobId,
    status: details.status,
    planHash: details.planHash,
    approvalRequired: details.approvalRequired,
    actions: submission.actions,
    steps: details.steps,
  };
}

function exactRequiredHostActions(state) {
  if (
    !state?.operationsRequired ||
    state.operationsRequiredActions.size < 1 ||
    ![...state.operationsRequiredActions].every((action) => action.startsWith("host."))
  ) {
    return undefined;
  }
  return [...state.operationsRequiredActions];
}

function synchronousHostObservationOutcome(event, state) {
  if (toolCallFailed(event)) return undefined;
  const expectedActions = exactRequiredHostActions(state);
  const observedActions = event?.params?.actions;
  const expectedPeer = expectedActions?.includes("host.network-peer")
    ? state?.operationsNetworkPeer
    : undefined;
  const details = event?.result?.details;
  if (
    !expectedActions ||
    !Array.isArray(observedActions) ||
    observedActions.length !== expectedActions.length ||
    !expectedActions.every((action) => observedActions.includes(action)) ||
    (expectedActions.includes("host.network-peer") && !expectedPeer) ||
    (expectedPeer &&
      (event?.params?.peer !== expectedPeer.peer ||
        !Array.isArray(event?.params?.ports) ||
        event.params.ports.length !== expectedPeer.ports.length ||
        event.params.ports.some((port, index) => port !== expectedPeer.ports[index]))) ||
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    typeof details.jobId !== "string" ||
    !OPS_JOB_ID.test(details.jobId)
  ) {
    return undefined;
  }
  const submission = {
    jobId: details.jobId,
    actions: expectedActions.map((action) => ({
      target: "ods-host",
      action,
      ...(action === "host.network-peer" && expectedPeer
        ? { parameters: { peer: expectedPeer.peer, ports: expectedPeer.ports.join(",") } }
        : {}),
    })),
  };
  const outcome = operationsTerminalOutcome(
    { params: { jobId: details.jobId }, result: event.result },
    new Map([[details.jobId, submission]])
  );
  return outcome ? { submission, outcome } : undefined;
}

function replayTrackedEdit(content, pairs) {
  if (typeof content !== "string" || pairs.length === 0) return undefined;
  let updated = content;
  for (const { oldText, newText } of pairs) {
    if (!oldText || oldText === newText) return undefined;
    const position = updated.indexOf(oldText);
    if (
      position < 0 ||
      updated.indexOf(oldText, position + oldText.length) >= 0
    ) {
      return undefined;
    }
    updated =
      updated.slice(0, position) +
      newText +
      updated.slice(position + oldText.length);
    if (Buffer.byteLength(updated, "utf8") > MAX_TRACKED_WORKSPACE_FILE_BYTES) {
      return undefined;
    }
  }
  return updated;
}

function synchronousHostCommandOutcome(event, state) {
  if (toolCallFailed(event) || !state?.operationsHostCommandRequested) return undefined;
  const command = event?.params?.command;
  const details = event?.result?.details;
  if (
    typeof command !== "string" ||
    !command.trim() ||
    command.length > 16_384 ||
    Buffer.byteLength(command, "utf8") > 16_384 ||
    command.includes("\0") ||
    (typeof state.operationsExactHostCommand === "string" &&
      command !== state.operationsExactHostCommand) ||
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    typeof details.jobId !== "string" ||
    !OPS_JOB_ID.test(details.jobId)
  ) {
    return undefined;
  }
  const submission = {
    jobId: details.jobId,
    actions: [{ target: "ods-host", action: "raw-shell" }],
  };
  const outcome = operationsTerminalOutcome(
    { params: { jobId: details.jobId }, result: event.result },
    new Map([[details.jobId, submission]])
  );
  return { submission, outcome };
}

function synchronousHostOdsStatusProjection(event) {
  const value = event?.result?.details?.odsStatusProjection;
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return operationsOdsStatusProjection({
    result: {
      details: {
        projection: value,
        runtime: value.runtime,
      },
    },
  });
}

function cleanSingleLine(value, pattern, maximum) {
  if (typeof value !== "string") return undefined;
  const text = value.trim();
  if (!text || text.length > maximum || text.includes("\n") || text.includes("\r")) {
    return undefined;
  }
  return pattern.test(text) ? text : undefined;
}

function osPrettyName(value) {
  if (typeof value !== "string" || value.length > 16 * 1024 || value.includes("\0")) {
    return undefined;
  }
  const line = value.split(/\r?\n/).find((candidate) => candidate.startsWith("PRETTY_NAME="));
  if (!line) return undefined;
  let text = line.slice("PRETTY_NAME=".length).trim();
  if (
    text.length >= 2 &&
    ((text.startsWith('"') && text.endsWith('"')) ||
      (text.startsWith("'") && text.endsWith("'")))
  ) {
    text = text.slice(1, -1);
  }
  return cleanSingleLine(text, /^[A-Za-z0-9][A-Za-z0-9 .,_+()/:;~'&-]{0,255}$/, 256);
}

function safeHostLines(step, maximumLines = 2048, maximumBytes = 256 * 1024) {
  if (
    !step ||
    step.stderr.trim() ||
    step.riskSignals.length > 0 ||
    typeof step.stdout !== "string" ||
    step.stdout.length > maximumBytes ||
    /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(step.stdout)
  ) {
    return undefined;
  }
  const lines = step.stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length || lines.length > maximumLines || lines.some((line) => line.length > 2048)) {
    return undefined;
  }
  return lines;
}

function formatHostBytes(value) {
  if (!Number.isSafeInteger(value) || value < 0) return undefined;
  const gib = value / (1024 ** 3);
  return `${gib.toFixed(gib >= 10 ? 1 : 2)} GiB`;
}

function processEvidence(step) {
  const lines = safeHostLines(step);
  if (!lines) return undefined;
  const entries = lines.map((line) => {
    const match = line.match(/^([0-9]+)\s+([0-9]+)\s+([A-Za-z0-9_.+-]{1,64})\s+(\S{1,16})\s+([0-9.]+)\s+([0-9.]+)\s+([A-Za-z0-9_.+:@%()\/-]{1,128})$/);
    if (!match) return undefined;
    const cpu = Number(match[5]);
    const memory = Number(match[6]);
    if (!Number.isFinite(cpu) || !Number.isFinite(memory)) return undefined;
    return { pid: match[1], user: match[3], cpu, memory, command: match[7] };
  });
  if (entries.some((entry) => !entry)) return undefined;
  const renderProcess = (entry) =>
    `${entry.command} (pid ${entry.pid}, ${entry.cpu}% CPU, ${entry.memory}% memory)`;
  const topCpu = [...entries]
    .sort((left, right) => right.cpu - left.cpu || right.memory - left.memory)
    .slice(0, 3)
    .map(renderProcess);
  const topMemory = [...entries]
    .sort((left, right) => right.memory - left.memory || right.cpu - left.cpu)
    .slice(0, 3)
    .map(renderProcess);
  return `Processes: ${entries.length} visible; top 3 by CPU: ${topCpu.join("; ")}; ` +
    `top 3 by memory: ${topMemory.join("; ")}.`;
}

function serviceEvidence(step) {
  const lines = safeHostLines(step);
  if (!lines) return undefined;
  const entries = lines.map((line) => {
    const match = line.match(/^([A-Za-z0-9@_.:-]{1,256}\.service)\s+(\S{1,32})\s+(\S{1,32})\s+(\S{1,32})(?:\s+.*)?$/);
    return match ? { unit: match[1], active: match[3], sub: match[4] } : undefined;
  });
  if (entries.some((entry) => !entry)) return undefined;
  const failed = entries.filter((entry) => entry.active === "failed" || entry.sub === "failed");
  const important = entries.filter((entry) => /^(?:ods-|openclaw-|pixel-)/.test(entry.unit));
  const sample = [...new Set([...failed, ...important, ...entries])]
    .slice(0, 10)
    .map((entry) => `${entry.unit}=${entry.active}/${entry.sub}`);
  return `System services: ${entries.length} running or failed; failed: ${failed.length ? failed.map((entry) => entry.unit).join(", ") : "none"}; sample: ${sample.join(", ")}.`;
}

function cpuEvidenceFields(step) {
  if (!step || step.stderr.trim() || step.riskSignals.length > 0 || step.stdout.length > 64 * 1024) {
    return undefined;
  }
  let value;
  try {
    value = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  if (!exactKeys(value, ["lscpu"]) || !Array.isArray(value.lscpu) || value.lscpu.length > 128) {
    return undefined;
  }
  const wanted = new Set([
    "Architecture:", "CPU(s):", "Model name:", "Vendor ID:",
    "Thread(s) per core:", "Core(s) per socket:", "Socket(s):",
    "Virtualization:", "Hypervisor vendor:",
  ]);
  const fields = [];
  for (const entry of value.lscpu) {
    if (!exactKeys(entry, ["field", "data"]) || typeof entry.field !== "string") return undefined;
    if (!wanted.has(entry.field)) continue;
    const data = cleanSingleLine(String(entry.data), /^[A-Za-z0-9][A-Za-z0-9 ._+()/:,@-]{0,255}$/, 256);
    if (!data) return undefined;
    fields.push({ field: entry.field, data });
  }
  return fields.length >= 2 ? fields : undefined;
}

function cpuEvidence(step) {
  const fields = cpuEvidenceFields(step);
  return fields
    ? `CPU: ${fields.map(({ field, data }) => `${field.slice(0, -1)} ${data}`).join("; ")}.`
    : undefined;
}

function gpuEvidence(step) {
  if (!step || step.stderr.trim() || step.riskSignals.length > 0 || step.stdout.length > 64 * 1024) {
    return undefined;
  }
  let value;
  try {
    value = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  if (
    !exactKeys(value, ["available", "backend", "devices", "kind", "schemaVersion"]) ||
    value.schemaVersion !== 1 ||
    value.kind !== "ods-host-gpu" ||
    typeof value.available !== "boolean" ||
    !["nvidia", "unavailable"].includes(value.backend) ||
    !Array.isArray(value.devices) ||
    value.devices.length > 16
  ) {
    return undefined;
  }
  const devices = [];
  for (const device of value.devices) {
    if (!exactKeys(device, ["driver", "memoryMiB", "name"])) return undefined;
    const name = cleanSingleLine(device.name, /^[A-Za-z0-9][A-Za-z0-9 ._+()/@-]{0,95}$/, 96);
    const driver = cleanSingleLine(device.driver, /^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$/, 64);
    if (!name || !driver || !Number.isInteger(device.memoryMiB) || device.memoryMiB < 1 || device.memoryMiB > 10_000_000) {
      return undefined;
    }
    devices.push(`${name} (${device.memoryMiB} MiB; driver ${driver})`);
  }
  if (value.available !== (devices.length > 0)) return undefined;
  if (!value.available && (value.backend !== "unavailable" || devices.length > 0)) return undefined;
  if (value.available && value.backend !== "nvidia") return undefined;
  return value.available
    ? `GPU: ${devices.join("; ")}. Device identifiers and serial numbers are omitted.`
    : "GPU telemetry is unavailable through the bounded host observer.";
}

function tailscaleEvidence(step) {
  if (!step || step.stderr.trim() || step.riskSignals.length > 0 || step.stdout.length > 4096) {
    return undefined;
  }
  let value;
  try {
    value = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  if (
    !exactKeys(value, ["available", "kind", "schemaVersion", "serviceRunning", "state"]) ||
    value.schemaVersion !== 1 ||
    value.kind !== "ods-host-tailscale" ||
    typeof value.available !== "boolean" ||
    typeof value.serviceRunning !== "boolean" ||
    !new Set(["running", "starting", "stopped", "needs-login", "service-running", "service-not-running", "not-installed", "unknown"]).has(value.state)
  ) {
    return undefined;
  }
  const runningStates = new Set(["running", "service-running"]);
  if (
    (!value.available && value.state !== "not-installed") ||
    (value.available && value.state === "not-installed") ||
    value.serviceRunning !== runningStates.has(value.state)
  ) {
    return undefined;
  }
  return `Tailscale: ${value.available ? "available" : "not installed"}; state ${value.state}; service running ${value.serviceRunning ? "yes" : "no"}. Addresses, peers, accounts, and routes are omitted.`;
}

function cpuArchitectureEvidence(step) {
  const fields = cpuEvidenceFields(step);
  return fields?.find(({ field }) => field === "Architecture:")?.data;
}

function uptimeEvidence(step) {
  const lines = safeHostLines(step, 2, 1024);
  if (!lines || lines.length !== 1) return undefined;
  const match = lines[0].match(
    /^[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s+up\s+([0-9]{1,4}\s+days?(?:,\s*[0-9]{1,3}:[0-9]{2})?|[0-9]{1,4}\s+min|[0-9]{1,3}:[0-9]{2}),\s+([0-9]{1,6})\s+users?,\s+load average:\s+([0-9]+(?:\.[0-9]+)?),\s+([0-9]+(?:\.[0-9]+)?),\s+([0-9]+(?:\.[0-9]+)?)$/
  );
  if (!match) return undefined;
  return `Uptime: ${match[1]}; users: ${match[2]}; ` +
    `load average (1/5/15m): ${match[3]}, ${match[4]}, ${match[5]}.`;
}

function memoryEvidence(step) {
  const lines = safeHostLines(step, 16, 4096);
  if (!lines) return undefined;
  const memory = lines.find((line) => line.startsWith("Mem:"));
  const swap = lines.find((line) => line.startsWith("Swap:"));
  const parse = (line) => line?.split(/\s+/).slice(1).map((item) => Number(item));
  const mem = parse(memory);
  const swp = parse(swap);
  if (!mem || mem.length < 3 || mem.some((item) => !Number.isSafeInteger(item) || item < 0)) {
    return undefined;
  }
  if (
    !swp ||
    swp.length < 3 ||
    swp.some((item) => !Number.isSafeInteger(item) || item < 0) ||
    swp[1] > swp[0] ||
    swp[2] > swp[0] ||
    swp[1] + swp[2] !== swp[0]
  ) {
    return undefined;
  }
  const total = formatHostBytes(mem[0]);
  const used = formatHostBytes(mem[1]);
  const available = formatHostBytes(mem[5] ?? mem[2]);
  const swapTotal = formatHostBytes(swp[0]);
  const swapUsed = formatHostBytes(swp[1]);
  const swapFree = formatHostBytes(swp[2]);
  if (!total || !used || !available || !swapTotal || !swapUsed || !swapFree) return undefined;
  return `Memory: ${used} used of ${total}; ${available} available; ` +
    `swap ${swapUsed} used of ${swapTotal}, ${swapFree} free.`;
}

function storageEvidence(step) {
  const lines = safeHostLines(step, 512);
  if (!lines || !/^Type\s+1B-blocks\s+Used\s+Avail\s+Use%\s+Mounted on$/.test(lines[0])) {
    return undefined;
  }
  const mounts = [];
  for (const line of lines.slice(1)) {
    const match = line.match(/^(\S+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]{1,3}%)\s+(.+)$/);
    if (!match) return undefined;
    const type = cleanSingleLine(match[1], /^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$/, 64);
    const mount = cleanSingleLine(match[6], /^\/(?:[A-Za-z0-9_./:@+,-]{0,510})$/, 512);
    const size = formatHostBytes(Number(match[2]));
    const available = formatHostBytes(Number(match[4]));
    if (!type || !mount || !size || !available) return undefined;
    mounts.push({
      mount,
      text: `${mount} (${type}, ${match[5]} used, ${available} free of ${size})`,
    });
  }
  if (!mounts.length) return undefined;
  const useful = mounts.filter(({ mount }) =>
    mount === "/" ||
    /^\/mnt\/[A-Za-z]$/.test(mount) ||
    !/^\/(?:dev(?:\/|$)|init(?:\/|$)|run(?:\/|$)|usr\/lib\/wsl(?:\/|$)|mnt\/wslg?(?:\/|$))/.test(mount)
  );
  const selected = useful.slice(0, 6);
  return `Storage mounts: ${selected.map(({ text }) => text).join("; ")}.`;
}

function networkAddressEvidence(step) {
  if (!step || step.stderr.trim() || step.riskSignals.length > 0 || step.stdout.length > 128 * 1024) {
    return undefined;
  }
  let value;
  try {
    value = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  if (!Array.isArray(value) || value.length > 128) return undefined;
  const interfaces = [];
  for (const entry of value) {
    const name = cleanSingleLine(entry?.ifname, /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,63}$/, 64);
    if (!name || !Array.isArray(entry.addr_info) || entry.addr_info.length > 64) return undefined;
    const addresses = [];
    for (const address of entry.addr_info) {
      if (!address || !["inet", "inet6"].includes(address.family)) continue;
      const local = cleanSingleLine(address.local, /^[0-9A-Fa-f:.]{1,64}$/, 64);
      if (!local || !Number.isInteger(address.prefixlen) || address.prefixlen < 0 || address.prefixlen > 128) {
        return undefined;
      }
      addresses.push(`${local}/${address.prefixlen}`);
    }
    interfaces.push(`${name}${addresses.length ? `=${addresses.join(",")}` : "=no address"}`);
  }
  return `Network interfaces: ${interfaces.join("; ")}.`;
}

function networkRouteEvidence(step) {
  if (!step || step.stderr.trim() || step.riskSignals.length > 0 || step.stdout.length > 128 * 1024) {
    return undefined;
  }
  let value;
  try {
    value = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  if (!Array.isArray(value) || value.length > 512) return undefined;
  const routes = [];
  for (const entry of value.slice(0, 24)) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) return undefined;
    const dst = cleanSingleLine(String(entry.dst ?? "default"), /^(?:[0-9A-Fa-f:./]{1,80}|default)$/, 80);
    const dev = cleanSingleLine(String(entry.dev ?? "unknown"), /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,63}$/, 64);
    const gateway = entry.gateway === undefined
      ? undefined
      : cleanSingleLine(String(entry.gateway), /^[0-9A-Fa-f:.]{1,64}$/, 64);
    if (!dst || !dev || (entry.gateway !== undefined && !gateway)) return undefined;
    routes.push(`${dst} via ${gateway ?? "direct"} dev ${dev}`);
  }
  return `Network routes: ${value.length}; sample: ${routes.join("; ")}.`;
}

function listeningPortEvidence(step) {
  const lines = safeHostLines(step, 4096);
  if (!lines) return undefined;
  const endpoints = lines.map((line) => {
    const match = line.match(/^(tcp|udp)\s+([A-Z-]{1,16})\s+[0-9]+\s+[0-9]+\s+(\S{1,256})\s+\S{1,256}$/);
    if (!match) return undefined;
    const local = cleanSingleLine(match[3], /^[A-Za-z0-9.*:%[\]_-]{1,256}$/, 256);
    return local ? `${match[1]} ${match[2]} ${local}` : undefined;
  });
  if (endpoints.some((entry) => !entry)) return undefined;
  const prioritized = [
    ...endpoints.filter((entry) => entry.startsWith("tcp ")),
    ...endpoints.filter((entry) => entry.startsWith("udp ")),
  ];
  return `Listening TCP/UDP endpoints: ${endpoints.length}; sample: ${prioritized.slice(0, 12).join("; ")}.`;
}

function privatePeerAddressScope(address) {
  const family = isIP(address);
  if (family === 4) {
    const octets = address.split(".").map(Number);
    if (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127) return "tailscale";
    if (
      octets[0] === 10 ||
      (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
      (octets[0] === 192 && octets[1] === 168)
    ) return "lan";
    if (octets[0] === 169 && octets[1] === 254) return "link-local";
    return undefined;
  }
  if (family !== 6) return undefined;
  const lower = address.toLowerCase();
  if (lower.startsWith("fd7a:115c:a1e0:")) return "tailscale";
  const first = Number.parseInt(lower.split(":", 1)[0], 16);
  if ((first & 0xffc0) === 0xfe80) return "link-local";
  if ((first & 0xfe00) === 0xfc00) return "lan";
  return undefined;
}

function networkPeerEvidence(step) {
  if (!step || step.stderr.trim() || step.riskSignals.length > 0 || step.stdout.length > 64 * 1024) {
    return undefined;
  }
  let value;
  try {
    value = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  if (
    !exactKeys(value, [
      "addresses", "kind", "ports", "reachable", "resolved",
      "schemaVersion", "tailscale", "target",
    ]) ||
    value.schemaVersion !== 1 ||
    value.kind !== "ods-host-network-peer" ||
    typeof value.target !== "string" ||
    value.target !== step.parameters?.peer ||
    typeof value.resolved !== "boolean" ||
    typeof value.reachable !== "boolean" ||
    !Array.isArray(value.ports) ||
    value.ports.length < 1 ||
    value.ports.length > 8 ||
    value.ports.join(",") !== step.parameters?.ports ||
    value.ports.some((port) => !Number.isInteger(port) || port < 1 || port > 65535) ||
    !Array.isArray(value.addresses) ||
    value.addresses.length > 8 ||
    !exactKeys(value.tailscale, ["addresses", "available", "found", "online"]) ||
    typeof value.tailscale.available !== "boolean" ||
    typeof value.tailscale.found !== "boolean" ||
    ![true, false, null].includes(value.tailscale.online) ||
    !Array.isArray(value.tailscale.addresses) ||
    value.tailscale.addresses.length > 4
  ) {
    return undefined;
  }
  if (
    (!value.tailscale.available && (value.tailscale.found || value.tailscale.online !== null)) ||
    (!value.tailscale.found &&
      (value.tailscale.online !== null || value.tailscale.addresses.length !== 0)) ||
    (value.tailscale.found && typeof value.tailscale.online !== "boolean") ||
    value.tailscale.addresses.some(
      (address) => privatePeerAddressScope(address) !== "tailscale"
    )
  ) {
    return undefined;
  }
  const paths = [];
  let positive = value.tailscale.online === true;
  for (const item of value.addresses) {
    if (
      !exactKeys(item, ["address", "family", "icmpReachable", "scope", "tcp"]) ||
      !["ipv4", "ipv6"].includes(item.family) ||
      !["lan", "link-local", "tailscale"].includes(item.scope) ||
      ![true, false, null].includes(item.icmpReachable) ||
      !Array.isArray(item.tcp) ||
      item.tcp.length !== value.ports.length ||
      isIP(item.address) !== (item.family === "ipv4" ? 4 : 6) ||
      privatePeerAddressScope(item.address) !== item.scope
    ) {
      return undefined;
    }
    const open = [];
    for (const [index, result] of item.tcp.entries()) {
      if (
        !exactKeys(result, ["open", "port"]) ||
        result.port !== value.ports[index] ||
        typeof result.open !== "boolean"
      ) {
        return undefined;
      }
      if (result.open) open.push(result.port);
    }
    if (item.icmpReachable === true || open.length > 0) positive = true;
    paths.push(
      `${item.address} (${item.scope}; ICMP ${
        item.icmpReachable === null ? "unavailable" : item.icmpReachable ? "reachable" : "no reply"
      }; open TCP ${open.length ? open.join(",") : "none of probed ports"})`
    );
  }
  if (value.resolved !== (value.addresses.length > 0) || value.reachable !== positive) {
    return undefined;
  }
  const tailscale = value.tailscale.available
    ? value.tailscale.found
      ? `exact peer found; online ${value.tailscale.online ? "yes" : "no"}`
      : "available; exact peer not found"
    : "status unavailable";
  return (
    `Private network peer \`${value.target}\`: resolved ${value.resolved ? "yes" : "no"}; ` +
    `positive reachability ${value.reachable ? "yes" : "no"}; ` +
    `addresses ${paths.length ? paths.join("; ") : "none"}; Tailscale ${tailscale}. ` +
    (value.reachable
      ? ""
      : "No reply or open probed service does not by itself prove that the peer is offline.")
  ).trim();
}

function operationsHostEvidenceText(
  requiredActions,
  terminalJobs,
  odsAppsProjection = undefined,
  odsStatusProjection = undefined
) {
  if (!(requiredActions instanceof Set) || requiredActions.size === 0) return undefined;
  if (!(terminalJobs instanceof Map)) return undefined;
  const steps = new Map();
  const unsuccessful = [];
  for (const outcome of terminalJobs.values()) {
    if (outcome.status !== "succeeded") {
      unsuccessful.push(outcome);
      continue;
    }
    for (const step of outcome.steps) {
      if (step.target === "ods-host" && requiredActions.has(step.action)) {
        const submission = outcome.actions.find(
          (action) => action.target === step.target && action.action === step.action
        );
        steps.set(step.action, {
          ...step,
          parameters: submission?.parameters,
          jobId: outcome.jobId,
        });
      }
    }
  }
  // `host.cpu` is a structured lscpu observation whose validated payload
  // already contains Architecture. Treat that exact field as equivalent typed
  // evidence during a broad inventory instead of forcing a redundant
  // `host.architecture` job and discarding an otherwise complete report.
  const derivedArchitecture =
    requiredActions.has("host.architecture") &&
    !steps.has("host.architecture") &&
    steps.has("host.cpu")
      ? cpuArchitectureEvidence(steps.get("host.cpu"))
      : undefined;
  const missing = [...requiredActions].filter((action) =>
    !steps.has(action) && !(action === "host.architecture" && derivedArchitecture)
  );
  if (missing.length > 0) {
    const failedRequiredOutcome = unsuccessful.find((outcome) =>
      outcome.actions.some(({ target, action }) =>
        target === "ods-host" && missing.includes(action)
      )
    );
    if (!failedRequiredOutcome) return undefined;
    const plan = typeof failedRequiredOutcome.planHash === "string" && SHA256.test(failedRequiredOutcome.planHash)
      ? ` Plan SHA-256: ${failedRequiredOutcome.planHash}.`
      : "";
    return `Pixel's required host Operations job reached terminal status ${failedRequiredOutcome.status}. Job: ${failedRequiredOutcome.jobId}.${plan}`;
  }
  const lines = [OPERATIONS_HOST_EVIDENCE_PREFIX];
  const identity = steps.get("host.identity");
  if (identity) {
    const value = cleanSingleLine(
      identity.stdout,
      /^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/,
      253
    );
    if (!value || identity.stderr.trim() || identity.riskSignals.length > 0) return undefined;
    lines.push(`- Hostname: \`${value}\` (job \`${identity.jobId}\`)`);
  }
  const kernel = steps.get("host.kernel");
  if (kernel) {
    const value = cleanSingleLine(
      kernel.stdout,
      /^[A-Za-z0-9][A-Za-z0-9 ._~+/:#()-]{0,511}$/,
      512
    );
    if (!value || kernel.stderr.trim() || kernel.riskSignals.length > 0) return undefined;
    lines.push(`- Kernel: \`${value}\` (job \`${kernel.jobId}\`)`);
  }
  const architecture = steps.get("host.architecture");
  if (architecture) {
    const value = cleanSingleLine(
      architecture.stdout,
      /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/,
      64
    );
    if (!value || architecture.stderr.trim() || architecture.riskSignals.length > 0) return undefined;
    lines.push(`- Architecture: \`${value}\` (job \`${architecture.jobId}\`)`);
  } else if (derivedArchitecture) {
    const cpu = steps.get("host.cpu");
    lines.push(
      `- Architecture: \`${derivedArchitecture}\` (from structured host.cpu job \`${cpu.jobId}\`)`
    );
  }
  const platform = steps.get("host.platform");
  if (platform) {
    const value = cleanSingleLine(
      platform.stdout,
      /^[A-Za-z0-9][A-Za-z0-9 ._~+/:#()-]{0,1023}$/,
      1024
    );
    if (!value || platform.stderr.trim() || platform.riskSignals.length > 0) return undefined;
    lines.push(`- Platform: \`${value}\` (job \`${platform.jobId}\`)`);
  }
  const osRelease = steps.get("host.os-release");
  if (osRelease) {
    const value = osPrettyName(osRelease.stdout);
    if (!value || osRelease.stderr.trim() || osRelease.riskSignals.length > 0) return undefined;
    lines.push(`- Operating system: \`${value}\` (job \`${osRelease.jobId}\`)`);
  }
  const renderers = [
    ["host.uptime", "Uptime and load", uptimeEvidence],
    ["host.cpu", "Hardware", cpuEvidence],
    ["host.gpu", "GPU", gpuEvidence],
    ["host.memory", "Memory", memoryEvidence],
    ["host.storage", "Storage", storageEvidence],
    ["host.processes", "Processes", processEvidence],
    ["host.services", "Services", serviceEvidence],
    ["host.network-addresses", "Addresses", networkAddressEvidence],
    ["host.network-routes", "Routes", networkRouteEvidence],
    ["host.listening-ports", "Listening ports", listeningPortEvidence],
    ["host.tailscale", "Tailscale", tailscaleEvidence],
    ["host.network-peer", "Network peer", networkPeerEvidence],
  ];
  for (const [action, label, renderer] of renderers) {
    const step = steps.get(action);
    if (!step) continue;
    const value = renderer(step);
    if (!value) return undefined;
    lines.push(`- ${label}: ${value} (job \`${step.jobId}\`)`);
  }
  if (odsAppsProjection) {
    const apps = odsAppsProjection.apps
      .map(({ name, status }) => `\`${name}\` (${status})`)
      .join(", ");
    lines.push(
      `- ODS container projection: ${odsAppsProjection.online_app_count} of ${odsAppsProjection.app_count} allowlisted ODS application containers online; ${apps || "none reported"}.`
    );
    const applicationDetails = odsAppsProjection.apps
      .filter(({ display_name, purpose, url }) => display_name && purpose && url)
      .map(
        ({ name, display_name: displayName, purpose, url }) =>
          `\`${name}\`: ${displayName} - ${purpose} - <${url}>`
      )
      .join("; ");
    if (applicationDetails) {
      lines.push(`- ODS application details: ${applicationDetails}.`);
    }
    lines.push(
      "- Container boundary: this host-produced status projection covers allowlisted ODS application containers only; it does not enumerate unrelated or non-ODS containers."
    );
  }
  if (odsStatusProjection) {
    const availability =
      odsStatusProjection.ingress_ready && odsStatusProjection.gateway_reachable
        ? "available"
        : "unavailable";
    const runtime = odsStatusProjection.runtime
      ? `model \`${odsStatusProjection.runtime.model}\`; context ${odsStatusProjection.runtime.context_length} tokens`
      : "model unavailable; context unavailable";
    lines.push(
      `- ODS runtime projection: ${runtime}; Pixel ${availability}; ODS version \`${odsStatusProjection.ods_version}\`.`
    );
    if (!odsAppsProjection) {
      lines.push(
        `- ODS container count projection: ${odsStatusProjection.online_app_count} of ${odsStatusProjection.app_count} allowlisted ODS application containers online.`
      );
    }
    lines.push(
      "- Runtime boundary: this current host-produced status projection is untrusted status evidence only and grants no authority for an action."
    );
  }
  return lines.join("\n");
}

function exactKeys(value, keys) {
  return (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).sort().join("\u0000") === [...keys].sort().join("\u0000")
  );
}

function boundedJsonSnapshot(value, maxBytes = 262_144) {
  try {
    const serialized = JSON.stringify(value);
    if (
      typeof serialized !== "string" ||
      Buffer.byteLength(serialized, "utf8") > maxBytes
    ) {
      return undefined;
    }
    return JSON.parse(serialized);
  } catch {
    return undefined;
  }
}

function operationsOdsAppsProjection(event) {
  if (toolCallFailed(event)) return undefined;
  // Plugin hooks may receive framework-owned objects with ephemeral undefined
  // properties that cannot exist in the persisted JSON tool result. Validate
  // the bounded wire representation so live and replayed results obey exactly
  // the same contract.
  const value = boundedJsonSnapshot(event?.result?.details?.projection);
  if (
    !exactKeys(value, [
      "app_count", "online_app_count", "apps", "timestamp", "stale", "boundary",
    ]) ||
    value.boundary !== "status-only" ||
    value.stale !== false ||
    !Number.isInteger(value.app_count) ||
    value.app_count < 0 ||
    value.app_count > 64 ||
    !Number.isInteger(value.online_app_count) ||
    value.online_app_count < 0 ||
    value.online_app_count > value.app_count ||
    !Array.isArray(value.apps) ||
    value.apps.length !== value.app_count ||
    typeof value.timestamp !== "string" ||
    value.timestamp.length > 64 ||
    !Number.isFinite(Date.parse(value.timestamp))
  ) {
    return undefined;
  }
  const apps = [];
  const names = new Set();
  for (const app of value.apps) {
    const keys = Object.keys(app ?? {}).sort().join("\u0000");
    const minimal = ["name", "status"].sort().join("\u0000");
    const enriched = ["name", "status", "display_name", "purpose", "url"]
      .sort()
      .join("\u0000");
    if (
      !app ||
      typeof app !== "object" ||
      Array.isArray(app) ||
      (keys !== minimal && keys !== enriched) ||
      typeof app.name !== "string" ||
      !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(app.name) ||
      names.has(app.name) ||
      !["running", "healthy", "unhealthy", "starting", "stopped"].includes(app.status)
    ) {
      return undefined;
    }
    if (
      keys === enriched &&
      (!cleanSingleLine(
        app.display_name,
        /^[A-Za-z0-9][A-Za-z0-9 .,_+()\/:;~'&-]{0,127}$/,
        128
      ) ||
        !cleanSingleLine(
          app.purpose,
          /^[A-Za-z0-9][A-Za-z0-9 .,_+()\/:;~'&-]{0,255}$/,
          256
        ) ||
        typeof app.url !== "string" ||
        !/^http:\/\/localhost:[1-9][0-9]{0,4}\/[A-Za-z0-9._~!$&'()*+,;=:@%/?#-]*$/.test(app.url))
    ) {
      return undefined;
    }
    names.add(app.name);
    apps.push(
      keys === enriched
        ? {
            name: app.name,
            status: app.status,
            display_name: app.display_name.trim(),
            purpose: app.purpose.trim(),
            url: app.url,
          }
        : { name: app.name, status: app.status }
    );
  }
  const online = apps.filter(({ status }) => status === "running" || status === "healthy").length;
  if (online !== value.online_app_count) return undefined;
  return {
    app_count: value.app_count,
    online_app_count: value.online_app_count,
    apps,
    timestamp: value.timestamp,
  };
}

function operationsOdsStatusProjection(event) {
  if (toolCallFailed(event)) return undefined;
  const details = event?.result?.details;
  const value = boundedJsonSnapshot(details?.projection);
  const compactKeys = [
    "status", "ingress_ready", "gateway_reachable", "docker", "ods_version",
    "online_app_count", "runtime", "app_count", "timestamp", "stale", "boundary",
  ];
  const legacyKeys = [...compactKeys, "apps"];
  if (
    (!exactKeys(value, compactKeys) && !exactKeys(value, legacyKeys)) ||
    value.status !== "ok" ||
    typeof value.ingress_ready !== "boolean" ||
    typeof value.gateway_reachable !== "boolean" ||
    !["ok", "unavailable"].includes(value.docker) ||
    typeof value.ods_version !== "string" ||
    !/^(?:unknown|[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?)$/.test(value.ods_version) ||
    value.boundary !== "status-only" ||
    value.stale !== false ||
    typeof value.timestamp !== "string" ||
    value.timestamp.length > 64 ||
    !Number.isFinite(Date.parse(value.timestamp))
  ) {
    return undefined;
  }
  let appCount = value.app_count;
  let onlineAppCount = value.online_app_count;
  if (Object.hasOwn(value, "apps")) {
    const appsProjection = operationsOdsAppsProjection({
      result: {
        details: {
          projection: {
            app_count: value.app_count,
            online_app_count: value.online_app_count,
            apps: value.apps,
            timestamp: value.timestamp,
            stale: value.stale,
            boundary: value.boundary,
          },
        },
      },
    });
    if (!appsProjection) return undefined;
    appCount = appsProjection.app_count;
    onlineAppCount = appsProjection.online_app_count;
  } else if (
    !Number.isInteger(appCount) ||
    !Number.isInteger(onlineAppCount) ||
    appCount < 0 ||
    appCount > 256 ||
    onlineAppCount < 0 ||
    onlineAppCount > appCount
  ) {
    return undefined;
  }
  let runtimeValue = value.runtime;
  if (
    runtimeValue !== null &&
    !exactKeys(runtimeValue, ["model", "context_length"])
  ) {
    // OpenClaw can transiently replace projection.runtime with its own runtime
    // marker while leaving the status tool's dedicated, sanitized runtime field
    // intact. Bind only to that same-result duplicate and validate it below.
    runtimeValue = boundedJsonSnapshot(details?.runtime);
  }
  let runtime = null;
  if (runtimeValue !== null) {
    if (
      !exactKeys(runtimeValue, ["model", "context_length"]) ||
      typeof runtimeValue.model !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9._+:/ -]{0,255}$/.test(runtimeValue.model) ||
      !Number.isInteger(runtimeValue.context_length) ||
      runtimeValue.context_length < 4096 ||
      runtimeValue.context_length > 10_000_000
    ) {
      return undefined;
    }
    runtime = {
      model: runtimeValue.model,
      context_length: runtimeValue.context_length,
    };
  }
  return {
    runtime,
    ingress_ready: value.ingress_ready,
    gateway_reachable: value.gateway_reachable,
    ods_version: value.ods_version,
    app_count: appCount,
    online_app_count: onlineAppCount,
    timestamp: value.timestamp,
  };
}

function validatedToolSearchEnvelope(candidate, expectedToolName, expectedSourceName) {
  const value = boundedJsonSnapshot(candidate);
  const tool = value?.tool;
  const result = value?.result;
  if (
    !tool ||
    typeof tool !== "object" ||
    Array.isArray(tool) ||
    tool.id !== `openclaw:${expectedSourceName}:${expectedToolName}` ||
    tool.source !== "openclaw" ||
    tool.sourceName !== expectedSourceName ||
    tool.name !== expectedToolName ||
    !result ||
    typeof result !== "object" ||
    Array.isArray(result)
  ) {
    return undefined;
  }
  return { tool, result };
}

function toolSearchEventEnvelope(event, expectedToolName, expectedSourceName) {
  const params = event?.params;
  if (
    !params ||
    typeof params !== "object" ||
    Array.isArray(params) ||
    params.id !== expectedToolName
  ) {
    return undefined;
  }
  const envelope = validatedToolSearchEnvelope(
    event?.result?.details,
    expectedToolName,
    expectedSourceName
  );
  if (!envelope) return undefined;
  return {
    params:
      params.args && typeof params.args === "object" && !Array.isArray(params.args)
        ? params.args
        : {},
    ...envelope,
  };
}

function toolSearchSelectedToolEvent(event, expectedToolName, expectedSourceName) {
  if (toolCallFailed(event)) return undefined;
  const envelope = toolSearchEventEnvelope(event, expectedToolName, expectedSourceName);
  return envelope ? { params: envelope.params, result: envelope.result } : undefined;
}

function persistedToolSearchEnvelope(
  message,
  expectedToolName,
  expectedSourceName,
  capturedEnvelope
) {
  // OpenClaw keeps the complete, framework-owned Tool Search envelope in
  // message.details even when it truncates the model-visible JSON text block.
  // Validate that bounded structured copy first so large failures can still be
  // reduced to actionable evidence instead of consuming a compact model's
  // entire remaining context. Retain the JSON block path for older runtimes.
  const structuredEnvelope = validatedToolSearchEnvelope(
    message?.details,
    expectedToolName,
    expectedSourceName
  );
  if (structuredEnvelope) return structuredEnvelope;
  const boundedCapturedEnvelope = validatedToolSearchEnvelope(
    capturedEnvelope,
    expectedToolName,
    expectedSourceName
  );
  if (boundedCapturedEnvelope) return boundedCapturedEnvelope;
  if (!Array.isArray(message?.content)) return undefined;
  for (const block of message.content) {
    if (block?.type !== "text" || typeof block.text !== "string") continue;
    let parsed;
    try {
      parsed = JSON.parse(block.text);
    } catch {
      continue;
    }
    const envelope = validatedToolSearchEnvelope(
      parsed,
      expectedToolName,
      expectedSourceName
    );
    if (envelope) return envelope;
  }
  return undefined;
}

function persistedToolSearchResult(message, expectedToolName, expectedSourceName) {
  return Boolean(
    persistedToolSearchEnvelope(message, expectedToolName, expectedSourceName)
  );
}

function cleanUnittestSummary(result) {
  if (
    !result ||
    typeof result !== "object" ||
    Array.isArray(result) ||
    result.isError === true ||
    result?.details?.status !== "completed" ||
    result?.details?.exitCode !== 0 ||
    execResultHasNonCleanUnittestOutcome({ result })
  ) {
    return undefined;
  }
  const values = [
    result?.details?.aggregated,
    ...(Array.isArray(result.content)
      ? result.content.map((item) => item?.type === "text" ? item.text : undefined)
      : []),
  ].filter((value) => typeof value === "string");
  for (const value of values) {
    const match = value.match(/(?:^|\n)(Ran\s+[1-9][0-9]*\s+tests?\s+in\s+[^\r\n]+\r?\n\r?\nOK)\s*$/i);
    if (match) return match[1];
  }
  return undefined;
}

function compactCleanVerificationResult(message, pending) {
  if (!verificationFingerprintIsPythonUnittest(pending?.verificationFingerprint)) {
    return undefined;
  }
  const envelope = persistedToolSearchEnvelope(
    message,
    "exec",
    "core",
    pending?.capturedToolSearchEnvelope
  );
  const summary = cleanUnittestSummary(envelope?.result);
  if (!summary) return undefined;
  const details = envelope.result.details;
  const compactDetails = {
    status: "completed",
    exitCode: 0,
    ...(Number.isInteger(details?.durationMs) && details.durationMs >= 0
      ? { durationMs: details.durationMs }
      : {}),
    ...(typeof details?.cwd === "string" && details.cwd
      ? { cwd: details.cwd }
      : {}),
  };
  return {
    ...message,
    content: [{
      type: "text",
      text: JSON.stringify({
        result: {
          content: [{
            type: "text",
            text:
              "[Per-test success lines compacted after guard validation.]\n" +
              summary,
          }],
          details: compactDetails,
        },
      }),
    }],
    details: {
      result: {
        content: [{ type: "text", text: summary }],
        details: compactDetails,
      },
      tool: envelope.tool,
    },
  };
}

function compactFailedUnittestText(result) {
  if (
    !result ||
    typeof result !== "object" ||
    Array.isArray(result) ||
    result?.details?.exitCode === 0
  ) {
    return undefined;
  }
  const values = Array.isArray(result.content)
    ? result.content
      .filter((item) => item?.type === "text" && typeof item.text === "string")
      .map((item) => item.text)
    : [];
  const source = values.sort((left, right) => right.length - left.length)[0];
  if (typeof source !== "string" || source.length < 600) return undefined;
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const failureIndex = lines.findLastIndex((line) => /^(?:FAIL|ERROR):\s+/.test(line));
  const ranIndex = lines.findLastIndex((line) => /^Ran\s+[1-9][0-9]*\s+tests?\s+in\s+/.test(line));
  const diagnosticEnd = ranIndex > failureIndex ? ranIndex : lines.length;
  const errorIndex = lines.findLastIndex(
    (line, index) =>
      index > failureIndex &&
      index < diagnosticEnd &&
      /^(?:AssertionError|[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception))(?::|$)/.test(line)
  );
  let frameIndex = -1;
  for (let index = errorIndex - 1; index > failureIndex; index -= 1) {
    if (/^\s*File\s+"/.test(lines[index])) {
      frameIndex = index;
      if (lines[index].includes("/workspace/")) break;
    }
  }
  const detailStart = frameIndex >= 0
    ? frameIndex
    : Math.max(failureIndex + 1, errorIndex - 3, 0);
  const detailEnd = errorIndex >= detailStart
    ? Math.min(diagnosticEnd, errorIndex + 12)
    : Math.min(diagnosticEnd, detailStart + 20);
  const summaryLines = [];
  if (failureIndex >= 0) summaryLines.push(lines[failureIndex]);
  summaryLines.push(...lines.slice(detailStart, detailEnd));
  if (ranIndex >= 0) summaryLines.push(...lines.slice(ranIndex));
  let summary = summaryLines.join("\n").trim();
  if (summary.length > 1400) {
    summary = `${summaryLines[0]}\n${summary.slice(-1320)}`;
  }
  return `[Earlier unittest framework frames compacted.]\n${summary}`;
}

function compactWorkspaceCoreResult(message, pending, state) {
  const toolName = pending?.selectedToolName;
  if (
    !state?.workspaceTaskRequested ||
    !["read", "write", "edit", "apply_patch", "exec", "process"].includes(toolName)
  ) {
    return undefined;
  }
  const envelope = persistedToolSearchEnvelope(
    message,
    toolName,
    "core",
    pending?.capturedToolSearchEnvelope
  );
  if (!envelope) return undefined;
  const result = envelope.result;
  let content = Array.isArray(result.content)
    ? result.content.filter(
        (item) => item && typeof item === "object" && typeof item.type === "string"
      )
    : [];
  if (verificationFingerprintIsPythonUnittest(pending?.verificationFingerprint)) {
    const failedSummary = compactFailedUnittestText(result);
    if (failedSummary) content = [{ type: "text", text: failedSummary }];
  }
  const details = result?.details;
  const compactDetails = {
    ...(typeof details?.status === "string" ? { status: details.status } : {}),
    ...(Number.isInteger(details?.exitCode) ? { exitCode: details.exitCode } : {}),
    ...(typeof details?.sessionId === "string" && details.sessionId
      ? { sessionId: details.sessionId }
      : {}),
    ...(Number.isInteger(details?.durationMs) && details.durationMs >= 0
      ? { durationMs: details.durationMs }
      : {}),
    ...(typeof details?.cwd === "string" && details.cwd ? { cwd: details.cwd } : {}),
  };
  if (content.length === 0) {
    const status = compactDetails.status ?? (result.isError === true ? "error" : "completed");
    content = [{
      type: "text",
      text:
        `[core ${toolName}: ${status}` +
        `${compactDetails.sessionId ? `; session ${compactDetails.sessionId}` : ""}]`,
    }];
  }
  return {
    ...message,
    content,
    details: {
      tool: envelope.tool,
      result: {
        ...(result.isError === true ? { isError: true } : {}),
        content,
        ...(Object.keys(compactDetails).length > 0 ? { details: compactDetails } : {}),
      },
    },
  };
}

function boundedCatalogString(value, pattern, maximum) {
  if (typeof value !== "string" || !value || value.length > maximum) return undefined;
  if ([...value].some((character) => character.codePointAt(0) < 32)) return undefined;
  return pattern && !pattern.test(value) ? undefined : value;
}

function boundedCatalogList(value, pattern, maximumItems = 64, maximumLength = 256) {
  if (!Array.isArray(value) || value.length > maximumItems) return undefined;
  const result = value.map((item) => boundedCatalogString(item, pattern, maximumLength));
  if (result.some((item) => item === undefined) || new Set(result).size !== result.length) {
    return undefined;
  }
  return result;
}

function extensionCatalogResult(step, submittedParameters) {
  if (
    !step ||
    step.target !== "ods-host" ||
    step.action !== "ods.extensions.search" ||
    step.stderr.trim() ||
    step.riskSignals.length > 0 ||
    typeof step.stdout !== "string" ||
    step.stdout.length > 256 * 1024
  ) {
    return undefined;
  }
  let value;
  try {
    value = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  const topKeys = [
    "schemaVersion", "kind", "query", "totalCatalog", "totalMatches",
    "truncated", "matches", "boundary",
  ];
  if (
    !exactKeys(value, topKeys) ||
    value.schemaVersion !== 1 ||
    value.kind !== "ods-pixel-extension-search" ||
    value.boundary !==
      "Read-only catalog projection; it grants no installation or configuration authority." ||
    !Number.isInteger(value.totalCatalog) ||
    value.totalCatalog < 1 ||
    value.totalCatalog > 256 ||
    !Number.isInteger(value.totalMatches) ||
    value.totalMatches < 0 ||
    value.totalMatches > value.totalCatalog ||
    value.truncated !== (value.totalMatches > 10) ||
    !Array.isArray(value.matches) ||
    value.matches.length !== Math.min(value.totalMatches, 10)
  ) {
    return undefined;
  }
  const query = boundedCatalogString(value.query, /^[A-Za-z0-9 _/+:#.\-]{1,80}$/, 80);
  if (!query || submittedParameters?.query !== query) return undefined;
  const entryKeys = [
    "id", "name", "description", "category", "gpuBackends", "dependsOn",
    "requiredConfiguration", "optionalConfiguration", "tags", "featureNames",
  ];
  const identifiers = new Set();
  const matches = [];
  for (const entry of value.matches) {
    if (!exactKeys(entry, entryKeys)) return undefined;
    const id = boundedCatalogString(entry.id, /^[a-z0-9][a-z0-9._-]{0,63}$/, 64);
    const name = boundedCatalogString(
      entry.name,
      /^[A-Za-z0-9][A-Za-z0-9 ._+()/:&'\-]{0,127}$/,
      128
    );
    const description = boundedCatalogString(entry.description, undefined, 1000);
    const category = boundedCatalogString(entry.category, /^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$/, 64);
    const gpuBackends = boundedCatalogList(entry.gpuBackends, /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/, 16, 32);
    const dependsOn = boundedCatalogList(entry.dependsOn, /^[a-z0-9][a-z0-9._-]{0,63}$/, 64, 64);
    const requiredConfiguration = boundedCatalogList(entry.requiredConfiguration, /^[A-Z][A-Z0-9_]{0,127}$/, 64, 128);
    const optionalConfiguration = boundedCatalogList(entry.optionalConfiguration, /^[A-Z][A-Z0-9_]{0,127}$/, 64, 128);
    const tags = boundedCatalogList(entry.tags, /^[A-Za-z0-9][A-Za-z0-9._+\-/]{0,127}$/, 64, 128);
    const featureNames = boundedCatalogList(entry.featureNames, /^[A-Za-z0-9][A-Za-z0-9 ._+()/:&'\-]{0,255}$/, 32, 256);
    if (
      !id || !name || !description || !category || !gpuBackends || !dependsOn ||
      !requiredConfiguration || !optionalConfiguration || !tags || !featureNames ||
      identifiers.has(id)
    ) {
      return undefined;
    }
    identifiers.add(id);
    matches.push({
      id,
      name,
      description,
      category,
      gpuBackends,
      dependsOn,
      requiredConfiguration,
      optionalConfiguration,
      tags,
      featureNames,
    });
  }
  return { ...value, query, matches };
}

const EXTENSION_LIFECYCLE_BOUNDARY =
  "Scoped ODS extension lifecycle proxy; it grants no Docker, shell, credential, arbitrary HTTP, or data-purge authority.";
const EXTENSION_LIFECYCLE_STATUSES = new Set([
  "enabled", "cli_installed", "disabled", "stopped", "unhealthy",
  "installing", "setting_up", "error", "not_installed", "incompatible",
]);
const EXTENSION_LIFECYCLE_SUCCESS = new Map([
  ["install", new Set(["enabled", "cli_installed"])],
  ["enable", new Set(["enabled", "cli_installed"])],
  ["disable", new Set(["disabled"])],
  ["remove", new Set(["not_installed"])],
]);

function sortedConfigurationKeys(value) {
  const result = boundedCatalogList(value, /^[A-Z][A-Z0-9_]{0,127}$/, 128, 128);
  if (!result || result.some((entry, index) => index > 0 && result[index - 1] >= entry)) {
    return undefined;
  }
  return result;
}

function sameEffectiveLifecycleStatus(left, right) {
  return left === right ||
    [left, right].every((status) => ["enabled", "cli_installed"].includes(status));
}

function extensionLifecycleResult(step, submittedAction) {
  const expectedAction = submittedAction?.action?.replace(/^ods\.extensions\./, "");
  const submittedParameters = submittedAction?.parameters;
  if (
    !step ||
    step.target !== "ods-host" ||
    step.action !== submittedAction?.action ||
    step.stderr.trim() ||
    step.riskSignals.length > 0 ||
    typeof step.stdout !== "string" ||
    step.stdout.length > 256 * 1024 ||
    !["inspect", "install", "enable", "disable", "remove"].includes(expectedAction) ||
    !exactKeys(submittedParameters, ["serviceId"]) ||
    boundedCatalogString(submittedParameters.serviceId, /^[a-z0-9][a-z0-9._-]{0,63}$/, 64) === undefined
  ) {
    return undefined;
  }
  let value;
  try {
    value = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  const topKeys = [
    "schemaVersion", "kind", "action", "extensionId", "outcome",
    "previousStatus", "currentStatus", "changed", "externalEffectOccurred",
    "requiredConfiguration", "optionalConfiguration", "missingConfiguration",
    "rollback", "boundary",
  ];
  if (
    !exactKeys(value, topKeys) ||
    value.schemaVersion !== 1 ||
    value.kind !== "ods-pixel-extension-lifecycle" ||
    value.boundary !== EXTENSION_LIFECYCLE_BOUNDARY ||
    value.action !== expectedAction ||
    value.extensionId !== submittedParameters.serviceId ||
    !["ready", "blocked", "noop", "succeeded", "failed"].includes(value.outcome) ||
    !EXTENSION_LIFECYCLE_STATUSES.has(value.previousStatus) ||
    !EXTENSION_LIFECYCLE_STATUSES.has(value.currentStatus) ||
    typeof value.changed !== "boolean" ||
    typeof value.externalEffectOccurred !== "boolean" ||
    !exactKeys(value.rollback, ["attempted", "succeeded"]) ||
    typeof value.rollback.attempted !== "boolean" ||
    ![true, false, null].includes(value.rollback.succeeded)
  ) {
    return undefined;
  }
  const required = sortedConfigurationKeys(value.requiredConfiguration);
  const optional = sortedConfigurationKeys(value.optionalConfiguration);
  const missing = sortedConfigurationKeys(value.missingConfiguration);
  if (
    !required || !optional || !missing ||
    required.some((key) => optional.includes(key)) ||
    missing.some((key) => !required.includes(key)) ||
    (value.rollback.attempted === false && value.rollback.succeeded !== null) ||
    (value.rollback.attempted === true && typeof value.rollback.succeeded !== "boolean")
  ) {
    return undefined;
  }
  if (expectedAction === "inspect") {
    if (
      !["ready", "blocked"].includes(value.outcome) ||
      value.currentStatus !== value.previousStatus ||
      value.changed ||
      value.externalEffectOccurred ||
      value.rollback.attempted ||
      (value.outcome === "ready") !== (missing.length === 0)
    ) {
      return undefined;
    }
  } else if (["blocked", "noop"].includes(value.outcome)) {
    if (
      value.currentStatus !== value.previousStatus ||
      value.changed ||
      value.externalEffectOccurred ||
      value.rollback.attempted
    ) {
      return undefined;
    }
  } else if (value.outcome === "succeeded") {
    if (
      value.changed !== true ||
      value.externalEffectOccurred !== true ||
      missing.length > 0 ||
      value.rollback.attempted ||
      !EXTENSION_LIFECYCLE_SUCCESS.get(expectedAction)?.has(value.currentStatus)
    ) {
      return undefined;
    }
  } else if (value.outcome === "failed") {
    if (
      value.changed && !value.externalEffectOccurred ||
      (value.externalEffectOccurred && expectedAction !== "remove" && !value.rollback.attempted) ||
      (value.rollback.succeeded === true &&
        !sameEffectiveLifecycleStatus(value.currentStatus, value.previousStatus))
    ) {
      return undefined;
    }
  } else {
    return undefined;
  }
  return { ...value, requiredConfiguration: required, optionalConfiguration: optional, missingConfiguration: missing };
}

function operationsContinuationTerminalOutcome(event, continuation) {
  if (toolCallFailed(event) || !continuation) return undefined;
  const requestedJobId = event?.params?.jobId;
  const details = event?.result?.details;
  if (
    requestedJobId !== continuation.jobId ||
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.jobId !== continuation.jobId ||
    details.planHash !== continuation.planHash ||
    details.waitTimedOut === true ||
    !["succeeded", "failed", "cancelled", "rejected", "awaiting-approval"].includes(
      details.status
    )
  ) {
    return undefined;
  }
  if (details.status === "awaiting-approval") {
    return details.approvalRequired === true
      ? { ...continuation, status: details.status }
      : undefined;
  }
  if (details.status !== "succeeded") {
    return { ...continuation, status: details.status };
  }
  if (details.approvalRequired !== true || !Array.isArray(details.steps) || details.steps.length !== 1) {
    return undefined;
  }
  const step = details.steps[0];
  if (
    !step ||
    typeof step !== "object" ||
    Array.isArray(step) ||
    step.target !== "ods-host" ||
    !/^(?:raw-shell|ods\.extensions\.(?:install|enable|disable|remove))$/.test(step.action) ||
    step.exitCode !== 0 ||
    typeof step.stdout !== "string" ||
    typeof step.stderr !== "string" ||
    !step.outputTruncated ||
    typeof step.outputTruncated !== "object" ||
    step.outputTruncated.stdout !== false ||
    step.outputTruncated.stderr !== false ||
    !Array.isArray(step.riskSignals)
  ) {
    return undefined;
  }
  if (step.action === "raw-shell") {
    if (
      step.stdout.length > 64 * 1024 ||
      step.stderr.length > 64 * 1024 ||
      !Number.isFinite(step.durationSeconds) ||
      step.durationSeconds < 0
    ) {
      return undefined;
    }
    return {
      ...continuation,
      status: details.status,
      kind: "host-command",
      step,
    };
  }
  let rawResult;
  try {
    rawResult = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  if (
    !rawResult ||
    typeof rawResult !== "object" ||
    Array.isArray(rawResult) ||
    typeof rawResult.extensionId !== "string" ||
    !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(rawResult.extensionId)
  ) {
    return undefined;
  }
  const submittedAction = {
    target: "ods-host",
    action: step.action,
    parameters: { serviceId: rawResult.extensionId },
  };
  const result = extensionLifecycleResult(step, submittedAction);
  if (!result) return undefined;
  return {
    ...continuation,
    status: details.status,
    action: submittedAction.action,
    result,
  };
}

function operationsContinuationEvidenceText(outcome) {
  if (!outcome) return undefined;
  if (outcome.status === "awaiting-approval") {
    return `Pixel rechecked Operations job ${outcome.jobId} with plan SHA-256 ${outcome.planHash}; the host still reports awaiting-approval. No operation was accepted as completed.`;
  }
  if (outcome.status !== "succeeded") {
    return `Pixel rechecked Operations job ${outcome.jobId} with plan SHA-256 ${outcome.planHash}; its verified terminal status is ${outcome.status}. No successful operation result was accepted.`;
  }
  if (outcome.kind === "host-command") {
    const step = outcome.step;
    return [
      OPERATIONS_HOST_COMMAND_EVIDENCE_PREFIX,
      "- Target: `ods-host`; action: `raw-shell`; exit code: `0`.",
      `- Duration: ${step.durationSeconds} seconds.`,
      `- Standard output (untrusted command output): ${JSON.stringify(step.stdout)}.`,
      `- Standard error (untrusted command output): ${JSON.stringify(step.stderr)}.`,
      `- Continued broker job: \`${outcome.jobId}\`; plan SHA-256: \`${outcome.planHash}\`.`,
      "- Authority: the broker executed only after an external owner approval matched this immutable plan hash.",
    ].join("\n");
  }
  const result = outcome.result;
  if (!result) return undefined;
  return [
    OPERATIONS_EXTENSION_LIFECYCLE_EVIDENCE_PREFIX,
    `- Extension: \`${result.extensionId}\`.`,
    `- Requested action: \`${result.action}\`; verified outcome: \`${result.outcome}\`.`,
    `- State: \`${result.previousStatus}\` -> \`${result.currentStatus}\`.`,
    `- Change observed: ${result.changed ? "yes" : "no"}; external effect attempted: ${result.externalEffectOccurred ? "yes" : "no"}.`,
    `- Missing required configuration keys: ${result.missingConfiguration.length ? result.missingConfiguration.map((key) => `\`${key}\``).join(", ") : "none"}.`,
    `- Rollback: ${result.rollback.attempted ? (result.rollback.succeeded ? "succeeded" : "failed") : "not required"}.`,
    `- Authority: ${EXTENSION_LIFECYCLE_BOUNDARY}`,
    `- Continued lifecycle job: \`${outcome.jobId}\`; plan SHA-256: \`${outcome.planHash}\`.`,
  ].join("\n");
}

function lifecycleOutcomeForAction(terminalJobs, action) {
  if (!(terminalJobs instanceof Map)) return undefined;
  const matches = [...terminalJobs.values()].filter(
    (outcome) => outcome.actions?.length === 1 && outcome.actions[0]?.action === action
  );
  return matches.length === 1 ? matches[0] : undefined;
}

function parsedLifecycleOutcome(terminalJobs, action) {
  const outcome = lifecycleOutcomeForAction(terminalJobs, action);
  if (!outcome || outcome.status !== "succeeded" || outcome.steps.length !== 1) {
    return undefined;
  }
  const result = extensionLifecycleResult(outcome.steps[0], outcome.actions[0]);
  return result ? { outcome, result } : undefined;
}

function inspectionAlreadySatisfiesLifecycleAction(inspection, mutationAction) {
  const action = mutationAction?.replace(/^ods\.extensions\./, "");
  return inspection?.result?.outcome === "ready" &&
    EXTENSION_LIFECYCLE_SUCCESS.get(action)?.has(inspection.result.currentStatus) === true;
}

function extensionLifecycleEvidenceText(requiredActions, terminalJobs) {
  const mutationActions = [...requiredActions].filter(
    (action) => action.startsWith("ods.extensions.") && action !== "ods.extensions.inspect"
  );
  if (
    requiredActions.size !== 2 ||
    !requiredActions.has("ods.extensions.inspect") ||
    mutationActions.length !== 1
  ) {
    return undefined;
  }
  const inspectionOutcome = lifecycleOutcomeForAction(terminalJobs, "ods.extensions.inspect");
  if (!inspectionOutcome) return undefined;
  if (inspectionOutcome.status !== "succeeded") {
    const plan = typeof inspectionOutcome.planHash === "string" && SHA256.test(inspectionOutcome.planHash)
      ? ` Plan SHA-256: ${inspectionOutcome.planHash}.`
      : "";
    return `Pixel's ODS extension inspection job reached terminal status ${inspectionOutcome.status}. No lifecycle change was accepted. Job: ${inspectionOutcome.jobId}.${plan}`;
  }
  const inspection = parsedLifecycleOutcome(terminalJobs, "ods.extensions.inspect");
  if (!inspection || !["ready", "blocked"].includes(inspection.result.outcome)) return undefined;
  if (inspection.result.outcome === "blocked") {
    if (terminalJobs.size !== 1) return undefined;
    return [
      OPERATIONS_EXTENSION_LIFECYCLE_EVIDENCE_PREFIX,
      `- Extension: \`${inspection.result.extensionId}\`.`,
      `- Inspection: blocked in state \`${inspection.result.currentStatus}\`; no change or external effect occurred.`,
      `- Missing required configuration keys: ${inspection.result.missingConfiguration.map((key) => `\`${key}\``).join(", ")}.`,
      `- Authority: ${EXTENSION_LIFECYCLE_BOUNDARY}`,
      `- Inspection job: \`${inspection.outcome.jobId}\`.`,
    ].join("\n");
  }
  const mutationAction = mutationActions[0];
  const mutationOutcome = lifecycleOutcomeForAction(terminalJobs, mutationAction);
  if (!mutationOutcome) {
    if (
      terminalJobs.size !== 1 ||
      !inspectionAlreadySatisfiesLifecycleAction(inspection, mutationAction)
    ) {
      return undefined;
    }
    const requestedAction = mutationAction.replace(/^ods\.extensions\./, "");
    return [
      OPERATIONS_EXTENSION_LIFECYCLE_EVIDENCE_PREFIX,
      `- Extension: \`${inspection.result.extensionId}\`.`,
      `- Requested action: \`${requestedAction}\`; verified outcome: already satisfied.`,
      `- State: \`${inspection.result.currentStatus}\`; no mutation or external effect was needed.`,
      `- Missing required configuration keys: ${inspection.result.missingConfiguration.length ? inspection.result.missingConfiguration.map((key) => `\`${key}\``).join(", ") : "none"}.`,
      `- Authority: ${EXTENSION_LIFECYCLE_BOUNDARY}`,
      `- Inspection job: \`${inspection.outcome.jobId}\`.`,
    ].join("\n");
  }
  if (mutationOutcome.status === "awaiting-approval") {
    return `Pixel prepared the exact ${mutationAction} plan for extension ${inspection.result.extensionId}, but external approval is required. No lifecycle change was executed. Job: ${mutationOutcome.jobId}. Plan SHA-256: ${mutationOutcome.planHash}.`;
  }
  if (mutationOutcome.status !== "succeeded") {
    return `Pixel's ODS extension lifecycle job reached terminal status ${mutationOutcome.status}. No successful lifecycle result was accepted. Job: ${mutationOutcome.jobId}.`;
  }
  const mutation = parsedLifecycleOutcome(terminalJobs, mutationAction);
  if (!mutation || mutation.result.extensionId !== inspection.result.extensionId) return undefined;
  const result = mutation.result;
  const lines = [
    OPERATIONS_EXTENSION_LIFECYCLE_EVIDENCE_PREFIX,
    `- Extension: \`${result.extensionId}\`.`,
    `- Requested action: \`${result.action}\`; verified outcome: \`${result.outcome}\`.`,
    `- State: \`${result.previousStatus}\` -> \`${result.currentStatus}\`.`,
    `- Change observed: ${result.changed ? "yes" : "no"}; external effect attempted: ${result.externalEffectOccurred ? "yes" : "no"}.`,
    `- Missing required configuration keys: ${result.missingConfiguration.length ? result.missingConfiguration.map((key) => `\`${key}\``).join(", ") : "none"}.`,
    `- Rollback: ${result.rollback.attempted ? (result.rollback.succeeded ? "succeeded" : "failed") : "not required"}.`,
    `- Authority: ${EXTENSION_LIFECYCLE_BOUNDARY}`,
    `- Inspection job: \`${inspection.outcome.jobId}\`; lifecycle job: \`${mutation.outcome.jobId}\`.`,
  ];
  return lines.join("\n");
}

function operationsEvidenceText(
  requiredActions,
  terminalJobs,
  odsAppsProjection = undefined,
  odsStatusProjection = undefined
) {
  if (!(requiredActions instanceof Set) || requiredActions.size === 0) return undefined;
  if (requiredActions.size === 1 && requiredActions.has("raw-shell")) {
    if (!(terminalJobs instanceof Map) || terminalJobs.size !== 1) return undefined;
    const outcome = [...terminalJobs.values()][0];
    if (
      outcome.actions?.length !== 1 ||
      outcome.actions[0]?.target !== "ods-host" ||
      outcome.actions[0]?.action !== "raw-shell"
    ) {
      return undefined;
    }
    if (outcome.status === "awaiting-approval") {
      return outcome.approvalRequired === true &&
        typeof outcome.planHash === "string" && SHA256.test(outcome.planHash)
        ? `Pixel prepared a protected ODS host command plan, but external approval is required. No command was executed. Job: ${outcome.jobId}. Plan SHA-256: ${outcome.planHash}.`
        : undefined;
    }
    if (outcome.status !== "succeeded") {
      const plan = typeof outcome.planHash === "string" && SHA256.test(outcome.planHash)
        ? ` Plan SHA-256: ${outcome.planHash}.`
        : "";
      return `Pixel's protected ODS host command job reached terminal status ${outcome.status}. No successful command result was accepted. Job: ${outcome.jobId}.${plan}`;
    }
    if (
      outcome.approvalRequired !== true ||
      typeof outcome.planHash !== "string" ||
      !SHA256.test(outcome.planHash) ||
      outcome.steps?.length !== 1
    ) {
      return undefined;
    }
    const step = outcome.steps[0];
    if (
      step.target !== "ods-host" ||
      step.action !== "raw-shell" ||
      step.exitCode !== 0 ||
      typeof step.stdout !== "string" ||
      typeof step.stderr !== "string" ||
      step.stdout.length > 64 * 1024 ||
      step.stderr.length > 64 * 1024 ||
      !step.outputTruncated ||
      typeof step.outputTruncated !== "object" ||
      step.outputTruncated.stdout !== false ||
      step.outputTruncated.stderr !== false ||
      !Array.isArray(step.riskSignals) ||
      !Number.isFinite(step.durationSeconds) ||
      step.durationSeconds < 0
    ) {
      return undefined;
    }
    return [
      OPERATIONS_HOST_COMMAND_EVIDENCE_PREFIX,
      "- Target: `ods-host`; action: `raw-shell`; exit code: `0`.",
      `- Duration: ${step.durationSeconds} seconds.`,
      `- Standard output (untrusted command output): ${JSON.stringify(step.stdout)}.`,
      `- Standard error (untrusted command output): ${JSON.stringify(step.stderr)}.`,
      `- Broker job: \`${outcome.jobId}\`; plan SHA-256: \`${outcome.planHash}\`.`,
      "- Authority: the broker executed only after an external owner approval matched this immutable plan hash.",
    ].join("\n");
  }
  const hostActions = new Set([
    "host.identity", "host.kernel", "host.architecture", "host.platform", "host.os-release", "host.uptime",
    "host.processes", "host.services", "host.cpu", "host.gpu", "host.memory", "host.storage",
    "host.network-addresses", "host.network-routes", "host.listening-ports", "host.tailscale",
    "host.network-peer",
  ]);
  if ([...requiredActions].every((action) => hostActions.has(action))) {
    return operationsHostEvidenceText(
      requiredActions,
      terminalJobs,
      odsAppsProjection,
      odsStatusProjection
    );
  }
  if (requiredActions.size === 1 && requiredActions.has("ods.extensions.list")) {
    if (!(terminalJobs instanceof Map) || terminalJobs.size !== 1) return undefined;
    const outcome = [...terminalJobs.values()][0];
    if (outcome.status !== "succeeded") {
      const plan = typeof outcome.planHash === "string" && SHA256.test(outcome.planHash)
        ? ` Plan SHA-256: ${outcome.planHash}.`
        : "";
      return `Pixel's live ODS extension inventory job reached terminal status ${outcome.status}. No extension-state result or external effect was accepted. Job: ${outcome.jobId}.${plan}`;
    }
    if (outcome.steps.length !== 1 || outcome.actions.length !== 1) return undefined;
    const result = extensionInventoryResult(outcome.steps[0], outcome.actions[0]);
    if (!result) return undefined;
    const lines = [
      OPERATIONS_EXTENSION_INVENTORY_EVIDENCE_PREFIX,
      `- Catalog total: ${result.summary.total}; installed: ${result.summary.installed}; enabled: ${result.summary.enabled}; CLI-installed: ${result.summary.cliInstalled}.`,
      `- Degraded or inactive installed state: disabled ${result.summary.disabled}; stopped ${result.summary.stopped}; unhealthy ${result.summary.unhealthy}; installing ${result.summary.installing}; setting up ${result.summary.settingUp}; error ${result.summary.error}.`,
      `- Not installed: ${result.summary.notInstalled}; incompatible: ${result.summary.incompatible}.`,
    ];
    const installed = result.extensions.filter(
      (entry) => !["not_installed", "incompatible"].includes(entry.status)
    );
    if (installed.length) {
      for (const entry of installed) {
        lines.push(
          `- \`${entry.name}\` (\`${entry.id}\`): status \`${entry.status}\`; source \`${entry.source}\`; category \`${entry.category}\`; installable ${entry.installable ? "yes" : "no"}.`
        );
      }
    } else {
      lines.push("- Installed extensions: none.");
    }
    if (odsAppsProjection) {
      const apps = odsAppsProjection.apps
        .map(({ name, status }) => `\`${name}\` (${status})`)
        .join(", ");
      lines.push(
        `- ODS container projection: ${odsAppsProjection.online_app_count} of ${odsAppsProjection.app_count} allowlisted ODS application containers online; ${apps || "none reported"}.`
      );
    }
    if (odsStatusProjection) {
      const availability = odsStatusProjection.ingress_ready && odsStatusProjection.gateway_reachable
        ? "available"
        : "unavailable";
      const runtime = odsStatusProjection.runtime
        ? `model \`${odsStatusProjection.runtime.model}\`; context ${odsStatusProjection.runtime.context_length} tokens`
        : "model unavailable; context unavailable";
      lines.push(
        `- ODS runtime projection: ${runtime}; Pixel ${availability}; ODS version \`${odsStatusProjection.ods_version}\`; ${odsStatusProjection.online_app_count} of ${odsStatusProjection.app_count} projected containers online.`
      );
    }
    lines.push(`- Authority: ${EXTENSION_INVENTORY_BOUNDARY}`);
    lines.push(`- Broker job: \`${outcome.jobId}\`.`);
    return lines.join("\n");
  }
  if (requiredActions.has("ods.extensions.inspect")) {
    return extensionLifecycleEvidenceText(requiredActions, terminalJobs);
  }
  if (requiredActions.size !== 1 || !requiredActions.has("ods.extensions.search")) {
    return undefined;
  }
  if (!(terminalJobs instanceof Map) || terminalJobs.size !== 1) return undefined;
  const outcome = [...terminalJobs.values()][0];
  if (outcome.status !== "succeeded") {
    const plan = typeof outcome.planHash === "string" && SHA256.test(outcome.planHash)
      ? ` Plan SHA-256: ${outcome.planHash}.`
      : "";
    return `Pixel's ODS extension catalog job reached terminal status ${outcome.status}. No catalog result or external effect was accepted. Job: ${outcome.jobId}.${plan}`;
  }
  if (outcome.steps.length !== 1 || outcome.actions.length !== 1) return undefined;
  const result = extensionCatalogResult(
    outcome.steps[0],
    outcome.actions[0]?.parameters
  );
  if (!result) return undefined;
  const lines = [
    OPERATIONS_EXTENSION_CATALOG_EVIDENCE_PREFIX,
    `- Query: \`${result.query}\``,
    `- Catalog: ${result.totalMatches} match(es) among ${result.totalCatalog}; results truncated: ${result.truncated ? "yes" : "no"}.`,
  ];
  const compactList = (items, maximum = 8) => {
    const visible = items.slice(0, maximum).map((item) => `\`${item}\``).join(", ");
    return `${visible || "none"}${items.length > maximum ? `, plus ${items.length - maximum} more` : ""}`;
  };
  if (result.matches.length) {
    for (const [index, match] of result.matches.entries()) {
      const description = match.description.length > 320
        ? `${match.description.slice(0, 317)}...`
        : match.description;
      lines.push(`- Match ${index + 1}: \`${match.name}\` (\`${match.id}\`).`);
      lines.push(`  - What it does: ${JSON.stringify(description)}.`);
      lines.push(`  - Category: \`${match.category}\`; GPU backends: ${compactList(match.gpuBackends)}.`);
      lines.push(`  - Dependencies: ${compactList(match.dependsOn)}.`);
      lines.push(`  - Required configuration keys: ${compactList(match.requiredConfiguration)}.`);
      lines.push(`  - Optional configuration keys: ${compactList(match.optionalConfiguration)}.`);
    }
  } else {
    lines.push("- Matches: none.");
  }
  lines.push("- Installed/enabled state: not included in this read-only catalog receipt; Pixel will inspect one exact extension ID before any lifecycle action.");
  lines.push("- Authority: read-only catalog projection; no installation or configuration authority.");
  lines.push(`- Broker job: \`${outcome.jobId}\`.`);
  return lines.join("\n");
}

function webFetchWasTruncated(event) {
  if (event?.error) return false;
  const details = event?.result?.details;
  if (
    details &&
    typeof details === "object" &&
    !Array.isArray(details) &&
    details.truncated === true &&
    details.status === 200
  ) {
    return true;
  }
  const content = event?.result?.content;
  if (!Array.isArray(content)) return false;
  for (const part of content) {
    if (!part || typeof part !== "object" || typeof part.text !== "string") continue;
    try {
      const parsed = JSON.parse(part.text);
      if (parsed?.truncated === true && parsed?.status === 200) return true;
    } catch {
      // Non-JSON tool text cannot establish a successful truncated fetch.
    }
  }
  return false;
}

function canonicalWebFetchSucceeded(event) {
  if (toolCallFailed(event)) return false;
  const statuses = [];
  const details = event?.result?.details;
  if (details && typeof details === "object" && !Array.isArray(details)) {
    statuses.push(details.status);
  }
  const content = event?.result?.content;
  if (Array.isArray(content)) {
    for (const part of content) {
      if (!part || typeof part !== "object" || typeof part.text !== "string") continue;
      try {
        statuses.push(JSON.parse(part.text)?.status);
      } catch {
        // Plain-text source content has no independently inspectable HTTP status.
      }
    }
  }
  return statuses.some(
    (status) => Number.isInteger(status) && status >= 200 && status < 300
  );
}

function runIdentity(event, context) {
  const runId = context?.runId ?? event?.runId;
  const sessionId = context?.sessionId;
  return {
    runId: typeof runId === "string" && runId ? runId : undefined,
    sessionId: typeof sessionId === "string" && sessionId ? sessionId : undefined,
  };
}

export function urlTargetsNonPublicAddress(raw) {
  if (typeof raw !== "string" || !raw) return false;
  try {
    const target = new URL(raw);
    if (!new Set(["http:", "https:"]).has(target.protocol)) return true;
    if (target.username || target.password) return true;
    const hostname = target.hostname
      .replace(/^\[|\]$/g, "")
      .replace(/\.+$/, "")
      .toLowerCase();
    if (!hostname || isIP(hostname)) return true;
    return (
      hostname === "localhost" ||
      hostname.endsWith(".localhost") ||
      hostname.endsWith(".local") ||
      hostname.endsWith(".internal") ||
      !hostname.includes(".")
    );
  } catch {
    // Let the built-in tool produce its normal validation error for malformed
    // public URLs. This preflight exists only to make obvious private targets
    // a clean, conversational denial before the fetch runtime aborts the run.
    return false;
  }
}

export function textRequestsPrivateUrlAccess(text) {
  if (typeof text !== "string" || !text) return false;
  const urls = text.match(/https?:\/\/[^\s"'`|;&<>]+/gi) ?? [];
  if (!urls.some((url) => urlTargetsNonPublicAddress(url.replace(/[),.\]}]+$/, "")))) {
    return false;
  }
  if (
    ARTIFACT_DRAFT_PREFIX.test(text) &&
    ARTIFACT_NOUN.test(text) &&
    !FOLLOWUP_PRIVATE_ACCESS.test(text)
  ) {
    return false;
  }
  return /\b(?:access|browse|call|check|connect|download|fetch|inspect|open|query|read|request|retrieve|summarize|test|visit)\b|\btell\s+me\b|\bwhat(?:'s|\s+is)\s+(?:at|on)\b/i.test(
    text
  );
}

function messageContentText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && typeof part === "object" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n");
}

const CURRENT_MESSAGE_WRAPPER = "[Current message - respond to this]\nUser:";

function unwrapCurrentUserText(text) {
  if (typeof text !== "string" || !text) return "";
  const normalized = text.replace(/\r\n/g, "\n");
  const first = normalized.indexOf(CURRENT_MESSAGE_WRAPPER);
  if (first === -1) return normalized;
  // The dashboard/OpenClaw compatibility prompt uses exactly one trusted
  // current-message delimiter after its history transcript. If untrusted user
  // content introduces another delimiter, keep the complete prompt so every
  // safety classifier fails conservatively instead of accepting a forged tail.
  if (
    normalized.indexOf(CURRENT_MESSAGE_WRAPPER, first + CURRENT_MESSAGE_WRAPPER.length) !== -1
  ) {
    return normalized;
  }
  return normalized.slice(first + CURRENT_MESSAGE_WRAPPER.length).trimStart();
}

function currentUserText(messages, prompt = undefined) {
  if (typeof prompt === "string" && prompt) return unwrapCurrentUserText(prompt);
  if (!Array.isArray(messages)) return "";
  const userMessage = [...messages]
    .reverse()
    .find((message) => message && message.role === "user");
  return unwrapCurrentUserText(messageContentText(userMessage?.content));
}

function currentOwnerIntentText(messages, prompt = undefined) {
  const currentText = currentUserText(messages, prompt);
  const deliveryContractIndex = currentText.lastIndexOf(
    "\n\n[ODS Pixel delivery requirement:"
  );
  return deliveryContractIndex >= 0
    ? currentText.slice(0, deliveryContractIndex)
    : currentText;
}

function explicitlyRejectsOdsTool(text, toolPattern) {
  const actionNegation = new RegExp(
    `\\b(?:do\\s+not|don't|never|must\\s+not|should\\s+not)\\s+` +
      `(?:call|invoke|query|run|use)\\b[^.!?;\\n]{0,80}\\b(?:${toolPattern})\\b`,
    "i"
  );
  const omissionNegation = new RegExp(
    `\\b(?:avoid|skip|without)\\b[^.!?;\\n]{0,80}\\b(?:${toolPattern})\\b`,
    "i"
  );
  const directNegation = new RegExp(`\\bnot\\s+(?:the\\s+)?(?:${toolPattern})\\b`, "i");
  return text
    .split(/[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)/i)
    .some(
      (clause) =>
        actionNegation.test(clause) ||
        omissionNegation.test(clause) ||
        directNegation.test(clause)
    );
}

function explicitlyExcludesHostObservation(text, facetPattern) {
  const exclusion = new RegExp(
    `\\b(?:` +
      `(?:do\\s+not|don't|never|must\\s+not|should\\s+not)\\s+` +
        `(?:repeat|restate|include|report|show|list|add|substitute|reveal|disclose|expose|inspect|check|observe|measure|probe)|` +
      `(?:avoid|skip|omit|exclude)|` +
      `without(?:\\s+(?:repeating|restating|including|reporting|showing|listing|adding))?` +
    `)\\b[^.!?;\\n]{0,120}\\b(?:${facetPattern})\\b`,
    "i"
  );
  return exclusion.test(text);
}

export function userMessageAuthorizesRecursiveDelete(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  if (!text) return false;
  return /\b(?:delete|remove|erase|wipe)\s+(?:the\s+)?(?:directory|folder|tree|workspace\s+tree)?\s*(?:at\s+)?["'`]?\/workspace(?:\/[A-Za-z0-9._/-]+)?["'`]?\s+(?:recursively|and\s+(?:all\s+)?(?:its\s+)?contents)\b/i.test(
    text
  );
}

function requestsRecursiveForcedDelete(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return false;
  const command = params.command;
  if (typeof command !== "string" || !command.trim()) return false;
  const invocations = command.matchAll(/(?:^|[;&|]\s*)rm\s+((?:(?:--[A-Za-z-]+|-[A-Za-z]+)\s+)+)/gim);
  for (const match of invocations) {
    const options = match[1];
    const recursive = /--recursive\b/i.test(options) || /(?:^|\s)-[A-Za-z]*[rR][A-Za-z]*(?:\s|$)/.test(options);
    const forced = /--force\b/i.test(options) || /(?:^|\s)-[A-Za-z]*f[A-Za-z]*(?:\s|$)/.test(options);
    if (recursive && forced) return true;
  }
  return false;
}

function execLaunchesWorkspaceServer(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return false;
  const command = params.command;
  if (typeof command !== "string" || !command.trim()) return false;
  return (
    /\bpython(?:3(?:\.\d+)?)?\s+-m\s+http\.server\b/i.test(command) ||
    /\b(?:npx|pnpm\s+dlx|bunx)\s+(?:--yes\s+)?(?:vite|serve|http-server)\b/i.test(command) ||
    /\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve|preview)\b/i.test(command) ||
    /\b(?:vite|next\s+dev|astro\s+dev|hugo\s+server|jekyll\s+serve)\b/i.test(command)
  );
}

function workspacePreviewMkdirDirectory(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return undefined;
  const command = params.command;
  if (typeof command !== "string" || !command.trim()) return undefined;
  const match = command.trim().match(
    /^mkdir\s+-p\s+(?:--\s+)?(?:"([^"\r\n]+)"|'([^'\r\n]+)'|([^\s;&|><`$()]+))$/i
  );
  if (!match) return undefined;
  const rawDirectory = match[1] ?? match[2] ?? match[3];
  const absoluteWorkspacePath = rawDirectory.startsWith("/workspace/");
  if (
    !absoluteWorkspacePath &&
    normalizeExecWorkdir(params.workdir ?? ".") !== "."
  ) {
    return undefined;
  }
  const directory = normalizeWorkspaceFilePath(rawDirectory);
  const parts = typeof directory === "string" ? directory.split("/") : [];
  if (
    parts.length === 0 ||
    parts.length > 16 ||
    parts.some(
      (part) =>
        ["", ".", ".."].includes(part) || !WORKSPACE_PATH_COMPONENT.test(part)
    )
  ) {
    return undefined;
  }
  return directory;
}

export function userMessageOdsToolRequirements(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  if (!text) return [];
  const genericOdsTool = String.raw`ODS\s+(?:read-only\s+)?(?:projection|tool)s?`;
  const statusTool =
    String.raw`(?:pixel_ods_status|ODS\s+(?:health|model|status)(?:\s+(?:projection|tool)s?)?)`;
  const appsTool =
    String.raw`(?:pixel_ods_apps_list|ODS\s+(?:app|application)s?(?:\s+(?:list|projection|tool)s?)?)`;
  const rejectsStatus = explicitlyRejectsOdsTool(
    text,
    `(?:${genericOdsTool}|${statusTool})`
  );
  const rejectsApps = explicitlyRejectsOdsTool(text, `(?:${genericOdsTool}|${appsTool})`);
  const requirements = [];
  const asksAvailability =
    /\b(?:is|are)\s+(?:the\s+)?(?:ODS|Pixel)\b.{0,32}\bavailable\b/i.test(text) ||
    /\b(?:ODS|Pixel)\b.{0,32}\b(?:is|are)\s+available\b/i.test(text);
  const asksDockerStatus =
    /\bdocker\b[^.!?;\n]{0,48}\b(?:available|health|healthy|online|running|status|working)\b/i.test(text) ||
    /\b(?:available|health|healthy|online|running|status|working)\b[^.!?;\n]{0,48}\bdocker\b/i.test(text);
  const asksLiveServiceState = /\bODS\b/i.test(text) &&
    /\bservices?\b[^.!?;\n]{0,80}\b(?:health|healthy|online|running|status)\b/i.test(text);
  const asksModelState = text
    .split(/[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)/i)
    .some((clause) => {
      const modelState =
        /\b(?:active|current|loaded|running)\s+(?:ODS\s+|Pixel\s+)?model\b/i.test(clause) ||
        /\b(?:ODS\s+|Pixel\s+)?model\s+(?:is\s+)?(?:currently\s+)?(?:active|current|loaded|running)\b/i.test(
          clause
        );
      const asks =
        /\b(?:what|which|identify|name|report|tell|show|inspect|check|verify)\b/i.test(clause) ||
        clause.trimEnd().endsWith("?");
      return modelState && asks;
    });
  const asksStatus =
    !rejectsStatus &&
    (/\bpixel_ods_status\b/i.test(text) ||
      asksModelState ||
      /\b(?:ODS|Pixel)\b.{0,80}\b(?:health|status|online|service count|services online|context (?:window|length|limit))\b/i.test(
        text
      ) ||
      /\b(?:health|status|online|service count|services online|context (?:window|length|limit))\b.{0,80}\b(?:ODS|Pixel)\b/i.test(
        text
      ) ||
      asksAvailability ||
      asksDockerStatus ||
      asksLiveServiceState);
  const asksNamedServiceInventory =
    /\b(?:which|what|list|show|inspect|audit|inventory|report|tell\s+me)\b[^.!?;\n]{0,120}\b(?:ODS\s+)?services?\b[^.!?;\n]{0,120}\b(?:installed|enabled|disabled|healthy|unhealthy|running|stopped|status)\b/i.test(text) ||
    /\b(?:ODS\s+)?services?\b[^.!?;\n]{0,120}\b(?:installed|enabled|disabled|healthy|unhealthy|running|stopped|status)\b/i.test(text);
  const asksApps =
    !rejectsApps &&
    (/\bpixel_ods_apps_list\b/i.test(text) ||
      /\bODS\b.{0,80}\b(?:apps?|applications?)\b/i.test(text) ||
      /\b(?:apps?|applications?)\b.{0,80}\bODS\b/i.test(text) ||
      /\bODS(?:\s+(?:app|application|service)s?)?\s+(?:links?|URLs?)\b/i.test(text) ||
      /\bconfigured\s+(?:app\s+)?(?:links?|URLs?)\b.{0,48}\bODS\b/i.test(text) ||
      (/\b(?:n8n|Open\s*WebUI|Perplexica|SearXNG|LiteLLM|Hermes)\b/i.test(text) &&
        /\b(?:configured|link|URL|where|address)\b/i.test(text)) ||
      asksNamedServiceInventory);
  if (asksStatus) requirements.push("pixel_ods_status");
  if (asksApps) requirements.push("pixel_ods_apps_list");
  return requirements;
}

export function userMessageRequiresOperations(messages, prompt = undefined) {
  return userMessageOperationsRequirements(messages, prompt).required;
}

export function userMessageRequestsExtensionCatalog(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  if (!text) return false;
  const requestsLiveState =
    /\b(?:which|what|list|show|inspect|audit|inventory|report)\b[^.!?;\n]{0,120}\b(?:ODS\s+)?extensions?\b[^.!?;\n]{0,120}\b(?:installed|enabled|disabled|healthy|unhealthy|running|stopped|status|state|source|core|optional)\b/i.test(text) ||
    /\b(?:ODS\s+)?extensions?\b[^.!?;\n]{0,120}\b(?:installed|enabled|disabled|healthy|unhealthy|running|stopped|status|state|source|core|optional)\b/i.test(text);
  const explicitCatalog =
    /\bods\.extensions\.search\b/i.test(text) ||
    /\b(?:extension|extensions)\s+catalog\b/i.test(text) ||
    /\b(?:search|browse|find)\b[^.!?;\n]{0,80}\b(?:installable|supported|available)?\s*(?:ODS\s+)?extensions?\b/i.test(text) ||
    /\binstallable\b[^.!?;\n]{0,80}\b(?:ODS\s+)?extensions?\b/i.test(text);
  if (requestsLiveState && !explicitCatalog) return false;
  return (
    explicitCatalog ||
    /\b(?:installable|supported|available)\b.{0,80}\b(?:ODS\s+)?extensions?\b/i.test(text) ||
    /\b(?:ODS\s+)?extensions?\b.{0,80}\b(?:installable|supported|available)\b/i.test(text)
  );
}

const EXTENSION_INVENTORY_BOUNDARY =
  "Read-only live ODS extension inventory; it exposes only bounded status metadata and grants no installation, configuration, credential, Docker, or shell authority.";

function extensionInventoryResult(step, submittedAction) {
  if (
    !step ||
    step.target !== "ods-host" ||
    step.action !== "ods.extensions.list" ||
    submittedAction?.action !== "ods.extensions.list" ||
    !exactKeys(submittedAction?.parameters ?? {}, []) ||
    step.stderr.trim() ||
    step.riskSignals.length > 0 ||
    typeof step.stdout !== "string" ||
    step.stdout.length > 256 * 1024
  ) {
    return undefined;
  }
  let value;
  try {
    value = JSON.parse(step.stdout);
  } catch {
    return undefined;
  }
  const summaryKeys = [
    "total", "installed", "enabled", "cliInstalled", "disabled", "stopped",
    "unhealthy", "installing", "settingUp", "error", "notInstalled", "incompatible",
  ];
  if (
    !exactKeys(value, ["schemaVersion", "kind", "outcome", "summary", "extensions", "boundary"]) ||
    value.schemaVersion !== 1 ||
    value.kind !== "ods-pixel-extension-inventory" ||
    value.outcome !== "succeeded" ||
    value.boundary !== EXTENSION_INVENTORY_BOUNDARY ||
    !exactKeys(value.summary, summaryKeys) ||
    !Array.isArray(value.extensions) ||
    value.extensions.length > 256
  ) {
    return undefined;
  }
  for (const key of summaryKeys) {
    if (!Number.isInteger(value.summary[key]) || value.summary[key] < 0 || value.summary[key] > 256) {
      return undefined;
    }
  }
  if (value.summary.total !== value.extensions.length) return undefined;
  const identifiers = new Set();
  const extensions = [];
  for (const entry of value.extensions) {
    if (!exactKeys(entry, ["id", "name", "category", "status", "source", "installable"])) {
      return undefined;
    }
    const id = boundedCatalogString(entry.id, /^[a-z0-9][a-z0-9._-]{0,63}$/, 64);
    const name = boundedCatalogString(entry.name, undefined, 128);
    const category = boundedCatalogString(entry.category, undefined, 64);
    if (
      !id || !name || !category || identifiers.has(id) ||
      !EXTENSION_LIFECYCLE_STATUSES.has(entry.status) ||
      !["core", "user", "library"].includes(entry.source) ||
      typeof entry.installable !== "boolean"
    ) {
      return undefined;
    }
    identifiers.add(id);
    extensions.push({
      id,
      name,
      category,
      status: entry.status,
      source: entry.source,
      installable: entry.installable,
    });
  }
  const statusKeys = new Map([
    ["enabled", "enabled"],
    ["cli_installed", "cliInstalled"],
    ["disabled", "disabled"],
    ["stopped", "stopped"],
    ["unhealthy", "unhealthy"],
    ["installing", "installing"],
    ["setting_up", "settingUp"],
    ["error", "error"],
    ["not_installed", "notInstalled"],
    ["incompatible", "incompatible"],
  ]);
  for (const [status, key] of statusKeys) {
    if (value.summary[key] !== extensions.filter((entry) => entry.status === status).length) {
      return undefined;
    }
  }
  const installedStatuses = new Set([
    "enabled", "cli_installed", "disabled", "stopped", "unhealthy", "installing",
    "setting_up", "error",
  ]);
  if (value.summary.installed !== extensions.filter((entry) => installedStatuses.has(entry.status)).length) {
    return undefined;
  }
  return { ...value, extensions };
}

export function userMessageRequestsExtensionInventory(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  if (!text || userMessageExtensionLifecycleIntent(messages, prompt)) return false;
  const extensionState =
    /\b(?:installed|enabled|disabled|healthy|unhealthy|running|stopped|status|state|source|core|optional)\b/i;
  const inventoryIntent =
    /\b(?:which|what|list|show|inspect|audit|inventory|report|tell\s+me)\b/i;
  return /\b(?:ODS\s+)?extensions?\b/i.test(text) && extensionState.test(text) && inventoryIntent.test(text);
}

export function userMessageExtensionCatalogExactQuery(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  if (!text || !userMessageRequestsExtensionCatalog(messages, prompt)) return undefined;
  const quoted = text.match(/\bquery\s*(?:is|[:=])?\s*([`"'])([^\r\n]{1,80})\1/i);
  const exact = quoted ?? text.match(/\bquery\s+(?:is\s+)?(.{1,80}?)\s+exactly(?:\s+as\s+written)?(?:[.!?]|$)/i);
  const value = exact ? (quoted ? exact[2] : exact[1]).trim() : "";
  if (!value || value.length > 80 || /[\u0000-\u001f\u007f]/.test(value)) return undefined;
  return value;
}

export function userMessageExtensionLifecycleIntent(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  if (!text) return undefined;
  const match = text.match(
    /\b(install|enable|disable|remove|uninstall)\s+(?:the\s+)?(?:(?:installed|existing|enabled|disabled)\s+)?(?:ODS\s+)?extension\s+(?:(?:with\s+)?(?:the\s+)?(?:exact\s+)?id\s+)?[`"']?([a-z0-9](?:[a-z0-9_-]|\.(?=[a-z0-9])){0,63})(?![a-z0-9_-]|\.(?=[a-z0-9]))[`"']?/i
  );
  if (!match) return undefined;
  const requested = match[1].toLowerCase();
  return {
    action: requested === "uninstall" ? "remove" : requested,
    serviceId: match[2].toLowerCase(),
  };
}

export function userMessageOperationsContinuation(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  if (
    !text ||
    !/\b(?:check|continue|follow\s*up|inspect|query|report|status|verify)\b/i.test(text)
  ) {
    return undefined;
  }
  const jobIds = [
    ...new Set([...text.matchAll(/\bops-[0-9]{13}-[a-f0-9]{12}\b/gi)].map((match) => match[0].toLowerCase())),
  ];
  const planHashes = [
    ...new Set(
      [...text.matchAll(/\bplan\s+sha(?:-?256)?\s*(?::|=|is)?\s*[`"']?([a-f0-9]{64})[`"']?/gi)]
        .map((match) => match[1].toLowerCase())
    ),
  ];
  if (jobIds.length !== 1 || planHashes.length !== 1) return undefined;
  return { jobId: jobIds[0], planHash: planHashes[0] };
}

const DEFAULT_NETWORK_PEER_PORTS = Object.freeze([22, 80, 443, 3389, 5985, 5986]);

export function userMessageNetworkPeerRequest(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (
    !text ||
    /https?:\/\//i.test(text) ||
    /(?:^|\s)[0-9a-f:.]+\/[0-9]{1,3}\b/i.test(text) ||
    !/\b(?:LAN|local\s+network|Tailscale|network|reachable|reachability|resolve|ping|probe|connectivity)\b/i.test(text) ||
    !/\b(?:check|inspect|probe|ping|resolve|test|verify|reachable|reachability|connectivity|online)\b/i.test(text)
  ) {
    return undefined;
  }
  const patterns = [
    /^\s*[`"']?([A-Za-z0-9][A-Za-z0-9.:-]{0,252})[`"']?\s+is\s+(?:an?\s+)?(?:Windows|Linux|macOS|Mac)?\s*(?:computer|machine|host|device)\b/i,
    /\b(?:computer|machine|host|device|peer)\s+(?:named|called)\s+[`"']?([A-Za-z0-9][A-Za-z0-9.:-]{0,252})[`"']?/i,
    /\b(?:ping|probe|resolve)\s+[`"']?([A-Za-z0-9][A-Za-z0-9.:-]{0,252})[`"']?/i,
    /\b(?:reachability|connectivity)\s+(?:of|to|for)\s+[`"']?([A-Za-z0-9][A-Za-z0-9.:-]{0,252})[`"']?/i,
  ];
  const peer = patterns.map((pattern) => text.match(pattern)?.[1]).find(Boolean);
  if (
    !peer ||
    /^(?:this|the|my|our|local|ODS|Pixel|computer|machine|host|system|network|Tailscale)$/i.test(peer) ||
    peer.includes("..") ||
    peer.split(".").some((label) => label.startsWith("-") || label.endsWith("-"))
  ) {
    return undefined;
  }
  const escapedPeer = peer.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (
    new RegExp(
      `\\b(?:do\\s+not|don't|never|must\\s+not|should\\s+not)\\b[^.!?;\\n]{0,64}` +
        `\\b(?:contact|access|inspect|query|connect|ping|probe|resolve)?\\s*${escapedPeer}\\b`,
      "i"
    ).test(text)
  ) {
    return undefined;
  }
  if (isIP(peer) && !privatePeerAddressScope(peer)) return undefined;
  const ports = [];
  const explicit = text.match(
    /\bports?\s+((?:[0-9]{1,5}(?:\s*(?:,|and)\s*[0-9]{1,5}){0,7}))/i
  )?.[1] ?? "";
  for (const value of explicit.match(/\b[0-9]{1,5}\b/g) ?? []) {
    const port = Number(value);
    if (port >= 1 && port <= 65535 && !ports.includes(port)) ports.push(port);
    if (ports.length === 8) break;
  }
  if (/\bSSH\b/i.test(text) && !ports.includes(22)) ports.push(22);
  if (/\b(?:RDP|Remote Desktop)\b/i.test(text) && !ports.includes(3389)) ports.push(3389);
  if (/\bWinRM\b/i.test(text)) {
    for (const port of [5985, 5986]) if (!ports.includes(port)) ports.push(port);
  }
  return {
    peer,
    ports: (ports.length ? ports : [...DEFAULT_NETWORK_PEER_PORTS]).slice(0, 8),
  };
}

export function userMessageOperationsRequirements(messages, prompt = undefined) {
  // Pixel Edge appends trusted delivery/routing guidance beside the owner
  // message for small local models. That guidance is not owner intent: words
  // such as "route" must not silently expand a bounded host request into
  // network-route/listener work.
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return { required: false, actions: [] };
  const explicitOperations =
    /\b(?:use|using|via|through|with)\b.{0,48}\b(?:Pixel\s+)?Operations(?:\s+(?:Broker|capabilit(?:y|ies)|tools?))?\b/i.test(
      text
    );
  const capabilityInventory = userMessageRequestsOperationsCapabilityInventory(
    messages,
    prompt
  );
  const hostEvidence =
    /\b(?:hostname|host identity|host platform|kernel|machine architecture|operating[- ]system(?: signature)?|(?:host\s+)?os(?:\s+(?:signature|release))?)\b/i.test(
      text
    ) && /\b(?:ODS|host|machine)\b/i.test(text);
  const hostContextPattern = /\b(?:ODS\s+)?(?:host|machine|computer|system|laptop|notebook|desktop|pc)\b/i;
  const hostContext = hostContextPattern.test(text);
  const hostScopeFacetPatterns = [
    /\b(?:hostname|host identity|kernel|machine architecture|architecture|cpu architecture|host platform|operating[- ]system(?: signature)?|(?:host\s+)?os(?:\s+(?:signature|release))?|linux distribution|distro|uptime|load averages?|system load)\b/i,
    /\b(?:process|processes|process inventory)\b/i,
    /\b(?:systemd|system services?|service inventory)\b/i,
    /\b(?:cpu|processor|hardware)\b/i,
    /\b(?:gpu|graphics(?:\s+(?:card|processor))?|video\s+card)\b/i,
    /\b(?:memory|ram|swap)\b/i,
    /\b(?:disk|filesystem|storage|mounts?)\b/i,
    /\b(?:network|interfaces?|addresses?|ip addresses?|routes?|routing|ports?|listeners?)\b/i,
    /\btailscale\b/i,
  ];
  // A second request to find network peers must not narrow an independent
  // request to inspect this computer. Evaluate facets in the same clause as
  // the host inspection, including coordinated discovery requests.
  const hostIntentClauses = text.split(
    /[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)|\b(?:and|then)\s+(?=(?:find|discover|locate|detect|identify|list|look\s+for|scan)\b)/i
  );
  const artifactOrExplanation = /\b(?:explain|tutorial|example|hypothetical|fictional|pretend|build|create|design|implement|write|preview)\b/i;
  const negatedObservationClause = (clause) => /^\s*(?:but\s+)?(?:please\s+)?(?:do\s+not|don't|never|avoid|skip|omit|exclude)\b/i.test(clause);
  const networkDiscoveryClause = (clause) =>
    /\b(?:LAN|local\s+network)\b/i.test(clause) &&
    /\b(?:computers|machines|hosts|devices|peers)\b/i.test(clause) &&
    /\b(?:find|discover|locate|detect|identify|list|scan|look\s+for|which|what)\b/i.test(clause);
  // Facets in a later "Report CPU and RAM" sentence still refine the same
  // inspection. Only an independent peer-discovery clause is outside the
  // local-host facet scope; do not broaden every multi-sentence host request.
  const localHostFacetText = hostIntentClauses.filter((clause) =>
    !networkDiscoveryClause(clause) && !negatedObservationClause(clause)).join(" ");
  const hostExplorationPattern =
    /\b(?:explore|inspect(?:ion)?|inventory|survey|understand|examine|show\s+me\s+around)\b/i;
  // Do not combine an artifact instruction such as "inspect every file" with
  // a later preview phrase such as "the host can verify it". Host exploration
  // authority requires the host scope and exploration intent in the same
  // owner-authored clause.
  const hostExplorationIntent = hostIntentClauses
    .some(
      (clause) =>
        hostContextPattern.test(clause) && hostExplorationPattern.test(clause) &&
        !artifactOrExplanation.test(clause) && !negatedObservationClause(clause)
    );
  // A device question does not need the word "Operations" or "inspect".
  // Keep its request and device in the same clause; artifact-building and
  // explanatory sentences must not become compulsory host work.
  const directHostObservation = hostIntentClauses
    .some((clause) => hostContextPattern.test(clause) &&
      /\b(?:tell\s+me|show\s+me|check|report|measure|what(?:['’]s|\s+is)?|which|how\s+(?:much|many))\b/i.test(clause) &&
      !artifactOrExplanation.test(clause) && !negatedObservationClause(clause));
  const naturalHostOverview = hostIntentClauses.some((clause) =>
    hostContextPattern.test(clause) && !artifactOrExplanation.test(clause) &&
    !negatedObservationClause(clause) && (
      /\b(?:what|anything)\b.{0,32}\b(?:can|could|do)\s+you\b.{0,32}\b(?:tell|show)\b.{0,24}\b(?:about|regarding)\b/i.test(clause) ||
      /\b(?:tell|show)\s+me\b.{0,24}\b(?:about|around)\b/i.test(clause) ||
      /\b(?:describe|summari[sz]e|profile)\b.{0,24}\b(?:this|the|my|our|ODS)\b/i.test(clause)
    ));
  const broadScopeIntent =
    /\b(?:everything|anything)\b.{0,24}\b(?:about|here|on|regarding)\b/i.test(text) ||
    /\ball\s+(?:the\s+)?(?:host\s+|machine\s+|computer\s+|system\s+)?(?:details|facts|information)\b/i.test(text) ||
    /\b(?:full|complete|comprehensive|broad|thorough)\s+(?:host|machine|computer|system|inspection|inventory|survey|overview|profile)\b/i.test(
      text
    );
  const broadHostExploration = hostContext && (hostExplorationIntent || naturalHostOverview) &&
    (broadScopeIntent || !hostScopeFacetPatterns.some((pattern) => pattern.test(localHostFacetText)));
  const extensionCatalog = userMessageRequestsExtensionCatalog(messages, prompt);
  const extensionInventory = userMessageRequestsExtensionInventory(messages, prompt);
  const extensionLifecycle = userMessageExtensionLifecycleIntent(messages, prompt);
  const hostCommand = userMessageRequestsHostCommand(messages, prompt);
  const networkPeer = hostCommand
    ? undefined
    : userMessageNetworkPeerRequest(messages, prompt);
  const networkDiscoveryRequested = !hostCommand && !networkPeer && hostIntentClauses.some((clause) =>
    networkDiscoveryClause(clause) &&
    !artifactOrExplanation.test(clause) &&
    !negatedObservationClause(clause)) &&
    !explicitlyExcludesHostObservation(text, "LAN|local\\s+network|network");
  const localNetworkOverview = !hostCommand && !networkPeer && hostIntentClauses.some((clause) =>
    /\b(?:LAN|local\s+network|(?:host|machine|computer|system|my|our|this)\s+network)\b/i.test(clause) &&
    /\b(?:inspect|explore|check|survey|examine|report|show)\b/i.test(clause) &&
    !/\b(?:interfaces?|addresses?|routes?|routing|ports?|listeners?)\b/i.test(clause) &&
    !artifactOrExplanation.test(clause) && !negatedObservationClause(clause));
  const actions = [];
  if (
    /\b(?:hostname|host identity)\b/i.test(text) ||
    (hostContext && /\b(?:machine|system)?\s*identity\b/i.test(text))
  ) {
    actions.push("host.identity");
  }
  if (/\bkernel\b/i.test(text)) actions.push("host.kernel");
  if (/\b(?:machine architecture|architecture|cpu architecture)\b/i.test(text)) {
    actions.push("host.architecture");
  }
  if (/\bhost platform\b/i.test(text)) actions.push("host.platform");
  if (/\b(?:operating[- ]system(?: signature)?|(?:host\s+)?os(?:\s+(?:signature|release))?|linux distribution|distro)\b/i.test(text)) {
    actions.push("host.os-release");
  }
  if (broadHostExploration || (hostContext && /\b(?:uptime|load averages?|system load)\b/i.test(text))) {
    actions.push("host.uptime");
  }
  if (broadHostExploration || (hostContext && /\b(?:process|processes|process inventory)\b/i.test(text))) {
    actions.push("host.processes");
  }
  if (broadHostExploration || (hostContext && /\b(?:systemd|(?:system\s+)?services?|service inventory)\b/i.test(text))) {
    actions.push("host.services");
  }
  if (broadHostExploration || (hostContext && /\b(?:cpu|processor|hardware)\b/i.test(text))) {
    actions.push("host.cpu");
  }
  if (broadHostExploration || (hostContext && /\b(?:gpu|graphics(?:\s+(?:card|processor))?|video\s+card)\b/i.test(text))) {
    actions.push("host.gpu");
  }
  if (broadHostExploration || (hostContext && /\b(?:memory|ram|swap)\b/i.test(text))) {
    actions.push("host.memory");
  }
  if (broadHostExploration || (hostContext && /\b(?:disk|filesystem|storage|mounts?)\b/i.test(text))) {
    actions.push("host.storage");
  }
  if (broadHostExploration || networkDiscoveryRequested || localNetworkOverview || (hostContext && /\b(?:network interfaces?|interfaces?|addresses?|ip addresses?)\b/i.test(text))) {
    actions.push("host.network-addresses");
  }
  if (broadHostExploration || networkDiscoveryRequested || localNetworkOverview || (hostContext && /\b(?:routes?|routing)\b/i.test(text))) {
    actions.push("host.network-routes");
  }
  if (
    broadHostExploration ||
    (!networkPeer && hostContext && /\b(?:ports?|listeners?|listening endpoints?)\b/i.test(text))
  ) {
    actions.push("host.listening-ports");
  }
  if (broadHostExploration || (hostContext && /\btailscale\b/i.test(text))) {
    actions.push("host.tailscale");
  }
  if (networkPeer) actions.push("host.network-peer");
  if (broadHostExploration) {
    actions.push("host.identity", "host.kernel", "host.platform", "host.os-release", "host.uptime");
  }
  if (extensionInventory) actions.push("ods.extensions.list");
  else if (extensionCatalog) actions.push("ods.extensions.search");
  if (extensionLifecycle) {
    actions.push("ods.extensions.inspect");
    actions.push(`ods.extensions.${extensionLifecycle.action}`);
  }
  if (hostCommand) actions.push("raw-shell");
  const hostFacetPatterns = new Map([
    ["host.identity", "hostname|host identity"],
    ["host.kernel", "kernel"],
    ["host.architecture", "machine architecture|architecture|cpu architecture"],
    ["host.platform", "host platform"],
    ["host.os-release", "operating[- ]system(?: signature)?|(?:host\\s+)?os(?:\\s+(?:signature|release))?|linux distribution|distro"],
    ["host.uptime", "uptime|load averages?|system load"],
    ["host.processes", "process|processes|process inventory"],
    ["host.services", "systemd|system services?|service inventory"],
    ["host.cpu", "cpu|processor|hardware"],
    ["host.gpu", "gpu|graphics(?:\\s+(?:card|processor))?|video\\s+card"],
    ["host.memory", "memory|ram|swap"],
    ["host.storage", "disk|filesystem|storage|mounts?"],
    ["host.network-addresses", "network interfaces?|interfaces?|addresses?|ip addresses?"],
    ["host.network-routes", "routes?|routing"],
    ["host.listening-ports", "ports?|listeners?"],
    ["host.tailscale", "tailscale"],
    ["host.network-peer", "LAN|local network|Tailscale|network|reachable|reachability|resolve|ping|probe|connectivity"],
  ]);
  const excludesNetworkLocation = explicitlyExcludesHostObservation(
    text,
    "network(?:\\s+(?:location|details?))?|interfaces?|addresses?|ip addresses?"
  );
  const addressBearingActions = new Set([
    "host.network-addresses",
    "host.network-routes",
    "host.listening-ports",
  ]);
  // A local host command is one exact, immutable approval unit. Do not also
  // require typed observation actions merely because the owner's sentence
  // names a host facet (for example, "restart Docker and tell me the kernel").
  // The approved command itself must narrowly satisfy the whole request.
  const requestedActions = (hostCommand ? ["raw-shell"] : [...new Set(actions)]).filter((action) => {
    // This action is already bound to the one positively requested peer and
    // independently rejects a negation naming that peer. A separate exclusion
    // for another named host must not erase the requested peer observation.
    if (action === "host.network-peer") return Boolean(networkPeer);
    if (excludesNetworkLocation && addressBearingActions.has(action)) return false;
    const facetPattern = hostFacetPatterns.get(action);
    return !facetPattern || !explicitlyExcludesHostObservation(text, facetPattern);
  });
  return {
    required:
      capabilityInventory || explicitOperations || hostEvidence || broadHostExploration ||
      ((localNetworkOverview || networkDiscoveryRequested || (hostContext && (hostExplorationIntent || directHostObservation))) &&
        requestedActions.some((action) => action.startsWith("host."))) ||
      extensionInventory || extensionCatalog || Boolean(extensionLifecycle) || hostCommand || Boolean(networkPeer),
    actions: requestedActions,
    ...(networkPeer ? { networkPeer } : {}),
    ...(networkDiscoveryRequested ? { networkDiscoveryRequested: true } : {}),
  };
}

export function userMessageExactHostCommand(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return undefined;
  const exact = text.match(
    /(?:^|[.!?]\s+)(?:please\s+)?(?:run|execute|invoke)\s+(?:exactly\s+)?`([^`\r\n\0]{1,16384})`\s+(?:on|in|from|against|for)\s+(?:this|my|the|local)\s+(?:ODS\s+)?(?:host|machine|computer|laptop)\b/i
  );
  return exact?.[1];
}

export function userMessageRequestsHostCommand(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return false;
  if (
    userMessageRequestsOperationsCapabilityInventory(messages, prompt) ||
    userMessageExtensionLifecycleIntent(messages, prompt) ||
    userMessageOperationsContinuation(messages, prompt) ||
    userMessageRequestsExactByteDownload(messages, prompt)
  ) {
    return false;
  }
  if (userMessageExactHostCommand(messages, prompt)) return true;
  const localHost = /\b(?:ODS[- ]host|local\s+(?:ODS\s+)?host|this\s+(?:ODS\s+)?(?:host|machine|computer|laptop)|my\s+(?:ODS\s+)?(?:host|machine|computer|laptop))\b/i;
  const action = /\b(?:run|execute|invoke|launch|start|stop|restart|reload|install|uninstall|remove|update|upgrade|configure|modify|change|create|delete)\b/i;
  const remoteHost = /\b(?:SSH\s+(?:(?:connection|connectivity)\s+)?(?:to|into)|remote\s+(?:host|machine|computer|server|device)|(?:host|machine|computer|server|device)\s+(?:named|called)\s+[A-Za-z0-9][A-Za-z0-9._-]{0,127})\b/i;
  const remoteAction = /\b(?:connect|verify|check|inspect|query|report|troubleshoot|debug|run|execute|invoke)\b/i;
  const guidanceOnly =
    /\b(?:(?:how|what)\s+(?:do|should|would|can|could)\s+(?:I|we|you)\b[^.!?;\n]{0,96}\b(?:run|execute|install|restart|configure|change)|(?:should|can|could|would)\s+(?:I|we)\b[^.!?;\n]{0,96}\b(?:run|execute|install|restart|configure|change)|(?:tell|show|explain)\s+(?:me\s+)?how\s+to\b[^.!?;\n]{0,96}\b(?:run|execute|install|restart|configure|change))\b/i;
  const explicitlyRejected =
    /\b(?:(?:do\s+not|don't|never|must\s+not|should\s+not)\s+(?:ever\s+|actually\s+|please\s+|now\s+){0,2}(?:run|execute|invoke|launch|start|stop|restart|reload|install|uninstall|remove|update|upgrade|configure|modify|change|create|delete)|without\s+(?:running|executing|invoking|launching|starting|stopping|restarting|reloading|installing|uninstalling|removing|updating|upgrading|configuring|modifying|changing|creating|deleting))\b/i;
  const remoteGuidanceOnly =
    /\b(?:(?:how|what)\s+(?:do|should|would|can|could)\s+(?:I|we|you)\b[^.!?;\n]{0,96}\b(?:SSH|connect|inspect|query)|(?:tell|show|explain)\s+(?:me\s+)?how\s+to\b[^.!?;\n]{0,96}\b(?:SSH|connect|inspect|query))\b/i;
  const remoteExplicitlyRejected =
    /\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+(?:ever\s+|actually\s+|please\s+|now\s+){0,2}(?:SSH|connect|contact|access|inspect|query|run|execute|invoke)\b/i;
  return text
    .split(/[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)/i)
    .some((clause) => {
      const remoteHostMatch = clause.match(remoteHost);
      const remoteActionMatch = clause.match(remoteAction);
      if (remoteHostMatch && remoteActionMatch) {
        if (remoteGuidanceOnly.test(clause) || remoteExplicitlyRejected.test(clause)) {
          return false;
        }
        return !/\b(?:can|could|would|should|does|did|will)\s+(?:the\s+)?(?:remote\s+)?(?:host|machine|computer|server|device)\b/i.test(
          clause
        );
      }
      const hostMatch = clause.match(localHost);
      const actionMatch = clause.match(action);
      if (!hostMatch || !actionMatch) return false;
      // Negation and how-to language constrain only their own clause. A later
      // safety boundary such as "Do not run anything else" must not erase an
      // earlier exact host command that the owner explicitly requested.
      if (guidanceOnly.test(clause) || explicitlyRejected.test(clause)) return false;
      if (/\b(?:workspace|sandbox)\b/i.test(clause) && !/\bODS[- ]host\b/i.test(clause)) {
        return false;
      }
      if (
        /\bstart\s+by\b|\bupdate\s+(?:me|us)\b/i.test(clause) ||
        /\b(?:can|could|would|should|does|did|will)\s+(?:this|my|the|local|ODS)\s+(?:ODS\s+)?(?:host|machine|computer|laptop)\b/i.test(clause)
      ) {
        return false;
      }
      if (actionMatch.index < hostMatch.index) {
        const relation = clause.slice(
          actionMatch.index + actionMatch[0].length,
          hostMatch.index
        );
        if (relation.trim() && !/\b(?:on|in|from|against|for)\b/i.test(relation)) {
          return false;
        }
      }
      return !/\brun\s+(?:a|any|some|the)?\s*(?:command|shell)\s*(?:on|against|for)?\s*(?:this|my|the|ODS)?\s*(?:host|machine|computer|laptop)?\s*$/i.test(
        clause.trim()
      );
    });
}

export function userMessageRequestsOperationsCapabilityInventory(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text || !/\b(?:Pixel\s+)?Operations\b/i.test(text)) return false;
  const inventoryScope =
    /\b(?:capabilit(?:y|ies)|inventory|named\s+(?:actions?|operations?)|action\s+IDs?|enabled\s+targets?)\b/i.test(
      text
    );
  const inspectionIntent =
    /\b(?:inspect|inventory|list|report|show|tell|what|which|available|exist)\b/i.test(text);
  return inventoryScope && inspectionIntent;
}

export function userMessageRequiresOdsAppsProjection(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text || !userMessageOperationsRequirements(messages, prompt).required) return false;
  const asksOdsApplications = userMessageOdsToolRequirements([], text).includes(
    "pixel_ods_apps_list"
  );
  if (asksOdsApplications) return true;
  if (!/\b(?:Docker\s+)?containers?\b/i.test(text)) return false;
  if (
    /\bnot\s+just\s+(?:the\s+)?(?:agent\s+)?(?:containers?|sandbox)\b/i.test(text) ||
    /\bdistinguish\b[^.!?;\n]{0,96}\bhost\b[^.!?;\n]{0,96}\bfrom\b[^.!?;\n]{0,96}\bcontainers?\b/i.test(text) ||
    explicitlyExcludesHostObservation(
      text,
      "(?:Docker\\s+)?containers?(?:\\s+information)?"
    )
  ) {
    return false;
  }
  const asksContainerDetails =
    /(?:\b(?:list|name|identify|which|details?|statuses?|purposes?|links?|URLs?)\b[^.!?;\n]{0,96}\bcontainers?\b|\bcontainers?\b[^.!?;\n]{0,96}\b(?:list|names?|details?|statuses?|purposes?|links?|URLs?)\b)/i.test(
      text
    );
  const statusAlreadyRequired = userMessageOdsToolRequirements([], text).includes(
    "pixel_ods_status"
  );
  return asksContainerDetails || !statusAlreadyRequired;
}

export function userMessageRequiresOdsStatusProjection(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text || !userMessageOperationsRequirements([], text).required) return false;
  return userMessageOdsToolRequirements([], text).includes("pixel_ods_status");
}

export function userMessageRequestsWorkspaceContinuation(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return false;
  const namesWorkspace = /(?:\/workspace(?:\/[A-Za-z0-9._/-]+)?|\bworkspace\b)/i;
  const requestsAction =
    /\b(?:continue|work|inspect|create|write|edit|fix|repair|update|build|implement|run|test|verify|read|save|generate)\b/i;
  const rejectsAction =
    /\b(?:do\s+not|don't|never|must\s+not|should\s+not|avoid|skip|without)\b[^.!?;\n]{0,96}\b(?:continue|work|inspect|create|write|edit|fix|repair|update|build|implement|run|test|verify|read|save|generate)\b/i;
  return text
    .split(/[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)/i)
    .some(
      (clause) =>
        namesWorkspace.test(clause) &&
        requestsAction.test(clause) &&
        !rejectsAction.test(clause)
    );
}

export function userMessageRequestsWorkspaceTools(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return false;
  if (userMessageRequestsWorkspaceContinuation(messages, prompt)) return true;
  return (
    /(?:\/workspace(?:\/[A-Za-z0-9._/-]+)?|\bworkspace\b)/i.test(text) &&
    /\b(?:work|inspect|create|write|edit|update|build|implement|run|test|verify|read|save|generate)\b/i.test(text) &&
    !/\b(?:do\s+not|don't|never|must\s+not|should\s+not|avoid|skip|without)\b[^.!?;\n]{0,96}\b(?:use|touch|change|create|write|edit|update|run|read)\b[^.!?;\n]{0,48}\b(?:\/workspace|workspace)\b/i.test(text)
  );
}

export function userMessageRequestsWorkspaceMutation(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text || !userMessageRequestsWorkspaceTools([], text)) return false;
  const mutation =
    /\b(?:create|write|edit|update|build|implement|save|generate|modify|overwrite|patch)\b/i;
  const rejection =
    /\b(?:do\s+not|don't|never|must\s+not|should\s+not|avoid|skip|without)\b[^.!?;\n]{0,96}\b(?:create|write|edit|update|build|implement|save|generate|modify|overwrite|patch)\b/i;
  return text
    .split(/[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)/i)
    .some((clause) => mutation.test(clause) && !rejection.test(clause));
}

export function userMessageRequestsWorkspacePreview(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return false;
  const website =
    /\b(?:browser\b[^.!?;\n]{0,32}\bapps?|dashboards?|frontends?|landing\s+pages?|portals?|sites?|web\b[^.!?;\n]{0,32}\bapps?|web\s*pages?|websites?)\b/i.test(text);
  const application = /\b(?:apps?|applications?)\b/i.test(text);
  const browserInterface =
    /\b(?:forms?|user\s+interfaces?|ui\s+demos?|wireframes?)\b/i.test(text) ||
    (/\bprototypes?\b/i.test(text) &&
      /\b(?:browser|checkout|flow|form|interface|onboarding|screen|sign[- ]?up|ui|ux|web)\b/i.test(text));
  const build =
    /\b(?:build|can\s+you\s+(?:build|create|make)|create|develop|design|generate|give\s+me|implement|make|show\s+me|want|would\s+like|write)\b/i.test(text);
  const revise =
    /\b(?:add|change|continue|edit|improve|keep|modify|patch|refresh|remove|republish|speed\s+up|tweak|update|work)\b/i.test(text);
  const browserVisual =
    /\b(?:artworks?|animated\s+(?:art|illustrations?|scenes?)|interactive\s+(?:art|charts?|diagrams?))\b/i.test(text) ||
    /\b(?:svgs?|breakout|brick[- ]?breakers?|browser[- ]?games?|canvas\s+(?:demos?|games?)|interactive\s+(?:demos?|experiences?|visuali[sz]ations?)|task\s+boards?|to-?do\s+(?:apps?|boards?|lists?)|video\s*games?|videogames?|visual\s+(?:demos?|showcases?)|visuali[sz]ations?|voxel(?:[- ](?:based|styles?))?|webgl\s+(?:demos?|scenes?))\b/i.test(text) ||
    /\b(?:arcade|board|card|puzzle|racing|rhythm|strategy|word)?\s*games?\b/i.test(text);
  const explicitBrowser =
    website || /\b(?:browser|canvas|html|svg|webgl)\b/i.test(text);
  const nativeImplementation =
    /\b(?:c\+\+|command[- ]?line|cli|desktop|java|kotlin|python|rust|swift|terminal)\b/i.test(text) ||
    /\bnative\s+(?:apps?|applications?|binar(?:y|ies)|code|programs?|services?|tools?)\b/i.test(text) ||
    /\bgo\s+(?:app|application|binary|code|program|service)\b/i.test(text);
  const nativeOnly =
    nativeImplementation && !explicitBrowser;
  const rejectsPreview =
    /\b(?:do\s+not|don't|never|must\s+not|should\s+not|avoid|skip)\s+(?:show(?:ing)?|preview(?:ing)?|view(?:ing)?|open(?:ing)?|serv(?:e|ing)|publish(?:ing)?)\b/i.test(text) ||
    /\bwithout\s+(?:show(?:ing)?|preview(?:ing)?|view(?:ing)?|open(?:ing)?|serv(?:e|ing)|publish(?:ing)?)\b/i.test(text);
  const rejectsCreation =
    /\b(?:do\s+not|don['’]t|never|must\s+not|should\s+not)\s+(?:build|create|develop|design|generate|implement|make|show|write)\b/i.test(text);
  const explanatoryOnly =
    /\b(?:explain|history|review|tutorial|what\s+is|why)\b/i.test(text) &&
    !/\b(?:build|create|develop|design|generate|implement|make)\b/i.test(text);
  const nonVisualImplementation =
    /\b(?:backend|daemon|engine|file\s+format|library|parser|renderer|seriali[sz]er|server|service)\b/i.test(text) &&
    !/\b(?:browser|demo|interactive|visuali[sz]ation)\b/i.test(text);
  if (
    rejectsPreview ||
    rejectsCreation ||
    nativeOnly ||
    explanatoryOnly ||
    nonVisualImplementation
  ) return false;
  const directPreview =
    /\b(?:preview|publish|serve|open|show|view)\b[^.!?;\n]{0,96}\b(?:artworks?|illustrations?|charts?|diagrams?|animations?|games?)\b/i.test(text) ||
    /\b(?:preview|publish|serve|open|show|view)\b[^.!?;\n]{0,96}\b(?:site|website|web\s*page|frontend)\b/i.test(text) ||
    /\b(?:site|website|web\s*page|frontend)\b[^.!?;\n]{0,96}\b(?:preview|publish|serve|open|show|view)\b/i.test(text) ||
    /\b(?:open|publish|refresh|republish|serve|show|view)\b[^.!?;\n]{0,96}\b(?:live\s+)?(?:preview|site|website|web\s*page|frontend)\b/i.test(text);
  const unreachableLocalPreview =
    /\b(?:localhost|local\s+host)\b/i.test(text) &&
    /\b(?:not\s+(?:seeing|loading|opening|working)|can(?:not|'t)\s+(?:see|load|open|reach)|investigate|fix)\b/i.test(text);
  const interactiveDelivery =
    freshWorkspaceCreationRequested(text) &&
    /\b(?:show|display|preview)\b[^.!?;\n]{0,64}\bhere\b/i.test(text) &&
    /\b(?:controls?|interacti(?:ve|on)|keyboard|mobile|phone|touch)\b/i.test(text);
  return directPreview || unreachableLocalPreview || interactiveDelivery ||
    ((website || application || browserInterface) && (build || revise)) ||
    (browserVisual && build);
}

function userMessageRequiresWorkspacePreviewAuthorship(
  messages,
  prompt = undefined
) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text || !userMessageRequestsWorkspacePreview(messages, prompt)) return false;
  const create =
    /\b(?:build|can\s+you\s+(?:build|create|make)|create|develop|design|generate|give\s+me|implement|make|show\s+me|want|would\s+like|write)\b/i;
  const rejectsCreation =
    /\b(?:do\s+not|don['’]t|never|must\s+not|should\s+not|avoid|skip|without)\b[^.!?;\n]{0,96}\b(?:build|create|develop|design|generate|implement|make|write)\b/i;
  // "Show me" can request an existing artifact, not a new creation. Requiring
  // fresh writes here would force the model to replace files just to display
  // them. The preview tool still requires a successful current-run index read
  // and an independently verified snapshot; it must not claim fresh authorship.
  const explicitCreation =
    /\b(?:build|create|develop|generate|implement|write)\b/i.test(text) ||
    /\b(?:design|make)\s+(?:(?:me|us)\s+)?(?:a|an|another|new)\b/i.test(text) ||
    /\b(?:from\s+scratch|novel|original)\b/i.test(text);
  const reuseExisting =
    /\b(?:existing|previous|prior|already[- ]created)\b[^.!?;\n]{0,48}\b(?:artwork|animation|chart|design|diagram|files?|game|illustration|site|website)\b/i.test(text) ||
    /\b(?:artwork|animation|chart|diagram|game|illustration|site|website)\b[^.!?;\n]{0,48}\bin\s+(?:(?:my|the|our)\s+)?workspace\b/i.test(text) ||
    /\b(?:show|open|view|preview)\s+(?:me\s+)?(?:the|that|this|our|my)\b[^.!?;\n]{0,64}\b(?:artwork|animation|chart|diagram|game|illustration|site|website)\b/i.test(text);
  if (reuseExisting && !explicitCreation) return false;
  return create.test(text) && !rejectsCreation.test(text);
}

export function userMessageRequestsWorkspacePreviewInspection(
  messages,
  prompt = undefined
) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text || !userMessageRequestsWorkspacePreview(messages, prompt)) return false;
  return (
    /\b(?:inspect|read|review|check)\s+(?:each|every|all)(?:\s+(?:of\s+the|the))?\s+(?:created|generated|preview|site|website)?\s*files?\b/i.test(text) ||
    /\b(?:inspect|read|review|check)\s+(?:the\s+)?(?:created|generated|preview|site|website)\s+files?\b/i.test(text)
  );
}

function freshWorkspaceCreationRequested(text) {
  const creation = /\b(?:build|create|develop|design|generate|implement|make|write)\s+(?:(?:me|us)\s+)?(?:a|an|another|new|some)\s+\w/i;
  const rejected = /\b(?:do\s+not|don['’]t|never|must\s+not|should\s+not|avoid|skip|without)\b[^.!?;\n]{0,96}\b(?:build|create|develop|design|generate|implement|make|write)\b/i;
  const existingTarget = /\b(?:existing|previous|prior|same|current)\s+\w/i;
  const visualTarget = /\b(?:for|from|in|inside|into|of|on|onto|to|using|with)\s+(?:(?:this|that|the|my|our)\s+)(?:animated\s+)?(?:app|artifact|board|dashboard|demo|form|game|illustration|interface|page|preview|prototype|scene|site|svg|visual|voxel|website)\b|\b(?:for|in|into|of|on|onto|to|using|with)\s+(?:it|that)\b/i;
  // A new object can be a timer, calculator, or something not in a catalog.
  // Later "make it accessible" refers to that object. Component requests
  // anchored to existing work ("create a button for this app") remain edits.
  return text.split(/[.!?;\n]+/).some((clause) =>
    creation.test(clause) && !rejected.test(clause) &&
    !existingTarget.test(clause) && !visualTarget.test(clause)
  );
}

export function userMessageRequestsWorkspaceVisualContinuation(
  messages,
  prompt = undefined
) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return false;
  if (userMessageRequestsWorkspaceContinuation([], text)) return false;
  const action =
    /\b(?:add|animate|change|continue|edit|improve|keep|make|modify|polish|refresh|remove|republish|restyle|rework|speed\s+up|tweak|update)\b/i;
  const visualReference =
    /\b(?:existing|previous|prior|same|that|the|this)\s+(?:animated\s+)?(?:app|artifact|board|dashboard|demo|form|game|illustration|interface|page|preview|prototype|scene|site|svg|task\s+board|visual|voxel(?:\s+world)?|web\s*page|web\s*site|website)\b/i;
  const pronounReference =
    /\b(?:add|animate|change|continue|edit|improve|keep|make|modify|polish|refresh|remove|republish|restyle|rework|speed\s+up|tweak|update)\s+(?:it|that)\b/i;
  const rejection =
    /\b(?:do\s+not|don['’]t|never|must\s+not|should\s+not|avoid|skip)\b[^.!?;\n]{0,96}\b(?:add|animate|change|continue|edit|improve|keep|make|modify|polish|refresh|remove|republish|restyle|rework|speed\s+up|tweak|update)\b/i;
  const withoutAction =
    /\bwithout\s+(?:(?:also|any|further)\s+)?(?:adding|animating|changing|continuing|editing|improving|keeping|making|modifying|polishing|refreshing|removing|republishing|restyling|reworking|speeding\s+up|tweaking|updating)\b/i;
  const clauses = text.split(/[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)/i);
  // In a creation request, "keep it self-contained" or "make it responsive"
  // refers to the new artifact, not a missing artifact from an earlier turn.
  // Stop at relational words so "create a button for this app" stays an edit.
  if (freshWorkspaceCreationRequested(text)) {
    return false;
  }
  return clauses.some(
      (clause) =>
        action.test(clause) &&
        (visualReference.test(clause) || pronounReference.test(clause)) &&
        !rejection.test(clause) &&
        !withoutAction.test(clause)
    );
}

function workspacePreviewDirectoryFromState(state) {
  const indexDirectories = new Set(
    [...(state?.successfulWritePaths ?? []), ...(state?.successfulReadPaths ?? [])]
      .filter((value) => typeof value === "string" && value.endsWith("/index.html"))
      .map((value) => value.slice(0, -"/index.html".length))
      .filter(Boolean)
  );
  if (indexDirectories.size === 1) return [...indexDirectories][0];
  return state?.workspacePreviewDirectory;
}

function workspacePreviewReadPaths(state) {
  const directory = state?.workspacePreview?.relativeDirectory;
  if (typeof directory !== "string" || !directory) return [];
  const prefix = `${directory}/`;
  return [...(state?.successfulReadPaths ?? [])].filter((value) =>
    typeof value === "string" &&
    value.startsWith(prefix) &&
    value.slice(prefix.length).split("/").every((part) => WORKSPACE_PATH_COMPONENT.test(part))
  );
}

function workspacePreviewReadbackComplete(state) {
  if (!state?.workspacePreview) return false;
  if (!state.workspacePreviewInspectionRequested) return true;
  return workspacePreviewReadPaths(state).length >= state.workspacePreview.files;
}

function workspacePreviewNextKnownReadPath(state) {
  const preview = state?.workspacePreview;
  if (!preview) return undefined;
  const prefix = `${preview.relativeDirectory}/`;
  const candidates = new Set(
    [...(state?.successfulWritePaths ?? [])].filter((value) =>
      typeof value === "string" &&
      value.startsWith(prefix) &&
      value.slice(prefix.length).split("/").every((part) => WORKSPACE_PATH_COMPONENT.test(part))
    )
  );
  if (preview.files === 1) candidates.add(`${preview.relativeDirectory}/${preview.entryFile}`);
  return [...candidates].find((value) => !state.successfulReadPaths.has(value));
}

function workspacePreviewAuthoredSnapshot(state, expectedDirectory) {
  const prefix = `${expectedDirectory}/`;
  const paths = [...state.successfulWritePaths]
    .filter((value) => typeof value === "string" && value.startsWith(prefix))
    .sort();
  const digest = createHash("sha256");
  let bytes = 0;
  for (const fullPath of paths) {
    const content = state.successfulWriteContentByPath.get(fullPath);
    if (typeof content !== "string") return undefined;
    const relativePath = fullPath.slice(prefix.length);
    const encodedPath = Buffer.from(relativePath, "utf8");
    const encodedContent = Buffer.from(content, "utf8");
    const pathLength = Buffer.alloc(4);
    const contentLength = Buffer.alloc(8);
    pathLength.writeUInt32BE(encodedPath.length);
    contentLength.writeBigUInt64BE(BigInt(encodedContent.length));
    digest.update(pathLength);
    digest.update(encodedPath);
    digest.update(contentLength);
    digest.update(encodedContent);
    bytes += encodedContent.length;
  }
  return {
    paths,
    bytes,
    sha256: digest.digest("hex"),
  };
}

function workspacePreviewOutcome(event, expectedDirectory, state) {
  const details = event?.result?.details;
  const exactDirectory = details?.relativeDirectory === expectedDirectory;
  if (
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    event?.result?.isError === true ||
    details.schemaVersion !== 1 ||
    details.kind !== "ods-pixel-workspace-preview" ||
    details.status !== "succeeded" ||
    !exactDirectory ||
    !/^site-[a-f0-9]{24}$/.test(details.siteId) ||
    details.siteId !== `site-${details.sha256?.slice(0, 24)}` ||
    !Number.isInteger(details.port) ||
    details.port < 1 ||
    details.port > 65535 ||
    details.url !==
      `http://${details.siteId}.localhost:${details.port}/${details.siteId}/` ||
    !Number.isInteger(details.files) ||
    details.files < 1 ||
    details.files > 128 ||
    !Number.isInteger(details.bytes) ||
    details.bytes < 1 ||
    details.bytes > 16 * 1024 * 1024 ||
    details.entryFile !== "index.html" ||
    !/^[a-f0-9]{64}$/.test(details.sha256) ||
    !/^[a-f0-9]{64}$/.test(details.entrySha256) ||
    details.httpStatus !== 200 ||
    details.readbackVerified !== true ||
    details.executable !== false ||
    details.overwritten !== false
  ) {
    return undefined;
  }
  if (state?.workspacePreviewAuthorshipRequired) {
    const authored = workspacePreviewAuthoredSnapshot(state, expectedDirectory);
    const entryPath = `${expectedDirectory}/${details.entryFile}`;
    const entryContent = state.successfulWriteContentByPath.get(entryPath);
    if (
      !authored ||
      authored.paths.length !== details.files ||
      authored.bytes !== details.bytes ||
      authored.sha256 !== details.sha256 ||
      typeof entryContent !== "string" ||
      createHash("sha256").update(entryContent, "utf8").digest("hex") !==
        details.entrySha256
    ) {
      return undefined;
    }
  }
  return {
    relativeDirectory: details.relativeDirectory,
    siteId: details.siteId,
    port: details.port,
    url: details.url,
    files: details.files,
    bytes: details.bytes,
    sha256: details.sha256,
    entrySha256: details.entrySha256,
  };
}

export function userMessageWorkspaceContinuationPath(messages, prompt = undefined) {
  if (!userMessageRequestsWorkspaceContinuation(messages, prompt)) return undefined;
  const text = currentOwnerIntentText(messages, prompt);
  const paths = new Set();
  for (const match of text.matchAll(/\/workspace\/([A-Za-z0-9._/-]{1,512})/gi)) {
    const value = match[1].replace(/[.,;!?]+$/g, "");
    const parts = value.split("/");
    if (
      parts.length < 1 ||
      parts.length > 16 ||
      parts.some(
        (part) =>
          ["", ".", ".."].includes(part) || !WORKSPACE_PATH_COMPONENT.test(part)
      )
    ) {
      continue;
    }
    paths.add(value);
  }
  return paths.size === 1 ? [...paths][0] : undefined;
}

export function userMessageWorkspaceDirectoryPath(messages, prompt = undefined) {
  const path = userMessageWorkspaceContinuationPath(messages, prompt);
  if (!path) return undefined;
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return undefined;
  const normalized = text.toLowerCase();
  const lowerPath = path.toLowerCase();
  for (const prefix of [
    "in ", "inside ", "within ", "under ",
    "in the directory ", "inside the directory ",
    "within the directory ", "under the directory ",
    "in the new directory ", "inside the new directory ",
    "within the new directory ", "under the new directory ",
    "in the existing directory ", "inside the existing directory ",
    "within the existing directory ", "under the existing directory ",
    "in the preserved directory ", "inside the preserved directory ",
    "within the preserved directory ", "under the preserved directory ",
    "workdir ", "working directory ", "as the working directory ",
  ]) {
    const needle = `${prefix}/workspace/${lowerPath}`;
    let offset = normalized.indexOf(needle);
    while (offset >= 0) {
      const before = offset === 0 ? "" : normalized[offset - 1];
      const after = normalized[offset + needle.length] ?? "";
      if (
        (!before || !/[A-Za-z0-9_]/.test(before)) &&
        (!after || /[\s.,;!?`"']/.test(after))
      ) {
        return path;
      }
      offset = normalized.indexOf(needle, offset + needle.length);
    }
  }
  return undefined;
}

function userMessageWorkspaceRequestedFiles(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return [];
  const files = new Set();
  for (const match of text.matchAll(
    /(?:^|[\s/`"'])([A-Za-z0-9][A-Za-z0-9._-]{0,126}\.[A-Za-z][A-Za-z0-9]{0,11})(?=$|[\s,;:!?`"')])/g
  )) {
    if (WORKSPACE_PATH_COMPONENT.test(match[1])) files.add(match[1]);
  }
  return [...files];
}

export function userMessageRequestsOperationsEvidenceArtifact(messages, prompt = undefined) {
  if (!userMessageRequestsWorkspaceContinuation(messages, prompt)) return false;
  const text = currentOwnerIntentText(messages, prompt);
  const path = userMessageWorkspaceContinuationPath(messages, prompt) ?? "";
  const namesEvidenceArtifact =
    /\b(?:report|evidence|findings|inspection|inventory|snapshot|summary)\b/i.test(text) ||
    /(?:^|\/)(?:[^/]*[-_.])?(?:report|evidence|findings|inspection|inventory|snapshot|summary)(?:[-_.][^/]*)?$/i.test(path);
  const bindsObservedFacts =
    /\b(?:exact|observed|verified|actual|real|host|machine|system|status|facts?|evidence)\b/i.test(text);
  return namesEvidenceArtifact && bindsObservedFacts;
}

export function userMessageRequestsPrivateUrl(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  return textRequestsPrivateUrlAccess(text);
}

export function userMessageRequestsExactByteDownload(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  if (!text) return false;
  const capabilityInquiryWithoutSource =
    !/https:\/\//i.test(text) &&
    /\b(?:capabilit(?:y|ies)|capability\s+inventory|supported\s+(?:actions?|operations?|tools?)|what\s+can\s+you\s+do|whether\s+you\s+can)\b/i.test(text) &&
    /\b(?:inspect|inventory|list|report|tell|explain|whether|what)\b/i.test(text);
  // Mentioning a capability in a read-only inventory question is not a request
  // to exercise it. Keep ambiguous action requests fail-closed, but do not let
  // phrases such as "can you fetch exact bytes?" hijack the inventory route.
  if (capabilityInquiryWithoutSource) return false;
  const asksDownload =
    /\b(?:download|fetch|retrieve|save)\b/i.test(text) &&
    /\b(?:file|artifact|object|page|response|bytes?)\b/i.test(text);
  const asksExactBytes =
    /\b(?:byte-for-byte|byte exact|byte-exact|exact[- ]bytes?|exact bytes?|raw bytes?|origin(?: server)? bytes?|remote(?: object)? bytes?)\b/i.test(
      text
    );
  return asksDownload && asksExactBytes;
}

function exactDownloadWorkspacePath(text, sourceUrl) {
  const quoted = text.match(
    /\b(?:workspace\s+)?(?:file|artifact)\s+(?:named|at|as)\s*[`"']([A-Za-z0-9][A-Za-z0-9._/-]{0,511})[`"']/i
  );
  const unquoted = text.match(
    /\b(?:workspace\s+)?(?:file|artifact)\s+(?:named|at|as)\s+([A-Za-z0-9][A-Za-z0-9._/-]{0,511})(?=[\s,.;!?)]|$)/i
  );
  const direct = text.match(
    /\b(?:download|fetch|retrieve|save)\b[^\n]{0,240}?\b(?:as|to|into)\s+[`"']?([A-Za-z0-9][A-Za-z0-9._/-]{0,511})[`"']?(?=[\s,.;!?)]|$)/i
  );
  const hasExplicitDestinationClause =
    /\b(?:workspace\s+)?(?:file|artifact)\s+(?:named|at|as)\s+\S+/i.test(text) ||
    /\b(?:download|fetch|retrieve|save)\b[^\n]{0,240}?\b(?:as|to|into)\s+\S+/i.test(text);
  let value = (quoted?.[1] ?? unquoted?.[1] ?? direct?.[1] ?? "").replace(
    /^\/?workspace\//i,
    ""
  ).replace(/[.,;!?]+$/, "");
  if (!value) {
    if (hasExplicitDestinationClause) return undefined;
    try {
      const pathname = new URL(sourceUrl).pathname;
      const candidate = pathname.split("/").filter(Boolean).at(-1) ?? "download.bin";
      value = OPS_ARTIFACT_FILENAME.test(candidate) ? `downloads/${candidate}` : "downloads/download.bin";
    } catch {
      return undefined;
    }
  }
  const parts = value.split("/");
  if (
    value.startsWith("/") ||
    value.includes("\\") ||
    value.length > 512 ||
    parts.length < 1 ||
    parts.length > 16 ||
    parts.some(
      (part) =>
        ["", ".", ".."].includes(part) || !WORKSPACE_PATH_COMPONENT.test(part)
    ) ||
    !OPS_ARTIFACT_FILENAME.test(parts.at(-1))
  ) {
    return undefined;
  }
  return value;
}

export function userMessageExactDownloadRequest(messages, prompt = undefined) {
  if (!userMessageRequestsExactByteDownload(messages, prompt)) return undefined;
  const text = currentUserText(messages, prompt);
  const candidates = text.match(/https:\/\/[^\s<>`"']+/gi) ?? [];
  const urls = [];
  for (const candidate of candidates) {
    let value = candidate.replace(/[),.;!?\]}]+$/g, "");
    try {
      const parsed = new URL(value);
      if (
        parsed.protocol === "https:" &&
        parsed.hostname &&
        !parsed.username &&
        !parsed.password &&
        !parsed.hash
      ) {
        urls.push(parsed.href);
      }
    } catch {
      // The broker route stays unavailable when the owner URL is ambiguous.
    }
  }
  if (urls.length !== 1) return { exact: true };
  const relativePath = exactDownloadWorkspacePath(text, urls[0]);
  if (!relativePath) return { exact: true, url: urls[0] };
  const digest = text.match(
    /\b(?:sha-?256|sha256|expected\s+digest|digest)\b[^a-f0-9]{0,32}([a-f0-9]{64})(?![a-f0-9])/i
  )?.[1]?.toLowerCase();
  return {
    exact: true,
    url: urls[0],
    relativePath,
    filename: relativePath.split("/").at(-1),
    expectedSha256: digest,
  };
}

function validGitHubRepository(owner, repository) {
  return (
    /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(owner) &&
    /^[A-Za-z0-9._-]{1,100}$/.test(repository) &&
    !repository.endsWith(".")
  );
}

export function userMessageGitHubRepositoryUrl(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  if (!text) return undefined;
  const explicit = text.match(
    /https?:\/\/github\.com\/([A-Za-z0-9-]{1,39})\/([A-Za-z0-9._-]{1,100})(?=[\s/?#),.;\]}]|$)/i
  );
  let match = explicit;
  if (!match) {
    match = text.match(
      /\b([A-Za-z0-9-]{1,39})\/([A-Za-z0-9._-]{1,100})\b(?=.{0,64}\bGitHub\s+(?:repo(?:sitory)?|project)\b)/i
    );
  }
  if (!match) {
    match = text.match(
      /\bGitHub\s+(?:repo(?:sitory)?|project)\b.{0,64}\b([A-Za-z0-9-]{1,39})\/([A-Za-z0-9._-]{1,100})\b/i
    );
  }
  if (!match) return undefined;
  // A sentence-final period is not part of the repository name, but the URL
  // matcher must otherwise allow dots for legitimate names and the .git form.
  const repository = match[2].replace(/\.+$/g, "").replace(/\.git$/i, "");
  if (!repository || !validGitHubRepository(match[1], repository)) return undefined;
  return `https://github.com/${match[1]}/${repository}`;
}

export function userMessageGitHubFileUrl(messages, prompt = undefined) {
  const text = currentUserText(messages, prompt);
  const repositoryUrl = userMessageGitHubRepositoryUrl(messages, prompt);
  if (!text || !repositoryUrl) return undefined;
  let repository;
  try {
    const target = new URL(repositoryUrl);
    const parts = target.pathname.split("/").filter(Boolean);
    if (parts.length !== 2 || !validGitHubRepository(parts[0], parts[1])) {
      return undefined;
    }
    repository = parts;
  } catch {
    return undefined;
  }

  // Accept only a plainly named, repository-relative path. Do not interpret
  // traversal, URL-encoded text, absolute paths, or the Owner/Repo identifier
  // itself as a file target. Each accepted segment is safe to place in a raw
  // GitHub URL after independent encoding.
  const paths = text.matchAll(
    /(?:^|[\s"'`(])((?:[A-Za-z0-9._-]+\/)+[A-Za-z0-9._-]+)(?=[\s"'`,).;:?!\]}]|$)/g
  );
  for (const match of paths) {
    const relative = match[1];
    if (relative.length > 240) continue;
    if (relative.toLowerCase() === `${repository[0]}/${repository[1]}`.toLowerCase()) {
      continue;
    }
    const segments = relative.split("/");
    if (
      segments.length < 2 ||
      segments.length > 16 ||
      segments.some((segment) => !segment || segment === "." || segment === "..")
    ) {
      continue;
    }
    const encoded = segments.map((segment) => encodeURIComponent(segment)).join("/");
    return `https://raw.githubusercontent.com/${repository[0]}/${repository[1]}/HEAD/${encoded}`;
  }
  return undefined;
}

export function githubReadmeUrl(repositoryUrl) {
  if (typeof repositoryUrl !== "string") return undefined;
  try {
    const target = new URL(repositoryUrl);
    const parts = target.pathname.split("/").filter(Boolean);
    if (
      target.protocol !== "https:" ||
      target.hostname.toLowerCase() !== "github.com" ||
      parts.length !== 2 ||
      !validGitHubRepository(parts[0], parts[1])
    ) {
      return undefined;
    }
    return `https://raw.githubusercontent.com/${parts[0]}/${parts[1]}/HEAD/README.md`;
  } catch {
    return undefined;
  }
}

function fetchTargetsNonPublicAddress(event) {
  return urlTargetsNonPublicAddress(event?.params?.url);
}

function canonicalFetchUrl(event) {
  const raw = event?.params?.url;
  if (typeof raw !== "string" || !raw) return undefined;
  try {
    const target = new URL(raw);
    if (!new Set(["http:", "https:"]).has(target.protocol)) return undefined;
    target.hash = "";
    return target.toString();
  } catch {
    return undefined;
  }
}

function execTargetsNonPublicAddress(event) {
  const command = event?.params?.command;
  if (typeof command !== "string" || !command) return false;
  const urls = command.match(/https?:\/\/[^\s"'`|;&<>]+/gi) ?? [];
  if (urls.some((url) => urlTargetsNonPublicAddress(url.replace(/[),.\]}]+$/, "")))) {
    return true;
  }
  if (!/(?:^|\s|[;&|])(?:curl|wget)(?:\s|$)/i.test(command)) return false;
  const arguments_ = command.match(/"[^"]*"|'[^']*'|[^\s]+/g) ?? [];
  return arguments_.some((argument) => {
    const candidate = argument.replace(/^["']|["'),.;\]}]+$/g, "");
    if (
      candidate.startsWith("-") ||
      !/^(?:localhost|[a-z0-9.-]+\.(?:local|internal)|\[[0-9a-f:]+\]|\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?(?:\/|$)/i.test(
        candidate
      )
    ) {
      return false;
    }
    return urlTargetsNonPublicAddress(`http://${candidate}`);
  });
}

export function createToolLoopGuard({
  abortRun,
  abortRunAndDrain,
  execControl,
  evidenceArtifactWriter,
  execMarkerCleanupDelayMs = 5000,
  limits,
  warn = () => {},
} = {}) {
  const effective = normalizedLimits(limits);
  // This intentionally stays plugin-local. OpenClaw's runContext write API is
  // disabled for this non-bundled hook path. Bound the cache itself instead of
  // requesting conversation access merely for cleanup.
  const runs = new Map();
  const activeUsers = new Map();
  const pendingToolRuns = new Map();
  const sessionPreviews = new Map();

  function pruneRuns() {
    while (runs.size >= MAX_TRACKED_RUNS) {
      runs.delete(runs.keys().next().value);
    }
  }

  function pruneActiveUsers() {
    while (activeUsers.size >= MAX_TRACKED_RUNS) {
      activeUsers.delete(activeUsers.keys().next().value);
    }
  }

  function rememberSessionPreview(sessionId, preview) {
    if (typeof sessionId !== "string" || !sessionId || !preview) return;
    if (sessionPreviews.has(sessionId)) sessionPreviews.delete(sessionId);
    while (sessionPreviews.size >= MAX_TRACKED_RUNS) {
      sessionPreviews.delete(sessionPreviews.keys().next().value);
    }
    sessionPreviews.set(sessionId, Object.freeze({ ...preview }));
  }

  function rememberToolRun(
    toolCallId,
    runId,
    selectedToolName,
    selectedParams,
    verificationFingerprint
  ) {
    if (typeof toolCallId !== "string" || !toolCallId) return;
    if (pendingToolRuns.has(toolCallId)) pendingToolRuns.delete(toolCallId);
    while (pendingToolRuns.size >= MAX_TRACKED_RUNS * 4) {
      pendingToolRuns.delete(pendingToolRuns.keys().next().value);
    }
    pendingToolRuns.set(toolCallId, {
      runId,
      selectedToolName,
      selectedParams,
      verificationFingerprint,
    });
  }

  function stateFor(runId) {
    let state = runs.get(runId);
    if (!state) {
      pruneRuns();
      state = {
        search: 0,
        fetch: 0,
        total: 0,
        webExhausted: false,
        webTerminalBlocks: 0,
        codingExhausted: false,
        codingTerminalBlocks: 0,
        invalidEditCreateBlocks: 0,
        oversizedEditBlocks: 0,
        successfulWritePaths: new Set(),
        successfulWriteContentByPath: new Map(),
        compareSwapRepairCounts: new Map(),
        successfulReadPaths: new Set(),
        repeatedWriteBlocks: new Map(),
        privateNetworkExhausted: false,
        privateNetworkPrompt: false,
        clientCancelled: false,
        fetchedUrls: new Set(),
        fetchPivots: new Set(),
        targetedExtractPending: undefined,
        targetedExtractBlocks: 0,
        githubCanonicalUrl: undefined,
        githubReadmeUrl: undefined,
        githubFileUrl: undefined,
        githubCanonicalSatisfied: false,
        githubCanonicalFailed: false,
        githubCanonicalBlocks: 0,
        odsRoutingInitialized: false,
        odsRequiredTools: new Set(),
        odsRoutingBlocks: 0,
        odsRoutingExhausted: false,
        odsRoutingTerminalBlocks: 0,
        exactDownloadRequested: false,
        exactDownloadRequest: undefined,
        exactDownloadSubmissions: new Map(),
        exactDownloadBrokerObserved: false,
        exactDownloadArtifact: undefined,
        exactDownloadPromotion: undefined,
        exactDownloadPromotionAttempted: false,
        exactDownloadTerminalOutcome: undefined,
        exactDownloadTerminalBlocks: 0,
        operationsRequired: false,
        operationsRequiredActions: new Set(),
        operationsHostCommandRequested: false,
        operationsExactHostCommand: undefined,
        operationsNetworkPeer: undefined,
        operationsNetworkDiscoveryRequested: false,
        unrequestedOperationsDeniedRounds: 0,
        unrequestedOperationsDeniedRound: 0,
        unrequestedOperationsTerminal: false,
        unrequestedOperationsAborted: false,
        operationsInventoryOnly: false,
        operationsInventoryAttempted: false,
        operationsInventory: undefined,
        operationsExpectedQuery: undefined,
        operationsExpectedExtensionLifecycle: undefined,
        operationsContinuation: undefined,
        operationsContinuationOutcome: undefined,
        operationsSubmittedJobs: new Map(),
        operationsTerminalJobs: new Map(),
        operationsHostResultCompactionsRemaining: 0,
        operationsTerminalBlocks: 0,
        operationsTerminalAborted: false,
        operationsRequiresOdsAppsProjection: false,
        operationsOdsAppsProjectionAttempted: false,
        operationsOdsAppsProjectionToolSearchPending: false,
        operationsOdsAppsProjection: undefined,
        operationsRequiresOdsStatusProjection: false,
        operationsOdsStatusProjectionAttempted: false,
        operationsOdsStatusProjectionToolSearchPending: false,
        operationsOdsStatusProjection: undefined,
        operationsWorkspaceContinuationRequested: false,
        operationsWorkspaceEvidenceArtifactRequested: false,
        operationsWorkspaceExpectedPath: undefined,
        operationsWorkspaceWriteVerified: false,
        operationsWorkspaceReadVerified: false,
        ownerIntentObserved: false,
        workspaceTaskRequested: false,
        workspaceMutationRequested: false,
        workspaceTaskPath: undefined,
        workspaceTaskDirectory: undefined,
        workspaceRequestedFiles: [],
        workspacePythonUnittestRequested: false,
        workspaceParsedJsonVerificationRequested: false,
        workspaceVerificationRequested: false,
        workspacePreviewRequested: false,
        workspacePreviewAuthorshipRequired: false,
        workspacePreviewModelAuthored: false,
        workspaceVisualContinuationRequested: false,
        workspaceVisualContinuationUnavailable: false,
        workspaceVisualContinuationEdited: false,
        workspacePreviewInspectionRequested: false,
        workspacePreviewDirectory: undefined,
        workspacePreviewAttempted: false,
        workspacePreview: undefined,
        workspaceToolSearchRouted: false,
        workspaceToolSearchQueries: new Set(),
        workspaceInspectionRouted: false,
        workspaceInspectionPollCorrections: 0,
        failedTestReadCorrections: 0,
        invalidUnittestBlocks: 0,
        invalidParsedJsonBlocks: 0,
        noOpEditBlocks: 0,
        operationsPromptRound: 0,
        operationsCorrectionPromptRound: undefined,
        operationsRoutingBlocks: 0,
        failedExec: new Map(),
        invalidExecArgumentAttempts: 0,
        successfulExec: new Map(),
        successfulExecBlocks: new Map(),
        failedVerificationAttempts: 0,
        latestVerificationStatus: undefined,
        latestVerificationFingerprint: undefined,
        wrappedExecFailurePending: false,
        suppressStaleExecWarning: false,
        recursiveDeleteAuthorized: false,
        pendingExecSessions: new Map(),
        pendingExecBlocks: new Map(),
        execOriginalByWrapped: new Map(),
        verificationOriginalByWrapped: new Map(),
        currentSessionId: undefined,
        currentSessionKey: undefined,
        visibleReplyText: undefined,
        visibleReplyTerminalAborted: false,
      };
      runs.set(runId, state);
    }
    return state;
  }

  function completeVerifiedEvidenceArtifact(state) {
    if (
      typeof evidenceArtifactWriter !== "function" ||
      !state?.operationsWorkspaceEvidenceArtifactRequested ||
      !state.operationsWorkspaceExpectedPath ||
      state.operationsWorkspaceWriteVerified ||
      state.operationsWorkspaceReadVerified ||
      (state.operationsRequiresOdsStatusProjection && !state.operationsOdsStatusProjection) ||
      (state.operationsRequiresOdsAppsProjection && !state.operationsOdsAppsProjection)
    ) {
      return;
    }
    const everySubmittedJobIsTerminal =
      state.operationsSubmittedJobs.size > 0 &&
      [...state.operationsSubmittedJobs.keys()].every((jobId) =>
        state.operationsTerminalJobs.has(jobId)
      );
    if (!everySubmittedJobIsTerminal) return;
    const content = operationsEvidenceText(
      state.operationsRequiredActions,
      state.operationsTerminalJobs,
      state.operationsOdsAppsProjection,
      state.operationsOdsStatusProjection
    );
    if (
      typeof content !== "string" ||
      ![
        OPERATIONS_HOST_EVIDENCE_PREFIX,
        OPERATIONS_HOST_COMMAND_EVIDENCE_PREFIX,
        OPERATIONS_EXTENSION_CATALOG_EVIDENCE_PREFIX,
        OPERATIONS_EXTENSION_LIFECYCLE_EVIDENCE_PREFIX,
      ].some((prefix) => content.startsWith(prefix))
    ) {
      return;
    }
    try {
      const result = evidenceArtifactWriter({
        relativePath: state.operationsWorkspaceExpectedPath,
        content: `${content}\n`,
      });
      if (
        result?.relativePath === state.operationsWorkspaceExpectedPath &&
        result?.readbackVerified === true
      ) {
        state.operationsWorkspaceWriteVerified = true;
        state.operationsWorkspaceReadVerified = true;
      }
    } catch (error) {
      warn(`Pixel deterministic evidence artifact failed closed: ${String(error)}`);
    }
  }

  function beforeToolCall(event, context, agentId = "pixel") {
    if (context?.agentId !== agentId) return undefined;
    // OpenClaw 2026.6 does not consistently expose sessionKey during
    // before_prompt_build for OpenAI-compatible HTTP turns. Tool hooks do
    // receive the complete run context, so refresh the opaque user -> session
    // cancellation mapping here as well. This keeps dashboard disconnects
    // capable of aborting a long model continuation after the first tool.
    observeRun(context, agentId);
    const toolName = context?.toolName ?? event?.toolName;
    let normalizedParams = normalizeWorkspaceParams(toolName, event?.params);

    const { runId, sessionId } = runIdentity(event, context);
    // OpenClaw's before_tool_call context may omit sessionId even though the
    // earlier before_prompt_build hook supplied the exact run identity. Keep
    // policy and deterministic routing active from runId alone; operations
    // that truly need a session still fail closed on the optional sessionId.
    const state = runId ? stateFor(runId) : undefined;
    // Put this terminal fuse before every tool-specific return, including
    // Tool Search, reply controls, and workspace recovery adaptations. The
    // first offending round remains a recoverable routing correction. Only
    // after a second offending round and one final-answer opportunity does
    // another tool abort the real active run instead of blocking forever.
    if (state?.unrequestedOperationsTerminal) {
      if (state.operationsPromptRound > 0 &&
          state.operationsPromptRound === state.unrequestedOperationsDeniedRound) {
        // Parallel siblings were chosen before the terminal correction
        // reached the model. Do not turn them into an early run abort.
        return { block: true, blockReason: UNREQUESTED_OPERATIONS_TERMINAL_REASON };
      }
      const activeSession = sessionId ?? state.currentSessionId;
      if (!state.unrequestedOperationsAborted && typeof activeSession === "string" && activeSession) {
        try {
          state.unrequestedOperationsAborted = typeof abortRun === "function" && Boolean(abortRun(activeSession));
        } catch (error) {
          warn(`Pixel unrequested-Operations abort failed for run ${runId}: ${String(error)}`);
        }
      }
      return { block: true, blockReason: UNREQUESTED_OPERATIONS_LOOP_ABORT_REASON };
    }
    let pendingParams = normalizedParams ?? event?.params;
    if (
      state?.workspaceVisualContinuationRequested &&
      FILE_PATH_TOOLS.has(toolName) &&
      typeof pendingParams?.path === "string" &&
      WORKSPACE_PATH_COMPONENT.test(pendingParams.path)
    ) {
      normalizedParams = {
        ...pendingParams,
        path: `${state.workspaceTaskDirectory}/${pendingParams.path}`,
      };
      pendingParams = normalizedParams;
    }
    const effectiveReplyTool = toolName === "tool_call"
      ? pendingParams?.id?.split(":").at(-1)
      : toolName;
    const effectiveReplyArgs = toolName === "tool_call"
      ? pendingParams?.args
      : pendingParams;
    const exactReplyText =
      effectiveReplyArgs &&
      typeof effectiveReplyArgs === "object" &&
      !Array.isArray(effectiveReplyArgs) &&
      typeof effectiveReplyArgs.text === "string" &&
      Object.keys(effectiveReplyArgs).length === 1
        ? effectiveReplyArgs.text
        : effectiveReplyArgs &&
            typeof effectiveReplyArgs === "object" &&
            !Array.isArray(effectiveReplyArgs) &&
            typeof effectiveReplyArgs.message === "string" &&
            Object.keys(effectiveReplyArgs).every((key) =>
              ["sessionKey", "message"].includes(key)
            )
          ? effectiveReplyArgs.message
          : undefined;
    const safeReplyText =
      typeof exactReplyText === "string" &&
      exactReplyText.length > 0 &&
      exactReplyText.length <= 4096 &&
      !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(exactReplyText)
        ? exactReplyText
        : undefined;
    const selfSessionSend =
      effectiveReplyTool === "sessions_send" &&
      typeof state?.currentSessionKey === "string" &&
      effectiveReplyArgs?.sessionKey === state.currentSessionKey;
    if (effectiveReplyTool === "reply_to_current" || selfSessionSend) {
      if (state?.latestVerificationStatus === "passed" && safeReplyText) {
        state.visibleReplyText = safeReplyText;
        if (
          !state.visibleReplyTerminalAborted &&
          typeof (sessionId ?? state.currentSessionId) === "string" &&
          (sessionId ?? state.currentSessionId)
        ) {
          try {
            state.visibleReplyTerminalAborted = Boolean(
              abortRun?.(sessionId ?? state.currentSessionId)
            );
          } catch (error) {
            warn(`Pixel verified-reply fast-path abort failed: ${String(error)}`);
          }
        }
      }
      return { block: true, blockReason: VISIBLE_REPLY_REQUIRES_FINAL_REASON };
    }
    if (
      state?.workspaceTaskDirectory &&
      toolName === "tool_call" &&
      pendingParams &&
      typeof pendingParams === "object" &&
      !Array.isArray(pendingParams) &&
      typeof pendingParams.id === "string" &&
      pendingParams.args &&
      typeof pendingParams.args === "object" &&
      !Array.isArray(pendingParams.args)
    ) {
      let nestedName = pendingParams.id.split(":").at(-1);
      const compactUnittestPath =
        pendingParams.id === "python3" &&
        pendingParams.args.run === "unittest" &&
        typeof pendingParams.args.test === "string"
          ? pendingParams.args.test
          : undefined;
      const requestedPath = normalizeWorkspaceFilePath(
        pendingParams.args.path ?? compactUnittestPath
      );
      const directoryBasename = state.workspaceTaskDirectory.split("/").at(-1);
      const basenamePrefix = `${directoryBasename}/`;
      const basenameRelativePath =
        typeof requestedPath === "string" &&
        requestedPath.startsWith(basenamePrefix) &&
        requestedPath
          .slice(basenamePrefix.length)
          .split("/")
          .every((part) => WORKSPACE_PATH_COMPONENT.test(part));
      const exactRequestedPaths = new Set(
        state.workspaceRequestedFiles.map(
          (file) => `${state.workspaceTaskDirectory}/${file}`
        )
      );
      if (state.workspaceTaskPath) {
        exactRequestedPaths.add(state.workspaceTaskPath);
      }
      const readbackCandidate = (() => {
        if (typeof requestedPath !== "string") return undefined;
        if (exactRequestedPaths.has(requestedPath)) return requestedPath;
        if (basenameRelativePath) {
          const candidate =
            `${state.workspaceTaskDirectory}/${requestedPath.slice(basenamePrefix.length)}`;
          return exactRequestedPaths.has(candidate) ? candidate : undefined;
        }
        if (WORKSPACE_PATH_COMPONENT.test(requestedPath)) {
          const candidate = `${state.workspaceTaskDirectory}/${requestedPath}`;
          return exactRequestedPaths.has(candidate) ? candidate : undefined;
        }
        return undefined;
      })();
      const compactPythonArgs = pendingParams.args.args ?? [];
      const compactPythonContext = pendingParams.args.context;
      const compactUnittestRunner =
        pendingParams.id === "python3" &&
        Object.keys(pendingParams.args).sort().join("\u0000") ===
          ["run", "test"].sort().join("\u0000") &&
        pendingParams.args.run === "unittest";
      const compactPythonRunner =
        (
          compactUnittestRunner ||
          (
            (pendingParams.id === "python3" || nestedName === "exec") &&
            Object.keys(pendingParams.args).every(
              (key) => key === "path" || key === "args" || key === "context"
            ) &&
            (compactPythonContext === undefined || compactPythonContext === "fork") &&
            Array.isArray(compactPythonArgs) &&
            (compactPythonArgs.length === 0 ||
              (compactPythonArgs.length === 1 && compactPythonArgs[0] === "-v"))
          )
        ) &&
        readbackCandidate &&
        state.successfulWritePaths.has(readbackCandidate);
      const compactPythonFile = compactPythonRunner
        ? readbackCandidate.slice(`${state.workspaceTaskDirectory}/`.length)
        : undefined;
      if (
        typeof compactPythonFile === "string" &&
        /^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(compactPythonFile)
      ) {
        // A compact model can treat the generic Tool Search transport as a
        // language runner, either inventing `python3` as the catalog id or
        // selecting exec with `{path,args}`. Adapt only a test file that the
        // owner named and this run successfully wrote, and only the observed
        // optional verbose flag. Use the auditable unittest runner so failure
        // evidence and retry fuses remain authoritative.
        pendingParams = {
          id: "openclaw:core:exec",
          args: {
            command: `python3 -m unittest -v ${compactPythonFile}`,
            workdir: `/workspace/${state.workspaceTaskDirectory}`,
          },
        };
        nestedName = "exec";
      }
      // Compact models sometimes invent a bare `readback` id after a successful
      // write. Adapt only the exact owner-requested path, with only a path
      // argument, to the native read tool. Namespaced or unrelated readback
      // tools remain untouched, so this adds no filesystem authority.
      if (
        pendingParams.id === "readback" &&
        Object.keys(pendingParams.args).length === 1 &&
        Object.keys(pendingParams.args)[0] === "path" &&
        readbackCandidate
      ) {
        pendingParams = {
          ...pendingParams,
          id: "read",
          args: { path: readbackCandidate },
        };
        nestedName = "read";
      }
      if (
        FILE_PATH_TOOLS.has(nestedName) &&
        typeof requestedPath === "string" &&
        (WORKSPACE_PATH_COMPONENT.test(requestedPath) || basenameRelativePath) &&
        requestedPath !== directoryBasename
      ) {
        const relativePath = basenameRelativePath
          ? requestedPath.slice(basenamePrefix.length)
          : requestedPath;
        pendingParams = {
          ...pendingParams,
          args: {
            ...pendingParams.args,
            path: `${state.workspaceTaskDirectory}/${relativePath}`,
          },
        };
      }
      if (
        nestedName === "write" &&
        typeof pendingParams.args.content === "string"
      ) {
        const content = completeRequestedUnittestImports(
          stripTrailingToolEnvelopeLeak(pendingParams.args.content),
          state,
          normalizeWorkspaceFilePath(pendingParams.args.path)
        );
        if (content !== pendingParams.args.content) {
          pendingParams = {
            ...pendingParams,
            args: { ...pendingParams.args, content },
          };
        }
        const normalizedWritePath = normalizeWorkspaceFilePath(pendingParams.args.path);
        const workspaceTestPath =
          typeof state.workspaceTaskDirectory === "string" &&
          normalizedWritePath.startsWith(`${state.workspaceTaskDirectory}/`) &&
          /^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(
            normalizedWritePath.split("/").at(-1)
          );
        const originallyRequestedUnittestPath = state.workspaceRequestedFiles.some(
          (file) =>
            /^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(file) &&
            normalizedWritePath === `${state.workspaceTaskDirectory}/${file}`
        );
        const requestedUnittestPath = workspaceTestPath &&
          (state.workspacePythonUnittestRequested || originallyRequestedUnittestPath);
        if (
          state.workspacePythonUnittestRequested &&
          requestedUnittestPath &&
          !hasRequestedUnittestStructure(pendingParams.args.content)
        ) {
          state.invalidUnittestBlocks += 1;
          if (state.invalidUnittestBlocks === 1) {
            return { block: true, blockReason: REQUESTED_UNITTEST_REQUIRED_REASON };
          }
          if (state.invalidUnittestBlocks === 2) {
            return { block: true, blockReason: REQUESTED_UNITTEST_RETRY_REASON };
          }
          if (state.invalidUnittestBlocks === 3) {
            return {
              block: true,
              blockReason: requestedUnittestFinalRetryReason(state),
            };
          }
          state.codingExhausted = true;
          state.codingTerminalBlocks = 1;
          return { block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON };
        }
        if (
          state.workspaceParsedJsonVerificationRequested &&
          workspaceTestPath &&
          !/\bjson\.loads\s*\(/.test(pendingParams.args.content)
        ) {
          state.invalidParsedJsonBlocks += 1;
          if (state.invalidParsedJsonBlocks <= 2) {
            return {
              block: true,
              blockReason: REQUESTED_PARSED_JSON_REQUIRED_REASON,
            };
          }
          state.codingExhausted = true;
          state.codingTerminalBlocks = 1;
          return { block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON };
        }
      }
    }
    let workspaceInspectionShape = false;
    let workspaceInspectionAdapted = false;
    if (
      state?.workspaceTaskDirectory &&
      state.workspaceTaskPath &&
      !state.workspaceVisualContinuationRequested &&
      toolName === "tool_call" &&
      pendingParams &&
      typeof pendingParams === "object" &&
      !Array.isArray(pendingParams) &&
      typeof pendingParams.id === "string" &&
      pendingParams.args &&
      typeof pendingParams.args === "object" &&
      !Array.isArray(pendingParams.args)
    ) {
      const nestedName = pendingParams.id.split(":").at(-1);
      const keys = Object.keys(pendingParams.args);
      const requestedPath = normalizeWorkspaceFilePath(pendingParams.args.path);
      const basename = state.workspaceTaskPath.split("/").at(-1);
      const matchesAuthorizedPath =
        requestedPath === state.workspaceTaskPath || requestedPath === basename;
      // Small models commonly call an invented `ls` catalog id, or call the
      // exact exec id with only a path. Adapt only that read-first shape, only
      // for the one workspace path explicitly authorized in this live owner
      // request. Creating the named directory is already required by the
      // requested workspace task; no host or Operations authority is added.
      const exactAuthorizedPathShape =
        keys.length === 1 &&
        keys[0] === "path" &&
        matchesAuthorizedPath &&
        (pendingParams.id === "ls" || nestedName === "exec");
      const readDirectoryShape =
        keys.length === 1 &&
        keys[0] === "path" &&
        matchesAuthorizedPath &&
        nestedName === "read";
      const emptyProcessListShape =
        keys.length === 1 &&
        keys[0] === "action" &&
        pendingParams.args.action === "list" &&
        (nestedName === "process" || nestedName === "exec") &&
        state.pendingExecSessions.size === 0;
      const normalizedInspectionCommand =
        typeof pendingParams.args.command === "string"
          ? pendingParams.args.command.trim().replace(/\s+/g, " ")
          : undefined;
      const execDirectoryShape =
        nestedName === "exec" &&
        // PTY/background/yield controls do not change the semantics of this
        // exact read-only listing. Compact models frequently copy them from
        // the catalog description, so include them without accepting any
        // additional command, cwd, environment, or input surface.
        keys.every((key) =>
          ["command", "yieldMs", "timeout", "pty", "background"].includes(key)
        ) &&
        new Set([
          `ls -la /workspace/${state.workspaceTaskPath}`,
          `ls -la /workspace/${state.workspaceTaskPath}/`,
          `ls -la ${state.workspaceTaskPath}`,
          `ls -la ${state.workspaceTaskPath}/`,
        ]).has(normalizedInspectionCommand);
      const unrelatedProjectionShape =
        state.workspaceTaskRequested &&
        !state.operationsRequired &&
        state.odsRequiredTools.size === 0 &&
        (nestedName === "pixel_ods_status" || nestedName === "pixel_ods_apps_list");
      // Once the exact owner-named artifact has been written, a read of that
      // path is verification, not a confused directory-inspection attempt.
      // Preserve it byte-for-byte instead of routing it back through exec.
      workspaceInspectionShape =
        !state.successfulWritePaths.has(state.workspaceTaskPath) &&
        (
          exactAuthorizedPathShape ||
          readDirectoryShape ||
          emptyProcessListShape ||
          execDirectoryShape ||
          unrelatedProjectionShape
        );
      if (!state.workspaceInspectionRouted && workspaceInspectionShape) {
        const inspectionPath = state.workspaceTaskPath;
        const command =
          `mkdir -p -- ${inspectionPath} && pwd && uname -sr && ls -la -- ${inspectionPath}`;
        pendingParams = {
          id: "openclaw:core:exec",
          args: { command },
        };
        state.workspaceInspectionRouted = true;
        workspaceInspectionAdapted = true;
      }
    }
    const workspaceDirectoryReady = Boolean(
      state?.workspaceTaskDirectory &&
      (
        state.workspaceInspectionRouted ||
        state.workspaceRequestedFiles.some((file) =>
          state.successfulWritePaths.has(`${state.workspaceTaskDirectory}/${file}`) ||
          state.successfulReadPaths.has(`${state.workspaceTaskDirectory}/${file}`)
        )
      )
    );
    if (
      state?.workspaceTaskDirectory &&
      workspaceDirectoryReady &&
      !workspaceInspectionAdapted &&
      !workspaceInspectionShape &&
      toolName === "tool_call" &&
      pendingParams &&
      typeof pendingParams === "object" &&
      !Array.isArray(pendingParams) &&
      pendingParams.id?.split(":").at(-1) === "exec" &&
      pendingParams.args &&
      typeof pendingParams.args === "object" &&
      !Array.isArray(pendingParams.args) &&
      typeof pendingParams.args.command === "string" &&
      pendingParams.args.workdir === undefined
    ) {
      pendingParams = {
        ...pendingParams,
        args: {
          ...pendingParams.args,
          workdir: `/workspace/${state.workspaceTaskDirectory}`,
        },
      };
    }
    if (
      state?.workspaceTaskDirectory &&
      state.latestVerificationStatus === "failed" &&
      toolName === "tool_call" &&
      pendingParams?.id?.split(":").at(-1) === "write" &&
      pendingParams.args &&
      typeof pendingParams.args === "object" &&
      !Array.isArray(pendingParams.args)
    ) {
      const repairPath = normalizeWorkspaceFilePath(pendingParams.args.path);
      const previousContent = state.successfulWriteContentByPath.get(repairPath);
      const replacementContent = pendingParams.args.content;
      const runWrittenWorkspacePath =
        typeof repairPath === "string" &&
        repairPath.startsWith(`${state.workspaceTaskDirectory}/`);
      const repairCount = state.compareSwapRepairCounts.get(repairPath) ?? 0;
      if (
        runWrittenWorkspacePath &&
        state.successfulWritePaths.has(repairPath) &&
        typeof previousContent === "string" &&
        typeof replacementContent === "string" &&
        previousContent !== replacementContent &&
        previousContent.length <= MAX_COMPARE_SWAP_REPAIR_CHARS &&
        replacementContent.length <= MAX_COMPARE_SWAP_REPAIR_CHARS &&
        repairCount < MAX_COMPARE_SWAP_REPAIRS_PER_PATH
      ) {
        // Compact models often regenerate a complete short file after a real
        // failed verification even when directed to use edit. Preserve the
        // no-clobber property by turning that replacement into an exact
        // compare-and-swap edit against only the bytes this run wrote inside
        // the owner-authorized workspace. A
        // concurrent or external change makes the edit fail instead of being
        // overwritten, and the per-path cap preserves the repair-loop fuse.
        pendingParams = {
          id: "edit",
          args: {
            path: repairPath,
            edits: [{ oldText: previousContent, newText: replacementContent }],
          },
        };
        state.compareSwapRepairCounts.set(repairPath, repairCount + 1);
      }
    }
    if (
      state?.workspaceTaskDirectory &&
      toolName === "tool_call" &&
      pendingParams?.id?.split(":").at(-1) === "exec" &&
      pendingParams.args &&
      typeof pendingParams.args === "object" &&
      !Array.isArray(pendingParams.args)
    ) {
      const canonicalUnittest = canonicalRequestedUnittestParams(
        pendingParams.args,
        state
      );
      if (canonicalUnittest) {
        pendingParams = { ...pendingParams, args: canonicalUnittest };
      }
    }
    if (
      state?.workspaceTaskDirectory &&
      toolName === "tool_call" &&
      pendingParams?.id?.split(":").at(-1) === "exec" &&
      pendingParams.args &&
      typeof pendingParams.args === "object" &&
      !Array.isArray(pendingParams.args) &&
      verificationExecFingerprint(pendingParams.args)
    ) {
      pendingParams = {
        ...pendingParams,
        args: {
          ...pendingParams.args,
          pty: false,
          background: false,
          yieldMs: Math.max(30_000, Number(pendingParams.args.yieldMs) || 0),
        },
      };
    }
    const pendingSelectedName =
      toolName === "tool_call" && typeof pendingParams?.id === "string"
        ? pendingParams.id.split(":").at(-1)
        : toolName;
    if (state && pendingSelectedName === WORKSPACE_PREVIEW_TOOL) {
      if (!state.workspacePreviewRequested) {
        return {
          block: true,
          blockReason:
            "Pixel blocked an unsolicited workspace publication. Publish a preview only when the owner's current request asks to build or display a website or browser-rendered visual.",
        };
      }
      const suppliedArgs =
        toolName === "tool_call" ? pendingParams?.args : pendingParams;
      if (suppliedArgs?.scaffold !== undefined) {
        return {
          block: true,
          blockReason:
            "Pixel blocked an ODS-authored creative scaffold. The active model must create the owner's requested artifact with workspace tools, then publish that exact directory.",
        };
      }
      const args = suppliedArgs;
      const providedDirectory = normalizeWorkspaceFilePath(args?.relativeDirectory);
      const observedDirectory = workspacePreviewDirectoryFromState(state);
      const directory = observedDirectory ?? providedDirectory;
      const hasObservedIndex =
        typeof directory === "string" &&
        (state.workspacePreviewAuthorshipRequired
          ? state.successfulWritePaths.has(`${directory}/index.html`)
          : (
            state.successfulWritePaths.has(`${directory}/index.html`) ||
            state.successfulReadPaths.has(`${directory}/index.html`)
          ));
      if (!directory || !hasObservedIndex) {
        return { block: true, blockReason: WORKSPACE_PREVIEW_REQUIRES_FILES_REASON };
      }
      state.workspacePreviewDirectory = directory;
      if (toolName === "tool_call") {
        pendingParams = {
          ...pendingParams,
          id: WORKSPACE_PREVIEW_TOOL,
          args: { relativeDirectory: directory },
        };
      } else {
        normalizedParams = { relativeDirectory: directory };
        pendingParams = normalizedParams;
      }
    }
    const selectedToolTarget =
      toolName === "tool_call" &&
      pendingParams &&
      typeof pendingParams === "object" &&
      !Array.isArray(pendingParams) &&
      typeof pendingParams.id === "string"
        ? pendingParams.id
        : toolName;
    const selectedToolName =
      toolName === "tool_call" && typeof selectedToolTarget === "string"
        ? selectedToolTarget.split(":").at(-1)
        : selectedToolTarget;
    const selectedParams =
      toolName === "tool_call" &&
      pendingParams?.args &&
      typeof pendingParams.args === "object" &&
      !Array.isArray(pendingParams.args)
        ? pendingParams.args
        : pendingParams;
    if (state?.workspaceVisualContinuationUnavailable) {
      return {
        block: true,
        blockReason: WORKSPACE_VISUAL_CONTINUATION_UNAVAILABLE_REASON,
      };
    }
    if (state?.workspaceVisualContinuationRequested) {
      const continuationDirectory = state.workspaceTaskDirectory;
      const selectedPath = FILE_PATH_TOOLS.has(selectedToolName)
        ? normalizeWorkspaceFilePath(selectedParams?.path)
        : undefined;
      const insideContinuationDirectory =
        typeof selectedPath === "string" &&
        typeof continuationDirectory === "string" &&
        selectedPath.startsWith(`${continuationDirectory}/`) &&
        selectedPath
          .slice(continuationDirectory.length + 1)
          .split("/")
          .every((part) => WORKSPACE_PATH_COMPONENT.test(part));
      if (
        !["read", "edit", "exec", "process", "tool_search", "tool_describe", WORKSPACE_PREVIEW_TOOL].includes(selectedToolName) ||
        (FILE_PATH_TOOLS.has(selectedToolName) && !insideContinuationDirectory)
      ) {
        return {
          block: true,
          blockReason: WORKSPACE_VISUAL_CONTINUATION_SCOPE_REASON,
        };
      }
      if (
        selectedToolName === "edit" &&
        !state.successfulReadPaths.has(selectedPath)
      ) {
        return {
          block: true,
          blockReason: WORKSPACE_VISUAL_CONTINUATION_REQUIRES_READ_REASON,
        };
      }
      if (
        selectedToolName === WORKSPACE_PREVIEW_TOOL &&
        !state.workspaceVisualContinuationEdited
      ) {
        return {
          block: true,
          blockReason: WORKSPACE_VISUAL_CONTINUATION_REQUIRES_EDIT_REASON,
        };
      }
    }
    const selectedEvent = selectedToolName === toolName
      ? { ...event, params: selectedParams }
      : { ...event, toolName: selectedToolName, params: selectedParams };
    const inspectionCompleteReason = () => {
      const nextFile = state?.workspaceRequestedFiles?.find((file) => {
        const path = state.workspaceTaskDirectory
          ? `${state.workspaceTaskDirectory}/${file}`
          : file;
        return !state.successfulWritePaths.has(path);
      });
      if (!nextFile) return WORKSPACE_INSPECTION_COMPLETE_REASON;
      const nextPath = state.workspaceTaskDirectory
        ? `${state.workspaceTaskDirectory}/${nextFile}`
        : nextFile;
      const pythonTestFile =
        /^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(nextFile);
      const testFileHint = !pythonTestFile
        ? ""
        : state.workspacePythonUnittestRequested
          ? " The owner explicitly requires unittest: include import unittest, at least one " +
            "class inheriting unittest.TestCase, and only the requested test_* methods; omit " +
            "comments, docstrings, helper cases, and a custom print runner."
          : " For a Python test file, include every required test-framework and implementation import.";
      return (
        "Inspection complete. Make exactly one tool call next: call tool_call with " +
        `id openclaw:core:write and args path ${JSON.stringify(nextPath)} plus content ` +
        "containing the complete requested file you compose. Keep it concise (under 1000 " +
        `characters when the requirements fit) and omit unrequested demos or CLI wrappers.${testFileHint} ` +
        "Do not call tool_search, read, exec, or process before this write."
      );
    };
    if (state?.workspaceTaskRequested && toolName === "tool_search") {
      const query = typeof event?.params?.query === "string"
        ? event.params.query.trim().replace(/\s+/g, " ").toLowerCase()
        : "";
      if (!state.workspaceToolSearchRouted) {
        state.workspaceToolSearchRouted = true;
        if (query) state.workspaceToolSearchQueries.add(query);
        state.workspaceToolSearchQueries.add(WORKSPACE_TOOL_SEARCH_QUERY);
        return {
          params: { query: WORKSPACE_TOOL_SEARCH_QUERY, limit: 6 },
        };
      }
      // Resolving core file tools does not resolve every capability a task may
      // need. Let the model discover a different capability (for example the
      // preview publisher after writing a site). Discovery grants no execution
      // authority; normal tool permissions and turn limits still apply.
      if (query && !state.workspaceToolSearchQueries.has(query)) {
        state.workspaceToolSearchQueries.add(query);
        return undefined;
      }
      if (state.workspaceInspectionRouted) {
        if (state.workspaceInspectionPollCorrections === 0) {
          state.workspaceInspectionPollCorrections = 1;
          return { block: true, blockReason: inspectionCompleteReason() };
        }
        state.codingExhausted = true;
        state.codingTerminalBlocks = 1;
        return { block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON };
      }
      return { block: true, blockReason: WORKSPACE_TOOL_SEARCH_COMPLETE_REASON };
    }
    if (
      state?.workspaceInspectionRouted &&
      toolName === "tool_call" &&
      !workspaceInspectionAdapted &&
      (
        workspaceInspectionShape ||
        (
          (selectedToolName === "exec" || selectedToolName === "process") &&
          selectedParams?.action === "poll" &&
          typeof selectedParams.sessionId !== "string" &&
          state.pendingExecSessions.size === 0
        )
      )
    ) {
      if (state.workspaceInspectionPollCorrections === 0) {
        state.workspaceInspectionPollCorrections = 1;
        return {
          block: true,
          blockReason: inspectionCompleteReason(),
        };
      }
      state.codingExhausted = true;
      state.codingTerminalBlocks = 1;
      return { block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON };
    }
    if (state) {
      const selectedReadPath = selectedToolName === "read"
        ? normalizeWorkspaceFilePath(selectedParams?.path)
        : undefined;
      if (
        state.latestVerificationStatus === "failed" &&
        selectedReadPath &&
        state.successfulWritePaths.has(selectedReadPath) &&
        /(?:^|\/)test[^/]*\.[A-Za-z0-9]+$/i.test(selectedReadPath)
      ) {
        if (state.failedTestReadCorrections === 0) {
          state.failedTestReadCorrections = 1;
          return { block: true, blockReason: FAILED_TEST_READ_REPAIR_REASON };
        }
        state.codingExhausted = true;
        state.codingTerminalBlocks = 1;
        return { block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON };
      }
      const writePath = selectedToolName === "write"
        ? normalizeWorkspaceFilePath(selectedParams?.path)
        : undefined;
      if (writePath && state.successfulWritePaths.has(writePath)) {
        const blocks = state.repeatedWriteBlocks.get(writePath) ?? 0;
        state.repeatedWriteBlocks.set(writePath, blocks + 1);
        if (blocks === 0) {
          return { block: true, blockReason: REPEATED_WRITE_REQUIRES_PATCH_REASON };
        }
        state.codingExhausted = true;
        state.codingTerminalBlocks = 1;
        return { block: true, blockReason: REPEATED_WRITE_RETRY_EXHAUSTED_REASON };
      }
      if (selectedToolName === "edit" && noOpEdit(selectedParams)) {
        if (state.noOpEditBlocks === 0) {
          state.noOpEditBlocks = 1;
          return { block: true, blockReason: NOOP_EDIT_REQUIRES_CHANGE_REASON };
        }
        state.codingExhausted = true;
        state.codingTerminalBlocks = 1;
        return { block: true, blockReason: NOOP_EDIT_RETRY_EXHAUSTED_REASON };
      }
      if (selectedToolName === "edit" && oversizedWholeFileEdit(selectedParams)) {
        if (state.oversizedEditBlocks === 0) {
          state.oversizedEditBlocks = 1;
          return { block: true, blockReason: FOCUSED_EDIT_REQUIRED_REASON };
        }
        state.codingExhausted = true;
        state.codingTerminalBlocks = 1;
        return { block: true, blockReason: FOCUSED_EDIT_RETRY_EXHAUSTED_REASON };
      }
      if (
        state.workspacePreviewRequested &&
        selectedToolName === "exec" &&
        execLaunchesWorkspaceServer(selectedParams)
      ) {
        return { block: true, blockReason: WORKSPACE_PREVIEW_REQUIRES_TOOL_REASON };
      }
      const setupDirectory =
        state.workspacePreviewRequested && selectedToolName === "exec"
          ? workspacePreviewMkdirDirectory(selectedParams)
          : undefined;
      if (setupDirectory) {
        return {
          block: true,
          blockReason:
            "Pixel does not need a separate directory-preparation command for this preview. " +
            `Call tool_call now with id write and path "${setupDirectory}/index.html" plus ` +
            "HTML authored entirely by the active model. Use a polished self-contained document, " +
            "or write any local assets inside that artifact directory before calling " +
            "pixel_ods_workspace_preview for that directory. ODS supplies no creative bytes.",
        };
      }
      rememberToolRun(
        context?.toolCallId ?? event?.toolCallId,
        runId,
        selectedToolName,
        selectedParams,
        selectedToolName === "exec"
          ? verificationExecFingerprint(selectedParams)
          : undefined
      );
      if (
        selectedToolName !== SYNCHRONOUS_HOST_OBSERVE_TOOL &&
        selectedToolName !== SYNCHRONOUS_HOST_COMMAND_TOOL &&
        state.operationsHostResultCompactionsRemaining > 0
      ) {
        state.operationsHostResultCompactionsRemaining = 0;
      }
    }
    if (toolName === "process" && state) {
      const params = normalizedParams ?? event?.params;
      const canonicalSessionId = canonicalPendingProcessSessionId(
        params,
        state.pendingExecSessions
      );
      if (canonicalSessionId && canonicalSessionId !== params?.sessionId) {
        normalizedParams = { ...params, sessionId: canonicalSessionId };
      }
    }

    if (state?.clientCancelled) {
      return { block: true, blockReason: CLIENT_CANCELLED_REASON };
    }

    const invalidNewFileEdit =
      selectedToolName === "edit" &&
      Array.isArray(selectedParams?.edits) &&
      selectedParams.edits.length > 0 &&
      selectedParams.edits.every(
        (entry) =>
          entry &&
          typeof entry === "object" &&
          !Array.isArray(entry) &&
          typeof entry.oldText === "string" &&
          entry.oldText.length === 0 &&
          typeof entry.newText === "string"
      );
    if (invalidNewFileEdit) {
      if (!state || state.invalidEditCreateBlocks === 0) {
        if (state) state.invalidEditCreateBlocks = 1;
        return { block: true, blockReason: EDIT_CREATE_REQUIRES_WRITE_REASON };
      }
      if (state.invalidEditCreateBlocks === 1) {
        state.invalidEditCreateBlocks = 2;
        return { block: true, blockReason: EDIT_CREATE_RETRY_EXHAUSTED_REASON };
      }
      let aborted = false;
      try {
        aborted = typeof abortRun === "function" && Boolean(abortRun(sessionId));
      } catch (error) {
        warn(`Pixel invalid-edit abort failed for run ${runId}: ${String(error)}`);
      }
      warn(
        `Pixel stopped a repeated invalid new-file edit for run ${runId}; active run aborted=${aborted}`
      );
      return { block: true, blockReason: EDIT_CREATE_LOOP_ABORT_REASON };
    }

    // Tool Search transports a selected catalog entry through `tool_call`.
    // Permit only an exact Operations target while host work is required; the
    // nested real tool call re-enters OpenClaw's normal policy and hooks, where
    // this guard still canonicalizes and validates every target, action, and
    // parameter before the broker sees it. Non-Operations catalog targets stay
    // blocked by the host boundary below.
    let wrappedToolParams = toolName === "tool_call"
      ? pendingParams
      : normalizedParams ?? event?.params;
    const wrappedToolTarget =
      toolName === "tool_call" &&
      wrappedToolParams &&
      typeof wrappedToolParams === "object" &&
      !Array.isArray(wrappedToolParams) &&
      typeof wrappedToolParams.id === "string"
        ? wrappedToolParams.id
        : undefined;
    const wrappedToolName = typeof wrappedToolTarget === "string"
      ? wrappedToolTarget.split(":").at(-1)
      : undefined;
    const effectiveToolName = wrappedToolName ?? toolName;
    if (
      state?.workspaceTaskRequested &&
      !state.operationsRequired &&
      state.odsRequiredTools.size === 0 &&
      (effectiveToolName === "pixel_ods_status" ||
        effectiveToolName === "pixel_ods_apps_list")
    ) {
      return {
        block: true,
        blockReason: state.workspaceInspectionRouted
          ? inspectionCompleteReason()
          : WORKSPACE_UNREQUESTED_PROJECTION_REASON,
      };
    }
    const workspaceOperation =
      effectiveToolName === EVIDENCE_REPORT_TOOL
        ? "write"
        : effectiveToolName === EVIDENCE_READBACK_TOOL
          ? "read"
          : effectiveToolName;
    if (
      state?.operationsWorkspaceContinuationRequested &&
      state.operationsWorkspaceExpectedPath &&
      (workspaceOperation === "write" || workspaceOperation === "read")
    ) {
      const sourceArgs = toolName === "tool_call"
        ? wrappedToolParams?.args
        : normalizedParams ?? event?.params;
      const verifiedEvidence = state.operationsWorkspaceEvidenceArtifactRequested
        ? operationsEvidenceText(
          state.operationsRequiredActions,
          state.operationsTerminalJobs,
          state.operationsOdsAppsProjection,
          state.operationsOdsStatusProjection
        )
        : undefined;
      if (workspaceOperation === "read") {
        normalizedParams = toolName === "tool_call"
          ? { id: "read", args: { path: state.operationsWorkspaceExpectedPath } }
          : { path: state.operationsWorkspaceExpectedPath };
      } else {
        const suppliedContent = sourceArgs && typeof sourceArgs === "object" && !Array.isArray(sourceArgs)
          ? typeof sourceArgs.content === "string"
            ? sourceArgs.content
            : typeof sourceArgs.text === "string"
              ? sourceArgs.text
              : undefined
          : undefined;
        const content = verifiedEvidence ? `${verifiedEvidence}\n` : suppliedContent;
        if (typeof content === "string") {
          normalizedParams = toolName === "tool_call"
            ? {
              id: "write",
              args: { path: state.operationsWorkspaceExpectedPath, content },
            }
            : { path: state.operationsWorkspaceExpectedPath, content };
        }
      }
      wrappedToolParams = normalizedParams;
    }
    if (
      state?.operationsRequired &&
      effectiveToolName === SYNCHRONOUS_HOST_OBSERVE_TOOL
    ) {
      const actions = exactRequiredHostActions(state);
      if (!actions || state.operationsSubmittedJobs.size > 0) {
        return { block: true, blockReason: OPERATIONS_REQUIRES_BROKER_REASON };
      }
      const params = {
        actions,
        ...(actions.includes("host.network-peer") && state.operationsNetworkPeer
          ? {
            peer: state.operationsNetworkPeer.peer,
            ports: state.operationsNetworkPeer.ports,
          }
          : {}),
        ...(state.operationsRequiresOdsStatusProjection
          ? { includeOdsStatus: true }
          : {}),
      };
      return toolName === "tool_call"
        ? { params: { id: SYNCHRONOUS_HOST_OBSERVE_TOOL, args: params } }
        : { params };
    }
    if (
      state?.operationsRequired &&
      (effectiveToolName === "pixel_ops_job_get" || effectiveToolName === "pixel_ops_job_wait")
    ) {
      const requestedArgs = toolName === "tool_call"
        ? wrappedToolParams?.args
        : normalizedParams ?? event?.params;
      const pendingJobIds = [...state.operationsSubmittedJobs.keys()].filter(
        (jobId) => !state.operationsTerminalJobs.has(jobId)
      );
      const requestedJobId = requestedArgs && typeof requestedArgs === "object"
        ? requestedArgs.jobId ?? requestedArgs.sessionId
        : undefined;
      const jobId = state.operationsContinuation?.jobId ??
        (pendingJobIds.includes(requestedJobId)
          ? requestedJobId
          : pendingJobIds.length === 1
            ? pendingJobIds[0]
            : undefined);
      if (jobId) {
        return toolName === "tool_call"
          ? { params: { id: effectiveToolName, args: { jobId } } }
          : { params: { jobId } };
      }
    }
    if (state?.operationsRequired && !state.operationsHostCommandRequested &&
        effectiveToolName === SYNCHRONOUS_HOST_COMMAND_TOOL) {
      return { block: true, blockReason: OPERATIONS_REQUIRES_BROKER_REASON };
    }
    if (state?.operationsHostCommandRequested) {
      if (state.operationsTerminalJobs.size > 0) {
        return { block: true, blockReason: OPERATIONS_HOST_COMMAND_COMPLETE_REASON };
      }
      if (
        effectiveToolName === SYNCHRONOUS_HOST_COMMAND_TOOL ||
        (toolName === "tool_call" && effectiveToolName === "pixel_ops_shell_propose")
      ) {
        if (state.operationsSubmittedJobs.size > 0) {
          return { block: true, blockReason: OPERATIONS_HOST_COMMAND_REQUIRES_PROPOSAL_REASON };
        }
        const requestedArgs = toolName === "tool_call"
          ? wrappedToolParams?.args
          : normalizedParams ?? event?.params;
        const command = state.operationsExactHostCommand ?? requestedArgs?.command;
        if (
          typeof command !== "string" ||
          !command.trim() ||
          command.length > 16_384 ||
          Buffer.byteLength(command, "utf8") > 16_384 ||
          command.includes("\0")
        ) {
          return { block: true, blockReason: OPERATIONS_HOST_COMMAND_REQUIRES_PROPOSAL_REASON };
        }
        const params = { command };
        return toolName === "tool_call"
          ? { params: { id: SYNCHRONOUS_HOST_COMMAND_TOOL, args: params } }
          : { params };
      }
      return { block: true, blockReason: OPERATIONS_HOST_COMMAND_REQUIRES_PROPOSAL_REASON };
    }
    if (state?.operationsInventoryOnly) {
      if (state.operationsInventory) {
        return { block: true, blockReason: OPERATIONS_INVENTORY_COMPLETE_REASON };
      }
      if (state.operationsInventoryAttempted) {
        return {
          block: true,
          blockReason: OPERATIONS_INVENTORY_UNVERIFIED_DELIVERY_PREFIX,
        };
      }
      if (effectiveToolName === "pixel_ops_inventory") {
        return toolName === "tool_call"
          ? { params: { id: "pixel_ops_inventory", args: {} } }
          : { params: {} };
      }
      return { block: true, blockReason: OPERATIONS_INVENTORY_REQUIRES_TOOL_REASON };
    }
    if (
      state?.operationsRequired &&
      toolName === "tool_call" &&
      OPERATIONS_TOOLS.has(effectiveToolName)
    ) {
      return undefined;
    }
    const operationsJobsAreTerminal =
      state?.operationsRequired === true &&
      state.operationsSubmittedJobs.size > 0 &&
      [...state.operationsSubmittedJobs.keys()].every((jobId) =>
        state.operationsTerminalJobs.has(jobId)
      );
    if (
      toolName === "tool_call" &&
      operationsJobsAreTerminal &&
      (
        (effectiveToolName === "pixel_ods_status" &&
          state.operationsRequiresOdsStatusProjection &&
          !state.operationsOdsStatusProjectionAttempted &&
          !state.operationsOdsStatusProjectionToolSearchPending) ||
        (effectiveToolName === "pixel_ods_apps_list" &&
          state.operationsRequiresOdsAppsProjection &&
          !state.operationsOdsAppsProjectionAttempted &&
          !state.operationsOdsAppsProjectionToolSearchPending)
      )
    ) {
      // The nested projection call re-enters this guard and consumes the exact
      // one-call allowance when the runtime exposes nested hooks. The outer
      // result is also validated below because same-plugin Tool Search calls
      // are intentionally not re-hooked by every OpenClaw runtime.
      if (effectiveToolName === "pixel_ods_status") {
        state.operationsOdsStatusProjectionToolSearchPending = true;
      } else {
        state.operationsOdsAppsProjectionToolSearchPending = true;
      }
      return undefined;
    }

    if (
      state?.ownerIntentObserved &&
      !state.operationsRequired &&
      !state.exactDownloadRequested &&
      OPERATIONS_TOOLS.has(effectiveToolName)
    ) {
      // Count offending model rounds cumulatively, not parallel siblings or
      // unrelated authorized workspace calls. Without model-round telemetry,
      // each subsequent offending selection is the bounded fallback attempt.
      if (state.operationsPromptRound === 0 ||
          state.operationsPromptRound !== state.unrequestedOperationsDeniedRound) {
        state.unrequestedOperationsDeniedRounds += 1;
      }
      state.unrequestedOperationsDeniedRound = state.operationsPromptRound;
      state.unrequestedOperationsTerminal = state.unrequestedOperationsDeniedRounds >= 2;
      return {
        block: true,
        blockReason: state.unrequestedOperationsTerminal
          ? UNREQUESTED_OPERATIONS_TERMINAL_REASON
          : OPERATIONS_NOT_REQUESTED_REASON,
      };
    }

    if (state?.exactDownloadRequested && state.exactDownloadPromotion) {
      return { block: true, blockReason: EXACT_DOWNLOAD_COMPLETE_REASON };
    }

    if (
      state?.workspacePreview &&
      !state.operationsRequired &&
      !state.exactDownloadRequested
    ) {
      if (workspacePreviewReadbackComplete(state)) {
        return { block: true, blockReason: WORKSPACE_PREVIEW_COMPLETE_REASON };
      }
      const selectedReadPath = selectedToolName === "read"
        ? normalizeWorkspaceFilePath(selectedParams?.path)
        : undefined;
      const previewPrefix = `${state.workspacePreview.relativeDirectory}/`;
      const safeUnreadPreviewPath =
        typeof selectedReadPath === "string" &&
        selectedReadPath.startsWith(previewPrefix) &&
        selectedReadPath
          .slice(previewPrefix.length)
          .split("/")
          .every((part) => WORKSPACE_PATH_COMPONENT.test(part)) &&
        !state.successfulReadPaths.has(selectedReadPath);
      if (!safeUnreadPreviewPath) {
        return {
          block: true,
          blockReason: WORKSPACE_PREVIEW_REQUIRES_READBACK_REASON,
        };
      }
    }

    if (state?.exactDownloadRequested && !EXACT_DOWNLOAD_BROKER_TOOLS.has(effectiveToolName)) {
      if (state.exactDownloadTerminalBlocks === 0) {
        state.exactDownloadTerminalBlocks = 1;
        return {
          block: true,
          blockReason: state.exactDownloadArtifact
            ? EXACT_DOWNLOAD_REQUIRES_PROMOTION_REASON
            : state.exactDownloadSubmissions.size > 0
              ? EXACT_DOWNLOAD_REQUIRES_WAIT_REASON
              : EXACT_DOWNLOAD_REQUIRES_BROKER_REASON,
        };
      }
      let aborted = false;
      try {
        aborted = typeof abortRun === "function" && Boolean(abortRun(sessionId));
      } catch (error) {
        warn(`Pixel exact-download abort failed for run ${runId}: ${String(error)}`);
      }
      warn(
        `Pixel stopped a tool retry after the exact-download boundary for run ${runId}; active run aborted=${aborted}`
      );
      return { block: true, blockReason: EXACT_DOWNLOAD_LOOP_ABORT_REASON };
    }

    if (state?.exactDownloadRequested) {
      const request = state.exactDownloadRequest;
      const exactDownloadParams = (selectedToolName, params) =>
        toolName === "tool_call"
          ? { params: { id: selectedToolName, args: params } }
          : { params };
      if (!request?.url || !request?.filename || !request?.relativePath) {
        return { block: true, blockReason: EXACT_DOWNLOAD_REQUEST_UNBOUND_REASON };
      }
      if (effectiveToolName === "pixel_ops_download_stage") {
        if (state.exactDownloadArtifact) {
          return { block: true, blockReason: EXACT_DOWNLOAD_REQUIRES_PROMOTION_REASON };
        }
        if (state.exactDownloadSubmissions.size > 0) {
          return { block: true, blockReason: EXACT_DOWNLOAD_REQUIRES_WAIT_REASON };
        }
        const params = { url: request.url, filename: request.filename };
        if (request.expectedSha256) params.expectedSha256 = request.expectedSha256;
        return exactDownloadParams("pixel_ops_download_stage", params);
      }
      if (effectiveToolName === "pixel_ops_job_get" || effectiveToolName === "pixel_ops_job_wait") {
        const submission = [...state.exactDownloadSubmissions.values()].at(-1);
        if (!submission) {
          return { block: true, blockReason: EXACT_DOWNLOAD_REQUIRES_BROKER_REASON };
        }
        if (state.exactDownloadArtifact) {
          return { block: true, blockReason: EXACT_DOWNLOAD_REQUIRES_PROMOTION_REASON };
        }
        return exactDownloadParams(effectiveToolName, { jobId: submission.jobId });
      }
      if (effectiveToolName === "pixel_ods_download_promote") {
        const artifact = state.exactDownloadArtifact;
        if (!artifact) {
          return {
            block: true,
            blockReason:
              state.exactDownloadSubmissions.size > 0
                ? EXACT_DOWNLOAD_REQUIRES_WAIT_REASON
                : EXACT_DOWNLOAD_REQUIRES_BROKER_REASON,
          };
        }
        return exactDownloadParams("pixel_ods_download_promote", {
          jobId: artifact.jobId,
          filename: artifact.filename,
          relativePath: artifact.relativePath,
          sha256: artifact.sha256,
          sourceUrl: artifact.requestedSource,
        });
      }
    }

    if (state?.operationsContinuation) {
      if (state.operationsContinuationOutcome) {
        return { block: true, blockReason: OPERATIONS_CONTINUATION_COMPLETE_REASON };
      }
      if (toolName === "pixel_ops_job_get" || toolName === "pixel_ops_job_wait") {
        return { params: { jobId: state.operationsContinuation.jobId } };
      }
      if (state.operationsTerminalBlocks === 0) {
        state.operationsTerminalBlocks = 1;
        return {
          block: true,
          blockReason: OPERATIONS_CONTINUATION_REQUIRES_STATUS_REASON,
        };
      }
      let aborted = false;
      try {
        aborted = typeof abortRun === "function" && Boolean(abortRun(sessionId));
      } catch (error) {
        warn(`Pixel Operations-continuation abort failed for run ${runId}: ${String(error)}`);
      }
      warn(
        `Pixel stopped a tool retry after the Operations-continuation boundary for run ${runId}; active run aborted=${aborted}`
      );
      return { block: true, blockReason: OPERATIONS_LOOP_ABORT_REASON };
    }

    const operationsMayReadOdsApps =
      state?.operationsRequired === true &&
      state.operationsRequiresOdsAppsProjection === true &&
      toolName === "pixel_ods_apps_list" &&
      state.operationsOdsAppsProjectionAttempted === false &&
      state.operationsSubmittedJobs.size > 0 &&
      [...state.operationsSubmittedJobs.keys()].every((jobId) =>
        state.operationsTerminalJobs.has(jobId)
      );
    const operationsMayReadOdsStatus =
      state?.operationsRequired === true &&
      state.operationsRequiresOdsStatusProjection === true &&
      toolName === "pixel_ods_status" &&
      state.operationsOdsStatusProjectionAttempted === false &&
      state.operationsSubmittedJobs.size > 0 &&
      [...state.operationsSubmittedJobs.keys()].every((jobId) =>
        state.operationsTerminalJobs.has(jobId)
      );
    const operationsMayContinueInWorkspace =
      state?.operationsRequired === true &&
      state.operationsWorkspaceContinuationRequested === true &&
      WORKSPACE_CONTINUATION_TOOLS.has(effectiveToolName) &&
      verificationForRun(runId).status === "passed";
    if (operationsMayReadOdsApps) {
      state.operationsOdsAppsProjectionAttempted = true;
    } else if (operationsMayReadOdsStatus) {
      state.operationsOdsStatusProjectionAttempted = true;
    } else if (
      state?.operationsRequired &&
      !OPERATIONS_TOOLS.has(toolName) &&
      !operationsMayContinueInWorkspace
    ) {
      // One model response may contain several parallel tool calls. Return the
      // same correction to every disallowed call in that response instead of
      // treating the second sibling call as a second ignored correction. A
      // later model continuation that still ignores the boundary is aborted.
      state.operationsRoutingBlocks += 1;
      if (
        state.operationsRoutingBlocks < 4 &&
        (state.operationsCorrectionPromptRound === undefined ||
          state.operationsCorrectionPromptRound === state.operationsPromptRound)
      ) {
        state.operationsCorrectionPromptRound = state.operationsPromptRound;
        const missingProjection =
          operationsJobsAreTerminal &&
          (
            (state.operationsRequiresOdsStatusProjection &&
              !state.operationsOdsStatusProjectionAttempted) ||
            (state.operationsRequiresOdsAppsProjection &&
              !state.operationsOdsAppsProjectionAttempted)
          );
        return {
          block: true,
          blockReason: missingProjection
            ? OPERATIONS_REQUIRES_PROJECTIONS_REASON
            : OPERATIONS_REQUIRES_BROKER_REASON,
        };
      }
      let aborted = false;
      try {
        aborted = typeof abortRun === "function" && Boolean(abortRun(sessionId));
      } catch (error) {
        warn(`Pixel Operations-routing abort failed for run ${runId}: ${String(error)}`);
      }
      warn(
        `Pixel stopped a tool retry after the Operations boundary for run ${runId}; active run aborted=${aborted}`
      );
      return { block: true, blockReason: OPERATIONS_LOOP_ABORT_REASON };
    }

    if (
      state?.operationsRequiredActions?.size > 0 &&
      OPERATIONS_SUBMISSION_TOOLS.has(toolName)
    ) {
      let params = normalizedParams ?? event?.params;
      const lifecycle = state.operationsExpectedExtensionLifecycle;
      if (lifecycle && toolName === "pixel_ops_workflow_submit") {
        return {
          block: true,
          blockReason: OPERATIONS_EXTENSION_LIFECYCLE_SEQUENCE_REASON,
        };
      }
      if (lifecycle && toolName === "pixel_ops_run") {
        const permittedLifecycleActions = new Set([
          "ods.extensions.inspect",
          `ods.extensions.${lifecycle.action}`,
        ]);
        if (permittedLifecycleActions.has(params?.action)) {
          params = {
            target: "ods-host",
            action: params.action,
            parameters: { serviceId: lifecycle.serviceId },
          };
          normalizedParams = params;
          const alreadySubmitted = [...state.operationsSubmittedJobs.values()].some(
            (submission) => submission.actions?.some((entry) => entry.action === params.action)
          );
          if (alreadySubmitted) {
            return {
              block: true,
              blockReason: OPERATIONS_EXTENSION_LIFECYCLE_SEQUENCE_REASON,
            };
          }
          if (params.action !== "ods.extensions.inspect") {
            const inspection = parsedLifecycleOutcome(
              state.operationsTerminalJobs,
              "ods.extensions.inspect"
            );
            if (
              !inspection ||
              inspection.result.outcome !== "ready" ||
              inspection.result.extensionId !== lifecycle.serviceId
            ) {
              return {
                block: true,
                blockReason: OPERATIONS_EXTENSION_LIFECYCLE_SEQUENCE_REASON,
              };
            }
          }
        }
      }
      if (
        toolName === "pixel_ops_run" &&
        state.operationsRequiredActions.size === 1 &&
        state.operationsRequiredActions.has("ods.extensions.list") &&
        params?.action === "ods.extensions.list"
      ) {
        params = { target: "ods-host", action: "ods.extensions.list" };
        normalizedParams = params;
      }
      // Some otherwise-capable models shorten the single local ODS target to
      // `host` or omit the `host.` namespace from an otherwise exact action.
      // Canonicalize only those exact aliases, only for observations the
      // current owner request already requires, and never for a different
      // target, action, parameter, tier, or authority level.
      const requiresHostWorkflow =
        state.operationsRequiredActions.size > 1 &&
        [...state.operationsRequiredActions].every((action) => action.startsWith("host."));
      if (toolName === "pixel_ops_run" && requiresHostWorkflow) {
        return {
          block: true,
          blockReason: `${OPERATIONS_REQUIRES_WORKFLOW_REASON} Required actions: ${[
            ...state.operationsRequiredActions,
          ].join(", ")}.`,
        };
      }
      if (
        toolName === "pixel_ops_run" &&
        ["host", "ods-host"].includes(params?.target) &&
        typeof params?.action === "string" &&
        (
          params.target === "host" ||
          (
            !state.operationsRequiredActions.has(params.action) &&
            state.operationsRequiredActions.has(`host.${params.action}`)
          )
        ) &&
        (
          state.operationsRequiredActions.has(params.action) ||
          state.operationsRequiredActions.has(`host.${params.action}`)
        )
      ) {
        const action = state.operationsRequiredActions.has(params.action)
          ? params.action
          : `host.${params.action}`;
        params = { ...params, target: "ods-host", action };
        normalizedParams = params;
      } else if (
        toolName === "pixel_ops_workflow_submit" &&
        Array.isArray(params?.steps) &&
        params.steps.length > 0 &&
        params.steps.some(
          (step) =>
            step?.target === "host" ||
            (
              typeof step?.action === "string" &&
              !state.operationsRequiredActions.has(step.action) &&
              state.operationsRequiredActions.has(`host.${step.action}`)
            )
        ) &&
        params.steps.every(
          (step) =>
            step &&
            typeof step === "object" &&
            !Array.isArray(step) &&
            ["host", "ods-host"].includes(step.target) &&
            typeof step.action === "string" &&
            (
              state.operationsRequiredActions.has(step.action) ||
              state.operationsRequiredActions.has(`host.${step.action}`)
            )
        )
      ) {
        params = {
          ...params,
          steps: params.steps.map((step) => ({
            ...step,
            target: "ods-host",
            action: state.operationsRequiredActions.has(step.action)
              ? step.action
              : `host.${step.action}`,
          })),
        };
        normalizedParams = params;
      }
      let actions = [];
      if (toolName === "pixel_ops_run") {
        actions = [{ target: params?.target, action: params?.action }];
      } else if (toolName === "pixel_ops_workflow_submit" && Array.isArray(params?.steps)) {
        actions = params.steps.map((step) => ({
          target: step?.target,
          action: step?.action,
        }));
      }
      const matches =
        actions.length > 0 &&
        actions.every(
          ({ target, action }) =>
            target === "ods-host" && state.operationsRequiredActions.has(action)
        );
      if (!matches) {
        return {
          block: true,
          blockReason: `${OPERATIONS_WRONG_ACTION_REASON} Required actions: ${[
            ...state.operationsRequiredActions,
          ].join(", ")}.`,
        };
      }
      if (
        toolName === "pixel_ops_run" &&
        params?.action === "ods.extensions.search" &&
        state.operationsExpectedQuery !== undefined &&
        params?.parameters?.query !== state.operationsExpectedQuery
      ) {
        return {
          params: {
            target: "ods-host",
            action: "ods.extensions.search",
            parameters: { query: state.operationsExpectedQuery },
          },
        };
      }
      if (normalizedParams !== undefined) return { params: normalizedParams };
    }

    if (
      selectedToolName === "exec" &&
      requestsRecursiveForcedDelete(selectedParams) &&
      !state?.recursiveDeleteAuthorized
    ) {
      return { block: true, blockReason: RECURSIVE_DELETE_REQUIRES_OWNER_REASON };
    }

    if (
      selectedToolName === "exec" &&
      !verificationCommandIsAuditable(selectedParams)
    ) {
      if (state) state.latestVerificationStatus = "failed";
      return { block: true, blockReason: VERIFICATION_COMMAND_NOT_AUDITABLE_REASON };
    }

    if (state?.privateNetworkPrompt) {
      state.privateNetworkPrompt = false;
      state.privateNetworkExhausted = true;
      return { block: true, blockReason: PRIVATE_URL_REQUEST_REASON };
    }

    if (state?.privateNetworkExhausted) {
      let aborted = false;
      try {
        aborted = typeof abortRun === "function" && Boolean(abortRun(sessionId));
      } catch (error) {
        warn(`Pixel private-network abort failed for run ${runId}: ${String(error)}`);
      }
      warn(
        `Pixel stopped a tool retry after a private-network block for run ${runId}; active run aborted=${aborted}`
      );
      return { block: true, blockReason: PRIVATE_NETWORK_LOOP_ABORT_REASON };
    }

    if (state?.odsRoutingExhausted) {
      if (state.odsRoutingTerminalBlocks === 0) {
        state.odsRoutingTerminalBlocks = 1;
        return { block: true, blockReason: ODS_TOOL_ROUTING_ABORT_REASON };
      }
      let aborted = false;
      try {
        aborted = typeof abortRun === "function" && Boolean(abortRun(sessionId));
      } catch (error) {
        warn(`Pixel ODS-routing abort failed for run ${runId}: ${String(error)}`);
      }
      warn(
        `Pixel stopped a tool retry after an ODS-routing block for run ${runId}; active run aborted=${aborted}`
      );
      return { block: true, blockReason: ODS_TOOL_ROUTING_LOOP_ABORT_REASON };
    }

    if (state?.odsRequiredTools.size > 0) {
      if (state.odsRequiredTools.has(effectiveToolName)) {
        state.odsRequiredTools.delete(effectiveToolName);
        state.odsRoutingBlocks = 0;
      } else if (state.odsRoutingBlocks === 0) {
        state.odsRoutingBlocks = 1;
        const required = [...state.odsRequiredTools].join(" and ");
        return {
          block: true,
          blockReason:
            `This request asks for ODS facts exposed by dedicated read-only tools. ` +
            `Before any other tool, call ${required} exactly once. Then continue the owner's remaining work normally.`,
        };
      } else {
        state.odsRoutingExhausted = true;
        return { block: true, blockReason: ODS_TOOL_ROUTING_ABORT_REASON };
      }
    }

    if (
      (toolName === "web_fetch" || toolName === "pixel_ods_web_extract") &&
      fetchTargetsNonPublicAddress(event)
    ) {
      if (state) state.privateNetworkExhausted = true;
      warn("Pixel blocked a non-public web_fetch destination before execution");
      return { block: true, blockReason: WEB_FETCH_PUBLIC_ONLY_REASON };
    }
    if (selectedToolName === "exec" && execTargetsNonPublicAddress(selectedEvent)) {
      if (state) state.privateNetworkExhausted = true;
      warn("Pixel blocked an exec-based private HTTP(S) destination before execution");
      return { block: true, blockReason: EXEC_PRIVATE_NETWORK_REASON };
    }

    if (state?.targetedExtractPending) {
      const requestedUrl = canonicalFetchUrl(event);
      const exactGitHubFileContinuation =
        toolName === "web_fetch" &&
        state.githubFileUrl &&
        requestedUrl === state.githubFileUrl &&
        state.targetedExtractPending === state.githubReadmeUrl;
      if (exactGitHubFileContinuation) {
        // The owner explicitly named this validated file in the same canonical
        // repository. A truncated README may already contain the requested
        // repository description, so allow the exact second source instead of
        // forcing an irrelevant same-page extraction.
        state.targetedExtractPending = undefined;
        state.targetedExtractBlocks = 0;
      } else if (
        toolName === "pixel_ods_web_extract" &&
        requestedUrl === state.targetedExtractPending
      ) {
        state.targetedExtractPending = undefined;
        state.targetedExtractBlocks = 0;
      } else if (state.targetedExtractBlocks === 0) {
        state.targetedExtractBlocks = 1;
        return { block: true, blockReason: WEB_FETCH_TRUNCATED_PIVOT_REASON };
      } else {
        state.targetedExtractPending = undefined;
        state.webExhausted = true;
        return { block: true, blockReason: WEB_BUDGET_EXHAUSTED_REASON };
      }
    }

    if (
      state?.githubCanonicalUrl &&
      !state.githubCanonicalSatisfied &&
      WEB_TOOLS.has(toolName)
    ) {
      if (state.githubCanonicalFailed) {
        state.webExhausted = true;
        return { block: true, blockReason: GITHUB_CANONICAL_FETCH_FAILED_REASON };
      }
      const requestedUrl = canonicalFetchUrl(event);
      if (
        toolName === "web_fetch" &&
        requestedUrl === state.githubReadmeUrl
      ) {
        state.githubCanonicalBlocks = 0;
      } else if (state.githubCanonicalBlocks === 0) {
        state.githubCanonicalBlocks = 1;
        return {
          block: true,
          blockReason:
            `${GITHUB_CANONICAL_SOURCE_PREFIX} ${state.githubCanonicalUrl}. ` +
            `Do not search or narrate a retry. Call web_fetch once with exactly ${state.githubReadmeUrl} now.`,
        };
      } else {
        state.webExhausted = true;
        return { block: true, blockReason: WEB_BUDGET_EXHAUSTED_REASON };
      }
    }

    if (WEB_TOOLS.has(toolName) && (!runId || !sessionId)) {
      return {
        block: true,
        blockReason:
          "Pixel could not establish the bounded run identity required for web access. Do not call another tool in this turn; explain that web research is temporarily unavailable.",
      };
    }

    // Non-web tool hooks in OpenClaw can omit sessionId even though the exact
    // runId was established by before_prompt_build. The run-bound execution
    // wrapper, policy state, and retry fuses all key on runId, so keep them
    // active. Web access still requires both identities above, and a missing
    // runId remains fail-closed for cancellable execution.
    if (!runId) {
      if (selectedToolName === "exec" && execControl) {
        return { block: true, blockReason: CANCELLABLE_EXEC_UNAVAILABLE_REASON };
      }
      const effectiveParams = toolName === "tool_call"
        ? wrappedToolParams
        : normalizedParams;
      return effectiveParams && effectiveParams !== event?.params
        ? { params: effectiveParams }
        : undefined;
    }

    if (state.webExhausted) {
      if (state.webTerminalBlocks === 0) {
        state.webTerminalBlocks = 1;
        return { block: true, blockReason: WEB_BUDGET_EXHAUSTED_REASON };
      }
      let aborted = false;
      try {
        aborted = typeof abortRun === "function" && Boolean(abortRun(sessionId));
      } catch (error) {
        warn(`Pixel web-loop abort failed for run ${runId}: ${String(error)}`);
      }
      warn(
        `Pixel stopped a repeated web-tool loop for run ${runId}; active run aborted=${aborted}`
      );
      return { block: true, blockReason: WEB_LOOP_ABORT_REASON };
    }

    if (state.codingExhausted) {
      if (state.codingTerminalBlocks === 0) {
        state.codingTerminalBlocks = 1;
        return { block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON };
      }
      let aborted = false;
      try {
        aborted = typeof abortRun === "function" && Boolean(abortRun(sessionId));
      } catch (error) {
        warn(`Pixel coding-loop abort failed for run ${runId}: ${String(error)}`);
      }
      warn(
        `Pixel stopped a repeated coding-tool loop for run ${runId}; active run aborted=${aborted}`
      );
      return { block: true, blockReason: CODING_LOOP_ABORT_REASON };
    }

    if (selectedToolName === "exec") {
      // A malformed model envelope is not a failed cancellation boundary.
      // Reject it before prepare(), permit a bounded schema correction, and
      // still route the corrected call through every normal safety check.
      // Do not unwrap nested objects or infer missing command/control fields.
      if (typeof selectedParams?.command !== "string" || !selectedParams.command.trim()) {
        state.invalidExecArgumentAttempts += 1;
        if (state.invalidExecArgumentAttempts >= effective.failedExecRetries) {
          state.codingExhausted = true;
          state.codingTerminalBlocks = 1;
          return { block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON };
        }
        return { block: true, blockReason: EXEC_ARGUMENTS_REQUIRE_COMMAND_REASON };
      }
      const fingerprint = execFingerprint(selectedParams);
      const verificationFingerprint = verificationExecFingerprint(
        selectedParams
      );
      const pendingSessionId = fingerprint
        ? [...state.pendingExecSessions.entries()].find(
            ([, pending]) => pending?.fingerprint === fingerprint
          )?.[0]
        : undefined;
      if (pendingSessionId) {
        const blocks = state.pendingExecBlocks.get(pendingSessionId) ?? 0;
        if (blocks === 0) {
          state.pendingExecBlocks.set(pendingSessionId, 1);
          return { block: true, blockReason: PENDING_EXEC_REQUIRES_POLL_REASON };
        }
        if (blocks === 1) {
          state.pendingExecBlocks.set(pendingSessionId, 2);
          return { block: true, blockReason: PENDING_EXEC_RETRY_EXHAUSTED_REASON };
        }
        let aborted = false;
        try {
          aborted = typeof abortRun === "function" && Boolean(abortRun(sessionId));
        } catch (error) {
          warn(`Pixel pending-exec abort failed for run ${runId}: ${String(error)}`);
        }
        warn(
          `Pixel stopped repeated restarts of pending process ${pendingSessionId} for run ${runId}; active run aborted=${aborted}`
        );
        return { block: true, blockReason: PENDING_EXEC_LOOP_ABORT_REASON };
      }
      if (fingerprint && (state.successfulExec.get(fingerprint) ?? 0) >= 2) {
        const blocks = state.successfulExecBlocks.get(fingerprint) ?? 0;
        if (blocks === 0) {
          state.successfulExecBlocks.set(fingerprint, 1);
          return { block: true, blockReason: CODING_REPEAT_NO_PROGRESS_REASON };
        }
        state.codingExhausted = true;
        state.codingTerminalBlocks = 1;
        return { block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON };
      }
      if (
        (fingerprint &&
          (state.failedExec.get(fingerprint) ?? 0) >= effective.failedExecRetries) ||
        (verificationFingerprint &&
          state.failedVerificationAttempts >= effective.failedVerificationAttempts)
      ) {
        state.codingExhausted = true;
        return { block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON };
      }
    }

    if (!WEB_TOOLS.has(toolName)) {
      if (selectedToolName === "exec" && execControl) {
        const params = { ...selectedParams };
        const originalFingerprint = execFingerprint(params);
        const originalVerificationFingerprint = verificationExecFingerprint(params);
        try {
          params.command = execControl.prepare(runId, params.command);
        } catch (error) {
          warn(`Pixel cancellable exec preparation failed for run ${runId}: ${String(error)}`);
          // The correction text is explicitly terminal. If the model ignores
          // it and asks for another tool, abort this run instead of allowing
          // parameter-shape or timeout variations to create an unbounded loop.
          if (state) {
            state.codingExhausted = true;
            state.codingTerminalBlocks = 1;
          }
          return { block: true, blockReason: CANCELLABLE_EXEC_UNAVAILABLE_REASON };
        }
        const wrappedFingerprint = execFingerprint(params);
        if (originalFingerprint && wrappedFingerprint) {
          state.execOriginalByWrapped.set(wrappedFingerprint, originalFingerprint);
        }
        if (originalVerificationFingerprint && wrappedFingerprint) {
          state.verificationOriginalByWrapped.set(
            wrappedFingerprint,
            originalVerificationFingerprint
          );
        }
        return {
          params: toolName === "tool_call"
            ? { ...pendingParams, id: pendingParams.id, args: params }
            : params,
        };
      }
      const effectiveParams = toolName === "tool_call"
        ? wrappedToolParams
        : normalizedParams;
      return effectiveParams && effectiveParams !== event?.params
        ? { params: effectiveParams }
        : undefined;
    }

    if (toolName === "web_fetch") {
      const fetchUrl = canonicalFetchUrl(event);
      if (fetchUrl && state.fetchedUrls.has(fetchUrl)) {
        if (state.fetchPivots.has(fetchUrl)) {
          state.webExhausted = true;
          return { block: true, blockReason: WEB_BUDGET_EXHAUSTED_REASON };
        }
        state.fetchPivots.add(fetchUrl);
        return { block: true, blockReason: WEB_FETCH_REPEAT_PIVOT_REASON };
      }
    }

    const kind = toolName === "web_search" ? "search" : "fetch";
    if (state[kind] >= effective[kind] || state.total >= effective.total) {
      state.webExhausted = true;
      return { block: true, blockReason: WEB_BUDGET_EXHAUSTED_REASON };
    }

    state[kind] += 1;
    state.total += 1;
    if (toolName === "web_fetch") {
      const fetchUrl = canonicalFetchUrl(event);
      if (fetchUrl) state.fetchedUrls.add(fetchUrl);
    }
    return normalizedParams ? { params: normalizedParams } : undefined;
  }

  function observeRun(context, agentId = "pixel", event = undefined) {
    if (context?.agentId !== agentId) return;
    const runId = context?.runId;
    const sessionId = context?.sessionId;
    if (
      typeof runId === "string" &&
      runId &&
      typeof sessionId === "string" &&
      sessionId &&
      userMessageRequestsPrivateUrl(event?.messages, event?.prompt)
    ) {
      stateFor(runId).privateNetworkPrompt = true;
    }
    if (typeof runId === "string" && runId) {
      const state = stateFor(runId);
      if (typeof sessionId === "string" && sessionId) {
        state.currentSessionId = sessionId;
      }
      if (typeof context?.sessionKey === "string" && context.sessionKey) {
        state.currentSessionKey = context.sessionKey;
      }
      if (currentUserText(event?.messages, event?.prompt)) {
        state.ownerIntentObserved = true;
        const visualContinuationRequested =
          userMessageRequestsWorkspaceVisualContinuation(
            event?.messages,
            event?.prompt
          );
        const trustedSessionPreview =
          visualContinuationRequested && typeof sessionId === "string" && sessionId
            ? sessionPreviews.get(sessionId)
            : undefined;
        state.workspaceVisualContinuationRequested = Boolean(trustedSessionPreview);
        state.workspaceVisualContinuationUnavailable =
          visualContinuationRequested && !trustedSessionPreview;
        state.workspacePreviewRequested = Boolean(trustedSessionPreview) ||
          (!visualContinuationRequested &&
            userMessageRequestsWorkspacePreview(
              event?.messages,
              event?.prompt
            ));
        state.workspacePreviewAuthorshipRequired = Boolean(
          state.workspacePreviewRequested &&
          !trustedSessionPreview &&
          userMessageRequiresWorkspacePreviewAuthorship(
            event?.messages,
            event?.prompt
          )
        );
        state.workspacePreviewInspectionRequested =
          userMessageRequestsWorkspacePreviewInspection(
            event?.messages,
            event?.prompt
          );
        state.workspaceTaskRequested =
          state.workspacePreviewRequested ||
          userMessageRequestsWorkspaceTools(event?.messages, event?.prompt);
        state.workspaceMutationRequested =
          state.workspacePreviewRequested ||
          userMessageRequestsWorkspaceMutation(event?.messages, event?.prompt);
        state.workspaceTaskPath = userMessageWorkspaceContinuationPath(
          event?.messages,
          event?.prompt
        );
        state.workspaceTaskDirectory = userMessageWorkspaceDirectoryPath(
          event?.messages,
          event?.prompt
        );
        state.workspaceRequestedFiles = userMessageWorkspaceRequestedFiles(
          event?.messages,
          event?.prompt
        );
        if (trustedSessionPreview) {
          state.workspaceTaskPath =
            `${trustedSessionPreview.relativeDirectory}/index.html`;
          state.workspaceTaskDirectory = trustedSessionPreview.relativeDirectory;
          state.workspaceRequestedFiles = ["index.html"];
          state.workspacePreviewDirectory = trustedSessionPreview.relativeDirectory;
        }
        state.workspacePythonUnittestRequested = /\bunittest\b/i.test(
          currentOwnerIntentText(event?.messages, event?.prompt) ?? ""
        );
        state.workspaceParsedJsonVerificationRequested =
          /\bparsed\s+JSON\b|\bjson\.loads\b/i.test(
            currentOwnerIntentText(event?.messages, event?.prompt) ?? ""
          );
        state.workspaceVerificationRequested = state.workspaceTaskRequested &&
          /\b(?:(?:run|execute)\s+(?:the\s+)?(?:unit\s*)?tests?|verification|verify|test\s+suite)\b/i.test(
            currentOwnerIntentText(event?.messages, event?.prompt) ?? ""
          );
        state.workspaceToolSearchRouted = false;
        state.workspaceToolSearchQueries.clear();
        state.recursiveDeleteAuthorized = userMessageAuthorizesRecursiveDelete(
          event?.messages,
          event?.prompt
        );
        state.exactDownloadRequest = userMessageExactDownloadRequest(
          event?.messages,
          event?.prompt
        );
        state.exactDownloadRequested = Boolean(state.exactDownloadRequest?.exact);
        const operations = userMessageOperationsRequirements(
          event?.messages,
          event?.prompt
        );
        const operationsContinuation = userMessageOperationsContinuation(
          event?.messages,
          event?.prompt
        );
        state.operationsContinuation = operationsContinuation;
        state.operationsRequired =
          !state.exactDownloadRequested &&
          (operations.required || Boolean(operationsContinuation));
        state.operationsRequiredActions = new Set(
          state.operationsRequired && !operationsContinuation ? operations.actions : []
        );
        state.operationsHostCommandRequested =
          state.operationsRequired &&
          !operationsContinuation &&
          userMessageRequestsHostCommand(event?.messages, event?.prompt);
        state.operationsExactHostCommand = state.operationsHostCommandRequested
          ? userMessageExactHostCommand(event?.messages, event?.prompt)
          : undefined;
        state.operationsNetworkPeer = operations.networkPeer;
        state.operationsNetworkDiscoveryRequested = operations.networkDiscoveryRequested === true;
        state.operationsInventoryOnly =
          state.operationsRequired &&
          !operationsContinuation &&
          userMessageRequestsOperationsCapabilityInventory(
            event?.messages,
            event?.prompt
          );
        state.operationsExpectedQuery = state.operationsRequired && !operationsContinuation
          ? userMessageExtensionCatalogExactQuery(event?.messages, event?.prompt)
          : undefined;
        state.operationsExpectedExtensionLifecycle = state.operationsRequired && !operationsContinuation
          ? userMessageExtensionLifecycleIntent(event?.messages, event?.prompt)
          : undefined;
        state.operationsRequiresOdsAppsProjection =
          state.operationsRequired &&
          !operationsContinuation &&
          userMessageRequiresOdsAppsProjection(event?.messages, event?.prompt);
        state.operationsRequiresOdsStatusProjection =
          state.operationsRequired &&
          !operationsContinuation &&
          userMessageRequiresOdsStatusProjection(event?.messages, event?.prompt);
        state.operationsWorkspaceContinuationRequested =
          state.operationsRequired &&
          !operationsContinuation &&
          userMessageRequestsWorkspaceContinuation(event?.messages, event?.prompt);
        state.operationsWorkspaceEvidenceArtifactRequested =
          state.operationsWorkspaceContinuationRequested &&
          userMessageRequestsOperationsEvidenceArtifact(event?.messages, event?.prompt);
        state.operationsWorkspaceExpectedPath =
          state.operationsWorkspaceContinuationRequested
            ? userMessageWorkspaceContinuationPath(event?.messages, event?.prompt)
            : undefined;
      }
      if (!state.operationsRequired && !state.odsRoutingInitialized) {
        const requirements = userMessageOdsToolRequirements(event?.messages, event?.prompt);
        if (requirements.length > 0) {
          state.odsRequiredTools = new Set(requirements);
          state.odsRoutingInitialized = true;
        }
      }
      const githubUrl = userMessageGitHubRepositoryUrl(event?.messages, event?.prompt);
      if (githubUrl) {
        if (!state.githubCanonicalUrl) {
          state.githubCanonicalUrl = githubUrl;
          state.githubReadmeUrl = githubReadmeUrl(githubUrl);
          state.githubFileUrl = userMessageGitHubFileUrl(event?.messages, event?.prompt);
        }
      }
    }
    const prefix = `agent:${agentId}:openai-user:`;
    const sessionKey = context?.sessionKey;
    if (
      typeof sessionKey !== "string" ||
      !sessionKey.startsWith(prefix) ||
      typeof runId !== "string" ||
      !runId ||
      typeof sessionId !== "string" ||
      !sessionId
    ) {
      return;
    }
    const user = sessionKey.slice(prefix.length);
    if (!ODS_OPENAI_USER.test(user)) return;
    // A client can only send this cancellation request while its matching
    // dashboard response is still open. Keep the most recently observed
    // session per opaque user and bound stale completed entries instead of
    // requesting OpenClaw's broad raw-conversation permission for agent_end.
    if (activeUsers.has(user)) activeUsers.delete(user);
    pruneActiveUsers();
    activeUsers.set(user, { runId, sessionId, sessionKey });
  }

  function observeModelCall(event, context, agentId = "pixel") {
    if (context?.agentId !== agentId) return;
    const runId = context?.runId ?? event?.runId;
    if (typeof runId !== "string" || !runId) return;
    stateFor(runId).operationsPromptRound += 1;
  }

  async function abortUserRun(user) {
    if (typeof user !== "string" || !ODS_OPENAI_USER.test(user)) return false;
    const active = activeUsers.get(user);
    if (!active) return false;
    let aborted = false;
    let executionSignalled = execControl ? false : true;
    stateFor(active.runId).clientCancelled = true;
    if (execControl) {
      try {
        executionSignalled = Boolean(execControl.signal(active.runId));
      } catch (error) {
        warn(`Pixel client-cancel execution signal failed: ${String(error)}`);
      }
    }
    try {
      if (typeof abortRunAndDrain === "function") {
        const result = await abortRunAndDrain(active.sessionId, active.sessionKey);
        aborted = Boolean(result?.aborted ?? result);
      } else {
        aborted = typeof abortRun === "function" && Boolean(abortRun(active.sessionId));
      }
    } catch (error) {
      warn(`Pixel client-cancel abort failed: ${String(error)}`);
    }
    const cancelled = aborted && executionSignalled;
    if (executionSignalled && typeof execControl?.clear === "function") {
      const cleanup = setTimeout(() => {
        try {
          execControl.clear(active.runId);
        } catch (error) {
          warn(`Pixel client-cancel marker cleanup failed: ${String(error)}`);
        }
      }, Math.max(0, execMarkerCleanupDelayMs));
      cleanup.unref?.();
    }
    if (cancelled) activeUsers.delete(user);
    return cancelled;
  }

  function afterToolCall(event, context, agentId = "pixel") {
    if (context?.agentId !== agentId) return;
    const toolName = context?.toolName ?? event?.toolName;
    const runId = context?.runId ?? event?.runId;
    if (typeof runId !== "string" || !runId) return;
    const state = stateFor(runId);
    const toolCallId = context?.toolCallId ?? event?.toolCallId;
    const pendingToolRun = pendingToolRuns.get(toolCallId);
    if (
      toolName === "tool_call" &&
      ["read", "write", "edit", "apply_patch", "exec", "process"].includes(
        pendingToolRun?.selectedToolName
      )
    ) {
      const envelope = toolSearchEventEnvelope(
        event,
        pendingToolRun.selectedToolName,
        "core"
      );
      if (envelope) {
        // `tool_result_persist` runs with the same opaque call ID but may see
        // only the already-truncated model-visible content. Preserve this
        // bounded, structurally validated post-tool snapshot on that exact
        // pending call so persistence cannot confuse results across runs.
        pendingToolRun.capturedToolSearchEnvelope = {
          tool: envelope.tool,
          result: envelope.result,
        };
      }
    }
    const directMutation =
      WORKSPACE_MUTATION_TOOLS.has(toolName) &&
      event?.result &&
      typeof event.result === "object" &&
      !Array.isArray(event.result)
        ? { name: toolName, event }
        : undefined;
    const wrappedMutation = toolName === "tool_call"
      ? [...WORKSPACE_MUTATION_TOOLS].flatMap((name) => {
          const selected = toolSearchSelectedToolEvent(event, name, "core");
          return selected ? [{ name, event: selected }] : [];
        })[0]
      : undefined;
    const completedMutation = directMutation ?? wrappedMutation;
    const successfulMutation =
      completedMutation && !toolCallFailed(completedMutation.event)
        ? completedMutation
        : undefined;
    if (successfulMutation) {
      state.invalidEditCreateBlocks = 0;
      state.oversizedEditBlocks = 0;
      state.noOpEditBlocks = 0;
      state.invalidUnittestBlocks = 0;
      state.invalidParsedJsonBlocks = 0;
      state.failedExec.clear();
      state.successfulExec.clear();
      state.successfulExecBlocks.clear();
    }
    const completedWritePath = successfulMutation?.name === "write"
      ? normalizeWorkspaceFilePath(successfulMutation.event?.params?.path)
      : undefined;
    if (completedWritePath) {
      state.successfulWritePaths.add(completedWritePath);
      state.repeatedWriteBlocks.delete(completedWritePath);
      const writtenContent = successfulMutation.event?.params?.content;
      if (
        typeof writtenContent === "string" &&
        Buffer.byteLength(writtenContent, "utf8") <= MAX_TRACKED_WORKSPACE_FILE_BYTES
      ) {
        state.successfulWriteContentByPath.set(completedWritePath, writtenContent);
      } else {
        state.successfulWriteContentByPath.delete(completedWritePath);
      }
    }
    const completedEditPath = successfulMutation?.name === "edit"
      ? normalizeWorkspaceFilePath(successfulMutation.event?.params?.path)
      : undefined;
    const completedEditPairs = successfulMutation?.name === "edit"
      ? editReplacementPairs(successfulMutation.event?.params)
      : [];
    if (
      completedEditPath &&
      state.workspaceVisualContinuationRequested &&
      completedEditPath.startsWith(`${state.workspaceTaskDirectory}/`)
    ) {
      state.workspaceVisualContinuationEdited = true;
    }
    if (completedEditPath && state.successfulWritePaths.has(completedEditPath)) {
      const editedContent = replayTrackedEdit(
        state.successfulWriteContentByPath.get(completedEditPath),
        completedEditPairs
      );
      if (editedContent === undefined) {
        state.successfulWriteContentByPath.delete(completedEditPath);
      } else {
        state.successfulWriteContentByPath.set(completedEditPath, editedContent);
      }
    }
    const completedRead =
      toolName === "read" && event?.result && typeof event.result === "object"
        ? event
        : toolName === "tool_call"
          ? toolSearchSelectedToolEvent(event, "read", "core")
          : undefined;
    const completedReadPath = completedRead && !toolCallFailed(completedRead)
      ? normalizeWorkspaceFilePath(completedRead.params?.path)
      : undefined;
    if (completedReadPath) {
      state.successfulReadPaths.add(completedReadPath);
    }
    const wrappedPreviewEvent =
      toolName === "tool_call" &&
      event?.params?.id?.split(":").at(-1) === WORKSPACE_PREVIEW_TOOL
        ? toolSearchSelectedToolEvent(event, WORKSPACE_PREVIEW_TOOL, "pixel-ods")
        : undefined;
    const previewEvent = toolName === WORKSPACE_PREVIEW_TOOL
      ? event
      : wrappedPreviewEvent;
    if (previewEvent) {
      state.workspacePreviewAttempted = true;
      const requestedDirectory = normalizeWorkspaceFilePath(
        previewEvent?.params?.relativeDirectory
      );
      if (requestedDirectory) state.workspacePreviewDirectory = requestedDirectory;
      const preview = workspacePreviewOutcome(
        previewEvent,
        state.workspacePreviewDirectory,
        state
      );
      if (preview) {
        state.workspacePreviewDirectory = preview.relativeDirectory;
        state.workspacePreviewModelAuthored = state.workspacePreviewAuthorshipRequired;
        state.workspacePreview = preview;
        state.successfulWriteContentByPath.clear();
        rememberSessionPreview(state.currentSessionId, preview);
      }
    }
    if (state.operationsRequired) {
      if (state.operationsInventoryOnly) {
        const wrappedInventory =
          toolName === "tool_call"
            ? toolSearchSelectedToolEvent(
              event,
              "pixel_ops_inventory",
              "pixel-operations-broker"
            )
            : undefined;
        const inventoryEvent = toolName === "pixel_ops_inventory"
          ? event
          : wrappedInventory;
        if (inventoryEvent) {
          state.operationsInventoryAttempted = true;
          state.operationsInventory = operationsInventoryProjection(inventoryEvent);
        }
      }
      const wrappedHostCommand =
        toolName === "tool_call"
          ? toolSearchSelectedToolEvent(
            event,
            SYNCHRONOUS_HOST_COMMAND_TOOL,
            "pixel-ods"
          )
          : undefined;
      const hostCommand =
        toolName === SYNCHRONOUS_HOST_COMMAND_TOOL
          ? synchronousHostCommandOutcome(event, state)
          : wrappedHostCommand
            ? synchronousHostCommandOutcome(wrappedHostCommand, state)
            : undefined;
      if (hostCommand) {
        state.operationsSubmittedJobs.set(
          hostCommand.submission.jobId,
          hostCommand.submission
        );
        if (hostCommand.outcome) {
          state.operationsTerminalJobs.set(
            hostCommand.outcome.jobId,
            hostCommand.outcome
          );
        }
        state.operationsHostResultCompactionsRemaining = 2;
      }
      const wrappedHostObservation =
        toolName === "tool_call"
          ? toolSearchSelectedToolEvent(
            event,
            SYNCHRONOUS_HOST_OBSERVE_TOOL,
            "pixel-ods"
          )
          : undefined;
      const hostObservation =
        toolName === SYNCHRONOUS_HOST_OBSERVE_TOOL
          ? synchronousHostObservationOutcome(event, state)
          : wrappedHostObservation
            ? synchronousHostObservationOutcome(wrappedHostObservation, state)
            : undefined;
      if (hostObservation) {
        state.operationsSubmittedJobs.set(
          hostObservation.submission.jobId,
          hostObservation.submission
        );
        state.operationsTerminalJobs.set(
          hostObservation.outcome.jobId,
          hostObservation.outcome
        );
        // A Tool Search call can persist both the selected plugin result and
        // its outer wrapper. Compact at most those two messages, and clear the
        // allowance as soon as any different tool starts.
        state.operationsHostResultCompactionsRemaining = 2;
        const combinedStatus = synchronousHostOdsStatusProjection(
          wrappedHostObservation ?? event
        );
        if (state.operationsRequiresOdsStatusProjection && combinedStatus) {
          state.operationsOdsStatusProjectionAttempted = true;
          state.operationsOdsStatusProjectionToolSearchPending = false;
          state.operationsOdsStatusProjection = combinedStatus;
        }
      }
      const submission = operationsSubmission(event, toolName);
      if (submission) {
        state.operationsSubmittedJobs.set(submission.jobId, submission);
      }
      if (toolName === "pixel_ops_job_get" || toolName === "pixel_ops_job_wait") {
        const continuationOutcome = operationsContinuationTerminalOutcome(
          event,
          state.operationsContinuation
        );
        if (continuationOutcome) {
          state.operationsContinuationOutcome = continuationOutcome;
          state.operationsTerminalBlocks = 0;
        }
        const outcome = operationsTerminalOutcome(
          event,
          state.operationsSubmittedJobs
        );
        if (outcome) state.operationsTerminalJobs.set(outcome.jobId, outcome);
      }
      const wrappedStatusEvent =
        toolName === "tool_call"
          ? toolSearchSelectedToolEvent(event, "pixel_ods_status", "pixel-ods")
          : undefined;
      const wrappedAppsEvent =
        toolName === "tool_call"
          ? toolSearchSelectedToolEvent(event, "pixel_ods_apps_list", "pixel-ods")
          : undefined;
      const wrappedAppsAttempt =
        toolName === "tool_call" &&
        event?.params?.id === "pixel_ods_apps_list" &&
        state.operationsOdsAppsProjectionToolSearchPending;
      const wrappedStatusAttempt =
        toolName === "tool_call" &&
        event?.params?.id === "pixel_ods_status" &&
        state.operationsOdsStatusProjectionToolSearchPending;
      if (
        state.operationsRequiresOdsAppsProjection &&
        (toolName === "pixel_ods_apps_list" || wrappedAppsAttempt || wrappedAppsEvent)
      ) {
        state.operationsOdsAppsProjectionAttempted = true;
        state.operationsOdsAppsProjectionToolSearchPending = false;
        state.operationsOdsAppsProjection = operationsOdsAppsProjection(
          wrappedAppsEvent ?? event
        );
      }
      if (
        state.operationsRequiresOdsStatusProjection &&
        (toolName === "pixel_ods_status" || wrappedStatusAttempt || wrappedStatusEvent)
      ) {
        state.operationsOdsStatusProjectionAttempted = true;
        state.operationsOdsStatusProjectionToolSearchPending = false;
        state.operationsOdsStatusProjection = operationsOdsStatusProjection(
          wrappedStatusEvent ?? event
        );
      }
      let workspaceToolName;
      let workspaceToolEvent;
      if (toolName === "write" || toolName === "read") {
        workspaceToolName = toolName;
        workspaceToolEvent = event;
      } else if (
        toolName === "tool_call" &&
        (event?.params?.id === "write" || event?.params?.id === "read")
      ) {
        workspaceToolName = event.params.id;
        workspaceToolEvent = toolSearchSelectedToolEvent(
          event,
          workspaceToolName,
          "core"
        );
      }
      if (
        state.operationsWorkspaceContinuationRequested &&
        state.operationsWorkspaceExpectedPath &&
        workspaceToolEvent &&
        !toolCallFailed(workspaceToolEvent) &&
        Array.isArray(workspaceToolEvent?.result?.content) &&
        workspaceToolEvent.result.content.some(
          (item) => item && item.type === "text" && typeof item.text === "string"
        )
      ) {
        const observedPath = normalizeWorkspaceFilePath(
          workspaceToolEvent?.params?.path
        );
        if (observedPath === state.operationsWorkspaceExpectedPath) {
          if (workspaceToolName === "write") {
            state.operationsWorkspaceWriteVerified = true;
          } else if (workspaceToolName === "read") {
            state.operationsWorkspaceReadVerified = true;
          }
        }
      }
      completeVerifiedEvidenceArtifact(state);
      const hostOnly =
        state.operationsRequiredActions.size > 0 &&
        [...state.operationsRequiredActions].every((action) => action.startsWith("host."));
      const everySubmittedJobIsTerminal =
        state.operationsSubmittedJobs.size > 0 &&
        [...state.operationsSubmittedJobs.keys()].every((jobId) =>
          state.operationsTerminalJobs.has(jobId)
        );
      const everyRequiredProjectionIsPresent =
        (!state.operationsRequiresOdsAppsProjection || state.operationsOdsAppsProjection) &&
        (!state.operationsRequiresOdsStatusProjection || state.operationsOdsStatusProjection);
      const workspaceContinuationIsComplete =
        !state.operationsWorkspaceContinuationRequested ||
        (state.operationsWorkspaceWriteVerified && state.operationsWorkspaceReadVerified);
      if (
        hostOnly &&
        everyRequiredProjectionIsPresent &&
        workspaceContinuationIsComplete &&
        everySubmittedJobIsTerminal &&
        !state.operationsTerminalAborted &&
        typeof context?.sessionId === "string" &&
        context.sessionId
      ) {
        const verification = verificationForRun(runId);
        if (verification.text && ["passed", "failed"].includes(verification.status)) {
          state.operationsTerminalAborted = true;
          try {
            abortRun(context.sessionId);
          } catch (error) {
            warn(`Pixel terminal Operations fast-path abort failed: ${String(error)}`);
          }
        }
      }
    }
    const wrappedExactDownloadToolName =
      toolName === "tool_call" && typeof event?.params?.id === "string"
        ? event.params.id.split(":").at(-1)
        : undefined;
    const wrappedExactDownloadEvent =
      wrappedExactDownloadToolName && EXACT_DOWNLOAD_BROKER_TOOLS.has(wrappedExactDownloadToolName)
        ? toolSearchSelectedToolEvent(
          event,
          wrappedExactDownloadToolName,
          wrappedExactDownloadToolName === "pixel_ods_download_promote"
            ? "pixel-ods"
            : "pixel-operations-broker"
        )
        : undefined;
    const exactDownloadToolName = wrappedExactDownloadEvent
      ? wrappedExactDownloadToolName
      : toolName;
    const exactDownloadEvent = wrappedExactDownloadEvent ?? event;
    if (exactDownloadToolName === "pixel_ops_download_stage") {
      const submission = exactDownloadSubmission(exactDownloadEvent, state.exactDownloadRequest);
      if (submission) {
        state.exactDownloadSubmissions.set(submission.jobId, submission);
        state.exactDownloadTerminalBlocks = 0;
      }
    }
    if (exactDownloadToolName === "pixel_ops_job_get" || exactDownloadToolName === "pixel_ops_job_wait") {
      const artifact = exactDownloadTerminalArtifact(
        exactDownloadEvent,
        state.exactDownloadSubmissions
      );
      if (artifact) {
        state.exactDownloadBrokerObserved = true;
        state.exactDownloadArtifact = artifact;
        state.exactDownloadTerminalOutcome = undefined;
        state.exactDownloadTerminalBlocks = 0;
      } else {
        const outcome = exactDownloadTerminalOutcome(
          exactDownloadEvent,
          state.exactDownloadSubmissions
        );
        if (outcome) {
          state.exactDownloadBrokerObserved = true;
          state.exactDownloadTerminalOutcome = outcome;
        }
      }
    }
    if (exactDownloadToolName === "pixel_ods_download_promote") {
      state.exactDownloadPromotionAttempted = true;
      const promotion = exactDownloadPromotion(exactDownloadEvent, state.exactDownloadArtifact);
      if (promotion) {
        state.exactDownloadPromotion = promotion;
        state.exactDownloadTerminalBlocks = 0;
      }
    }
    if (
      state.githubReadmeUrl &&
      toolName === "web_fetch" &&
      canonicalFetchUrl(event) === state.githubReadmeUrl
    ) {
      state.githubCanonicalSatisfied = canonicalWebFetchSucceeded(event);
      state.githubCanonicalFailed = !state.githubCanonicalSatisfied;
    }
    if (toolName === "process") {
      const completion = completedProcessResult(event);
      if (!completion) return;
      const pending = state.pendingExecSessions.get(completion.sessionId);
      if (!pending) return;
      state.pendingExecSessions.delete(completion.sessionId);
      state.pendingExecBlocks.delete(completion.sessionId);
      const verificationFailed =
        completion.failed ||
        (
          pending.verificationFingerprint &&
          verificationFingerprintIsPythonUnittest(pending.verificationFingerprint) &&
          execResultHasNonCleanUnittestOutcome(event)
        );
      if (verificationFailed) {
        if (pending.fingerprint) {
          state.failedExec.set(
            pending.fingerprint,
            (state.failedExec.get(pending.fingerprint) ?? 0) + 1
          );
          state.successfulExec.delete(pending.fingerprint);
          state.successfulExecBlocks.delete(pending.fingerprint);
        }
        if (pending.verificationFingerprint) {
          state.failedVerificationAttempts += 1;
          state.latestVerificationStatus = "failed";
        }
      } else {
        if (pending.fingerprint) {
          state.failedExec.delete(pending.fingerprint);
          state.successfulExec.set(
            pending.fingerprint,
            (state.successfulExec.get(pending.fingerprint) ?? 0) + 1
          );
        }
        if (pending.verificationFingerprint) {
          state.failedVerificationAttempts = 0;
          state.latestVerificationStatus = "passed";
        }
      }
      return;
    }
    // A successful file mutation permits another identical command, but does
    // not erase the run-wide failed-verification count. This distinguishes a
    // useful repair cycle from unbounded edit/test churn.
    if (toolName === "web_fetch" && webFetchWasTruncated(event)) {
      const fetchUrl = canonicalFetchUrl(event);
      if (fetchUrl) {
        state.targetedExtractPending = fetchUrl;
        state.targetedExtractBlocks = 0;
      }
      return;
    }
    const wrappedExecEvent = toolName === "tool_call"
      ? toolSearchSelectedToolEvent(event, "exec", "core") ??
        (event?.params?.id === "exec"
          ? {
            params:
              event.params.args &&
              typeof event.params.args === "object" &&
              !Array.isArray(event.params.args)
                ? event.params.args
                : {},
            result: event?.result,
            error: event?.error,
          }
          : undefined)
      : undefined;
    const execEvent = toolName === "exec" ? event : wrappedExecEvent;
    if (!execEvent) return;
    const observedFingerprint = execFingerprint(execEvent?.params);
    const fingerprint = state.execOriginalByWrapped.get(observedFingerprint) ?? observedFingerprint;
    const verificationFingerprint =
      state.verificationOriginalByWrapped.get(observedFingerprint) ??
      verificationExecFingerprint(execEvent?.params);
    if (observedFingerprint) state.execOriginalByWrapped.delete(observedFingerprint);
    if (observedFingerprint) {
      state.verificationOriginalByWrapped.delete(observedFingerprint);
    }
    if (!fingerprint && !verificationFingerprint) return;
    if (verificationFingerprint) {
      state.latestVerificationFingerprint = verificationFingerprint;
    }
    const pendingSessionId = runningExecSessionId(execEvent);
    if (pendingSessionId) {
      if (state.pendingExecSessions.size >= MAX_PENDING_EXEC_SESSIONS) {
        state.codingExhausted = true;
        return;
      }
      state.pendingExecSessions.set(pendingSessionId, {
        fingerprint,
        verificationFingerprint,
      });
      if (verificationFingerprint) state.latestVerificationStatus = "pending";
      return;
    }
    const verificationFailed =
      execFailed(execEvent) ||
      (
        verificationFingerprint &&
        verificationFingerprintIsPythonUnittest(verificationFingerprint) &&
        execResultHasNonCleanUnittestOutcome(execEvent)
      );
    // OpenClaw conservatively classifies its deferred `tool_call` wrapper as a
    // mutation. A failed wrapped exec therefore remains its last tool error
    // even after a later wrapped exec succeeds, unlike a native exec. Preserve
    // the failed receipt in the session, but tell the private ingress when that
    // exact generated warning is stale. A new wrapped exec failure always
    // revokes the signal.
    if (toolName === "tool_call") {
      if (verificationFailed) {
        state.wrappedExecFailurePending = true;
        state.suppressStaleExecWarning = false;
      } else if (state.wrappedExecFailurePending) {
        state.wrappedExecFailurePending = false;
        state.suppressStaleExecWarning = true;
      }
    }
    if (verificationFailed) {
      if (fingerprint) {
        state.failedExec.set(fingerprint, (state.failedExec.get(fingerprint) ?? 0) + 1);
        state.successfulExec.delete(fingerprint);
        state.successfulExecBlocks.delete(fingerprint);
      }
      if (verificationFingerprint) {
        state.failedVerificationAttempts += 1;
        state.latestVerificationStatus = "failed";
      }
    } else {
      if (fingerprint) {
        state.failedExec.delete(fingerprint);
        state.successfulExec.set(
          fingerprint,
          (state.successfulExec.get(fingerprint) ?? 0) + 1
        );
      }
      if (verificationFingerprint) {
        state.failedVerificationAttempts = 0;
        state.latestVerificationStatus = "passed";
      }
    }
  }

  function trustedOperationsContinuation(state, runId) {
    if (!state?.operationsRequired) return undefined;
    if (state.operationsInventoryOnly) {
      if (state.operationsInventory || state.operationsInventoryAttempted) return undefined;
      return {
        stage: "operations-inventory",
        instruction:
          "Do not reply yet. Call tool_call now with id pixel_ops_inventory and args {}. " +
          "This one read-only projection must finish before you report available capabilities.",
      };
    }
    if (
      state.operationsHostCommandRequested &&
      state.operationsSubmittedJobs.size === 0
    ) {
      const proposalArgs = state.operationsExactHostCommand
        ? `args ${JSON.stringify({
            command: state.operationsExactHostCommand,
          })}`
        : "args containing one command that narrowly satisfies the owner's request";
      return {
        stage: "host-command-proposal",
        instruction:
          `Do not reply yet. Call tool_call now with id ${SYNCHRONOUS_HOST_COMMAND_TOOL} and ${proposalArgs}. ` +
          "This submits one immutable approval proposal and waits internally for the broker receipt; it cannot execute while approval is pending or approve the command.",
      };
    }
    if (state.operationsHostCommandRequested) {
      const pendingJobIds = [...state.operationsSubmittedJobs.keys()].filter(
        (jobId) => !state.operationsTerminalJobs.has(jobId)
      );
      if (pendingJobIds.length === 1) {
        const jobId = pendingJobIds[0];
        return {
          stage: `host-command-wait-${jobId}`,
          instruction:
            `Do not reply yet. Call tool_call now with id pixel_ops_job_wait and args ${JSON.stringify({ jobId })}. ` +
            "Accept only a structurally matching terminal broker receipt; never approve, resubmit, or execute the command yourself.",
        };
      }
    }
    const requiredHostActions = exactRequiredHostActions(state);
    if (state.operationsSubmittedJobs.size === 0) {
      if (!requiredHostActions) return undefined;
      return {
        stage: "host-observe",
        instruction:
          `Do not reply yet. Call tool_call now with id ${SYNCHRONOUS_HOST_OBSERVE_TOOL} ` +
          `and args ${JSON.stringify({
            actions: requiredHostActions,
            ...(requiredHostActions.includes("host.network-peer") && state.operationsNetworkPeer
              ? {
                peer: state.operationsNetworkPeer.peer,
                ports: state.operationsNetworkPeer.ports,
              }
              : {}),
            ...(state.operationsRequiresOdsStatusProjection
              ? { includeOdsStatus: true }
              : {}),
          })}.`,
      };
    }
    const everySubmittedJobIsTerminal =
      state.operationsSubmittedJobs.size > 0 &&
      [...state.operationsSubmittedJobs.keys()].every((jobId) =>
        state.operationsTerminalJobs.has(jobId)
      );
    if (!everySubmittedJobIsTerminal) return undefined;
    if (
      state.operationsRequiresOdsStatusProjection &&
      !state.operationsOdsStatusProjection &&
      !state.operationsOdsStatusProjectionAttempted
    ) {
      return {
        stage: "ods-status",
        instruction:
          "Do not reply yet. Call tool_call now with id pixel_ods_status and args {}. " +
          "This required read-only projection must finish before any workspace step.",
      };
    }
    if (
      state.operationsRequiresOdsAppsProjection &&
      !state.operationsOdsAppsProjection &&
      !state.operationsOdsAppsProjectionAttempted
    ) {
      return {
        stage: "ods-apps",
        instruction:
          "Do not reply yet. Call tool_call now with id pixel_ods_apps_list and args {}. " +
          "This required read-only projection must finish before any workspace step.",
      };
    }
    if (
      !state.operationsWorkspaceContinuationRequested ||
      verificationForRun(runId).status !== "passed"
    ) {
      return undefined;
    }
    const relativePath = state.operationsWorkspaceExpectedPath;
    if (!relativePath) return undefined;
    if (!state.operationsWorkspaceWriteVerified) {
      if (state.operationsWorkspaceEvidenceArtifactRequested) {
        return {
          stage: "workspace-write",
          instruction:
            `Do not reply yet. Call tool_call now with id ${EVIDENCE_REPORT_TOOL} ` +
            "and args {}. This narrow adapter writes the exact receipt-bound report " +
            `to the owner-requested path \"${relativePath}\" without asking you to reproduce it.`,
        };
      }
      return {
        stage: "workspace-write",
        instruction:
          `Do not reply yet. Call tool_call now with id write and args containing path ` +
          `\"${relativePath}\" plus content built only from the verified evidence requested by the owner.`,
      };
    }
    if (!state.operationsWorkspaceReadVerified) {
      if (state.operationsWorkspaceEvidenceArtifactRequested) {
        return {
          stage: "workspace-read",
          instruction:
            `Do not reply yet. Call tool_call now with id ${EVIDENCE_READBACK_TOOL} ` +
            "and args {}. This narrow adapter reads back only the exact owner-requested report. " +
            "Reply only after the real readback succeeds.",
        };
      }
      return {
        stage: "workspace-read",
        instruction:
          `Do not reply yet. Call tool_call now with id read and args {\"path\":\"${relativePath}\"}. ` +
          "Reply only after the real readback succeeds.",
      };
    }
    return undefined;
  }

  function trustedWorkspacePreviewContinuation(state) {
    if (
      !state?.workspacePreviewRequested ||
      state.operationsRequired ||
      state.exactDownloadRequested
    ) {
      return undefined;
    }
    if (state.workspacePreview) {
      if (workspacePreviewReadbackComplete(state)) return undefined;
      const nextPath = workspacePreviewNextKnownReadPath(state);
      const completed = workspacePreviewReadPaths(state).length;
      return {
        stage: `workspace-preview-read-${completed}`,
        instruction: nextPath
          ? `Do not reply yet. The preview is already independently verified; call tool_call now with id read and args ${JSON.stringify({ path: nextPath })}. Do not curl the preview URL or call another tool.`
          : `Do not reply yet. The preview is already independently verified. Read the next unread static file inside ${state.workspacePreview.relativeDirectory} using only read; do not curl the preview URL or call another tool.`,
      };
    }
    const directory = workspacePreviewDirectoryFromState(state);
    if (!directory) {
      return {
        stage: "workspace-preview-files",
        instruction:
          "Do not reply yet. Create or inspect the requested static website in one workspace-relative directory with index.html and any local CSS or JavaScript assets. Do not start a server. After index.html has been written or read in this response, call pixel_ods_workspace_preview with that relative directory.",
      };
    }
    state.workspacePreviewDirectory = directory;
    return {
      stage: "workspace-preview",
      instruction:
        `Do not reply yet. Call tool_call now with id ${WORKSPACE_PREVIEW_TOOL} ` +
        `and args ${JSON.stringify({ relativeDirectory: directory })}. ` +
        "Do not start a sandbox server or claim another localhost URL.",
    };
  }

  function toolResultPersist(event, context, agentId = "pixel") {
    if (context?.agentId !== agentId) return undefined;
    const toolCallId = context?.toolCallId ?? event?.toolCallId;
    const pending = pendingToolRuns.get(toolCallId);
    pendingToolRuns.delete(toolCallId);
    const runId = pending?.runId ?? context?.runId ?? event?.runId;
    const state = runs.get(runId);
    const continuation = trustedOperationsContinuation(state, runId);
    if (!event?.message || typeof event.message !== "object") {
      return undefined;
    }
    const message = event?.message;
    const compactVerification = compactCleanVerificationResult(message, pending);
    const compactCoreResult = compactVerification
      ? undefined
      : compactWorkspaceCoreResult(message, pending, state);
    const workspaceStageInstruction = (() => {
      if (!compactCoreResult || !state?.workspaceTaskDirectory) return undefined;
      const nextFile = state.workspaceMutationRequested
        ? state.workspaceRequestedFiles.find((file) =>
          !state.successfulWritePaths.has(`${state.workspaceTaskDirectory}/${file}`)
        )
        : undefined;
      if (nextFile) {
        const nextPath = `${state.workspaceTaskDirectory}/${nextFile}`;
        const pythonTestFile =
          /^(?:test(?:_[A-Za-z0-9._-]+)?|[A-Za-z0-9._-]+_test)\.py$/i.test(nextFile);
        const testFileHint = !pythonTestFile
          ? ""
          : state.workspacePythonUnittestRequested
            ? " The owner explicitly requires unittest: include import unittest, at least one " +
              "class inheriting unittest.TestCase, and only the requested test_* methods; omit " +
              "comments, docstrings, helper cases, and a custom print runner."
            : " For a Python test file, include every required test-framework and implementation import.";
        return (
          "[ODS Pixel next step] Call tool_call next with id openclaw:core:write and " +
          `args path ${JSON.stringify(nextPath)} plus the complete requested content. ` +
          "Keep it concise (under 1000 characters when the requirements fit); do not " +
          `inspect or narrate first.${testFileHint}`
        );
      }
      if (state.latestVerificationStatus === "failed") {
        return `[ODS Pixel next step] ${FAILED_TEST_READ_REPAIR_REASON}`;
      }
      if (state.latestVerificationStatus === "passed") {
        return (
          "[ODS Pixel next step] Verification passed. Give the owner the concise final " +
          "result now; do not call another tool."
        );
      }
      if (state.workspaceMutationRequested && state.workspaceRequestedFiles.length > 0) {
        return (
          "[ODS Pixel next step] All explicitly requested files are written. Run the " +
          "owner-requested verification command now; the project workdir is applied automatically."
        );
      }
      return undefined;
    })();
    const previewStageInstruction = (() => {
      if (
        !state?.workspacePreview ||
        state.operationsRequired ||
        state.exactDownloadRequested
      ) {
        return undefined;
      }
      if (workspacePreviewReadbackComplete(state)) {
        return `[ODS Pixel next step] ${WORKSPACE_PREVIEW_COMPLETE_REASON}`;
      }
      const nextPath = workspacePreviewNextKnownReadPath(state);
      return nextPath
        ? (
          "[ODS Pixel next step] The preview is already independently verified. " +
          `Call tool_call next with id read and args ${JSON.stringify({ path: nextPath })}; ` +
          "do not curl the preview URL or call another tool."
        )
        : `[ODS Pixel next step] ${WORKSPACE_PREVIEW_REQUIRES_READBACK_REASON}`;
    })();
    const hostToolResult =
      pending?.selectedToolName === SYNCHRONOUS_HOST_OBSERVE_TOOL ||
      pending?.selectedToolName === SYNCHRONOUS_HOST_COMMAND_TOOL ||
      state?.operationsHostResultCompactionsRemaining > 0 ||
      persistedToolSearchResult(
        message,
        SYNCHRONOUS_HOST_OBSERVE_TOOL,
        "pixel-ods"
      ) ||
      persistedToolSearchResult(
        message,
        SYNCHRONOUS_HOST_COMMAND_TOOL,
        "pixel-ods"
      );
    const hostEvidence =
      hostToolResult
        ? operationsEvidenceText(
          state?.operationsRequiredActions,
          state?.operationsTerminalJobs,
          state?.operationsOdsAppsProjection,
          state?.operationsOdsStatusProjection
        )
        : undefined;
    // The external broker keeps the complete terminal receipt. Once the guard
    // has structurally validated that receipt in afterToolCall, persist only a
    // compact, receipt-bound projection into the model conversation. Tool
    // Search otherwise duplicates the multi-kilobyte broker object in content
    // and details, which can exhaust small local models before the required
    // continuation tool call closes.
    if (
      !continuation &&
      !hostEvidence &&
      !compactVerification &&
      !compactCoreResult &&
      !previewStageInstruction
    ) {
      return undefined;
    }
    const compactMessage = compactVerification ?? compactCoreResult ?? message;
    const content = hostEvidence
      ? [{
        type: "text",
        text:
          `${hostEvidence}\n- Receipt custody: full terminal evidence remains ` +
          "bound to the cited job ID in the external Operations Broker; this compact projection grants no authority.",
      }]
      : Array.isArray(compactMessage.content) ? [...compactMessage.content] : [];
    if (workspaceStageInstruction) {
      content.push({ type: "text", text: workspaceStageInstruction });
    }
    if (previewStageInstruction) {
      content.push({ type: "text", text: previewStageInstruction });
    }
    if (hostEvidence && state.operationsHostResultCompactionsRemaining > 0) {
      state.operationsHostResultCompactionsRemaining -= 1;
    }
    if (continuation) {
      content.push({
        type: "text",
        text: `${OPERATIONS_TRUSTED_CONTINUATION_PREFIX} ${continuation.instruction}`,
      });
    }
    return {
      message: {
        ...compactMessage,
        content,
      },
    };
  }

  function beforeAgentFinalize(event, context, agentId = "pixel") {
    if (context?.agentId !== agentId) return undefined;
    const runId = context?.runId ?? event?.runId;
    if (typeof runId !== "string" || !runId) return undefined;
    const state = runs.get(runId);
    const continuation =
      trustedOperationsContinuation(state, runId) ??
      trustedWorkspacePreviewContinuation(state);
    if (!continuation) return undefined;
    return {
      action: "revise",
      reason: "Pixel has not completed every owner-requested verified step.",
      retry: {
        instruction: continuation.instruction,
        idempotencyKey: `pixel-ods-${continuation.stage}`,
        maxAttempts: 1,
      },
    };
  }

  function verificationForRun(runId) {
    if (typeof runId !== "string" || !runId) return { status: "none" };
    const state = runs.get(runId);
    if (!state) return { status: "none" };
    if (state.unrequestedOperationsAborted) {
      return { status: "failed", text: UNREQUESTED_OPERATIONS_LOOP_ABORT_REASON };
    }
    if (
      state.workspacePreviewRequested &&
      !state.operationsRequired &&
      !state.exactDownloadRequested
    ) {
      if (!state.workspacePreview) {
        const hasIndexEvidence = [
          ...state.successfulWritePaths,
          ...state.successfulReadPaths,
        ].some(
          (value) => typeof value === "string" && value.endsWith("/index.html")
        );
        return {
          status: "failed",
          text: hasIndexEvidence
            ? WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX
            : WORKSPACE_PREVIEW_NOT_CREATED_DELIVERY_PREFIX,
        };
      }
      return {
        status: "passed",
        text:
          `${WORKSPACE_PREVIEW_PUBLISHED_DELIVERY_PREFIX}\n` +
          `- [Open the verified preview](${state.workspacePreview.url})\n` +
          `- Snapshot: ${state.workspacePreview.files} files, ` +
          `${state.workspacePreview.bytes} bytes, SHA-256 ` +
          `\`${state.workspacePreview.sha256}\`.\n` +
          `- Workspace artifact: \`${state.workspacePreview.relativeDirectory}/index.html\`.\n` +
          (state.workspacePreviewModelAuthored
            ? "- Origin: Pixel's active model wrote every published file in this request; ODS supplied no creative artifact bytes.\n"
            : "- Origin: ODS supplied no creative artifact bytes; Pixel published the workspace files selected in this request.\n") +
          "- Browser readback: HTTP 200 from the dedicated loopback preview origin.\n" +
          "- Interaction evidence: this static receipt does not claim that controls were clicked or exercised.",
        preview: {
          schemaVersion: 1,
          kind: "ods-pixel-workspace-preview",
          ...state.workspacePreview,
        },
      };
    }
    if (state.exactDownloadRequested) {
      const terminalText = exactDownloadTerminalText(state.exactDownloadTerminalOutcome);
      if (terminalText) return { status: "failed", text: terminalText };
      if (!state.exactDownloadBrokerObserved) {
        return {
          status: "failed",
          text: state.exactDownloadSubmissions.size > 0
            ? EXACT_DOWNLOAD_UNVERIFIED_DELIVERY_PREFIX
            : EXACT_DOWNLOAD_UNAVAILABLE_DELIVERY_PREFIX,
        };
      }
      if (!state.exactDownloadPromotion) {
        return {
          status: "failed",
          text: state.exactDownloadPromotionAttempted
            ? EXACT_DOWNLOAD_PROMOTION_FAILED_DELIVERY_PREFIX
            : EXACT_DOWNLOAD_UNPUBLISHED_DELIVERY_PREFIX,
        };
      }
      return {
        status: "passed",
        text: exactDownloadPublishedText(state.exactDownloadPromotion),
      };
    }
    if (state.operationsRequired) {
      if (state.operationsInventoryOnly) {
        const inventoryText = operationsInventoryEvidenceText(state.operationsInventory);
        return inventoryText
          ? { status: "passed", text: inventoryText }
          : {
            status: "failed",
            text: OPERATIONS_INVENTORY_UNVERIFIED_DELIVERY_PREFIX,
          };
      }
      if (state.operationsContinuation) {
        const evidenceText = operationsContinuationEvidenceText(
          state.operationsContinuationOutcome
        );
        if (!evidenceText) {
          return {
            status: "failed",
            text: OPERATIONS_CONTINUATION_UNVERIFIED_DELIVERY_PREFIX,
          };
        }
        return {
          status: state.operationsContinuationOutcome.status === "succeeded" ? "passed" : "failed",
          text: evidenceText,
        };
      }
      if (state.operationsSubmittedJobs.size === 0) {
        return {
          status: "failed",
          text: OPERATIONS_UNAVAILABLE_DELIVERY_PREFIX,
          code: OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE,
        };
      }
      if (
        [...state.operationsSubmittedJobs.keys()].some(
          (jobId) => !state.operationsTerminalJobs.has(jobId)
        )
      ) {
        return { status: "failed", text: OPERATIONS_UNVERIFIED_DELIVERY_PREFIX };
      }
      const evidenceText = operationsEvidenceText(
        state.operationsRequiredActions,
        state.operationsTerminalJobs,
        state.operationsOdsAppsProjection,
        state.operationsOdsStatusProjection
      );
      if (state.operationsRequiredActions.size > 0) {
        const hostOnly = [...state.operationsRequiredActions].every(
          (action) => action.startsWith("host.")
        );
        const terminalActions = hostOnly
          ? new Set(
            [...state.operationsTerminalJobs.values()].flatMap((outcome) =>
              outcome.actions.map(({ action }) => action)
            )
          )
          : new Set();
        const missingActions = hostOnly
          ? [...state.operationsRequiredActions].filter((action) =>
            !terminalActions.has(action) &&
            !(action === "host.architecture" && terminalActions.has("host.cpu"))
          )
          : [];
        if (hostOnly && missingActions.length > 0) {
          return {
            status: "failed",
            text: `${OPERATIONS_MISSING_REQUIRED_DELIVERY_PREFIX} Missing: ${missingActions
              .map((action) => `\`${action}\``)
              .join(", ")}.`,
          };
        }
        if (hostOnly && state.operationsRequiresOdsAppsProjection && !state.operationsOdsAppsProjection) {
          const partialText = operationsEvidenceText(
            state.operationsRequiredActions,
            state.operationsTerminalJobs,
            undefined,
            state.operationsOdsStatusProjection
          );
          return {
            status: "failed",
            text: `${partialText ?? OPERATIONS_UNVERIFIED_DELIVERY_PREFIX}\n- ${OPERATIONS_ODS_APPS_UNAVAILABLE_TEXT}`,
          };
        }
        if (
          hostOnly &&
          state.operationsRequiresOdsStatusProjection &&
          !state.operationsOdsStatusProjection
        ) {
          const partialText = operationsEvidenceText(
            state.operationsRequiredActions,
            state.operationsTerminalJobs,
            state.operationsOdsAppsProjection
          );
          return {
            status: "failed",
            text: `${partialText ?? OPERATIONS_UNVERIFIED_DELIVERY_PREFIX}\n- ${OPERATIONS_ODS_STATUS_UNAVAILABLE_TEXT}`,
          };
        }
        if (!evidenceText) {
          return { status: "failed", text: OPERATIONS_UNVERIFIED_DELIVERY_PREFIX };
        }
        const verifiedText = state.operationsWorkspaceContinuationRequested
          ? `${evidenceText}\n${
            state.operationsWorkspaceExpectedPath &&
            state.operationsWorkspaceWriteVerified &&
            state.operationsWorkspaceReadVerified
              ? `- Workspace artifact: Pixel wrote and read back \`/workspace/${state.operationsWorkspaceExpectedPath}\` in this response.`
              : "- Workspace continuation: the requested workspace artifact was not both written and read back successfully in this response."
          }`
          : evidenceText;
        return {
          status:
            !state.operationsNetworkDiscoveryRequested && (
            evidenceText.startsWith(OPERATIONS_HOST_EVIDENCE_PREFIX) ||
            evidenceText.startsWith(OPERATIONS_HOST_COMMAND_EVIDENCE_PREFIX) ||
            evidenceText.startsWith(OPERATIONS_EXTENSION_CATALOG_EVIDENCE_PREFIX) ||
            evidenceText.startsWith(OPERATIONS_EXTENSION_LIFECYCLE_EVIDENCE_PREFIX))
            ? "passed"
            : "failed",
          text: state.operationsNetworkDiscoveryRequested
            ? `${verifiedText}\n- ${NETWORK_DISCOVERY_UNVERIFIED_TEXT}`
            : verifiedText,
        };
      }
    }
    if (state.latestVerificationStatus === "pending") {
      return { status: "pending", text: VERIFICATION_PENDING_DELIVERY_PREFIX };
    }
    if (state.latestVerificationStatus === "failed") {
      return { status: "failed", text: VERIFICATION_FAILED_DELIVERY_PREFIX };
    }
    if (
      state.workspaceVerificationRequested &&
      state.latestVerificationStatus === undefined
    ) {
      return { status: "failed", text: VERIFICATION_NOT_RUN_DELIVERY_PREFIX };
    }
    if (state.githubCanonicalUrl && !state.githubCanonicalSatisfied) {
      return { status: "failed", text: GITHUB_SOURCE_UNVERIFIED_DELIVERY_PREFIX };
    }
    const staleExecWarningSuppression = state.suppressStaleExecWarning
      ? { suppressStaleExecWarning: true }
      : {};
    if (state.latestVerificationStatus === "passed") {
      if (state.visibleReplyText) {
        return {
          status: "passed",
          text: state.visibleReplyText,
          ...staleExecWarningSuppression,
        };
      }
      if (state.codingExhausted && state.workspaceTaskRequested && state.pendingExecSessions.size === 0) {
        const writtenFiles = [...state.successfulWritePaths].filter((file) =>
          typeof file === "string" && file.split("/").every((part) => WORKSPACE_PATH_COMPONENT.test(part))
        ).sort();
        if (writtenFiles.length > 0) {
          // Preserve real work when the model repeats a completed command and
          // cannot produce a final reply. This is a partial tool receipt, not
          // a fabricated model answer or a claim that every requirement passed.
          return {
            status: "passed",
            text: "Pixel stopped repeating completed work before it could finish its explanation. " +
              "The following results were recorded by its tools:\n" +
              writtenFiles.slice(0, 20).map((file) => `- File written: \`/workspace/${file}\`.`).join("\n") +
              (writtenFiles.length > 20 ? `\n- ${writtenFiles.length - 20} additional files were written.` : "") +
              "\n- The latest recognized test command completed successfully.\n" +
              "This does not establish complete test coverage or completion of every requested step. " +
              "The workspace is preserved; ask Pixel to continue from these files.",
            ...staleExecWarningSuppression,
          };
        }
      }
      return { status: "passed", ...staleExecWarningSuppression };
    }
    return { status: "none", ...staleExecWarningSuppression };
  }

  function replyPayloadSending(event) {
    const state = runs.get(event?.runId);
    if (!state) return undefined;
    // OpenClaw's OpenAI-compatible stream otherwise concatenates block/tool
    // narration from every model continuation into the terminal chat bubble.
    // The ODS UI already exposes honest elapsed progress. Suppress only this
    // known Pixel run's nonterminal delivery; tool results remain in the agent
    // loop and the verified final payload remains visible.
    if (event?.kind !== "final") {
      return {
        cancel: true,
        reason: "Pixel delivers one terminal owner-visible reply per turn.",
      };
    }
    const verification = verificationForRun(event?.runId);
    const authoritativeText = verification.text;
    if (!authoritativeText) return undefined;
    return {
      payload: {
        ...(event.payload ?? {}),
        text: authoritativeText,
      },
      reason:
        "Pixel replaced an unverified terminal reply with host-authoritative evidence truth.",
    };
  }

  return {
    beforeToolCall,
    afterToolCall,
    toolResultPersist,
    beforeAgentFinalize,
    replyPayloadSending,
    observeRun,
    observeModelCall,
    abortUserRun,
    verificationForRun,
    verificationStatus: (runId) => runs.get(runId)?.latestVerificationStatus,
    trackedRunCount: () => runs.size,
    trackedUserCount: () => activeUsers.size,
  };
}

export function createToolLoopGuardRegistry() {
  let shared;
  return {
    get(options) {
      shared ??= createToolLoopGuard(options);
      return shared;
    },
  };
}
